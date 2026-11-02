"""Catalog-level qualification of the steady isothermal gas-flow balance."""

import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (8.0, 0.018, 5.0e6, 4.8e6, 5000.0, 0.30),
    (12.5, 0.012, 7.0e6, 6.5e6, 2400.0, 0.25),
    (4.2, 0.025, 2.0e6, 1.9e6, 3200.0, 0.40),
    (10.0, 0.015, 4.0e6, 3.6e6, 800.0, 0.20),
    (6.0, 1e-7, 3.0e6, 2.9994e6, 1200.0, 0.22),
)


def _context():
    return RunContext(asset_id="isothermal-gas-reference", scenario_id="steady-horizontal-isothermal", clock_id="steady-state", origin="synthetic")


@pytest.mark.parametrize("rho,fd,p1,p2,length,diameter", CASES)
def test_catalog_isothermal_gas_matches_integrated_pressure_momentum_balance(rho, fd, p1, p2, length, diameter):
    result = evaluate(
        "hydraulics.isothermal_gas",
        {
            "mean_density_kg_m3": rho,
            "darcy_friction_factor": fd,
            "inlet_pressure_pa_abs": p1,
            "outlet_pressure_pa_abs": p2,
            "length_m": length,
            "diameter_m": diameter,
        },
        _context(),
    )
    area = math.pi * diameter**2 / 4.0
    pressure_integral = area**2 * rho * (p1**2 - p2**2)
    resistance = fd * length / diameter + 2.0 * math.log(p1 / p2)
    expected_mass_flow = math.sqrt(pressure_integral / (p1 * resistance))
    assert result.status == "computed"
    assert result.model_id == "hydraulics.isothermal_gas"
    assert result.values["mass_flow_kg_s"] == pytest.approx(expected_mass_flow, rel=2e-12)
    expected_velocity = expected_mass_flow / (rho * area)
    assert result.values["velocity_at_mean_density_m_s"] == pytest.approx(expected_velocity, rel=2e-12)
    assert result.values["mass_flow_kg_s"] ** 2 * p1 * resistance == pytest.approx(
        pressure_integral, rel=4e-12
    )
