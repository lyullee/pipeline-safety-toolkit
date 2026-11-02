"""Catalog-level qualification of the simple-wave surge pulse relation."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (998.0, 1200.0, 1.5, 0.0, 250.0, 0.05),
    (1025.0, 980.0, 0.0, 1.25, 500.0, 0.8),
    (850.0, 1400.0, -0.4, 0.6, 80.0, 0.01),
    (1200.0, 950.0, 2.0, -1.0, 1200.0, 2.6),
    (1000.0, 1000.0, 0.8, 0.8, 25.0, 0.05),
)


def _context():
    return RunContext(asset_id="surge-reference", scenario_id="instantaneous-simple-wave", clock_id="transient-event", origin="synthetic")


@pytest.mark.parametrize("rho,c,v_initial,v_final,length,closure_time", CASES)
def test_catalog_surge_pulse_matches_joukowsky_and_wave_roundtrip(rho,c,v_initial,v_final,length,closure_time):
    result = evaluate(
        "transient.surge_pulse",
        {
            "density_kg_m3": rho,
            "wave_speed_m_s": c,
            "initial_velocity_m_s": v_initial,
            "final_velocity_m_s": v_final,
            "length_m": length,
            "closure_time_s": closure_time,
        },
        _context(),
    )
    expected_pressure = rho * c * (v_initial - v_final)
    round_trip = 2.0 * length / c
    assert result.status == "computed"
    assert result.model_id == "transient.surge_pulse"
    assert result.values["instantaneous_pressure_pulse_pa"] == pytest.approx(
        expected_pressure, rel=2e-15, abs=1e-12
    )
    assert result.values["round_trip_time_s"] == pytest.approx(round_trip, rel=2e-15, abs=1e-15)
    assert result.values["fast_closure"] is (closure_time <= round_trip)


def test_catalog_surge_pulse_uses_inclusive_wave_travel_time_boundary():
    inputs = {
        "density_kg_m3": 1000.0,
        "wave_speed_m_s": 1000.0,
        "initial_velocity_m_s": 2.0,
        "final_velocity_m_s": 0.0,
        "length_m": 50.0,
    }
    at_boundary = evaluate("transient.surge_pulse", inputs | {"closure_time_s": 0.1}, _context())
    just_slower = evaluate("transient.surge_pulse", inputs | {"closure_time_s": 0.1000000001}, _context())
    just_faster = evaluate("transient.surge_pulse", inputs | {"closure_time_s": 0.0999999999}, _context())
    assert at_boundary.values["fast_closure"] is True
    assert just_slower.values["fast_closure"] is False
    assert just_faster.values["fast_closure"] is True

