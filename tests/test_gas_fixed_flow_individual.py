"""Independent thermal ODE and pressure/flow-boundary round trips."""
import json

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.gas_potential_network import (
    GasPotentialForwardRequest,GasPotentialPathRequest,evaluate_gas_potential_path,verify_gas_potential_path_record,
)
from test_elevated_general_gas_independent import IdealReferenceGas
from test_steady_gas_thermal_individual import _case


def _forward(parts,*,real=False,ambient=280.0,height=0.0,use_recovered_mass=False):
    mass,length,ua=(0.8,1000.0,40.0) if real else (0.0025,40000.0,1.0)
    baseline,reference=_case(parts,mass,length,height,ua,ambient,real=real)
    data=dict(baseline["request"])
    data.update(outlet_pressure_Pa_abs=None,
        prescribed_mass_flow_kg_s=baseline["mass_flow_kg_s"] if use_recovered_mass else mass,
        absolute_mass_flow_tolerance_kg_s=1e-10 if real else 1e-12)
    q=GasPotentialForwardRequest.model_validate_json(json.dumps(data))
    context=RunContext.model_validate_json(json.dumps(baseline["context"]))
    provider=None if real else IdealReferenceGas()
    record=evaluate_gas_potential_path(q,context,property_provider=provider)
    assert record["status"]=="computed_diagnostic",record["failure"]
    assert record["boundary_mode"]=="prescribed_mass_flow"
    assert record["outlet_pressure_residual_Pa"] is None
    assert abs(record["prescribed_mass_flow_residual_kg_s"])<q.absolute_mass_flow_tolerance_kg_s
    assert abs(record["global_total_energy_residual_W"])<(1e-4 if real else 1e-7)
    return record,baseline,reference,q,context,provider


@pytest.mark.parametrize("real,ambient,height",[(False,280.0,0.0),(False,320.0,100.0),(True,285.0,10.0)])
def test_known_mass_flow_predicts_pressure_and_temperature_with_continuum_refinement(real,ambient,height):
    errors=[]
    for parts in (2,4,8):
        record,_,reference,*_=_forward(parts,real=real,ambient=ambient,height=height)
        expected_p,expected_t=reference.y[:,-1]
        outlet=record["states"][-1]
        errors.append((abs(outlet["pressure_pa_abs"]-expected_p),abs(outlet["temperature_k"]-expected_t)))
    assert all(a[0]>2.4*b[0]>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1][0]<(1.0 if real else 0.1)
    assert errors[-1][1]<0.01


@pytest.mark.parametrize("real",[False,True])
def test_pressure_boundary_and_mass_boundary_use_same_equations_and_recover_each_other(real):
    record,baseline,_,q,context,provider=_forward(4,real=real,ambient=285.0,use_recovered_mass=True)
    for got,expected in zip(record["states"],baseline["states"],strict=True):
        assert got["pressure_pa_abs"]==pytest.approx(expected["pressure_pa_abs"],abs=0.002 if real else 2e-5)
        assert got["temperature_k"]==pytest.approx(expected["temperature_k"],abs=2e-7)
    inverse=baseline["request"]
    inverse["outlet_pressure_Pa_abs"]=record["states"][-1]["pressure_pa_abs"]
    returned=evaluate_gas_potential_path(GasPotentialPathRequest.model_validate_json(json.dumps(inverse)),context,property_provider=provider)
    assert returned["status"]=="computed_diagnostic",returned["failure"]
    assert returned["mass_flow_kg_s"]==pytest.approx(q.prescribed_mass_flow_kg_s,rel=1e-8)


def test_known_mass_flow_rejects_conflicting_or_unsupported_boundary_inputs():
    _,_,_,q,_,_=_forward(2)
    for updates in ({"outlet_pressure_Pa_abs":2e5},{"prescribed_mass_flow_kg_s":0.5}):
        with pytest.raises(ValueError):
            GasPotentialForwardRequest.model_validate({**q.model_dump(),**updates})
    incomplete=q.model_dump()
    incomplete.pop("prescribed_mass_flow_kg_s")
    with pytest.raises(ValueError):
        GasPotentialForwardRequest.model_validate(incomplete)


def test_known_mass_flow_receipt_replays_with_explicit_boundary_kind():
    record,_,_,q,context,provider=_forward(2)
    assert verify_gas_potential_path_record(record,request=q,context=context,property_provider=provider)
