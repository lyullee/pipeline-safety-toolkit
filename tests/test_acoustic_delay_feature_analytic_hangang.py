"""Analytic arrival-delay and quality transfer into timestamped SignalFrames."""

import numpy as np
import pytest

from pipeline_integrity.acoustics import (
    DelayRequest,
    RelativeTiming,
    SampledTrace,
    delay_feature_frames,
    estimate_delay,
)
from pipeline_integrity.contracts import RunContext, digest


def test_known_sample_delays_and_invalid_window_transfer_without_changing_signal_meaning():
    count = 2048
    dt = 0.0005
    context = RunContext(
        asset_id="synthetic_pipe", scenario_id="windowed_delay", clock_id="elapsed_s", origin="synthetic"
    )
    request = DelayRequest(
        maximum_delay_s=0.02,
        timing=RelativeTiming(
            right_minus_left_latency_s=0.0,
            absolute_error_bound_s=0.0,
            source_id="authored_common_clock",
        ),
        minimum_peak_correlation=0.9,
        minimum_relative_peak_gap=0.05,
        peak_exclusion_s=0.0015,
    )
    rng = np.random.default_rng(7601)
    results = []
    expected_times = []
    expected_delays = (7, -11, 19)
    window_starts = (0.0, 0.25, 0.5, 0.75)

    for index, start_s in enumerate(window_starts):
        left_values = rng.normal(size=count)
        lag = expected_delays[index] if index < len(expected_delays) else 13
        right_values = np.zeros(count)
        if lag > 0:
            right_values[lag:] = 0.4 * left_values[:-lag]
        else:
            right_values[:lag] = 0.4 * left_values[-lag:]
        valid = [True] * count
        if index == 3:
            valid[count // 2] = False
        common = dict(
            asset_id="synthetic_pipe",
            clock_id="elapsed_s",
            start_time_s=start_s,
            sample_period_s=dt,
            quantity_id="wall_acceleration",
            unit="m/s^2",
            origin="synthetic",
            source_digest=digest(("known sample delay window", index)),
        )
        left = SampledTrace(
            id=f"left_{index}", sensor_id="sensor_left", values=tuple(left_values), valid=(True,) * count, **common
        )
        right = SampledTrace(
            id=f"right_{index}", sensor_id="sensor_right", values=tuple(right_values), valid=tuple(valid), **common
        )
        results.append(estimate_delay(left, right, request, context))
        expected_times.append(start_s + (count - 1) * dt / 2)

    frames = tuple(delay_feature_frames(results, stream_id="analytic_delay_features"))
    assert len(frames) == len(results)
    assert [frame.time_s for frame in frames] == pytest.approx(expected_times, abs=1e-12)
    for index, lag in enumerate(expected_delays):
        frame = frames[index]
        assert frame.values["apparent_delay"] == pytest.approx(lag * dt, abs=1e-12)
        assert frame.values["peak_correlation"] > 0.999999
        assert frame.valid["apparent_delay"]
        assert frame.valid["peak_correlation"]
        assert frame.units["apparent_delay"] == "s"
        assert frame.units["peak_correlation"] == "1"
    withheld = frames[-1]
    assert results[-1]["status"] == "withheld"
    assert withheld.values["apparent_delay"] is None
    assert not any(withheld.valid.values())
