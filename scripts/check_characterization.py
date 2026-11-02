"""Execute original specimen transformations and cyclic relation through the native owner."""

import argparse
import json
import math
import runpy
import sys
from decimal import Decimal
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.characterization import evaluate_characterization
from pipeline_integrity.datasets import file_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    requests, context = runpy.run_path("examples/characterization.py")["make_cases"]()
    results = {name: evaluate_characterization(request, context) for name, request in requests.items()}
    assert all(r["status"] == "computed" for r in results.values())
    refs = {}

    def reference(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0), (name, actual, expected)
        refs[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    values = {k: r["values"] for k, r in results.items()}
    tensile = values["materials.tensile_point"]
    reference("engineering_stress_Pa", tensile["engineering_stress_Pa"], 200000000.0)
    reference("engineering_strain", tensile["engineering_strain"], 0.1)
    reference("uniform_true_stress_Pa", tensile["true_stress_Pa"], 220000000.0)
    reference("uniform_log_strain", tensile["true_strain"], math.log(1.1))
    ductility = values["materials.ductility_retention"]
    reference("retention_can_exceed_one", ductility["ductility_retention_ratio"], 1.25)
    reference("negative_loss_is_not_clipped", ductility["relative_ductility_loss"], -0.25)
    offset = values["materials.offset_yield"]
    reference("offset_stress_Pa", offset["offset_yield_stress_Pa"], float(Decimal(320) * Decimal(10) ** 6))
    reference("offset_strain", offset["intersection_strain"], float(Decimal(36) / Decimal(10000)))
    assert offset["bracket_indices"] == [2, 3]
    cyclic = values["materials.ramberg_osgood"]
    reference("elastic_amplitude", cyclic["elastic_strain_amplitude"], 0.001)
    reference("plastic_amplitude", cyclic["plastic_strain_amplitude"], float(Decimal(1) / Decimal(5) ** 5))
    reference("total_amplitude", cyclic["total_strain_amplitude"], 0.00132)
    nonuniform = requests["materials.tensile_point"].model_copy(
        update={
            "parameters": requests["materials.tensile_point"].parameters
            | {"uniform_deformation_declared": False}
        }
    )
    results["nonuniform"] = evaluate_characterization(nonuniform, context)
    assert (
        results["nonuniform"]["status"] == "computed"
        and "true_stress_Pa" not in results["nonuniform"]["values"]
    )
    zero = nonuniform.model_copy(
        update={"parameters": nonuniform.parameters | {"force_N": 0.0, "extension_m": 0.0}}
    )
    results["zero"] = evaluate_characterization(zero, context)
    assert (
        results["zero"]["values"]["engineering_stress_Pa"]
        == results["zero"]["values"]["engineering_strain"]
        == 0.0
    )
    elastic = requests["materials.offset_yield"].model_copy(
        update={
            "parameters": {
                "engineering_strain": [0.0, 0.001, 0.002],
                "engineering_stress_Pa": [0.0, 2e8, 4e8],
                "elastic_modulus_Pa": 2e11,
                "offset_strain": 0.002,
            }
        }
    )
    results["no_offset_crossing"] = evaluate_characterization(elastic, context)
    assert results["no_offset_crossing"]["status"] == "needs_data"
    invalid = requests["materials.offset_yield"].model_copy(
        update={
            "parameters": requests["materials.offset_yield"].parameters
            | {"engineering_strain": [0.0, 0.003, 0.001, 0.006]}
        }
    )
    try:
        evaluate_characterization(invalid, context)
    except ValueError:
        pass
    else:
        raise AssertionError("a non-monotonic branch was silently sorted")
    after = revision(args.materials)
    assert before == after, "native material source changed during characterization"
    private = Path("artifacts/characterization")
    private.mkdir(parents=True, exist_ok=True)
    for name, record in results.items():
        (private / f"{name}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    summary = {
        "schema": "pipeline_integrity.characterization_verification.v1",
        "status": "passed",
        "native_models": {name: results[name]["native_receipt"]["job"]["model_version"] for name in requests},
        "independent_reference_count": len(refs),
        "independent_references": refs,
        "nonuniform_true_stress_withheld": True,
        "zero_load_preserved": True,
        "no_crossing_needs_data": True,
        "nonmonotonic_branch_rejected_without_sorting": True,
        "cyclic_parameters_inferred_from_monotonic_data": False,
        "experimental_field_validation": False,
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/characterization.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/characterization.py")),
    }
    Path("research/characterization_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": len(requests),
                "independent_references": len(refs),
                "native_source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
