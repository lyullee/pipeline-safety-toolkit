"""Gas relief area correlation reference cases and dimensional scaling."""
import pytest
from pipeline_integrity.models.library_models import ReliefGas, relief_gas


def case(**overrides):
    p = dict(mass_flow_kg_s=24270/3600, temperature_k=348.0,
        compressibility=0.90, molar_mass_kg_mol=0.051, heat_capacity_ratio=1.11,
        relieving_pressure_pa_abs=670000.0, back_pressure_pa_abs=101325.0,
        discharge_coefficient=0.975, back_pressure_factor=1.0, combination_factor=1.0)
    p.update(overrides)
    return relief_gas(ReliefGas(**p)).values["required_area_m2"]


def test_gas_relief_critical_flow_reference_case():
    assert case() == pytest.approx(0.0036990460646834414, rel=2e-12)


def test_gas_relief_subcritical_flow_reference_case():
    assert case(back_pressure_pa_abs=532000.0) == pytest.approx(0.004248358775943481, rel=2e-12)


def test_gas_relief_area_obeys_mass_and_declared_factor_scaling():
    base = case()
    assert case(mass_flow_kg_s=2*(24270/3600)) == pytest.approx(2*base, rel=2e-12)
    assert case(discharge_coefficient=0.65) == pytest.approx(base*0.975/0.65, rel=2e-12)
