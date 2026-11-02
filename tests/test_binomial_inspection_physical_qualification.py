"""Exact binomial inspection bounds checked with independent binomial tails."""

import math
import runpy

import pytest

from pipeline_integrity.inspection_statistics import InspectionTrial, evaluate_inspection_statistics


CASES, CONTEXT = runpy.run_path("examples/inspection_statistics.py")["make_cases"]()
BASE = CASES["inspection.binomial_evidence"]


def evaluate(n, k, confidence=0.95, independent=True, response_kind="detection_at_declared_size"):
    parameters = dict(BASE.parameters)
    parameters.update(
        opportunities=n,
        positive_responses=k,
        confidence_level=confidence,
        independent_trials_declared=independent,
        response_kind=response_kind,
    )
    flawed = response_kind == "detection_at_declared_size"
    trials = tuple(
        InspectionTrial(
            observation_id=f"qual-{n}-{i}",
            specimen_id=f"qual-specimen-{n}-{i}",
            clock_id=CONTEXT.clock_id,
            truth_flawed=flawed,
            detected=i < k,
            flaw_size_m=0.002 if flawed else None,
        )
        for i in range(n)
    )
    request = BASE.model_copy(
        update={
            "parameters": parameters,
            "trials": trials,
            "declared_detection_size_m": 0.002 if flawed else None,
        }
    )
    return evaluate_inspection_statistics(request, CONTEXT)


def binomial_cdf(n, p, k):
    return sum(math.comb(n, j) * p**j * (1.0 - p) ** (n - j) for j in range(k + 1))


def binomial_survival(n, p, k):
    return sum(math.comb(n, j) * p**j * (1.0 - p) ** (n - j) for j in range(k, n + 1))


def bisect_root(fn, target):
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def test_all_successes_exact_one_sided_lower_bound():
    n, confidence = 8, 0.95
    result = evaluate(n, n, confidence)
    expected_lower = (1.0 - confidence) ** (1.0 / n)
    assert result["status"] == "computed"
    assert result["values"]["probability_estimate"] == pytest.approx(1.0)
    assert result["values"]["one_sided_lower_probability"] == pytest.approx(expected_lower, rel=2e-12)
    assert result["values"]["one_sided_upper_probability"] == pytest.approx(1.0)


def test_zero_successes_exact_one_sided_upper_bound():
    n, confidence = 9, 0.90
    result = evaluate(n, 0, confidence, response_kind="false_call_on_blank")
    expected_upper = 1.0 - (1.0 - confidence) ** (1.0 / n)
    assert result["values"]["probability_estimate"] == pytest.approx(0.0)
    assert result["values"]["one_sided_lower_probability"] == pytest.approx(0.0)
    assert result["values"]["one_sided_upper_probability"] == pytest.approx(expected_upper, rel=2e-12)
    assert result["values"]["response_kind"] == "false_call_on_blank"


def test_mixed_counts_match_independently_bisected_binomial_tails():
    n, k, confidence = 6, 2, 0.90
    alpha = 1.0 - confidence
    expected_lower = bisect_root(lambda p: binomial_survival(n, p, k), alpha)
    expected_upper = bisect_root(lambda p: binomial_survival(n, p, k + 1), confidence)
    result = evaluate(n, k, confidence)
    assert result["values"]["probability_estimate"] == pytest.approx(k / n)
    assert result["values"]["one_sided_lower_probability"] == pytest.approx(expected_lower, rel=2e-10)
    assert result["values"]["one_sided_upper_probability"] == pytest.approx(expected_upper, rel=2e-10)


@pytest.mark.parametrize("n,k", [(2, 0), (5, 3), (12, 11)])
def test_mixed_and_boundary_counts_preserve_ordered_probability_bounds(n, k):
    result = evaluate(n, k, 0.8)
    values = result["values"]
    assert 0.0 <= values["one_sided_lower_probability"] <= k / n
    assert k / n <= values["one_sided_upper_probability"] <= 1.0


def test_nonindependent_trials_are_withheld():
    result = evaluate(10, 8, independent=False)
    assert result["status"] == "needs_data"
    assert result["values"] == {}
