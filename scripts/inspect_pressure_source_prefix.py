"""Bounded original CSV-prefix inspection; no physical-unit or full-trace interpretation."""

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path

from pipeline_integrity import pressure_wave_data
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.pressure_wave_data import (
    _archive_tool_identity,
    list_pressure_members,
    pressure_member_labels,
    verify_pressure_archive,
)


def _budgets(lines, maximum_bytes, timeout_seconds):
    if (
        type(lines) is not int
        or not 1 <= lines <= 100
        or type(maximum_bytes) is not int
        or not 1024 <= maximum_bytes <= 1024 * 1024
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 300
    ):
        raise ValueError("choose explicit bounded line/byte/process-time budgets")


def review_prefix_payload(payload):
    """Inspect exact prefix bytes as format evidence, without interpreting Sample as seconds."""
    result = {
        "status": "unresolved",
        "header": None,
        "expected_header_matches": False,
        "numeric_data_rows": 0,
        "numeric_sample_prefix_strictly_increasing": None,
        "physical_units_or_sample_rate_inferred": False,
    }
    try:
        text = payload.decode("utf-8-sig", errors="strict")
        if not payload.endswith(b"\n") or "\x00" in text:
            raise ValueError("only complete LF/CRLF-terminated text lines are inspected")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        if not rows:
            raise ValueError("no complete CSV header")
        result["header"] = rows[0]
        result["expected_header_matches"] = rows[0] == ["Sample", "Value"]
        if not result["expected_header_matches"]:
            result["status"] = "header_requires_separate_format_review"
            return result
        samples = []
        for row in rows[1:]:
            if len(row) != 2 or any("\n" in value or "\r" in value for value in row):
                raise ValueError("the numeric two-column prefix contract does not support multiline fields")
            sample, value = (float(v) for v in row)
            if not math.isfinite(sample) or not math.isfinite(value):
                raise ValueError("nonfinite numeric prefix field")
            samples.append(sample)
        result.update(
            status="recognized_two_numeric_columns_prefix_only",
            numeric_data_rows=len(samples),
            numeric_sample_prefix_strictly_increasing=None
            if len(samples) < 2
            else all(b > a for a, b in zip(samples, samples[1:], strict=False)),
        )
    except (ValueError, UnicodeError, csv.Error) as exc:
        result.update(status="unsupported_or_incomplete_prefix_format", reason=str(exc))
    return result


def _decoded_lines(payload):
    try:
        return payload.decode("utf-8-sig", errors="strict").splitlines()
    except UnicodeError:
        return None


def _capture_prefix(inventory, member, *, lines, maximum_bytes, timeout_seconds):
    raw = bytearray()
    expired = threading.Event()
    started = time.monotonic()
    process = subprocess.Popen(
        [inventory["archive_tool"]["path"], "-xOf", inventory["archive"]["path"], member],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    stop_reason = "eof"
    stopped_by_reader = False
    observed_returncode = None

    def expire():
        expired.set()
        if process.poll() is None:
            process.kill()

    timer = threading.Timer(timeout_seconds, expire)
    timer.start()
    try:
        while len(raw) < maximum_bytes and raw.count(b"\n") < lines:
            chunk = os.read(process.stdout.fileno(), min(4096, maximum_bytes - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if raw.count(b"\n") >= lines:
            stop_reason = "line_goal"
        elif len(raw) >= maximum_bytes:
            stop_reason = "byte_budget"
        observed_returncode = process.poll()
    finally:
        timer.cancel()
        if process.poll() is None:
            stopped_by_reader = True
            process.kill()
        returncode = process.wait()
        process.stdout.close()
    elapsed = time.monotonic() - started
    return bytes(raw), {
        "expired": expired.is_set() or elapsed > timeout_seconds,
        "stop_reason": stop_reason,
        "stopped_by_reader": stopped_by_reader,
        "returncode_before_reader_stop": observed_returncode,
        "returncode": returncode,
        "duration_s": elapsed,
        "captured_bytes": len(raw),
        "captured_sha256": hashlib.sha256(raw).hexdigest(),
    }


def inspect_pressure_source_prefix(
    archive, tar_executable, *, member=None, lines=8, maximum_bytes=65536, timeout_seconds=300
):
    """Recheck source/tool; capture one member prefix. Hash/inventory time is separate."""
    _budgets(lines, maximum_bytes, timeout_seconds)
    started = time.monotonic()
    inventory = list_pressure_members(
        archive, tar_executable=tar_executable, byte_budget=1_000_000, timeout_s=min(20, timeout_seconds)
    )
    members = {item["member"]: item for item in inventory["entries"]}
    if not members:
        raise ValueError("archive inventory contains no reviewed pressure CSV")
    member = next(iter(members)) if member is None else member
    pressure_member_labels(member)
    if member not in members or not 0 < members[member]["bytes"]:
        raise ValueError("select one regular allowlisted nonempty pressure CSV")
    inventory_seconds = time.monotonic() - started
    raw, capture = _capture_prefix(
        inventory, member, lines=lines, maximum_bytes=maximum_bytes, timeout_seconds=timeout_seconds
    )
    unchanged = (
        verify_pressure_archive(archive) == inventory["archive"]
        and _archive_tool_identity(inventory["archive_tool"]["path"]) == inventory["archive_tool"]
    )
    # A CR at the byte limit is not a complete CRLF line. Keep only complete LF lines.
    complete = raw.split(b"\n")[:-1][:lines]
    goal = len(complete) == lines
    expected_stop = (
        capture["stop_reason"] in {"line_goal", "byte_budget"}
        and capture["stopped_by_reader"]
        and capture["returncode_before_reader_stop"] is None
    )
    tool_ok = capture["returncode"] == 0 or expected_stop
    adopted = unchanged and not capture["expired"] and goal and tool_ok
    payload = b"\n".join(complete) + b"\n" if adopted else b""
    status = (
        "prefix_inspected"
        if adopted
        else "source_or_tool_changed"
        if not unchanged
        else "prefix_timeout"
        if capture["expired"]
        else "archive_tool_failed"
        if not tool_ok
        else "requested_complete_line_goal_not_reached"
    )
    result = {
        "schema": "pipeline_integrity.pressure_source_prefix.v2",
        "status": status,
        "archive": inventory["archive"],
        "archive_tool": inventory["archive_tool"],
        "inventory_digest": digest(inventory),
        "member": member,
        "selected_entry": members[member],
        "source_identity": {
            key: inventory[key]
            for key in ("catalog_entry_sha256", "source", "citation", "license_id", "origin")
        },
        "source_unchanged": unchanged,
        "capture": capture,
        "prefix_bytes": len(payload),
        "prefix_sha256": hashlib.sha256(payload).hexdigest() if adopted else None,
        "prefix_base64": base64.b64encode(payload).decode("ascii") if adopted else None,
        "prefix_lines": _decoded_lines(payload) if adopted else [],
        "format_contract": review_prefix_payload(payload) if adopted else None,
        "complete_csv_extracted": False,
        "pressure_unit": None,
        "pressure_reference": None,
        "clock_unit": None,
        "clock_synchronization_verified": False,
        "arrival_ground_truth": None,
        "leak_onset_ground_truth": None,
        "numerical_model_executed": False,
        "inventory_and_preverification_duration_s": inventory_seconds,
        "whole_operation_duration_s": time.monotonic() - started,
        "implementation_sha256": file_digest(Path(__file__)),
        "reader_sha256": file_digest(Path(pressure_wave_data.__file__)),
        "budgets": {
            "lines": lines,
            "bytes": maximum_bytes,
            "seconds": timeout_seconds,
            "time_scope": "decompression process only; inventory and full pre/post archive hashing are separate",
        },
    }
    result["record_digest"] = digest(result)
    return result


def verify_pressure_prefix_receipt(record):
    """Check receipt/payload consistency only; this does not reread/authenticate the archive."""
    if record.get("schema") != "pipeline_integrity.pressure_source_prefix.v2" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("prefix receipt seal/schema changed")
    if record["status"] == "prefix_inspected":
        raw = base64.b64decode(record["prefix_base64"], validate=True)
        if (
            len(raw) != record["prefix_bytes"]
            or hashlib.sha256(raw).hexdigest() != record["prefix_sha256"]
            or _decoded_lines(raw) != record["prefix_lines"]
            or review_prefix_payload(raw) != record["format_contract"]
        ):
            raise ValueError("prefix payload/format receipt differs")
        budget, capture = record["budgets"], record["capture"]
        _budgets(budget["lines"], budget["bytes"], budget["seconds"])
        expected_stop = (
            capture["stop_reason"] == "line_goal"
            and capture["stopped_by_reader"]
            and capture["returncode_before_reader_stop"] is None
        )
        if (
            not record["source_unchanged"]
            or capture["expired"]
            or raw.count(b"\n") != budget["lines"]
            or not 0 < len(raw) <= capture["captured_bytes"] <= budget["bytes"]
            or not (capture["returncode"] == 0 or expected_stop)
        ):
            raise ValueError("prefix adoption contradicts its source/process/line-budget receipt")
    elif (
        record["prefix_base64"] is not None
        or record["prefix_sha256"] is not None
        or record["prefix_bytes"] != 0
        or record["prefix_lines"] != []
        or record["format_contract"] is not None
    ):
        raise ValueError("withheld prefix cannot retain adopted payload fields")
    pressure_member_labels(record["member"])
    if record["selected_entry"]["member"] != record["member"]:
        raise ValueError("prefix member differs from the selected regular-file inventory")
    if (
        any(
            record[key] is not None
            for key in (
                "pressure_unit",
                "pressure_reference",
                "clock_unit",
                "arrival_ground_truth",
                "leak_onset_ground_truth",
            )
        )
        or record["complete_csv_extracted"]
        or record["numerical_model_executed"]
        or record["clock_synchronization_verified"]
    ):
        raise ValueError(
            "prefix receipt cannot establish units, synchronization, truth or full-trace/model execution"
        )
    return True


def compare_pressure_prefix_pair(first, second):
    """Compare two sealed prefixes without promoting coordinates to time or values to pressure."""
    verify_pressure_prefix_receipt(first)
    verify_pressure_prefix_receipt(second)
    if first["status"] != "prefix_inspected" or second["status"] != "prefix_inspected":
        raise ValueError("pair comparison requires two adopted prefix receipts")
    first_labels = pressure_member_labels(first["member"])
    second_labels = pressure_member_labels(second["member"])
    if first_labels["pair_key"] != second_labels["pair_key"] or {
        first_labels["sensor_id"],
        second_labels["sensor_id"],
    } != {"P1", "P2"}:
        raise ValueError("prefixes must be the P1/P2 members of one filename condition")

    def columns(record):
        raw = base64.b64decode(record["prefix_base64"], validate=True)
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True))
        return rows[0], [float(row[0]) for row in rows[1:]], [float(row[1]) for row in rows[1:]]

    first_header, first_coordinates, first_values = columns(first)
    second_header, second_coordinates, second_values = columns(second)
    return {
        "schema": "pipeline_integrity.pressure_prefix_pair_comparison.v1",
        "filename_condition_matches": True,
        "sensor_ids": sorted((first_labels["sensor_id"], second_labels["sensor_id"])),
        "header_matches": first_header == second_header,
        "numeric_rows_compared": min(len(first_coordinates), len(second_coordinates)),
        "coordinate_prefix_matches": first_coordinates == second_coordinates,
        "value_prefix_matches": first_values == second_values,
        "coordinate_unit": None,
        "value_unit": None,
        "common_trigger_verified": False,
        "clock_synchronization_verified": False,
        "complete_traces_compared": False,
        "numerical_model_executed": False,
    }


def write_pressure_prefix_receipt(record, output):
    """Create a new receipt file; never overwrite an archive, tool or previous artifact."""
    verify_pressure_prefix_receipt(record)
    output = Path(output)
    if output.resolve() in {
        Path(record["archive"]["path"]).resolve(),
        Path(record["archive_tool"]["path"]).resolve(),
    }:
        raise ValueError("receipt destination collides with original archive or executable")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tar", type=Path, required=True)
    parser.add_argument("--member")
    parser.add_argument("--lines", type=int, default=8)
    parser.add_argument("--maximum-bytes", type=int, default=65536)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Reject existing outputs before any expensive checksum/solid-block access.
    if args.output.exists() or args.output.is_symlink() or args.output.resolve() == args.archive.resolve():
        parser.error("choose a new receipt path distinct from the archive")
    result = inspect_pressure_source_prefix(
        args.archive,
        args.tar,
        member=args.member,
        lines=args.lines,
        maximum_bytes=args.maximum_bytes,
        timeout_seconds=args.timeout_seconds,
    )
    write_pressure_prefix_receipt(result, args.output)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "prefix_bytes", "complete_csv_extracted", "numerical_model_executed")
            }
        )
    )


if __name__ == "__main__":
    main()
