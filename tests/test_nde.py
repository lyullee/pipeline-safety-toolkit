"""NDE source identities, incomplete waveform handling and native metrology boundary policy."""

import struct
import zipfile

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.nde import (
    EchoThicknessRequest,
    evaluate_echo_thickness,
    load_tubers_waveform,
    tubers_visual_pairs,
)


def waveform(
    tmp_path, rows="-0.000001,0.1\n0,0.5\n0.000001,0.2\n0.000002,\n", header="x-axis,1\nsecond,Volt\n"
):
    p = tmp_path / "source.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Ultrasonic Data/Plate_1/trace.csv", header + rows)
    return load_tubers_waveform(p, "Ultrasonic Data/Plate_1/trace.csv")


def inputs(trace):
    context = RunContext(
        asset_id="line",
        scenario_id="reference",
        clock_id=trace.native_clock_id,
        origin="user_declared",
        evidence=(
            EvidenceRef(
                id="source",
                citation="Authored CSV reference",
                origin="synthetic",
                sha256=trace.archive_sha256,
            ),
            EvidenceRef(id="review", citation="Authored echo/calibration interpretation", origin="synthetic"),
        ),
    )
    request = EchoThicknessRequest(
        waveform_digest=trace.waveform_digest,
        reference_index=0,
        return_index=2,
        sound_speed_m_s=2000.0,
        time_zero_offset_s=0.0,
        incidence_angle_rad=0.0,
        source_evidence_id="source",
        echo_review_evidence_id="review",
        calibration_evidence_id="review",
    )
    return request, context


class MaterialSpy:
    def __init__(self):
        self.calls = []

    def evaluate(self, model_id, parameters, context, *, location_id):
        self.calls.append(parameters)
        payload = {
            "model_id": model_id,
            "inputs": parameters,
            "status": "computed",
            "values": {"thickness_m": 0.002},
            "units": {"thickness_m": "m"},
            "context": {
                "asset_id": context.asset_id,
                "location_id": location_id,
                "time_basis": context.clock_id,
                "metadata": {"pipeline_context_digest": digest(context)},
            },
        }
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


def test_signed_native_times_and_partial_tail_record_are_not_shifted_or_dropped(tmp_path):
    trace = waveform(tmp_path)
    assert trace.time_s == (-1e-6, 0.0, 1e-6, 2e-6)
    assert trace.voltage_v[-1] is None and trace.source_rows == (3, 4, 5, 6)
    assert trace.audit()["negative_native_times"] == 1 and trace.audit()["missing_voltages"] == 1
    assert trace.invalid_cells == ({"row": 6, "column": "voltage", "native_token": ""},)
    assert trace.zero_reference == "not_documented" and not trace.source_labels_are_calibration


def test_only_the_explicit_complete_window_is_sent_to_native_metrology(tmp_path):
    trace = waveform(tmp_path)
    request, context = inputs(trace)
    spy = MaterialSpy()
    result = evaluate_echo_thickness(trace, request, context, materials=spy)
    assert spy.calls[0]["round_trip_time_s"] == 2e-6
    assert result["status"] == "exchange_checked" and result["thickness_m"] == 0.002
    assert not result["field_calibrated"] and not result["physical_echo_identity_automatically_verified"]


@pytest.mark.parametrize("end", [3, 9])
def test_missing_or_outside_echo_window_does_not_call_the_material_engine(tmp_path, end):
    trace = waveform(tmp_path)
    request, context = inputs(trace)
    spy = MaterialSpy()
    result = evaluate_echo_thickness(
        trace, request.model_copy(update={"return_index": end}), context, materials=spy
    )
    assert result["status"] == "needs_data" and result["native_result"] is None and not spy.calls


@pytest.mark.parametrize("rows", ["0,1\n0,2\n1,3\n", "0,1\n-1,2\n1,3\n", "0,1\nNaN,2\n1,3\n"])
def test_ambiguous_or_missing_native_clock_is_retained_and_blocks_selected_echoes(tmp_path, rows):
    trace = waveform(tmp_path, rows=rows)
    request, context = inputs(trace)
    result = evaluate_echo_thickness(trace, request, context, materials=MaterialSpy())
    assert result["status"] == "needs_data"


@pytest.mark.parametrize(
    "header", ["x-axis,1\nms,Volt\n", "x-axis,1\nsecond,mV\n", "time,amplitude\nsecond,Volt\n"]
)
def test_unrecognized_native_header_or_units_are_not_guessed(tmp_path, header):
    with pytest.raises(ValueError, match="header"):
        waveform(tmp_path, header=header)


def test_bad_source_clock_and_boolean_calibration_are_rejected_before_owner_call(tmp_path):
    trace = waveform(tmp_path)
    request, context = inputs(trace)
    spy = MaterialSpy()
    with pytest.raises(ValueError):
        evaluate_echo_thickness(
            trace, request, context.model_copy(update={"clock_id": "other"}), materials=spy
        )
    with pytest.raises(ValueError):
        evaluate_echo_thickness(
            trace, request.model_copy(update={"waveform_digest": "other"}), context, materials=spy
        )
    with pytest.raises(ValueError):
        evaluate_echo_thickness(
            trace, request.model_copy(update={"sound_speed_m_s": True}), context, materials=spy
        )
    assert not spy.calls


def test_changed_waveform_and_source_checksum_cannot_be_accepted(tmp_path):
    trace = waveform(tmp_path)
    with pytest.raises(ValueError, match="provenance changed"):
        trace.model_copy(update={"time_s": (0.0, 1.0, 2.0, 3.0)}).check_integrity()
    with pytest.raises(ValueError, match="pinned acquisition"):
        load_tubers_waveform(tmp_path / "source.zip", trace.member, expected_archive_sha256="0" * 64)


def test_fully_blank_records_are_reported_separately_from_incomplete_samples(tmp_path):
    trace = waveform(tmp_path, rows="0,1\n,\n1,2\n2,\n")
    assert trace.omitted_empty_record_rows == (4,) and trace.source_rows == (3, 5, 6)
    assert len(trace.time_s) == 3 and trace.voltage_v[-1] is None


def test_selected_echo_window_cannot_hide_an_omitted_internal_record(tmp_path):
    trace = waveform(tmp_path, rows="0,1\n,\n1,2\n2,3\n")
    request, context = inputs(trace)
    spy = MaterialSpy()
    result = evaluate_echo_thickness(trace, request, context, materials=spy)
    assert result["status"] == "needs_data" and "omitted_native_records" in result["issues"][0]
    assert not spy.calls


def png(width=4, height=4):
    # Minimal header fixtures exercise metadata indexing, without pretending to be decoded pixel data.
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x00\x00\x00\x00"
    )


def test_visual_orphan_masks_are_preserved_and_whole_correspondence_stays_unresolved(tmp_path):
    p = tmp_path / "visual.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Visual Data/1/Images/0.png", png())
        z.writestr("Visual Data/1/Masks/0.png", png())
        z.writestr("Visual Data/1/Masks/1.png", png())
    result = tubers_visual_pairs(p)
    assert result["status"] == "needs_data" and len(result["pairs"]) == 1
    assert result["masks_without_images"] == ["Visual Data/1/Masks/1.png"]
    assert not result["physical_pixel_scale_known"] and not result["independent_trials_established"]


def test_visual_native_dimension_mismatch_is_not_resized(tmp_path):
    p = tmp_path / "visual.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Visual Data/1/Images/0.png", png())
        z.writestr("Visual Data/1/Masks/0.png", png(width=5))
    with pytest.raises(ValueError, match="dimensions"):
        tubers_visual_pairs(p)
