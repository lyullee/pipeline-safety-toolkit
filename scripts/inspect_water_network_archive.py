"""Write a bounded source inventory for the licensed water-network archive."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.water_network_data import inspect_water_network_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, default=Path("research/water_network_archive_inventory.json"))
    args = parser.parse_args()
    result = inspect_water_network_archive(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "dataset_id",
                    "file_count",
                    "csv_count",
                    "pickle_count",
                    "source_hash",
                    "record_digest",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
