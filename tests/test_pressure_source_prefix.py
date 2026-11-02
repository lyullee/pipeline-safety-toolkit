"""Prefix tool/format contracts; synthetic subprocess receipts, queued and unrun."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.pressure_wave_data import PREFIX
from scripts import inspect_pressure_source_prefix as prefix

MEMBER = PREFIX + "Branched/Circumferential Crack/BR_CC_0.18 LPS_P1.csv"
RAW = b"Sample,Value\r\n0,-123\r\n3.91E-05,-122\r\n"


def stub(monkeypatch, *, raw=RAW, capture_changes=None, source_changed=False):
    archive = {"path": "authored.7z", "bytes": 717637500, "sha256": "a" * 64}
    tool = {"path": "authored-tar.exe", "sha256": "b" * 64}
    inventory = {
        "archive": archive,
        "archive_tool": tool,
        "entries": [{"member": MEMBER, "bytes": 23661637}],
        "catalog_entry_sha256": "c" * 64,
        "source": "authored fixture",
        "citation": "authored citation",
        "license_id": "CC-BY-4.0",
        "origin": "laboratory",
    }
    capture = dict(
        expired=False,
        stop_reason="line_goal",
        stopped_by_reader=True,
        returncode_before_reader_stop=None,
        returncode=-9,
        duration_s=0.01,
        captured_bytes=len(raw),
        captured_sha256=hashlib.sha256(raw).hexdigest(),
    )
    capture.update(capture_changes or {})
    monkeypatch.setattr(prefix, "list_pressure_members", lambda *args, **kwargs: inventory)
    monkeypatch.setattr(prefix, "_capture_prefix", lambda *args, **kwargs: (raw, capture))
    monkeypatch.setattr(
        prefix,
        "verify_pressure_archive",
        lambda *args: archive | ({"sha256": "d" * 64} if source_changed else {}),
    )
    monkeypatch.setattr(prefix, "_archive_tool_identity", lambda *args: tool)
    return lambda: prefix.inspect_pressure_source_prefix(
        Path("authored.7z"),
        Path("authored-tar.exe"),
        member=MEMBER,
        lines=3,
        maximum_bytes=1024,
        timeout_seconds=2,
    )


def test_prefix_preserves_exact_crlf_bytes_and_leaves_physical_units_unknown(monkeypatch):
    record = stub(monkeypatch)()
    assert record["status"] == "prefix_inspected"
    assert record["prefix_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert record["prefix_lines"] == ["Sample,Value", "0,-123", "3.91E-05,-122"]
    assert record["format_contract"]["expected_header_matches"]
    assert record["format_contract"]["numeric_data_rows"] == 2
    assert record["pressure_unit"] is record["pressure_reference"] is record["clock_unit"] is None
    assert not record["clock_synchronization_verified"] and not record["complete_csv_extracted"]
    assert prefix.verify_pressure_prefix_receipt(record)


@pytest.mark.parametrize(
    "changes,status",
    [
        (
            {"stopped_by_reader": False, "returncode_before_reader_stop": 1, "returncode": 1},
            "archive_tool_failed",
        ),
        ({"expired": True}, "prefix_timeout"),
        ({"stop_reason": "eof", "stopped_by_reader": True}, "archive_tool_failed"),
    ],
)
def test_complete_text_is_not_adopted_after_tool_failure_or_timeout(monkeypatch, changes, status):
    record = stub(monkeypatch, capture_changes=changes)()
    assert record["status"] == status
    assert record["prefix_bytes"] == 0 and record["prefix_base64"] is None
    assert prefix.verify_pressure_prefix_receipt(record)


def test_partial_crlf_or_insufficient_line_goal_is_not_reported_complete(monkeypatch):
    record = stub(monkeypatch, raw=b"Sample,Value\r\n0,1\r", capture_changes={"stop_reason": "byte_budget"})()
    assert record["status"] != "prefix_inspected" and record["prefix_lines"] == []
    assert record["prefix_bytes"] == 0


def test_changed_source_with_complete_prefix_is_withheld(monkeypatch):
    record = stub(monkeypatch, source_changed=True)()
    assert record["status"] == "source_or_tool_changed" and record["prefix_bytes"] == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'Sample,Value\n0,"unfinished\n',
        b"Sample,Value\n0,nan\n",
        b'Sample,Value\n"0\n.1",2\n',
        b"Sample,Value\n0,1,extra\n",
        b"Sample,Value\n0,\xff\n",
    ],
)
def test_format_inspector_does_not_accept_partial_quotes_multiline_or_nonfinite_fields(raw):
    result = prefix.review_prefix_payload(raw)
    assert result["status"] == "unsupported_or_incomplete_prefix_format"
    assert not result["physical_units_or_sample_rate_inferred"]


def test_different_header_requires_separate_review_instead_of_automatic_mapping():
    result = prefix.review_prefix_payload(b"Time,Pressure\n0,1\n")
    assert result["status"] == "header_requires_separate_format_review"
    assert result["header"] == ["Time", "Pressure"]
    assert not result["expected_header_matches"]


@pytest.mark.parametrize(
    "changes",
    [
        {"lines": True},
        {"maximum_bytes": True},
        {"timeout_seconds": True},
        {"lines": 0},
        {"maximum_bytes": 1023},
        {"timeout_seconds": float("nan")},
    ],
)
def test_invalid_budget_does_not_access_archive(monkeypatch, changes):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid budget accessed the archive")

    monkeypatch.setattr(prefix, "list_pressure_members", forbidden)
    with pytest.raises(ValueError):
        prefix.inspect_pressure_source_prefix(
            Path("unused.7z"),
            Path("unused-tar"),
            **(dict(lines=3, maximum_bytes=1024, timeout_seconds=2) | changes),
        )


def test_empty_or_unselected_member_is_not_extracted(monkeypatch):
    inspect = stub(monkeypatch)
    monkeypatch.setattr(prefix, "list_pressure_members", lambda *args, **kwargs: {"entries": []})
    with pytest.raises(ValueError, match="no reviewed"):
        inspect()


@pytest.mark.parametrize("mutation", ["payload", "format", "units", "adoption"])
def test_rehashed_receipt_inconsistency_is_rejected(monkeypatch, mutation):
    record = deepcopy(stub(monkeypatch)())
    if mutation == "payload":
        record["prefix_sha256"] = "0" * 64
    elif mutation == "format":
        record["format_contract"]["header"] = ["unreviewed", "header"]
    elif mutation == "units":
        record["pressure_unit"] = "Pa"
    else:
        record["capture"]["expired"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        prefix.verify_pressure_prefix_receipt(record)


def test_receipt_writer_cannot_overwrite_archive_tool_or_prior_artifact(monkeypatch, tmp_path):
    record = stub(monkeypatch)()
    record["archive"]["path"] = str(tmp_path / "original.7z")
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="collides"):
        prefix.write_pressure_prefix_receipt(record, tmp_path / "original.7z")
    target = tmp_path / "new_receipt.json"
    prefix.write_pressure_prefix_receipt(record, target)
    first = target.read_bytes()
    with pytest.raises(FileExistsError):
        prefix.write_pressure_prefix_receipt(record, target)
    assert target.read_bytes() == first


def test_pair_comparison_reports_only_prefix_relations_without_clock_or_unit_claims(monkeypatch):
    first = stub(monkeypatch)()
    second = deepcopy(first)
    second["member"] = MEMBER.replace("_P1.csv", "_P2.csv")
    second["selected_entry"]["member"] = second["member"]
    second["record_digest"] = digest({k: v for k, v in second.items() if k != "record_digest"})
    result = prefix.compare_pressure_prefix_pair(first, second)
    assert result["filename_condition_matches"] and result["coordinate_prefix_matches"]
    assert result["value_prefix_matches"]
    assert result["coordinate_unit"] is result["value_unit"] is None
    assert not result["common_trigger_verified"] and not result["clock_synchronization_verified"]
    assert not result["complete_traces_compared"] and not result["numerical_model_executed"]


def test_pair_comparison_rejects_two_members_that_are_not_one_p1_p2_condition(monkeypatch):
    first = stub(monkeypatch)()
    second = deepcopy(first)
    second["member"] = MEMBER.replace("0.18 LPS_P1.csv", "0.47 LPS_P2.csv")
    second["selected_entry"]["member"] = second["member"]
    second["record_digest"] = digest({k: v for k, v in second.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="one filename condition"):
        prefix.compare_pressure_prefix_pair(first, second)
