"""Exercise the three real owner models, independent references and explicit refusals."""

import argparse
import json
import math
import runpy
import sys
from decimal import Decimal
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue
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
    requests, context = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
    requests = {
        key: request.model_copy(update={"owner_source_tree_sha256": before["source_tree_sha256"]})
        for key, request in requests.items()
    }
    results, references, refusals = {}, {}, {}

    def evaluate(name, request):
        result = evaluate_cyclic_fatigue(
            request,
            context,
            client=client,
            owner_source_root=args.materials,
        )
        results[name] = result
        return result

    def reference(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-12), (name, actual, expected)
        references[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    def changed(request, **parameters):
        return request.model_copy(update={"parameters": request.parameters | parameters})

    def refused(name, request):
        try:
            evaluate(name, request)
        except ValueError:
            refusals[name] = "rejected"
        else:
            raise AssertionError(f"invalid native cyclic input was accepted: {name}")

    for name, request in requests.items():
        assert evaluate(name, request)["status"] == "computed"
    goodman = requests["fatigue.goodman"]
    path = requests["fatigue.hysteresis_path"]
    life = requests["fatigue.strain_life"]
    reference(
        "goodman_tensile_amplitude_Pa",
        results[goodman.method]["values"]["equivalent_fully_reversed_amplitude_Pa"],
        160000000.0,
    )
    reference(
        "goodman_zero_mean_Pa",
        evaluate("zero_mean", changed(goodman, mean_stress_Pa=0.0))["values"][
            "equivalent_fully_reversed_amplitude_Pa"
        ],
        120000000.0,
    )
    reference(
        "goodman_zero_amplitude_Pa",
        evaluate("zero_amplitude", changed(goodman, alternating_stress_Pa=0.0))["values"][
            "equivalent_fully_reversed_amplitude_Pa"
        ],
        0.0,
    )
    for mean in [8e8, 9e8]:
        assert (
            evaluate(f"mean_strength_boundary_{mean:g}", changed(goodman, mean_stress_Pa=mean))["status"]
            == "out_of_scope"
        )
    refused("negative_mean", changed(goodman, mean_stress_Pa=-1.0))
    refused("negative_amplitude", changed(goodman, alternating_stress_Pa=-1.0))
    refused("zero_strength", changed(goodman, ultimate_tensile_strength_Pa=0.0))
    refused("boolean_amplitude", changed(goodman, alternating_stress_Pa=True))
    refused("unexpected_parameter", changed(goodman, invented_correction=1.0))

    square_area = float(Decimal(200000000) * Decimal("0.002"))
    reference(
        "square_recorded_work_J_m3",
        results[path.method]["values"]["signed_recorded_path_work_J_m3"],
        square_area,
    )
    reference(
        "square_closed_work_J_m3",
        results[path.method]["values"]["signed_closed_cycle_work_J_m3"],
        square_area,
    )
    reverse = changed(
        path,
        stress_Pa=list(reversed(path.parameters["stress_Pa"])),
        strain_fraction=list(reversed(path.parameters["strain_fraction"])),
    )
    reference(
        "reversed_signed_work_J_m3",
        evaluate("reverse_order", reverse)["values"]["signed_closed_cycle_work_J_m3"],
        -square_area,
    )
    assert (
        evaluate("undeclared_cycle", changed(path, cycle_boundary_declared=False))["values"][
            "signed_closed_cycle_work_J_m3"
        ]
        is None
    )
    triangle = path.model_copy(
        update={
            "path_sample_ids": ("triangle-0", "triangle-1", "triangle-2"),
            "sample_time_s": None,
            "observation_period_s": None,
            "parameters": path.parameters
            | {
                "stress_Pa": [0.0, 2e8, 2e8],
                "strain_fraction": [0.0, 0.0, 0.002],
                "linear_closure_declared": True,
                "maximum_endpoint_stress_difference_Pa": 2e8,
                "maximum_endpoint_strain_difference": 0.002,
            },
        }
    )
    reference(
        "linear_triangle_closure_J_m3",
        evaluate("explicit_linear_closure", triangle)["values"]["signed_closed_cycle_work_J_m3"],
        square_area / 2,
    )
    assert (
        evaluate("open_path", changed(triangle, linear_closure_declared=False))["values"][
            "signed_closed_cycle_work_J_m3"
        ]
        is None
    )
    assert (
        evaluate(
            "outside_endpoint_tolerance", changed(triangle, maximum_endpoint_stress_difference_Pa=2e8 - 1.0)
        )["values"]["signed_closed_cycle_work_J_m3"]
        is None
    )
    refused("misaligned_path", changed(path, strain_fraction=[0.0, 0.001, 0.0]))
    refused("boolean_closure", changed(path, linear_closure_declared=1))
    unknown = evaluate("unknown_path_row", changed(path, strain_fraction=[0.0, 0.0, None, 0.002, 0.0]))
    assert unknown["status"] == "needs_data" and unknown["native_receipt"] is None

    expected_cycles = float((Decimal("0.005") + Decimal("0.1")) ** 2 / Decimal("0.00105") ** 2 / 2)
    reference(
        "strain_life_closed_form_cycles", results[life.method]["values"]["cycles_to_failure"], expected_cycles
    )
    reference(
        "strain_life_two_reversals",
        results[life.method]["values"]["reversals_to_failure"],
        2 * expected_cycles,
    )
    low_boundary = changed(life, minimum_valid_cycles=0.5, strain_amplitude=0.10500000000000001)
    reference(
        "strain_life_lower_boundary_cycles",
        evaluate("lower_cycle_boundary", low_boundary)["values"]["cycles_to_failure"],
        0.5,
    )
    for name, amplitude in [("above_supported_amplitude", 1.0), ("below_supported_amplitude", 1e-8)]:
        assert evaluate(name, changed(life, strain_amplitude=amplitude))["status"] == "out_of_scope"
    refused("zero_strain_amplitude", changed(life, strain_amplitude=0.0))
    refused("nonnegative_fatigue_exponent", changed(life, fatigue_strength_exponent=0.0))
    refused("reversed_cycle_support", changed(life, minimum_valid_cycles=1e6))
    for name, request in requests.items():
        assert (
            evaluate("unreviewed_" + name, request.model_copy(update={"applicability_reviews": {}}))["status"]
            == "needs_data"
        )
    pinned = prepare_material_job(
        goodman.method,
        goodman.parameters,
        context,
        location_id=goodman.location_id,
        evidence_ids=goodman.evidence_ids,
        client=client,
    )
    pinned_result = evaluate_cyclic_fatigue(
        goodman, context, client=client, pinned_job=pinned, owner_source_root=args.materials
    )
    assert pinned_result["values"] == results[goodman.method]["values"]
    results["pinned_job_replay"] = pinned_result
    refused("wrong_source_pin", goodman.model_copy(update={"owner_source_tree_sha256": "0" * 64}))
    after = revision(args.materials)
    assert before == after, "the native material source changed during cyclic verification"
    private = Path("artifacts/cyclic_fatigue")
    private.mkdir(parents=True, exist_ok=True)
    (private / "native_records.json").write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (private / "independent_references.json").write_text(
        json.dumps(references, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": "pipeline_integrity.cyclic_fatigue_verification.v1",
        "status": "passed",
        "native_models": {
            name: {
                "model_version": results[name]["native_receipt"]["job"]["model_version"],
                "input_schema_digest": results[name]["native_receipt"]["job"]["input_schema_digest"],
            }
            for name in requests
        },
        "native_case_count": sum(record["native_receipt"] is not None for record in results.values()),
        "case_status_counts": {
            status: sum(r["status"] == status for r in results.values())
            for status in ("computed", "needs_data", "out_of_scope")
        },
        "independent_reference_count": len(references),
        "maximum_reference_absolute_error": max(r["absolute_error"] for r in references.values()),
        "refusal_count": len(refusals),
        "pinned_job_replay_verified": True,
        "signed_work_unknown_closure_and_source_order_preserved": True,
        "cycles_and_reversals_distinct": True,
        "experimental_field_validation": False,
        "failure_probability_or_remaining_service_life_inferred": False,
        "public_data_calibration_performed": False,
        "limitations": [
            "Authored arithmetic and root references are software verification only.",
            "Material support and population declarations are caller reviews, not independent qualification.",
            "No mean-stress correction is automatically chained into strain life.",
            "Path energy is neither accumulated fatigue damage nor a failure probability.",
        ],
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "external_source_license_sha256": file_digest(args.materials / "LICENSE"),
        "external_source_notices_sha256": file_digest(args.materials / "THIRD_PARTY_NOTICES.md"),
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/cyclic_fatigue.py")),
        "source_guard_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/cyclic_fatigue.py")),
    }
    Path("research/cyclic_fatigue_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": len(requests),
                "independent_references": len(references),
                "refusals": len(refusals),
                "native_source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
