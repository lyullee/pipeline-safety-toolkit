"""Bounded analytic oracle for sampled two-sensor pipe leak localization."""

import numpy as np
import pytest

from pipeline_integrity.acoustics import DelayRequest, PipeSpan, RelativeTiming, SampledTrace, localize_acoustic_leak, verify_acoustic_localization_record
from pipeline_integrity.contracts import RunContext, digest


def make_inputs(lag=13, sample_period_s=0.001, span_length_m=100.0, left_position_m=0.0):
    n = 1024
    source = np.random.default_rng(8127).normal(size=n)
    observed = np.zeros(n)
    if lag > 0:
        observed[lag:] = source[:-lag]
    elif lag < 0:
        observed[:lag] = source[-lag:]
    else:
        observed[:] = source
    common = dict(
        asset_id="pipe-A", clock_id="shared-clock", start_time_s=0.0,
        sample_period_s=sample_period_s, quantity_id="wall_acceleration", unit="m/s^2",
        origin="synthetic", source_digest=digest("independently authored shifted random excitation"), valid=(True,) * n,
    )
    left = SampledTrace(id="wave-L", sensor_id="sensor-L", values=tuple(source), **common)
    right = SampledTrace(id="wave-R", sensor_id="sensor-R", values=tuple(observed * 0.4), **common)
    request = DelayRequest(
        maximum_delay_s=0.08, peak_exclusion_s=0.002,
        timing=RelativeTiming(right_minus_left_latency_s=0.0, absolute_error_bound_s=0.0, source_id="synthetic-clock"),
        coherence_segment_samples=128,
    )
    span = PipeSpan(
        left_position_m=left_position_m, right_position_m=left_position_m + span_length_m,
        wave_speed_m_s=1000.0, wave_speed_error_m_s=0.0,
        propagation_source_id="authored-constant-speed", geometry_source_id="authored-sensor-span",
        left_sensor_id="sensor-L", right_sensor_id="sensor-R", source_bracketed=True,
        single_path_nondispersive=True,
    )
    context = RunContext(asset_id="pipe-A", scenario_id="analytic-delay-oracle", clock_id="shared-clock", origin="synthetic")
    return left, right, request, span, context


@pytest.mark.parametrize("lag", [-13, 13])
def test_integer_shift_matches_independent_two_speed_travel_time_equation(lag):
    left, right, request, span, context = make_inputs(lag=lag)
    result = localize_acoustic_leak(left, right, request, span, context)
    delay_s = lag * left.sample_period_s
    c = span.wave_speed_m_s
    length = span.right_position_m - span.left_position_m
    expected = span.left_position_m + (length - c * delay_s) / 2.0
    assert result["status"] == "estimated"
    assert result["delay"]["lag_samples"] == lag
    assert result["delay"]["corrected_delay_s"] == pytest.approx(delay_s, abs=1e-15)
    assert result["location"]["position_m"] == pytest.approx(expected, abs=1e-10)
    assert result["location"]["unconstrained_position_bounds_m"][0] <= expected
    assert expected <= result["location"]["unconstrained_position_bounds_m"][1]


def test_joint_length_and_time_scaling_doubles_localized_distance():
    base = localize_acoustic_leak(*make_inputs(lag=13))
    scaled = localize_acoustic_leak(*make_inputs(lag=13, sample_period_s=0.002, span_length_m=200.0))
    assert scaled["location"]["position_m"] == pytest.approx(2.0 * base["location"]["position_m"], abs=1e-10)


def test_sensor_span_translation_translates_localized_coordinate():
    base = localize_acoustic_leak(*make_inputs(lag=13))
    translated = localize_acoustic_leak(*make_inputs(lag=13, left_position_m=25.0))
    assert translated["location"]["position_m"] == pytest.approx(base["location"]["position_m"] + 25.0)


def test_unreviewed_source_bracketing_withholds_position_but_keeps_child_evidence_valid():
    left, right, request, span, context = make_inputs()
    result = localize_acoustic_leak(left, right, request, span.model_copy(update={"source_bracketed": False}), context)
    assert result["status"] == "needs_data"
    assert result["location"].get("position_m") is None
    assert verify_acoustic_localization_record(result)


def test_unordered_sensor_span_is_rejected():
    left, right, request, span, context = make_inputs()
    invalid_span = span.model_copy(update={"left_position_m": 60.0, "right_position_m": 40.0})
    with pytest.raises(ValueError, match="ordered and separated"):
        localize_acoustic_leak(left, right, request, invalid_span, context)
