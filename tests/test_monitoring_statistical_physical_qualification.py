"""Independent analytic score checks for the seven statistical detectors."""

import math

import numpy as np
import pytest

from process_monitoring.registry import create_detector


def score(name, training, evaluation, **parameters):
    detector = create_detector(name, **parameters)
    detector.fit(np.asarray(training, dtype=float))
    return np.asarray(detector.score_samples(np.asarray(evaluation, dtype=float)))


def test_zscore_matches_sample_standardization():
    actual = score("zscore", [[0.0], [1.0], [2.0]], [[3.0]])
    # The training mean is 1 and unbiased sample standard deviation is 1.
    assert actual == pytest.approx([2.0])


def test_robust_z_matches_median_absolute_deviation_scale():
    actual = score("robust_z", [[0.0], [0.0], [2.0], [2.0], [4.0]], [[4.0]])
    scale = 1.4826022185 * 2.0
    assert actual == pytest.approx([2.0 / scale])


def test_ewma_matches_independent_scalar_recursion():
    alpha = 0.4
    actual = score("ewma", [[-1.0], [1.0]], [[math.sqrt(2.0)], [0.0], [-math.sqrt(2.0)]], robust=False, alpha=alpha)
    normalized = [1.0, 0.0, -1.0]
    state = 0.0
    expected = []
    for value in normalized:
        state = alpha * value + (1.0 - alpha) * state
        expected.append(abs(state))
    assert actual == pytest.approx(expected)


def test_two_sided_cusum_matches_positive_negative_recurrence():
    actual = score("cusum", [[-1.0], [1.0]], [[math.sqrt(2.0)], [math.sqrt(2.0)], [-math.sqrt(2.0)]], robust=False, allowance=0.5)
    positive = negative = 0.0
    expected = []
    for value in [1.0, 1.0, -1.0]:
        positive = max(0.0, positive + value - 0.5)
        negative = max(0.0, negative - value - 0.5)
        expected.append(max(positive, negative))
    assert actual == pytest.approx(expected)


def test_gaussian_mahalanobis_matches_one_dimensional_sample_covariance():
    actual = score("gaussian", [[-1.0], [0.0], [1.0]], [[2.0]], regularization=0.25)
    sample_variance = 2.0 / 3.0
    assert actual == pytest.approx([4.0 / sample_variance], rel=1e-12)


def test_seasonal_residual_uses_phase_local_median_and_mad():
    training = [[0.0], [10.0], [2.0], [12.0], [0.0], [10.0], [2.0], [12.0]]
    actual = score("seasonal", training, [[4.0], [12.0]], period=2, scale_floor=0.5)
    scale = 1.4826022185
    assert actual == pytest.approx([3.0 / scale, 1.0 / scale])


def test_normal_upper_tail_score_matches_erfc_probability():
    actual = score("distribution_tail", [[-1.0], [0.0], [1.0]], [[0.0], [1.0]], distribution="normal", tail="upper")
    sigma = math.sqrt(2.0 / 3.0)
    expected = [-math.log(0.5), -math.log(0.5 * math.erfc((1.0 / sigma) / math.sqrt(2.0)))]
    assert actual == pytest.approx(expected, rel=1e-12)


def test_zscore_is_invariant_to_consistent_positive_affine_sensor_transform():
    base = score("zscore", [[0.0], [1.0], [2.0]], [[-1.0], [3.0]])
    transformed = score("zscore", [[7.0], [10.0], [13.0]], [[4.0], [16.0]])
    assert transformed == pytest.approx(base)
