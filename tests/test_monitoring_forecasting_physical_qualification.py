"""Small analytic qualification set for causal lagged regression forecasting."""

import math

import numpy as np
import pytest

from process_monitoring import Monitor, SignalBatch, create_detector


RADIUS, ANGLE = 0.98, 0.24
AR1 = 2.0 * RADIUS * math.cos(ANGLE)
AR2 = -(RADIUS**2)


def ar2(length, initial=(0.0, 1.0)):
    values = [float(initial[0]), float(initial[1])]
    for _ in range(2, length):
        values.append(AR1 * values[-1] + AR2 * values[-2])
    return np.asarray(values)


def batch(values, *, episodes=None):
    values = np.asarray(values, dtype=float).reshape(-1, 1)
    return SignalBatch(
        np.arange(len(values), dtype=float),
        values,
        ("pressure",),
        units=("MPa",),
        episodes=episodes,
    )


def fit(values):
    return Monitor(
        create_detector("lag_ridge", window=3, alpha=0.0), threshold=1.0e12
    ).fit(batch(values))


def test_known_ar2_forecast_residual_matches_closed_form_one_step_oracle():
    training = ar2(100)
    monitor = fit(training)
    history = ar2(102)[-2:]
    clean = ar2(12, initial=tuple(history))
    observed = clean.copy()
    observed[-1] += 0.2
    result = monitor.score(batch(observed))
    expected_forecast = AR1 * clean[-2] + AR2 * clean[-3]
    expected_score = abs(observed[-1] - expected_forecast) / (
        monitor.detector.scale[0] * monitor.detector.error_scale[0]
    )
    assert result.valid[-1]
    assert result.scores[-1] == pytest.approx(expected_score, rel=2e-5)
    assert result.scores[-1] > 1.0


def test_forecast_residual_is_invariant_to_consistent_sensor_affine_scaling():
    values = ar2(100)
    first = fit(values)
    second = fit(7.0 * values + 11.0)
    future = ar2(12, initial=tuple(values[-2:]))
    future[-1] += 0.1
    original_result = first.score(batch(future))
    transformed_result = second.score(batch(7.0 * future + 11.0))
    assert original_result.scores[-1] == pytest.approx(
        transformed_result.scores[-1], rel=2e-5, abs=1e-8
    )


def test_appending_future_data_does_not_change_any_prior_forecast_score():
    training = ar2(100)
    monitor = fit(training)
    future = ar2(14, initial=tuple(training[-2:]))
    prefix = monitor.score(batch(future[:10]))
    extended = future.copy()
    extended[-1] += 100.0
    full = monitor.score(batch(extended))
    np.testing.assert_allclose(prefix.scores, full.scores[:10], equal_nan=True)


def test_episode_boundary_withholds_windows_without_cross_episode_forecasts():
    model = fit(ar2(100))
    scoring = np.r_[ar2(5), ar2(5, initial=(20.0, 21.0))]
    episodes = np.r_[np.zeros(5, dtype=int), np.ones(5, dtype=int)]
    result = model.score(batch(scoring, episodes=episodes))
    assert result.valid.tolist() == [False, False, True, True, True, False, False, True, True, True]
