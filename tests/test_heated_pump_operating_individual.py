"""Independent closed-law and continuous-ODE thermal pump operating points."""
import hashlib
import json
import math

import pytest
from scipy.optimize import toms748

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.heated_transport import HeatedTransportRequest
from pipeline_integrity.heated_operating_point import (
    HeatedPumpOperatingPointRequest,evaluate_heated_operating_point,
    verify_heated_operating_point_record,
)
from pipeline_integrity.models.pump_curves import ReferencePumpCurve
from test_heated_variable_viscosity_individual import (
    RHO,CAPACITY,DIAMETER,LENGTH,ExponentialViscosityLiquid,_run,_continuous_reference,
)

UPSTREAM,DOWNSTREAM,H0,SLOPE=1e6,1.6e6,100.0,40000.0
G=9.80665


def _case(parts,*,kappa=0.07,conductance=1500.0,ambient=290.0):
    settings=dict(kappa=kappa,viscosity=0.04,conductance=conductance,ambient=ambient)
    baseline=_run(parts,**settings)
    transport=HeatedTransportRequest.model_validate_json(json.dumps(baseline["request"]))
    context=RunContext.model_validate_json(json.dumps(baseline["context"]))
    evidence=EvidenceRef(id="thermal_pump_mapping",citation="Authored SI pump/thermal mapping fixture v1",
        origin="synthetic",sha256=hashlib.sha256(b"authored SI pump/thermal mapping fixture v1").hexdigest())
    context=context.model_copy(update={"evidence":context.evidence+(evidence,)})
    curve=ReferencePumpCurve(volume_flow_m3_s=(0.0003,0.0008,0.0013),
        head_m=(88.0,68.0,48.0),source_id="authored_linear_pump")
    q=HeatedPumpOperatingPointRequest(id="thermal_pump_root",transport=transport,
        downstream_pressure_pa_abs=DOWNSTREAM,mass_flow_bracket_kg_s=(RHO*0.0004,RHO*0.0012),
        mass_flow_absolute_tolerance_kg_s=1e-11,pressure_absolute_tolerance_Pa=0.001,pressure_relative_tolerance=0.0,
        mapping_review_evidence_id=evidence.id,diagnostic_model_and_selected_bracket_reviewed=True,
        pump_curve=curve,upstream_pressure_pa_abs=UPSTREAM,curve_reference_density_kg_m3=RHO,
        constant_density_curve_mapping_reviewed=True)
    return q,context,ExponentialViscosityLiquid(kappa,0.04)


def _solve(parts,**settings):
    q,c,provider=_case(parts,**settings)
    r=evaluate_heated_operating_point(q,c,property_provider=provider)
    assert r["status"]=="computed_diagnostic",r["issue"]
    final=r["transport_result"]
    flow=r["mass_flow_kg_s"]/RHO
    inlet=final["request"]["inlet"]["inputs"]["P"]
    assert inlet == pytest.approx(UPSTREAM+RHO*G*(H0-SLOPE*flow),abs=1e-6)
    assert final["values"]["outlet"]["pressure_pa_abs"] == pytest.approx(DOWNSTREAM,abs=0.001)
    assert r["pump_mapping"]["hydraulic_power_W"] == pytest.approx(flow*(inlet-UPSTREAM))
    assert abs(final["values"]["global_reduced_energy_residual_W"])<1e-5
    assert r["global_uniqueness_proved"] is False
    return r,q,c,provider


def test_constant_viscosity_adiabatic_pump_root_has_exact_closed_flow_and_energy_budget():
    r,q,c,provider=_solve(4,kappa=0.0,conductance=0.0)
    resistance=128*0.04*LENGTH/(math.pi*DIAMETER**4)
    exact=(UPSTREAM+RHO*G*H0-DOWNSTREAM)/(RHO*G*SLOPE+resistance)
    assert r["mass_flow_kg_s"] == pytest.approx(RHO*exact,rel=2e-11)
    pressure_loss=resistance*exact
    rise=r["transport_result"]["values"]["outlet"]["temperature_k"]-340
    assert rise == pytest.approx(pressure_loss/(RHO*CAPACITY),abs=3e-11)
    assert verify_heated_operating_point_record(r,request=q,context=c,property_provider=provider)


def test_exponential_adiabatic_pump_root_converges_to_independent_logarithmic_pressure_law():
    kappa=0.08
    resistance=128*0.04*LENGTH/(math.pi*DIAMETER**4)
    def residual(flow):
        loss=RHO*CAPACITY/kappa*math.log1p(kappa*resistance*flow/(RHO*CAPACITY))
        return UPSTREAM+RHO*G*(H0-SLOPE*flow)-loss-DOWNSTREAM
    exact=toms748(residual,0.0004,0.0012,xtol=1e-16)
    errors=[]
    for parts in (4,8,16,32):
        r,*_=_solve(parts,kappa=kappa,conductance=0.0)
        errors.append(abs(r["mass_flow_kg_s"]-RHO*exact))
    assert all(a>1.7*b>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1]<4e-7


@pytest.mark.parametrize("ambient",[290.0,360.0])
def test_thermal_operating_root_and_temperature_converge_to_independent_continuous_ode(ambient):
    def reference(flow):
        temperature,pressure=_continuous_reference(kappa=0.07,viscosity=0.04,volume_flow=flow,
            ambient=ambient,conductance=1500.0)
        loss=2e6-pressure
        return temperature,UPSTREAM+RHO*G*(H0-SLOPE*flow)-loss-DOWNSTREAM
    exact=toms748(lambda flow:reference(flow)[1],0.0004,0.0012,xtol=1e-16)
    exact_temperature=reference(exact)[0]
    flow_errors,temperature_errors=[],[]
    for parts in (4,8,16,32,64):
        r,*_=_solve(parts,ambient=ambient)
        flow_errors.append(abs(r["mass_flow_kg_s"]-RHO*exact))
        temperature_errors.append(abs(r["transport_result"]["values"]["outlet"]["temperature_k"]-exact_temperature))
    assert all(a>1.7*b>0 for a,b in zip(flow_errors,flow_errors[1:]))
    assert all(a>1.6*b>0 for a,b in zip(temperature_errors,temperature_errors[1:]))
    assert flow_errors[-1]/(RHO*exact)<0.005
    assert temperature_errors[-1]<0.1


def test_density_mapping_mismatch_is_withheld_instead_of_silently_changing_volume_basis():
    q,c,provider=_case(4)
    changed=q.model_copy(update={"curve_reference_density_kg_m3":910.0})
    r=evaluate_heated_operating_point(changed,c,property_provider=provider)
    assert r["status"]=="withheld" and r["transport_result"] is None
    assert "density" in r["issue"]


def test_curve_extrapolation_unreviewed_mapping_and_unpinned_source_are_rejected():
    q,c,provider=_case(4)
    for changes in ({"mass_flow_bracket_kg_s":(0.1,2.0)},
                    {"constant_density_curve_mapping_reviewed":False}):
        with pytest.raises(ValueError):
            HeatedPumpOperatingPointRequest.model_validate({**q.model_dump(),**changes})
    bad=q.transport.model_copy(update={"owner_pins":{}})
    with pytest.raises(ValueError,match="pins differ"):
        evaluate_heated_operating_point(q.model_copy(update={"transport":bad}),c,property_provider=provider)


def test_unbracketed_pressure_target_does_not_return_a_partial_root_or_hydraulic_power():
    q,c,provider=_case(4)
    r=evaluate_heated_operating_point(q.model_copy(update={"downstream_pressure_pa_abs":5e6}),c,property_provider=provider)
    assert r["status"]=="withheld" and r["mass_flow_kg_s"] is None
    assert r["pump_mapping"]["hydraulic_power_W"] is None


def test_actual_coolprop_water_interface_preserves_pressure_curve_phase_and_energy_budget():
    import CoolProp.CoolProp as CP
    from test_heated_incompressible_source_individual import _example
    rho=CP.PropsSI("Dmass","P",1e6,"T",300,"Water")
    base,context=_example.make_case(reviewed=True)
    mapping=EvidenceRef(id="water_pump_mapping",citation="Authored narrow water pump/pressure mapping",
        origin="synthetic",sha256=hashlib.sha256(b"narrow-water-mapping-v1").hexdigest())
    context=context.model_copy(update={"evidence":context.evidence+(mapping,)})
    q=HeatedPumpOperatingPointRequest(id="water_curve_root",transport=base,
        downstream_pressure_pa_abs=8e5,mass_flow_bracket_kg_s=(rho*0.0005,rho*0.0018),
        mapping_review_evidence_id=mapping.id,diagnostic_model_and_selected_bracket_reviewed=True,
        pump_curve=ReferencePumpCurve(volume_flow_m3_s=(0.0,0.001,0.002),head_m=(60.0,40.0,20.0),source_id="authored_water_curve"),
        upstream_pressure_pa_abs=5e5,curve_reference_density_kg_m3=rho,
        maximum_relative_density_deviation=0.001,constant_density_curve_mapping_reviewed=True,
        mass_flow_absolute_tolerance_kg_s=1e-10,pressure_absolute_tolerance_Pa=0.01,pressure_relative_tolerance=0.0)
    r=evaluate_heated_operating_point(q,context)
    assert r["status"]=="computed_diagnostic",r["issue"]
    final=r["transport_result"]
    flow=r["mass_flow_kg_s"]/rho
    assert final["request"]["inlet"]["inputs"]["P"] == pytest.approx(5e5+rho*G*(60-20000*flow),abs=1e-6)
    assert final["values"]["outlet"]["phase"] == "liquid"
    assert abs(final["values"]["global_reduced_energy_residual_W"])<1e-4
    assert abs(r["trials"][-1]["pressure_residual_Pa"])<0.01
