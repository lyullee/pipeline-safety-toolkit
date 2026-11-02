"""Pinned water-network source inventory with raw labels and split identities retained."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, EvidenceRef, Name, RunContext, digest
from .datasets import file_digest
from .inverse_localization import (
    LocalizationRequest,
    LocalizationRow,
    LocationCandidate,
    boundary_metadata,
)
from .metrology import MeasurementBasis
from .source_archive import pinned_source_zip

DATASET_ID = "probabilistic_leak_localization"
ARCHIVE_FILENAME = "data.zip"
ROOT = PurePosixPath("data")
MAXIMUM_ARCHIVE_BYTES = 256_000_000
MAXIMUM_MEMBER_BYTES = 256_000_000
MAXIMUM_TOTAL_BYTES = 512_000_000
MAXIMUM_MEMBERS = 128
EVENT_TABLES = {
    "data/raw/BKTown/experiments/infield_logging_leaks_MC_3.csv",
    "data/raw/BKTown/experiments/infield_logging_leaks_10_m3h.csv",
}
PRESSURE_TABLES = {
    "data/raw/BKTown/pressure_meas/mobile_pressure_data-08072020_17112020-UTCp01h00.csv",
    "data/raw/BKTown/pressure_meas/mobile_pressure_data-08122020_15022021-UTCp01h00.csv",
}
EVENT_HEADERS = ("Datum", "Beginuur", "Einduur", "Locatie", "3GE FID", "Opmerkingen")
PRESSURE_HEADER = "datetime"
NETWORK_LINKS = (
    {
        "run_id": "HPC_run_08",
        "input": "data/input/BKTown/HPC_run_08/BKTown_original_WDN_8Dec2020_17Dec2020_uncalibrated.inp",
        "sensors": "data/input/BKTown/HPC_run_08/pressure_loggers_asset_id.csv",
        "events": "data/raw/BKTown/experiments/infield_logging_leaks_MC_3.csv",
    },
    {
        "run_id": "HPC_run_05",
        "input": "data/input/BKTown/HPC_run_05/BKTown_original_WDN_8Dec2020_17Dec2020_calibrated.inp",
        "sensors": "data/input/BKTown/HPC_run_05/pressure_loggers_asset_id.csv",
        "events": "data/raw/BKTown/experiments/infield_logging_leaks_MC_3.csv",
    },
    {
        "run_id": "HPC_run_04",
        "input": "data/input/BKTown/HPC_run_04/BKTown_extended_WDN_01Jul2020_31Aug2020_calibrated.inp",
        "sensors": "data/input/BKTown/HPC_run_04/pressure_loggers_asset_id.csv",
        "events": "data/raw/BKTown/experiments/infield_logging_leaks_10_m3h.csv",
    },
)


class WaterHeadSensorBinding(Contract):
    """Caller-reviewed channel/port/basis mapping for one pressure-log column."""

    channel_id: Name
    physical_port_id: Name
    basis: MeasurementBasis
    mapping_evidence_id: Name

    @model_validator(mode="after")
    def head_basis(self):
        if self.basis.unit != "m" or self.basis.pressure_reference is not None:
            raise ValueError("water pressure-head observations require a length basis in metres")
        if self.basis.quantity != "pressure_head":
            raise ValueError("water log channels must be reviewed as hydraulic pressure head")
        return self


class WaterNetworkCoordinateTransform(Contract):
    """Caller-reviewed affine map from source x/y coordinates into target metres."""

    source_frame_id: Name
    source_coordinate_unit_id: Name
    target_frame_id: Name
    linear_m_per_source_unit: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    offset_m: tuple[float, float, float]
    evidence_id: Name

    @model_validator(mode="after")
    def full_rank(self):
        if len(self.linear_m_per_source_unit) != 3 or any(
            len(row) != 2 for row in self.linear_m_per_source_unit
        ):
            raise ValueError("coordinate transform must map two source axes into three metre axes")
        a = tuple(self.linear_m_per_source_unit[row][0] for row in range(3))
        b = tuple(self.linear_m_per_source_unit[row][1] for row in range(3))
        cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
        if not all(math.isfinite(value) for value in (*a, *b, *self.offset_m)):
            raise ValueError("coordinate transform values must be finite")
        if not all(math.isfinite(value) for value in cross):
            raise ValueError("coordinate transform axis cross-product overflowed")
        try:
            cross_norm_squared = math.fsum(value * value for value in cross)
        except (OverflowError, ValueError) as exc:
            raise ValueError("coordinate transform rank check overflowed") from exc
        if not math.isfinite(cross_norm_squared) or cross_norm_squared <= 0:
            raise ValueError("coordinate transform must preserve two independent source axes")
        return self


class WaterNetworkLocationCandidates(Contract):
    """Sealed finite location candidates derived through a reviewed coordinate transform."""

    source_coordinate_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_coordinate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transform: WaterNetworkCoordinateTransform
    candidates: tuple[LocationCandidate, ...]
    candidate_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def verify_digest(self):
        expected = digest(self.model_dump(mode="python", exclude={"candidate_record_digest"}))
        if self.candidate_record_digest != expected:
            raise ValueError("coordinate candidate conversion record changed")


class WaterNetworkInverseObservationBridge(Contract):
    """Traceable observation rows plus source, clock-origin and port-mapping evidence."""

    schema: Literal["pipeline_integrity.water_network_inverse_observation_bridge.v1"] = (
        "pipeline_integrity.water_network_inverse_observation_bridge.v1"
    )
    source_pressure_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_member_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_origin_timestamp_local: str
    sensor_bindings: dict[str, WaterHeadSensorBinding]
    rows: tuple[LocalizationRow, ...]
    reader_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bridge_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def verify_digest(self):
        expected = digest(self.model_dump(mode="python", exclude={"bridge_digest"}))
        if self.bridge_digest != expected:
            raise ValueError("inverse observation bridge or mapping evidence changed")


def _member_name(name: str) -> PurePosixPath:
    if not isinstance(name, str):
        raise ValueError("archive member name must be text")
    member = PurePosixPath(name.rstrip("/"))
    if (
        member.is_absolute()
        or ".." in member.parts
        or "\\" in name
        or ":" in name
        or "//" in name
        or member.as_posix() != name.rstrip("/")
        or not member.is_relative_to(ROOT)
    ):
        raise ValueError("archive member must remain below the reviewed data root")
    return member


def _csv_profile(payload: bytes) -> dict:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed CSV member is not UTF-8") from exc
    rows = csv.reader(io.StringIO(text, newline=""))
    header = next(rows, None)
    if not header or any(not cell for cell in header):
        raise ValueError("CSV needs a nonempty header row")
    width = len(header)
    nonempty_cells = [0] * width
    blank_cells = [0] * width
    row_count = 0
    for row in rows:
        if len(row) != width:
            raise ValueError("CSV row width differs from its source header")
        row_count += 1
        for index, value in enumerate(row):
            (nonempty_cells if value != "" else blank_cells)[index] += 1
    return {
        "header": header,
        "row_count": row_count,
        "nonempty_cells_by_column": nonempty_cells,
        "blank_cells_by_column": blank_cells,
        "source_order_preserved": True,
        "interpretation": "raw text only; no unit, clock, missingness or event semantics assigned",
    }


def parse_water_network_event_table(payload: bytes, *, member_name: str) -> dict:
    """Retain the two published event tables as source cells without interpreting their labels."""
    if member_name not in EVENT_TABLES:
        raise ValueError("select one reviewed field-experiment event table")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed event table is not UTF-8") from exc
    rows = csv.reader(io.StringIO(text, newline=""))
    headers = next(rows, None)
    if tuple(headers or ()) != EVENT_HEADERS:
        raise ValueError("published event table header profile changed")
    observations = []
    for source_row, cells in enumerate(rows, start=2):
        if len(cells) != len(EVENT_HEADERS):
            raise ValueError("published event row width differs from the reviewed profile")
        observations.append(
            {"source_row": source_row, "source_cells": dict(zip(EVENT_HEADERS, cells, strict=True))}
        )
    body = {
        "schema": "pipeline_integrity.water_network_event_source.v1",
        "dataset_id": DATASET_ID,
        "member_name": member_name,
        "member_sha256": hashlib.sha256(payload).hexdigest(),
        "headers": list(EVENT_HEADERS),
        "observations": observations,
        "source_order_preserved": True,
        "date_and_times_parsed": False,
        "coordinates_resolved": False,
        "event_duration_seconds": None,
        "flow_units_assigned": False,
        "record_digest": None,
    }
    body["record_digest"] = digest(body)
    return body


def _pressure_window_rows(
    payload: bytes,
    *,
    sensor_ids: tuple[str, ...],
    start: str,
    end: str,
    maximum_rows: int,
    maximum_values: int = 1_000_000,
):
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed pressure table is not UTF-8") from exc
    begin, finish = _source_timestamp(start), _source_timestamp(end)
    if finish < begin:
        raise ValueError("pressure window end must follow its start")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames or []
    if not header or header[0] != PRESSURE_HEADER or len(header) != len(set(header)):
        raise ValueError("pressure table needs unique sensor columns after its datetime field")
    if not sensor_ids or len(sensor_ids) != len(set(sensor_ids)) or set(sensor_ids) - set(header[1:]):
        raise ValueError("select distinct sensor IDs that exist in the source header")
    result = []
    previous_timestamp = None
    for source_row, raw in enumerate(reader, start=2):
        stamp = raw.get(PRESSURE_HEADER)
        timestamp = _source_timestamp(stamp)
        if begin <= timestamp <= finish:
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError("selected source timestamps must strictly increase")
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("pressure table row width differs from its source header")
            result.append(
                {
                    "source_row": source_row,
                    "timestamp_local": stamp,
                    "values": {sensor: raw[sensor] or None for sensor in sensor_ids},
                }
            )
            previous_timestamp = timestamp
            if len(result) > maximum_rows or len(result) * len(sensor_ids) > maximum_values:
                raise ValueError("selected pressure window exceeds its caller row budget")
    return header, result


def _source_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("source timestamp must remain text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("source timestamp differs from the reviewed local clock format") from exc
    if parsed.tzinfo is not None:
        raise ValueError("source local clock must not be assigned an unreviewed timezone")
    return parsed


def _csv_identifier_column(payload: bytes, column: str) -> tuple[str, ...]:
    rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    if column not in (rows.fieldnames or ()):
        raise ValueError("reviewed identity column is absent from source CSV")
    values = tuple(row[column] for row in rows)
    if not values or any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError("reviewed source identity column contains blanks or duplicates")
    return values


def _inp_identities(payload: bytes) -> tuple[set[str], set[str]]:
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed EPANET input is not UTF-8") from exc
    section = None
    junction_ids, coordinate_ids = set(), set()
    for line in lines:
        value = line.strip()
        if not value or value.startswith(";"):
            continue
        if value.startswith("[") and value.endswith("]"):
            section = value.upper()
            continue
        identifier = value.split()[0]
        if section == "[JUNCTIONS]":
            junction_ids.add(identifier)
        elif section == "[COORDINATES]":
            coordinate_ids.add(identifier)
    if not junction_ids or not coordinate_ids:
        raise ValueError("reviewed EPANET file needs junction and coordinate sections")
    return junction_ids, coordinate_ids


def _epanet_coordinate_rows(payload: bytes, node_ids: tuple[str, ...]) -> list[dict]:
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("reviewed EPANET input is not UTF-8") from exc
    section = None
    found = {}
    for line_number, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw or raw.startswith(";"):
            continue
        if raw.startswith("[") and raw.endswith("]"):
            section = raw.upper()
            continue
        if section != "[COORDINATES]":
            continue
        fields = raw.split()
        if len(fields) < 3:
            raise ValueError("EPANET coordinate row needs node ID and two axes")
        node_id, x_text, y_text = fields[:3]
        if node_id in found:
            raise ValueError("EPANET coordinate node identities must be unique")
        try:
            x_value, y_value = float(x_text), float(y_text)
        except ValueError as exc:
            raise ValueError("EPANET coordinate axes must be numeric source values") from exc
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise ValueError("EPANET coordinate axes must be finite")
        found[node_id] = {
            "node_id": node_id,
            "source_x": x_value,
            "source_y": y_value,
            "source_x_text": x_text,
            "source_y_text": y_text,
            "source_line": line_number,
        }
    absent = set(node_ids) - set(found)
    if absent:
        raise ValueError("one or more requested candidate nodes have no EPANET coordinate row")
    return [found[node_id] for node_id in node_ids]


def read_epanet_coordinate_record(
    path: Path,
    *,
    run_id: str,
    candidate_node_ids: tuple[str, ...],
) -> dict:
    """Read selected raw node coordinates from one checksum-pinned EPANET input member."""
    selected = [row for row in NETWORK_LINKS if row["run_id"] == run_id]
    if len(selected) != 1:
        raise ValueError("select one reviewed water-network run ID")
    if not candidate_node_ids or len(candidate_node_ids) != len(set(candidate_node_ids)):
        raise ValueError("candidate node IDs must be nonempty and unique")
    if len(candidate_node_ids) > 256:
        raise ValueError("coordinate candidate selection exceeds the inverse-location limit")
    path = Path(path)
    binding = selected[0]
    with pinned_source_zip(
        path,
        DATASET_ID,
        asset_filename=ARCHIVE_FILENAME,
        maximum_archive_bytes=MAXIMUM_ARCHIVE_BYTES,
        maximum_member_bytes=MAXIMUM_MEMBER_BYTES,
        maximum_total_bytes=MAXIMUM_TOTAL_BYTES,
        maximum_members=MAXIMUM_MEMBERS,
    ) as (archive, provenance):
        info = archive.getinfo(binding["input"])
        if info.file_size > MAXIMUM_MEMBER_BYTES:
            raise ValueError("EPANET input exceeds the reviewed source member budget")
        payload = archive.read(info)
        node_rows = _epanet_coordinate_rows(payload, candidate_node_ids)
        body = {
            "schema": "pipeline_integrity.water_network_coordinates.v1",
            "dataset_id": DATASET_ID,
            "provenance": provenance,
            "run_id": run_id,
            "member_name": binding["input"],
            "member_crc32": f"{info.CRC:08x}",
            "member_sha256": hashlib.sha256(payload).hexdigest(),
            "source_coordinate_frame_id": None,
            "source_length_unit": None,
            "rows": node_rows,
            "source_order": [row["node_id"] for row in node_rows],
            "coordinate_interpretation": "source x/y numbers only; frame, datum, axis and unit unresolved",
            "reader_sha256": file_digest(Path(__file__)),
            "record_digest": None,
        }
        body["record_digest"] = digest(body)
        return body


def verify_epanet_coordinate_record(record: dict, *, path: Path) -> bool:
    """Replay the selected coordinate rows from the pinned source member."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {**record, "record_digest": None}
    ):
        raise ValueError("water-network coordinate source record seal changed")
    if not isinstance(record.get("rows"), list):
        raise ValueError("water-network coordinate source rows are malformed")
    replay = read_epanet_coordinate_record(
        path,
        run_id=record.get("run_id"),
        candidate_node_ids=tuple(row.get("node_id") for row in record["rows"]),
    )
    if replay != record:
        raise ValueError("water-network coordinate source bytes or reader changed on replay")
    return True


def to_water_network_location_candidates(
    record: dict,
    *,
    transform: WaterNetworkCoordinateTransform,
    candidate_id_by_node: dict[str, str],
    location_id_by_node: dict[str, str],
    physical_port_id_by_node: dict[str, str],
    evidence_id_by_node: dict[str, str],
    candidate_parameters_by_node: dict[str, dict[str, float | None]],
) -> WaterNetworkLocationCandidates:
    """Transform source coordinates only with caller-reviewed map and location/port evidence."""
    _validated_source_record(
        record,
        schema="pipeline_integrity.water_network_coordinates.v1",
    )
    member_sha256 = record.get("member_sha256")
    if not isinstance(member_sha256, str) or len(member_sha256) != 64:
        raise ValueError("water coordinate record lacks its INP member checksum")
    if not isinstance(record.get("rows"), list) or not record["rows"]:
        raise ValueError("water-network coordinate source record has no selected nodes")
    node_ids = tuple(row.get("node_id") for row in record["rows"])
    if len(node_ids) < 2 or len(node_ids) > 256 or len(node_ids) != len(set(node_ids)):
        raise ValueError("inverse localization needs two to 256 distinct candidate nodes")
    mappings = (
        candidate_id_by_node,
        location_id_by_node,
        physical_port_id_by_node,
        evidence_id_by_node,
        candidate_parameters_by_node,
    )
    if any(set(mapping) != set(node_ids) for mapping in mappings):
        raise ValueError("each candidate node needs explicit identity, port, evidence and parameter mappings")
    parameter_keys = {frozenset(values) for values in candidate_parameters_by_node.values()}
    if len(parameter_keys) != 1:
        raise ValueError("all finite candidates must declare the same fixed parameter identities")
    candidate_ids = tuple(candidate_id_by_node[node_id] for node_id in node_ids)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate identity mappings must be unique")
    matrix = transform.linear_m_per_source_unit
    candidates = []
    for row in record["rows"]:
        node_id, x_value, y_value = row["node_id"], row["source_x"], row["source_y"]
        try:
            position = tuple(
                math.fsum((matrix[axis][0] * x_value, matrix[axis][1] * y_value, transform.offset_m[axis]))
                for axis in range(3)
            )
        except (OverflowError, ValueError) as exc:
            raise ValueError("coordinate transform overflowed its finite representation") from exc
        if not all(math.isfinite(value) for value in position):
            raise ValueError("coordinate transform produced a nonfinite position")
        candidates.append(
            LocationCandidate(
                id=candidate_id_by_node[node_id],
                location_id=location_id_by_node[node_id],
                coordinate_frame_id=transform.target_frame_id,
                position_m=position,
                physical_port_id=physical_port_id_by_node[node_id],
                parameters=candidate_parameters_by_node[node_id],
                evidence_id=evidence_id_by_node[node_id],
            )
        )
    body = {
        "source_coordinate_record_digest": record["record_digest"],
        "source_archive_sha256": record["provenance"]["archive_sha256"],
        "source_coordinate_sha256": record["member_sha256"],
        "transform": transform,
        "candidates": tuple(candidates),
    }
    return WaterNetworkLocationCandidates(
        **body,
        candidate_record_digest=digest(body),
    )


def _network_id_coverage(archive) -> list[dict]:
    results = []
    for binding in NETWORK_LINKS:
        junctions, coordinates = _inp_identities(archive.read(binding["input"]))
        sensor_ids = _csv_identifier_column(archive.read(binding["sensors"]), "asset_id")
        event_ids = _csv_identifier_column(archive.read(binding["events"]), "3GE FID")
        results.append(
            {
                "run_id": binding["run_id"],
                "sensor_asset_count": len(sensor_ids),
                "sensor_ids_in_junction_section": sum(value in junctions for value in sensor_ids),
                "sensor_ids_in_coordinate_section": sum(value in coordinates for value in sensor_ids),
                "event_node_count": len(event_ids),
                "event_ids_in_junction_section": sum(value in junctions for value in event_ids),
                "event_ids_in_coordinate_section": sum(value in coordinates for value in event_ids),
                "identity_basis": "exact source strings only; no field-port, coordinate-frame, axis or length unit assigned",
                "source_members": binding,
            }
        )
    return results


def read_water_network_pressure_window(
    path: Path,
    *,
    member_name: str,
    sensor_ids: tuple[str, ...],
    start_timestamp: str,
    end_timestamp: str,
    maximum_rows: int = 100_000,
    maximum_values: int = 1_000_000,
) -> dict:
    """Read explicitly selected local-clock pressure-head text without assigning timezone or SI pressure."""
    if member_name not in PRESSURE_TABLES:
        raise ValueError("select one reviewed BKTown pressure logger table")
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 1_000_000:
        raise ValueError("pressure-window row budget is outside the supported range")
    if type(maximum_values) is not int or not 1 <= maximum_values <= 10_000_000:
        raise ValueError("pressure-window sensor-value budget is outside the supported range")
    path = Path(path)
    with pinned_source_zip(
        path,
        DATASET_ID,
        asset_filename=ARCHIVE_FILENAME,
        maximum_archive_bytes=MAXIMUM_ARCHIVE_BYTES,
        maximum_member_bytes=MAXIMUM_MEMBER_BYTES,
        maximum_total_bytes=MAXIMUM_TOTAL_BYTES,
        maximum_members=MAXIMUM_MEMBERS,
    ) as (archive, provenance):
        try:
            info = archive.getinfo(member_name)
        except KeyError as exc:
            raise ValueError("selected pressure table is absent from the pinned archive") from exc
        if info.file_size > MAXIMUM_MEMBER_BYTES:
            raise ValueError("pressure table exceeds the source member budget")
        payload = archive.read(info)
        header, rows = _pressure_window_rows(
            payload,
            sensor_ids=sensor_ids,
            start=start_timestamp,
            end=end_timestamp,
            maximum_rows=maximum_rows,
            maximum_values=maximum_values,
        )
        body = {
            "schema": "pipeline_integrity.water_network_pressure_window.v1",
            "dataset_id": DATASET_ID,
            "provenance": provenance,
            "member_name": member_name,
            "member_bytes": info.file_size,
            "member_crc32": f"{info.CRC:08x}",
            "member_sha256": hashlib.sha256(payload).hexdigest(),
            "header": header,
            "selected_sensor_ids": list(sensor_ids),
            "maximum_rows": maximum_rows,
            "maximum_selected_values": maximum_values,
            "selection": {
                "start_timestamp_local_inclusive": start_timestamp,
                "end_timestamp_local_inclusive": end_timestamp,
            },
            "clock_timezone": None,
            "timestamps_parsed_as_naive_local": True,
            "pressure_quantity": "source pressure-head values; caller basis review required",
            "pressure_unit": None,
            "rows": rows,
            "source_order_preserved": True,
            "reader_sha256": file_digest(Path(__file__)),
            "record_digest": None,
        }
        body["record_digest"] = digest(body)
        return body


def verify_water_network_pressure_window(record: dict, *, path: Path) -> bool:
    """Re-read the pinned archive window and compare every raw value and source binding."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {**record, "record_digest": None}
    ):
        raise ValueError("water pressure-window source record seal changed")
    selection = record.get("selection", {})
    replay = read_water_network_pressure_window(
        path,
        member_name=record.get("member_name"),
        sensor_ids=tuple(record.get("selected_sensor_ids", ())),
        start_timestamp=selection.get("start_timestamp_local_inclusive"),
        end_timestamp=selection.get("end_timestamp_local_inclusive"),
        maximum_rows=record.get("maximum_rows"),
        maximum_values=record.get("maximum_selected_values"),
    )
    if replay != record:
        raise ValueError("water pressure-window raw source, selection or reader differs on replay")
    return True


def _validated_source_record(record: dict, *, schema: str) -> dict:
    if not isinstance(record, dict) or record.get("schema") != schema:
        raise ValueError("water-network source record schema differs")
    if record.get("record_digest") != digest({**record, "record_digest": None}):
        raise ValueError("water-network source record seal changed")
    if record.get("reader_sha256") != file_digest(Path(__file__)):
        raise ValueError("water-network source record was produced by a different reader revision")
    provenance = record.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("supplier_checksum_verified") is not True
        or not isinstance(provenance.get("archive_sha256"), str)
        or len(provenance["archive_sha256"]) != 64
    ):
        raise ValueError("water-network source record lacks pinned archive provenance")
    return record


def _to_inverse_localization_rows(
    record: dict,
    *,
    event_origin_timestamp: str,
    sensor_bindings: dict[str, WaterHeadSensorBinding],
    maximum_output_rows: int = 512,
) -> tuple[LocalizationRow, ...]:
    """Build owner rows after source-record schema, hash and reader identity checks."""
    _validated_source_record(
        record,
        schema="pipeline_integrity.water_network_pressure_window.v1",
    )
    member_sha256 = record.get("member_sha256")
    if not isinstance(member_sha256, str) or len(member_sha256) != 64:
        raise ValueError("water pressure-window record lacks its raw member checksum")
    if record.get("clock_timezone") is not None or record.get("timestamps_parsed_as_naive_local") is not True:
        raise ValueError("source clock metadata changed or acquired an unreviewed timezone")
    if set(sensor_bindings) != set(record.get("selected_sensor_ids", ())):
        raise ValueError("provide exactly one reviewed basis and physical port for every selected sensor")
    if type(maximum_output_rows) is not int or not 1 <= maximum_output_rows <= 512:
        raise ValueError("inverse observation row budget must be between one and 512")
    bindings = {
        key: WaterHeadSensorBinding.model_validate(value.model_dump(mode="python"))
        for key, value in sensor_bindings.items()
    }
    origin = _source_timestamp(event_origin_timestamp)
    output = []
    for row in record["rows"]:
        elapsed = (_source_timestamp(row["timestamp_local"]) - origin).total_seconds()
        if elapsed < 0:
            raise ValueError("inverse localization origin must not follow any selected observation")
        for sensor in record["selected_sensor_ids"]:
            raw = row["values"][sensor]
            value = None
            valid = False
            if raw is not None:
                try:
                    numeric = float(raw)
                except (TypeError, ValueError):
                    numeric = math.nan
                if math.isfinite(numeric):
                    value, valid = numeric, True
            binding = bindings[sensor]
            output.append(
                LocalizationRow(
                    id=f"{sensor}_{row['source_row']}",
                    channel_id=binding.channel_id,
                    sensor_id=sensor,
                    physical_port_id=binding.physical_port_id,
                    time_s=elapsed,
                    basis=binding.basis,
                    value=value,
                    valid=valid,
                )
            )
            if len(output) > maximum_output_rows:
                raise ValueError(
                    "inverse observation row budget exceeded; select a narrower window or fewer sensors"
                )
    return tuple(output)


def build_water_network_inverse_bridge(
    record: dict,
    *,
    event_origin_timestamp: str,
    sensor_bindings: dict[str, WaterHeadSensorBinding],
    maximum_output_rows: int = 512,
) -> WaterNetworkInverseObservationBridge:
    """Create owner localization rows while retaining per-sensor mapping and source evidence."""
    rows = _to_inverse_localization_rows(
        record,
        event_origin_timestamp=event_origin_timestamp,
        sensor_bindings=sensor_bindings,
        maximum_output_rows=maximum_output_rows,
    )
    provenance = record["provenance"]
    body = {
        "schema": "pipeline_integrity.water_network_inverse_observation_bridge.v1",
        "source_pressure_record_digest": record["record_digest"],
        "source_archive_sha256": provenance["archive_sha256"],
        "source_member_sha256": record["member_sha256"],
        "event_origin_timestamp_local": event_origin_timestamp,
        "sensor_bindings": {
            key: WaterHeadSensorBinding.model_validate(value.model_dump(mode="python"))
            for key, value in sensor_bindings.items()
        },
        "rows": rows,
        "reader_sha256": file_digest(Path(__file__)),
    }
    return WaterNetworkInverseObservationBridge(
        **body,
        bridge_digest=digest(body),
    )


def bind_water_network_localization_context(
    request: LocalizationRequest,
    bridge: WaterNetworkInverseObservationBridge,
    candidate_set: WaterNetworkLocationCandidates,
    *,
    scenario_id: str,
    origin: Literal["measured", "simulation"],
    dataset_evidence_id: str,
    evidence: tuple[EvidenceRef, ...],
) -> RunContext:
    """Bind matching water observations, candidates and reviewed evidence to an owner request."""
    q = LocalizationRequest.model_validate(request.model_dump(mode="python"))
    bridge.verify_digest()
    candidate_set.verify_digest()
    if bridge.source_archive_sha256 != candidate_set.source_archive_sha256:
        raise ValueError("pressure observations and coordinate candidates come from different archives")
    if q.observation_source_sha256 != bridge.source_member_sha256:
        raise ValueError("localization request does not pin the bridged pressure member")
    if q.rows != bridge.rows or q.candidates != candidate_set.candidates:
        raise ValueError("localization request rows or candidates differ from the sealed adapter receipts")
    ids = [item.id for item in evidence]
    if len(set(ids)) != len(ids):
        raise ValueError("water-network context evidence identifiers must be unique")
    by_id = {item.id: item for item in evidence}
    dataset_ref = by_id.get(dataset_evidence_id)
    if (
        dataset_ref is None
        or dataset_ref.origin != origin
        or dataset_ref.sha256 != bridge.source_archive_sha256
        or dataset_ref.license_id != "CC-BY-4.0"
    ):
        raise ValueError(
            "include an origin-matched EvidenceRef with the pinned archive SHA and CC-BY-4.0 license"
        )
    required = {q.evidence_id, dataset_evidence_id, candidate_set.transform.evidence_id}
    required.update(bridge.sensor_bindings[sensor].mapping_evidence_id for sensor in bridge.sensor_bindings)
    required.update(candidate.evidence_id for candidate in q.candidates)
    required.update(q.review_evidence_ids.values())
    required.update(basis.evidence_id for basis in q.parameter_bases.values())
    required.update(row.basis.evidence_id for row in q.rows)
    required.update(
        basis.reference_state.evidence_id
        for basis in (*q.parameter_bases.values(), *(row.basis for row in q.rows))
        if basis.reference_state is not None
    )
    if required - set(ids):
        raise ValueError("water-network RunContext lacks request, measurement, candidate or mapping evidence")
    return RunContext(
        asset_id=q.asset_id,
        scenario_id=scenario_id,
        clock_id=q.clock_id,
        origin=origin,
        evidence=evidence,
        metadata={
            "inverse_localization_boundary": boundary_metadata(q),
            "water_network_source": {
                "dataset_evidence_id": dataset_evidence_id,
                "archive_sha256": bridge.source_archive_sha256,
                "pressure_member_sha256": bridge.source_member_sha256,
                "pressure_bridge_digest": bridge.bridge_digest,
                "coordinate_member_sha256": candidate_set.source_coordinate_sha256,
                "coordinate_record_digest": candidate_set.source_coordinate_record_digest,
                "candidate_record_digest": candidate_set.candidate_record_digest,
                "coordinate_transform_evidence_id": candidate_set.transform.evidence_id,
            },
        },
    )


def inspect_water_network_archive(path: Path) -> dict:
    """Pin the archive and inspect bounded metadata/CSV schemas without extraction or pickle loading."""
    path = Path(path)
    with pinned_source_zip(
        path,
        DATASET_ID,
        asset_filename=ARCHIVE_FILENAME,
        maximum_archive_bytes=MAXIMUM_ARCHIVE_BYTES,
        maximum_member_bytes=MAXIMUM_MEMBER_BYTES,
        maximum_total_bytes=MAXIMUM_TOTAL_BYTES,
        maximum_members=MAXIMUM_MEMBERS,
    ) as (archive, provenance):
        members = []
        for info in archive.infolist():
            name = info.filename
            if name.startswith("__MACOSX/"):
                continue
            logical = _member_name(name)
            row = {
                "name": name,
                "bytes": info.file_size,
                "crc32": f"{info.CRC:08x}",
                "kind": "directory" if info.is_dir() else logical.suffix.lower().lstrip(".") or "unknown",
                "network_id": logical.parts[2] if len(logical.parts) > 2 else None,
                "run_id": next(
                    (part for part in logical.parts if part.startswith(("HPC_run_", "local_run_"))), None
                ),
                "source_stage": logical.parts[1] if len(logical.parts) > 1 else None,
            }
            if not info.is_dir() and logical.suffix.lower() == ".csv":
                payload = archive.read(info)
                row["csv_profile"] = _csv_profile(payload)
                if name in EVENT_TABLES:
                    event_record = parse_water_network_event_table(payload, member_name=name)
                    row["event_table_summary"] = {
                        "observation_count": len(event_record["observations"]),
                        "member_sha256": event_record["member_sha256"],
                        "source_cells_embedded": False,
                    }
            elif not info.is_dir() and logical.suffix.lower() == ".pkl":
                row["interpretation"] = "opaque pickle; deliberately not deserialized or executed"
            members.append(row)
        body = {
            "schema": "pipeline_integrity.water_network_archive_inventory.v1",
            "dataset_id": DATASET_ID,
            "provenance": provenance,
            "members": members,
            "network_id_coverage": _network_id_coverage(archive),
            "file_count": sum(row["kind"] != "directory" for row in members),
            "csv_count": sum(row["kind"] == "csv" for row in members),
            "pickle_count": sum(row["kind"] == "pkl" for row in members),
            "source_hash": file_digest(path),
            "reader_sha256": file_digest(Path(__file__)),
            "extracted": False,
            "archive_members_executed": False,
            "record_digest": None,
        }
        body["record_digest"] = digest(body)
        return body


def verify_water_network_archive_inventory(record: dict, *, path: Path) -> bool:
    """Re-read the currently pinned archive and require an identical source inventory."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {**record, "record_digest": None}
    ):
        raise ValueError("water-network source inventory seal changed")
    replay = inspect_water_network_archive(path)
    if replay != record:
        raise ValueError("water-network archive bytes or current inventory reader changed")
    return True
