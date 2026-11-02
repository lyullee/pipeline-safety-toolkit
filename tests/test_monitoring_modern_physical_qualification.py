"""Independent score oracle for one maintained modern-library detector (ECOD)."""

import math

import numpy as np
import pytest

from process_monitoring.registry import create_detector


TRAIN = np.asarray([[-2.0, -2.0], [-1.0, -1.0], [1.0, 1.0], [2.0, 2.0]])
QUERIES = np.asarray([[-3.0, -3.0], [0.0, 0.0], [3.0, 3.0]])


def ecod_oracle(training, query):
    """Direct ECDF/log-tail calculation from a frozen training set plus one query."""
    combined = np.vstack([training, query])
    left = []
    right = []
    for j in range(combined.shape[1]):
        column = combined[:, j]
        x = query[j]
        cdf = np.count_nonzero(column <= x) / len(column)
        reverse_cdf = np.count_nonzero(-column <= -x) / len(column)
        left.append(-math.log(cdf))
        right.append(-math.log(reverse_cdf))

    left = np.asarray(left)
    right = np.asarray(right)
    # Independent third-central-moment skewness formula; no SciPy estimator call.
    centered = combined - combined.mean(axis=0)
    m2 = np.mean(centered**2, axis=0)
    m3 = np.mean(centered**3, axis=0)
    signed_skew = np.sign(m3 / np.power(m2, 1.5))
    skew_score = -left * np.sign(signed_skew - 1) + right * np.sign(signed_skew + 1)
    return float(np.maximum(np.maximum(left, right), skew_score).sum())


def scores(training=TRAIN, queries=QUERIES):
    detector = create_detector("ecod", n_jobs=1)
    detector.fit(np.asarray(training, dtype=float))
    return np.asarray(detector.score_samples(np.asarray(queries, dtype=float)))


def test_ecod_tail_scores_match_independent_empirical_log_tail_oracle():
    actual = scores()
    expected = [ecod_oracle(TRAIN, row) for row in QUERIES]
    assert actual == pytest.approx(expected, rel=1e-13, abs=1e-13)


def test_ecod_score_is_invariant_to_positive_affine_feature_unit_change():
    base = scores()
    transformed = scores(TRAIN * 4.0 + 17.0, QUERIES * 4.0 + 17.0)
    assert transformed == pytest.approx(base, rel=1e-13, abs=1e-13)


def test_ecod_assigns_greater_score_to_both_extreme_tails_than_the_center():
    actual = scores()
    assert actual[0] == pytest.approx(actual[2], rel=1e-13, abs=1e-13)
    assert actual[0] > actual[1]
    assert np.isfinite(actual).all()
