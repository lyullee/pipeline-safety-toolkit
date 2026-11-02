"""Actual publisher geometry application; reported labels are comparisons, not validation truth."""

import argparse
import json
import math
import sys
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, digest
from pipeline_integrity.copper_hardness import (
    CopperGeometryReview,
    evaluate_copper_indentation,
    load_copper_hardness,
)
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.hardness_exchange import verify_hardness_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--archive", type=Path, default=Path("data/raw/copper_mechanical/Files.zip"))
    parser.add_argument("--output", type=Path, default=Path("research/copper_hardness_results.json"))
    args = parser.parse_args()
    before, archive_before = revision(args.materials), file_digest(args.archive)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.materials / "src").resolve()))
    source = load_copper_hardness(args.archive)
    source_before = digest(source)
    records, errors, comparisons = [], [], {"testing.brinell": [], "testing.vickers": []}
    row_order, forces, counts = [], {}, Counter()
    for test in source["tests"]:
        counts[test["method"]] += len(test["rows"])
        forces.setdefault(test["method"], set()).add(test["metadata"]["Prüfkraft F"]["value"])
        for row in test["rows"]:
            review = CopperGeometryReview(
                expected_test_digest=test["record_digest"],
                expected_row_digest=row["record_digest"],
                units_and_column_mapping_reviewed=True,
                force_dimensions_dwell_and_method_reviewed=True,
                geometry_model_reviewed=True,
                diagnostic_limitations_reviewed=True,
                declared_material_condition_id="assumed_only_for_indentation_geometry",
                declared_surface_condition_id="assumed_only_for_indentation_geometry",
                assumption_evidence=EvidenceRef(
                    id="authored_geometry_assumptions",
                    origin="synthetic",
                    citation="Explicit numerical geometry scenario; source NN surface and unknown material state remain unresolved",
                    license_id="MIT",
                    limitations=("not field calibration, test qualification or acceptance",),
                ),
            )
            result = evaluate_copper_indentation(test, row["row_id"], review)
            original, diagnostic = (
                result["source_condition_result"],
                result["declared_assumptions_geometry_diagnostic"],
            )
            assert original["status"] == "needs_data" and original["native_receipt"] is None
            assert diagnostic["status"] == "computed", diagnostic["issues"]
            verify_hardness_record(original)
            verify_hardness_record(diagnostic)
            p, actual = diagnostic["request"]["parameters"], diagnostic["values"]
            if test["method"] == "testing.brinell":
                with localcontext() as ctx:
                    ctx.prec = 60
                    radius = Decimal(str(p["ball_diameter_m"])) / 2
                    half = Decimal(str(p["mean_indentation_diameter_m"])) / 2
                    height = radius - (radius * radius - half * half).sqrt()
                    area = 2 * Decimal(str(math.pi)) * radius * height
                    reference = float(Decimal(str(p["test_force_N"])) / area)
                conventional = "conventional_HBW"
            else:
                diagonal = (p["first_diagonal_m"] + p["second_diagonal_m"]) / 2
                reference = p["test_force_N"] / (diagonal * diagonal / (2 * math.cos(math.radians(22))))
                conventional = "conventional_HV"
            for key, expected in (
                ("indentation_hardness_Pa", reference),
                (conventional, reference / 9.80665e6),
            ):
                assert math.isclose(actual[key], expected, rel_tol=1e-12)
                errors.append(abs(actual[key] - expected) / abs(expected))
            report = row["cells"]["reported_hardness"]
            if report["state"] == "observed":
                difference = actual[conventional] - report["value"]
                comparisons[test["method"]].append(
                    dict(difference=difference, relative_difference=difference / report["value"])
                )
            row_order.append(row["row_id"])
            records.append(result)
    after = revision(args.materials)
    assert before == after and archive_before == file_digest(args.archive)
    assert source_before == digest(source)
    assert row_order == [row["row_id"] for test in source["tests"] for row in test["rows"]]
    assert len(set(row_order)) == len(row_order)
    assert counts == {"testing.brinell": 61, "testing.vickers": 66} and len(source["tests"]) == 23
    assert all(
        t["metadata"]["Probe Vorbereitung"]["state"] == "unresolved_placeholder" for t in source["tests"]
    )
    artifacts = Path("artifacts/copper_hardness")
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "source_records.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (artifacts / "application_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = dict(
        schema="pipeline_integrity.copper_hardness_verification.v1",
        status="passed",
        source=source["provenance"],
        source_doi="10.5281/zenodo.10820299",
        primary_workbook_count=len(source["tests"]),
        source_indentation_count=sum(counts.values()),
        indentation_count_by_method=dict(counts),
        source_condition_needs_data_count=len(records),
        declared_assumptions_geometry_computed_count=len(records),
        independent_arithmetic_reference_count=len(errors),
        maximum_relative_arithmetic_error=max(errors),
        source_preparation_NN_report_count=len(source["tests"]),
        actual_material_condition_unresolved=True,
        source_summary_method_label_conflict_count=sum(
            t["summary_method_label_conflict"] for t in source["tests"]
        ),
        explicit_source_force_N_by_method={m: sorted(v) for m, v in forces.items()},
        provider_report_comparison={
            method: dict(
                count=len(values),
                maximum_absolute_difference=max(abs(v["difference"]) for v in values),
                mean_absolute_difference=sum(abs(v["difference"]) for v in values) / len(values),
                maximum_absolute_relative_difference=max(abs(v["relative_difference"]) for v in values),
                acceptance_threshold_applied=False,
                independent_validation=False,
            )
            for method, values in comparisons.items()
        },
        source_row_order_preserved=True,
        source_records_unchanged_during_application=True,
        source_rows_deleted_or_imputed=False,
        original_NN_replaced=False,
        same_specimen_labels_prove_cross_method_pairing=False,
        field_calibration_or_standard_qualification=False,
        strength_brittleness_or_failure_probability_inferred=False,
        workbook_sha256={t["member"]: t["workbook_sha256"] for t in source["tests"]},
        source_record_digest=source["record_digest"],
        application_record_digests=[r["record_digest"] for r in records],
        original_receipts_retained_in="artifacts/copper_hardness/application_records.json",
        external_material_source_before=before,
        external_material_source_after=after,
        external_source_unchanged=True,
        source_archive_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/copper_hardness.py")),
        hardness_facade_sha256=file_digest(Path("src/pipeline_integrity/hardness_exchange.py")),
        source_archive_helper_sha256=file_digest(Path("src/pipeline_integrity/source_archive.py")),
        script_sha256=file_digest(Path(__file__)),
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "primary_workbook_count",
                    "source_indentation_count",
                    "source_condition_needs_data_count",
                    "declared_assumptions_geometry_computed_count",
                    "independent_arithmetic_reference_count",
                    "maximum_relative_arithmetic_error",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
