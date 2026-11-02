"""Independent synthetic check of per-band delay and location constraints.

The same known digital arrival is processed through two reviewed bands. Each
band receives its own propagation speed and returns a conditional location
interval; the combined result is only their deterministic intersection.
"""

import numpy as np
import pytest

from pipeline_integrity.acoustic_multiband import (
    MultibandAcousticRequest,
    PropagationBand,
    evaluate_multiband_acoustics,
    verify_multiband_acoustic_record,
)
from pipeline_integrity.acoustics import DelayRequest, PipeSpan, RelativeTiming, SampledTrace
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest


def test_multiband_delay_and_each_location_contain_closed_form_arrival():
    count = 4096
    sample_period_s = 0.001
    lag_samples = 8
    rng = np.random.default_rng(19473)
    source = rng.normal(size=count)
    received = np.zeros(count)
    received[lag_samples:] = 0.37 * source[:-lag_samples]
    common = dict(
        asset_id="synthetic_acoustic_span",
        clock_id="shared_elapsed",
        start_time_s=0.0,
        sample_period_s=sample_period_s,
        quantity_id="wall_acceleration",
        unit="m/s^2",
        origin="synthetic",
        valid=(True,) * count,
    )
    left = SampledTrace(
        id="left_trace", sensor_id="left_sensor", values=tuple(source),
        source_digest=digest("authored broadband acoustic trace"), **common
    )
    right = SampledTrace(
        id="right_trace", sensor_id="right_sensor", values=tuple(received),
        source_digest=digest("authored delayed broadband acoustic trace"), **common
    )
    evidence = tuple(
        EvidenceRef(
            id=identity,
            citation="Authored synthetic propagation review",
            origin="synthetic",
            sha256=digest(("multiband-review", identity)),
        )
        for identity in ("review-low", "review-high")
    )
    context = RunContext(
        asset_id="synthetic_acoustic_span",
        scenario_id="two-band-known-arrival",
        clock_id="shared_elapsed",
        origin="synthetic",
        evidence=evidence,
    )
    delay = DelayRequest(
        maximum_delay_s=0.02,
        timing=RelativeTiming(
            right_minus_left_latency_s=0.0,
            absolute_error_bound_s=0.0,
            source_id="authored_common_clock",
        ),
        minimum_peak_correlation=0.9,
        minimum_relative_peak_gap=0.05,
        peak_exclusion_s=0.002,
    )

    def span(speed, source_id):
        return PipeSpan(
            left_position_m=0.0,
            right_position_m=100.0,
            wave_speed_m_s=speed,
            wave_speed_error_m_s=0.0,
            propagation_source_id=source_id,
            geometry_source_id="shared-authored-geometry",
            left_sensor_id="left_sensor",
            right_sensor_id="right_sensor",
            source_bracketed=True,
            single_path_nondispersive=True,
        )

    request = MultibandAcousticRequest(
        id="two-band-arrival",
        left=left,
        right=right,
        delay=delay,
        bands=(
            PropagationBand(
                id="low", frequency_bounds_hz=(20.0, 90.0), span=span(990.0, "low-speed"),
                narrowband_propagation_review_evidence_id="review-low",
            ),
            PropagationBand(
                id="high", frequency_bounds_hz=(120.0, 220.0), span=span(1010.0, "high-speed"),
                narrowband_propagation_review_evidence_id="review-high",
            ),
        ),
    )

    record = evaluate_multiband_acoustics(request, context)
    assert record["status"] == "estimated"
    assert record["independent_band_assumption"] is False
    assert record["probability_calculated"] is False
    assert len(record["bands"]) == 2
    intervals = []
    for row, speed in zip(record["bands"], (990.0, 1010.0), strict=True):
        local = row["localization"]
        assert local["status"] == "estimated"
        assert local["delay"]["lag_samples"] == lag_samples
        assert local["delay"]["corrected_delay_s"] == pytest.approx(
            lag_samples * sample_period_s, abs=sample_period_s
        )
        lo, hi = local["location"]["unconstrained_position_bounds_m"]
        # Equal sensor endpoints and zero flow give x=(L-c*tau)/2.
        exact_position = (100.0 - speed * lag_samples * sample_period_s) / 2.0
        assert lo <= exact_position <= hi
        intervals.append((lo, hi))

    lower = max(item[0] for item in intervals)
    upper = min(item[1] for item in intervals)
    assert record["cross_band_position_constraint_m"] == pytest.approx((lower, upper))
    assert lower <= 46.0 <= upper
    assert verify_multiband_acoustic_record(record, request=request, context=context)
