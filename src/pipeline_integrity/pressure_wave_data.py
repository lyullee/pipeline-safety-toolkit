"""Bounded, checksum-first access to separately labelled experimental pressure CSVs."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import shutil
import subprocess
import threading
from pathlib import Path, PurePosixPath

from .acoustics import SampledTrace
from .contracts import digest
from .datasets import catalog, file_digest

ARCHIVE_SHA256 = "c6d4a68901a145155a58ccb691769753cb7c0960416a375acac12128bedfd881"
ARCHIVE_BYTES = 717637500
PUBLICATION_DOI = "10.1016/j.dib.2023.109148"
PUBLICATION_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC10147960/"
PREFIX = "Dataset of Leak Simulations in Experimental Testbed Water Network/Dynamic Pressure Sensor/"
_MEMBER = re.compile(
    re.escape(PREFIX)
    + r"(Branched|Looped)/(Circumferential Crack|Gasket Leak|Longitudinal Crack|No-leak|Orifice Leak)/"
    + r"(BR|LO)_(CC|GL|LC|NL|OL)_(0\.18 LPS|0\.47 LPS|ND|Transient)_(P[12])\.csv"
)
_TYPES = {
    "CC": "Circumferential Crack",
    "GL": "Gasket Leak",
    "LC": "Longitudinal Crack",
    "NL": "No-leak",
    "OL": "Orifice Leak",
}


def pressure_source_publication_metadata() -> dict:
    """Return cited acquisition facts without inventing a CSV value/time conversion."""
    body = {
        "schema": "pipeline_integrity.pressure_source_publication_metadata.v1",
        "article": {
            "doi": PUBLICATION_DOI,
            "url": PUBLICATION_URL,
            "citation": (
                "Aghashahi, Sela and Banks (2023), Benchmarking dataset for leak detection and "
                "localization in water distribution systems, Data in Brief 48, 109148"
            ),
            "license_id": "CC-BY-4.0",
        },
        "dynamic_pressure_acquisition": {
            "sensor_brand": "PCB",
            "sensor_model": "102B16",
            "daq_model": "NI-9234",
            "software": "LabVIEW NXG 5.1",
            "published_measurement_frequency_Hz": 51200.0,
            "published_record_duration_s": 30.0,
            "published_sensitivity_V_Pa": 7.25e-6,
            "published_measurement_range_Pa": [1000.0, 689000.0],
        },
        "csv_mapping": {
            "sample_column_unit": None,
            "sample_column_scale_to_s": None,
            "value_column_quantity": None,
            "value_column_scale_to_Pa": None,
            "value_column_offset_Pa": None,
            "pressure_reference": None,
        },
        "pairing": {
            "P1_P2_common_trigger_verified": False,
            "P1_P2_sample_alignment_verified": False,
            "arrival_ground_truth_available": False,
            "leak_onset_ground_truth_available": False,
        },
        "interpretation": (
            "The publication describes acquisition hardware and nominal measurement frequency; "
            "it does not by itself establish the numeric CSV column conversion or paired trigger."
        ),
    }
    return {**body, "record_digest": digest(body)}


def pressure_member_labels(member: str) -> dict:
    """Read experimental labels, never infer an onset or a shared clock from names."""
    p = PurePosixPath(member)
    if (
        p.is_absolute()
        or ".." in p.parts
        or "\\" in member
        or ":" in member
        or p.as_posix() != member
        or any(ord(c) < 32 or ord(c) == 127 for c in member)
    ):
        raise ValueError("unsafe archive member path")
    match = _MEMBER.fullmatch(member)
    if not match:
        raise ValueError("member is not an allowlisted dynamic pressure CSV")
    network, leak, code, leak_code, flow, sensor = match.groups()
    if network != {"BR": "Branched", "LO": "Looped"}[code] or leak != _TYPES[leak_code]:
        raise ValueError("directory and experimental filename labels disagree")
    return {
        "network": network,
        "leak_condition": leak,
        "background_flow_condition": flow,
        "sensor_id": sensor,
        "pair_key": member[:-6],
        "arrival_ground_truth": None,
        "leak_onset_ground_truth": None,
        "shared_clock_verified": False,
    }


def verify_pressure_archive(archive: Path, *, expected_sha256=ARCHIVE_SHA256, expected_bytes=ARCHIVE_BYTES):
    """Hash the actual input before invoking an archive tool; no cache is trusted."""
    archive = Path(archive).resolve(strict=True)
    if not archive.is_file() or archive.stat().st_size != expected_bytes:
        raise ValueError("archive byte count differs")
    h = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != expected_sha256:
        raise ValueError("archive checksum differs")
    return {"path": str(archive), "bytes": expected_bytes, "sha256": h.hexdigest()}


def _bounded_command(args, *, byte_budget, timeout_s):
    if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
        raise ValueError("explicit output budget must be in (0, 128 MiB]")
    if not math.isfinite(timeout_s) or not 0 < timeout_s <= 300:
        raise ValueError("explicit tool timeout must be in (0, 300] seconds")
    output = bytearray()
    expired = threading.Event()
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:

        def expire():
            expired.set()
            process.kill()

        timer = threading.Timer(timeout_s, expire)
        timer.start()
        try:
            while chunk := process.stdout.read(65536):
                if len(output) + len(chunk) > byte_budget:
                    process.kill()
                    raise ValueError("archive output exceeds explicit byte budget")
                output.extend(chunk)
            code = process.wait()
            if expired.is_set():
                raise TimeoutError("archive operation exceeded explicit time budget")
            if code:
                raise ValueError("archive tool returned a failure status")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
    return bytes(output)


def _archive_tool_identity(executable):
    selected = Path(executable)
    if not selected.is_file():
        located = shutil.which(str(executable))
        if located is None:
            raise ValueError("select an existing trusted archive executable")
        selected = Path(located)
    selected = selected.resolve(strict=True)
    return {"path": str(selected), "sha256": file_digest(selected)}


def list_pressure_members(archive: Path, *, tar_executable: Path, byte_budget: int, timeout_s: float):
    """Inventory only regular allowlisted CSVs. No archive member is executed."""
    verification = verify_pressure_archive(archive)
    source = next(s for s in catalog() if s.id == "leak_multisensor_transients")
    if (source.rights_review, source.acquisition, source.license_id) != (
        "reviewed_license",
        "allowed",
        "CC-BY-4.0",
    ) or not any(
        a.bytes == ARCHIVE_BYTES and a.checksum == "sha256:" + ARCHIVE_SHA256 for a in source.assets
    ):
        raise ValueError("pressure source catalog rights or archive identity changed")
    tool = _archive_tool_identity(tar_executable)
    raw = _bounded_command(
        [tool["path"], "-tvf", verification["path"]], byte_budget=byte_budget, timeout_s=timeout_s
    )
    entries = []
    seen = set()
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if PREFIX not in line:
            continue
        # bsdtar verbose fields: mode links user group size month day year path.
        parts = line.split(maxsplit=8)
        if len(parts) != 9 or not parts[0].startswith("-") or parts[1] not in {"0", "1"}:
            if line.endswith("/"):
                continue
            raise ValueError("pressure member is not a regular file or listing is unsupported")
        member = parts[8]
        labels = pressure_member_labels(member)
        if member in seen:
            raise ValueError("duplicate archive pressure member")
        seen.add(member)
        entries.append({"member": member, "bytes": int(parts[4]), "labels": labels})
    if _archive_tool_identity(tool["path"]) != tool or verify_pressure_archive(archive) != verification:
        raise ValueError("archive or archive executable changed during inventory")
    return {
        "archive": verification,
        "entries": entries,
        "archive_tool": tool,
        "catalog_entry_sha256": digest(source),
        "source": source.source,
        "citation": source.citation,
        "license_id": source.license_id,
        "origin": source.origin,
    }


def read_pressure_member(
    archive: Path, member: str, *, tar_executable: Path, byte_budget: int, timeout_s: float
):
    """Return one checked regular CSV as bytes, without writing archive paths to disk."""
    labels = pressure_member_labels(member)
    if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
        raise ValueError("explicit output budget must be in (0, 128 MiB]")
    inventory = list_pressure_members(
        archive, tar_executable=tar_executable, byte_budget=1024 * 1024, timeout_s=timeout_s
    )
    selected = [x for x in inventory["entries"] if x["member"] == member]
    if len(selected) != 1 or not 0 < selected[0]["bytes"] <= byte_budget:
        raise ValueError("selected CSV is absent or outside byte budget")
    raw = _bounded_command(
        [inventory["archive_tool"]["path"], "-xOf", inventory["archive"]["path"], member],
        byte_budget=byte_budget,
        timeout_s=timeout_s,
    )
    if len(raw) != selected[0]["bytes"]:
        raise ValueError("extracted byte count differs from inventory")
    if (
        verify_pressure_archive(archive) != inventory["archive"]
        or _archive_tool_identity(inventory["archive_tool"]["path"]) != inventory["archive_tool"]
    ):
        raise ValueError("archive or archive executable changed during selected extraction")
    return raw, {
        "archive": inventory["archive"],
        "member": member,
        "labels": labels,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_identity": {
            k: inventory[k]
            for k in ("archive_tool", "catalog_entry_sha256", "source", "citation", "license_id", "origin")
        },
    }


def parse_pressure_csv(
    raw: bytes,
    *,
    delimiter: str,
    header_rows: int,
    time_column: int,
    pressure_column: int,
    time_scale_to_s: float,
    pressure_scale_to_Pa: float,
    pressure_offset_Pa: float,
    clock_id: str,
    sensor_id: str,
    maximum_rows: int,
    mapping_evidence_id: str,
    pressure_reference: str,
):
    """Explicit column/unit mapping only. No guessed sample rate, onset, or zero offset."""
    if (
        not clock_id.strip()
        or not sensor_id.strip()
        or not mapping_evidence_id.strip()
        or pressure_reference not in {"absolute", "gauge", "dynamic_perturbation"}
        or len(delimiter) != 1
        or any(type(v) is not int for v in (header_rows, time_column, pressure_column, maximum_rows))
        or header_rows < 0
        or time_column < 0
        or pressure_column < 0
        or time_column == pressure_column
        or not 0 < maximum_rows <= 2_000_000
    ):
        raise ValueError("invalid explicit CSV mapping")
    if any(
        isinstance(v, bool) for v in (time_scale_to_s, pressure_scale_to_Pa, pressure_offset_Pa)
    ) or not all(math.isfinite(v) for v in (time_scale_to_s, pressure_scale_to_Pa, pressure_offset_Pa)):
        raise ValueError("nonfinite unit mapping")
    if time_scale_to_s <= 0 or pressure_scale_to_Pa <= 0:
        raise ValueError("unit scales must be positive")
    if not isinstance(raw, bytes) or len(raw) > 128 * 1024 * 1024:
        raise ValueError("bounded source CSV bytes required")
    rows = _strict_pressure_csv_rows(raw, delimiter)
    headers = []
    for _ in range(header_rows):
        try:
            headers.append(next(rows))
        except StopIteration as exc:
            raise ValueError("CSV has fewer rows than the declared header") from exc
    times, pressures = [], []
    for row in rows:
        if len(times) >= maximum_rows:
            raise ValueError("CSV exceeds explicit row budget; no silent truncation")
        try:
            t = float(row[time_column]) * time_scale_to_s
            p = float(row[pressure_column]) * pressure_scale_to_Pa + pressure_offset_Pa
        except (ValueError, IndexError) as exc:
            raise ValueError("CSV does not match explicit numeric columns") from exc
        if not math.isfinite(t) or not math.isfinite(p) or times and t <= times[-1]:
            raise ValueError("nonfinite pressure/time or non-increasing clock")
        if pressure_reference == "absolute" and p <= 0:
            raise ValueError(
                "absolute pressure must be positive; signed gauge/perturbation values remain supported"
            )
        times.append(t)
        pressures.append(p)
    if len(times) < 2:
        raise ValueError("at least two measured rows are required")
    body = {
        "time_s": times,
        "pressure_Pa": pressures,
        "clock_id": clock_id,
        "sensor_id": sensor_id,
        "pressure_reference": pressure_reference,
        "mapping_evidence_id": mapping_evidence_id,
        "headers": headers,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "arrival_ground_truth": None,
        "leak_onset_ground_truth": None,
        "shared_clock_verified": False,
        "mapping": dict(
            delimiter=delimiter,
            header_rows=header_rows,
            time_column=time_column,
            pressure_column=pressure_column,
            time_scale_to_s=time_scale_to_s,
            pressure_scale_to_Pa=pressure_scale_to_Pa,
            pressure_offset_Pa=pressure_offset_Pa,
            clock_id=clock_id,
            sensor_id=sensor_id,
            maximum_rows=maximum_rows,
            mapping_evidence_id=mapping_evidence_id,
            pressure_reference=pressure_reference,
        ),
        "mapping_independently_validated": False,
        "source_time_origin": "caller_declared_source_clock; signed pre-trigger times are not shifted",
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return dict(body, record_digest=digest(body))


def _strict_pressure_csv_rows(raw: bytes, delimiter: str):
    """Parse CSV rows strictly and expose parser/field-limit failures uniformly."""
    try:
        text = raw.decode("utf-8-sig", errors="strict")
        yield from csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    except csv.Error as exc:
        raise ValueError("CSV quoting is malformed or a field exceeds the parser limit") from exc


def verify_pressure_csv_record(record, raw):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("pressure CSV record seal changed")
    if parse_pressure_csv(raw, **record["mapping"]) != record:
        raise ValueError("pressure CSV source/mapping/parser differs on replay")
    return True


def qualify_pressure_pair(
    first_record,
    first_raw,
    first_member,
    second_record,
    second_raw,
    second_member,
    *,
    pairing_evidence_id,
    common_trigger_verified,
    sample_alignment_verified,
):
    """Bind replayed P1/P2 records only when caller-supplied pairing evidence permits it."""
    if (
        not isinstance(pairing_evidence_id, str)
        or not pairing_evidence_id.strip()
        or type(common_trigger_verified) is not bool
        or type(sample_alignment_verified) is not bool
    ):
        raise ValueError("pairing evidence and explicit boolean review decisions are required")
    verify_pressure_csv_record(first_record, first_raw)
    verify_pressure_csv_record(second_record, second_raw)
    first_labels = pressure_member_labels(first_member)
    second_labels = pressure_member_labels(second_member)
    sensors = {first_labels["sensor_id"], second_labels["sensor_id"]}
    if first_labels["pair_key"] != second_labels["pair_key"] or sensors != {"P1", "P2"}:
        raise ValueError("records must be the P1/P2 members of one filename condition")
    record_sensors = {first_record["sensor_id"], second_record["sensor_id"]}
    if record_sensors != {"P1", "P2"} or first_record["sensor_id"] != first_labels["sensor_id"]:
        raise ValueError("parsed sensor identity differs from the selected archive members")
    if second_record["sensor_id"] != second_labels["sensor_id"]:
        raise ValueError("parsed sensor identity differs from the selected archive members")
    if first_record["pressure_reference"] != second_record["pressure_reference"]:
        raise ValueError("paired pressure reference must match")

    clocks_match = first_record["clock_id"] == second_record["clock_id"]
    coordinates_match = first_record["time_s"] == second_record["time_s"]
    ready = common_trigger_verified and sample_alignment_verified
    if ready and (not clocks_match or not coordinates_match):
        raise ValueError("verified trigger/alignment contradicts parsed clocks or coordinates")
    body = {
        "schema": "pipeline_integrity.pressure_pair_qualification.v1",
        "status": "ready_for_paired_analysis" if ready else "needs_pairing_evidence",
        "pair_key": first_labels["pair_key"],
        "sensor_ids": sorted(sensors),
        "pressure_reference": first_record["pressure_reference"],
        "source_sha256_by_sensor": {
            first_record["sensor_id"]: first_record["source_sha256"],
            second_record["sensor_id"]: second_record["source_sha256"],
        },
        "record_digest_by_sensor": {
            first_record["sensor_id"]: first_record["record_digest"],
            second_record["sensor_id"]: second_record["record_digest"],
        },
        "pairing_evidence_id": pairing_evidence_id,
        "common_trigger_verified": common_trigger_verified,
        "sample_alignment_verified": sample_alignment_verified,
        "clock_ids_match": clocks_match,
        "parsed_coordinates_match": coordinates_match,
        "arrival_ground_truth": None,
        "leak_onset_ground_truth": None,
        "pairing_does_not_establish_units_or_event_truth": True,
    }
    return {**body, "record_digest": digest(body)}


def verify_pressure_pair_qualification(
    qualification,
    first_record,
    first_raw,
    first_member,
    second_record,
    second_raw,
    second_member,
    *,
    expected_pairing_evidence_id,
):
    """Replay a pair qualification against both original parsed records and byte payloads."""
    if (
        not isinstance(expected_pairing_evidence_id, str)
        or not expected_pairing_evidence_id.strip()
        or not isinstance(qualification, dict)
        or qualification.get("record_digest")
        != digest({key: value for key, value in qualification.items() if key != "record_digest"})
    ):
        raise ValueError("pressure pair qualification seal changed")
    if qualification["pairing_evidence_id"] != expected_pairing_evidence_id:
        raise ValueError("pressure pair qualification evidence identity differs")
    reproduced = qualify_pressure_pair(
        first_record,
        first_raw,
        first_member,
        second_record,
        second_raw,
        second_member,
        pairing_evidence_id=qualification["pairing_evidence_id"],
        common_trigger_verified=qualification["common_trigger_verified"],
        sample_alignment_verified=qualification["sample_alignment_verified"],
    )
    if reproduced != qualification:
        raise ValueError("pressure pair qualification inputs or implementation differ on replay")
    return True


def build_pressure_pair_trace_record(
    qualification,
    first_record,
    first_raw,
    first_member,
    second_record,
    second_raw,
    second_member,
    *,
    expected_pairing_evidence_id,
    asset_id,
    trace_id_by_sensor,
    origin,
    time_origin_shift_s,
    time_origin_evidence_id,
    maximum_grid_error_s,
):
    """Build replayable fixed-grid Pa traces without interpolation or inferred time origin."""
    verify_pressure_pair_qualification(
        qualification,
        first_record,
        first_raw,
        first_member,
        second_record,
        second_raw,
        second_member,
        expected_pairing_evidence_id=expected_pairing_evidence_id,
    )
    if qualification["status"] != "ready_for_paired_analysis":
        raise ValueError("only a ready pressure pair can become paired analysis traces")
    if (
        not isinstance(asset_id, str)
        or not asset_id.strip()
        or not isinstance(time_origin_evidence_id, str)
        or not time_origin_evidence_id.strip()
        or origin not in {"measured", "laboratory"}
        or set(trace_id_by_sensor) != {"P1", "P2"}
        or any(not isinstance(v, str) or not v.strip() for v in trace_id_by_sensor.values())
        or len(set(trace_id_by_sensor.values())) != 2
    ):
        raise ValueError("explicit asset, trace IDs, source origin and time-origin evidence are required")
    if any(isinstance(v, bool) for v in (time_origin_shift_s, maximum_grid_error_s)) or not all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in (time_origin_shift_s, maximum_grid_error_s)
    ):
        raise ValueError("finite numeric time-origin and grid-error declarations are required")
    if maximum_grid_error_s < 0:
        raise ValueError("maximum grid error cannot be negative")

    records = {first_record["sensor_id"]: first_record, second_record["sensor_id"]: second_record}
    times = first_record["time_s"]
    if times != second_record["time_s"] or len(times) < 8:
        raise ValueError("ready paired traces require at least eight identical parsed coordinates")
    period = (times[-1] - times[0]) / (len(times) - 1)
    grid_error = max(abs(value - (times[0] + index * period)) for index, value in enumerate(times))
    start = times[0] + time_origin_shift_s
    if not math.isfinite(period) or period <= 0 or start < 0 or grid_error > maximum_grid_error_s:
        raise ValueError("parsed coordinates cannot satisfy the declared fixed-grid/time-origin contract")

    traces = {}
    for sensor_id in ("P1", "P2"):
        record = records[sensor_id]
        trace = SampledTrace(
            id=trace_id_by_sensor[sensor_id],
            asset_id=asset_id,
            sensor_id=sensor_id,
            clock_id=record["clock_id"],
            start_time_s=start,
            sample_period_s=period,
            quantity_id="pressure",
            unit="Pa",
            origin=origin,
            source_digest=record["source_sha256"],
            values=tuple(record["pressure_Pa"]),
            valid=(True,) * len(record["pressure_Pa"]),
        )
        traces[sensor_id] = trace.model_dump(mode="json")
    body = {
        "schema": "pipeline_integrity.pressure_pair_trace_record.v1",
        "qualification_digest": qualification["record_digest"],
        "parsed_record_digest_by_sensor": qualification["record_digest_by_sensor"],
        "asset_id": asset_id,
        "origin": origin,
        "time_origin_shift_s": float(time_origin_shift_s),
        "time_origin_evidence_id": time_origin_evidence_id,
        "maximum_grid_error_s": float(maximum_grid_error_s),
        "observed_grid_error_s": grid_error,
        "interpolation_or_resampling_performed": False,
        "traces": traces,
    }
    return {**body, "record_digest": digest(body)}


def verify_pressure_pair_trace_record(record, *args, **kwargs):
    """Replay the raw-to-SampledTrace bridge with caller-pinned source evidence."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("pressure pair trace record seal changed")
    replayed = build_pressure_pair_trace_record(
        *args,
        asset_id=record["asset_id"],
        trace_id_by_sensor={key: value["id"] for key, value in record["traces"].items()},
        origin=record["origin"],
        time_origin_shift_s=record["time_origin_shift_s"],
        time_origin_evidence_id=record["time_origin_evidence_id"],
        maximum_grid_error_s=record["maximum_grid_error_s"],
        **kwargs,
    )
    if replayed != record:
        raise ValueError("pressure pair trace construction differs on replay")
    return True
