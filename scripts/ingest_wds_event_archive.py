"""Write a provenance-only receipt for the locally acquired WDSEventDB archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from pipeline_integrity.wds_event_data import read_wds_event_archive, verify_wds_event_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing source-inspection receipt")
    record = read_wds_event_archive(args.archive)
    source_replay_verified = verify_wds_event_archive(record, args.archive)
    categories = Counter()
    formula_cells = 0
    blank_cells = 0
    workbooks = []
    for table in record.tables:
        categories[table.category] += len(table.rows)
        formula_cells += len(table.formula_cells)
        blank_cells += sum(value is None for row in table.rows for value in row)
        workbooks.append(
            {
                "member": table.member,
                "category": table.category,
                "event_id": table.event_id,
                "workbook_sha256": table.workbook_sha256,
                "data_rows": len(table.rows),
                "header_sha256": hashlib.sha256("\x1f".join(table.headers).encode("utf-8")).hexdigest(),
                "formula_cells": len(table.formula_cells),
                "blank_cells": sum(value is None for row in table.rows for value in row),
            }
        )
    summary = {
        "schema": "pipeline_integrity.wds_event_ingestion_summary.v2",
        "archive_bytes": record.archive_bytes,
        "archive_md5": record.archive_md5,
        "archive_sha256": record.archive_sha256,
        "repository": record.repository,
        "repository_commit": record.repository_commit,
        "license_id": record.license_id,
        "reader_sha256": record.reader_sha256,
        "python_version": record.python_version,
        "openpyxl_version": record.openpyxl_version,
        "source_replay_verified": source_replay_verified,
        "workbook_count": len(workbooks),
        "data_row_count": sum(categories.values()),
        "category_rows": dict(sorted(categories.items())),
        "formula_cell_count": formula_cells,
        "blank_cell_count": blank_cells,
        "units_mapped": False,
        "time_axis_mapped": False,
        "formulas_evaluated": False,
        "source_rows_included": False,
        "workbooks": workbooks,
        "record_digest": record.record_digest,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(
        json.dumps(
            {
                "archive_sha256": summary["archive_sha256"],
                "workbook_count": summary["workbook_count"],
                "data_row_count": summary["data_row_count"],
                "formula_cell_count": summary["formula_cell_count"],
                "source_replay_verified": summary["source_replay_verified"],
                "source_rows_included": False,
            }
        )
    )


if __name__ == "__main__":
    main()
