"""Depth roots, separate energy integrals and smooth-branch support events."""
import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq,minimize_scalar

from pipeline_integrity.steady_circular_depth import (
    SteadyCircularFlow,CircularDepthRootRequest,SteadyCircularProfileRequest,
    solve_circular_normal_depth,solve_circular_critical_depth,evaluate_steady_circular_profile,
)


G=9.80665
D=1.0


def geometry(h):
    radius=D/2
    theta=math.acos((radius-h)/radius)
    area=radius**2*theta-(radius-h)*math.sqrt(h*(D-h))
    perimeter=2*radius*theta
    return area,perimeter,2*math.sqrt(h*(D-h)),area/perimeter


def flow(**updates):
    return SteadyCircularFlow(**{**dict(inner_diameter_m=D,volume_flow_m3_s=0.15,gravity_m_s2=G,
        hydrostatic_prismatic_wet_section_reviewed=True),**updates})


def profile(q,depth,*,length=10.0,step=0.1,support=(0.05,0.95),margin=1e-3):
    return SteadyCircularProfileRequest(flow=q,initial_depth_m=depth,
        stations_m=tuple(map(float,np.linspace(0,length,21))),depth_support_m=support,
        maximum_step_m=step,minimum_critical_denominator_magnitude=margin)


@pytest.mark.parametrize("depth",[0.2,0.5,0.8])
@pytest.mark.parametrize("angle",[0.002,0.02])
def test_normal_depth_recovers_independent_section_force_balance(depth,angle):
    area,perimeter,_,radius=geometry(depth)
    integrated=quad(lambda y:2*math.sqrt(y*(D-y)),0,depth,epsabs=1e-12)[0]
    assert integrated==pytest.approx(area,rel=1e-10)
    coefficient=40.0
    discharge=coefficient*area*math.sqrt(radius*math.tan(angle))
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle,
        friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient)
    result=solve_circular_normal_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=(depth-0.05,depth+0.05)))
    assert result.status=="computed",result.notes
    assert result.values["depth_m"]==pytest.approx(depth,abs=1e-9)
    # Per-unit-length gravity and wall drag balance under the same g*cos convention.
    normal=G*math.cos(angle)
    driving=G*math.sin(angle)*area
    drag=normal*area*(discharge/area)**2/(coefficient**2*radius)
    assert driving==pytest.approx(drag)
    assert result.values["global_uniqueness_proved"] is False


def test_selected_brackets_keep_both_valid_near_full_normal_depth_roots():
    coefficient=40.0
    angle=0.002
    full_area=math.pi*D**2/4
    discharge=1.02*coefficient*full_area*math.sqrt((D/4)*math.tan(angle))
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle,
        friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient)
    roots=[]
    for bracket in ((0.5,0.95),(0.95,0.9999999)):
        result=solve_circular_normal_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=bracket))
        assert result.status=="computed",result.notes
        roots.append(result.values["depth_m"])
    assert roots[0]<0.95<roots[1]
    for depth in roots:
        area,_,_,radius=geometry(depth)
        assert coefficient*area*math.sqrt(radius*math.tan(angle))==pytest.approx(discharge,rel=1e-8)


@pytest.mark.parametrize("discharge",[0.15,-0.4])
def test_critical_depth_minimizes_independent_specific_energy(discharge):
    angle=0.03
    normal=G*math.cos(angle)
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle)
    def energy(depth):return normal*depth+0.5*(discharge/geometry(depth)[0])**2
    independent=minimize_scalar(energy,bounds=(0.02,0.98),method="bounded",options={"xatol":1e-12})
    assert independent.success
    result=solve_circular_critical_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=(0.02,0.98)))
    assert result.status=="computed",result.notes
    h=result.values["depth_m"]
    assert h==pytest.approx(independent.x,abs=1e-8)
    area,_,width,_=geometry(h)
    assert abs(discharge/area)==pytest.approx(math.sqrt(normal*area/width),rel=1e-8)
    assert energy(h-1e-3)>energy(h) and energy(h+1e-3)>energy(h)


@pytest.mark.parametrize("initial,bracket",[(0.6,(0.4,0.95)),(0.2,(0.05,0.21))])
def test_lossless_steady_profiles_match_independent_energy_level_roots(initial,bracket):
    angle=0.001
    discharge=0.15
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle)
    result=evaluate_steady_circular_profile(profile(q,initial,length=10.0,support=(0.03,0.97)))
    assert result.status=="computed",result.notes
    normal=G*math.cos(angle)
    axial=G*math.sin(angle)
    initial_energy=normal*initial+0.5*(discharge/geometry(initial)[0])**2
    for row in result.values["rows"]:
        station=row["station_m"]
        expected=brentq(lambda h:normal*h+0.5*(discharge/geometry(h)[0])**2-axial*station-initial_energy,
            *bracket,xtol=1e-13)
        assert row["depth_m"]==pytest.approx(expected,abs=2e-9)
        assert abs(row["energy_budget_residual_J_kg"])<1e-7
    assert result.values["termination"]=="requested_station_reached"


def test_resisted_profile_matches_independent_spatial_quadrature():
    angle=0.002
    discharge=0.15
    coefficient=40.0
    initial=0.6
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle,
        friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient)
    result=evaluate_steady_circular_profile(profile(q,initial,length=10.0))
    assert result.status=="computed",result.notes
    normal=G*math.cos(angle)
    def dx_dh(h):
        area,_,width,radius=geometry(h)
        fr2=discharge**2*width/(normal*area**3)
        friction=discharge**2/(coefficient**2*area**2*radius)
        return (1-fr2)/(math.tan(angle)-friction)
    for row in result.values["rows"][1::4]:
        expected=quad(dx_dh,initial,row["depth_m"],epsabs=1e-10,epsrel=1e-10)[0]
        assert row["station_m"]==pytest.approx(expected,abs=1e-7)
        assert row["signed_friction_work_J_kg"]>0
        assert abs(row["energy_budget_residual_J_kg"])<1e-6


@pytest.mark.parametrize("direction",[1.0,-1.0])
def test_uniform_chezy_state_remains_exactly_steady_with_signed_energy_work(direction):
    initial=0.6
    angle=direction*0.002
    area,_,_,radius=geometry(initial)
    coefficient=40.0
    discharge=direction*coefficient*area*math.sqrt(radius*abs(math.tan(angle)))
    q=flow(volume_flow_m3_s=discharge,signed_downhill_angle_rad=angle,
        friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient)
    inverse=solve_circular_normal_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=(0.55,0.65)))
    assert inverse.status=="computed",inverse.notes
    assert inverse.values["depth_m"]==pytest.approx(initial,abs=1e-9)
    result=evaluate_steady_circular_profile(profile(q,initial,length=10.0))
    assert result.status=="computed",result.notes
    for row in result.values["rows"]:
        assert row["depth_m"]==pytest.approx(initial,abs=1e-10)
        assert row["signed_friction_work_J_kg"]==pytest.approx(G*math.sin(angle)*row["station_m"],abs=1e-9)


def test_critical_margin_event_stops_before_singularity_at_independent_quadrature_station():
    discharge=0.15
    coefficient=10.0
    initial=0.4
    margin=0.02
    q=flow(volume_flow_m3_s=discharge,friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient)
    def fr2(h):
        area,_,width,_=geometry(h)
        return discharge**2*width/(G*area**3)
    terminal_depth=brentq(lambda h:1-fr2(h)-margin,0.15,initial,xtol=1e-13)
    def dx_dh(h):
        area,_,_,radius=geometry(h)
        friction=discharge**2/(coefficient**2*area**2*radius)
        return -(1-fr2(h))/friction
    expected=quad(dx_dh,initial,terminal_depth,epsabs=1e-10,epsrel=1e-10)[0]
    result=evaluate_steady_circular_profile(profile(q,initial,length=10.0,step=0.01,margin=margin))
    assert result.status=="computed",result.notes
    assert result.values["termination"]=="critical_margin"
    last=result.values["rows"][-1]
    assert last["depth_m"]==pytest.approx(terminal_depth,abs=2e-9)
    assert last["station_m"]==pytest.approx(expected,abs=1e-7)
    assert last["station_m"]<10


def test_static_tilted_surface_stops_at_declared_upper_depth_without_clipping():
    angle=0.01
    q=flow(volume_flow_m3_s=0.0,signed_downhill_angle_rad=angle)
    result=evaluate_steady_circular_profile(profile(q,0.5,length=100.0,step=0.2,support=(0.1,0.6)))
    assert result.status=="computed",result.notes
    assert result.values["termination"]=="upper_depth_support"
    last=result.values["rows"][-1]
    assert last["depth_m"]==pytest.approx(0.6,abs=1e-12)
    assert last["station_m"]==pytest.approx(0.1/math.tan(angle),abs=1e-9)


def test_missing_bracket_support_and_applicability_never_infer_a_depth_branch():
    q=flow()
    assert solve_circular_normal_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=(0.1,0.9))).status=="out_of_scope"
    assert solve_circular_critical_depth(CircularDepthRootRequest(flow=flow(volume_flow_m3_s=0.0),depth_bracket_m=(0.1,0.9))).status=="out_of_scope"
    assert solve_circular_critical_depth(CircularDepthRootRequest(flow=q,depth_bracket_m=(0.7,0.9))).status=="out_of_scope"
    with pytest.raises(ValueError):CircularDepthRootRequest(flow=q,depth_bracket_m=(0.0,0.9))
    with pytest.raises(ValueError,match="requires review"):
        solve_circular_critical_depth(CircularDepthRootRequest(flow=q.model_copy(update={"hydrostatic_prismatic_wet_section_reviewed":False}),depth_bracket_m=(0.1,0.9)))
    limited=evaluate_steady_circular_profile(profile(q,0.6).model_copy(update={"maximum_rhs_evaluations":1}))
    assert limited.status=="out_of_scope" and limited.values=={}
