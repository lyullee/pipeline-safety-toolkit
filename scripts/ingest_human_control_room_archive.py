"""Create a participant-free structure receipt for the pinned human-factors archive."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.human_control_room_data import (
    read_human_control_room_archive,
    summarize_human_control_room_archive,
    verify_human_control_room_archive,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("research/human_control_room_ingestion_status_v3.json")
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite an existing ingestion receipt")
    record = read_human_control_room_archive(args.archive)
    verify_human_control_room_archive(record, args.archive)
    summary = summarize_human_control_room_archive(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "status": "source_replay_verified",
                "sheets": summary["sheet_count"],
                "data_rows": summary["data_row_count"],
                "participant_rows_in_receipt": False,
                "receipt": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
