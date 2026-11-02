"""Read a source-pinned control-room human-factors workbook without inferring HEP."""

from __future__ import annotations

import hashlib
import io
import math
import platform
import stat
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from .contracts import Contract, digest
from .source_archive import pinned_source_zip

ZENODO_RECORD = "https://zenodo.org/records/13984198"
ARCHIVE_BYTES = 116_494
ARCHIVE_MD5 = "22c0303c109ea5f00e4cfe22ca954db0"
ARCHIVE_SHA256 = "b7c317f96354dbfd587656e0e4d4c9fe6f2a6c9d1b0df808b46efe67e4090038"
WORKBOOK_MEMBER = "CISC-LIVE-LAB-3-dataset-03c2e58/concatenated_data_zenodo.xlsx"
WORKBOOK_SHA256 = "1fbc5bea29ae4cb543b90fbbdd214027b3334f354c77210ee0501b07c6e981f7"
EXPECTED_MEMBERS = frozenset(
    {
        "CISC-LIVE-LAB-3-dataset-03c2e58/",
        "CISC-LIVE-LAB-3-dataset-03c2e58/LICENSE",
        "CISC-LIVE-LAB-3-dataset-03c2e58/README.md",
        WORKBOOK_MEMBER,
    }
)
EXPECTED_SHEETS = (
    "participant_info",
    "spam",
    "support_questions",
    "AI_questions",
    "nasa_tlx",
    "sart",
    "indexes",
    "simulator_logs",
    "watch_data",
    "eye_tracking",
    "AI_vs_human",
)
PUBLISHER_REPORTED_PARTICIPANTS = 92
MAXIMUM_WORKBOOK_BYTES = 16 * 1024 * 1024
MAXIMUM_ROWS_PER_SHEET = 100_000
MAXIMUM_CELLS = 2_000_000


class HumanControlRoomTable(Contract):
    """One original workbook sheet, including participant-level values in memory."""

    sheet_name: str
    headers: tuple[str | None, ...]
    rows: tuple[tuple[Any, ...], ...]
    excel_row_numbers: tuple[int, ...]
    formula_cells: tuple[tuple[int, int, str], ...]
    table_digest: str

    def check_integrity(self):
        body = self.model_dump(mode="json", exclude={"table_digest"})
        if digest(body) != self.table_digest:
            raise ValueError("human control-room source table changed")
        if len(self.rows) != len(self.excel_row_numbers):
            raise ValueError("human control-room row identity count differs")
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("human control-room row width differs from its header")
        if tuple(sorted(set(self.excel_row_numbers))) != self.excel_row_numbers:
            raise ValueError("human control-room Excel row numbers must be strictly ordered and unique")
        return True


class HumanControlRoomArchive(Contract):
    """A source-sealed in-memory archive; it contains participant-level data."""

    schema_version: Literal["pipeline_integrity.human_control_room_archive.v1"] = (
        "pipeline_integrity.human_control_room_archive.v1"
    )
    dataset_id: Literal["human_control_room"] = "human_control_room"
    zenodo_record: Literal["https://zenodo.org/records/13984198"] = ZENODO_RECORD
    license_id: Literal["CC-BY-4.0"] = "CC-BY-4.0"
    archive_bytes: int
    archive_md5: str
    archive_sha256: str
    workbook_sha256: str
    workbook_member: Literal[WORKBOOK_MEMBER] = WORKBOOK_MEMBER
    reader_sha256: str
    dependency_sha256: dict[str, str]
    python_version: str
    openpyxl_version: str
    tables: tuple[HumanControlRoomTable, ...]
    units_mapped: Literal[False] = False
    time_axis_mapped: Literal[False] = False
    task_outcomes_mapped: Literal[False] = False
    human_failure_probability_estimated: Literal[False] = False
    participant_level_data_included: Literal[True] = True
    record_digest: str

    def check_integrity(self):
        checked = HumanControlRoomArchive.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != checked.record_digest:
            raise ValueError("human control-room archive receipt changed")
        if checked.archive_bytes != ARCHIVE_BYTES or checked.archive_md5 != ARCHIVE_MD5:
            raise ValueError("human control-room source asset pin differs")
        if checked.archive_sha256 != ARCHIVE_SHA256 or checked.workbook_sha256 != WORKBOOK_SHA256:
            raise ValueError("human control-room source or workbook SHA-256 differs")
        if checked.reader_sha256 != _reader_sha256():
            raise ValueError("human control-room reader source differs from the receipt")
        if checked.dependency_sha256 != _dependency_sha256():
            raise ValueError("human control-room reader dependency source differs from the receipt")
        if tuple(table.sheet_name for table in checked.tables) != EXPECTED_SHEETS:
            raise ValueError("human control-room workbook sheet inventory differs")
        for table in checked.tables:
            table.check_integrity()
        return True


def _reader_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _dependency_sha256() -> dict[str, str]:
    return {
        name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
        for name in ("contracts", "datasets", "source_archive")
    }


def _cell_value(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite workbook values are not supported")
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise ValueError(f"unsupported workbook cell type: {type(value).__name__}")


def _read_workbook(raw: bytes) -> tuple[HumanControlRoomTable, ...]:
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
    except Exception as error:
        raise ValueError("source-pinned workbook could not be opened") from error
    try:
        if tuple(workbook.sheetnames) != EXPECTED_SHEETS:
            raise ValueError("human control-room workbook sheet names or order differ")
        tables = []
        total_cells = 0
        for worksheet in workbook.worksheets:
            if worksheet.max_row is None or worksheet.max_column is None or worksheet.max_row < 1:
                raise ValueError("human control-room workbook contains an empty sheet")
            if worksheet.max_row > MAXIMUM_ROWS_PER_SHEET:
                raise ValueError("human control-room row budget exceeded")
            total_cells += worksheet.max_row * worksheet.max_column
            if total_cells > MAXIMUM_CELLS:
                raise ValueError("human control-room workbook cell budget exceeded")
            raw_rows = worksheet.iter_rows()
            first = next(raw_rows, None)
            if first is None:
                raise ValueError("human control-room workbook sheet has no header row")
            headers = tuple(_cell_value(cell.value) for cell in first)
            if any(value is not None and not isinstance(value, str) for value in headers):
                raise ValueError("human control-room header cells must be text or blank")
            rows, row_numbers, formulas = [], [], []
            for row_number, row in enumerate(raw_rows, start=2):
                values = tuple(_cell_value(cell.value) for cell in row)
                rows.append(values)
                row_numbers.append(row_number)
                formulas.extend(
                    (cell.row, cell.column, cell.value)
                    for cell in row
                    if cell.data_type == "f" and isinstance(cell.value, str)
                )
            body = {
                "sheet_name": worksheet.title,
                "headers": headers,
                "rows": tuple(rows),
                "excel_row_numbers": tuple(row_numbers),
                "formula_cells": tuple(formulas),
            }
            table = HumanControlRoomTable(**body, table_digest=digest(body))
            table.check_integrity()
            tables.append(table)
        return tuple(tables)
    finally:
        workbook.close()


def read_human_control_room_archive(
    path,
    *,
    maximum_archive_bytes=128 * 1024 * 1024,
    maximum_member_bytes=MAXIMUM_WORKBOOK_BYTES,
    maximum_total_bytes=32 * 1024 * 1024,
    maximum_members=16,
):
    """Read a pinned workbook without extracting archive members or exporting rows.

    The returned tables include participant-level values. Public research receipts
    should use :func:`summarize_human_control_room_archive` instead of serializing
    this object.
    """
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("human control-room source must be a regular non-symlink file")
    try:
        with pinned_source_zip(
            source,
            "human_control_room",
            maximum_archive_bytes=maximum_archive_bytes,
            maximum_member_bytes=maximum_member_bytes,
            maximum_total_bytes=maximum_total_bytes,
            maximum_members=maximum_members,
        ) as (archive, metadata):
            names = [member.filename for member in archive.infolist()]
            if len(names) != len(set(names)) or set(names) != EXPECTED_MEMBERS:
                raise ValueError("human control-room archive member inventory differs")
            for member in archive.infolist():
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or member.flag_bits & 1:
                    raise ValueError("human control-room archive contains a link or encrypted member")
            if archive.testzip() is not None:
                raise ValueError("human control-room archive CRC check failed")
            workbook_info = archive.getinfo(WORKBOOK_MEMBER)
            if workbook_info.file_size > MAXIMUM_WORKBOOK_BYTES:
                raise ValueError("human control-room workbook byte budget exceeded")
            workbook_raw = archive.read(WORKBOOK_MEMBER)
            workbook_sha = hashlib.sha256(workbook_raw).hexdigest()
            if workbook_sha != WORKBOOK_SHA256:
                raise ValueError("human control-room workbook SHA-256 differs from the reviewed version")
            tables = _read_workbook(workbook_raw)
    except BadZipFile as error:
        raise ValueError("human control-room source is not a valid ZIP archive") from error

    if metadata["archive_bytes"] != ARCHIVE_BYTES or metadata["archive_sha256"] != ARCHIVE_SHA256:
        raise ValueError("human control-room archive differs from the reviewed Zenodo file")
    body = {
        "archive_bytes": metadata["archive_bytes"],
        "archive_md5": ARCHIVE_MD5,
        "archive_sha256": metadata["archive_sha256"],
        "workbook_sha256": workbook_sha,
        "reader_sha256": _reader_sha256(),
        "dependency_sha256": _dependency_sha256(),
        "python_version": platform.python_version(),
        "openpyxl_version": __import__("openpyxl").__version__,
        "tables": tables,
    }
    record = HumanControlRoomArchive(**body, record_digest="0" * 64)
    record_body = record.model_dump(mode="json", exclude={"record_digest"})
    record = record.model_copy(update={"record_digest": digest(record_body)})
    record.check_integrity()
    return record


def verify_human_control_room_archive(record, path):
    """Replay the pinned archive and require exact participant-table identity."""
    checked = HumanControlRoomArchive.model_validate(record.model_dump(mode="python"))
    checked.check_integrity()
    replayed = read_human_control_room_archive(path)
    if replayed != checked:
        raise ValueError("human control-room source replay differs")
    return True


def summarize_human_control_room_archive(record):
    """Return aggregate workbook structure only; no participant IDs or row values."""
    checked = HumanControlRoomArchive.model_validate(record.model_dump(mode="python"))
    checked.check_integrity()
    sheets = []
    for table in checked.tables:
        participant_index = next(
            (index for index, header in enumerate(table.headers) if header == "Participant"), None
        )
        scenario_index = next((index for index, header in enumerate(table.headers) if header == "Scenario"), None)
        sheets.append(
            {
                "sheet_name": table.sheet_name,
                "data_rows": len(table.rows),
                "column_count": len(table.headers),
                "header_digest": digest(table.headers),
                "formula_cell_count": len(table.formula_cells),
                "blank_cell_count": sum(value is None for row in table.rows for value in row),
                "distinct_participant_count": (
                    len({row[participant_index] for row in table.rows if row[participant_index] is not None})
                    if participant_index is not None
                    else None
                ),
                "distinct_scenario_count": (
                    len({row[scenario_index] for row in table.rows if row[scenario_index] is not None})
                    if scenario_index is not None
                    else None
                ),
            }
        )
    body = {
        "schema": "pipeline_integrity.human_control_room_ingestion.v1",
        "dataset_id": checked.dataset_id,
        "source_record": checked.zenodo_record,
        "license_id": checked.license_id,
        "source_archive_bytes": checked.archive_bytes,
        "source_archive_md5": checked.archive_md5,
        "source_archive_sha256": checked.archive_sha256,
        "workbook_sha256": checked.workbook_sha256,
        "reader_sha256": checked.reader_sha256,
        "dependency_sha256": checked.dependency_sha256,
        "python_version": checked.python_version,
        "openpyxl_version": checked.openpyxl_version,
        "sheet_count": len(sheets),
        "data_row_count": sum(item["data_rows"] for item in sheets),
        "formula_cell_count": sum(item["formula_cell_count"] for item in sheets),
        "publisher_reported_participant_count": PUBLISHER_REPORTED_PARTICIPANTS,
        "participant_count_reconciliation_complete": False,
        "sheets": sheets,
        "participant_level_rows_included": False,
        "participant_identifiers_included": False,
        "header_text_included": False,
        "units_mapped": False,
        "time_axis_mapped": False,
        "task_outcomes_mapped": False,
        "human_failure_probability_estimated": False,
        "pipeline_field_transfer_established": False,
    }
    return {**body, "record_digest": digest(body)}
