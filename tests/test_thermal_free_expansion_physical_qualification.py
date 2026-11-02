"""Closed-form qualification of the catalog unrestrained linear-expansion model."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (12.0, 12.0e-6, 293.15, 373.15),
    (4.5, 0.0, 300.0, 850.0),
    (120.0, 17.3e-6, 500.0, 250.0),
    (0.25, 80.0e-6, 273.15, 400.0),
    (30.0, 2.0e-6, 900.0, 900.0),
)


def _context():
    return RunContext(asset_id="expansion-reference", scenario_id="uniform-temperature-free-expansion", clock_id="steady-state", origin="synthetic")


@pytest.mark.parametrize("length,alpha,tref,top", CASES)
def test_catalog_free_expansion_matches_linear_strain_solution(length, alpha, tref, top):
    result = evaluate(
        "thermal.free_expansion",
        {
            "reference_length_m": length,
            "linear_expansion_per_k": alpha,
            "reference_temperature_k": tref,
            "operating_temperature_k": top,
        },
        _context(),
    )
    expected_change = length * alpha * (top - tref)
    expected_operating_length = length + expected_change
    assert expected_operating_length > 0.0
    assert result.status == "computed"
    assert result.model_id == "thermal.free_expansion"
    assert result.values["free_length_change_m"] == pytest.approx(expected_change, rel=2e-15, abs=1e-14)
    assert result.values["operating_length_m"] == pytest.approx(expected_operating_length, rel=2e-15, abs=1e-14)
