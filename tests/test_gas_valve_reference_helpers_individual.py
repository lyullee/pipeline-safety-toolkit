"""Same-gas mass-rate normalization and independent conventional coefficient units."""
import math
import pytest
from pipeline_integrity.gas_valve_reference import GasReferenceFlowRequest as Request
from pipeline_integrity.gas_valve_reference import normalize_gas_reference_flow,gas_valve_capacity_coefficients
from pipeline_integrity.models.library_models import GasControlValve
from pipeline_integrity.thermo import ThermodynamicState

R,CP=287.05,1005.
class IdealReferenceGas:
    def resolve(self,q):
        p,t=q.inputs["P"],q.inputs["T"]
        return ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=p,temperature_k=t,
            density_kg_m3=p/(R*t),enthalpy_j_kg=CP*t,entropy_j_kg_k=CP*math.log(t/300)-R*math.log(p/1e5),
            phase="gas",viscosity_pa_s=None,cp_j_kg_k=CP,cv_j_kg_k=CP-R,conductivity_w_m_k=None,
            speed_of_sound_m_s=None,provider_version="authored-ideal-gas",provider_revision="1")


@pytest.mark.parametrize("p,t,flow",[(101325.,288.7055555555555,.2),(120000.,300.,.7),(101325.,273.15,1.)])
def test_reference_flow_preserves_independent_ideal_gas_mass_rate(p,t,flow):
    q=Request(fluid="AuthoredGas",source_volume_flow_m3_s=flow,source_pressure_pa_abs=p,source_temperature_k=t,
        same_composition_gas_reference_basis_reviewed=True)
    out=normalize_gas_reference_flow(q,property_provider=IdealReferenceGas())
    incoming_mass=flow*p/(R*t)
    outgoing_mass=out.target_volume_flow_m3_s*q.target_pressure_pa_abs/(R*q.target_temperature_k)
    assert outgoing_mass==pytest.approx(incoming_mass,rel=3e-15)
    assert out.target_volume_flow_m3_s==pytest.approx(flow*(p/q.target_pressure_pa_abs)*(q.target_temperature_k/t),rel=3e-15)


def test_unreviewed_or_missing_flow_does_not_resolve_an_assumed_reference():
    class NoCall:
        def resolve(self,q):raise AssertionError("provider must not be called")
    q=Request(fluid="AuthoredGas",source_pressure_pa_abs=101325.,source_temperature_k=300.)
    assert normalize_gas_reference_flow(q,property_provider=NoCall()).status=="needs_data"


def test_liquid_property_reference_is_not_substituted_for_a_gas_reference():
    class Liquid(IdealReferenceGas):
        def resolve(self,q):return super().resolve(q).model_copy(update={"phase":"liquid"})
    q=Request(fluid="AuthoredGas",source_volume_flow_m3_s=.2,source_pressure_pa_abs=101325.,source_temperature_k=300.,
        same_composition_gas_reference_basis_reviewed=True)
    with pytest.raises(ValueError):normalize_gas_reference_flow(q,property_provider=Liquid())


def test_zero_reference_flow_preserves_zero_mass_rate():
    q=Request(fluid="AuthoredGas",source_volume_flow_m3_s=0.,source_pressure_pa_abs=101325.,source_temperature_k=300.,
        same_composition_gas_reference_basis_reviewed=True)
    assert normalize_gas_reference_flow(q,property_provider=IdealReferenceGas()).target_volume_flow_m3_s==0


def test_provider_cannot_silently_change_the_requested_reference_state():
    class WrongTemperature(IdealReferenceGas):
        def resolve(self,q):return super().resolve(q).model_copy(update={"temperature_k":350.})
    q=Request(fluid="AuthoredGas",source_volume_flow_m3_s=.2,source_pressure_pa_abs=101325.,source_temperature_k=300.,
        same_composition_gas_reference_basis_reviewed=True)
    with pytest.raises(ValueError):normalize_gas_reference_flow(q,property_provider=WrongTemperature())


@pytest.mark.parametrize("rate",[.1,.3])
def test_gas_capacity_exposes_conventional_cv_from_independent_unit_ratio(rate):
    p=GasControlValve(temperature_k=330.,molar_mass_kg_mol=.028,viscosity_pa_s=1.6e-5,heat_capacity_ratio=1.4,
        compressibility=.95,inlet_pressure_pa_abs=8e5,outlet_pressure_pa_abs=4e5,standard_volume_flow_m3_s=rate,
        reference_temperature_k=273.15,reference_pressure_pa_abs=101325.,inlet_diameter_m=.1,outlet_diameter_m=.1,
        valve_diameter_m=.1,pressure_recovery_factor=.85,valve_style_factor=.7,terminal_pressure_ratio_factor=.5)
    out=gas_valve_capacity_coefficients(p)
    psi=.45359237*9.80665/.0254**2
    kv_per_cv=(231*.0254**3*60)*math.sqrt(1e5/psi)
    assert out.cv==pytest.approx(out.kv/kv_per_cv,rel=3e-12)
    assert out.normal_reference_temperature_k==273.15 and out.normal_reference_pressure_pa_abs==101325.
