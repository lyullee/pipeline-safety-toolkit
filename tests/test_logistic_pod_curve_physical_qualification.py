"""Closed-form checks of the user-calibrated continuous POD curve."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_flaw_population",
    scenario_id="continuous_logistic_pod",
    clock_id="single_reviewed_procedure",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently authored logistic probability cases", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(size, midpoint=0.004, scale=0.3):
    result = CLIENT.evaluate(
        "inspection.logistic_pod",
        {"flaw_size_m": size, "midpoint_size_m": midpoint, "log_size_scale": scale},
        CONTEXT,
        location_id="procedure-a",
    )
    assert result.status == "computed"
    return result.payload["values"]["probability_of_detection"]


def oracle(size, midpoint, scale):
    if size == 0:
        return 0.0
    z = math.log(size / midpoint) / scale
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


@pytest.mark.parametrize("size", [0.0004, 0.001, 0.004, 0.012, 0.04])
def test_curve_matches_independently_evaluated_logistic_probability(size):
    assert evaluate(size) == pytest.approx(oracle(size, 0.004, 0.3), rel=2e-15, abs=0)


def test_declared_midpoint_is_exactly_fifty_percent_detection():
    assert evaluate(0.004) == 0.5


def test_log_odds_match_declared_power_law_and_size_units_scale_together():
    size, midpoint, scale = 0.009, 0.004, 0.35
    p = evaluate(size, midpoint, scale)
    assert math.log(p / (1.0 - p)) == pytest.approx(math.log(size / midpoint) / scale, rel=2e-15)
    assert evaluate(size * 1000.0, midpoint * 1000.0, scale) == pytest.approx(p, rel=2e-15)


def test_curve_is_monotone_and_larger_scale_flattens_toward_midpoint():
    sizes = [0.0002, 0.0005, 0.001, 0.004, 0.01, 0.04]
    narrow = [evaluate(x, scale=0.2) for x in sizes]
    broad = [evaluate(x, scale=0.8) for x in sizes]
    assert all(a < b for a, b in zip(narrow, narrow[1:]))
    for p_narrow, p_broad in zip(narrow, broad, strict=True):
        assert abs(p_broad - 0.5) <= abs(p_narrow - 0.5) + 1e-15


def test_extreme_size_ratios_remain_finite_and_zero_size_maps_to_zero():
    assert evaluate(0.0) == 0.0
    for size in (1e-300, 1e100):
        value = evaluate(size, midpoint=1e-3, scale=0.25)
        assert math.isfinite(value)
        assert 0.0 <= value <= 1.0


@pytest.mark.parametrize("values", [(-1e-3, 4e-3, 0.3), (1e-3, 0.0, 0.3), (1e-3, 4e-3, 0.0)])
def test_nonphysical_size_or_scale_is_rejected(values):
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "inspection.logistic_pod",
            {"flaw_size_m": values[0], "midpoint_size_m": values[1], "log_size_scale": values[2]},
            CONTEXT,
            location_id="procedure-a",
        )
