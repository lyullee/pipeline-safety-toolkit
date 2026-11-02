"""Synthetic contract checks for the selected-member percussion reader."""

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.percussion_data import (
    PercussionRecording,
    _member_identity,
    percussion_recording_to_sampled_trace,
    summarize_percussion_recording,
)


def _recording(times=(0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007)):
    body = {
        "dataset_id": "percussion",
        "archive_sha256": "f22a5d01347a2dce8830763954a6cb99ba3914ec97070277096370f52e0529c6",
        "member_path": "Pipe percussion dataset/A/1/001.csv",
        "member_bytes": 100,
        "condition": "A",
        "tap_position": 1,
        "stroke_number": 1,
        "timestamp_header": "时间 - cDAQ9185-1D34FAFMod1/ai0",
        "pressure_header": "Pascals - cDAQ9185-1D34FAFMod1/ai0",
        "time_unit": "s",
        "pressure_unit": "Pa",
        "times_s": times,
        "pressure_pa": tuple(float(index) for index in range(len(times))),
        "row_count": len(times),
    }
    return PercussionRecording(**body, source_digest=digest(body))


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("Pipe percussion dataset/A/1/001.csv", ("A", 1, 1)),
        ("Pipe percussion dataset/D/1/a017.csv", ("D", 1, 17)),
        ("Pipe percussion dataset/D/5/e110.csv", ("D", 5, 110)),
        ("Pipe percussion dataset/G/5/110.csv", ("G", 5, 110)),
    ],
)
def test_member_path_maps_condition_position_and_stroke_without_guessing(member, expected):
    assert _member_identity(member) == expected


@pytest.mark.parametrize(
    "member",
    [
        "../A/1/001.csv",
        "Pipe percussion dataset/D/1/001.csv",
        "Pipe percussion dataset/A/1/a001.csv",
        "Pipe percussion dataset/A/6/001.csv",
        "Pipe percussion dataset/A/1/000.csv",
        "Pipe percussion dataset/A/1/111.csv",
    ],
)
def test_member_path_rejects_escape_or_condition_mismatch(member):
    with pytest.raises(ValueError):
        _member_identity(member)


def test_existing_acoustic_api_bridge_preserves_explicit_clock_and_pressure_unit():
    trace = percussion_recording_to_sampled_trace(
        _recording(), asset_id="pipe-A", sensor_id="mic-1", clock_id="clock-reviewed"
    )
    assert trace.sample_period_s == pytest.approx(0.001)
    assert trace.unit == "Pa"
    assert trace.clock_id == "clock-reviewed"
    assert trace.origin == "laboratory"


def test_public_percussion_summary_excludes_sample_values():
    recording = _recording()
    summary = summarize_percussion_recording(recording)
    assert summary["pressure_values_included"] is False
    assert "pressure_pa" not in summary
    assert "pressure_min_pa" in summary


def test_irregular_trace_is_not_silently_resampled():
    recording = _recording((0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.008))
    with pytest.raises(ValueError, match="not uniform"):
        percussion_recording_to_sampled_trace(
            recording, asset_id="pipe-A", sensor_id="mic-1", clock_id="clock-reviewed"
        )
