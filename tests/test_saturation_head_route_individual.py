"""Independent linear optimization, exact intersections and depth capacity."""
import math

import numpy as np
import pytest
from scipy.optimize import linprog

from pipeline_integrity.saturation_head_route import (
    SaturationHeadRouteRequest,PartialNormalDepthCheck,evaluate_saturation_head_route,
)


def case(**updates):
    return SaturationHeadRouteRequest(**{**dict(asset_id="authored_floor_pipe",fluid_state_basis_id="supplied_constant_liquid",
        horizontal_stations_m=(0.0,1000.0,2000.0,3000.0),elevations_m=(10.0,10.0,0.0,0.0),
        full_flow_friction_gradients_m_m=(0.001,0.001,0.001),density_kg_m3=1000.0,gravity_m_s2=10.0,
        saturation_pressure_Pa_abs=2000.0,downstream_pressure_Pa_abs=10000.0,
        query_stations_m=(1500.0,1900.0),
        prescribed_gradients_constant_density_equilibrium_floor_and_small_velocity_head_reviewed=True),**updates})


def optimization_reference(q):
    # Minimize all nodal heads subject to pressure-floor and full-flow gradient
    # inequalities. No running maximum or route event is used in this oracle.
    stations=sorted(set((*q.horizontal_stations_m,*q.query_stations_m)))
    n=len(stations)
    accumulated=[0.0]
    for i,gradient in enumerate(q.full_flow_friction_gradients_m_m):
        accumulated.append(accumulated[-1]+gradient*(q.horizontal_stations_m[i+1]-q.horizontal_stations_m[i]))
    loss=np.interp(stations,q.horizontal_stations_m,accumulated)
    elevation=np.interp(stations,q.horizontal_stations_m,q.elevations_m)
    floor=elevation+q.saturation_pressure_Pa_abs/q.density_kg_m3/q.gravity_m_s2
    matrix=np.zeros((n-1,n))
    for i in range(n-1):matrix[i,i]=-1;matrix[i,i+1]=1
    boundary=np.zeros((1,n));boundary[0,-1]=1
    ending=q.elevations_m[-1]+q.downstream_pressure_Pa_abs/q.density_kg_m3/q.gravity_m_s2
    result=linprog(np.ones(n),A_ub=matrix,b_ub=-np.diff(loss),A_eq=boundary,b_eq=[ending],
        bounds=[(float(v),None) for v in floor],method="highs",
        options={"primal_feasibility_tolerance":1e-9,"dual_feasibility_tolerance":1e-9})
    assert result.success,result.message
    return dict(zip(stations,result.x))


def test_exact_transfer_intersections_and_original_boundary_are_retained():
    q=case()
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    values=result.values
    points={r["station_m"]:r for r in values["rows"]}
    expected={0.0:11.2,1000.0:10.2,1500.0:5.2,1800.0:2.2,1900.0:2.1,2000.0:2.0,3000.0:1.0}
    for station,head in expected.items():
        row=min(values["rows"],key=lambda r:abs(r["station_m"]-station))
        assert row["station_m"]==pytest.approx(station,abs=1e-9)
        assert row["piezometric_head_m"]==pytest.approx(head,abs=1e-10)
    assert points[1500.0]["pressure_Pa_abs"]==pytest.approx(2000.0,abs=1e-6)
    assert points[1900.0]["pressure_Pa_abs"]==pytest.approx(11000.0,abs=1e-6)
    assert [e["kind"] for e in values["events"]]==["gravity_candidate_start","gravity_candidate_end"]
    assert [e["station_m"] for e in values["events"]]==pytest.approx([1000,1800],abs=1e-9)
    assert values["total_piezometric_head_loss_m"]==pytest.approx(10.2)
    assert values["baseline_full_flow_head_loss_m"]==pytest.approx(3)
    assert values["additional_floor_head_loss_m"]==pytest.approx(7.2)
    assert values["all_gravity_candidate_capacity_checked"] is False
    assert values["gas_volume_predicted"] is False
    assert values["conservative_moving_interface_solution"] is False


@pytest.mark.parametrize("seed",[2,7,19])
def test_piecewise_route_matches_independent_global_head_optimization(seed):
    rng=np.random.default_rng(seed)
    stations=np.concatenate(([0.0],np.cumsum(rng.uniform(200,1500,10))))
    heights=rng.uniform(-5,30,len(stations))
    gradients=rng.uniform(0.0001,0.002,len(stations)-1)
    q=case(horizontal_stations_m=tuple(map(float,stations)),elevations_m=tuple(map(float,heights)),
        full_flow_friction_gradients_m_m=tuple(map(float,gradients)),
        query_stations_m=tuple(map(float,np.linspace(0,stations[-1],41))))
    expected=optimization_reference(q)
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    observed={r["station_m"]:r for r in result.values["rows"]}
    for station,head in expected.items():
        assert observed[station]["piezometric_head_m"]==pytest.approx(head,abs=2e-8)
    for piece in result.values["segments"]:
        length=piece["end_station_m"]-piece["start_station_m"]
        assert piece["piezometric_head_loss_m"]>=piece["prescribed_full_flow_gradient_m_m"]*length-1e-9
        if piece["regime"]=="floor_contact_candidate":
            assert piece["piezometric_head_loss_m"]==pytest.approx(-piece["elevation_gradient_m_m"]*length,abs=1e-9)
    assert abs(result.values["head_loss_budget_residual_m"])<1e-9


def test_two_separated_gravity_candidates_retain_a_filled_middle_reach():
    q=case(horizontal_stations_m=(0.0,1000.0,2000.0,3000.0,4000.0,5000.0,6000.0),
        elevations_m=(15.0,15.0,0.0,8.0,8.0,0.0,0.0),
        full_flow_friction_gradients_m_m=(0.001,)*6,query_stations_m=(2500.0,))
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    events=result.values["events"]
    assert [e["kind"] for e in events]==["gravity_candidate_start","gravity_candidate_end"]*2
    assert [e["station_m"] for e in events]==pytest.approx([1000,1000+4000/14,4000,4000+5200/7],abs=1e-8)
    assert all(p["regime"]=="filled_or_marginal" for p in result.values["segments"]
               if p["start_station_m"]>=2000 and p["end_station_m"]<=4000)
    expected=optimization_reference(q)
    observed={r["station_m"]:r["piezometric_head_m"] for r in result.values["rows"]}
    for station,head in expected.items():assert observed[station]==pytest.approx(head,abs=2e-8)


@pytest.mark.parametrize("offset",[-1e6,1e6])
def test_vertical_datum_shift_does_not_change_pressure_or_transfer_positions(offset):
    q=case()
    base=evaluate_saturation_head_route(q).values
    shifted=evaluate_saturation_head_route(q.model_copy(update={"elevations_m":tuple(v+offset for v in q.elevations_m)}))
    assert shifted.status=="computed",shifted.notes
    assert [r["pressure_Pa_abs"] for r in shifted.values["rows"]]==pytest.approx([r["pressure_Pa_abs"] for r in base["rows"]],abs=1e-8)
    assert [e["station_m"] for e in shifted.values["events"]]==[e["station_m"] for e in base["events"]]
    assert shifted.values["total_piezometric_head_loss_m"]==pytest.approx(base["total_piezometric_head_loss_m"])


def test_linear_survey_partition_does_not_change_the_route_or_events():
    q=case()
    x=[];z=[];gradients=[]
    for i,gradient in enumerate(q.full_flow_friction_gradients_m_m):
        if i==0:x.append(q.horizontal_stations_m[i]);z.append(q.elevations_m[i])
        x.extend(((q.horizontal_stations_m[i]+q.horizontal_stations_m[i+1])/2,q.horizontal_stations_m[i+1]))
        z.extend(((q.elevations_m[i]+q.elevations_m[i+1])/2,q.elevations_m[i+1]))
        gradients.extend((gradient,gradient))
    refined=q.model_copy(update={"horizontal_stations_m":tuple(x),"elevations_m":tuple(z),"full_flow_friction_gradients_m_m":tuple(gradients)})
    baseline=evaluate_saturation_head_route(q).values
    result=evaluate_saturation_head_route(refined)
    assert result.status=="computed",result.notes
    points={r["station_m"]:r for r in result.values["rows"]}
    for row in baseline["rows"]:
        match=min(result.values["rows"],key=lambda r:abs(r["station_m"]-row["station_m"]))
        assert match["pressure_Pa_abs"]==pytest.approx(row["pressure_Pa_abs"],abs=1e-7)
    assert [e["station_m"] for e in result.values["events"]]==pytest.approx([e["station_m"] for e in baseline["events"]],abs=1e-9)


def test_sufficient_outlet_pressure_keeps_every_section_filled():
    q=case(downstream_pressure_Pa_abs=200000.0)
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    assert result.values["events"]==[]
    assert all(p["regime"]=="filled_or_marginal" for p in result.values["segments"])
    assert result.values["additional_floor_head_loss_m"]==pytest.approx(0,abs=1e-12)
    assert result.values["upstream_pressure_Pa_abs"]==pytest.approx(130000.0)


def test_flat_transformed_obstacle_is_marginal_not_an_invented_vapor_fraction():
    q=case(elevations_m=(3.0,2.0,1.0,0.0),downstream_pressure_Pa_abs=2000.0)
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    assert result.values["events"]==[]
    assert all(p["regime"]=="filled_or_marginal" for p in result.values["segments"])
    assert [r["pressure_Pa_abs"] for r in result.values["rows"]]==pytest.approx([2000]*len(result.values["rows"]),abs=1e-7)
    assert result.values["gas_volume_predicted"] is False


def test_optional_partial_flow_depth_checks_mass_capacity_and_velocity_head_budget():
    diameter=0.8;discharge=0.15;coefficient=37.0
    full_area=math.pi*diameter**2/4
    gradient=(discharge/full_area)**2/(coefficient**2*(diameter/4))
    check=PartialNormalDepthCheck(inner_diameter_m=diameter,volume_flow_m3_s=discharge,
        chezy_coefficient_m_half_per_s=coefficient,depth_bracket_m=(0.01,0.7),maximum_velocity_head_change_m=0.2)
    q=case(horizontal_stations_m=(0.0,8000.0,16000.0,24000.0),elevations_m=(64.0,64.0,0.0,0.0),
        full_flow_friction_gradients_m_m=(gradient,)*3,query_stations_m=(),
        downstream_pressure_Pa_abs=50000.0,partial_normal_depth_check=check)
    result=evaluate_saturation_head_route(q)
    assert result.status=="computed",result.notes
    assert result.values["all_gravity_candidate_capacity_checked"] is True
    assert result.values["checked_gravity_segment_count"]>=1
    for piece in result.values["segments"]:
        if piece["regime"]!="floor_contact_candidate":continue
        depth=piece["normal_depth_m"]
        radius=diameter/2
        theta=math.acos((radius-depth)/radius)
        area=radius**2*theta-(radius-depth)*math.sqrt(depth*(diameter-depth))
        wetted=2*radius*theta
        recovered=coefficient*area*math.sqrt((area/wetted)*(-piece["elevation_gradient_m_m"]))
        assert recovered==pytest.approx(discharge,rel=1e-8)
        assert abs(piece["velocity_head_increment_over_full_m"])<0.2


def test_unavailable_capacity_branch_and_unreviewed_or_invalid_boundary_are_withheld():
    q=case()
    with pytest.raises(ValueError):SaturationHeadRouteRequest.model_validate({**q.model_dump(),"downstream_pressure_Pa_abs":1000.0})
    with pytest.raises(ValueError):SaturationHeadRouteRequest.model_validate({**q.model_dump(),"horizontal_stations_m":(0.0,1.0)})
    with pytest.raises(ValueError,match="require review"):
        evaluate_saturation_head_route(q.model_copy(update={"prescribed_gradients_constant_density_equilibrium_floor_and_small_velocity_head_reviewed":False}))
    check=PartialNormalDepthCheck(inner_diameter_m=1.0,volume_flow_m3_s=100.0,
        chezy_coefficient_m_half_per_s=40.0,depth_bracket_m=(0.01,0.99))
    result=evaluate_saturation_head_route(q.model_copy(update={"partial_normal_depth_check":check}))
    assert result.status=="out_of_scope" and result.values=={}


def test_pressure_density_scaling_preserves_head_and_scales_absolute_pressures():
    q=case()
    base=evaluate_saturation_head_route(q).values
    scaled=evaluate_saturation_head_route(q.model_copy(update={"density_kg_m3":4000.0,
        "saturation_pressure_Pa_abs":8000.0,"downstream_pressure_Pa_abs":40000.0}))
    assert scaled.status=="computed",scaled.notes
    assert [r["piezometric_head_m"] for r in scaled.values["rows"]]==pytest.approx([r["piezometric_head_m"] for r in base["rows"]])
    assert [r["pressure_Pa_abs"] for r in scaled.values["rows"]]==pytest.approx([4*r["pressure_Pa_abs"] for r in base["rows"]])


def test_optional_velocity_head_limit_is_enforced_instead_of_silently_claiming_exact_routing():
    q=case()
    check=PartialNormalDepthCheck(inner_diameter_m=1.0,volume_flow_m3_s=0.1,
        chezy_coefficient_m_half_per_s=40.0,depth_bracket_m=(0.01,0.95),maximum_velocity_head_change_m=0.0)
    result=evaluate_saturation_head_route(q.model_copy(update={"partial_normal_depth_check":check}))
    assert result.status=="out_of_scope" and result.values=={}
    assert "velocity-head" in result.notes[0]
