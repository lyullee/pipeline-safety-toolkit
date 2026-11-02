import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.acoustics import DelayRequest
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.leak_multisensor_data import (
    HYDROPHONE_PREFIX,
    LeakMultisensorArchiveSession,
    accelerometer_member_labels,
    bind_accelerometer_delay_request,
    build_accelerometer_pair_trace_record,
    hydrophone_member_labels,
    parse_accelerometer_csv,
    qualify_accelerometer_pair,
    read_hydrophone_member,
    verify_accelerometer_csv_record,
    verify_accelerometer_pair_qualification,
    verify_accelerometer_pair_trace_record,
    verify_hydrophone_source_record,
    verify_leak_multisensor_member_source,
)

MEMBER = (
    "Dataset of Leak Simulations in Experimental Testbed Water Network/Accelerometer/"
    "Branched/Circumferential Crack/BR_CC_0.18 LPS_A1.csv"
)
HYDROPHONE_MEMBER = HYDROPHONE_PREFIX + "Branched/Circumferential Crack/BR_CC_Transient_NN_H1.raw"


def mapping():
    return {
        "delimiter": ",",
        "header_rows": 1,
        "coordinate_column": 0,
        "value_column": 1,
        "coordinate_scale_to_s": 1.0,
        "value_scale_to_m_s2": 1.0,
        "value_offset_m_s2": 0.0,
        "clock_id": "reviewed-clock",
        "sensor_id": "A1",
        "maximum_rows": 8,
        "mapping_evidence_id": "reviewed-map",
    }


def test_accelerometer_filename_labels_do_not_invent_units_or_clock_truth():
    labels = accelerometer_member_labels(MEMBER)
    assert labels["sensor_id"] == "A1"
    assert labels["physical_unit"] is None
    assert not labels["shared_clock_verified"]


def test_hydrophone_filename_labels_preserve_unknown_marker_and_decode_state():
    labels = hydrophone_member_labels(HYDROPHONE_MEMBER)
    assert labels["filename_marker"] == "NN"
    assert labels["filename_marker_meaning"] is None
    assert labels["sensor_id"] == "H1"
    assert not labels["vendor_raw_decoded"]
    assert labels["physical_unit"] is None


def test_hydrophone_background_label_does_not_invent_network_or_flow():
    member = HYDROPHONE_PREFIX + "Background Noise/Background Noise_H2.raw"
    labels = hydrophone_member_labels(member)
    assert labels["network"] is labels["background_flow_condition"] is None
    assert labels["leak_condition"] == "Background Noise"
    assert labels["sensor_id"] == "H2"


@pytest.mark.parametrize(
    "member",
    [
        "../" + HYDROPHONE_MEMBER,
        HYDROPHONE_MEMBER.replace("Branched", "Looped"),
        HYDROPHONE_MEMBER.replace(".raw", ".csv"),
        HYDROPHONE_MEMBER.replace("H1.raw", "H3.raw"),
    ],
)
def test_hydrophone_member_rejects_unsafe_or_conflicting_labels(member):
    with pytest.raises(ValueError):
        hydrophone_member_labels(member)


def test_opaque_hydrophone_receipt_verifies_bytes_without_decoding():
    raw = b"vendor-specific-bytes"
    body = {
        "schema": "pipeline_integrity.opaque_hydrophone_source.v1",
        "member": HYDROPHONE_MEMBER,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "labels": hydrophone_member_labels(HYDROPHONE_MEMBER),
        "inventory_record_digest": "inventory-receipt",
        "vendor_raw_decoded": False,
        "decoder_id": None,
        "calibration_id": None,
    }
    record = {**body, "record_digest": digest(body)}
    assert verify_hydrophone_source_record(record, raw)
    with pytest.raises(ValueError):
        verify_hydrophone_source_record(record, raw + b"changed")


def test_multisensor_session_reads_multiple_allowlisted_members_and_replays_receipts(tmp_path, monkeypatch):
    from pipeline_integrity import leak_multisensor_data as module

    archive = tmp_path / "source.7z"
    archive.write_bytes(b"pinned")
    tool = {"path": "trusted-archive-tool", "sha256": "a" * 64}
    inventory = {
        "archive": {"bytes": 6, "sha256": module.ARCHIVE_SHA256},
        "archive_tool": tool,
        "record_digest": "inventory-seal",
    }
    listing = (
        f"-rw-r--r-- 1 user group 7 Oct 28 2026 {MEMBER}\n"
        f"-rw-r--r-- 1 user group 5 Oct 28 2026 {HYDROPHONE_MEMBER}\n"
    ).encode()
    outputs = iter((listing, b"0,1\n1,2", b"raw!!"))
    monkeypatch.setattr(module, "inventory_leak_multisensor_archive", lambda *a, **k: inventory)
    monkeypatch.setattr(module, "_archive_tool_identity", lambda *a: tool)
    monkeypatch.setattr(
        module,
        "verify_pressure_archive",
        lambda path, **kwargs: {"path": str(Path(path).resolve()), "bytes": 6, "sha256": module.ARCHIVE_SHA256},
    )
    monkeypatch.setattr(module, "_bounded_command", lambda *a, **k: next(outputs))

    with LeakMultisensorArchiveSession(
        archive, tar_executable=Path("trusted-archive-tool"), byte_budget=4096, timeout_s=2
    ) as session:
        csv_raw, csv_receipt = session.read_member(MEMBER, family="accelerometer", byte_budget=64)
        raw, raw_receipt = session.read_member(HYDROPHONE_MEMBER, family="hydrophone", byte_budget=64)
        assert verify_leak_multisensor_member_source(csv_receipt, csv_raw)
        assert verify_leak_multisensor_member_source(raw_receipt, raw)
        with pytest.raises(ValueError, match="sensor family"):
            session.read_member(HYDROPHONE_MEMBER, family="accelerometer", byte_budget=64)


def test_multisensor_session_detects_source_change_before_close(tmp_path, monkeypatch):
    from pipeline_integrity import leak_multisensor_data as module

    archive = tmp_path / "source.7z"
    archive.write_bytes(b"pinned")
    tool = {"path": "trusted-archive-tool", "sha256": "a" * 64}
    inventory = {
        "archive": {"bytes": 6, "sha256": module.ARCHIVE_SHA256},
        "archive_tool": tool,
        "record_digest": "inventory-seal",
    }
    listing = f"-rw-r--r-- 1 user group 7 Oct 28 2026 {MEMBER}\n".encode()
    outputs = iter((listing, b"0,1\n1,2"))
    monkeypatch.setattr(module, "inventory_leak_multisensor_archive", lambda *a, **k: inventory)
    monkeypatch.setattr(module, "_archive_tool_identity", lambda *a: tool)
    monkeypatch.setattr(
        module,
        "verify_pressure_archive",
        lambda path, **kwargs: {"path": str(Path(path).resolve()), "bytes": 6, "sha256": module.ARCHIVE_SHA256},
    )
    monkeypatch.setattr(module, "_bounded_command", lambda *a, **k: next(outputs))

    with pytest.raises(ValueError, match="archive or tool changed during the read session"):
        with LeakMultisensorArchiveSession(
            archive, tar_executable=Path("trusted-archive-tool"), byte_budget=4096, timeout_s=2
        ) as session:
            session.read_member(MEMBER, family="accelerometer", byte_budget=64)
            archive.write_bytes(b"changed source")


def test_hydrophone_reader_checks_regular_member_size_before_returning_bytes(monkeypatch):
    from pipeline_integrity import leak_multisensor_data as module

    raw = b"opaque"
    inventory = {
        "archive_tool": {"path": "trusted-tar", "sha256": "0" * 64},
        "record_digest": "inventory-receipt",
    }
    archive = {"path": "pinned.7z", "bytes": 717637500, "sha256": "f" * 64}
    listing = f"-rw-r--r-- 1 user group {len(raw)} Oct 28 2026 {HYDROPHONE_MEMBER}\n".encode()
    outputs = iter((listing, raw))
    monkeypatch.setattr(module, "inventory_leak_multisensor_archive", lambda *a, **k: inventory)
    monkeypatch.setattr(module, "verify_pressure_archive", lambda *a, **k: archive)
    monkeypatch.setattr(module, "_archive_tool_identity", lambda *a: inventory["archive_tool"])
    monkeypatch.setattr(module, "_bounded_command", lambda *a, **k: next(outputs))
    payload, record = read_hydrophone_member(
        Path("pinned.7z"),
        HYDROPHONE_MEMBER,
        tar_executable=Path("trusted-tar"),
        byte_budget=100,
        timeout_s=1,
    )
    assert payload == raw
    assert verify_hydrophone_source_record(record, raw)


@pytest.mark.parametrize(
    "member",
    [
        MEMBER.replace("A1.csv", "P1.csv"),
        MEMBER.replace("Branched", "Looped"),
        "../" + MEMBER,
    ],
)
def test_accelerometer_member_rejects_wrong_family_inconsistent_or_unsafe_paths(member):
    with pytest.raises(ValueError):
        accelerometer_member_labels(member)


def test_explicit_acceleration_mapping_is_replayable_without_event_truth():
    raw = b"Sample,Value\n0,0.1\n0.01,-0.2\n0.02,0.3\n"
    record = parse_accelerometer_csv(raw, **mapping())
    assert record["acceleration_m_s2"] == [0.1, -0.2, 0.3]
    assert record["leak_onset_ground_truth"] is None
    assert verify_accelerometer_csv_record(record, raw)


def test_changed_mapping_or_source_cannot_reuse_accelerometer_receipt():
    raw = b"Sample,Value\n0,0.1\n0.01,-0.2\n"
    record = parse_accelerometer_csv(raw, **mapping())
    changed = deepcopy(record)
    changed["mapping"]["value_scale_to_m_s2"] = 9.81
    with pytest.raises(ValueError):
        verify_accelerometer_csv_record(changed, raw)
    with pytest.raises(ValueError):
        verify_accelerometer_csv_record(record, raw.replace(b"-0.2", b"-0.3"))


@pytest.mark.parametrize(
    "raw",
    [
        b"Sample,Value\n0,0.1\n0,-0.2\n",
        b"Sample,Value\n0,0.1\n",
        b"Sample,Value\n0,nan\n0.1,0.2\n",
    ],
)
def test_invalid_accelerometer_rows_are_not_silently_repaired(raw):
    with pytest.raises(ValueError):
        parse_accelerometer_csv(raw, **mapping())


def paired_records():
    times = [f"{index * 0.01:.2f}" for index in range(8)]
    first_raw = ("Sample,Value\n" + "".join(f"{time},{index}\n" for index, time in enumerate(times))).encode()
    second_raw = (
        "Sample,Value\n" + "".join(f"{time},{index + 0.5}\n" for index, time in enumerate(times))
    ).encode()
    first = parse_accelerometer_csv(first_raw, **mapping())
    second_mapping = mapping() | {"sensor_id": "A2", "mapping_evidence_id": "reviewed-map-a2"}
    second = parse_accelerometer_csv(second_raw, **second_mapping)
    return first, first_raw, MEMBER, second, second_raw, MEMBER.replace("A1.csv", "A2.csv")


def delay_boundary_inputs():
    args = paired_records()
    qualification = qualify_accelerometer_pair(
        *args,
        pairing_evidence_id="pair-review",
        common_trigger_verified=True,
        sample_alignment_verified=True,
        unit_mapping_reviewed=True,
    )
    trace_record = build_accelerometer_pair_trace_record(
        qualification,
        *args,
        expected_pairing_evidence_id="pair-review",
        asset_id="testbed",
        trace_id_by_sensor={"A1": "a1-trace", "A2": "a2-trace"},
        origin="laboratory",
        time_origin_shift_s=0.0,
        time_origin_evidence_id="clock-origin-review",
        maximum_grid_error_s=1e-12,
    )
    evidence = tuple(
        EvidenceRef(id=value, citation=value, origin="laboratory")
        for value in (
            "pair-review",
            "reviewed-map",
            "reviewed-map-a2",
            "sensor-roles",
            "delay-analysis",
        )
    )
    context = RunContext(
        asset_id="testbed",
        scenario_id="branched-cc-018",
        clock_id="reviewed-clock",
        origin="laboratory",
        evidence=evidence,
    )
    request = DelayRequest(maximum_delay_s=0.02, maximum_samples=1000)
    return qualification, trace_record, context, request


def test_pair_qualification_requires_unit_trigger_and_alignment_review_and_replays():
    args = paired_records()
    blocked = qualify_accelerometer_pair(
        *args,
        pairing_evidence_id="pair-review",
        common_trigger_verified=True,
        sample_alignment_verified=False,
        unit_mapping_reviewed=True,
    )
    assert blocked["status"] == "needs_pairing_or_mapping_evidence"
    ready = qualify_accelerometer_pair(
        *args,
        pairing_evidence_id="pair-review",
        common_trigger_verified=True,
        sample_alignment_verified=True,
        unit_mapping_reviewed=True,
    )
    assert ready["status"] == "ready_for_paired_analysis"
    assert verify_accelerometer_pair_qualification(ready, *args, expected_pairing_evidence_id="pair-review")


def test_pair_qualification_rejects_claimed_alignment_that_contradicts_clock():
    args = list(paired_records())
    args[3] = parse_accelerometer_csv(
        args[4],
        **(
            mapping()
            | {
                "sensor_id": "A2",
                "mapping_evidence_id": "reviewed-map-a2",
                "clock_id": "other-clock",
            }
        ),
    )
    with pytest.raises(ValueError, match="contradicts"):
        qualify_accelerometer_pair(
            *args,
            pairing_evidence_id="pair-review",
            common_trigger_verified=True,
            sample_alignment_verified=True,
            unit_mapping_reviewed=True,
        )


def test_ready_accelerometer_pair_builds_fixed_grid_sampled_traces_and_replays():
    args = paired_records()
    qualification = qualify_accelerometer_pair(
        *args,
        pairing_evidence_id="pair-review",
        common_trigger_verified=True,
        sample_alignment_verified=True,
        unit_mapping_reviewed=True,
    )
    record = build_accelerometer_pair_trace_record(
        qualification,
        *args,
        expected_pairing_evidence_id="pair-review",
        asset_id="testbed",
        trace_id_by_sensor={"A1": "a1-trace", "A2": "a2-trace"},
        origin="laboratory",
        time_origin_shift_s=0.0,
        time_origin_evidence_id="clock-origin-review",
        maximum_grid_error_s=1e-12,
    )
    assert set(record["traces"]) == {"A1", "A2"}
    assert all(trace["quantity_id"] == "acceleration" for trace in record["traces"].values())
    assert not record["interpolation_or_resampling_performed"]
    assert verify_accelerometer_pair_trace_record(
        record,
        qualification,
        *args,
        expected_pairing_evidence_id="pair-review",
    )


def test_trace_bridge_rejects_unready_pair_without_resampling():
    args = paired_records()
    qualification = qualify_accelerometer_pair(
        *args,
        pairing_evidence_id="pair-review",
        common_trigger_verified=False,
        sample_alignment_verified=False,
        unit_mapping_reviewed=True,
    )
    with pytest.raises(ValueError, match="ready"):
        build_accelerometer_pair_trace_record(
            qualification,
            *args,
            expected_pairing_evidence_id="pair-review",
            asset_id="testbed",
            trace_id_by_sensor={"A1": "a1-trace", "A2": "a2-trace"},
            origin="laboratory",
            time_origin_shift_s=0.0,
            time_origin_evidence_id="clock-origin-review",
            maximum_grid_error_s=1e-12,
        )


def test_delay_binding_preserves_explicit_roles_request_and_source_boundary():
    qualification, trace_record, context, request = delay_boundary_inputs()
    left, right, bound_request, bound_context = bind_accelerometer_delay_request(
        trace_record,
        qualification,
        context,
        request,
        expected_trace_record_digest=trace_record["record_digest"],
        expected_qualification_digest=qualification["record_digest"],
        left_sensor_id="A1",
        right_sensor_id="A2",
        sensor_role_evidence_id="sensor-roles",
        analysis_evidence_id="delay-analysis",
    )
    boundary = bound_context.metadata["accelerometer_delay_boundary"]
    assert (left.sensor_id, right.sensor_id) == ("A1", "A2")
    assert bound_request == request
    assert boundary["delay_definition"] == "arrival_at_right_minus_arrival_at_left"
    assert boundary["trace_record_digest"] == trace_record["record_digest"]
    assert not boundary["sensor_roles_inferred"]
    assert not boundary["location_or_probability_calculated"]


def test_delay_binding_accepts_role_reversal_only_as_an_explicit_evidenced_choice():
    qualification, trace_record, context, request = delay_boundary_inputs()
    left, right, _, bound_context = bind_accelerometer_delay_request(
        trace_record,
        qualification,
        context,
        request,
        expected_trace_record_digest=trace_record["record_digest"],
        expected_qualification_digest=qualification["record_digest"],
        left_sensor_id="A2",
        right_sensor_id="A1",
        sensor_role_evidence_id="sensor-roles",
        analysis_evidence_id="delay-analysis",
    )
    assert (left.sensor_id, right.sensor_id) == ("A2", "A1")
    assert bound_context.metadata["accelerometer_delay_boundary"]["left_sensor_id"] == "A2"


@pytest.mark.parametrize(
    ("missing_evidence", "changed_digest"),
    [
        ("pair-review", False),
        ("reviewed-map", False),
        ("sensor-roles", False),
        ("delay-analysis", False),
        (None, True),
    ],
)
def test_delay_binding_rejects_missing_evidence_or_changed_source_seal(missing_evidence, changed_digest):
    qualification, trace_record, context, request = delay_boundary_inputs()
    if missing_evidence:
        context = context.model_copy(
            update={"evidence": tuple(item for item in context.evidence if item.id != missing_evidence)}
        )
    expected_trace_digest = "0" * 64 if changed_digest else trace_record["record_digest"]
    with pytest.raises(ValueError):
        bind_accelerometer_delay_request(
            trace_record,
            qualification,
            context,
            request,
            expected_trace_record_digest=expected_trace_digest,
            expected_qualification_digest=qualification["record_digest"],
            left_sensor_id="A1",
            right_sensor_id="A2",
            sensor_role_evidence_id="sensor-roles",
            analysis_evidence_id="delay-analysis",
        )
