"""Run four owned inspection models against authored independent numerical references."""

import argparse
import json
import math
import runpy
import sys
from decimal import Decimal, localcontext
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.inspection_statistics import (
    check_inspection_statistics_record,
    evaluate_inspection_statistics,
)
from pipeline_integrity.material_exchange import prepare_material_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument(
        "--source-kind", choices=("reviewed_checkout", "private_pinned_snapshot"), default="reviewed_checkout"
    )
    args = parser.parse_args()
    before = revision(args.materials)
    source_assets = (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "src/matintegrity/catalog_data/sources.json",
        "src/matintegrity/catalog_data/datasets.json",
        "src/matintegrity/catalog_data/capabilities.json",
    )
    source_asset_hashes = {name: file_digest(args.materials / name) for name in source_assets}
    sys.path.insert(0, str((args.materials / "src").resolve()))
    import matintegrity

    requests, context = runpy.run_path("examples/inspection_statistics.py")["make_cases"]()

    def evaluate(request):
        return evaluate_inspection_statistics(
            request,
            context,
            source_root=args.materials,
            expected_source_sha256=before["source_tree_sha256"],
        )

    results = {method: evaluate(request) for method, request in requests.items()}
    assert all(record["status"] == "computed" for record in results.values())
    refs = {}

    def reference(name, actual, expected):
        expected = float(expected)
        assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-14), (name, actual, expected)
        refs[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    detection = requests["inspection.binomial_evidence"]
    values = results[detection.method]["values"]
    reference("all_hits_point_estimate", values["probability_estimate"], 1)
    reference("all_hits_lower_exact_tail", values["one_sided_lower_probability"], 0.05 ** (1 / 10))
    reference("all_hits_upper", values["one_sided_upper_probability"], 1)
    blank = detection.model_copy(
        update={
            "trials": tuple(
                t.model_copy(update={"truth_flawed": False, "detected": False, "flaw_size_m": None})
                for t in detection.trials
            ),
            "declared_detection_size_m": None,
            "parameters": detection.parameters
            | {"positive_responses": 0, "response_kind": "false_call_on_blank"},
        }
    )
    results["verified_blanks_zero_false_calls"] = evaluate(blank)
    values = results["verified_blanks_zero_false_calls"]["values"]
    reference("blank_zero_false_call_estimate", values["probability_estimate"], 0)
    reference("blank_zero_false_call_lower", values["one_sided_lower_probability"], 0)
    reference(
        "blank_zero_false_call_upper_exact_tail", values["one_sided_upper_probability"], 1 - 0.05 ** (1 / 10)
    )

    pod = requests["inspection.hit_miss_pod"]
    values = results[pod.method]["values"]
    # Each size group is exactly on one increasing curve: 2/10, 5/10, 8/10.
    # These closed-form checks do not use a second fit or the owner's solver.
    for index, expected in enumerate((0.8, 0.2, 0.5)):
        reference(f"POD_original_query_order_{index}", values["probability_of_detection"][index], expected)
    reference("POD_midpoint_m", values["logistic_midpoint_size_m"], Decimal("0.002"))
    reference("POD_log_size_scale", values["logistic_log_size_scale"], Decimal("0.5"))
    reference("POD_target_half_size_m", values["target_size_estimate_m"], Decimal("0.002"))
    reference(
        "POD_intercept_covariance",
        values["parameter_covariance_standardized"][0][0],
        Decimal(10) / Decimal(57),
    )
    reference(
        "POD_slope_covariance", values["parameter_covariance_standardized"][1][1], Decimal(5) / Decimal(24)
    )
    assert values["hits"] == values["misses"] == 15
    assert all(
        0 < lower < point < 1
        for lower, point in zip(
            values["pointwise_lower_probability_of_detection"],
            values["probability_of_detection"],
            strict=True,
        )
    )
    separated_trials = tuple(t.model_copy(update={"detected": t.flaw_size_m == 0.004}) for t in pod.trials)
    separated = pod.model_copy(
        update={
            "trials": separated_trials,
            "parameters": pod.parameters | {"detected": [t.detected for t in separated_trials]},
        }
    )
    results["POD_complete_separation"] = evaluate(separated)
    assert results["POD_complete_separation"]["status"] == "needs_data"
    assert results["POD_complete_separation"]["native_receipt"] is not None
    results["POD_unbounded_search_domain"] = evaluate(
        pod.model_copy(
            update={
                "parameters": pod.parameters
                | {"target_probability": 0.999, "maximum_threshold_size_m": 0.004}
            }
        )
    )
    unbounded = results["POD_unbounded_search_domain"]["values"]
    assert unbounded["target_size_upper_confidence_m"] is None
    assert unbounded["upper_confidence_bound_status"] == "not_bounded_within_declared_search_domain"
    assert unbounded["target_size_estimate_extrapolated"]

    tofd = requests["inspection.tofd_depth"]
    values = results[tofd.method]["values"]
    with localcontext() as decimal_context:
        decimal_context.prec = 45
        depth = Decimal("0.000099").sqrt()
        reference("TOFD_depth_from_authored_right_triangle_m", values["tip_depth_m"], depth)
        reference("TOFD_ligament_m", values["remaining_backwall_ligament_m"], Decimal("0.02") - depth)
    zero_delay = tofd.model_copy(
        update={
            "parameters": tofd.parameters | {"delay_from_lateral_wave_s": 0.0},
            "tofd_reference": tofd.tofd_reference.model_copy(update={"diffraction_arrival_s": 5e-6}),
        }
    )
    results["TOFD_zero_delay"] = evaluate(zero_delay)
    reference("TOFD_zero_delay_depth_m", results["TOFD_zero_delay"]["values"]["tip_depth_m"], 0)
    outside = tofd.model_copy(update={"parameters": tofd.parameters | {"wall_thickness_m": 0.005}})
    results["TOFD_beyond_wall"] = evaluate(outside)
    assert results["TOFD_beyond_wall"]["status"] == "out_of_scope"
    assert results["TOFD_beyond_wall"]["values"]["remaining_backwall_ligament_m"] < 0

    grid = requests["geometry.thickness_grid"]
    values = results[grid.method]["values"]
    for key, expected in (
        ("minimum_measured_thickness_m", Decimal("0.004")),
        ("mean_measured_thickness_m", Decimal("0.0075")),
        ("observed_area_m2", Decimal("0.0024")),
        ("missing_area_m2", Decimal("0.0012")),
        ("observed_fraction", Decimal(2) / Decimal(3)),
        ("net_measured_loss_volume_m3", Decimal("0.000006")),
    ):
        reference("grid_" + key, values[key], expected)
    assert values["connected_below_threshold_areas_m2"] == [0.0006, 0.0006]
    results["grid_net_thicker_than_reference"] = evaluate(
        grid.model_copy(update={"parameters": grid.parameters | {"reference_thickness_m": 0.005}})
    )
    reference(
        "grid_negative_net_loss_is_not_clipped",
        results["grid_net_thicker_than_reference"]["values"]["net_measured_loss_volume_m3"],
        Decimal("-0.000006"),
    )
    results["grid_all_unknown"] = evaluate(
        grid.model_copy(
            update={"parameters": grid.parameters | {"cell_thickness_m": [[None] * 3, [None] * 3]}}
        )
    )
    assert results["grid_all_unknown"]["status"] == "needs_data"
    assert results["grid_all_unknown"]["native_receipt"] is None
    for method in (detection.method, pod.method):
        request = requests[method]
        results[method + "_dependence_unreviewed"] = evaluate(
            request.model_copy(
                update={"parameters": request.parameters | {"independent_trials_declared": False}}
            )
        )
        assert results[method + "_dependence_unreviewed"]["status"] == "needs_data"
    results["actual_data_without_truth_withheld"] = evaluate(pod.model_copy(update={"trials": ()}))
    assert results["actual_data_without_truth_withheld"]["native_receipt"] is None

    job = prepare_material_job(
        pod.method, pod.parameters, context, location_id=pod.location_id, evidence_ids=pod.evidence_ids
    )
    results["POD_pinned_job"] = evaluate_inspection_statistics(
        pod,
        context,
        pinned_job=job,
        source_root=args.materials,
        expected_source_sha256=before["source_tree_sha256"],
    )
    for record in results.values():
        assert check_inspection_statistics_record(record)
        assert record["period_failure_probability"] is record["human_error_probability"] is None
    after = revision(args.materials)
    assert before == after
    assert source_asset_hashes == {name: file_digest(args.materials / name) for name in source_assets}
    private = Path("artifacts/inspection_statistics")
    private.mkdir(parents=True, exist_ok=True)
    records_path = private / "native_records.json"
    records_path.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    schema_checks = {
        method: {
            "version": results[method]["native_receipt"]["job"]["model_version"],
            "input_schema_digest": results[method]["native_receipt"]["job"]["input_schema_digest"],
            "output_units": results[method]["output_units"],
            "probability_semantics": results[method]["probability_semantics"],
        }
        for method in requests
    }
    summary = {
        "schema": "pipeline_integrity.inspection_statistics_verification.v1",
        "status": "passed",
        "native_models": schema_checks,
        "case_count": len(results),
        "case_states": {name: r["status"] for name, r in results.items()},
        "independent_reference_count": len(refs),
        "independent_references": refs,
        "authored_trial_counts": {"POD": 30, "single_size_detection": 10, "verified_blank": 10},
        "raw_or_derived_observation_rows_in_public_summary": False,
        "private_record_sha256": file_digest(records_path),
        "private_record_digest": digest(results),
        "data_rights": {
            "authored_examples": "MIT",
            "external_engine": "matintegrity; separate source ownership and attribution",
            "external_engine_source_redistributed": False,
            "TUBERS_or_ILI_relabelled_as_independent_hit_miss_truth": False,
        },
        "limitations": [
            "Authored synthetic contract and numerical verification, not independent experimental validation.",
            "No POD calibration from TUBERS or ILI: independent detection/truth observations are absent.",
            "One-sided binomial bounds are separate tails; POD confidence is pointwise and asymptotic.",
            "Missing cells, blank trials, clusters, separation and an unbounded search remain explicit.",
            "No acceptance, dead-zone clearance, human-error or period-failure probability is inferred.",
        ],
        "external_material_version": matintegrity.__version__,
        "external_source_execution": {
            "source_kind": args.source_kind,
            "selected_source_licenses_and_package_resources_preserved": True,
            "license_notice_and_package_resource_sha256": source_asset_hashes,
            "source_checkout_path_in_public_summary": False,
        },
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/inspection_statistics.py")),
        "source_guard_implementation_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/inspection_statistics.py")),
    }
    Path("research/inspection_statistics_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": len(requests),
                "cases": len(results),
                "independent_references": len(refs),
                "native_source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
