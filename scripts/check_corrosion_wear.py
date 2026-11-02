"""Verify reused loss models against authored scalars and a same-exposure probability identity."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.corrosion_wear import evaluate_corrosion_wear, verify_corrosion_wear_record
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_exchange import prepare_material_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    from pipeline_integrity.adapters import MaterialClient

    client = MaterialClient()
    support_files = [
        args.materials / "LICENSE",
        args.materials / "THIRD_PARTY_NOTICES.md",
        *sorted((args.materials / "src").rglob("*.json")),
    ]
    support_before = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in support_files}
    requests, context = runpy.run_path("examples/corrosion_wear.py")["make_cases"]()
    requests = {
        k: r.model_copy(update={"owner_source_tree_sha256": before["source_tree_sha256"]})
        for k, r in requests.items()
    }
    results, references, refusals = {}, {}, []

    def evaluate(name, request, **kwargs):
        result = evaluate_corrosion_wear(
            request, context, client=client, owner_source_root=args.materials, **kwargs
        )
        verify_corrosion_wear_record(result)
        results[name] = result
        return result

    def changed(request, **parameters):
        return request.model_copy(update={"parameters": request.parameters | parameters})

    def reference(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-25), (name, actual, expected)
        references[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    def refused(name, request):
        try:
            evaluate(name, request)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid loss request accepted: " + name)

    for method, request in requests.items():
        assert evaluate(method, request)["status"] == "computed"
    power, wear, spatial = [
        requests[k]
        for k in ("corrosion.power_law", "degradation.archard_wear", "corrosion.gumbel_area_maximum")
    ]
    reference("penetration_m", results[power.method]["values"]["penetration_m"], 0.0004)
    reference(
        "zero_physical_exposure",
        evaluate("zero_exposure", changed(power, exposure_s=0.0))["values"]["penetration_m"],
        0.0,
    )
    reference("wear_volume_m3", results[wear.method]["values"]["wear_volume_m3"], 1e-11)
    reference("distributed_mean_wear_depth_m", results[wear.method]["values"]["mean_wear_depth_m"], 1e-8)
    for key in ("normal_force_N", "sliding_distance_m", "dimensionless_wear_coefficient"):
        reference(
            "zero_" + key,
            evaluate("zero_" + key, changed(wear, **{key: 0.0}))["values"]["wear_volume_m3"],
            0.0,
        )
    values = results[spatial.method]["values"]
    mu, beta = values["fitted_block_location_m"], values["fitted_block_scale_m"]
    # Independent scalar relations use fitted parameters; fitting and all resampling remain native.
    target = spatial.parameters["target_block_count"]
    q = spatial.parameters["quantile_probability"]
    reference(
        "spatial_quantile_from_fitted_parameters_m",
        values["target_maximum_depth_quantile_m"],
        mu + beta * (math.log(target) - math.log(-math.log(q))),
    )
    block_cdf = math.exp(-math.exp(-(spatial.parameters["depth_threshold_m"] - mu) / beta))
    reference(
        "area_exceedance_from_block_cdf", values["area_depth_exceedance_probability"], 1 - block_cdf**target
    )
    reference("observed_surface_area_m2", values["observed_area_m2"], 0.32)
    reference("target_surface_area_m2", values["target_area_m2"], 0.64)
    assert values["declared_diagnostic_outcome"] == "satisfied"
    assert len(values["depth_quantile_parameter_interval_m"]) == 2
    assert values["parameter_resamples"] == values["gof_monte_carlo_resamples"] == 99
    assert (
        evaluate("constant_maxima", changed(spatial, maximum_depth_m=[0.003] * 32))["status"] == "needs_data"
    )
    assert (
        evaluate("unverified_blocks", changed(spatial, independent_equivalent_blocks=False))["status"]
        == "needs_data"
    )
    assert (
        evaluate("area_outside_support", changed(spatial, target_block_count=81))["status"] == "out_of_scope"
    )
    assert (
        evaluate(
            "negative_tail_diagnostic_not_satisfied",
            changed(spatial, maximum_depth_m=[v - 0.002 for v in spatial.parameters["maximum_depth_m"]]),
        )["status"]
        == "needs_data"
    )
    assert evaluate("outside_physical_age", changed(power, exposure_s=1001.0))["status"] == "out_of_scope"
    assert (
        evaluate("other_origin", power.model_copy(update={"exposure_origin_id": "logger_zero"}))["status"]
        == "out_of_scope"
    )
    assert evaluate("outside_contact_hardness", changed(wear, hardness_Pa=1e7))["status"] == "out_of_scope"
    for method, request in requests.items():
        assert (
            evaluate("unreviewed_" + method, request.model_copy(update={"applicability_reviews": {}}))[
                "status"
            ]
            == "needs_data"
        )
        assert (
            evaluate("unknown_" + method, request.model_copy(update={"parameters": None}))["status"]
            == "needs_data"
        )
    refused("zero_reference_time", changed(power, reference_time_s=0.0))
    refused("zero_distributed_area", changed(wear, distributed_area_m2=0.0))
    refused("duplicate_blocks", changed(spatial, block_ids=["same"] * 32))
    refused(
        "negative_block_depth",
        changed(spatial, maximum_depth_m=[-0.1, *spatial.parameters["maximum_depth_m"][1:]]),
    )
    refused("wrong_source_pin", power.model_copy(update={"owner_source_tree_sha256": "0" * 64}))
    job = prepare_material_job(
        power.method,
        power.parameters,
        context,
        location_id=power.location_id,
        evidence_ids=power.evidence_ids,
        client=client,
    )
    assert evaluate("pinned_job_replay", power, pinned_job=job)["values"] == results[power.method]["values"]
    after = revision(args.materials)
    support_after = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in support_files}
    assert before == after and support_before == support_after, "material source or resources changed"
    private = Path("artifacts/corrosion_wear")
    private.mkdir(parents=True, exist_ok=True)
    (private / "native_records.json").write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (private / "independent_references.json").write_text(
        json.dumps(references, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": "pipeline_integrity.corrosion_wear_verification.v1",
        "status": "passed",
        "native_models": {
            name: {
                "model_version": results[name]["native_receipt"]["job"]["model_version"],
                "input_schema_digest": results[name]["native_receipt"]["job"]["input_schema_digest"],
            }
            for name in requests
        },
        "native_case_count": sum(r["native_receipt"] is not None for r in results.values()),
        "case_status_counts": {
            s: sum(r["status"] == s for r in results.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        "independent_reference_count": len(references),
        "maximum_reference_absolute_error": max(r["absolute_error"] for r in references.values()),
        "refusal_count": len(refusals),
        "refusals": refusals,
        "pinned_job_replay_verified": True,
        "zero_and_unknown_are_distinct": True,
        "calibration_extrapolation_withheld": True,
        "spatial_exceedance_is_period_failure_probability": False,
        "experimental_field_validation": False,
        "public_data_calibration_performed": False,
        "parameter_interval_is_prediction_interval": False,
        "limitations": [
            "Independent scalar identities do not independently validate Gumbel fitting, resampling or the physical tail.",
            "Physical exposure origin and contact support require separate evidence; authored inputs are software references.",
            "Equal-area independence, uncensored maxima and unchanged population remain caller reviews, not field qualification.",
        ],
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "external_support_files_before": support_before,
        "external_support_files_after": support_after,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/corrosion_wear.py")),
        "source_guard_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/corrosion_wear.py")),
    }
    Path("research/corrosion_wear_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": len(requests),
                "independent_references": len(references),
                "native_cases": summary["native_case_count"],
                "refusals": len(refusals),
            }
        )
    )


if __name__ == "__main__":
    main()
