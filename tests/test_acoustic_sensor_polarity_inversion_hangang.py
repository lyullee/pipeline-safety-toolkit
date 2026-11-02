"""Synthetic sensor-polarity inversion check for acoustic delay semantics."""

import numpy as np

from pipeline_integrity.acoustics import DelayRequest, SampledTrace, estimate_delay
from pipeline_integrity.contracts import RunContext, digest


def test_sensor_polarity_inversion_preserves_arrival_when_absolute_polarity_is_reviewed():
    count = 2048
    lag = 19
    dt = 0.001
    source = np.random.default_rng(210).normal(size=count)
    received = np.zeros(count)
    received[lag:] = -0.4 * source[:-lag]
    common = dict(
        asset_id="synthetic_pipe",
        clock_id="shared_elapsed",
        start_time_s=0.0,
        sample_period_s=dt,
        quantity_id="wall_acceleration",
        unit="m/s^2",
        origin="synthetic",
        source_digest=digest("authored broadband arrival with sensor polarity inversion"),
        valid=(True,) * count,
    )
    left = SampledTrace(id="trace-left", sensor_id="sensor-left", values=tuple(source), **common)
    right = SampledTrace(id="trace-right", sensor_id="sensor-right", values=tuple(received), **common)
    context = RunContext(
        asset_id="synthetic_pipe", scenario_id="polarity-inversion", clock_id="shared_elapsed", origin="synthetic"
    )
    absolute = estimate_delay(
        left, right,
        DelayRequest(
            maximum_delay_s=0.1,
            polarity="absolute",
            minimum_peak_correlation=0.95,
            minimum_relative_peak_gap=0.05,
            peak_exclusion_s=0.004,
        ),
        context,
    )
    assert absolute["status"] == "estimated"
    assert absolute["lag_samples"] == lag
    assert absolute["apparent_delay_s"] == lag * dt
    assert absolute["peak_correlation_signed"] < -0.999999
    assert absolute["primary_peak_score"] > 0.999999

    positive_only = estimate_delay(
        left, right,
        DelayRequest(
            maximum_delay_s=0.1,
            polarity="positive",
            minimum_peak_correlation=0.95,
            minimum_relative_peak_gap=0.05,
            peak_exclusion_s=0.004,
        ),
        context,
    )
    assert positive_only["status"] == "withheld"
    assert "weak_peak_correlation" in positive_only["issues"]
