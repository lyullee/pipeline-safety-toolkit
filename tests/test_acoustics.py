import json
import zipfile

import numpy as np
import pytest

from pipeline_integrity.acoustic_data import SheffieldArchive
from pipeline_integrity.acoustics import (
    DelayRequest,
    PipeSpan,
    RelativeTiming,
    SampledTrace,
    delay_feature_frames,
    estimate_delay,
    locate_on_span,
    localize_acoustic_leak,
    slice_trace,
    verify_acoustic_localization_record,
)
from pipeline_integrity.contracts import RunContext, digest


def traces(lag=23, n=4096, gain=1.0):
    a = np.random.default_rng(4).normal(size=n)
    b = np.zeros(n)
    if lag > 0:
        b[lag:] = a[:-lag]
    elif lag < 0:
        b[:lag] = a[-lag:]
    else:
        b = a.copy()
    base = {
        "asset_id": "pipe",
        "clock_id": "elapsed",
        "start_time_s": 0.0,
        "sample_period_s": 0.0005,
        "quantity_id": "wall_acceleration",
        "unit": "m/s^2",
        "origin": "synthetic",
        "source_digest": digest("authored noise shift"),
        "valid": (True,) * n,
    }
    return (
        SampledTrace(id="a", sensor_id="left", values=tuple(a * gain), **base),
        SampledTrace(id="b", sensor_id="right", values=tuple(b * 0.4 * gain), **base),
    )


def context():
    return RunContext(asset_id="pipe", scenario_id="known_delay", clock_id="elapsed", origin="synthetic")


def request(**updates):
    return DelayRequest(
        **{
            "maximum_delay_s": 0.08,
            "peak_exclusion_s": 0.003,
            "timing": RelativeTiming(
                right_minus_left_latency_s=0.0, absolute_error_bound_s=0.0001, source_id="synthetic_clock"
            ),
            **updates,
        }
    )


def span(**updates):
    return PipeSpan(
        **{
            "left_position_m": 0.0,
            "right_position_m": 100.0,
            "wave_speed_m_s": 1000.0,
            "wave_speed_error_m_s": 5.0,
            "propagation_source_id": "authored_constant_speed",
            "geometry_source_id": "authored_span",
            "left_sensor_id": "left",
            "right_sensor_id": "right",
            "source_bracketed": True,
            "single_path_nondispersive": True,
            **updates,
        }
    )


@pytest.mark.parametrize("lag", [-23, 0, 23])
@pytest.mark.parametrize("method", ["normalized_xcorr", "gcc_phat"])
def test_delay_sign_gain_and_sample_resolution_location(lag, method):
    a, b = traces(lag)
    r = estimate_delay(a, b, request(method=method), context())
    assert r["status"] == "estimated" and r["lag_samples"] == lag
    assert r["apparent_delay_s"] == pytest.approx(lag * 0.0005)
    assert r["peak_correlation_signed"] > 0.999
    location = locate_on_span(r, span(), context())
    truth = (100 - 1000 * lag * 0.0005) / 2
    assert location["position_m"] == pytest.approx(truth)
    assert (
        location["unconstrained_position_bounds_m"][0]
        <= truth
        <= location["unconstrained_position_bounds_m"][1]
    )
    assert not location["bounds_are_statistical_confidence_interval"]
    huge = estimate_delay(*traces(lag, gain=1e250), request(method=method), context())
    assert huge["lag_samples"] == lag
    json.dumps(huge, allow_nan=False)


def test_start_offsets_and_relative_latency_are_distinct_from_propagation():
    a, b = traces(lag=60)
    b = b.model_copy(update={"start_time_s": 0.01})
    timing = RelativeTiming(
        right_minus_left_latency_s=0.002, absolute_error_bound_s=0.0002, source_id="declared_latency"
    )
    r = estimate_delay(a, b, request(timing=timing), context())
    assert r["corrected_delay_s"] == pytest.approx(0.038)
    location = locate_on_span(r, span(velocity_m_s=100.0), context())
    assert location["position_m"] == pytest.approx((100 * 900 - 0.038 * 990000) / 2000)


def test_missing_samples_units_clock_and_rate_are_not_repaired():
    a, b = traces()
    for updated, pattern in [
        (b.model_copy(update={"unit": "Pa"}), "quantity"),
        (b.model_copy(update={"clock_id": "other"}), "clock"),
        (b.model_copy(update={"sample_period_s": 0.00051}), "rates"),
    ]:
        with pytest.raises(ValueError, match=pattern):
            estimate_delay(a, updated, request(), context())
    values = list(b.values)
    values[10] = None
    flags = list(b.valid)
    flags[10] = False
    invalid = b.model_copy(update={"values": tuple(values), "valid": tuple(flags)})
    r = estimate_delay(a, invalid, request(), context())
    assert r["status"] == "withheld" and r["sample_count"] == len(b.values)
    with pytest.raises(ValueError, match="budget"):
        estimate_delay(a, b, request(maximum_samples=100), context())


def test_periodic_ambiguity_noise_and_zero_energy_do_not_qualify_a_location():
    a, b = traces(n=4000)
    sine = np.sin(np.arange(4000) * 2 * np.pi / 50)
    periodic = estimate_delay(
        a.model_copy(update={"values": tuple(sine)}),
        b.model_copy(update={"values": tuple(sine)}),
        request(),
        context(),
    )
    assert periodic["status"] == "withheld" and "ambiguous_or_untestable_peak" in periodic["issues"]
    noise = b.model_copy(update={"values": tuple(np.random.default_rng(10).normal(size=4000))})
    r = estimate_delay(a, noise, request(), context())
    assert r["status"] == "withheld" and locate_on_span(r, span(), context())["status"] == "needs_data"
    zero = a.model_copy(update={"values": (0.0,) * 4000})
    r = estimate_delay(zero, b, request(), context())
    assert r["issues"] == ["insufficient_waveform_energy"]
    json.dumps(r, allow_nan=False)


def test_unresolved_timing_geometry_and_shared_noise_do_not_become_leak_evidence():
    a, b = traces(lag=0)
    r = estimate_delay(a, b, request(timing=None), context())
    assert r["status"] == "estimated" and r["corrected_delay_s"] is None
    assert locate_on_span(r, span(), context())["status"] == "needs_data"
    qualified = estimate_delay(a, b, request(), context())
    assert locate_on_span(qualified, span(source_bracketed=False), context())["status"] == "needs_data"
    qualified["corrected_delay_s"] = 0.05
    with pytest.raises(ValueError, match="unchanged"):
        locate_on_span(qualified, span(), context())


def test_interval_bounds_contain_flow_speed_timing_and_geometry_combinations():
    a, b = traces(lag=60)
    r = estimate_delay(a, b, request(), context())
    model = span(
        left_position_m=50.0,
        right_position_m=150.0,
        left_position_error_m=2.0,
        right_position_error_m=1.0,
        wave_speed_error_m_s=100.0,
        velocity_m_s=100.0,
        velocity_error_m_s=50.0,
        additional_delay_error_s=0.001,
    )
    result = locate_on_span(r, model, context())
    lo, hi = result["unconstrained_position_bounds_m"]
    rng = np.random.default_rng(5)
    for _ in range(200):
        left, right = rng.uniform(48, 52), rng.uniform(149, 151)
        c, u = rng.uniform(900, 1100), rng.uniform(50, 150)
        error = r["delay_error_bound_s"] + 0.001
        delay = r["corrected_delay_s"] + rng.uniform(-error, error)
        x = left + ((right - left) * (c - u) - delay * (c * c - u * u)) / (2 * c)
        assert lo <= x <= hi


def test_window_features_preserve_time_quality_and_reject_reordered_results():
    a, b = traces(n=4096)
    pairs = [(slice_trace(a, i, 1024), slice_trace(b, i, 1024)) for i in (0, 1024, 2048)]
    results = [estimate_delay(x, y, request(), context()) for x, y in pairs]
    frames = tuple(delay_feature_frames(results, stream_id="delay_diagnostics"))
    assert [f.time_s for f in frames] == sorted(f.time_s for f in frames)
    assert frames[0].units["apparent_delay"] == "s"
    with pytest.raises(ValueError, match="strictly increase"):
        tuple(delay_feature_frames(results[::-1], stream_id="bad"))


def test_sheffield_reader_rejects_combined_tables_and_preserves_native_acceleration_units(tmp_path):
    import pandas as pd

    member = "Acoustic Data (Leakage Experiments)/Coherence (simultaneous recording)/3.6mmDirect_2.8bar/test#1/test_1.csv"
    combined = "Acoustic Data (Leakage Experiments)/All_Data/combined.csv"
    path = tmp_path / "authored_schema.zip"
    table = pd.DataFrame(
        {
            "Time (s)": np.arange(128) / 8192,
            "Accelerometer 1 (m/s^2)": np.arange(128, dtype=float),
            "Accelerometer 2 (m/s^2)": np.arange(128, dtype=float) + 2,
        }
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, table.to_csv(index=False))
        z.writestr(combined, table.to_csv(index=False))
    with SheffieldArchive(path) as reader:
        a, b, report = reader.read_pair(member, start_index=16, sample_count=64)
        assert a.values[0] == 16.0 and b.values[0] == 18.0
        assert a.unit == "m/s^2" and a.start_time_s == 16 / 8192
        assert report["wave_speed_m_s"] is None and not report["amplitude_scaling_applied"]
        with pytest.raises(ValueError, match="combined spatial"):
            reader.read_pair(combined, sample_count=64)


def test_saved_acoustic_job_has_the_same_direction_and_location_through_cli(tmp_path):
    from pipeline_integrity.cli import main

    a, b = traces(lag=23)
    job = {
        "left": a.model_dump(mode="json"),
        "right": b.model_dump(mode="json"),
        "specification": request().model_dump(mode="json"),
        "span": span().model_dump(mode="json"),
        "context": context().model_dump(mode="json"),
    }
    source = tmp_path / "job.json"
    output = tmp_path / "result.json"
    source.write_text(json.dumps(job), encoding="utf-8")
    assert main(["acoustic-delay", str(source), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["delay"]["lag_samples"] == 23
    assert result["location"]["position_m"] == pytest.approx((100 - 1000 * 23 * 0.0005) / 2)


def test_composed_localization_binds_sensor_order_and_child_receipts():
    a, b = traces(lag=23)
    composed = localize_acoustic_leak(a, b, request(), span(), context())
    assert verify_acoustic_localization_record(composed)
    altered = json.loads(json.dumps(composed))
    altered["location"]["position_m"] += 1.0
    with pytest.raises(ValueError, match="composition digest"):
        verify_acoustic_localization_record(altered)
    with pytest.raises(ValueError, match="position intervals"):
        localize_acoustic_leak(a, b, request(), span().model_copy(update={"left_position_m": 100.0}), context())
    shifted = localize_acoustic_leak(a, b, request(), span(left_position_m=1.0), context())
    assert shifted["location"]["span"]["left_position_m"] == 1.0
