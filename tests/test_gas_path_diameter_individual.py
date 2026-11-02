"""Diameter recovery against independent continuous thermal momentum ODEs."""
import copy
import json

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.gas_path_sizing import (
    GasPathDiameterRequest, evaluate_gas_path_diameter, verify_gas_path_diameter_record,
)
from pipeline_integrity.gas_potential_network import GasPotentialForwardRequest, evaluate_gas_potential_path
from test_elevated_general_gas_independent import D, IdealReferenceGas
from test_steady_gas_thermal_individual import _case


def setup(parts, *, real=False, ambient=280.0, height=0.0):
    mass, length, ua = (0.8, 1000.0, 40.0) if real else (0.0025, 40000.0, 1.0)
    baseline, continuum = _case(parts, mass, length, height, ua, ambient, real=real)
    data = dict(baseline["request"])
    data.update(outlet_pressure_Pa_abs=None, prescribed_mass_flow_kg_s=mass,
                absolute_mass_flow_tolerance_kg_s=1e-10 if real else 1e-12)
    forward = GasPotentialForwardRequest.model_validate_json(json.dumps(data))
    q = GasPathDiameterRequest(id="selected_diameter", transport=forward,
        target_outlet_pressure_Pa_abs=float(continuum.y[0, -1]),
        diameter_bracket_m=(D * 0.97, D * 1.03),
        absolute_diameter_tolerance_m=1e-11, absolute_pressure_tolerance_Pa=0.001,
        selected_bracket_and_fixed_total_heat_boundary_reviewed=True)
    context = RunContext.model_validate_json(json.dumps(baseline["context"]))
    return q, context, None if real else IdealReferenceGas()


@pytest.mark.parametrize("real,ambient,height", [(False,280.0,0.0),(False,320.0,100.0),(True,285.0,10.0)])
def test_diameter_for_independent_continuum_target_converges_to_known_geometry(real,ambient,height):
    errors = []
    for parts in (2,4,8):
        q,c,provider = setup(parts,real=real,ambient=ambient,height=height)
        result = evaluate_gas_path_diameter(q,c,property_provider=provider)
        assert result["status"] == "computed_diagnostic",result["issue"]
        errors.append(abs(result["diameter_m"]-D))
        transport = result["transport_result"]
        assert abs(transport["states"][-1]["pressure_pa_abs"]-q.target_outlet_pressure_Pa_abs)<0.001
        assert abs(transport["mass_flow_kg_s"]-q.transport.prescribed_mass_flow_kg_s)<q.transport.absolute_mass_flow_tolerance_kg_s
        assert abs(transport["global_total_energy_residual_W"])<(1e-4 if real else 1e-7)
        assert result["global_uniqueness_proved"] is False
        assert all(s["wall_conductance_W_K"] == q.transport.segments[i].wall_conductance_W_K
                   for i,s in enumerate(transport["request"]["segments"]))
    assert all(a>2.3*b>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1]/D<0.001


def test_selected_root_recovers_the_forward_discrete_solution_and_endpoint_root():
    q,c,provider=setup(2)
    forward=evaluate_gas_potential_path(q.transport,c,property_provider=provider)
    target=forward["states"][-1]["pressure_pa_abs"]
    for bracket in ((D*.97,D*1.03),(D,D*1.03)):
        selected=q.model_copy(update={"target_outlet_pressure_Pa_abs":target,"diameter_bracket_m":bracket})
        result=evaluate_gas_path_diameter(selected,c,property_provider=provider)
        assert result["status"]=="computed_diagnostic",result["issue"]
        assert result["diameter_m"]==pytest.approx(D,abs=2e-9)


def test_unbracketed_target_does_not_return_a_diameter():
    q,c,provider=setup(2)
    q=q.model_copy(update={"target_outlet_pressure_Pa_abs":q.transport.inlet_pressure_Pa_abs+1000})
    result=evaluate_gas_path_diameter(q,c,property_provider=provider)
    assert result["status"]=="withheld"
    assert result["diameter_m"] is None and result["transport_result"] is None
    assert "does not enclose" in result["issue"]


def test_diameter_search_requires_ordered_reviewed_geometry_and_pressure_support():
    q,c,provider=setup(2)
    for updates in ({"diameter_bracket_m":(D,D)},
                    {"diameter_bracket_m":(D*1.1,D*1.2)},
                    {"target_outlet_pressure_Pa_abs":1.0},
                    {"maximum_segment_evaluations":1}):
        with pytest.raises(ValueError):
            GasPathDiameterRequest.model_validate({**q.model_dump(),**updates})
    with pytest.raises(ValueError,match="require review"):
        evaluate_gas_path_diameter(q.model_copy(update={"selected_bracket_and_fixed_total_heat_boundary_reviewed":False}),c,property_provider=provider)


def test_diameter_receipt_replays_and_detects_tampering():
    q,c,provider=setup(2)
    result=evaluate_gas_path_diameter(q,c,property_provider=provider)
    assert result["status"]=="computed_diagnostic",result["issue"]
    assert verify_gas_path_diameter_record(result,request=q,context=c,property_provider=provider)
    changed=copy.deepcopy(result)
    changed["diameter_m"]*=1.01
    with pytest.raises(ValueError,match="receipt changed"):
        verify_gas_path_diameter_record(changed,property_provider=provider)
