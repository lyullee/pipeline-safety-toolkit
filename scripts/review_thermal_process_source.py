"""Read pinned originals and retain only provenance/meaning aggregates publicly."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.thermal_process_data import (
    COMPRESSOR_ID,
    IDS,
    LN2_ID,
    NASA_ID,
    read_thermal_process_source,
    verify_thermal_process_record,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    directory = Path("artifacts/thermal_process_data")
    directory.mkdir(parents=True, exist_ok=True)
    records, checks = {}, {}
    for identity in IDS:
        record = read_thermal_process_source(identity, args.raw_root / identity)
        records[identity] = record
        (directory / (identity + ".json")).write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        checks[identity + ":original_replay"] = verify_thermal_process_record(
            record, source_root=args.raw_root
        )
        checks[identity + ":not_physical_validation"] = (
            record["independent_physical_model_validation"] is False
        )
        checks[identity + ":no_program_execution"] = record["supplier_programs_executed"] is False
        print(json.dumps({"read": identity, "assets": len(record["assets"])}), flush=True)
    comp = next(iter(records[COMPRESSOR_ID]["products"].values()))
    checks["actual_35036_by_25"] = comp["observations"] == 35036 and comp["sensor_columns"] == 25
    checks["actual_clock_no_timezone_invention"] = (
        comp["clock"]["timezone"] is None
        and comp["clock"]["utc_assigned"] is False
        and not comp["chronology_sorted"]
    )
    checks["actual_units_not_guessed"] = all(v["source_unit"] is None for v in comp["channels"].values())
    checks["dcs_not_map_truth"] = (
        comp["model_mapping_status"] == "needs_data"
        and comp["map_points_inferred"] is False
        and comp["native_compressor_calls"] == 0
    )
    curves = records[LN2_ID]["products"]["Uncertainty Data_All curves.xlsx"]
    checks["actual_ln2_89_curves"] = curves["observations"] == 89 and curves["fluid"] == "Nitrogen"
    checks["no_standard_uncertainty_or_independence_invented"] = all(
        v["reported_U_coverage_factor"] is None and v["standard_joint_covariance"] is None
        for v in curves["records"]
    )
    checks["no_nitrogen_to_hydrogen_transfer"] = (
        curves["native_hydrogen_model_calls"] == 0
        and curves["model_mapping_status"] == "out_of_scope_for_hydrogen_only_closures"
    )
    checks["corrected_comparison_columns_not_used"] = (
        records[LN2_ID]["products"]["All boiling curves_after ANSYS.xlsx"][
            "third_party_comparison_columns_excluded"
        ]
        is True
    )
    nasa = records[NASA_ID]["products"]["19670028965.pdf"]
    checks["actual_original_50page_report"] = nasa["source_pages"] == 50
    checks["ocr_not_validated_numbers"] = (
        nasa["numeric_table_cells_qualified"] is False and nasa["figure_digitization_performed"] is False
    )
    checks["local_sha_not_supplier_verification"] = all(
        a["supplier_checksum_verified"] is False and a["checksum_origin"] == "previous_acquisition"
        for a in records[NASA_ID]["assets"]
    )
    checks["zenodo_supplier_checksums"] = all(
        a["supplier_checksum_verified"] is True
        for identity in (LN2_ID, COMPRESSOR_ID)
        for a in records[identity]["assets"]
    )
    mutations = 0
    for what in ("unit", "fluid", "coverage", "clock", "origin"):
        changed = deepcopy(records[LN2_ID])
        product = changed["products"]["Uncertainty Data_All curves.xlsx"]
        if what == "unit":
            product["records"][0]["heat_flux_W_m2"] *= 10000
        elif what == "fluid":
            product["fluid"] = "Hydrogen"
        elif what == "coverage":
            product["records"][0]["reported_U_coverage_factor"] = 2.0
        elif what == "clock":
            product["records"][0]["timestamp_utc"] = "invented"
        else:
            changed["assets"][0]["dataset_origin"] = "simulation"
        changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
        try:
            verify_thermal_process_record(changed, source_root=args.raw_root)
        except ValueError:
            refused = True
            mutations += 1
        else:
            refused = False
        checks["rehashed_refusal:" + what] = refused
    assets = [a for r in records.values() for a in r["assets"]]
    result = {
        "schema": "pipeline_integrity.thermal_process_source_review.v1",
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "acquired_groups": len(records),
        "acquired_files": len(assets),
        "acquired_bytes": sum(a["bytes"] for a in assets),
        "assets": assets,
        "source_observations": {
            "refinery_chronological_observations": comp["observations"],
            "refinery_sensor_columns": comp["sensor_columns"],
            "processed_ln2_curve_rows": curves["observations"],
            "ln2_raw_complete_rows_qualified": False,
            "nasa_numeric_cells_qualified": False,
        },
        "source_runtime": {k: r["runtime"] for k, r in records.items()},
        "source_records_sha256": {k: r["record_digest"] for k, r in records.items()},
        "file_sha256": {
            p: file_digest(Path(p))
            for p in (
                "src/pipeline_integrity/thermal_process_data.py",
                "src/pipeline_integrity/soil_data.py",
                "tests/test_thermal_process_data.py",
                "tests/test_source_budgets.py",
                "scripts/review_thermal_process_source.py",
                "docs/THERMAL_PROCESS_DATA_KO.md",
            )
        },
        "rehashed_mutations_refused": mutations,
        "actual_native_equipment_calls": 0,
        "independent_physical_validation": False,
        "source_rows_or_detailed_receipts_in_release": False,
    }
    Path("research/thermal_process_source_results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "passed",
                    "total",
                    "acquired_groups",
                    "acquired_files",
                    "acquired_bytes",
                    "rehashed_mutations_refused",
                )
            }
        )
    )
    if not all(checks.values()):
        raise SystemExit("thermal process source checks failed")


if __name__ == "__main__":
    main()
