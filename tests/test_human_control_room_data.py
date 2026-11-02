"""Source reader and privacy boundary for the human control-room experiment."""

from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import Workbook

from pipeline_integrity.contracts import digest
from pipeline_integrity.human_control_room_data import (
    ARCHIVE_BYTES,
    ARCHIVE_MD5,
    ARCHIVE_SHA256,
    EXPECTED_SHEETS,
    WORKBOOK_SHA256,
    HumanControlRoomArchive,
    HumanControlRoomTable,
    _dependency_sha256,
    _reader_sha256,
    _read_workbook,
    read_human_control_room_archive,
    summarize_human_control_room_archive,
)


def _table(name, *, rows=(("P-PRIVATE-SYNTHETIC",),)):
    body = {
        "sheet_name": name,
        "headers": ("Participant",),
        "rows": rows,
        "excel_row_numbers": tuple(range(2, 2 + len(rows))),
        "formula_cells": (),
    }
    return HumanControlRoomTable(**body, table_digest=digest(body))


def _archive(tables=None):
    tables = tuple(tables or (_table(name) for name in EXPECTED_SHEETS))
    body = {
        "archive_bytes": ARCHIVE_BYTES,
        "archive_md5": ARCHIVE_MD5,
        "archive_sha256": ARCHIVE_SHA256,
        "workbook_sha256": WORKBOOK_SHA256,
        "reader_sha256": _reader_sha256(),
        "dependency_sha256": _dependency_sha256(),
        "python_version": "3.12.14",
        "openpyxl_version": "3.1.5",
        "tables": tables,
    }
    record = HumanControlRoomArchive(**body, record_digest="0" * 64)
    return record.model_copy(
        update={
            "record_digest": digest(record.model_dump(mode="json", exclude={"record_digest"}))
        }
    )


def test_synthetic_workbook_reader_preserves_formula_and_excel_row_identity():
    workbook = Workbook()
    first = workbook.active
    first.title = EXPECTED_SHEETS[0]
    first.append(["Participant", "Calculated"])
    first.append(["P-SYNTHETIC", "=1+1"])
    for name in EXPECTED_SHEETS[1:]:
        sheet = workbook.create_sheet(name)
        sheet.append(["Synthetic column"])
    stream = BytesIO()
    workbook.save(stream)

    tables = _read_workbook(stream.getvalue())
    assert tables[0].excel_row_numbers == (2,)
    assert tables[0].rows == (("P-SYNTHETIC", "=1+1"),)
    assert tables[0].formula_cells == ((2, 2, "=1+1"),)


def test_public_summary_never_contains_participant_values_or_headers():
    record = _archive()
    summary = summarize_human_control_room_archive(record)
    serialized = str(summary)
    assert "P-PRIVATE-SYNTHETIC" not in serialized
    assert "Participant" not in serialized
    assert summary["participant_level_rows_included"] is False
    assert summary["header_text_included"] is False


def test_table_receipt_rejects_resealed_row_mutation():
    table = _table("participant_info")
    changed = table.model_copy(update={"rows": (("P-CHANGED",),)})
    with pytest.raises(ValueError, match="table changed"):
        changed.check_integrity()


def test_reader_rejects_archive_that_does_not_match_supplier_pin(tmp_path):
    source = tmp_path / "dataset-v1.1.2.zip"
    source.write_bytes(b"synthetic unpinned input")
    with pytest.raises(ValueError, match="byte size or archive budget differs|checksum differs"):
        read_human_control_room_archive(source)


def test_summary_replay_checks_archive_integrity_before_export():
    record = _archive()
    changed = deepcopy(record.model_dump(mode="python"))
    changed["units_mapped"] = True
    with pytest.raises(ValueError):
        summarize_human_control_room_archive(HumanControlRoomArchive.model_construct(**changed))
