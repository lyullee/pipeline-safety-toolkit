"""Archive/parser regressions queued for the user's later verification environment."""

import csv
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.pressure_wave_data import (
    PREFIX,
    build_pressure_pair_trace_record,
    parse_pressure_csv,
    pressure_member_labels,
    pressure_source_publication_metadata,
    qualify_pressure_pair,
    read_pressure_member,
    verify_pressure_archive,
    verify_pressure_csv_record,
    verify_pressure_pair_qualification,
    verify_pressure_pair_trace_record,
)


def mapping(**changes):
    return (
        dict(
            delimiter=",",
            header_rows=1,
            time_column=0,
            pressure_column=1,
            time_scale_to_s=0.001,
            pressure_scale_to_Pa=1000,
            pressure_offset_Pa=0,
            clock_id="supplied-clock",
            sensor_id="P1",
            maximum_rows=20,
            mapping_evidence_id="synthetic-mapping",
            pressure_reference="dynamic_perturbation",
        )
        | changes
    )


def test_publication_hardware_metadata_does_not_invent_csv_mapping_or_pairing():
    metadata = pressure_source_publication_metadata()
    assert metadata["dynamic_pressure_acquisition"] == {
        "sensor_brand": "PCB",
        "sensor_model": "102B16",
        "daq_model": "NI-9234",
        "software": "LabVIEW NXG 5.1",
        "published_measurement_frequency_Hz": 51200.0,
        "published_record_duration_s": 30.0,
        "published_sensitivity_V_Pa": 7.25e-6,
        "published_measurement_range_Pa": [1000.0, 689000.0],
    }
    assert all(value is None for value in metadata["csv_mapping"].values())
    assert not any(metadata["pairing"].values())
    assert metadata["record_digest"] == digest(
        {key: value for key, value in metadata.items() if key != "record_digest"}
    )


def test_pair_identity_does_not_create_clock_or_arrival_truth():
    one = pressure_member_labels(PREFIX + "Branched/No-leak/BR_NL_Transient_P1.csv")
    two = pressure_member_labels(PREFIX + "Branched/No-leak/BR_NL_Transient_P2.csv")
    assert one["pair_key"] == two["pair_key"]
    assert one["background_flow_condition"] == "Transient"
    assert one["leak_condition"] == "No-leak"
    assert one["arrival_ground_truth"] is None and not one["shared_clock_verified"]


@pytest.mark.parametrize(
    "member",
    [
        "../x.csv",
        "/x.csv",
        "C:/x.csv",
        "x\\y.csv",
        PREFIX + "Looped/No-leak/BR_NL_ND_P1.csv",
        PREFIX + "Branched/No-leak/BR_CC_ND_P1.csv",
        PREFIX + "Branched/No-leak/BR_NL_ND_P1.py",
    ],
)
def test_rejects_unsafe_or_conflicting_members(member):
    with pytest.raises(ValueError):
        pressure_member_labels(member)


def test_actual_checksum_before_any_tool(tmp_path, monkeypatch):
    source = tmp_path / "archive.7z"
    source.write_bytes(b"untrusted")

    def forbidden(*args, **kwargs):
        pytest.fail("archive tool invoked before checksum validation")

    monkeypatch.setattr("pipeline_integrity.pressure_wave_data._bounded_command", forbidden)
    with pytest.raises(ValueError, match="byte count"):
        read_pressure_member(
            source,
            PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
            tar_executable=Path("tar"),
            byte_budget=1000,
            timeout_s=1,
        )
    with pytest.raises(ValueError, match="checksum"):
        verify_pressure_archive(source, expected_bytes=9, expected_sha256="0" * 64)
    assert (
        verify_pressure_archive(
            source, expected_bytes=9, expected_sha256=hashlib.sha256(b"untrusted").hexdigest()
        )["bytes"]
        == 9
    )


def test_explicit_units_and_negative_dynamic_values_preserved():
    parsed = parse_pressure_csv(b"time,value\n0,-2\n1,3\n", **mapping())
    assert parsed["time_s"] == [0, 0.001]
    assert parsed["pressure_Pa"] == [-2000, 3000]
    assert parsed["pressure_reference"] == "dynamic_perturbation"
    assert parsed["arrival_ground_truth"] is None


@pytest.mark.parametrize(
    "raw",
    [
        b"t,p\n0,1\n0,2\n",
        b"t,p\n0,nan\n1,2\n",
        b"t,p\n0,1\n-1,2\n",
        b"t,p\n0,1\n",
        b"t,p\n0,x\n1,2\n",
        b"t,p\n0,1\n1\n",
        b"\xff",
    ],
)
def test_bad_measurements_fail_without_interpolation(raw):
    with pytest.raises(ValueError):
        parse_pressure_csv(raw, **mapping())


def test_malformed_csv_quotes_are_rejected_as_a_contract_error():
    raw = b't,p\n0,"unterminated\n1,2\n'
    with pytest.raises(ValueError, match="CSV quoting"):
        parse_pressure_csv(raw, **mapping())


def test_csv_field_limit_failure_is_normalized_and_never_truncated():
    oversized_field = "x" * (csv.field_size_limit() + 1)
    raw = f"t,p,unused\n0,1,{oversized_field}\n1,2,ok\n".encode()
    with pytest.raises(ValueError, match="field exceeds the parser limit"):
        parse_pressure_csv(raw, **mapping())


@pytest.mark.parametrize(
    "changes",
    [
        {"time_scale_to_s": 0},
        {"pressure_scale_to_Pa": float("nan")},
        {"pressure_reference": "unknown"},
        {"mapping_evidence_id": ""},
        {"maximum_rows": 1},
        {"header_rows": 10},
        {"pressure_column": 0},
    ],
)
def test_mapping_and_budget_are_explicit(changes):
    with pytest.raises(ValueError):
        parse_pressure_csv(b"t,p\n0,1\n1,2\n", **mapping(**changes))


def test_absolute_pressure_cannot_be_negative_but_gauge_and_pretrigger_are_preserved():
    raw = b"t,p\n-1,-2\n0,3\n"
    with pytest.raises(ValueError, match="absolute pressure"):
        parse_pressure_csv(raw, **mapping(pressure_reference="absolute"))
    parsed = parse_pressure_csv(raw, **mapping(pressure_reference="gauge"))
    assert parsed["time_s"] == [-0.001, 0.0]
    assert parsed["pressure_Pa"] == [-2000.0, 3000.0]
    assert parsed["mapping"]["pressure_scale_to_Pa"] == 1000
    assert parsed["mapping_independently_validated"] is False
    assert verify_pressure_csv_record(parsed, raw)


def test_csv_rehashed_mapping_or_result_change_replayed():
    raw = b"t,p\n0,1\n1,2\n"
    record = deepcopy(parse_pressure_csv(raw, **mapping()))
    record["mapping"]["pressure_offset_Pa"] = 100.0
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="replay"):
        verify_pressure_csv_record(record, raw)


def test_linked_pressure_member_is_rejected_before_extraction(monkeypatch):
    from pipeline_integrity import pressure_wave_data as module

    member = PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv"
    monkeypatch.setattr(module, "verify_pressure_archive", lambda *a: {"path": "authored.7z"})
    monkeypatch.setattr(
        module, "_archive_tool_identity", lambda *a: {"path": "authored-tar", "sha256": "0" * 64}
    )
    monkeypatch.setattr(
        module,
        "_bounded_command",
        lambda *a, **kw: ("-rw-r--r-- 2 user group 20 Oct 28 2026 " + member + "\n").encode(),
    )
    with pytest.raises(ValueError, match="regular file"):
        module.list_pressure_members(
            Path("authored.7z"), tar_executable=Path("authored-tar"), byte_budget=1000, timeout_s=1
        )


def test_archive_replacement_during_stdout_extraction_is_rejected(monkeypatch):
    from pipeline_integrity import pressure_wave_data as module

    member = PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv"
    original = {"path": "authored.7z", "sha256": "0" * 64}
    inventory = {
        "archive": original,
        "entries": [{"member": member, "bytes": 1}],
        "archive_tool": {"path": "authored-tar"},
    }
    monkeypatch.setattr(module, "list_pressure_members", lambda *a, **kw: inventory)
    monkeypatch.setattr(module, "_bounded_command", lambda *a, **kw: b"x")
    monkeypatch.setattr(module, "verify_pressure_archive", lambda *a: original | {"sha256": "f" * 64})
    with pytest.raises(ValueError, match="changed during selected extraction"):
        module.read_pressure_member(
            Path("authored.7z"), member, tar_executable=Path("authored-tar"), byte_budget=1000, timeout_s=1
        )


def test_pair_qualification_stays_blocked_without_reviewed_trigger_and_alignment():
    raw_one = b"t,p\n0,1\n1,2\n"
    raw_two = b"t,p\n0,3\n1,4\n"
    one = parse_pressure_csv(raw_one, **mapping(sensor_id="P1"))
    two = parse_pressure_csv(raw_two, **mapping(sensor_id="P2"))
    result = qualify_pressure_pair(
        one,
        raw_one,
        PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
        two,
        raw_two,
        PREFIX + "Branched/No-leak/BR_NL_ND_P2.csv",
        pairing_evidence_id="review-pending",
        common_trigger_verified=False,
        sample_alignment_verified=False,
    )
    assert result["status"] == "needs_pairing_evidence"
    assert result["parsed_coordinates_match"] and result["clock_ids_match"]
    assert result["arrival_ground_truth"] is result["leak_onset_ground_truth"] is None
    assert result["record_digest"] == digest(
        {key: value for key, value in result.items() if key != "record_digest"}
    )
    assert verify_pressure_pair_qualification(
        result,
        one,
        raw_one,
        PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
        two,
        raw_two,
        PREFIX + "Branched/No-leak/BR_NL_ND_P2.csv",
        expected_pairing_evidence_id="review-pending",
    )


def test_pair_qualification_rejects_claimed_alignment_that_contradicts_records():
    raw_one = b"t,p\n0,1\n1,2\n"
    raw_two = b"t,p\n0,3\n2,4\n"
    one = parse_pressure_csv(raw_one, **mapping(sensor_id="P1"))
    two = parse_pressure_csv(raw_two, **mapping(sensor_id="P2"))
    with pytest.raises(ValueError, match="contradicts"):
        qualify_pressure_pair(
            one,
            raw_one,
            PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
            two,
            raw_two,
            PREFIX + "Branched/No-leak/BR_NL_ND_P2.csv",
            pairing_evidence_id="reviewed-trigger-record",
            common_trigger_verified=True,
            sample_alignment_verified=True,
        )


def test_pair_qualification_rejects_cross_condition_and_sensor_identity_mismatch():
    raw = b"t,p\n0,1\n1,2\n"
    one = parse_pressure_csv(raw, **mapping(sensor_id="P1"))
    two = parse_pressure_csv(raw, **mapping(sensor_id="P2"))
    with pytest.raises(ValueError, match="one filename condition"):
        qualify_pressure_pair(
            one,
            raw,
            PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
            two,
            raw,
            PREFIX + "Branched/No-leak/BR_NL_0.18 LPS_P2.csv",
            pairing_evidence_id="review",
            common_trigger_verified=False,
            sample_alignment_verified=False,
        )


def test_pair_qualification_replay_rejects_changed_evidence_or_raw_payload():
    raw_one = b"t,p\n0,1\n1,2\n"
    raw_two = b"t,p\n0,3\n1,4\n"
    one = parse_pressure_csv(raw_one, **mapping(sensor_id="P1"))
    two = parse_pressure_csv(raw_two, **mapping(sensor_id="P2"))
    members = (
        PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
        PREFIX + "Branched/No-leak/BR_NL_ND_P2.csv",
    )
    result = qualify_pressure_pair(
        one,
        raw_one,
        members[0],
        two,
        raw_two,
        members[1],
        pairing_evidence_id="pending-review",
        common_trigger_verified=False,
        sample_alignment_verified=False,
    )
    changed = deepcopy(result)
    changed["pairing_evidence_id"] = "different-review"
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="evidence identity"):
        verify_pressure_pair_qualification(
            changed,
            one,
            raw_one,
            members[0],
            two,
            raw_two,
            members[1],
            expected_pairing_evidence_id="pending-review",
        )
    with pytest.raises(ValueError, match="replay"):
        verify_pressure_pair_qualification(
            result,
            one,
            raw_one,
            members[0],
            two,
            b"t,p\n0,3\n1,5\n",
            members[1],
            expected_pairing_evidence_id="pending-review",
        )


def ready_pair_fixture():
    rows_one = b"t,p\n-1,1\n0,2\n1,3\n2,4\n3,5\n4,6\n5,7\n6,8\n"
    rows_two = b"t,p\n-1,8\n0,7\n1,6\n2,5\n3,4\n4,3\n5,2\n6,1\n"
    one = parse_pressure_csv(rows_one, **mapping(sensor_id="P1", time_scale_to_s=1.0))
    two = parse_pressure_csv(rows_two, **mapping(sensor_id="P2", time_scale_to_s=1.0))
    members = (
        PREFIX + "Branched/No-leak/BR_NL_ND_P1.csv",
        PREFIX + "Branched/No-leak/BR_NL_ND_P2.csv",
    )
    qualification = qualify_pressure_pair(
        one,
        rows_one,
        members[0],
        two,
        rows_two,
        members[1],
        pairing_evidence_id="reviewed-pair",
        common_trigger_verified=True,
        sample_alignment_verified=True,
    )
    return qualification, one, rows_one, members[0], two, rows_two, members[1]


def test_ready_pair_builds_source_bound_traces_without_resampling_and_replays():
    args = ready_pair_fixture()
    record = build_pressure_pair_trace_record(
        *args,
        expected_pairing_evidence_id="reviewed-pair",
        asset_id="laboratory-loop",
        trace_id_by_sensor={"P1": "left", "P2": "right"},
        origin="laboratory",
        time_origin_shift_s=1.0,
        time_origin_evidence_id="reviewed-pretrigger-origin",
        maximum_grid_error_s=0.0,
    )
    assert record["traces"]["P1"]["start_time_s"] == 0.0
    assert record["traces"]["P1"]["sample_period_s"] == 1.0
    assert record["traces"]["P1"]["source_digest"] == args[1]["source_sha256"]
    assert not record["interpolation_or_resampling_performed"]
    assert verify_pressure_pair_trace_record(record, *args, expected_pairing_evidence_id="reviewed-pair")


@pytest.mark.parametrize("change", ["unready", "origin", "start", "grid", "ids"])
def test_pair_trace_bridge_rejects_unqualified_or_implicit_transform(change):
    args = list(ready_pair_fixture())
    kwargs = dict(
        expected_pairing_evidence_id="reviewed-pair",
        asset_id="laboratory-loop",
        trace_id_by_sensor={"P1": "left", "P2": "right"},
        origin="laboratory",
        time_origin_shift_s=1.0,
        time_origin_evidence_id="reviewed-pretrigger-origin",
        maximum_grid_error_s=0.0,
    )
    if change == "unready":
        args[0] = dict(args[0], status="needs_pairing_evidence")
        args[0]["record_digest"] = digest({k: v for k, v in args[0].items() if k != "record_digest"})
    elif change == "origin":
        kwargs["origin"] = "synthetic"
    elif change == "start":
        kwargs["time_origin_shift_s"] = 0.0
    elif change == "grid":
        args[1] = deepcopy(args[1])
        args[1]["time_s"][3] += 0.1
        args[1]["record_digest"] = digest({k: v for k, v in args[1].items() if k != "record_digest"})
    else:
        kwargs["trace_id_by_sensor"] = {"P1": "same", "P2": "same"}
    with pytest.raises(ValueError):
        build_pressure_pair_trace_record(*args, **kwargs)
