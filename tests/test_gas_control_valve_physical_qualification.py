"""Gas control valve adapter vs published example and ideal scaling checks."""
import pytest
from pipeline_integrity.models.library_models import GasControlValve, gas_control_valve


def base(**changes):
    p=dict(temperature_k=433.0,molar_mass_kg_mol=0.04401,viscosity_pa_s=1.4665e-4,
        heat_capacity_ratio=1.30,compressibility=0.988,inlet_pressure_pa_abs=680000.0,
        outlet_pressure_pa_abs=310000.0,standard_volume_flow_m3_s=38/36,
        reference_temperature_k=273.15,reference_pressure_pa_abs=101325.0,
        inlet_diameter_m=0.08,outlet_diameter_m=0.10,valve_diameter_m=0.05,
        pressure_recovery_factor=0.85,valve_style_factor=0.42,terminal_pressure_ratio_factor=0.60)
    p.update(changes)
    return gas_control_valve(GasControlValve(**p)).values


def test_gas_valve_published_nonchoked_reducer_example():
    assert base()["kv"] == pytest.approx(72.5866454539105, rel=2e-12)


def test_gas_valve_published_small_flow_example():
    result=base(temperature_k=320.0,molar_mass_kg_mol=0.03995,viscosity_pa_s=5.625e-5,
        heat_capacity_ratio=1.67,compressibility=1.0,inlet_pressure_pa_abs=280000.0,
        outlet_pressure_pa_abs=130000.0,standard_volume_flow_m3_s=0.46/3600.0,
        inlet_diameter_m=0.015,outlet_diameter_m=0.015,valve_diameter_m=0.015,
        pressure_recovery_factor=0.98,valve_style_factor=0.07,terminal_pressure_ratio_factor=0.8)
    assert result["kv"] == pytest.approx(0.016498765335995726, rel=2e-12)


def test_gas_valve_flow_and_temperature_similarity_without_fitting_corrections():
    p=dict(inlet_diameter_m=0.05,outlet_diameter_m=0.05,valve_diameter_m=0.05,
           standard_volume_flow_m3_s=0.2,pressure_recovery_factor=0.85,
           valve_style_factor=0.42,terminal_pressure_ratio_factor=0.60)
    b=base(**p)
    flow2=base(**(p|{"standard_volume_flow_m3_s":0.4}))
    temp2=base(**(p|{"temperature_k":866.0}))
    assert flow2["kv"] == pytest.approx(2*b["kv"], rel=2e-12)
    assert temp2["kv"] == pytest.approx((2**0.5)*b["kv"], rel=2e-12)
    assert not b["diagnostics"]["laminar"]
