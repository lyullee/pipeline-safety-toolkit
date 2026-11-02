"""Read original field workbooks; bind only explicitly supplied reviewed mapping/context."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.field_leak_data import (
    PROFILES,
    FieldLeakMapping,
    bind_field_leak_source,
    read_field_leak_workbook,
)


def bounded_document(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("field mapping/context exceeds the input byte budget")
    return raw.decode("utf-8-sig")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--workbook", choices=tuple(PROFILES))
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.mapping is None) != (args.context is None) or args.mapping is not None and args.workbook is None:
        parser.error("binding requires --workbook, --mapping and --context together")
    root = args.source_root.resolve(strict=True)
    sources = []
    for filename in tuple(PROFILES) if args.workbook is None else (args.workbook,):
        path = root / filename
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("field source leaves the chosen root")
        sources.append(read_field_leak_workbook(path))
    result = {
        "schema": "pipeline_integrity.field_source_review.v1",
        "sources": sources,
        "binding": None,
        "detector_training_or_risk_execution": False,
    }
    if args.mapping is not None:
        mapping = FieldLeakMapping.model_validate_json(bounded_document(args.mapping, 1_000_000))
        context = RunContext.model_validate_json(bounded_document(args.context, 2_000_000))
        result["binding"] = bind_field_leak_source(sources[0], mapping, context, source_root=root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
