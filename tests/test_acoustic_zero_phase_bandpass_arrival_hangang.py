"""Band-limited acoustic delay invariance under zero-phase digital filtering."""

import numpy as np
import pytest

from pipeline_integrity.acoustics import DelayRequest, SampledTrace, estimate_delay
from pipeline_integrity.contracts import RunContext, digest


@pytest.mark.parametrize("lag_samples", [-23, 23])
def test_zero_phase_bandpass_preserves_known_sensor_arrival(lag_samples):
    sample_period_s = 0.00025
    sample_count = 8192
    time_s = np.arange(sample_count) * sample_period_s
    tau_s = time_s - 0.8
    envelope = np.exp(-((tau_s / 0.20) ** 2))
    chirp_phase = 2 * np.pi * (240.0 * tau_s + 0.5 * 500.0 * tau_s**2)
    inband_burst = envelope * np.sin(chirp_phase)
    out_of_band_interference = 0.5 * envelope * np.sin(2 * np.pi * 1000.0 * tau_s)
    source = inband_burst + out_of_band_interference
    received = np.zeros(sample_count)
    if lag_samples > 0:
        received[lag_samples:] = 0.35 * source[:-lag_samples]
    else:
        received[:lag_samples] = 0.35 * source[-lag_samples:]

    common = dict(
        asset_id="synthetic_pipe",
        clock_id="shared_elapsed",
        start_time_s=0.0,
        sample_period_s=sample_period_s,
        quantity_id="wall_acceleration",
        unit="m/s^2",
        origin="synthetic",
        source_digest=digest("known shifted acoustic chirp plus out-of-band sensor interference"),
        valid=(True,) * sample_count,
    )
    left = SampledTrace(id="source_trace", sensor_id="sensor_A", values=tuple(source), **common)
    right = SampledTrace(id="received_trace", sensor_id="sensor_B", values=tuple(received), **common)
    context = RunContext(
        asset_id="synthetic_pipe", scenario_id="zero-phase-filter", clock_id="shared_elapsed", origin="synthetic"
    )
    result = estimate_delay(
        left,
        right,
        DelayRequest(
            maximum_delay_s=0.05,
            band_hz=(180.0, 420.0),
            filter_order=4,
            polarity="positive",
            minimum_overlap_fraction=0.8,
            minimum_peak_correlation=0.9,
            peak_exclusion_s=0.002,
            minimum_relative_peak_gap=0.05,
        ),
        context,
    )
    assert result["status"] == "estimated"
    assert result["lag_samples"] == lag_samples
    assert result["apparent_delay_s"] == pytest.approx(lag_samples * sample_period_s, abs=1e-12)
    assert result["peak_correlation_signed"] > 0.99
