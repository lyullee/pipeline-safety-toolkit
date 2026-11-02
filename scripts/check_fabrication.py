"""Execute material-owned fabrication models against original scalar references."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.fabrication_exchange import evaluate_fabrication


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    requests, context = runpy.run_path("examples/fabrication_methods.py")["make_cases"]()
    results = {method: evaluate_fabrication(request, context) for method, request in requests.items()}
    assert all(result.status == "computed" for result in results.values())
    references = {}

    def check(name, actual, expected, *, atol=1e-12):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=atol), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    bend = results["fabrication.bend_strain"]
    bolt = results["fabrication.bolt_preload"]
    wall = results["fabrication.thickness_budget"]
    check("authored_bend_kinematic_strain", bend.values["outer_fiber_strain"], 0.1)
    check("authored_torque_factor_force_N", bolt.values["preload_N"], 25000.0)
    check("authored_effective_area_stress_Pa", bolt.values["nominal_tensile_stress_Pa"], 100000000.0)
    for key, expected in {
        "minimum_supplied_thickness_m": 0.009,
        "minimum_formed_thickness_m": 0.0081,
        "nominal_budget_structural_thickness_m": 0.0071,
        "nominal_budget_margin_m": 0.0001,
        "measured_structural_thickness_m": 0.0075,
        "measured_margin_m": 0.0005,
    }.items():
        check("authored_" + key, wall.values[key], expected)
    assert bend.requirement_outcome == "not_satisfied" and bend.status == "computed"
    assert bolt.requirement_outcome == wall.requirement_outcome == "satisfied"
    bolt_request = requests["fabrication.bolt_preload"]
    zero = evaluate_fabrication(
        bolt_request.model_copy(update={"parameters": bolt_request.parameters | {"torque_N_m": 0.0}}), context
    )
    check("zero_torque_force_N", zero.values["preload_N"], 0.0)
    check("zero_torque_stress_Pa", zero.values["nominal_tensile_stress_Pa"], 0.0)
    assert zero.status == "computed" and zero.requirement_outcome == "not_satisfied"
    bend_request = requests["fabrication.bend_strain"]
    boundary = evaluate_fabrication(
        bend_request.model_copy(
            update={"parameters": bend_request.parameters | {"centerline_bend_radius_m": 0.06}}
        ),
        context,
    )
    assert boundary.status == "out_of_scope" and boundary.requirement_outcome == "unknown"
    wall_request = requests["fabrication.thickness_budget"]
    exhausted = evaluate_fabrication(
        wall_request.model_copy(
            update={"parameters": wall_request.parameters | {"manufacturing_minus_tolerance_m": 0.01}}
        ),
        context,
    )
    assert exhausted.status == "out_of_scope"
    no_measurement = evaluate_fabrication(
        wall_request.model_copy(
            update={"parameters": wall_request.parameters | {"measured_postfabrication_thickness_m": None}}
        ),
        context,
    )
    assert no_measurement.status == "computed" and no_measurement.values["measured_margin_m"] is None
    unknowns = {}
    for name, changes in {
        "all_parameters": {"parameters": None},
        "unreviewed_method": {"applicability_reviews": {}},
        "unreviewed_nut_factor": {"parameters": bolt_request.parameters | {"nut_factor": None}},
        "unreviewed_effective_area": {"parameters": bolt_request.parameters | {"tensile_area_m2": None}},
    }.items():
        result = evaluate_fabrication(bolt_request.model_copy(update=changes), context)
        assert result.status == "needs_data" and result.native_receipt is None
        unknowns[name] = result.status
    for result in results.values():
        result.check_integrity()
        native_context = result.native_receipt.native.payload["context"]
        assert native_context["asset_id"] == result.request.asset_id
        assert native_context["location_id"] == result.request.location_id
        assert native_context["time_basis"] == context.clock_id
        assert native_context["metadata"]["pipeline_input_validation_mode"] == "json"
    repeated = evaluate_fabrication(bolt_request, context, pinned_job=bolt.native_receipt.job)
    assert repeated.values == bolt.values
    try:
        evaluate_fabrication(
            bolt_request,
            context,
            pinned_job=bolt.native_receipt.job.model_copy(update={"input_schema_digest": "0" * 64}),
        )
    except ValueError as error:
        assert "changed" in str(error)
    else:
        raise AssertionError("the reviewed native schema pin changed without rejection")
    try:
        evaluate_fabrication(
            bolt_request.model_copy(update={"parameters": bolt_request.parameters | {"torque_N_m": True}}),
            context,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("a Boolean was silently accepted as a torque")
    after = revision(args.materials)
    assert before == after, "material-owner source changed during verification"
    summary = {
        "schema": "pipeline_integrity.fabrication_verification.v1",
        "passed": True,
        "native_owner": before,
        "native_sources_unchanged": True,
        "native_models": {
            method: dict(
                model_version=result.native_receipt.job.model_version,
                input_schema_digest=result.native_receipt.job.input_schema_digest,
                status=result.status,
                values=result.values,
                output_units=result.output_units,
                requirement_outcome=result.requirement_outcome,
                receipt_digest=result.native_receipt.result_digest,
                result_digest=result.result_digest,
            )
            for method, result in results.items()
        },
        "independent_authored_references": references,
        "zero_torque_status": zero.status,
        "zero_torque_requirement_outcome": zero.requirement_outcome,
        "bend_support_boundary_status": boundary.status,
        "exhausted_wall_status": exhausted.status,
        "optional_measurement_absence_preserved": True,
        "measured_wall_manufacturing_and_forming_losses_not_subtracted_twice": True,
        "unknown_cases": unknowns,
        "strict_json_boolean_rejected": True,
        "schema_pin_change_rejected": True,
        "native_context_preserved": True,
        "calculation_and_requirement_outcomes_separate": True,
        "pressure_capacity_leak_tightness_human_error_or_code_acceptance_established": False,
        "field_validation": False,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/fabrication_exchange.py")),
        "example_sha256": file_digest(Path("examples/fabrication_methods.py")),
        "script_sha256": file_digest(Path(__file__)),
        "test_sha256": file_digest(Path("tests/test_fabrication_exchange.py")),
    }
    Path("research/fabrication_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "native_models": list(results),
                "independent_references": len(references),
                "native_sources_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
