"""Read the CC0 water-distribution event workbook archive without inventing units or time."""

from __future__ import annotations

import hashlib
import math
import os
import platform
import stat
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

from .contracts import Contract, SignalFrame, digest

ARCHIVE_BYTES = 2_265_766
ARCHIVE_MD5 = "4ac57517151a5958ebc2226954254f8d"
ARCHIVE_SHA256 = "d1452c4a311f2c3132ddda1b78caab4c782f5c658553c5e32266cee230db459c"
REPOSITORY = "https://github.com/Moazeni-s-Concise-Lab/WDSEventDB"
REPOSITORY_COMMIT = "b9a3b54fbfce153e0728ec7c7e9f1e18414a0c19"
ZENODO_RECORD = "https://zenodo.org/records/17547955"
REPOSITORY_LICENSE = "CC0-1.0"
_HEADERS = (
    "Pressure 1 Out",
    "Pressure 2 Out",
    "Pressure 3 In",
    "Pressure 4 In",
    "Water Flow 1",
    "Water Flow 2",
    "Water Flow 3",
    "Water Flow 4",
    "VFD 1",
    "VFD 2",
    "VFD 3",
    "VFD 4-1",
    "VFD 4-2",
    "Analog Valve 1",
    "Analog Valve 2",
    "Labels",
)
_WORKBOOKS = {
    "WDSEventDB/Clean/CleanData.xlsx": ("clean", "CleanData"),
    "WDSEventDB/Leakage/LeakEvent1.xlsx": ("leakage", "LeakEvent1"),
    "WDSEventDB/Leakage/LeakEvent2.xlsx": ("leakage", "LeakEvent2"),
    "WDSEventDB/Leakage/LeakEvent3.xlsx": ("leakage", "LeakEvent3"),
    "WDSEventDB/Sensor Failure/SensorEvent1.xlsx": ("sensor_failure", "SensorEvent1"),
    "WDSEventDB/Sensor Failure/SensorEvent23.xlsx": ("sensor_failure", "SensorEvent23"),
    "WDSEventDB/Sensor Failure/SensorEvent45.xlsx": ("sensor_failure", "SensorEvent45"),
    "WDSEventDB/Sensor Failure/SensorEvent67.xlsx": ("sensor_failure", "SensorEvent67"),
    "WDSEventDB/Cyberattack/CyberEvent1-4.xlsx": ("cyberattack", "CyberEvent1-4"),
}
_MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 250_000


class WdsEventTable(Contract):
    member: str
    category: Literal["clean", "leakage", "sensor_failure", "cyberattack"]
    event_id: str
    sheet_name: str
    workbook_sha256: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    excel_row_numbers: tuple[int, ...]
    formula_cells: tuple[tuple[int, int, str], ...]
    table_digest: str

    def check_integrity(self):
        body = self.model_dump(mode="json", exclude={"table_digest"})
        if digest(body) != self.table_digest:
            raise ValueError("WDS event table changed")


class WdsEventArchive(Contract):
    schema_version: Literal["pipeline_integrity.wds_event_archive.v2"] = "pipeline_integrity.wds_event_archive.v2"
    archive_bytes: int
    archive_md5: str
    archive_sha256: str
    repository: str = REPOSITORY
    repository_commit: str = REPOSITORY_COMMIT
    license_id: Literal["CC0-1.0"] = "CC0-1.0"
    reader_sha256: str
    python_version: str
    openpyxl_version: str
    tables: tuple[WdsEventTable, ...]
    units_mapped: Literal[False] = False
    time_axis_mapped: Literal[False] = False
    formulas_evaluated: Literal[False] = False
    record_digest: str

    def check_integrity(self):
        checked = WdsEventArchive.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("WDS event archive receipt changed")
        if checked.reader_sha256 != _reader_sha256():
            raise ValueError("WDS event reader code differs from the code pinned in this receipt")
        if len(checked.tables) != len(_WORKBOOKS) or {t.member for t in checked.tables} != set(_WORKBOOKS):
            raise ValueError("WDS event archive workbook inventory changed")
        for table in checked.tables:
            table.check_integrity()
        return True


class WdsEventSelection(Contract):
    """A source-bound, caller-defined selection of unchanged workbook rows."""

    schema_version: Literal["pipeline_integrity.wds_event_selection.v1"] = "pipeline_integrity.wds_event_selection.v1"
    archive_digest: str
    member: str
    workbook_sha256: str
    selected_excel_rows: tuple[int, ...]
    selection_rule: str
    selection_evidence_id: str
    selection_evidence_sha256: str
    selected_row_digests: tuple[str, ...]
    units_mapped: Literal[False] = False
    time_axis_mapped: Literal[False] = False
    failure_probability_estimated: Literal[False] = False
    record_digest: str

    def check_integrity(self, archive: WdsEventArchive):
        archive.check_integrity()
        body = self.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("WDS event selection receipt changed")
        if len(self.selection_evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.selection_evidence_sha256):
            raise ValueError("WDS selection evidence artifact SHA-256 is invalid")
        if self.archive_digest != archive.record_digest:
            raise ValueError("WDS event selection belongs to a different archive receipt")
        tables = {table.member: table for table in archive.tables}
        table = tables.get(self.member)
        if table is None or table.workbook_sha256 != self.workbook_sha256:
            raise ValueError("WDS event selection workbook identity differs")
        rows = dict(zip(table.excel_row_numbers, table.rows, strict=True))
        if (
            len(self.selected_excel_rows) != len(set(self.selected_excel_rows))
            or tuple(sorted(self.selected_excel_rows)) != self.selected_excel_rows
        ):
            raise ValueError("WDS event selection row references are invalid")
        expected = tuple(digest({"excel_row": number, "values": rows.get(number)}) for number in self.selected_excel_rows)
        if any(rows.get(number) is None for number in self.selected_excel_rows) or expected != self.selected_row_digests:
            raise ValueError("WDS event selection row payload differs from its source")
        return True


class WdsEventEvidencePacket(Contract):
    """Transport metadata for a consumer adapter; raw measurements stay in the source archive."""

    schema_version: Literal["pipeline_integrity.wds_event_evidence_packet.v1"] = "pipeline_integrity.wds_event_evidence_packet.v1"
    selection_digest: str
    archive_digest: str
    workbook_sha256: str
    member: str
    selected_excel_rows: tuple[int, ...]
    selection_evidence_id: str
    selection_evidence_sha256: str
    purpose: Literal["monitoring_replay", "dataset_partition", "risk_evidence_review"]
    consumer_contract: str
    raw_rows_included: Literal[False] = False
    units_mapped: Literal[False] = False
    time_axis_mapped: Literal[False] = False
    probability_estimated: Literal[False] = False
    record_digest: str

    def check_integrity(self, archive: WdsEventArchive, selection: WdsEventSelection):
        selection.check_integrity(archive)
        body = self.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("WDS event evidence packet changed")
        expected = (
            selection.record_digest,
            selection.archive_digest,
            selection.workbook_sha256,
            selection.member,
            selection.selected_excel_rows,
            selection.selection_evidence_id,
            selection.selection_evidence_sha256,
        )
        actual = (
            self.selection_digest,
            self.archive_digest,
            self.workbook_sha256,
            self.member,
            self.selected_excel_rows,
            self.selection_evidence_id,
            self.selection_evidence_sha256,
        )
        if actual != expected:
            raise ValueError("WDS evidence packet differs from its reviewed selection")
        return True


class WdsEventPartition(Contract):
    """Caller-defined group partition for evaluation; no independence is inferred."""

    schema_version: Literal["pipeline_integrity.wds_event_partition.v1"] = "pipeline_integrity.wds_event_partition.v1"
    archive_digest: str
    member_sha256: dict[str, str]
    group_members: dict[str, tuple[str, ...]]
    split_groups: dict[Literal["train", "validation", "test"], tuple[str, ...]]
    grouping_rule: str
    grouping_evidence_id: str
    grouping_evidence_sha256: str
    row_random_split: Literal[False] = False
    independence_established: Literal[False] = False
    record_digest: str

    def check_integrity(self, archive: WdsEventArchive):
        archive.check_integrity()
        body = self.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("WDS partition receipt changed")
        if self.archive_digest != archive.record_digest:
            raise ValueError("WDS partition belongs to another source archive")
        current = {table.member: table.workbook_sha256 for table in archive.tables}
        if current != self.member_sha256:
            raise ValueError("WDS partition workbook inventory or digest changed")
        if not self.group_members or set(member for members in self.group_members.values() for member in members) != set(current):
            raise ValueError("WDS partition groups must cover every source workbook")
        if len(self.group_members) > 10000 or any(not key.strip() for key in self.group_members):
            raise ValueError("WDS partition group count or identifiers exceed the contract")
        if any(not members or len(members) != len(set(members)) for members in self.group_members.values()):
            raise ValueError("WDS partition groups must contain unique workbook members")
        if len({member for members in self.group_members.values() for member in members}) != len(current):
            raise ValueError("a workbook cannot belong to multiple partition groups")
        if not {"train", "test"} <= set(self.split_groups):
            raise ValueError("train and test partitions are required")
        assigned = [group for groups in self.split_groups.values() for group in groups]
        if len(assigned) != len(set(assigned)) or set(assigned) != set(self.group_members):
            raise ValueError("every reviewed source group must be assigned to exactly one split")
        if any(not groups or len(groups) != len(set(groups)) for groups in self.split_groups.values()):
            raise ValueError("declared partitions cannot be empty or repeat groups")
        if not self.grouping_rule.strip() or not self.grouping_evidence_id.strip():
            raise ValueError("grouping rule and evidence reference are required")
        if len(self.grouping_evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.grouping_evidence_sha256):
            raise ValueError("grouping evidence artifact SHA-256 is invalid")
        return True



class WdsSignalChannelMapping(Contract):
    """Caller-reviewed affine conversion from one workbook column to an SI signal."""

    source_header: str
    signal_name: str
    source_unit: str
    si_unit: str
    multiplier_to_si: float
    offset_to_si: float = 0.0

    def check_integrity(self):
        if not self.source_header.strip() or self.source_header == "Labels":
            raise ValueError("a continuous instrument header is required; event labels are excluded")
        if not self.signal_name.strip() or not self.source_unit.strip() or not self.si_unit.strip():
            raise ValueError("signal name and reviewed source/SI units are required")
        if not math.isfinite(self.multiplier_to_si) or self.multiplier_to_si == 0 or not math.isfinite(self.offset_to_si):
            raise ValueError("channel conversion must be finite with a nonzero scale")
        return True


class WdsSignalMapping(Contract):
    """Reviewed physical channel and clock mapping supplied by the consumer."""

    schema_version: Literal["pipeline_integrity.wds_signal_mapping.v1"] = "pipeline_integrity.wds_signal_mapping.v1"
    member: str
    stream_id: str
    clock_id: str
    channels: tuple[WdsSignalChannelMapping, ...]
    mapping_rule: str
    mapping_evidence_id: str
    mapping_evidence_sha256: str
    time_basis: str
    time_evidence_id: str
    time_evidence_sha256: str
    record_digest: str

    def check_integrity(self, archive: WdsEventArchive):
        archive.check_integrity()
        if digest(self.model_dump(mode="json", exclude={"record_digest"})) != self.record_digest:
            raise ValueError("WDS signal mapping receipt changed")
        table = next((item for item in archive.tables if item.member == self.member), None)
        if table is None:
            raise ValueError("WDS signal mapping member is not in the pinned archive")
        if not self.channels or len({item.source_header for item in self.channels}) != len(self.channels):
            raise ValueError("signal mappings must use unique source headers")
        if len({item.signal_name for item in self.channels}) != len(self.channels):
            raise ValueError("signal mappings must use unique output channel names")
        if any(item.source_header not in table.headers for item in self.channels):
            raise ValueError("mapped source header is absent from the selected workbook")
        if not all((self.stream_id.strip(), self.clock_id.strip(), self.mapping_rule.strip(), self.mapping_evidence_id.strip(), self.time_basis.strip(), self.time_evidence_id.strip())):
            raise ValueError("stream, clock, mapping rule and evidence references are required")
        for value in (self.mapping_evidence_sha256, self.time_evidence_sha256):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("mapping and time evidence must be pinned by SHA-256")
        for item in self.channels:
            item.check_integrity()
        return True


class WdsSignalBatch(Contract):
    """SignalFrames plus source-row identity; it never infers time from row numbers."""

    schema_version: Literal["pipeline_integrity.wds_signal_batch.v1"] = "pipeline_integrity.wds_signal_batch.v1"
    archive_digest: str
    table_digest: str
    mapping_digest: str
    selection_digest: str
    member: str
    event_category: Literal["clean", "leakage", "sensor_failure", "cyberattack"]
    event_id: str
    excel_row_numbers: tuple[int, ...]
    time_s: tuple[float, ...]
    time_axis_digest: str
    frames: tuple[SignalFrame, ...]
    invalid_values_by_channel: dict[str, int]
    row_random_split: Literal[False] = False
    failure_probability_estimated: Literal[False] = False
    record_digest: str

    def check_integrity(self, archive: WdsEventArchive, mapping: WdsSignalMapping, selection: WdsEventSelection):
        selection.check_integrity(archive)
        mapping.check_integrity(archive)
        if digest(self.model_dump(mode="json", exclude={"record_digest"})) != self.record_digest:
            raise ValueError("WDS SignalFrame batch receipt changed")
        table = next(item for item in archive.tables if item.member == mapping.member)
        source_identity = (archive.record_digest, table.table_digest, mapping.record_digest, table.member, table.category, table.event_id)
        recorded_identity = (self.archive_digest, self.table_digest, self.mapping_digest, self.member, self.event_category, self.event_id)
        if source_identity != recorded_identity:
            raise ValueError("WDS SignalFrame batch source identity differs")
        if (
            selection.member != self.member
            or selection.record_digest != self.selection_digest
            or selection.selected_excel_rows != self.excel_row_numbers
        ):
            raise ValueError("WDS SignalFrame batch differs from its reviewed row selection")
        if not self.frames or len(self.frames) != len(self.excel_row_numbers) or len(self.time_s) != len(self.excel_row_numbers):
            raise ValueError("WDS SignalFrame batch row and frame counts differ")
        if tuple(sorted(set(self.excel_row_numbers))) != self.excel_row_numbers:
            raise ValueError("WDS SignalFrame source rows must be unique and ordered")
        if any(not math.isfinite(value) or value < 0 for value in self.time_s) or any(right <= left for left, right in zip(self.time_s, self.time_s[1:])):
            raise ValueError("WDS SignalFrame batch time values must be finite and strictly increasing")
        expected_time_digest = digest({"rows": self.excel_row_numbers, "time_s": self.time_s, "time_basis": mapping.time_basis, "evidence_id": mapping.time_evidence_id, "evidence_sha256": mapping.time_evidence_sha256})
        if expected_time_digest != self.time_axis_digest:
            raise ValueError("WDS SignalFrame batch time-axis digest differs")
        expected_frames, expected_invalid = _map_wds_signal_frames(table, mapping, self.excel_row_numbers, self.time_s)
        if tuple(frame.model_dump(mode="json") for frame in self.frames) != tuple(frame.model_dump(mode="json") for frame in expected_frames):
            raise ValueError("WDS SignalFrames differ from the pinned source rows and reviewed mapping")
        if self.invalid_values_by_channel != expected_invalid:
            raise ValueError("WDS invalid-value counts differ from source replay")
        return True


def _map_wds_signal_frames(table, mapping, excel_rows, times):
    source_rows = dict(zip(table.excel_row_numbers, table.rows, strict=True))
    if any(row_number not in source_rows for row_number in excel_rows):
        raise ValueError("WDS SignalFrame batch references a row outside the pinned workbook")
    columns = {channel.source_header: table.headers.index(channel.source_header) for channel in mapping.channels}
    frames, invalid = [], {channel.signal_name: 0 for channel in mapping.channels}
    for row_number, time_s in zip(excel_rows, times, strict=True):
        source = source_rows[row_number]
        values, units, valid = {}, {}, {}
        for channel in mapping.channels:
            raw_value = source[columns[channel.source_header]]
            usable = type(raw_value) in (int, float) and math.isfinite(raw_value)
            converted = raw_value * channel.multiplier_to_si + channel.offset_to_si if usable else None
            usable = usable and math.isfinite(converted)
            values[channel.signal_name] = converted if usable else None
            units[channel.signal_name] = channel.si_unit
            valid[channel.signal_name] = usable
            invalid[channel.signal_name] += int(not usable)
        frames.append(SignalFrame(
            stream_id=mapping.stream_id, clock_id=mapping.clock_id, time_s=float(time_s),
            values=values, units=units, valid=valid, origin="laboratory",
        ))
    return tuple(frames), invalid


def build_wds_signal_mapping(
    archive: WdsEventArchive,
    *,
    member: str,
    stream_id: str,
    clock_id: str,
    channels: tuple[WdsSignalChannelMapping, ...],
    mapping_rule: str,
    mapping_evidence_id: str,
    mapping_evidence_sha256: str,
    time_basis: str,
    time_evidence_id: str,
    time_evidence_sha256: str,
) -> WdsSignalMapping:
    """Seal caller-reviewed channel units and a separately sourced clock definition."""
    body = dict(
        schema_version="pipeline_integrity.wds_signal_mapping.v1", member=member,
        stream_id=stream_id, clock_id=clock_id, channels=channels,
        mapping_rule=mapping_rule, mapping_evidence_id=mapping_evidence_id,
        mapping_evidence_sha256=mapping_evidence_sha256, time_basis=time_basis,
        time_evidence_id=time_evidence_id, time_evidence_sha256=time_evidence_sha256,
    )
    initial = WdsSignalMapping(**body, record_digest="pending")
    result = WdsSignalMapping(**body, record_digest=digest(initial.model_dump(mode="json", exclude={"record_digest"})))
    result.check_integrity(archive)
    return result


def build_wds_signal_batch(
    archive: WdsEventArchive,
    selection: WdsEventSelection,
    mapping: WdsSignalMapping,
    time_s_by_excel_row: Mapping[int, float],
) -> WdsSignalBatch:
    """Map reviewed rows to owner SignalFrames using explicit, evidence-bound times."""
    selection.check_integrity(archive)
    mapping.check_integrity(archive)
    if selection.member != mapping.member:
        raise ValueError("selection and signal mapping must reference the same workbook")
    rows_selected = selection.selected_excel_rows
    if set(time_s_by_excel_row) != set(rows_selected):
        raise ValueError("an explicit time value is required for every selected Excel row and no others")
    times = tuple(time_s_by_excel_row[row] for row in rows_selected)
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in times):
        raise ValueError("caller-supplied times must be finite nonnegative seconds")
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("caller-supplied times must strictly increase in source-row order")
    table = next(item for item in archive.tables if item.member == selection.member)
    frames, invalid = _map_wds_signal_frames(table, mapping, rows_selected, times)
    time_digest = digest({"rows": rows_selected, "time_s": times, "time_basis": mapping.time_basis, "evidence_id": mapping.time_evidence_id, "evidence_sha256": mapping.time_evidence_sha256})
    body = dict(
        schema_version="pipeline_integrity.wds_signal_batch.v1", archive_digest=archive.record_digest,
        table_digest=table.table_digest, mapping_digest=mapping.record_digest,
        selection_digest=selection.record_digest, member=table.member,
        event_category=table.category, event_id=table.event_id, excel_row_numbers=rows_selected,
        time_s=times, time_axis_digest=time_digest, frames=frames, invalid_values_by_channel=invalid,
        row_random_split=False, failure_probability_estimated=False,
    )
    initial = WdsSignalBatch(**body, record_digest="pending")
    batch = WdsSignalBatch(**body, record_digest=digest(initial.model_dump(mode="json", exclude={"record_digest"})))
    batch.check_integrity(archive, mapping, selection)
    return batch


def verify_wds_signal_batch(
    batch: WdsSignalBatch,
    archive: WdsEventArchive,
    selection: WdsEventSelection,
    mapping: WdsSignalMapping,
    time_s_by_excel_row: Mapping[int, float],
) -> bool:
    """Rebuild and exact-compare a mapped batch from its pinned source and reviewed inputs."""
    batch.check_integrity(archive, mapping, selection)
    replayed = build_wds_signal_batch(archive, selection, mapping, time_s_by_excel_row)
    if replayed.record_digest != batch.record_digest:
        raise ValueError("WDS SignalFrame batch replay differs from the stored receipt")
    return True

def select_wds_event_rows(
    archive: WdsEventArchive,
    member: str,
    excel_rows: tuple[int, ...],
    *,
    selection_rule: str,
    selection_evidence_id: str,
    selection_evidence_sha256: str,
) -> WdsEventSelection:
    """Bind caller-reviewed row choices without assigning units, time, or failure rates."""
    archive.check_integrity()
    if not selection_rule.strip() or not selection_evidence_id.strip():
        raise ValueError("selection rule and review evidence ID are required")
    if len(selection_evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in selection_evidence_sha256):
        raise ValueError("review evidence artifact SHA-256 is required")
    if len(selection_rule) > 2000 or len(selection_evidence_id) > 256:
        raise ValueError("selection rule or evidence ID exceeds the receipt budget")
    if type(excel_rows) is not tuple:
        raise ValueError("Excel row references must be supplied as a tuple")
    table = next((item for item in archive.tables if item.member == member), None)
    if table is None:
        raise ValueError("selected workbook is not in the pinned WDS archive")
    if not excel_rows or len(excel_rows) != len(set(excel_rows)):
        raise ValueError("one or more unique Excel row numbers are required")
    if any(type(number) is not int for number in excel_rows):
        raise ValueError("Excel row references must be integers")
    selected = tuple(sorted(excel_rows))
    rows = dict(zip(table.excel_row_numbers, table.rows, strict=True))
    if any(number not in rows for number in selected):
        raise ValueError("selected Excel row does not exist in the source workbook")
    body = dict(
        schema_version="pipeline_integrity.wds_event_selection.v1",
        archive_digest=archive.record_digest,
        member=member,
        workbook_sha256=table.workbook_sha256,
        selected_excel_rows=selected,
        selection_rule=selection_rule,
        selection_evidence_id=selection_evidence_id,
        selection_evidence_sha256=selection_evidence_sha256,
        selected_row_digests=tuple(digest({"excel_row": number, "values": rows[number]}) for number in selected),
        units_mapped=False,
        time_axis_mapped=False,
        failure_probability_estimated=False,
    )
    initial = WdsEventSelection(**body, record_digest="pending")
    result = WdsEventSelection(**body, record_digest=digest(initial.model_dump(mode="json", exclude={"record_digest"})))
    result.check_integrity(archive)
    return result


def build_wds_event_evidence_packet(
    archive: WdsEventArchive,
    selection: WdsEventSelection,
    *,
    purpose: Literal["monitoring_replay", "dataset_partition", "risk_evidence_review"],
    consumer_contract: str,
) -> WdsEventEvidencePacket:
    """Build provenance-only adapter metadata without leaking rows or implying calibrated risk."""
    selection.check_integrity(archive)
    if not consumer_contract.strip() or len(consumer_contract) > 256:
        raise ValueError("a bounded downstream consumer contract identifier is required")
    body = dict(
        schema_version="pipeline_integrity.wds_event_evidence_packet.v1",
        selection_digest=selection.record_digest,
        archive_digest=selection.archive_digest,
        workbook_sha256=selection.workbook_sha256,
        member=selection.member,
        selected_excel_rows=selection.selected_excel_rows,
        selection_evidence_id=selection.selection_evidence_id,
        selection_evidence_sha256=selection.selection_evidence_sha256,
        purpose=purpose,
        consumer_contract=consumer_contract,
        raw_rows_included=False,
        units_mapped=False,
        time_axis_mapped=False,
        probability_estimated=False,
    )
    initial = WdsEventEvidencePacket(**body, record_digest="pending")
    packet = WdsEventEvidencePacket(**body, record_digest=digest(initial.model_dump(mode="json", exclude={"record_digest"})))
    packet.check_integrity(archive, selection)
    return packet


def build_wds_event_partition(
    archive: WdsEventArchive,
    group_members: Mapping[str, tuple[str, ...]],
    split_groups: Mapping[Literal["train", "validation", "test"], tuple[str, ...]],
    *,
    grouping_rule: str,
    grouping_evidence_id: str,
    grouping_evidence_sha256: str,
) -> WdsEventPartition:
    """Create a replayable group split while leaving cross-run independence unclaimed."""
    archive.check_integrity()
    if not group_members or not split_groups:
        raise ValueError("explicit group and split assignments are required")
    if any(type(key) is not str or not key.strip() or type(value) is not tuple for key, value in group_members.items()):
        raise ValueError("group IDs and tuple-valued workbook membership must be explicit")
    if any(type(key) is not str or type(value) is not tuple for key, value in split_groups.items()):
        raise ValueError("split names and tuple-valued group assignments must be explicit")
    if not grouping_rule.strip() or not grouping_evidence_id.strip():
        raise ValueError("grouping rule and evidence reference are required")
    if len(grouping_evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in grouping_evidence_sha256):
        raise ValueError("grouping evidence artifact SHA-256 is invalid")
    body = dict(
        schema_version="pipeline_integrity.wds_event_partition.v1",
        archive_digest=archive.record_digest,
        member_sha256={table.member: table.workbook_sha256 for table in archive.tables},
        group_members={key: tuple(value) for key, value in group_members.items()},
        split_groups={key: tuple(value) for key, value in split_groups.items()},
        grouping_rule=grouping_rule,
        grouping_evidence_id=grouping_evidence_id,
        grouping_evidence_sha256=grouping_evidence_sha256,
        row_random_split=False,
        independence_established=False,
    )
    initial = WdsEventPartition(**body, record_digest="pending")
    partition = WdsEventPartition(**body, record_digest=digest(initial.model_dump(mode="json", exclude={"record_digest"})))
    partition.check_integrity(archive)
    return partition


def _file_identity(path: Path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("WDS event archive must be a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _source_hash(path: Path):
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def _reader_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def reviewed_artifact_sha256(path: str | Path) -> str:
    """Hash a regular review artifact so its identity can be retained in a source receipt."""
    artifact = Path(path)
    path_before = artifact.stat(follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode):
        raise ValueError("review evidence must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(artifact, flags)
    except OSError as error:
        raise ValueError("review evidence must be an openable regular non-symlink file") from error
    value = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or (path_before.st_dev, path_before.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("review evidence must be a regular non-symlink file")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("review evidence changed while its identity was read")
    path_after = artifact.stat(follow_symlinks=False)
    if (path_before.st_dev, path_before.st_ino, path_before.st_size, path_before.st_mtime_ns) != (path_after.st_dev, path_after.st_ino, path_after.st_size, path_after.st_mtime_ns):
        raise ValueError("review evidence path changed while its identity was read")
    return value.hexdigest()


def _safe_archive_member(name: str):
    member = PurePosixPath(name)
    if (
        not name
        or member.is_absolute()
        or member.as_posix() != name.rstrip("/")
        or ".." in member.parts
        or "\\" in name
        or ":" in name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise ValueError("unsafe WDS event archive member path")


def _primitive(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite workbook cells are preserved as invalid only by a separate reviewed mapping")
        return value
    raise ValueError(f"unsupported workbook cell type: {type(value).__name__}")


def read_wds_event_archive(path: str | Path) -> WdsEventArchive:
    """Read the nine pinned workbooks in memory; formulas, row order and raw cell units remain unchanged."""
    source = Path(path)
    reader_sha = _reader_sha256()
    before_identity = _file_identity(source)
    if before_identity[2] != ARCHIVE_BYTES:
        raise ValueError("WDS event archive size differs from the pinned publisher file")
    before_hash = _source_hash(source)
    if before_hash != (ARCHIVE_MD5, ARCHIVE_SHA256):
        raise ValueError("WDS event archive checksum differs from the pinned publisher file")

    try:
        import openpyxl
    except ImportError as error:
        raise ImportError("reading WDS event workbooks requires the openpyxl optional dependency") from error

    tables = []
    with ZipFile(source) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate WDS event archive members are ambiguous")
        total_uncompressed = 0
        workbook_names = set()
        for info in infos:
            _safe_archive_member(info.filename)
            mode = info.external_attr >> 16
            # ZIP writers can store permission bits without a Unix file type.
            # Only an explicitly declared non-file/non-directory type is special.
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("WDS event archive contains a link or special file")
            if info.flag_bits & 0x1:
                raise ValueError("encrypted WDS event archive members are unsupported")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
                raise ValueError("WDS event archive exceeds its uncompressed byte budget")
            if info.filename in _WORKBOOKS:
                workbook_names.add(info.filename)
            elif info.is_dir() or info.filename.startswith("__MACOSX/") or info.filename.endswith(".DS_Store"):
                continue
            else:
                raise ValueError("WDS event archive contains an unreviewed member")
        if workbook_names != set(_WORKBOOKS):
            raise ValueError("WDS event archive workbook inventory differs")
        damaged = archive.testzip()
        if damaged is not None:
            raise ValueError("WDS event archive CRC check failed")

        for member, (category, event_id) in _WORKBOOKS.items():
            info = archive.getinfo(member)
            if info.file_size <= 0 or info.file_size > 2 * 1024 * 1024:
                raise ValueError("WDS workbook exceeds the per-member read budget")
            raw = archive.read(info)
            if not raw.startswith(b"PK\x03\x04"):
                raise ValueError("WDS workbook is not an OOXML ZIP package")
            workbook = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=False)
            try:
                if len(workbook.worksheets) != 1:
                    raise ValueError("WDS workbook must contain exactly one reviewed sheet")
                sheet = workbook.worksheets[0]
                iterator = sheet.iter_rows(values_only=False)
                header_cells = next(iterator, None)
                if header_cells is None:
                    raise ValueError("WDS workbook is empty")
                headers = tuple(_primitive(cell.value) for cell in header_cells)
                if headers != _HEADERS:
                    raise ValueError("WDS workbook header differs from the pinned source schema")
                if sheet.max_row is None or sheet.max_row - 1 > _MAX_ROWS:
                    raise ValueError("WDS workbook exceeds the row budget")
                rows, row_numbers, formula_cells = [], [], []
                for excel_row, cells in enumerate(iterator, start=2):
                    row = tuple(_primitive(cell.value) for cell in cells)
                    if len(row) != len(headers):
                        raise ValueError("WDS workbook row width differs from its header")
                    rows.append(row)
                    row_numbers.append(excel_row)
                    for column, cell in enumerate(cells, start=1):
                        if cell.data_type == "f":
                            formula_cells.append((excel_row, column, cell.value))
                body = dict(
                    member=member,
                    category=category,
                    event_id=event_id,
                    sheet_name=sheet.title,
                    workbook_sha256=hashlib.sha256(raw).hexdigest(),
                    headers=headers,
                    rows=tuple(rows),
                    excel_row_numbers=tuple(row_numbers),
                    formula_cells=tuple(formula_cells),
                )
                table = WdsEventTable(**body, table_digest=digest(body))
                tables.append(table)
            finally:
                workbook.close()

    after_identity = _file_identity(source)
    after_hash = _source_hash(source)
    if before_identity != after_identity or before_hash != after_hash or reader_sha != _reader_sha256():
        raise ValueError("WDS event archive or reader code changed during reading")
    record_body = dict(
        schema_version="pipeline_integrity.wds_event_archive.v2",
        archive_bytes=ARCHIVE_BYTES,
        archive_md5=ARCHIVE_MD5,
        archive_sha256=ARCHIVE_SHA256,
        reader_sha256=reader_sha,
        python_version=platform.python_version(),
        openpyxl_version=openpyxl.__version__,
        tables=tuple(tables),
        units_mapped=False,
        time_axis_mapped=False,
        formulas_evaluated=False,
    )
    initial = WdsEventArchive(**record_body, record_digest="pending")
    sealed_body = initial.model_dump(mode="json", exclude={"record_digest"})
    record = WdsEventArchive(**record_body, record_digest=digest(sealed_body))
    record.check_integrity()
    return record


def verify_wds_event_archive(record: WdsEventArchive, path: str | Path) -> bool:
    """Replay source parsing against the original archive and pinned reader environment."""
    if not isinstance(record, WdsEventArchive):
        raise TypeError("a WdsEventArchive receipt is required")
    record.check_integrity()
    replayed = read_wds_event_archive(path)
    if replayed.record_digest != record.record_digest:
        raise ValueError("WDS event source replay differs from the stored receipt")
    return True
