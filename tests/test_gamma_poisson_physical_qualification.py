"""Independent conjugate identities for physical gamma-Poisson event rates."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_event_population",
    scenario_id="gamma_poisson_conjugacy",
    clock_id="exposure_seconds",
    origin="synthetic",
    evidence=(
        EvidenceRef(
            id="authored",
            citation="Independently authored synthetic conjugate-count cases",
            origin="synthetic",
            license_id="MIT",
        ),
    ),
)
CLIENT = MaterialClient()


def evaluate(prior_shape=2.0, prior_exposure_s=10.0, confirmed_events=3, exposure_s=20.0, complete=True):
    result = CLIENT.evaluate(
        "reliability.gamma_poisson",
        {
            "prior_shape": prior_shape,
            "prior_exposure_s": prior_exposure_s,
            "confirmed_events": confirmed_events,
            "exposure_s": exposure_s,
            "complete_ascertainment": complete,
        },
        CONTEXT,
        location_id="event-population-A",
    )
    return result


def test_posterior_mean_and_variance_match_shape_rate_conjugacy():
    result = evaluate()
    values = result.payload["values"]
    posterior_shape = 2.0 + 3
    posterior_rate = 10.0 + 20.0
    assert result.status == "computed"
    assert values["posterior_shape"] == pytest.approx(posterior_shape)
    assert values["posterior_exposure_s"] == pytest.approx(posterior_rate)
    assert values["mean_frequency_per_s"] == pytest.approx(posterior_shape / posterior_rate)
    assert values["variance_frequency_per_s2"] == pytest.approx(posterior_shape / posterior_rate**2)
    assert "posterior_predictive_occurrence_probability" not in values


def test_zero_confirmed_events_leave_prior_shape_unchanged():
    values = evaluate(confirmed_events=0).payload["values"]
    assert values["posterior_shape"] == pytest.approx(2.0)
    assert values["posterior_exposure_s"] == pytest.approx(30.0)
    assert values["mean_frequency_per_s"] == pytest.approx(2.0 / 30.0)


def test_uniform_exposure_scale_changes_rate_and_variance_by_expected_powers():
    base = evaluate().payload["values"]
    scaled = evaluate(prior_exposure_s=20.0, exposure_s=40.0).payload["values"]
    assert scaled["posterior_shape"] == pytest.approx(base["posterior_shape"])
    assert scaled["posterior_exposure_s"] == pytest.approx(2.0 * base["posterior_exposure_s"])
    assert scaled["mean_frequency_per_s"] == pytest.approx(base["mean_frequency_per_s"] / 2.0)
    assert scaled["variance_frequency_per_s2"] == pytest.approx(base["variance_frequency_per_s2"] / 4.0)


def test_expected_rate_equals_event_fraction_when_prior_matches_observation_basis():
    # Prior contributes one expected event per 10 seconds; data add four in 40 seconds.
    values = evaluate(prior_shape=1.0, prior_exposure_s=10.0, confirmed_events=4, exposure_s=40.0).payload["values"]
    assert values["mean_frequency_per_s"] == pytest.approx((1.0 + 4.0) / (10.0 + 40.0))
    assert values["mean_frequency_per_s"] == pytest.approx(0.1)


def test_incomplete_ascertainment_withholds_numeric_result():
    result = evaluate(complete=False)
    assert result.status == "needs_data"
    assert result.payload["values"] == {}


@pytest.mark.parametrize(
    "updates",
    [
        {"prior_shape": 0.0},
        {"prior_exposure_s": 0.0},
        {"exposure_s": 0.0},
        {"confirmed_events": -1},
        {"confirmed_events": 1.5},
    ],
)
def test_nonphysical_or_noninteger_count_inputs_are_rejected(updates):
    base = {
        "prior_shape": 2.0,
        "prior_exposure_s": 10.0,
        "confirmed_events": 3,
        "exposure_s": 20.0,
        "complete_ascertainment": True,
    }
    base.update(updates)
    with pytest.raises(ValidationError):
        CLIENT.evaluate("reliability.gamma_poisson", base, CONTEXT, location_id="event-population-A")
