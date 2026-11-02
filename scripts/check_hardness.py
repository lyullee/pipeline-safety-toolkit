"""Actual indentation-owner checks using independently authored geometry and SI references."""

import argparse
import json
import math
import runpy
import sys
from decimal import Decimal, localcontext
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.hardness_exchange import evaluate_hardness, verify_hardness_record
from pipeline_integrity.units import convert_quantity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("research/hardness_results.json"))
    args = parser.parse_args()
    before = revision(args.materials)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.materials / "src").resolve()))
    cases, context = runpy.run_path("examples/hardness_methods.py")["make_cases"]()
    records, references = {}, {}

    def check(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0.0), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    def evaluate(name, method, **changes):
        request = cases[method]
        result = evaluate_hardness(
            request.model_copy(update={"parameters": request.parameters | changes}), context
        )
        assert result["status"] == "computed", (name, result["issues"])
        verify_hardness_record(result)
        records[name] = result
        return result

    b = evaluate("authored_spherical_indentation", "testing.brinell")
    v = evaluate("authored_diamond_indentation", "testing.vickers")
    # The 3-4-5 section gives cap height 0.5 mm on a 2.5 mm radius sphere.
    spherical_area_m2 = 2 * math.pi * 0.0025 * 0.0005
    brinell_reference = 1000 / spherical_area_m2
    diamond_reference = 98.0665 / ((0.0002**2) / (2 * math.cos(math.radians(22))))
    reference_values = {
        "testing.brinell": dict(
            indentation_hardness_Pa=brinell_reference,
            conventional_HBW=(1000 / 9.80665) / (spherical_area_m2 * 1e6),
        ),
        "testing.vickers": dict(
            indentation_hardness_Pa=diamond_reference,
            conventional_HV=2 * math.cos(math.radians(22)) * 10 / 0.2**2,
        ),
    }
    for method, result in (("testing.brinell", b), ("testing.vickers", v)):
        for key, expected in reference_values[method].items():
            check(method + ":" + key, result["values"][key], expected)
    check("ten_kgf_to_newtons", convert_quantity(10.0, "kgf", "N"), 98.0665)
    check("millimetres_to_metres", convert_quantity(0.2, "mm", "m"), 0.0002)
    for method, request in cases.items():
        force = evaluate(
            method + ":double_force", method, test_force_N=2 * request.parameters["test_force_N"]
        )
        dimensions = {key: 2 * value for key, value in request.parameters.items() if key != "test_force_N"}
        length = evaluate(method + ":double_lengths", method, **dimensions)
        for key, expected in reference_values[method].items():
            check(method + ":force_scaling:" + key, force["values"][key], 2 * expected)
            check(method + ":length_scaling:" + key, length["values"][key], expected / 4)
    swapped = evaluate(
        "swapped_diagonal_labels", "testing.vickers", first_diagonal_m=0.00021, second_diagonal_m=0.00019
    )
    for key, expected in reference_values["testing.vickers"].items():
        check("diagonal_order:" + key, swapped["values"][key], expected)

    small = evaluate(
        "small_spherical_impression_numeric_reference", "testing.brinell", mean_indentation_diameter_m=1e-8
    )
    with localcontext() as precision:
        precision.prec = 70
        radius, half_chord = Decimal("0.0025"), Decimal("0.000000005")
        cap_height = radius - (radius * radius - half_chord * half_chord).sqrt()
        cap_area = 2 * Decimal(str(math.pi)) * radius * cap_height
        small_reference = float(Decimal(1000) / cap_area)
    check("small_cap_decimal_reference_Pa", small["values"]["indentation_hardness_Pa"], small_reference)
    check("small_cap_conventional_HBW", small["values"]["conventional_HBW"], small_reference / 9.80665e6)

    withheld, out_of_scope = {}, {}
    for method, request in cases.items():
        held = evaluate_hardness(request.model_copy(update={"applicability_reviews": {}}), context)
        assert held["status"] == "needs_data" and held["native_receipt"] is None
        verify_hardness_record(held)
        withheld[method + ":unreviewed"] = held["status"]
        outside = evaluate_hardness(
            request.model_copy(
                update={"conditions": request.conditions.model_copy(update={"geometry_applicable": False})}
            ),
            context,
        )
        assert outside["status"] == "out_of_scope" and outside["native_receipt"] is None
        verify_hardness_record(outside)
        out_of_scope[method] = outside["status"]
    request = cases["testing.vickers"]
    for name, changed in {
        "unknown_force": request.model_copy(
            update={"parameters": request.parameters | dict(test_force_N=None)}
        ),
        "nested_unknown": request.model_copy(
            update={"parameters": request.parameters | dict(source=dict(diagonals=[None]))}
        ),
        "unknown_dwell": request.model_copy(
            update={"conditions": request.conditions.model_copy(update={"dwell_s": None})}
        ),
        "unknown_geometry": request.model_copy(
            update={"conditions": request.conditions.model_copy(update={"geometry_applicable": None})}
        ),
        "unknown_material_state": request.model_copy(update={"material_condition_id": None}),
    }.items():
        held = evaluate_hardness(changed, context)
        assert held["status"] == "needs_data" and held["native_receipt"] is None
        verify_hardness_record(held)
        withheld[name] = held["status"]
    invalid = 0
    for method, change in (
        ("testing.brinell", dict(mean_indentation_diameter_m=0.005)),
        ("testing.brinell", dict(mean_indentation_diameter_m=0.006)),
        ("testing.brinell", dict(test_force_N=0.0)),
        ("testing.brinell", dict(ball_diameter_m=0.0)),
        ("testing.vickers", dict(first_diagonal_m=0.0)),
        ("testing.vickers", dict(second_diagonal_m=-0.0002)),
        ("testing.vickers", dict(test_force_N=True)),
        ("testing.vickers", dict(unrecognized_load=1.0)),
    ):
        request = cases[method]
        try:
            evaluate_hardness(request.model_copy(update={"parameters": request.parameters | change}), context)
        except ValueError:
            invalid += 1
        else:
            raise AssertionError((method, change, "invalid native indentation input was accepted"))
    after = revision(args.materials)
    assert before == after, "the separately owned material source changed during verification"
    result = dict(
        schema="pipeline_integrity.hardness_verification.v1",
        status="passed",
        native_models=sorted(cases),
        native_computed_case_count=len(records),
        native_model_versions={
            record["request"]["method"]: record["native_receipt"]["job"]["model_version"]
            for record in records.values()
        },
        independent_reference_count=len(references),
        independent_references=references,
        withheld_cases=withheld,
        reviewed_out_of_scope_cases=out_of_scope,
        invalid_native_inputs_rejected=invalid,
        native_geometry_error_is_not_procedure_acceptance=True,
        conventional_HV_and_HBW_units="kgf/mm^2; reporting convention retained alongside Pa",
        force_and_dimensions_passed_in_SI=True,
        field_indentation_data_used=False,
        nominal_material_properties_or_acceptance_limits_inferred=False,
        strength_brittleness_or_failure_probability_inferred=False,
        welding_hardness_traverse_reimplemented=False,
        original_authored_inputs_not_observations=True,
        source_record_digests={name: record["record_digest"] for name, record in records.items()},
        external_material_source_before=before,
        external_material_source_after=after,
        external_source_unchanged=before == after,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/hardness_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/hardness_methods.py")),
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                status=result["status"],
                native_models=len(cases),
                native_cases=len(records),
                independent_references=len(references),
                withheld_cases=len(withheld),
                out_of_scope_cases=len(out_of_scope),
                invalid_inputs_rejected=invalid,
            )
        )
    )


if __name__ == "__main__":
    main()
