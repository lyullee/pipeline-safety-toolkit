"""Continuous pressure/energy references for the existing segmented liquid owner."""
import math

import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from pipeline_integrity import heated_transport as ht
from pipeline_integrity.thermo import StateRequest
from test_heated_incompressible_source_individual import _example

RHO, CAPACITY, DIAMETER, LENGTH, INITIAL_T = 900.0, 2100.0, 0.08, 400.0, 340.0


class ExponentialViscosityLiquid:
    """Authored incompressible test fluid with a thermodynamic pressure-work term."""
    def __init__(self, kappa, reference_viscosity):
        self.kappa, self.reference_viscosity = kappa, reference_viscosity

    def runtime_identity(self):
        return {"provider":"authored_temperature_viscosity_liquid", "version":"1",
            "reference_basis":"u=2100*T; h=u+P/900 J/kg; constant density and heat capacity",
            "reference_temperature_K":INITIAL_T,"viscosity_Pa_s":self.reference_viscosity,
            "viscosity_log_slope_per_K":-self.kappa}

    def resolve(self, q):
        pressure=q.inputs["P"]
        temperature=q.inputs.get("T")
        if temperature is None:
            temperature=(q.inputs["Hmass"]-pressure/RHO)/CAPACITY
        return ht.ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=pressure,
            temperature_k=temperature,density_kg_m3=RHO,
            enthalpy_j_kg=CAPACITY*temperature+pressure/RHO,
            entropy_j_kg_k=CAPACITY*math.log(temperature/INITIAL_T),phase="liquid",
            viscosity_pa_s=self.reference_viscosity*math.exp(-self.kappa*(temperature-INITIAL_T)),
            cp_j_kg_k=CAPACITY,cv_j_kg_k=CAPACITY,conductivity_w_m_k=None,speed_of_sound_m_s=None,
            provider_version="1",provider_revision="authored_exact_constitutive_fixture")


def _run(parts, *, kappa=0.05, ambient=290.0, conductance=1500.0,
         viscosity=0.04, volume_flow=0.001, elevation=0.0):
    provider=ExponentialViscosityLiquid(kappa,viscosity)
    cells=tuple(ht.HeatedTransportCell(id=f"cell_{i}",length_m=LENGTH/parts,
        inner_diameter_m=DIAMETER,roughness_m=0.0,minor_loss_coefficient=0.0,
        outlet_minus_inlet_elevation_m=elevation/parts,conductance_W_K=conductance/parts,
        ambient_temperature_K=ambient,heater_into_fluid_W=0.0,
        evidence_id="authored_liquid_boundary") for i in range(parts))
    q,c=_example.make_case(property_provider_kind="custom",mass_flow_kg_s=RHO*volume_flow,
        inlet=StateRequest(fluid="authored_liquid",inputs={"P":2e6,"T":INITIAL_T}),
        pressure_support_pa_abs=(1e5,6e6),temperature_support_K=(270.0,390.0),cells=cells,
        reviews=dict.fromkeys(ht.REVIEWS,True),
        review_evidence_ids=dict.fromkeys(ht.REVIEWS,"authored_liquid_boundary"))
    q=q.model_copy(update={"owner_pins":ht.native_backend_metadata(q,property_provider=provider)})
    c=c.model_copy(update={"metadata":{"heated_transport_boundary":ht.boundary_metadata(q)}})
    record=ht.evaluate_heated_transport(q,c,property_provider=provider)
    assert record["status"] == "computed_diagnostic",record["notes"]
    for cell in record["cells"]:
        assert cell["mass_flux_residual_kg_s"] == 0
        assert abs(cell["reduced_energy_residual_W"]) < 2e-6
        assert abs(cell["internal_plus_flow_work_residual_W"]) < 2e-6
    assert abs(record["values"]["global_reduced_energy_residual_W"]) < 1e-5
    return record


def _pressure_gradient(temperature, kappa, viscosity, flow):
    mu=viscosity*math.exp(-kappa*(temperature-INITIAL_T))
    velocity=4*flow/(math.pi*DIAMETER**2)
    reynolds=RHO*velocity*DIAMETER/mu
    if reynolds < 2300:
        return 128*mu*flow/(math.pi*DIAMETER**4)
    assert reynolds > 4000
    # Solve the implicit smooth-wall law in its inverse-square-root coordinate.
    inverse_sqrt=brentq(lambda y:y+2*math.log10(2.51*y/reynolds),1,40,xtol=1e-13)
    return RHO*velocity**2/(2*DIAMETER*inverse_sqrt**2)


def _continuous_reference(*, kappa, viscosity, volume_flow, ambient, conductance, elevation=0.0):
    capacity_rate=RHO*volume_flow*CAPACITY
    def derivative(x,y):
        temperature,pressure=y
        loss=_pressure_gradient(temperature,kappa,viscosity,volume_flow)
        return [-conductance/LENGTH/capacity_rate*(temperature-ambient)+loss/(RHO*CAPACITY),
                -loss-RHO*9.80665*elevation/LENGTH]
    result=solve_ivp(derivative,(0,LENGTH),(INITIAL_T,2e6),method="DOP853",
                    rtol=2e-12,atol=(2e-10,2e-7))
    assert result.success
    return result.y[:,-1]


@pytest.mark.parametrize("kappa", [0.0,0.025,0.08])
def test_variable_laminar_adiabatic_friction_has_independent_logarithmic_solution(kappa):
    a=128*0.04*0.001/(math.pi*DIAMETER**4*RHO*CAPACITY)
    exact_rise=a*LENGTH if kappa == 0 else math.log1p(kappa*a*LENGTH)/kappa
    errors=[]
    for parts in (4,8,16,32):
        record=_run(parts,kappa=kappa,conductance=0)
        v=record["values"]
        delta=v["outlet"]["temperature_k"]-INITIAL_T
        assert 2e6-v["outlet"]["pressure_pa_abs"] == pytest.approx(RHO*CAPACITY*delta,abs=2e-6)
        assert v["total_heat_into_fluid_W"] == 0
        errors.append(abs(delta-exact_rise))
    if kappa:
        assert all(coarse > 1.8*fine for coarse,fine in zip(errors,errors[1:]))
        # y'=a*exp(-k*y) is contractive for the selected explicit cell size.
        # Its second derivative has magnitude at most k*a**2, so summing
        # local truncation errors gives k*(a*L)**2/(2*N), plus roundoff.
        truncation_bound=kappa*(a*LENGTH)**2/(2*32)
        assert errors[-1] <= truncation_bound+3e-12
    else:
        assert max(errors) < 3e-12


@pytest.mark.parametrize("ambient", [290.0,360.0])
@pytest.mark.parametrize("regime", ["laminar","turbulent"])
def test_temperature_viscosity_pressure_coupling_converges_to_independent_ode(ambient,regime):
    settings=dict(kappa=0.07,viscosity=0.04 if regime == "laminar" else 0.001,
        volume_flow=0.001 if regime == "laminar" else 0.004,
        ambient=ambient,conductance=1500.0 if regime == "laminar" else 5000.0)
    expected_t,expected_p=_continuous_reference(**settings)
    pressure_errors,temperature_errors=[],[]
    for parts in (4,8,16,32):
        record=_run(parts,**settings)
        v=record["values"]
        pressure_errors.append(abs(v["outlet"]["pressure_pa_abs"]-expected_p))
        temperature_errors.append(abs(v["outlet"]["temperature_k"]-expected_t))
        reynolds=[c["hydraulic_owner"]["values"]["reynolds"] for c in record["cells"]]
        assert max(reynolds)<2300 if regime == "laminar" else min(reynolds)>4000
        if ambient < INITIAL_T:
            assert v["final_viscosity_Pa_s"] > v["initial_viscosity_Pa_s"]
        else:
            assert v["final_viscosity_Pa_s"] < v["initial_viscosity_Pa_s"]
    assert all(coarse > 1.75*fine for coarse,fine in zip(pressure_errors,pressure_errors[1:]))
    assert all(coarse > 1.7*fine for coarse,fine in zip(temperature_errors,temperature_errors[1:]))
    assert pressure_errors[-1] < 0.04*abs(2e6-expected_p)
    assert temperature_errors[-1] < 0.001


@pytest.mark.parametrize("elevation", [-30.0,30.0])
def test_elevation_changes_pressure_without_spurious_gravity_heating(elevation):
    flat=_run(16)
    inclined=_run(16,elevation=elevation)
    tflat=flat["values"]["outlet"]["temperature_k"]
    tout=inclined["values"]["outlet"]["temperature_k"]
    assert tout == pytest.approx(tflat,abs=3e-12)
    pressure_change=(flat["values"]["outlet"]["pressure_pa_abs"]-
                     inclined["values"]["outlet"]["pressure_pa_abs"])
    assert pressure_change == pytest.approx(RHO*9.80665*elevation,abs=2e-7)


def test_viscosity_uses_reviewed_kelvin_difference_and_real_pressure_work_enthalpy():
    provider=ExponentialViscosityLiquid(0.04,0.01)
    hot=provider.resolve(StateRequest(fluid="authored_liquid",inputs={"P":2e6,"T":INITIAL_T}))
    cool=provider.resolve(StateRequest(fluid="authored_liquid",inputs={"P":1e6,"T":INITIAL_T-20}))
    assert cool.viscosity_pa_s/hot.viscosity_pa_s == pytest.approx(math.exp(0.8))
    assert hot.enthalpy_j_kg-hot.pressure_pa_abs/RHO == CAPACITY*INITIAL_T
    recovered=provider.resolve(StateRequest(fluid="authored_liquid",inputs={"P":1e6,"Hmass":hot.enthalpy_j_kg}))
    assert recovered.temperature_k-INITIAL_T == pytest.approx(1e6/(RHO*CAPACITY))
