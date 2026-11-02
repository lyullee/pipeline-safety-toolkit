"""Actual owner nodal scans against authored flat/plateau reference geometries."""

import argparse
import json
import math
import runpy
import sys
from copy import deepcopy
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import Pressure
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_exchange import prepare_material_job
from pipeline_integrity.profile_capacity import (
    MODEL_ID,
    evaluate_profile_capacity,
    verify_profile_capacity_record,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", required=True, type=Path)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    from pipeline_integrity.adapters import MaterialClient

    client = MaterialClient()
    source_files = [
        args.materials / "LICENSE",
        args.materials / "THIRD_PARTY_NOTICES.md",
        *sorted((args.materials / "src").rglob("*.json")),
    ]
    support_before = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in source_files}
    request, context = runpy.run_path("examples/profile_capacity.py")["make_case"]()
    request = request.model_copy(update={"owner_source_tree_sha256": before["source_tree_sha256"]})
    records, refs, refusals = {}, {}, []

    def evaluate(name, candidate, **kwargs):
        record = evaluate_profile_capacity(
            candidate, context, client=client, owner_source_root=args.materials, **kwargs
        )
        verify_profile_capacity_record(record)
        records[name] = record
        return record

    def changed(**parameters):
        return request.model_copy(update={"parameters": request.parameters | parameters})

    def reference(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0), (name, actual, expected)
        refs[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    def refused(name, candidate):
        try:
            evaluate(name, candidate)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid profile accepted: " + name)

    flat = evaluate("flat", request)
    assert flat["status"] == "computed"
    values = flat["values"]
    reference("flat_reduction", values["capacity_reduction_factor"], 8 / 9)
    reference(
        "flat_pressure_difference_Pa", values["declared_model_pressure_difference_capacity_Pa"], 1e7 * 8 / 9
    )
    reference("flat_critical_projected_area_m2", values["critical_window"]["projected_loss_area_m2"], 0.001)
    reference("flat_scanned_windows", values["scanned_nodal_windows"], 3)
    reference("flat_demand_ratio", flat["demand_comparison"]["demand_to_declared_capacity_ratio"], 0.5625)
    no_loss = evaluate("no_loss", changed(measured_remaining_thickness_m=[0.01] * 3))
    reference("no_loss_reduction", no_loss["values"]["capacity_reduction_factor"], 1.0)
    reference(
        "no_loss_pressure_difference_Pa",
        no_loss["values"]["declared_model_pressure_difference_capacity_Pa"],
        1e7,
    )
    plateau = changed(
        axial_position_m=[0.0, 0.25, 0.75, 1.0], measured_remaining_thickness_m=[0.01, 0.005, 0.005, 0.01]
    ).model_copy(update={"source_point_ids": ("node-0", "node-1", "node-2", "node-3")})
    plateau_values = evaluate("central_plateau", plateau)["values"]
    reference("plateau_reduction", plateau_values["capacity_reduction_factor"], 2 / 3)
    reference(
        "plateau_projected_area_m2", plateau_values["critical_window"]["projected_loss_area_m2"], 0.0025
    )
    reference("plateau_scanned_windows", plateau_values["scanned_nodal_windows"], 6)
    assert (
        plateau_values["critical_window"]["start_index"],
        plateau_values["critical_window"]["end_index"],
    ) == (1, 2)
    above = request.model_copy(update={"demand": Pressure(value=9.0, unit="MPa", reference="differential")})
    above_result = evaluate("demand_above_declared_capacity", above)
    assert (
        above_result["status"] == "computed"
        and not above_result["demand_comparison"]["demand_not_above_declared_model_capacity"]
    )
    assert evaluate("no_demand", request.model_copy(update={"demand": None}))["demand_comparison"] == {
        "status": "not_requested"
    }
    assert evaluate("incomplete_extent", changed(full_axial_extent_confirmed=False))["status"] == "needs_data"
    assert (
        evaluate("zero_ligament", changed(measured_remaining_thickness_m=[0.008, 0.0, 0.008]))["status"]
        == "out_of_scope"
    )
    assert (
        evaluate("thicker_than_baseline", changed(measured_remaining_thickness_m=[0.008, 0.011, 0.008]))[
            "status"
        ]
        == "needs_data"
    )
    assert (
        evaluate("unsupported_depth", changed(maximum_supported_depth_fraction=0.1))["status"]
        == "out_of_scope"
    )
    provider = deepcopy(request.parameters["bulging_table"])
    provider["window_length_m"] = [0.0, 0.2]
    assert evaluate("unsupported_window_length", changed(bulging_table=provider))["status"] == "out_of_scope"
    reference_provider = request.parameters["reference_capacity"] | {"geometry_id": "other_geometry"}
    assert (
        evaluate("provider_geometry_differs", changed(reference_capacity=reference_provider))["status"]
        == "needs_data"
    )
    assert (
        evaluate("unknown_point", changed(measured_remaining_thickness_m=[0.008, None, 0.008]))["status"]
        == "needs_data"
    )
    assert (
        evaluate("unreviewed", request.model_copy(update={"applicability_reviews": {}}))["status"]
        == "needs_data"
    )
    refused("nonmonotonic_positions", changed(axial_position_m=[0.0, 1.0, 0.5]))
    refused("negative_thickness", changed(measured_remaining_thickness_m=[0.008, -0.001, 0.008]))
    refused(
        "below_unit_bulging",
        changed(bulging_table=request.parameters["bulging_table"] | {"factor": [0.9, 1.0]}),
    )
    refused("wrong_source_pin", request.model_copy(update={"owner_source_tree_sha256": "0" * 64}))
    job = prepare_material_job(
        MODEL_ID,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    assert evaluate("pinned_job_replay", request, pinned_job=job)["values"] == values
    after = revision(args.materials)
    support_after = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in source_files}
    assert before == after and support_before == support_after
    private = Path("artifacts/profile_capacity")
    private.mkdir(parents=True, exist_ok=True)
    (private / "native_records.json").write_text(
        json.dumps(records, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (private / "independent_references.json").write_text(
        json.dumps(refs, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": "pipeline_integrity.profile_capacity_verification.v1",
        "status": "passed",
        "native_models": {
            MODEL_ID: {
                "model_version": flat["native_receipt"]["job"]["model_version"],
                "input_schema_digest": flat["native_receipt"]["job"]["input_schema_digest"],
            }
        },
        "native_case_count": sum(r["native_receipt"] is not None for r in records.values()),
        "case_status_counts": {
            s: sum(r["status"] == s for r in records.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        "independent_reference_count": len(refs),
        "maximum_reference_absolute_error": max(r["absolute_error"] for r in refs.values()),
        "refusal_count": len(refusals),
        "refusals": refusals,
        "pinned_job_replay_verified": True,
        "zero_ligament_is_zero_burst_capacity": False,
        "thicker_measurement_clipped_to_reference": False,
        "all_nodal_windows_retained_within_declared_provider_support": True,
        "demand_comparison_is_standard_or_design_acceptance": False,
        "public_data_calibration_performed": False,
        "experimental_field_validation": False,
        "limitations": [
            "Hypothetical original reference pressure and constant bulging factors verify arithmetic only; they are not engineering standard factors.",
            "Nodal scans do not establish a continuous-endpoint optimum or account for sizing/detection uncertainty, circumferential extent or crack-like features.",
            "Capacity and demand are internal-minus-external pressure differences, not absolute process pressure, MAWP, lifetime or failure probability.",
        ],
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "external_support_files_before": support_before,
        "external_support_files_after": support_after,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/profile_capacity.py")),
        "source_guard_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/profile_capacity.py")),
    }
    Path("research/profile_capacity_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": 1,
                "native_cases": summary["native_case_count"],
                "independent_references": len(refs),
                "refusals": len(refusals),
            }
        )
    )


if __name__ == "__main__":
    main()
