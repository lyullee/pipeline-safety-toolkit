"""Independent Bayes update checks for a no-indication inspection outcome."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_inspection_population",
    scenario_id="no_indication_bayes_update",
    clock_id="single_declared_inspection_opportunity",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently authored Bayes reference cases", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(weights, pod, coverage):
    return CLIENT.evaluate(
        "inspection.no_indication_update",
        {
            "prior_weights": list(weights),
            "probabilities_of_detection": list(pod),
            "coverage_fraction": coverage,
        },
        CONTEXT,
        location_id="inspection-zone-A",
    )


def oracle(weights, pod, coverage):
    total = math.fsum(weights)
    prior = [weight / total for weight in weights]
    no_indication_given_bin = [1.0 - coverage * probability for probability in pod]
    likelihood = math.fsum(p * q for p, q in zip(prior, no_indication_given_bin, strict=True))
    if likelihood == 0.0:
        return likelihood, None
    return likelihood, [p * q / likelihood for p, q in zip(prior, no_indication_given_bin, strict=True)]


def test_mixture_likelihood_and_posterior_match_direct_bayes_rule():
    weights, pod, coverage = [2.0, 5.0, 3.0], [0.1, 0.6, 0.9], 0.8
    expected_likelihood, expected_posterior = oracle(weights, pod, coverage)
    result = evaluate(weights, pod, coverage)
    assert result.status == "computed"
    values = result.payload["values"]
    assert values["probability_of_no_indication"] == pytest.approx(expected_likelihood, abs=1e-15)
    assert values["posterior_weights"] == pytest.approx(expected_posterior, abs=1e-15)
    assert math.fsum(values["posterior_weights"]) == pytest.approx(1.0, abs=1e-15)


def test_zero_coverage_leaves_prior_distribution_unchanged():
    result = evaluate([1.0, 4.0, 5.0], [0.0, 0.8, 1.0], 0.0)
    assert result.status == "computed"
    assert result.payload["values"]["probability_of_no_indication"] == pytest.approx(1.0)
    assert result.payload["values"]["posterior_weights"] == pytest.approx([0.1, 0.4, 0.5])


def test_common_prior_weight_scaling_does_not_change_posterior_or_likelihood():
    weights, pod, coverage = [2.0, 5.0, 3.0], [0.1, 0.6, 0.9], 0.8
    base = evaluate(weights, pod, coverage).payload["values"]
    scaled = evaluate([17.0 * w for w in weights], pod, coverage).payload["values"]
    assert scaled["probability_of_no_indication"] == pytest.approx(base["probability_of_no_indication"], abs=1e-15)
    assert scaled["posterior_weights"] == pytest.approx(base["posterior_weights"], abs=1e-15)


def test_more_inspection_coverage_reduces_no_indication_likelihood_and_high_pod_mass():
    weights, pod = [0.4, 0.6], [0.2, 0.9]
    low = evaluate(weights, pod, 0.25).payload["values"]
    high = evaluate(weights, pod, 0.75).payload["values"]
    assert high["probability_of_no_indication"] < low["probability_of_no_indication"]
    assert high["posterior_weights"][1] < low["posterior_weights"][1]
    for coverage, values in ((0.25, low), (0.75, high)):
        expected, posterior = oracle(weights, pod, coverage)
        assert values["probability_of_no_indication"] == pytest.approx(expected)
        assert values["posterior_weights"] == pytest.approx(posterior)


def test_zero_prior_mass_does_not_make_perfect_pod_bin_impossible():
    result = evaluate([1.0, 0.0], [0.3, 1.0], 1.0)
    assert result.status == "computed"
    assert result.payload["values"]["probability_of_no_indication"] == pytest.approx(0.7)
    assert result.payload["values"]["posterior_weights"] == pytest.approx([1.0, 0.0])


def test_zero_likelihood_has_no_conditional_posterior():
    result = evaluate([2.0, 3.0], [1.0, 1.0], 1.0)
    assert result.status == "out_of_scope"
    assert result.payload["values"] == {}


@pytest.mark.parametrize(
    "weights,pod,coverage",
    [([0.0, 0.0], [0.2, 0.7], 0.5), ([1.0], [0.2, 0.7], 0.5), ([1.0, 2.0], [-0.1, 0.8], 0.5), ([1.0, 2.0], [0.2, 0.8], 1.1)],
)
def test_invalid_prior_pod_alignment_or_coverage_is_rejected(weights, pod, coverage):
    with pytest.raises(ValidationError):
        evaluate(weights, pod, coverage)
