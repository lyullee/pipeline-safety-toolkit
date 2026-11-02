"""ODE, unit/basis, conditioning and live heated-pipe checks for supplied data."""
import json
import math

import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity import heated_transport as ht
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.liquid_viscosity import (
    ExponentialViscosityLaw,TwoPointViscosityFit,ViscosityEvaluationRequest,
    evaluate_exponential_viscosity,fit_exponential_viscosity,
)
from test_heated_variable_viscosity_individual import (
    RHO,CAPACITY,ExponentialViscosityLiquid,_run,_continuous_reference,
)


def _law(coefficient=0.02):
    return ExponentialViscosityLaw(material_basis_id="authored_material",reference_temperature_K=300.0,
        reference_kinematic_viscosity_m2_s=2e-5,temperature_coefficient_per_K=coefficient,
        temperature_support_K=(270.0,360.0))


@pytest.mark.parametrize("coefficient",[-0.02,0.0,0.025])
def test_constitutive_law_matches_independently_integrated_temperature_ode(coefficient):
    law=_law(coefficient)
    for temperature in (275.0,300.0,350.0):
        reference=solve_ivp(lambda t,y:-coefficient*y,(300.0,temperature),[2e-5],
            method="DOP853",rtol=2e-12,atol=1e-17)
        assert reference.success
        r=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=law,temperature_K=temperature))
        assert r.status=="ok"
        assert r.kinematic_viscosity_m2_s==pytest.approx(reference.y[0,-1],rel=2e-11)
        assert r.dynamic_viscosity_Pa_s is None


@pytest.mark.parametrize("scale",[1e-6,1.0,1e6])
def test_two_point_law_is_order_invariant_and_has_geometric_midpoint(scale):
    request=TwoPointViscosityFit(material_basis_id="authored_material",temperatures_K=(290.0,350.0),
        kinematic_viscosities_m2_s=(8e-5*scale,2e-5*scale))
    forward=fit_exponential_viscosity(request)
    reverse=fit_exponential_viscosity(request.model_copy(update={"temperatures_K":(350.0,290.0),
        "kinematic_viscosities_m2_s":(2e-5*scale,8e-5*scale)}))
    assert forward.status==reverse.status=="ok"
    assert forward.law.temperature_coefficient_per_K==pytest.approx(reverse.law.temperature_coefficient_per_K)
    for law in (forward.law,reverse.law):
        got=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=law,temperature_K=320.0))
        assert got.kinematic_viscosity_m2_s==pytest.approx(4e-5*scale,rel=2e-14)
    assert not forward.empirical_accuracy_claim


def test_kelvin_origin_shift_preserves_temperature_difference_and_curve():
    base=_law()
    shifted=base.model_copy(update={"reference_temperature_K":320.0,"temperature_support_K":(290.0,380.0)})
    first=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=base,temperature_K=330.0))
    second=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=shifted,temperature_K=350.0))
    assert first.kinematic_viscosity_m2_s==second.kinematic_viscosity_m2_s


@pytest.mark.parametrize("density",[600.0,1000.0,1400.0])
def test_dynamic_conversion_requires_real_density_and_preserves_kinematic_basis(density):
    r=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=_law(),temperature_K=300.0,
        density_kg_m3=density,density_material_basis_id="authored_material",density_temperature_K=300.0))
    assert r.status=="ok"
    # A unit shear-rate experiment has stress mu; diffusivity recovered from
    # mu/rho must agree with the independently supplied kinematic reference.
    assert r.dynamic_viscosity_Pa_s/density==pytest.approx(2e-5)


@pytest.mark.parametrize("changes",[
    {"density_material_basis_id":"another_material"},
    {"density_temperature_K":301.0},
    {"density_temperature_K":None}])
def test_mismatched_density_does_not_manufacture_dynamic_viscosity(changes):
    data=dict(law=_law(),temperature_K=300.0,density_kg_m3=900.0,
        density_material_basis_id="authored_material",density_temperature_K=300.0)
    data.update(changes)
    r=evaluate_exponential_viscosity(ViscosityEvaluationRequest(**data))
    assert r.status=="needs_data" and r.dynamic_viscosity_Pa_s is None
    assert r.kinematic_viscosity_m2_s==2e-5


@pytest.mark.parametrize("second,status",[(2e-5,"needs_data"),(3e-5,"out_of_scope")])
def test_same_temperature_is_underdetermined_or_conflicting_not_a_zero_slope_fit(second,status):
    result=fit_exponential_viscosity(TwoPointViscosityFit(material_basis_id="authored",temperatures_K=(300.0,300.0),
        kinematic_viscosities_m2_s=(2e-5,second)))
    assert result.status==status and result.law is None


def test_constant_and_extreme_calibration_ratios_use_positive_log_coordinates():
    equal=fit_exponential_viscosity(TwoPointViscosityFit(material_basis_id="authored",temperatures_K=(290.0,350.0),
        kinematic_viscosities_m2_s=(2e-5,2e-5)))
    assert equal.status=="ok" and equal.law.temperature_coefficient_per_K==0
    extreme=fit_exponential_viscosity(TwoPointViscosityFit(material_basis_id="authored",temperatures_K=(290.0,350.0),
        kinematic_viscosities_m2_s=(1e-250,1e250)))
    assert extreme.status=="ok"
    midpoint=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=extreme.law,temperature_K=320.0))
    assert midpoint.kinematic_viscosity_m2_s==pytest.approx(1,rel=2e-12)


@pytest.mark.parametrize("coefficient",[-1000.0,1000.0,1e308])
def test_overflow_and_underflow_are_not_inviscid_or_missing_states(coefficient):
    r=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=_law(coefficient),temperature_K=350.0))
    assert r.status=="out_of_scope" and r.kinematic_viscosity_m2_s is None


def test_outside_temperature_support_and_calibration_interval_are_withheld():
    r=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=_law(),temperature_K=400.0))
    assert r.status=="out_of_scope"
    fit=fit_exponential_viscosity(TwoPointViscosityFit(material_basis_id="authored",temperatures_K=(290.0,350.0),
        kinematic_viscosities_m2_s=(8e-5,2e-5),temperature_support_K=(300.0,340.0)))
    assert fit.status=="out_of_scope" and fit.law is None


class SuppliedLawLiquid(ExponentialViscosityLiquid):
    def __init__(self,law):
        super().__init__(0.0,0.04)
        self.law=law

    def runtime_identity(self):
        return {"provider":"authored_liquid_supplied_viscosity","version":"1",
            "reference_basis":"u=2100*T; h=u+P/900 J/kg; constant rho/cp",
            "viscosity_law":self.law.model_dump(mode="json")}

    def resolve(self,request):
        state=super().resolve(request)
        result=evaluate_exponential_viscosity(ViscosityEvaluationRequest(law=self.law,temperature_K=state.temperature_k,
            density_kg_m3=state.density_kg_m3,density_material_basis_id=self.law.material_basis_id,
            density_temperature_K=state.temperature_k))
        if result.status!="ok":
            raise ValueError(result.reason)
        return state.model_copy(update={"viscosity_pa_s":result.dynamic_viscosity_Pa_s})


def test_fitted_law_plugs_into_existing_heated_pipe_against_independent_continuum():
    kappa=0.07
    fit=fit_exponential_viscosity(TwoPointViscosityFit(material_basis_id="authored_liquid",
        temperatures_K=(340.0,320.0),kinematic_viscosities_m2_s=(0.04/RHO,0.04/RHO*math.exp(20*kappa)),
        temperature_support_K=(270.0,390.0)))
    provider=SuppliedLawLiquid(fit.law)
    baseline=_run(32,kappa=kappa)
    q=ht.HeatedTransportRequest.model_validate_json(json.dumps(baseline["request"]))
    context=RunContext.model_validate_json(json.dumps(baseline["context"]))
    q=q.model_copy(update={"owner_pins":ht.native_backend_metadata(q,property_provider=provider)})
    context=context.model_copy(update={"metadata":{"heated_transport_boundary":ht.boundary_metadata(q)}})
    record=ht.evaluate_heated_transport(q,context,property_provider=provider)
    assert record["status"]=="computed_diagnostic",record["notes"]
    expected_t,expected_p=_continuous_reference(kappa=kappa,viscosity=0.04,volume_flow=0.001,
        ambient=290.0,conductance=1500.0)
    got=record["values"]
    assert abs(got["outlet"]["temperature_k"]-expected_t)<0.001
    assert abs(got["outlet"]["pressure_pa_abs"]-expected_p)<0.04*(2e6-expected_p)
    assert abs(got["global_reduced_energy_residual_W"])<1e-5
