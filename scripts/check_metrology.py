"""Record original analytical uncertainty checks without distributing observations."""

import argparse
import copy
import json
import math
import runpy
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import (
    CoverageDeclaration,
    MeasurementBasis,
    MeasurementBudgetRequest,
    MeasurementInput,
    MeasurementLinearization,
    MeasurementOutput,
    StandardCovariance,
    propagate_measurement_uncertainty,
    verify_measurement_uncertainty,
)

ROOT = Path(__file__).resolve().parents[1]


def source_reencoding(raw_root, private):
    from pipeline_integrity.meter_data import read_vsl_calibration, verify_meter_source_record

    source_root = raw_root / "vsl_lng_water_calibration"
    source = read_vsl_calibration(source_root)
    if not verify_meter_source_record(source, source_root=raw_root):
        raise ValueError("meter source replay failed")
    evidence = EvidenceRef(
        id="source",
        citation=source["provenance"]["citation"],
        origin="laboratory",
        sha256=source["provenance"]["sha256"],
        license_id="CC-BY-4.0",
        limitations=(
            "Supplier aggregate uncertainty re-encoding; no calibration fit or independent validation",
        ),
    )
    errors, skipped, maximum_error = [], 0, 0.0
    private.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(source["summaries"]):
        if row["error_percent"] is None:
            skipped += 1
            continue
        uncertainty = row["uncertainty"]
        expanded = uncertainty["expanded_percent"] / 100
        factor = float(uncertainty["coverage_factor"])
        nominal = row["error_percent"] / 100
        event = f"summary:{row['source_sheet']}:{row['source_row']}"
        context = RunContext(
            asset_id=row["source_sheet"],
            scenario_id="supplier_scalar_uncertainty_reencoding",
            clock_id="publisher_summary_time_unknown",
            origin="laboratory",
            evidence=(evidence,),
            metadata={
                "meter_source_record_digest": source["record_digest"],
                "source_sheet": row["source_sheet"],
                "source_row": row["source_row"],
                "source_time": None,
                "source_fluid": row["fluid"],
                "coverage_condition": uncertainty["coverage_condition"],
            },
        )
        basis = MeasurementBasis(
            quantity="published_relative_meter_error", unit="1", definition_id=event, evidence_id="source"
        )
        inputs = (
            MeasurementInput(
                id="published_error", estimate=nominal, basis=basis, evidence_id="source", valid=True
            ),
        )
        request = MeasurementBudgetRequest(
            id="supplier_scalar_reencoding",
            asset_id=context.asset_id,
            clock_id=context.clock_id,
            event_id=event,
            time_s=None,
            inputs=inputs,
            linearization=MeasurementLinearization(
                model_id="identity_of_published_scalar",
                model_version="1",
                provider_source_sha256=file_digest(Path(__file__)),
                configuration_sha256=digest(
                    {"source_record_digest": source["record_digest"], "summary_index": index}
                ),
                inputs_sha256=digest(inputs),
                outputs=(MeasurementOutput(id="published_error", estimate=nominal, basis=basis),),
                jacobian=((1.0,),),
                evidence_id="source",
                local_approximation_reviewed=True,
            ),
            covariance=StandardCovariance(
                input_ids=("published_error",),
                values=(((expanded / factor) ** 2,),),
                evidence_id="source",
                definition_reviewed=True,
            ),
            coverage=CoverageDeclaration(factor=factor, evidence_id="source", reviewed=True),
        )
        record = propagate_measurement_uncertainty(request, context)
        if record["status"] != "computed" or not verify_measurement_uncertainty(record):
            raise ValueError("published scalar identity budget failed")
        difference = abs(record["outputs"]["published_error"]["expanded_uncertainty"] - expanded)
        maximum_error = max(maximum_error, difference)
        errors.append(difference <= 1e-14)
        (private / f"vsl_scalar_{index:03d}_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    run = next(r for r in source["runs"] if r["observations_si"]["reference_mass_flow_kg_s"] is not None)
    observed = run["observations_si"]
    reference, indication = observed["reference_mass_flow_kg_s"], observed["indicated_mass_flow_kg_s"]
    context = context.model_copy(
        update={
            "asset_id": run["source_sheet"],
            "scenario_id": "unresolved_component_covariance",
            "metadata": {
                "meter_source_record_digest": source["record_digest"],
                "source_sheet": run["source_sheet"],
                "source_row": run["source_row"],
                "source_time": None,
            },
        }
    )
    flow_basis = MeasurementBasis(
        quantity="mass_flow", unit="kg/s", definition_id="same_liquid_repeat_average", evidence_id="source"
    )
    error_basis = MeasurementBasis(
        quantity="relative_mass_flow_error",
        unit="1",
        definition_id=f"run:{run['source_sheet']}:{run['source_row']}:I_over_R_minus_one",
        evidence_id="source",
    )
    inputs = tuple(
        MeasurementInput(id=name, estimate=value, basis=flow_basis, evidence_id="source", valid=True)
        for name, value in (("reference", reference), ("indication", indication))
    )
    unknown_request = MeasurementBudgetRequest(
        id="actual_unresolved_reference_indication_covariance",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        event_id=f"run:{run['source_row']}",
        time_s=None,
        inputs=inputs,
        linearization=MeasurementLinearization(
            model_id="relative_error",
            model_version="1",
            provider_source_sha256=file_digest(Path(__file__)),
            configuration_sha256=digest({"definition": "I/R-1"}),
            inputs_sha256=digest(inputs),
            outputs=(MeasurementOutput(id="error", estimate=indication / reference - 1, basis=error_basis),),
            jacobian=((-indication / reference**2, 1 / reference),),
            evidence_id="source",
            local_approximation_reviewed=True,
        ),
        covariance=StandardCovariance(
            input_ids=("reference", "indication"),
            values=((None, None), (None, None)),
            evidence_id="source",
            definition_reviewed=True,
        ),
    )
    unknown = propagate_measurement_uncertainty(unknown_request, context)
    if unknown["status"] != "needs_data" or unknown["outputs"] or not verify_measurement_uncertainty(unknown):
        raise ValueError("missing measured component covariance was not withheld")
    (private / "vsl_unresolved_component_record.json").write_text(
        json.dumps(unknown, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "dataset_id": "vsl_lng_water_calibration",
        "source_sha256": source["provenance"]["sha256"],
        "license_id": "CC-BY-4.0",
        "scalar_identity_budgets": len(errors),
        "non_error_pulse_factor_summaries_skipped": skipped,
        "maximum_expanded_uncertainty_reencoding_error": maximum_error,
        "all_scalar_identities_passed": all(errors) and len(errors) == 104 and skipped == 19,
        "actual_component_covariance_status": unknown["status"],
        "component_covariance_inferred": False,
        "source_time_invented": False,
        "independent_validation_performed": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path)
    args = parser.parse_args()
    request, context, result = runpy.run_path(str(ROOT / "examples/metrology.py"))["example"]()
    checks = {}
    output = result["outputs"]["relative_error"]
    expected_variance = 0.11**2 * 0.04 + 0.1**2 * 0.09 - 2 * 0.11 * 0.1 * 0.01
    checks["correlated_relative_error_variance"] = math.isclose(
        output["standard_uncertainty"] ** 2, expected_variance, rel_tol=1e-14
    )
    checks["expanded_interval_overlap_is_indeterminate"] = output["decision"]["status"] == "indeterminate"
    checks["factor_does_not_supply_probability"] = output["supplied_coverage"]["supplied_probability"] is None
    checks["roundtrip_reconstruction"] = verify_measurement_uncertainty(result)
    withheld = request.model_dump(mode="json")
    withheld["covariance"]["values"][0][1] = None
    withheld["covariance"]["values"][1][0] = None
    unknown = propagate_measurement_uncertainty(
        MeasurementBudgetRequest.model_validate_json(json.dumps(withheld)), context
    )
    checks["missing_correlation_is_withheld"] = unknown["status"] == "needs_data" and not unknown["outputs"]
    rejected = 0
    for field in ("standard_uncertainty", "expanded_uncertainty", "estimate", "decision"):
        altered = copy.deepcopy(result)
        if field == "decision":
            altered["outputs"]["relative_error"][field]["status"] = "accepted_by_declared_rule"
        else:
            altered["outputs"]["relative_error"][field] += 0.1
        altered["record_digest"] = digest({k: v for k, v in altered.items() if k != "record_digest"})
        try:
            verify_measurement_uncertainty(altered)
        except ValueError:
            rejected += 1
    checks["four_rehashed_mutations_rejected"] = rejected == 4
    private = ROOT / "artifacts/metrology"
    private.mkdir(parents=True, exist_ok=True)
    for name, record in (("authored_relative_error", result), ("unresolved_correlation", unknown)):
        (private / f"{name}_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    summary = {
        "schema": "pipeline_integrity.metrology_verification.v1",
        "implementation_sha256": result["implementation_sha256"],
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(ROOT / "examples/metrology.py"),
        "runtime": result["runtime"],
        "checks": checks,
        "criterion_count": len(checks),
        "private_record_count": 2,
        "all_passed": all(checks.values()),
        "synthetic_error_estimate": output["estimate"],
        "synthetic_standard_uncertainty": output["standard_uncertainty"],
        "field_validation_performed": False,
        "custody_transfer_certified": False,
        "calibration_fit_performed": False,
    }
    if args.raw_root:
        summary["source_reencoding"] = source_reencoding(args.raw_root, private / "source")
        summary["private_record_count"] += summary["source_reencoding"]["scalar_identity_budgets"] + 1
        summary["all_passed"] &= summary["source_reencoding"]["all_scalar_identities_passed"]
    target = ROOT / "research/metrology_results.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not summary["all_passed"]:
        raise RuntimeError("metrology verification failed")
    print(json.dumps({"checks": len(checks), "all_passed": True}))


if __name__ == "__main__":
    main()
