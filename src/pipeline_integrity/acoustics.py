"""Paired waveform delay estimates and bounded, conditional one-path pipe locations."""

import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest


class SampledTrace(Contract):
    id: Name
    asset_id: Name
    sensor_id: Name
    clock_id: Name
    start_time_s: Nonnegative
    sample_period_s: Positive
    quantity_id: Name
    unit: Name
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    values: tuple[float | None, ...] = Field(min_length=8, max_length=2000000)
    valid: tuple[bool, ...]

    @model_validator(mode="after")
    def samples(self):
        if len(self.values) != len(self.valid) or any(
            ok and value is None for ok, value in zip(self.valid, self.values, strict=True)
        ):
            raise ValueError("waveform values and validity must align")
        end = self.start_time_s + (len(self.values) - 1) * self.sample_period_s
        if (
            not math.isfinite(end)
            or not math.isfinite(1 / self.sample_period_s)
            or self.start_time_s + self.sample_period_s == self.start_time_s
        ):
            raise ValueError("the waveform clock cannot represent its sample times")
        return self


def slice_trace(trace: SampledTrace, start_index: int, sample_count: int):
    if (
        type(start_index) is not int
        or type(sample_count) is not int
        or start_index < 0
        or sample_count < 8
        or start_index + sample_count > len(trace.values)
    ):
        raise ValueError("select an explicit in-range waveform sample interval")
    return trace.model_copy(
        update={
            "id": f"{trace.id}:{start_index}:{sample_count}",
            "start_time_s": trace.start_time_s + start_index * trace.sample_period_s,
            "values": trace.values[start_index : start_index + sample_count],
            "valid": trace.valid[start_index : start_index + sample_count],
        }
    )


class RelativeTiming(Contract):
    right_minus_left_latency_s: float
    absolute_error_bound_s: Nonnegative
    source_id: Name


class DelayRequest(Contract):
    method: Literal["normalized_xcorr", "gcc_phat"] = "normalized_xcorr"
    maximum_delay_s: Positive
    timing: RelativeTiming | None = None
    band_hz: tuple[Positive, Positive] | None = None
    filter_order: int = Field(default=4, ge=1, le=10)
    detrend: Literal["constant", "linear"] = "constant"
    polarity: Literal["positive", "absolute"] = "positive"
    minimum_overlap_fraction: float = Field(default=0.75, gt=0, le=1)
    minimum_peak_correlation: Fraction = 0.2
    peak_exclusion_s: Positive = 0.005
    minimum_relative_peak_gap: Fraction = 0.05
    minimum_rms: Nonnegative = 0.0
    coherence_segment_samples: int = Field(default=1024, ge=16)
    minimum_mean_coherence: Fraction = 0.0
    maximum_samples: int = Field(default=1000000, ge=32, le=2000000)
    maximum_relative_rate_difference: float = Field(default=1e-9, ge=0, le=1e-5)
    allow_peak_at_search_boundary: bool = False

    @model_validator(mode="after")
    def band(self):
        if self.band_hz and self.band_hz[0] >= self.band_hz[1]:
            raise ValueError("band edges must strictly increase")
        return self


def _body_with_digest(body):
    return {**body, "result_digest": digest(body)}


def estimate_delay(left: SampledTrace, right: SampledTrace, request: DelayRequest, context: RunContext):
    from scipy.fft import next_fast_len
    from scipy.signal import butter, coherence, correlate, correlation_lags, detrend, sosfiltfilt

    if (left.asset_id, left.clock_id) != (context.asset_id, context.clock_id) or (
        right.asset_id,
        right.clock_id,
    ) != (context.asset_id, context.clock_id):
        raise ValueError("waveforms and context require the same asset and clock")
    if left.sensor_id == right.sensor_id or left.id == right.id:
        raise ValueError("two distinct sensor records are required")
    if (left.quantity_id, left.unit, left.origin) != (right.quantity_id, right.unit, right.origin):
        raise ValueError("paired waveforms must share their quantity, unit and generation origin")
    n = len(left.values)
    if n != len(right.values) or n < 32 or n > request.maximum_samples:
        raise ValueError("paired sample counts must agree and fit the declared analysis budget")
    dt = left.sample_period_s
    if abs(right.sample_period_s / dt - 1) > request.maximum_relative_rate_difference:
        raise ValueError("sample rates differ; no implicit resampling or clock repair is performed")
    if request.band_hz and request.band_hz[1] >= 0.5 / dt:
        raise ValueError("the analysis band must lie strictly below Nyquist")
    drift = abs(right.sample_period_s - dt) * (n - 1)
    latency = request.timing.right_minus_left_latency_s if request.timing else 0.0
    offset = right.start_time_s - left.start_time_s
    body = {
        "schema": "pipeline_integrity.acoustic_delay.v1",
        "context": context.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "left_id": left.id,
        "right_id": right.id,
        "input_digests": {"left": digest(left), "right": digest(right)},
        "source_digests": {"left": left.source_digest, "right": right.source_digest},
        "input_origin": left.origin,
        "quantity_id": left.quantity_id,
        "unit": left.unit,
        "sample_period_s": dt,
        "sample_count": n,
        "maximum_rate_drift_s": drift,
        "analysis_time_s": (left.start_time_s + right.start_time_s) / 2 + (n - 1) * dt / 2,
        "delay_definition": "arrival_at_right_minus_arrival_at_left",
        "position_calculated": False,
        "probability_calculated": False,
        "field_validation_claim": False,
        "backend": {"numpy": version("numpy"), "scipy": version("scipy")},
        "implementation_sha256": file_digest(Path(__file__)),
    }
    missing = not all(left.valid) or not all(right.valid)
    if missing:
        return _body_with_digest(
            {**body, "status": "withheld", "issues": ["invalid_samples_not_compressed_or_interpolated"]}
        )
    raw = [np.array(t.values, dtype=float) for t in (left, right)]
    gains = [float(np.max(np.abs(v))) for v in raw]
    a, b = [
        detrend(v / gain if gain else v, type=request.detrend) for v, gain in zip(raw, gains, strict=True)
    ]
    if request.band_hz:
        sos = butter(request.filter_order, request.band_hz, btype="bandpass", fs=1 / dt, output="sos")
        a, b = sosfiltfilt(sos, a), sosfiltfilt(sos, b)
    normalized_rms = [float(np.sqrt(np.mean(v**2))) for v in (a, b)]
    rms = [r * g for r, g in zip(normalized_rms, gains, strict=True)]
    if not all(math.isfinite(v) and v > request.minimum_rms for v in rms):
        return _body_with_digest(
            {
                **body,
                "status": "withheld",
                "issues": ["insufficient_waveform_energy"],
                "processed_rms": [v if math.isfinite(v) else None for v in rms],
            }
        )
    # Scale before FFT and prefix sums so the diagnostic is invariant to sensor gain.
    a, b = a / normalized_rms[0], b / normalized_rms[1]
    lags = correlation_lags(n, n, mode="full")
    apparent = lags * dt + offset
    physical = apparent - latency
    overlaps = n - np.abs(lags)
    mask = (np.abs(physical) <= request.maximum_delay_s) & (
        overlaps >= math.ceil(n * request.minimum_overlap_fraction)
    )
    search = np.flatnonzero(mask)
    if len(search) < 3:
        raise ValueError("the delay range and overlap requirement leave fewer than three search bins")
    selected_lags = lags[search]
    a0 = np.maximum(0, -selected_lags)
    b0 = np.maximum(0, selected_lags)
    length = overlaps[search]

    def sums(v, start):
        p = np.r_[0, np.cumsum(v)]
        p2 = np.r_[0, np.cumsum(v * v)]
        return p[start + length] - p[start], p2[start + length] - p2[start]

    sa, ea = sums(a, a0)
    sb, eb = sums(b, b0)
    denominator = np.sqrt(np.maximum(0, ea - sa * sa / length) * np.maximum(0, eb - sb * sb / length))
    numerator = correlate(b, a, mode="full", method="fft")[search] - sa * sb / length
    corr = np.divide(numerator, denominator, out=np.full(len(search), np.nan), where=denominator > 0)
    if not np.isfinite(corr).all():
        return _body_with_digest(
            {**body, "status": "withheld", "issues": ["zero_variance_or_nonfinite_overlap"]}
        )
    if np.max(np.abs(corr)) > 1 + 1e-8:
        return _body_with_digest({**body, "status": "withheld", "issues": ["invalid_normalized_correlation"]})
    corr = np.clip(corr, -1, 1)
    if request.method == "normalized_xcorr":
        score = corr.copy()
    else:
        fft_length = next_fast_len(2 * n - 1)
        cross = np.fft.rfft(b, fft_length) * np.conj(np.fft.rfft(a, fft_length))
        magnitude = np.abs(cross)
        cross = np.divide(
            cross, magnitude, out=np.zeros_like(cross), where=magnitude > float(magnitude.max()) * 1e-12
        )
        if request.band_hz:
            frequencies = np.fft.rfftfreq(fft_length, dt)
            cross[(frequencies < request.band_hz[0]) | (frequencies > request.band_hz[1])] = 0.0
        circular = np.fft.irfft(cross, fft_length)
        score = np.r_[circular[-(n - 1) :], circular[:n]][search]
    if request.polarity == "absolute":
        score = np.abs(score)
    best = int(np.argmax(score))
    competitors = np.abs((selected_lags - selected_lags[best]) * dt) > request.peak_exclusion_s
    second = float(np.max(score[competitors])) if competitors.any() else None
    gap = (
        (float(score[best]) - second) / max(abs(float(score[best])), np.finfo(float).tiny)
        if second is not None
        else None
    )
    signed = float(corr[best])
    gate = abs(signed) if request.polarity == "absolute" else signed
    segment = min(request.coherence_segment_samples, n)
    segment_count = 1 + (n - segment) // (segment - segment // 2)
    frequencies, coh = coherence(
        a, b, fs=1 / dt, window="hann", nperseg=segment, noverlap=segment // 2, detrend="constant"
    )
    band = (
        (frequencies > 0)
        if request.band_hz is None
        else (frequencies >= request.band_hz[0]) & (frequencies <= request.band_hz[1])
    )
    mean_coherence = float(np.mean(coh[band])) if band.any() and np.isfinite(coh[band]).all() else None
    issues = []
    if gate < request.minimum_peak_correlation:
        issues.append("weak_peak_correlation")
    if gap is None or gap < request.minimum_relative_peak_gap:
        issues.append("ambiguous_or_untestable_peak")
    if best in (0, len(score) - 1) and not request.allow_peak_at_search_boundary:
        issues.append("peak_at_delay_search_boundary")
    if request.minimum_mean_coherence > 0 and (
        segment_count < 2 or mean_coherence is None or mean_coherence < request.minimum_mean_coherence
    ):
        issues.append("insufficient_coherence_evidence")
    body.update(
        status="withheld" if issues else "estimated",
        issues=issues,
        lag_samples=int(selected_lags[best]),
        apparent_delay_s=float(apparent[search[best]]),
        corrected_delay_s=float(physical[search[best]]) if request.timing else None,
        delay_error_bound_s=(dt / 2 + drift + request.timing.absolute_error_bound_s)
        if request.timing
        else None,
        delay_error_bound_includes_noise_estimation_error=False,
        peak_correlation_signed=signed,
        relative_peak_gap=gap,
        second_peak_score=second,
        primary_peak_score=float(score[best]),
        processed_rms=rms,
        coherence={
            "mean_in_band": mean_coherence,
            "segments": segment_count,
            "informative": segment_count >= 2,
            "frequency_bins": int(band.sum()),
            "not_event_probability": True,
        },
        overlap_samples=int(length[best]),
        search_bins=len(search),
        search_delay_range_s=[float(physical[search[0]]), float(physical[search[-1]])],
    )
    return _body_with_digest(body)


class PipeSpan(Contract):
    left_position_m: float
    right_position_m: float
    left_position_error_m: Nonnegative = 0.0
    right_position_error_m: Nonnegative = 0.0
    wave_speed_m_s: Positive
    wave_speed_error_m_s: Nonnegative
    velocity_m_s: float = 0.0
    velocity_error_m_s: Nonnegative = 0.0
    additional_delay_error_s: Nonnegative = 0.0
    propagation_source_id: Name
    geometry_source_id: Name
    left_sensor_id: Name
    right_sensor_id: Name
    source_bracketed: bool
    single_path_nondispersive: bool

    @model_validator(mode="after")
    def propagation(self):
        if (
            self.left_position_m + self.left_position_error_m
            >= self.right_position_m - self.right_position_error_m
        ):
            raise ValueError("sensor position intervals must be ordered and separated")
        if (
            self.wave_speed_m_s - self.wave_speed_error_m_s
            <= abs(self.velocity_m_s) + self.velocity_error_m_s
        ):
            raise ValueError("both directional wave speeds must remain positive throughout their bounds")
        return self


def locate_on_span(delay_result, span: PipeSpan, context: RunContext):
    if delay_result.get("schema") != "pipeline_integrity.acoustic_delay.v1" or digest(
        {k: v for k, v in delay_result.items() if k != "result_digest"}
    ) != delay_result.get("result_digest"):
        raise ValueError("an unchanged acoustic delay result is required")
    if delay_result["context"] != context.model_dump(mode="json"):
        raise ValueError("the localization context differs from the delay context")
    body = {
        "schema": "pipeline_integrity.acoustic_location.v1",
        "context": context.model_dump(mode="json"),
        "delay_result_digest": delay_result["result_digest"],
        "span": span.model_dump(mode="json"),
        "field_validation_claim": False,
        "probability_calculated": False,
        "bounds_are_statistical_confidence_interval": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    needed = []
    if delay_result["status"] != "estimated":
        needed.append("qualified_delay_estimate")
    if delay_result.get("corrected_delay_s") is None:
        needed.append("relative_timing_bias_and_error_bound")
    if not span.source_bracketed:
        needed.append("source_between_sensors_evidence")
    if not span.left_sensor_id or not span.right_sensor_id:
        needed.append("ordered_sensor_endpoint_identity")
    if not span.single_path_nondispersive:
        needed.append("single_path_nondispersive_propagation_evidence")
    if needed:
        return _body_with_digest({**body, "status": "needs_data", "missing": needed})
    tau = delay_result["corrected_delay_s"]
    d = span.right_position_m - span.left_position_m
    c = span.wave_speed_m_s
    u = span.velocity_m_s
    x = span.left_position_m + (d * (c - u) - tau * (c * c - u * u)) / (2 * c)

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1])

    def sub(a, b):
        return (a[0] - b[1], a[1] - b[0])

    def div(a, b):
        vals = [x / y for x in a for y in b]
        return (min(vals), max(vals))

    left = (
        span.left_position_m - span.left_position_error_m,
        span.left_position_m + span.left_position_error_m,
    )
    right = (
        span.right_position_m - span.right_position_error_m,
        span.right_position_m + span.right_position_error_m,
    )
    length = sub(right, left)
    speed = (c - span.wave_speed_error_m_s, c + span.wave_speed_error_m_s)
    flow = (u - span.velocity_error_m_s, u + span.velocity_error_m_s)
    downstream, upstream = add(speed, flow), sub(speed, flow)
    error = delay_result["delay_error_bound_s"] + span.additional_delay_error_s
    delay = (tau - error, tau + error)
    local = div(
        sub(div(length, downstream), delay), add(div((1.0, 1.0), downstream), div((1.0, 1.0), upstream))
    )
    interval = add(left, local)
    return _body_with_digest(
        {
            **body,
            "status": "estimated" if span.left_position_m <= x <= span.right_position_m else "withheld",
            "position_m": x,
            "unconstrained_position_bounds_m": list(interval),
            "nominal_inside_sensor_span": span.left_position_m <= x <= span.right_position_m,
            "bounds_method": "conservative_interval_arithmetic_of_user_supplied_bounds",
            "additional_delay_model_error_included": span.additional_delay_error_s > 0,
        }
    )


def localize_acoustic_leak(
    left: SampledTrace,
    right: SampledTrace,
    request: DelayRequest,
    span: PipeSpan | None,
    context: RunContext,
):
    """Compose the existing paired-delay and one-span location calculations."""
    left = SampledTrace.model_validate(left.model_dump(mode="python"))
    right = SampledTrace.model_validate(right.model_dump(mode="python"))
    request = DelayRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    if span is not None:
        span = PipeSpan.model_validate(span.model_dump(mode="python"))
    if (left.asset_id, right.asset_id) != (context.asset_id, context.asset_id):
        raise ValueError("both acoustic records must belong to the requested asset")
    if (left.clock_id, right.clock_id) != (context.clock_id, context.clock_id):
        raise ValueError("both acoustic records must use the requested clock")
    if span is not None and (span.left_sensor_id, span.right_sensor_id) != (
        left.sensor_id,
        right.sensor_id,
    ):
        raise ValueError("span endpoint sensor IDs must match the left/right waveform order")
    delay = estimate_delay(left, right, request, context)
    location = locate_on_span(delay, span, context) if span is not None else None
    body = {
        "schema": "pipeline_integrity.acoustic_localization.v1",
        "context": context.model_dump(mode="json"),
        "input_digests": {"left": digest(left), "right": digest(right)},
        "delay_result_digest": delay["result_digest"],
        "location_result_digest": location["result_digest"] if location is not None else None,
        "sensor_order": [left.sensor_id, right.sensor_id],
        "status": location["status"] if location is not None else delay["status"],
        "delay": delay,
        "location": location,
        "probability_calculated": False,
        "field_validation_claim": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return _body_with_digest(body)


def verify_acoustic_localization_record(record):
    """Check the sealed composition and both embedded result receipts."""
    if not isinstance(record, dict) or record.get("schema") != "pipeline_integrity.acoustic_localization.v1":
        raise ValueError("an acoustic localization composition record is required")
    body = {key: value for key, value in record.items() if key != "result_digest"}
    if digest(body) != record.get("result_digest"):
        raise ValueError("the acoustic localization composition digest is invalid")
    delay = record.get("delay")
    location = record.get("location")
    if not isinstance(delay, dict) or delay.get("schema") != "pipeline_integrity.acoustic_delay.v1":
        raise ValueError("the embedded delay receipt is missing")
    if digest({key: value for key, value in delay.items() if key != "result_digest"}) != delay.get(
        "result_digest"
    ):
        raise ValueError("the embedded delay receipt digest is invalid")
    if record.get("delay_result_digest") != delay.get("result_digest"):
        raise ValueError("the delay receipt reference does not match its embedded child")
    if location is None:
        if record.get("location_result_digest") is not None or record.get("status") != delay.get("status"):
            raise ValueError("delay-only composition status or location reference is inconsistent")
        if record.get("context") != delay.get("context"):
            raise ValueError("the composition and delay contexts do not match")
        return True
    if not isinstance(location, dict) or location.get("schema") != "pipeline_integrity.acoustic_location.v1":
        raise ValueError("the embedded location receipt is missing")
    if digest({key: value for key, value in location.items() if key != "result_digest"}) != location.get(
        "result_digest"
    ):
        raise ValueError("the embedded location receipt digest is invalid")
    if record.get("location_result_digest") != location.get("result_digest"):
        raise ValueError("the location receipt reference does not match its embedded child")
    if location.get("delay_result_digest") != delay.get("result_digest"):
        raise ValueError("the location child references a different delay receipt")
    if record.get("status") != location.get("status"):
        raise ValueError("the composition status does not match its location child")
    if record.get("context") != delay.get("context") or record.get("context") != location.get("context"):
        raise ValueError("the composition and child contexts do not match")
    return True


def delay_feature_frames(results, *, stream_id):
    previous = None
    reference = None
    for result in results:
        if result.get("schema") != "pipeline_integrity.acoustic_delay.v1" or digest(
            {k: v for k, v in result.items() if k != "result_digest"}
        ) != result.get("result_digest"):
            raise ValueError("unchanged delay results are required")
        identity = (result["context"]["asset_id"], result["context"]["clock_id"], result["input_origin"])
        if reference is not None and identity != reference:
            raise ValueError("feature results cannot mix assets, clocks or origins")
        reference = identity
        t = result["analysis_time_s"]
        if previous is not None and t <= previous:
            raise ValueError("delay feature times must strictly increase")
        previous = t
        values = {
            "apparent_delay": result.get("apparent_delay_s"),
            "peak_correlation": result.get("peak_correlation_signed"),
            "relative_peak_gap": result.get("relative_peak_gap"),
            "mean_coherence": result.get("coherence", {}).get("mean_in_band"),
        }
        yield SignalFrame(
            stream_id=stream_id,
            clock_id=identity[1],
            time_s=t,
            values=values,
            units={k: "s" if k == "apparent_delay" else "1" for k in values},
            valid={k: result["status"] == "estimated" and v is not None for k, v in values.items()},
            origin="simulation",
        )
