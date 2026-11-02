"""Pulse-echo geometry checked through the source-pinned native material path."""

import math
import zipfile

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.nde import EchoThicknessRequest, evaluate_echo_thickness, load_tubers_waveform


def make_inputs(tmp_path, times=(0.0, 2.0e-5, 4.0e-5), speed=5800.0, offset=4.0e-6, angle=0.3):
    archive = tmp_path / "synthetic_echo.zip"
    rows = "".join(f"{t:.12g},{0.2 + 0.1 * i:.6g}\n" for i, t in enumerate(times))
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("Ultrasonic Data/Plate_A/trace.csv", "x-axis,1\nsecond,Volt\n" + rows)
    trace = load_tubers_waveform(archive, "Ultrasonic Data/Plate_A/trace.csv")
    context = RunContext(
        asset_id="synthetic_plate",
        scenario_id="pulse_echo_reference",
        clock_id=trace.native_clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="source",
                citation="Self-authored pulse-echo reference trace",
                origin="synthetic",
                license_id="MIT",
                sha256=trace.archive_sha256,
            ),
            EvidenceRef(id="review", citation="Declared echo picks and calibration values", origin="synthetic"),
        ),
    )
    request = EchoThicknessRequest(
        waveform_digest=trace.waveform_digest,
        reference_index=0,
        return_index=len(times) - 1,
        sound_speed_m_s=speed,
        time_zero_offset_s=offset,
        incidence_angle_rad=angle,
        source_evidence_id="source",
        echo_review_evidence_id="review",
        calibration_evidence_id="review",
    )
    return trace, request, context


@pytest.mark.parametrize(
    "times,speed,offset,angle",
    [
        ((0.0, 2.0e-5, 4.0e-5), 5800.0, 4.0e-6, 0.0),
        ((-1.0e-5, 0.0, 3.0e-5), 3200.0, 5.0e-6, 0.35),
        ((0.0, 4.0e-5, 8.0e-5), 6200.0, 1.0e-5, 0.6),
    ],
)
def test_native_thickness_matches_round_trip_geometry(tmp_path, times, speed, offset, angle):
    trace, request, context = make_inputs(tmp_path, times, speed, offset, angle)
    result = evaluate_echo_thickness(trace, request, context)
    corrected_round_trip = times[-1] - times[0] - offset
    expected = 0.5 * corrected_round_trip * speed * math.cos(angle)
    assert result["status"] == "exchange_checked"
    assert result["thickness_m"] == pytest.approx(expected, rel=2e-12, abs=1e-14)
    native = result["native_result"]["payload"]
    assert native["model_id"] == "inspection.ultrasonic_thickness"
    assert native["units"]["thickness_m"] == "m"
    assert native["context"]["location_id"] == trace.member
    assert result["field_calibrated"] is False


def test_thickness_scales_linearly_with_sound_speed_and_corrected_time(tmp_path):
    trace, request, context = make_inputs(tmp_path)
    base = evaluate_echo_thickness(trace, request, context)
    faster = evaluate_echo_thickness(trace, request.model_copy(update={"sound_speed_m_s": 2 * request.sound_speed_m_s}), context)
    assert faster["thickness_m"] == pytest.approx(2.0 * base["thickness_m"])
    extra_offset = evaluate_echo_thickness(
        trace, request.model_copy(update={"time_zero_offset_s": request.time_zero_offset_s + 1e-6}), context
    )
    assert extra_offset["thickness_m"] < base["thickness_m"]
    expected_difference = 0.5e-6 * request.sound_speed_m_s * math.cos(request.incidence_angle_rad)
    assert base["thickness_m"] - extra_offset["thickness_m"] == pytest.approx(expected_difference)


def test_incidence_angle_reduces_projected_thickness_by_cosine(tmp_path):
    trace, request, context = make_inputs(tmp_path, angle=0.0)
    normal = evaluate_echo_thickness(trace, request, context)
    theta = 0.5
    oblique = evaluate_echo_thickness(trace, request.model_copy(update={"incidence_angle_rad": theta}), context)
    assert oblique["thickness_m"] == pytest.approx(normal["thickness_m"] * math.cos(theta))


def test_time_zero_that_consumes_round_trip_is_withheld_by_owner(tmp_path):
    trace, request, context = make_inputs(tmp_path)
    round_trip = trace.time_s[request.return_index] - trace.time_s[request.reference_index]
    result = evaluate_echo_thickness(trace, request.model_copy(update={"time_zero_offset_s": round_trip}), context)
    assert result["status"] == "withheld"
    assert result["thickness_m"] is None
    assert "native_thickness_unresolved:out_of_scope" in result["issues"]


def test_mismatched_clock_or_waveform_digest_blocks_owner_call(tmp_path):
    trace, request, context = make_inputs(tmp_path)
    with pytest.raises(ValueError, match="same waveform"):
        evaluate_echo_thickness(trace, request.model_copy(update={"waveform_digest": "0" * 64}), context)
    with pytest.raises(ValueError, match="same waveform"):
        evaluate_echo_thickness(trace, request, context.model_copy(update={"clock_id": "other_clock"}))
