"""Independent momentum-integral checks for the isothermal gas pipe owner."""

import math

import pytest

from pipeline_integrity.models.hydraulics import IsothermalGas, isothermal_gas


CASES = (
    (8.0, 0.018, 5.0e6, 4.8e6, 5000.0, 0.30),
    (12.5, 0.012, 7.0e6, 6.5e6, 2400.0, 0.25),
    (4.2, 0.025, 2.0e6, 1.9e6, 3200.0, 0.40),
    (10.0, 0.015, 4.0e6, 3.6e6, 800.0, 0.20),
)


@pytest.mark.parametrize("rho,fd,p1,p2,length,diameter", CASES)
def test_isothermal_gas_flow_matches_integrated_pressure_momentum_balance(
    rho, fd, p1, p2, length, diameter
):
    """Check A²ρ(P1²−P2²)=m²P1(fL/D+2 ln(P1/P2)) independently."""
    request = IsothermalGas(
        mean_density_kg_m3=rho,
        darcy_friction_factor=fd,
        inlet_pressure_pa_abs=p1,
        outlet_pressure_pa_abs=p2,
        length_m=length,
        diameter_m=diameter,
    )
    result = isothermal_gas(request)
    area = math.pi * diameter**2 / 4
    pressure_integral = area**2 * rho * (p1**2 - p2**2)
    resistance_and_acceleration = p1 * (fd * length / diameter + 2 * math.log(p1 / p2))
    expected_mass_flow = math.sqrt(pressure_integral / resistance_and_acceleration)

    assert result.status == "computed"
    assert result.values["mass_flow_kg_s"] == pytest.approx(expected_mass_flow, rel=2e-12)
    assert result.values["velocity_at_mean_density_m_s"] == pytest.approx(
        expected_mass_flow / (rho * area), rel=2e-12
    )
    recovered_integral = (
        result.values["mass_flow_kg_s"] ** 2 * resistance_and_acceleration
    )
    assert recovered_integral == pytest.approx(pressure_integral, rel=4e-12)


def test_small_friction_case_retains_material_compressible_acceleration_term():
    # Keep the pressure ratio above the near-zero-friction choking boundary.
    rho, p1, p2, length, diameter = 6.0, 3.0e6, 2.9994e6, 1200.0, 0.22
    fd = 1e-7
    request = IsothermalGas(
        mean_density_kg_m3=rho,
        darcy_friction_factor=fd,
        inlet_pressure_pa_abs=p1,
        outlet_pressure_pa_abs=p2,
        length_m=length,
        diameter_m=diameter,
    )
    result = isothermal_gas(request)
    area = math.pi * diameter**2 / 4
    friction_term = fd * length / diameter
    acceleration_term = 2 * math.log(p1 / p2)
    expected = area * math.sqrt(
        rho * (p1**2 - p2**2) / (p1 * (friction_term + acceleration_term))
    )
    assert result.status == "computed"
    assert result.values["mass_flow_kg_s"] == pytest.approx(expected, rel=2e-12)
    assert acceleration_term / (friction_term + acceleration_term) > 0.4
