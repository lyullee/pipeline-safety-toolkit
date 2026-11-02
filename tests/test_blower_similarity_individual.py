"""Authored map surfaces, similarity invariants and analytic speed roots."""
import copy
import math

import pytest

from pipeline_integrity.blower_similarity import (
    BlowerReducedMap,BlowerSimilarityRequest,BlowerSpeedRequest,
    evaluate_blower_similarity,verify_blower_similarity_record,
)
from pipeline_integrity.contracts import EvidenceRef,RunContext


def case(**updates):
    speeds=(0.5,1.0,1.5,2.0)
    flows=(0.1,0.2,0.4,0.8)
    mp=BlowerReducedMap(id="authored_affine_map",material_basis_id="same_gas",
        evidence_id="authored_map",nominal_angular_speed_rad_s=100.0,
        reference_inlet_pressure_Pa_abs=200000.0,reference_inlet_density_kg_m3=2.0,
        reduced_speed_ratios=speeds,reduced_volume_flows_m3_s=flows,
        pressure_ratios=tuple(tuple(1+0.4*s-0.1*q for q in flows) for s in speeds),
        density_specific_powers_W_m3_kg=tuple(tuple(40000+20000*s+30000*q for q in flows) for s in speeds))
    context=RunContext(asset_id="blower",scenario_id="manufactured_map",clock_id="steady",origin="synthetic",
        evidence=(EvidenceRef(id="authored_map",citation="Original manufactured affine map for similarity tests",origin="synthetic"),))
    payload=dict(id="observed_blower",asset_id=context.asset_id,clock_id=context.clock_id,
        performance_map=mp,material_basis_id="same_gas",inlet_pressure_Pa_abs=200000.0,
        inlet_density_kg_m3=2.0,mass_flow_kg_s=0.4,angular_speed_rad_s=100.0,
        same_geometry_material_and_similarity_domain_reviewed=True)
    payload.update(updates)
    return BlowerSimilarityRequest(**payload),context


@pytest.mark.parametrize("speed",[70.0,110.0,170.0])
def test_bilinear_map_matches_independent_affine_surface_and_power_units(speed):
    q,c=case(angular_speed_rad_s=speed)
    result=evaluate_blower_similarity(q,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    values=result["values"]
    s=speed/100
    flow=0.2/s
    expected_ratio=1+0.4*s-0.1*flow
    expected_specific=40000+20000*s+30000*flow
    assert values["pressure_ratio"]==pytest.approx(expected_ratio,rel=1e-14)
    assert values["shaft_power_W"]==pytest.approx(2*s**3*expected_specific,rel=1e-14)
    assert values["mapped_specific_shaft_work_J_kg"]*q.mass_flow_kg_s==pytest.approx(values["shaft_power_W"])
    assert result["outlet_temperature_K"] is None and result["isentropic_efficiency"] is None


@pytest.mark.parametrize("scale",[0.5,2.0,3.0])
def test_mach_similarity_preserves_map_point_and_cubic_power(scale):
    # Fixed geometry: Q~omega, p/rho~omega^2, P/rho~omega^3.
    q,c=case()
    baseline=evaluate_blower_similarity(q,c)["values"]
    actual=q.model_copy(update={"angular_speed_rad_s":100*scale,
        "mass_flow_kg_s":0.4*scale,"inlet_pressure_Pa_abs":200000*scale**2})
    result=evaluate_blower_similarity(actual,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    values=result["values"]
    assert values["reduced_speed_ratio"]==pytest.approx(1)
    assert values["reduced_volume_flow_m3_s"]==pytest.approx(0.2)
    assert values["pressure_ratio"]==pytest.approx(baseline["pressure_ratio"])
    assert values["shaft_power_W"]==pytest.approx(baseline["shaft_power_W"]*scale**3)


@pytest.mark.parametrize("scale",[0.2,5.0])
def test_fixed_thermal_coordinate_density_rescaling_preserves_specific_power(scale):
    q,c=case()
    baseline=evaluate_blower_similarity(q,c)["values"]
    actual=q.model_copy(update={"inlet_density_kg_m3":2*scale,
        "mass_flow_kg_s":0.4*scale,"inlet_pressure_Pa_abs":200000*scale})
    values=evaluate_blower_similarity(actual,c)["values"]
    assert values["pressure_ratio"]==pytest.approx(baseline["pressure_ratio"])
    assert values["shaft_power_W"]==pytest.approx(baseline["shaft_power_W"]*scale)
    assert values["mapped_specific_shaft_work_J_kg"]==pytest.approx(baseline["mapped_specific_shaft_work_J_kg"])


@pytest.mark.parametrize("thermal_multiplier",[1.0,1.5])
def test_speed_inverse_matches_independent_quadratic_root(thermal_multiplier):
    q,c=case(inlet_pressure_Pa_abs=200000*thermal_multiplier)
    target=1.4
    data=q.model_dump()
    data.update(angular_speed_rad_s=None,target_pressure_ratio=target,
                angular_speed_bracket_rad_s=(70.0,190.0))
    inverse=BlowerSpeedRequest.model_validate(data)
    result=evaluate_blower_similarity(inverse,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    k=1/math.sqrt(thermal_multiplier)
    # 0.4*k*s - 0.1*Q/s = target-1; Q=mass/rho=0.2.
    expected=((target-1)+math.sqrt((target-1)**2+4*0.4*k*0.1*0.2))/(2*0.4*k)
    assert result["values"]["angular_speed_rad_s"]==pytest.approx(100*expected,abs=1e-7)
    assert result["global_uniqueness_proved"] is False


def test_unknown_map_cell_and_extrapolation_remain_withheld():
    q,c=case()
    incomplete=q.performance_map.model_dump()
    rows=[list(row) for row in incomplete["pressure_ratios"]]
    rows[1][1]=None
    incomplete["pressure_ratios"]=tuple(tuple(row) for row in rows)
    actual=q.model_copy(update={"performance_map":BlowerReducedMap.model_validate(incomplete)})
    result=evaluate_blower_similarity(actual,c)
    assert result["status"]=="withheld" and result["values"] is None
    assert "missing" in result["issue"]
    result=evaluate_blower_similarity(q.model_copy(update={"angular_speed_rad_s":20.0}),c)
    assert result["status"]=="withheld" and result["values"] is None
    assert "extrapolation" in result["issue"]


def test_unbracketed_speed_and_inverse_map_limits_do_not_manufacture_a_solution():
    q,c=case()
    for updates in ({"target_pressure_ratio":3.0},{"angular_speed_bracket_rad_s":(20.0,190.0)}):
        payload={**q.model_dump(),"angular_speed_rad_s":None,"target_pressure_ratio":1.4,
                 "angular_speed_bracket_rad_s":(70.0,190.0),**updates}
        result=evaluate_blower_similarity(BlowerSpeedRequest.model_validate(payload),c)
        assert result["status"]=="withheld" and result["values"] is None


def test_material_evidence_geometry_and_map_shape_contracts_are_explicit():
    q,c=case()
    for updates in ({"material_basis_id":"different_gas"},
                    {"same_geometry_material_and_similarity_domain_reviewed":False}):
        with pytest.raises(ValueError):
            evaluate_blower_similarity(q.model_copy(update=updates),c)
    with pytest.raises(ValueError,match="evidence"):
        evaluate_blower_similarity(q,c.model_copy(update={"evidence":()}))
    for updates in ({"reduced_speed_ratios":(1.0,1.0,1.5,2.0)},
                    {"pressure_ratios":((1.2,1.3),)}):
        with pytest.raises(ValueError):
            BlowerReducedMap.model_validate({**q.performance_map.model_dump(),**updates})


def test_forward_and_inverse_receipts_replay_without_claiming_field_map_validation():
    q,c=case()
    inverse=BlowerSpeedRequest.model_validate({**q.model_dump(),"angular_speed_rad_s":None,
        "target_pressure_ratio":1.4,"angular_speed_bracket_rad_s":(70.0,190.0)})
    for request in (q,inverse):
        record=evaluate_blower_similarity(request,c)
        assert record["status"]=="computed_diagnostic",record["issue"]
        assert verify_blower_similarity_record(record,request=request,context=c)
        assert record["field_map_accuracy_claim"] is False
        assert record["surge_dynamics_or_margins_inferred"] is False
        changed=copy.deepcopy(record)
        changed["values"]["shaft_power_W"]*=1.01
        with pytest.raises(ValueError,match="receipt changed"):
            verify_blower_similarity_record(changed)
