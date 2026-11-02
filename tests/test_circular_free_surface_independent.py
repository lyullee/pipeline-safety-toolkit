"""Independent circular quadrature, nonlinear waves, source and budget checks."""
from functools import lru_cache
import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq

from pipeline_integrity.gravity_free_surface_pipe import (
    CircularFreeSurfaceSettings as Settings, FreeSurfaceBoundary as Boundary,
    FreeSurfaceBoundaryStage as Stage, FreeSurfaceDomainError, FreeSurfaceEvolutionError,
    circular_section_from_area, circular_section_from_depth, free_surface_area_discharge,
    solve_circular_free_surface_pipe,
)

G=9.80665


def periodic():
    b=Boundary(kind="periodic")
    return (Stage(start_time_s=0.,left=b,right=b),)


def solve(u,t=.1,**kwargs):
    stages=kwargs.pop("boundary_stages",periodic())
    return solve_circular_free_surface_pipe(u,Settings(length_m=1.,inner_diameter_m=1.,duration_s=t,**kwargs),
        boundary_stages=stages)


def assert_ledger(result):
    l=result.ledger
    scale=np.maximum(1.,np.abs(l.initial_mass_momentum)+np.abs(l.integrated_boundary_inflow)
                     +np.abs(l.integrated_distributed_sources))
    assert np.max(np.abs(l.residual)/scale)<2e-11


@pytest.mark.parametrize("diameter", [.3,1.])
@pytest.mark.parametrize("fraction", [1e-10,1e-6,.01,.2,.5,.8,1-1e-6])
def test_circular_geometry_matches_independent_positive_cross_section_quadratures(diameter,fraction):
    h=diameter*fraction
    section=circular_section_from_depth(h,diameter)
    # y=h*t removes the small absolute scale before adaptive integration.
    kernel=lambda t:math.sqrt(t)*math.sqrt(1-fraction*t)
    scale=2*h*math.sqrt(diameter*h)
    area=scale*quad(kernel,0,1,epsabs=1e-13,epsrel=1e-13)[0]
    pressure=h*scale*quad(lambda t:(1-t)*kernel(t),0,1,epsabs=1e-13,epsrel=1e-13)[0]
    moment=h*scale*quad(lambda t:t*kernel(t),0,1,epsabs=1e-13,epsrel=1e-13)[0]
    assert section.area_m2==pytest.approx(area,rel=4e-12)
    assert section.hydrostatic_integral_m3==pytest.approx(pressure,rel=4e-12)
    assert section.first_height_moment_m3==pytest.approx(moment,rel=4e-12)
    assert section.top_width_m==pytest.approx(2*math.sqrt(h*(diameter-h)),rel=2e-14)
    theta=math.atan2(2*math.sqrt(fraction*(1-fraction)),1-2*fraction)
    assert section.wetted_perimeter_m==pytest.approx(diameter*theta,rel=2e-14)


@pytest.mark.parametrize("depth", [1e-10,.001,.2,.5,.9,1-1e-6])
def test_area_inverse_preserves_the_supplied_area_and_recovers_depth(depth):
    a=circular_section_from_depth(depth,1.).area_m2
    result=circular_section_from_area(a,1.)
    assert result.area_m2==a
    assert result.depth_m==pytest.approx(depth,rel=3e-12,abs=1e-13 if depth>.9 else 0)


@pytest.mark.parametrize("kind", ["periodic","reflective","extrapolated","prescribed"])
def test_flat_resting_lake_is_stationary_under_consistent_boundary_states(kind):
    u=free_surface_area_discharge(np.full(12,.3),0.,1.)
    b=Boundary(kind=kind,prescribed_area_discharge=tuple(u[0]) if kind=="prescribed" else None)
    result=solve(u,t=.2,boundary_stages=(Stage(start_time_s=0.,left=b,right=b),))
    assert result.area_discharge==pytest.approx(u,abs=2e-14)
    assert_ledger(result)


@pytest.mark.parametrize("angle", [-.03,.03])
def test_uniform_chezy_flow_balances_tangential_gravity_and_friction(angle):
    geometry=circular_section_from_depth(.3,1.)
    coefficient=20.
    velocity=math.copysign(coefficient*math.sqrt(geometry.hydraulic_radius_m*abs(math.tan(angle))),angle)
    u=free_surface_area_discharge(np.full(12,.3),velocity,1.)
    result=solve(u,t=.2,signed_downhill_angle_rad=angle,friction_model="chezy",
                 chezy_coefficient_m_half_per_s=coefficient)
    assert result.area_discharge==pytest.approx(u,rel=3e-14,abs=3e-14)
    assert_ledger(result)


def test_periodic_nonlinear_motion_conserves_volume_and_momentum_without_claiming_exact_energy():
    n=80;x=(np.arange(n)+.5)/n
    u=free_surface_area_discharge(.3+.04*np.sin(2*np.pi*x),.2+.04*np.cos(2*np.pi*x),1.)
    result=solve(u,t=.15)
    assert_ledger(result)
    assert np.all(result.area_discharge[:,0]>0)
    assert np.all(result.area_discharge[:,0]<math.pi/4)
    assert not result.mechanical_energy.exact_energy_conservation_claimed
    assert result.mechanical_energy.final_J<result.mechanical_energy.initial_J


def test_small_free_surface_wave_has_geometric_wave_speed_and_spatial_convergence():
    h0=.3;reference=circular_section_from_depth(h0,1.)
    a0=float(reference.area_m2);width=2*math.sqrt(h0*(1-h0));speed=math.sqrt(G*a0/width)
    errors=[];epsilon=1e-6;duration=.12
    for n in (32,64,128):
        x=(np.arange(n)+.5)/n
        a=circular_section_from_depth(h0+epsilon*np.cos(2*np.pi*x),1.).area_m2
        u=np.column_stack((a,speed*(a-a0)))
        result=solve(u,t=duration)
        exact=a0+width*epsilon*np.cos(2*np.pi*(x-speed*duration))
        errors.append(np.mean(np.abs(result.area_discharge[:,0]-exact)))
        if n==128:
            z0=np.mean((a-a0)*np.exp(-2j*np.pi*x))
            z1=np.mean((result.area_discharge[:,0]-a0)*np.exp(-2j*np.pi*x))
            measured=-np.angle(z1/z0)/(2*np.pi*duration)
            assert measured==pytest.approx(speed,rel=.02)
        assert_ledger(result)
    assert errors[1]<.7*errors[0]
    assert errors[2]<.7*errors[1]


@lru_cache(maxsize=10000)
def reference_area(h):
    # Independent circular segment form, not the production angle inversion.
    return .25*math.acos(1-2*h)-(.5-h)*math.sqrt(h*(1-h))


@lru_cache(maxsize=10000)
def reference_pressure(h):
    return G*quad(lambda y:(h-y)*2*math.sqrt(y*(1-y)),0,h,epsabs=2e-13,epsrel=2e-12)[0]


def reference_speed(h):
    return math.sqrt(G*reference_area(h)/(2*math.sqrt(h*(1-h))))


def exact_circular_riemann(x,time,left_h=.45,right_h=.15):
    """Pressure-matched barotropic shock/rarefaction solution for wet dam release."""
    al,ar=reference_area(left_h),reference_area(right_h)

    def branch(h,hk):
        a,ak=reference_area(h),reference_area(hk)
        if h>hk:return math.sqrt((reference_pressure(h)-reference_pressure(hk))*(a-ak)/(a*ak))
        return quad(lambda y:math.sqrt(G*(2*math.sqrt(y*(1-y)))/reference_area(y)),hk,h,
                    epsabs=2e-12,epsrel=2e-12)[0]

    hs=brentq(lambda h:branch(h,left_h)+branch(h,right_h),right_h,left_h,xtol=1e-13)
    us=.5*(branch(hs,right_h)-branch(hs,left_h));astar=reference_area(hs)
    assert right_h<hs<left_h
    head=-reference_speed(left_h);tail=us-reference_speed(hs)
    shock=astar*us/(astar-ar)
    output=[]
    for location in x:
        xi=(location-.5)/time
        if xi<head:h,v=left_h,0.
        elif xi<tail:
            h=brentq(lambda y:-branch(y,left_h)-reference_speed(y)-xi,hs,left_h,xtol=1e-12)
            v=-branch(h,left_h)
        elif xi<shock:h,v=hs,us
        else:h,v=right_h,0.
        a=reference_area(h);output.append((a,a*v,h))
    return np.asarray(output)


def test_nonlinear_circular_shock_and_rarefaction_match_independent_riemann_solution_with_refinement():
    errors=[];b=Boundary(kind="extrapolated")
    for n in (100,200):
        x=(np.arange(n)+.5)/n
        u=free_surface_area_discharge(np.where(x<.5,.45,.15),0.,1.)
        result=solve(u,t=.12,boundary_stages=(Stage(start_time_s=0.,left=b,right=b),))
        exact=exact_circular_riemann(x,.12)
        error=np.mean(np.abs(result.area_discharge[:,0]-exact[:,0]))
        errors.append(error)
        if n==200:
            assert error<.015
            assert np.mean(np.abs(result.area_discharge[:,1]-exact[:,1]))<.025
        assert result.mechanical_energy.final_J<result.mechanical_energy.initial_J
        assert_ledger(result)
    assert errors[1]<.85*errors[0]


def test_uniform_tangential_gravity_has_exact_acceleration_and_work_budget():
    angle=.04;time=.15
    u=free_surface_area_discharge(np.full(4,.3),.2,1.)
    result=solve(u,t=time,signed_downhill_angle_rad=angle)
    expected=.2+G*math.sin(angle)*time
    assert result.velocity_m_s==pytest.approx(np.full(4,expected),abs=3e-14)
    assert_ledger(result)


@pytest.mark.parametrize("initial_v", [-.5,.5])
def test_uniform_chezy_friction_has_signed_analytic_decay_and_mechanical_dissipation(initial_v):
    radius=float(circular_section_from_depth(.3,1.).hydraulic_radius_m)
    coefficient=5.;time=.2
    u=free_surface_area_discharge(np.full(4,.3),initial_v,1.)
    result=solve(u,t=time,friction_model="chezy",chezy_coefficient_m_half_per_s=coefficient,maximum_step_s=.0005)
    expected=initial_v/(1+G/(coefficient**2*radius)*abs(initial_v)*time)
    assert result.velocity_m_s==pytest.approx(np.full(4,expected),rel=3e-8)
    assert result.mechanical_energy.integrated_chezy_dissipation_J>0
    assert result.mechanical_energy.final_J<result.mechanical_energy.initial_J
    assert_ledger(result)


def test_closure_event_and_optin_snapshots_preserve_pre_event_state_and_mass_budget():
    n=70;u=free_surface_area_discharge(np.full(n,.25),.15,1.)
    open_end=Boundary(kind="extrapolated");wall=Boundary(kind="reflective")
    stages=(Stage(start_time_s=0.,left=open_end,right=open_end),Stage(start_time_s=.023,left=open_end,right=wall))
    result=solve(u,t=.12,boundary_stages=stages,observation_times_s=(0.,.023,.06,.12))
    assert result.event_times_s==(.023,)
    assert result.observations[1].area_discharge==pytest.approx(u,abs=3e-14)
    assert result.observations[-1].depth_m[-1]>.25
    assert tuple(s.time_s for s in result.observations)==(0.,.023,.06,.12)
    assert not result.observations[-1].area_discharge.flags.writeable
    assert not np.shares_memory(result.observations[-1].area_discharge,result.area_discharge)
    expected_inflow=1000*u[0,1]*(.12-.023)
    assert result.ledger.integrated_boundary_inflow[0]==pytest.approx(expected_inflow,rel=2e-5)
    assert_ledger(result)


@pytest.mark.parametrize("area", [0.,math.pi/4,math.pi/4*1.01])
def test_dry_full_or_pressurized_initial_states_are_rejected_without_area_clipping(area):
    with pytest.raises(FreeSurfaceDomainError):solve(np.column_stack((np.full(4,area),np.zeros(4))))


def test_strong_closure_that_cannot_remain_free_surface_terminates_without_a_fake_computed_result():
    u=np.column_stack((np.full(4,.9*math.pi/4),np.full(4,5*.9*math.pi/4)))
    b=Boundary(kind="extrapolated");wall=Boundary(kind="reflective")
    with pytest.raises(FreeSurfaceEvolutionError):
        solve(u,t=.04,minimum_retry_step_s=1e-7,maximum_retries=12,maximum_steps=500,
            boundary_stages=(Stage(start_time_s=0.,left=b,right=wall),))


def test_scalar_and_per_cell_chezy_closures_agree_for_the_same_supplied_coefficient():
    u=free_surface_area_discharge(np.full(5,.3),.2,1.)
    a=solve(u,t=.05,friction_model="chezy",chezy_coefficient_m_half_per_s=20.)
    b=solve(u,t=.05,friction_model="chezy",chezy_coefficient_m_half_per_s=(20.,)*5)
    assert b.area_discharge==pytest.approx(a.area_discharge,abs=2e-15)


@pytest.mark.parametrize("updates", [dict(friction_model="chezy"),dict(chezy_coefficient_m_half_per_s=20.)])
def test_unknown_friction_or_ignored_closure_is_not_silently_substituted(updates):
    with pytest.raises(ValueError):Settings(length_m=1.,inner_diameter_m=1.,duration_s=.1,**updates)


@pytest.mark.parametrize("times", [(.05,.05),(.08,.04),(-.001,.05)])
def test_observation_times_are_not_reordered_clipped_or_merged(times):
    with pytest.raises(ValueError):solve(free_surface_area_discharge(np.full(4,.3),0.,1.),observation_times_s=times)
