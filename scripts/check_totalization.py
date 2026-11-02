"""Reproduce observed-window quantity/covariance and optional actual counter-period mapping."""

import argparse
import json
import math
import runpy
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.totalization import (
    totalize_measurements,
    verify_totalization_record,
)

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path)
    args = parser.parse_args()
    make = runpy.run_path(str(ROOT / "examples/totalization.py"))["make_case"]
    private = ROOT / "artifacts/totalization"
    checks, records = {}, []
    for meaning, amount, variance in (
        ("point_linear", 10.0, 0.0374),
        ("interval_mean", 10.0, 0.0524),
        ("cumulative_counter", 5.0, 0.02),
    ):
        request, context = make(meaning=meaning, reviewed=True)
        record = totalize_measurements(request, context)
        checks[f"{meaning}:nominal"] = (
            record["status"] == "computed" and record["values"]["complete_net_amount"] == amount
        )
        checks[f"{meaning}:joint_variance"] = math.isclose(
            record["uncertainty"]["output_covariance"][0][0], variance, rel_tol=1e-13
        )
        checks[f"{meaning}:replay"] = verify_totalization_record(record, request, context)["valid"]
        save(private / f"{meaning}_record.json", record)
        records.append(record)
    request, context = make(reviewed=True)
    unknown = totalize_measurements(request.model_copy(update={"covariance": None}), context)
    checks["missing_covariance_preserves_nominal_and_withholds_uncertainty"] = (
        unknown["status"] == "needs_data"
        and unknown["values"]["complete_net_amount"] == 10
        and not unknown["uncertainty"]["outputs"]
    )
    gap = totalize_measurements(request.model_copy(update={"maximum_interval_s": 1.5}), context)
    checks["gap_not_filled"] = (
        gap["status"] == "needs_data"
        and gap["values"]["complete_net_amount"] is None
        and gap["values"]["known_interval_net_amount"] == 2
    )
    refused = 0
    for key in ("quantity", "clock", "covariance", "source", "certification"):
        record = deepcopy(records[0])
        if key == "quantity":
            record["values"]["complete_net_amount"] += 1
        elif key == "clock":
            record["intervals"][0]["start_s"] += 1
        elif key == "covariance":
            record["uncertainty"]["output_covariance"][0][0] *= 2
        elif key == "source":
            record["quadrature_sha256"] = "a" * 64
        else:
            record["custody_transfer_certified"] = True
        record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
        refused += not verify_totalization_record(record)["valid"]
    checks["five_rehashed_mutations_refused"] = refused == 5
    source_application = None
    if args.raw_root:
        from pipeline_integrity.meter_data import read_vsl_calibration
        from pipeline_integrity.totalization_data import vsl_counter_period_cases

        source = read_vsl_calibration(args.raw_root / "vsl_lng_water_calibration")
        cases = vsl_counter_period_cases(source, source_root=args.raw_root)
        nominal = mass = volume = absent = 0
        for i, (request, context) in enumerate(cases):
            record = totalize_measurements(request, context)
            if record["status"] != "needs_data":
                raise AssertionError("unknown source reading/time covariance cannot produce full uncertainty")
            if record["values"] is not None:
                nominal += 1
                mass += request.output_basis.unit == "kg"
                volume += request.output_basis.unit == "m^3"
                expected = request.readings[0].estimate * request.times[-1].estimate
                if (
                    not math.isclose(record["values"]["complete_net_amount"], expected, rel_tol=1e-14)
                    or record["uncertainty"]["outputs"]
                ):
                    raise AssertionError("source interval mean/uncertainty semantics changed")
            else:
                absent += 1
            save(private / "source" / f"counter_period_{i:03d}.json", record)
        source_application = {
            "dataset_id": "vsl_lng_water_calibration",
            "source_sha256": source["provenance"]["sha256"],
            "actual_requests": len(cases),
            "nominal_counter_periods": nominal,
            "mass_periods": mass,
            "local_liquid_volume_periods": volume,
            "missing_periods_not_filled": absent,
            "status_counts": {"needs_data": len(cases)},
            "reading_time_covariance_inferred": False,
            "calendar_clock_invented": False,
            "separate_periods_summed_as_inventory": False,
            "independent_validation_performed": False,
        }
        checks["actual_274_periods_224_nominal_50_unknown"] = (len(cases), nominal, mass, volume, absent) == (
            274,
            224,
            160,
            64,
            50,
        )
    summary = {
        "schema": "pipeline_integrity.totalization_verification.v1",
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "rehashed_corruptions_refused": refused,
        "authored_records": len(records),
        "source_application": source_application,
        "file_sha256": {
            name: file_digest(ROOT / name)
            for name in (
                "src/pipeline_integrity/totalization.py",
                "src/pipeline_integrity/totalization_data.py",
                "src/pipeline_integrity/quadrature.py",
                "src/pipeline_integrity/contracts.py",
                "src/pipeline_integrity/metrology.py",
                "tests/test_totalization.py",
                "examples/totalization.py",
                "scripts/check_totalization.py",
            )
        },
        "missing_intervals_filled": False,
        "field_qualified": False,
        "custody_transfer_certified": False,
    }
    save(ROOT / "research/totalization_results.json", summary)
    if not all(checks.values()):
        raise AssertionError("totalization checks failed")
    print(
        json.dumps(
            {
                "criteria": len(checks),
                "all_passed": True,
                "actual_periods": 0 if source_application is None else source_application["actual_requests"],
            }
        )
    )


if __name__ == "__main__":
    main()
