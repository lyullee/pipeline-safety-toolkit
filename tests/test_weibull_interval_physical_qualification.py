"""Independent identities for bounded conditional Weibull survival intervals."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_component",
    scenario_id="conditional_weibull_interval_oracle",
    clock_id="seconds_from_age_origin",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently authored survival-identity cases", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(shape, scale, age, horizon):
    result = CLIENT.evaluate(
        "reliability.weibull_interval",
        {"shape": shape, "scale_s": scale, "current_age_s": age, "horizon_s": horizon},
        CONTEXT,
        location_id="component-A",
    )
    assert result.status == "computed"
    return result.payload["values"]


def oracle(shape, scale, age, horizon):
    # Conditional first-failure probability from the survival-ratio definition.
    before = math.exp(-((age / scale) ** shape))
    after = math.exp(-(((age + horizon) / scale) ** shape))
    return 1.0 - after / before


@pytest.mark.parametrize(
    "shape,scale,age,horizon",
    [(1.0, 100.0, 0.0, 10.0), (1.0, 100.0, 1_000.0, 2.0), (2.0, 100.0, 20.0, 10.0), (0.5, 40.0, 18.0, 9.0), (3.5, 12.0, 4.0, 2.0)],
)
def test_conditional_failure_probability_matches_survival_ratio(shape, scale, age, horizon):
    values = evaluate(shape, scale, age, horizon)
    expected = oracle(shape, scale, age, horizon)
    assert values["probability_given_survival"] == pytest.approx(expected, rel=2e-12, abs=1e-15)
    assert values["integrated_hazard"] == pytest.approx(-math.log1p(-expected), rel=2e-12, abs=1e-15)
    assert values["equivalent_hazard_per_s"] == pytest.approx(values["integrated_hazard"] / horizon)


def test_exponential_shape_is_memoryless_and_independent_of_current_age():
    early = evaluate(1.0, 250.0, 0.0, 12.0)["probability_given_survival"]
    aged = evaluate(1.0, 250.0, 1.0e9, 12.0)["probability_given_survival"]
    assert early == pytest.approx(-math.expm1(-12.0 / 250.0), rel=1e-13)
    assert aged == pytest.approx(early, rel=1e-13)


def test_adjacent_period_probabilities_compose_by_survival_multiplication():
    p_first = evaluate(2.4, 300.0, 60.0, 15.0)["probability_given_survival"]
    p_second = evaluate(2.4, 300.0, 75.0, 25.0)["probability_given_survival"]
    p_combined = evaluate(2.4, 300.0, 60.0, 40.0)["probability_given_survival"]
    assert p_combined == pytest.approx(p_first + (1.0 - p_first) * p_second, rel=2e-13)


def test_consistent_time_unit_scaling_preserves_dimensionless_probability():
    seconds = evaluate(1.8, 1_200.0, 400.0, 75.0)
    hours = evaluate(1.8, 1_200.0 / 3600.0, 400.0 / 3600.0, 75.0 / 3600.0)
    assert hours["probability_given_survival"] == pytest.approx(seconds["probability_given_survival"], rel=2e-13)
    assert hours["integrated_hazard"] == pytest.approx(seconds["integrated_hazard"], rel=2e-13)
    assert hours["equivalent_hazard_per_s"] == pytest.approx(seconds["equivalent_hazard_per_s"] * 3600.0, rel=2e-13)


def test_small_interval_probability_remains_positive_below_subtraction_resolution():
    values = evaluate(1.0, 1.0e12, 1.0e10, 1.0e-5)
    expected = -math.expm1(-1.0e-17)
    assert values["probability_given_survival"] > 0.0
    assert values["probability_given_survival"] == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("values", [(0.0, 100.0, 0.0, 1.0), (2.0, 0.0, 0.0, 1.0), (2.0, 1.0, -1.0, 1.0), (2.0, 1.0, 0.0, 0.0)])
def test_nonphysical_shape_scale_age_or_horizon_is_rejected(values):
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "reliability.weibull_interval",
            {"shape": values[0], "scale_s": values[1], "current_age_s": values[2], "horizon_s": values[3]},
            CONTEXT,
            location_id="component-A",
        )
