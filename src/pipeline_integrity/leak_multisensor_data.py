"""Checksum-pinned inventory and explicit accelerometer mapping for the leak testbed archive."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .acoustics import DelayRequest, SampledTrace
from .contracts import RunContext, digest
from .datasets import catalog, file_digest
from .pressure_wave_data import (
    ARCHIVE_BYTES,
    ARCHIVE_SHA256,
    _archive_tool_identity,
    _bounded_command,
    verify_pressure_archive,
    pressure_member_labels,
)

ROOT_PREFIX = "Dataset of Leak Simulations in Experimental Testbed Water Network/"
ACCELEROMETER_PREFIX = ROOT_PREFIX + "Accelerometer/"
PRESSURE_PREFIX = ROOT_PREFIX + "Dynamic Pressure Sensor/"
HYDROPHONE_PREFIX = ROOT_PREFIX + "Hydrophone/"
_ACCELEROMETER_MEMBER = re.compile(
    re.escape(ACCELEROMETER_PREFIX)
    + r"(Branched|Looped)/(Circumferential Crack|Gasket Leak|Longitudinal Crack|No-leak|Orifice Leak)/"
    + r"(BR|LO)_(CC|GL|LC|NL|OL)_(0\.18 LPS|0\.47 LPS|ND|Transient)_(A[12])\.csv"
)
_HYDROPHONE_MEMBER = re.compile(
    re.escape(HYDROPHONE_PREFIX)
    + r"(Branched|Looped)/(Circumferential Crack|Gasket Leak|Longitudinal Crack|No-leak|Orifice Leak)/"
    + r"(BR|LO)_(CC|GL|LC|NL|OL)_(0\.18 LPS|0\.47 LPS|ND|Transient)_(N|NN)_(H[12])\.raw"
)
_HYDROPHONE_BACKGROUND_MEMBER = re.compile(
    re.escape(HYDROPHONE_PREFIX) + r"Background Noise/Background Noise_(H[12])\.raw"
)
_NETWORKS = {"BR": "Branched", "LO": "Looped"}
_LEAK_TYPES = {
    "CC": "Circumferential Crack",
    "GL": "Gasket Leak",
    "LC": "Longitudinal Crack",
    "NL": "No-leak",
    "OL": "Orifice Leak",
}


def _safe_member(member: str) -> PurePosixPath:
    if not isinstance(member, str):
        raise ValueError("archive member must be text")
    path = PurePosixPath(member)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in member
        or ":" in member
        or path.as_posix() != member
        or any(ord(character) < 32 or ord(character) == 127 for character in member)
    ):
        raise ValueError("unsafe archive member path")
    return path


def accelerometer_member_labels(member: str) -> dict:
    """Return filename labels without treating them as event or clock truth."""
    _safe_member(member)
    match = _ACCELEROMETER_MEMBER.fullmatch(member)
    if not match:
        raise ValueError("member is not an allowlisted accelerometer CSV")
    network, leak, network_code, leak_code, flow, sensor = match.groups()
    if network != _NETWORKS[network_code] or leak != _LEAK_TYPES[leak_code]:
        raise ValueError("directory and accelerometer filename labels disagree")
    return {
        "network": network,
        "leak_condition": leak,
        "background_flow_condition": flow,
        "sensor_id": sensor,
        "pair_key": member[:-6],
        "leak_onset_ground_truth": None,
        "shared_clock_verified": False,
        "physical_quantity": None,
        "physical_unit": None,
    }


def hydrophone_member_labels(member: str) -> dict:
    """Return hydrophone filename labels without decoding or assigning N/NN semantics."""
    _safe_member(member)
    background = _HYDROPHONE_BACKGROUND_MEMBER.fullmatch(member)
    if background:
        sensor = background.group(1)
        return {
            "network": None,
            "leak_condition": "Background Noise",
            "background_flow_condition": None,
            "filename_marker": None,
            "sensor_id": sensor,
            "pair_key": member[:-6],
            "vendor_raw_decoded": False,
            "filename_marker_meaning": None,
            "shared_clock_verified": False,
            "physical_quantity": None,
            "physical_unit": None,
        }
    match = _HYDROPHONE_MEMBER.fullmatch(member)
    if not match:
        raise ValueError("member is not an allowlisted hydrophone RAW file")
    network, leak, network_code, leak_code, flow, marker, sensor = match.groups()
    if network != _NETWORKS[network_code] or leak != _LEAK_TYPES[leak_code]:
        raise ValueError("directory and hydrophone filename labels disagree")
    return {
        "network": network,
        "leak_condition": leak,
        "background_flow_condition": flow,
        "filename_marker": marker,
        "sensor_id": sensor,
        "pair_key": member[:-6],
        "vendor_raw_decoded": False,
        "filename_marker_meaning": None,
        "shared_clock_verified": False,
        "physical_quantity": None,
        "physical_unit": None,
    }


def inventory_leak_multisensor_archive(
    archive: Path, *, tar_executable: Path, byte_budget: int, timeout_s: float
):
    """Count regular sensor files in the pinned archive; do not decode vendor RAW bytes."""
    verification = verify_pressure_archive(
        archive, expected_sha256=ARCHIVE_SHA256, expected_bytes=ARCHIVE_BYTES
    )
    source = next(item for item in catalog() if item.id == "leak_multisensor_transients")
    if (source.rights_review, source.acquisition, source.license_id) != (
        "reviewed_license",
        "allowed",
        "CC-BY-4.0",
    ):
        raise ValueError("multisensor source rights or acquisition policy changed")
    assets = [
        asset
        for asset in source.assets
        if asset.bytes == ARCHIVE_BYTES and asset.checksum == "sha256:" + ARCHIVE_SHA256
    ]
    if len(assets) != 1:
        raise ValueError("multisensor catalog must contain one pinned supplier archive")
    archive_receipt = {
        "filename": assets[0].filename,
        "bytes": verification["bytes"],
        "sha256": verification["sha256"],
    }
    tool = _archive_tool_identity(tar_executable)
    listing = _bounded_command(
        [tool["path"], "-tvf", verification["path"]],
        byte_budget=byte_budget,
        timeout_s=timeout_s,
    )
    families = {
        "accelerometer": {"prefix": ACCELEROMETER_PREFIX, "suffix": ".csv", "files": 0, "bytes": 0},
        "dynamic_pressure": {"prefix": PRESSURE_PREFIX, "suffix": ".csv", "files": 0, "bytes": 0},
        "hydrophone": {"prefix": HYDROPHONE_PREFIX, "suffix": ".raw", "files": 0, "bytes": 0},
    }
    seen = set()
    for line in listing.decode("utf-8", errors="strict").splitlines():
        parts = line.split(maxsplit=8)
        if len(parts) != 9 or not parts[0].startswith("-"):
            continue
        member = parts[8]
        _safe_member(member)
        selected = [
            value
            for value in families.values()
            if member.startswith(value["prefix"]) and member.lower().endswith(value["suffix"])
        ]
        if not selected:
            continue
        if len(selected) != 1 or parts[1] not in {"0", "1"} or member in seen:
            raise ValueError("duplicate, linked or ambiguous multisensor member")
        seen.add(member)
        selected[0]["files"] += 1
        selected[0]["bytes"] += int(parts[4])
    if verify_pressure_archive(archive) != verification or _archive_tool_identity(tool["path"]) != tool:
        raise ValueError("archive or archive tool changed during multisensor inventory")
    summaries = {
        key: {item: value[item] for item in ("files", "bytes", "suffix")} for key, value in families.items()
    }
    body = {
        "schema": "pipeline_integrity.leak_multisensor_archive_inventory.v1",
        "dataset_id": source.id,
        "archive": archive_receipt,
        "archive_tool": tool,
        "catalog_entry_sha256": digest(source),
        "license_id": source.license_id,
        "origin": source.origin,
        "families": summaries,
        "vendor_raw_decoded": False,
        "physical_units_inferred": False,
        "event_or_clock_truth_inferred": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return {**body, "record_digest": digest(body)}


class LeakMultisensorArchiveSession:
    """Read several allowlisted members with one final whole-archive integrity pass.

    Inventory verification seals the archive before any member read. During the session,
    file identity metadata is checked around each bounded extraction. Exiting the context
    performs a final full SHA-256 check; callers must treat yielded member receipts as
    provisional until that exit succeeds.
    """

    def __init__(self, archive: Path, *, tar_executable: Path, byte_budget: int, timeout_s: float):
        if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
            raise ValueError("explicit listing budget must be in (0, 128 MiB]")
        self.archive = Path(archive)
        self.tar_executable = tar_executable
        self.byte_budget = byte_budget
        self.timeout_s = timeout_s
        self.inventory = None
        self._members = None
        self._signature = None
        self._closed = True

    @staticmethod
    def _file_signature(path):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def __enter__(self):
        if not self._closed:
            raise RuntimeError("multisensor archive session is already open")
        inventory = inventory_leak_multisensor_archive(
            self.archive,
            tar_executable=self.tar_executable,
            byte_budget=self.byte_budget,
            timeout_s=self.timeout_s,
        )
        tool = inventory["archive_tool"]
        local = Path(self.archive).resolve(strict=True)
        signature = self._file_signature(local)
        if signature[2] != inventory["archive"]["bytes"]:
            raise ValueError("multisensor archive changed after inventory verification")
        listing = _bounded_command(
            [tool["path"], "-tvf", str(local)], byte_budget=self.byte_budget, timeout_s=self.timeout_s
        )
        members = {}
        for line in listing.decode("utf-8", errors="strict").splitlines():
            parts = line.split(maxsplit=8)
            if len(parts) != 9 or not parts[8].startswith(ROOT_PREFIX):
                continue
            member = parts[8]
            _safe_member(member)
            if member.startswith(ACCELEROMETER_PREFIX) and member.endswith(".csv"):
                accelerometer_member_labels(member)
                family = "accelerometer"
            elif member.startswith(PRESSURE_PREFIX) and member.endswith(".csv"):
                pressure_member_labels(member)
                family = "dynamic_pressure"
            elif member.startswith(HYDROPHONE_PREFIX) and member.endswith(".raw"):
                hydrophone_member_labels(member)
                family = "hydrophone"
            else:
                continue
            if not parts[0].startswith("-") or parts[1] not in {"0", "1"} or member in members:
                raise ValueError("multisensor session requires unique regular files")
            size = int(parts[4])
            if size < 0:
                raise ValueError("archive member has a negative byte count")
            members[member] = {"family": family, "bytes": size}
        if self._file_signature(local) != signature:
            raise ValueError("multisensor archive changed while opening the session")
        self.inventory = inventory
        self._members = members
        self._signature = signature
        self._local_archive = local
        self._tool = tool
        self._closed = False
        return self

    def members(self, *, family: str | None = None):
        """List allowlisted members after the source archive is pinned and opened."""
        if self._closed or self._members is None:
            raise RuntimeError("open the archive session with a context manager before listing members")
        if family is not None and family not in {"accelerometer", "dynamic_pressure", "hydrophone"}:
            raise ValueError("unknown multisensor family")
        return tuple(
            {"member": name, **entry}
            for name, entry in sorted(self._members.items())
            if family is None or entry["family"] == family
        )

    def hash_members(self, *, family: str, batch_size: int = 16):
        """Hash allowlisted members with bounded, per-member extraction."""
        members = self.members(family=family)
        if type(batch_size) is not int or not 1 <= batch_size <= 16:
            raise ValueError("member hash batch_size must be in [1,16]")
        receipts = []
        for offset in range(0, len(members), batch_size):
            batch = members[offset:offset + batch_size]
            if self._file_signature(self._local_archive) != self._signature:
                raise ValueError("multisensor archive changed before batch extraction")
            # Keep each member boundary explicit and stream its payload to disk.
            for item in batch:
                if item["bytes"] > 2_000_000_000:
                    raise ValueError("one member exceeds the 2 GB scratch budget")
                with tempfile.TemporaryFile(mode="w+b") as payload:
                    try:
                        completed = subprocess.run(
                            [self._tool["path"], "-xOf", str(self._local_archive), item["member"]],
                            stdout=payload,
                            stderr=subprocess.PIPE,
                            timeout=self.timeout_s,
                            check=False,
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise TimeoutError("member extraction exceeded the declared time budget") from exc
                    if completed.returncode != 0:
                        detail = completed.stderr[-2048:].decode("utf-8", errors="replace")
                        raise ValueError(f"member extractor failed: {detail}")
                    if payload.tell() != item["bytes"]:
                        raise ValueError("extracted member size differs from the pinned inventory")
                    payload.seek(0)
                    hasher = hashlib.sha256()
                    remaining = item["bytes"]
                    while remaining:
                        block = payload.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError("member output ended before its inventoried byte count")
                        hasher.update(block)
                        remaining -= len(block)
                    receipts.append(
                        {"member": item["member"], "family": family, "bytes": item["bytes"], "sha256": hasher.hexdigest()}
                    )
                if self._file_signature(self._local_archive) != self._signature:
                    raise ValueError("multisensor archive changed during member extraction")
        return tuple(receipts)

    def read_member(self, member: str, *, family: str, byte_budget: int):
        """Return one raw member and receipt; no member is extracted to a filesystem path."""
        if self._closed or self.inventory is None:
            raise RuntimeError("open the archive session with a context manager before reading")
        if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
            raise ValueError("explicit member budget must be in (0, 128 MiB]")
        entry = self._members.get(member)
        if entry is None or entry["family"] != family or entry["bytes"] > byte_budget:
            raise ValueError("member is absent, outside its sensor family, or exceeds its byte budget")
        if self._file_signature(self._local_archive) != self._signature:
            raise ValueError("multisensor archive metadata changed during the session")
        raw = _bounded_command(
            [self._tool["path"], "-xOf", str(self._local_archive), member],
            byte_budget=byte_budget,
            timeout_s=self.timeout_s,
        )
        if len(raw) != entry["bytes"] or self._file_signature(self._local_archive) != self._signature:
            raise ValueError("member size or source archive changed during extraction")
        common = {
            "member": member,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "labels": (
                accelerometer_member_labels(member)
                if family == "accelerometer"
                else hydrophone_member_labels(member)
                if family == "hydrophone"
                else pressure_member_labels(member)
            ),
            "inventory_record_digest": self.inventory["record_digest"],
            "archive_sha256": self.inventory["archive"]["sha256"],
            "family": family,
        }
        body = {
            "schema": "pipeline_integrity.leak_multisensor_member_source.v1",
            **common,
            "vendor_raw_decoded": False,
            "physical_units_inferred": False,
            "event_or_clock_truth_inferred": False,
        }
        return raw, {**body, "record_digest": digest(body)}

    def __exit__(self, exception_type, exception, traceback):
        if self._closed:
            return False
        self._closed = True
        current_tool = _archive_tool_identity(self._tool["path"])
        verified = verify_pressure_archive(
            self._local_archive,
            expected_sha256=self.inventory["archive"]["sha256"],
            expected_bytes=self.inventory["archive"]["bytes"],
        )
        if current_tool != self._tool or self._file_signature(self._local_archive) != self._signature:
            raise ValueError("multisensor archive or tool changed during the read session")
        if verified["sha256"] != self.inventory["archive"]["sha256"]:
            raise ValueError("multisensor archive failed its final source-integrity check")
        return False


def verify_leak_multisensor_member_source(record, raw: bytes):
    """Verify a session member receipt and its source bytes without decoding the signal."""
    if (
        not isinstance(record, dict)
        or record.get("schema") != "pipeline_integrity.leak_multisensor_member_source.v1"
        or record.get("record_digest")
        != digest({key: value for key, value in record.items() if key != "record_digest"})
        or not isinstance(raw, bytes)
        or record.get("bytes") != len(raw)
        or record.get("sha256") != hashlib.sha256(raw).hexdigest()
        or record.get("family") not in {"accelerometer", "dynamic_pressure", "hydrophone"}
        or record.get("archive_sha256") != ARCHIVE_SHA256
        or record.get("physical_units_inferred") is not False
        or record.get("event_or_clock_truth_inferred") is not False
        or record.get("vendor_raw_decoded") is not False
    ):
        raise ValueError("multisensor member receipt or source bytes changed")
    family, member = record["family"], record["member"]
    labels = (
        accelerometer_member_labels(member)
        if family == "accelerometer"
        else pressure_member_labels(member)
        if family == "dynamic_pressure"
        else hydrophone_member_labels(member)
    )
    if record.get("labels") != labels:
        raise ValueError("multisensor member labels differ from the allowlisted filename")
    return True


def read_accelerometer_member(
    archive: Path, member: str, *, tar_executable: Path, byte_budget: int, timeout_s: float
):
    """Read one regular allowlisted accelerometer CSV without extracting archive paths."""
    labels = accelerometer_member_labels(member)
    if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
        raise ValueError("explicit output budget must be in (0, 128 MiB]")
    inventory = inventory_leak_multisensor_archive(
        archive, tar_executable=tar_executable, byte_budget=1024 * 1024, timeout_s=timeout_s
    )
    tool = inventory["archive_tool"]
    local_archive = verify_pressure_archive(archive)
    expected_archive = inventory["archive"]
    if {
        "bytes": local_archive["bytes"],
        "sha256": local_archive["sha256"],
    } != {key: expected_archive[key] for key in ("bytes", "sha256")}:
        raise ValueError("selected archive differs from the portable inventory receipt")
    raw = _bounded_command(
        [tool["path"], "-xOf", local_archive["path"], member],
        byte_budget=byte_budget,
        timeout_s=timeout_s,
    )
    if verify_pressure_archive(archive) != local_archive or _archive_tool_identity(tool["path"]) != tool:
        raise ValueError("archive or archive tool changed during accelerometer read")
    return raw, {
        "member": member,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "labels": labels,
        "inventory_record_digest": inventory["record_digest"],
    }


def read_hydrophone_member(
    archive: Path, member: str, *, tar_executable: Path, byte_budget: int, timeout_s: float
):
    """Read one pinned opaque RAW member; do not interpret its vendor byte format."""
    labels = hydrophone_member_labels(member)
    if type(byte_budget) is not int or not 0 < byte_budget <= 128 * 1024 * 1024:
        raise ValueError("explicit output budget must be in (0, 128 MiB]")
    inventory = inventory_leak_multisensor_archive(
        archive, tar_executable=tar_executable, byte_budget=1024 * 1024, timeout_s=timeout_s
    )
    tool = inventory["archive_tool"]
    local_archive = verify_pressure_archive(archive)
    listing = _bounded_command(
        [tool["path"], "-tvf", local_archive["path"]],
        byte_budget=1024 * 1024,
        timeout_s=timeout_s,
    )
    selected = []
    for line in listing.decode("utf-8", errors="strict").splitlines():
        parts = line.split(maxsplit=8)
        if len(parts) == 9 and parts[8] == member:
            selected.append(parts)
    if (
        len(selected) != 1
        or not selected[0][0].startswith("-")
        or selected[0][1] not in {"0", "1"}
        or not 0 < int(selected[0][4]) <= byte_budget
    ):
        raise ValueError("selected hydrophone member is absent, linked or outside byte budget")
    raw = _bounded_command(
        [tool["path"], "-xOf", local_archive["path"], member],
        byte_budget=byte_budget,
        timeout_s=timeout_s,
    )
    if len(raw) != int(selected[0][4]):
        raise ValueError("hydrophone member byte count differs from the archive inventory")
    if verify_pressure_archive(archive) != local_archive or _archive_tool_identity(tool["path"]) != tool:
        raise ValueError("archive or archive tool changed during hydrophone read")
    body = {
        "schema": "pipeline_integrity.opaque_hydrophone_source.v1",
        "member": member,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "labels": labels,
        "inventory_record_digest": inventory["record_digest"],
        "vendor_raw_decoded": False,
        "decoder_id": None,
        "calibration_id": None,
    }
    return raw, {**body, "record_digest": digest(body)}


def verify_hydrophone_source_record(record, raw: bytes):
    """Verify an opaque source receipt without interpreting the payload."""
    if (
        not isinstance(record, dict)
        or record.get("record_digest")
        != digest({key: value for key, value in record.items() if key != "record_digest"})
        or not isinstance(raw, bytes)
        or record.get("bytes") != len(raw)
        or record.get("sha256") != hashlib.sha256(raw).hexdigest()
        or record.get("labels") != hydrophone_member_labels(record.get("member", ""))
        or record.get("vendor_raw_decoded") is not False
        or record.get("decoder_id") is not None
        or record.get("calibration_id") is not None
    ):
        raise ValueError("opaque hydrophone source receipt or bytes changed")
    return True


def parse_accelerometer_csv(
    raw: bytes,
    *,
    delimiter: str,
    header_rows: int,
    coordinate_column: int,
    value_column: int,
    coordinate_scale_to_s: float,
    value_scale_to_m_s2: float,
    value_offset_m_s2: float,
    clock_id: str,
    sensor_id: str,
    maximum_rows: int,
    mapping_evidence_id: str,
):
    """Apply only caller-reviewed coordinate and acceleration mappings."""
    integers = (header_rows, coordinate_column, value_column, maximum_rows)
    scalars = (coordinate_scale_to_s, value_scale_to_m_s2, value_offset_m_s2)
    if (
        not isinstance(raw, bytes)
        or len(raw) > 128 * 1024 * 1024
        or len(delimiter) != 1
        or any(type(value) is not int for value in integers)
        or header_rows < 0
        or coordinate_column < 0
        or value_column < 0
        or coordinate_column == value_column
        or not 0 < maximum_rows <= 2_000_000
        or any(isinstance(value, bool) or not math.isfinite(value) for value in scalars)
        or coordinate_scale_to_s <= 0
        or value_scale_to_m_s2 <= 0
        or not clock_id.strip()
        or not sensor_id.strip()
        or not mapping_evidence_id.strip()
    ):
        raise ValueError("invalid explicit accelerometer mapping")
    rows = csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="strict")), delimiter=delimiter)
    headers = []
    for _ in range(header_rows):
        try:
            headers.append(next(rows))
        except StopIteration as error:
            raise ValueError("CSV has fewer rows than the declared header") from error
    times, accelerations = [], []
    for row in rows:
        if len(times) >= maximum_rows:
            raise ValueError("CSV exceeds explicit row budget; no silent truncation")
        try:
            time = float(row[coordinate_column]) * coordinate_scale_to_s
            value = float(row[value_column]) * value_scale_to_m_s2 + value_offset_m_s2
        except (IndexError, ValueError) as error:
            raise ValueError("CSV does not match explicit numeric columns") from error
        if not math.isfinite(time) or not math.isfinite(value) or times and time <= times[-1]:
            raise ValueError("nonfinite acceleration/time or non-increasing clock")
        times.append(time)
        accelerations.append(value)
    if len(times) < 2:
        raise ValueError("at least two measured rows are required")
    mapping = {
        "delimiter": delimiter,
        "header_rows": header_rows,
        "coordinate_column": coordinate_column,
        "value_column": value_column,
        "coordinate_scale_to_s": coordinate_scale_to_s,
        "value_scale_to_m_s2": value_scale_to_m_s2,
        "value_offset_m_s2": value_offset_m_s2,
        "clock_id": clock_id,
        "sensor_id": sensor_id,
        "maximum_rows": maximum_rows,
        "mapping_evidence_id": mapping_evidence_id,
    }
    body = {
        "schema": "pipeline_integrity.accelerometer_observations.v1",
        "time_s": times,
        "acceleration_m_s2": accelerations,
        "clock_id": clock_id,
        "sensor_id": sensor_id,
        "headers": headers,
        "mapping": mapping,
        "mapping_evidence_id": mapping_evidence_id,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "leak_onset_ground_truth": None,
        "shared_clock_verified": False,
        "mapping_independently_validated": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return {**body, "record_digest": digest(body)}


def verify_accelerometer_csv_record(record: dict, raw: bytes):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("accelerometer CSV record seal changed")
    if parse_accelerometer_csv(raw, **record["mapping"]) != record:
        raise ValueError("accelerometer source, mapping or parser differs on replay")
    return True


def qualify_accelerometer_pair(
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
    unit_mapping_reviewed,
):
    """Bind replayed A1/A2 records only after explicit unit, trigger and alignment review."""
    flags = (common_trigger_verified, sample_alignment_verified, unit_mapping_reviewed)
    if (
        not isinstance(pairing_evidence_id, str)
        or not pairing_evidence_id.strip()
        or any(type(value) is not bool for value in flags)
    ):
        raise ValueError("pairing evidence and explicit boolean review decisions are required")
    verify_accelerometer_csv_record(first_record, first_raw)
    verify_accelerometer_csv_record(second_record, second_raw)
    first_labels = accelerometer_member_labels(first_member)
    second_labels = accelerometer_member_labels(second_member)
    sensors = {first_labels["sensor_id"], second_labels["sensor_id"]}
    if first_labels["pair_key"] != second_labels["pair_key"] or sensors != {"A1", "A2"}:
        raise ValueError("records must be the A1/A2 members of one filename condition")
    if (
        first_record["sensor_id"] != first_labels["sensor_id"]
        or second_record["sensor_id"] != second_labels["sensor_id"]
        or {first_record["sensor_id"], second_record["sensor_id"]} != {"A1", "A2"}
    ):
        raise ValueError("parsed sensor identity differs from the selected accelerometer members")
    clocks_match = first_record["clock_id"] == second_record["clock_id"]
    coordinates_match = first_record["time_s"] == second_record["time_s"]
    ready = all(flags)
    if ready and (not clocks_match or not coordinates_match):
        raise ValueError("verified accelerometer pairing contradicts parsed clocks or coordinates")
    body = {
        "schema": "pipeline_integrity.accelerometer_pair_qualification.v1",
        "status": "ready_for_paired_analysis" if ready else "needs_pairing_or_mapping_evidence",
        "pair_key": first_labels["pair_key"],
        "sensor_ids": sorted(sensors),
        "quantity_id": "acceleration",
        "unit": "m/s^2",
        "source_sha256_by_sensor": {
            first_record["sensor_id"]: first_record["source_sha256"],
            second_record["sensor_id"]: second_record["source_sha256"],
        },
        "record_digest_by_sensor": {
            first_record["sensor_id"]: first_record["record_digest"],
            second_record["sensor_id"]: second_record["record_digest"],
        },
        "mapping_evidence_id_by_sensor": {
            first_record["sensor_id"]: first_record["mapping_evidence_id"],
            second_record["sensor_id"]: second_record["mapping_evidence_id"],
        },
        "pairing_evidence_id": pairing_evidence_id,
        "common_trigger_verified": common_trigger_verified,
        "sample_alignment_verified": sample_alignment_verified,
        "unit_mapping_reviewed": unit_mapping_reviewed,
        "clock_ids_match": clocks_match,
        "parsed_coordinates_match": coordinates_match,
        "leak_onset_ground_truth": None,
        "arrival_ground_truth": None,
        "pairing_does_not_establish_event_truth": True,
    }
    return {**body, "record_digest": digest(body)}


def verify_accelerometer_pair_qualification(
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
    if (
        not isinstance(expected_pairing_evidence_id, str)
        or not expected_pairing_evidence_id.strip()
        or not isinstance(qualification, dict)
        or qualification.get("record_digest")
        != digest({key: value for key, value in qualification.items() if key != "record_digest"})
        or qualification.get("pairing_evidence_id") != expected_pairing_evidence_id
    ):
        raise ValueError("accelerometer pair qualification seal or evidence changed")
    replayed = qualify_accelerometer_pair(
        first_record,
        first_raw,
        first_member,
        second_record,
        second_raw,
        second_member,
        pairing_evidence_id=qualification["pairing_evidence_id"],
        common_trigger_verified=qualification["common_trigger_verified"],
        sample_alignment_verified=qualification["sample_alignment_verified"],
        unit_mapping_reviewed=qualification["unit_mapping_reviewed"],
    )
    if replayed != qualification:
        raise ValueError("accelerometer pair qualification inputs or implementation differ on replay")
    return True


def build_accelerometer_pair_trace_record(
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
    """Build replayable fixed-grid acceleration traces without interpolation or inferred roles."""
    verify_accelerometer_pair_qualification(
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
        raise ValueError("only a ready accelerometer pair can become paired analysis traces")
    if (
        not isinstance(asset_id, str)
        or not asset_id.strip()
        or not isinstance(time_origin_evidence_id, str)
        or not time_origin_evidence_id.strip()
        or origin not in {"measured", "laboratory"}
        or set(trace_id_by_sensor) != {"A1", "A2"}
        or any(not isinstance(value, str) or not value.strip() for value in trace_id_by_sensor.values())
        or len(set(trace_id_by_sensor.values())) != 2
    ):
        raise ValueError("explicit asset, trace IDs, source origin and time-origin evidence are required")
    numeric = (time_origin_shift_s, maximum_grid_error_s)
    if any(isinstance(value, bool) for value in numeric) or not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in numeric
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
    if not math.isfinite(period) or period <= 0 or grid_error > maximum_grid_error_s or start < 0:
        raise ValueError("parsed accelerometer clock is not an accepted nonnegative fixed grid")
    traces = {}
    for sensor_id, record in records.items():
        trace = SampledTrace(
            id=trace_id_by_sensor[sensor_id],
            asset_id=asset_id,
            sensor_id=sensor_id,
            clock_id=record["clock_id"],
            start_time_s=start,
            sample_period_s=period,
            quantity_id="acceleration",
            unit="m/s^2",
            origin=origin,
            source_digest=record["source_sha256"],
            values=tuple(record["acceleration_m_s2"]),
            valid=(True,) * len(record["acceleration_m_s2"]),
        )
        traces[sensor_id] = trace.model_dump(mode="json")
    body = {
        "schema": "pipeline_integrity.accelerometer_pair_trace_record.v1",
        "qualification_digest": qualification["record_digest"],
        "parsed_record_digest_by_sensor": qualification["record_digest_by_sensor"],
        "asset_id": asset_id,
        "origin": origin,
        "time_origin_shift_s": float(time_origin_shift_s),
        "time_origin_evidence_id": time_origin_evidence_id,
        "maximum_grid_error_s": float(maximum_grid_error_s),
        "observed_grid_error_s": grid_error,
        "interpolation_or_resampling_performed": False,
        "sensor_roles_inferred": False,
        "traces": traces,
    }
    return {**body, "record_digest": digest(body)}


def verify_accelerometer_pair_trace_record(record, *args, **kwargs):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("accelerometer pair trace record seal changed")
    replayed = build_accelerometer_pair_trace_record(
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
        raise ValueError("accelerometer pair trace construction differs on replay")
    return True


def bind_accelerometer_delay_request(
    trace_record,
    qualification,
    context,
    request,
    *,
    expected_trace_record_digest,
    expected_qualification_digest,
    left_sensor_id,
    right_sensor_id,
    sensor_role_evidence_id,
    analysis_evidence_id,
):
    """Bind qualified A1/A2 traces to explicit roles and the existing acoustic delay engine."""
    if (
        not isinstance(trace_record, dict)
        or trace_record.get("record_digest") != expected_trace_record_digest
        or expected_trace_record_digest
        != digest({key: value for key, value in trace_record.items() if key != "record_digest"})
    ):
        raise ValueError("accelerometer trace record differs from the caller-pinned boundary")
    if (
        not isinstance(qualification, dict)
        or qualification.get("record_digest") != expected_qualification_digest
        or expected_qualification_digest
        != digest({key: value for key, value in qualification.items() if key != "record_digest"})
        or trace_record.get("qualification_digest") != expected_qualification_digest
    ):
        raise ValueError("accelerometer qualification differs from the caller-pinned boundary")
    if (
        qualification.get("schema") != "pipeline_integrity.accelerometer_pair_qualification.v1"
        or qualification.get("status") != "ready_for_paired_analysis"
        or not qualification.get("common_trigger_verified")
        or not qualification.get("sample_alignment_verified")
        or not qualification.get("unit_mapping_reviewed")
    ):
        raise ValueError("accelerometer pair is not ready for delay analysis")
    if left_sensor_id == right_sensor_id or {left_sensor_id, right_sensor_id} != set(
        trace_record.get("traces", {})
    ):
        raise ValueError("explicit distinct left/right roles must cover the accelerometer pair")
    traces = {
        sensor_id: SampledTrace.model_validate_json(json.dumps(payload))
        for sensor_id, payload in trace_record["traces"].items()
    }
    c = RunContext.model_validate(context.model_dump(mode="python"))
    q = DelayRequest.model_validate(request.model_dump(mode="python"))
    evidence_ids = [item.id for item in c.evidence]
    mapping_evidence = set(qualification.get("mapping_evidence_id_by_sensor", {}).values())
    required = {
        qualification.get("pairing_evidence_id"),
        sensor_role_evidence_id,
        analysis_evidence_id,
        *mapping_evidence,
    }
    if (
        None in required
        or any(not isinstance(value, str) or not value.strip() for value in required)
        or len(evidence_ids) != len(set(evidence_ids))
        or not required <= set(evidence_ids)
    ):
        raise ValueError("mapping, pairing, sensor-role and analysis evidence must be actual context members")
    for trace in traces.values():
        if (
            trace.quantity_id != "acceleration"
            or trace.unit != "m/s^2"
            or (trace.asset_id, trace.clock_id, trace.origin) != (c.asset_id, c.clock_id, c.origin)
        ):
            raise ValueError("qualified accelerometer traces differ from the context or SI quantity")
    if qualification.get("source_sha256_by_sensor") != {
        sensor_id: trace.source_digest for sensor_id, trace in traces.items()
    }:
        raise ValueError("accelerometer qualification and trace sources differ")
    metadata = dict(c.metadata)
    metadata.update(
        accelerometer_delay_boundary={
            "trace_record_digest": expected_trace_record_digest,
            "qualification_digest": expected_qualification_digest,
            "delay_request_digest": digest(q),
            "left_sensor_id": left_sensor_id,
            "right_sensor_id": right_sensor_id,
            "sensor_role_evidence_id": sensor_role_evidence_id,
            "analysis_evidence_id": analysis_evidence_id,
            "delay_definition": "arrival_at_right_minus_arrival_at_left",
            "sensor_roles_inferred": False,
            "location_or_probability_calculated": False,
        }
    )
    return (
        traces[left_sensor_id],
        traces[right_sensor_id],
        q,
        c.model_copy(update={"metadata": metadata}),
    )
