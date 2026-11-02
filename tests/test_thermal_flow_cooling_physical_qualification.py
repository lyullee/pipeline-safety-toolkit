"""Closed-form qualification of the catalog constant-property cooling model."""

import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (600.0, 300.0, 0.8, 4200.0, 1000.0),
    (450.0, 280.0, 0.5, 3900.0, 0.0),
    (300.0, 450.0, 1.2, 4100.0, 1500.0),
    (1000.0, 290.0, 2.0, 1000.0, 1.0e7),
    (320.0, 320.0, 0.2, 4000.0, 125.0),
)


def _context():
    return RunContext(asset_id="cooling-reference", scenario_id="constant-property-stream", clock_id="steady-state", origin="synthetic")


@pytest.mark.parametrize("tin,ta,m_dot,cp,ua", CASES)
def test_catalog_flow_cooling_matches_exponential_solution_and_energy_balance(tin, ta, m_dot, cp, ua):
    result = evaluate(
        "thermal.flow_cooling",
        {
            "inlet_temperature_k": tin,
            "ambient_temperature_k": ta,
            "mass_flow_kg_s": m_dot,
            "specific_heat_j_kg_k": cp,
            "conductance_w_k": ua,
        },
        _context(),
    )
    capacity = m_dot * cp
    expected_tout = ta + (tin - ta) * math.exp(-ua / capacity)
    expected_heat = capacity * (tin - expected_tout)
    assert result.status == "computed"
    assert result.model_id == "thermal.flow_cooling"
    assert result.values["outlet_temperature_k"] == pytest.approx(expected_tout, rel=2e-15, abs=1e-12)
    assert result.values["heat_loss_w"] == pytest.approx(expected_heat, rel=2e-15, abs=1e-10)
    assert capacity * (tin - result.values["outlet_temperature_k"]) == pytest.approx(
        result.values["heat_loss_w"], rel=2e-15, abs=1e-10
    )
