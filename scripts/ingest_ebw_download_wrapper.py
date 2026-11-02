"""Create a provenance-only receipt from the pinned EBW download container."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import file_digest  # noqa: E402
from pipeline_integrity.ebw_data import load_ebw_thermal  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/raw/ebw_thermal/Dataset on thermal cycles and cooling times during.zip"),
    )
    parser.add_argument("--output", type=Path, default=Path("research/ebw_download_wrapper_status.json"))
    args = parser.parse_args()
    source = load_ebw_thermal(args.archive)
    wrapper = source["download_container"]
    if wrapper is None:
        raise ValueError("this receipt is reserved for the one-member download-container input")
    body = {
        "schema": "pipeline_integrity.ebw_download_wrapper_ingestion.v1",
        "status": "source_ingested_provenance_only",
        "dataset_id": source["dataset_id"],
        "doi": source["doi"],
        "license_id": source["license_id"],
        "rights_evidence_url": source["identification_metadata_url"],
        "wrapper": wrapper,
        "publisher_asset": {
            "filename": source["source_filename"],
            "bytes": source["source_bytes"],
            "sha256": source["source_sha256"],
            "supplier_checksum": source["supplier_checksum"],
            "publisher_checksum_verified_before_xlsx_open": source[
                "publisher_checksum_verified_before_xlsx_open"
            ],
        },
        "source_record_digest": source["record_digest"],
        "physical_experiment_count": source["physical_experiment_count"],
        "sensor_history_count": source["sensor_history_count"],
        "physical_time_rows": source["physical_time_rows"],
        "sensor_samples": source["sensor_samples"],
        "complete_source_rows_retained": all(
            not record["source_rows_reordered_filled_or_smoothed"] for record in source["records"]
        ),
        "common_absolute_clock_across_experiments_established": source[
            "common_absolute_clock_across_experiments_established"
        ],
        "missing_values_imputed": source["missing_values_imputed"],
        "numerical_models_executed": False,
        "tests_executed": 0,
        "raw_observations_in_receipt": False,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/ebw_data.py")),
        "ingestion_script_sha256": file_digest(Path(__file__)),
    }
    receipt = {**body, "record_digest": digest(body)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "wrapper_bytes": wrapper["wrapper_bytes"],
                "publisher_asset_sha256": source["source_sha256"],
                "experiments": source["physical_experiment_count"],
                "sensor_histories": source["sensor_history_count"],
                "raw_observations_in_receipt": False,
                "numerical_models_executed": False,
                "tests_executed": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
