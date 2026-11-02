"""Independent closed-form checks for PRISM annual scenario aggregation."""
import math
import pytest
from lh2_tno.risk import AnnualScenario, annual_risk


def branch(sid, event, weather, frequency, harm, weather_p, occupancy):
    return AnnualScenario(
        id=sid,
        initiating_event_id=event,
        weather_class=weather,
        frequency_per_year=frequency,
        harm_probability_given_event=harm,
        annual_weather_probability=weather_p,
        occupancy_probability=occupancy,
    )


def test_partitioned_event_annual_rate_matches_independent_weighted_sum():
    rows = (
        branch("e1-calm", "E1", "calm", 0.02, 0.10, 0.75, 0.40),
        branch("e1-windy", "E1", "windy", 0.02, 0.30, 0.25, 0.80),
        branch("e2-calm", "E2", "calm", 0.01, 0.50, 1.00, 0.20),
    )
    expected = 0.02 * 0.10 * 0.75 * 0.40 + 0.02 * 0.30 * 0.25 * 0.80 + 0.01 * 0.50 * 1.0 * 0.20
    got = annual_risk(rows)
    assert got["value"] == pytest.approx(expected, rel=1e-14, abs=1e-16)
    assert got["known_contribution"] == pytest.approx(expected, rel=1e-14, abs=1e-16)
    assert got["units"] == "year^-1" and got["uncomputed_scenarios"] == []
    assert got["physical_validation_claim"] is False


def test_missing_event_frequency_withholds_total_but_preserves_known_partial_sum():
    rows = (
        branch("known", "E1", "all", 0.02, 0.10, 1.0, 0.5),
        branch("unknown", "E2", "all", None, 0.90, 1.0, 1.0),
    )
    got = annual_risk(rows)
    assert got["value"] is None
    assert got["known_contribution"] == pytest.approx(0.001)
    assert got["uncomputed_scenarios"] == ["E2"]


def test_weather_partition_is_invariant_to_branch_order():
    rows = (
        branch("dry", "E", "dry", 0.003, 0.7, 0.6, 0.8),
        branch("wet", "E", "wet", 0.003, 0.2, 0.4, 0.5),
    )
    a, b = annual_risk(rows), annual_risk(tuple(reversed(rows)))
    expected = 0.003 * (0.7 * 0.6 * 0.8 + 0.2 * 0.4 * 0.5)
    assert a["value"] == pytest.approx(expected, rel=1e-14)
    assert b["value"] == pytest.approx(expected, rel=1e-14)


@pytest.mark.parametrize(
    "rows",
    [
        (branch("dup", "E", "a", 0.1, 0.1, 0.5, 0.5), branch("dup", "E", "b", 0.1, 0.1, 0.5, 0.5)),
        (branch("a", "E", "same", 0.1, 0.1, 0.5, 0.5), branch("b", "E", "same", 0.1, 0.1, 0.5, 0.5)),
        (branch("a", "E", "a", 0.1, 0.1, 0.4, 0.5), branch("b", "E", "b", 0.1, 0.1, 0.4, 0.5)),
        (branch("a", "E", "a", 0.1, 0.1, 0.5, 0.5), branch("b", "E", "b", 0.2, 0.1, 0.5, 0.5)),
    ],
)
def test_invalid_scenario_or_event_partition_is_rejected(rows):
    with pytest.raises(ValueError):
        annual_risk(rows)


def test_zero_frequency_has_zero_rate_and_unit_scaling_is_explicit():
    got = annual_risk((branch("zero", "E", "all", 0.0, 1.0, 1.0, 1.0),))
    assert got["value"] == 0.0
    assert math.isfinite(got["value"]) and got["units"] == "year^-1"
