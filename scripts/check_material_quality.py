"""Execute the three owner models and independent authored interval/scalar references."""

import argparse
import json
import math
import runpy
import sys
from copy import deepcopy
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_exchange import prepare_material_job
from pipeline_integrity.material_quality import (
    CHEMISTRY,
    IMPACT,
    PITTING,
    evaluate_material_quality,
    verify_material_quality_record,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    from pipeline_integrity.adapters import MaterialClient

    client = MaterialClient()
    resources = [
        args.materials / "LICENSE",
        args.materials / "THIRD_PARTY_NOTICES.md",
        *sorted((args.materials / "src").rglob("*.json")),
    ]
    support_before = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in resources}
    requests, context = runpy.run_path("examples/material_quality.py")["make_cases"]()
    requests = {
        key: row.model_copy(update={"owner_source_tree_sha256": before["source_tree_sha256"]})
        for key, row in requests.items()
    }
    records, refs, refusals = {}, {}, []

    def evaluate(name, request, **kwargs):
        record = evaluate_material_quality(
            request, context, client=client, owner_source_root=args.materials, **kwargs
        )
        verify_material_quality_record(record)
        records[name] = record
        return record

    def ref(name, actual, expected, tolerance=1e-6):
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            assert math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), (
                name,
                actual,
                expected,
            )
            error = abs(actual - expected)
        else:
            assert actual == expected, (name, actual, expected)
            error = 0.0
        refs[name] = dict(actual=actual, expected=expected, absolute_error=error)

    def changed(method, **updates):
        request = requests[method]
        return request.model_copy(update={"parameters": deepcopy(request.parameters) | updates})

    def refuse(name, request, **kwargs):
        try:
            evaluate(name, request, **kwargs)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid material quality request accepted: " + name)

    for method, request in requests.items():
        assert evaluate(method, request)["status"] == "computed"
    chemistry = records[CHEMISTRY]["values"]
    ref("inside_intervals", chemistry["outcome"], "satisfied")
    ref("nested_interval_overlap", chemistry["elements"][0]["interval_overlap_outcome"], "satisfied")
    ref("declared_composition_index", records[PITTING]["values"]["composition_index"], 27.0)
    impact = records[IMPACT]["values"]
    for key, value in (
        ("lower_shelf_energy_J", 5.0),
        ("upper_shelf_energy_J", 105.0),
        ("mid_shelf_temperature_K", 300.0),
        ("logistic_transition_width_K", 10.0),
        ("target_energy_crossing_temperature_K", 300.0),
    ):
        ref(key, impact[key], value)
    ref(
        "fitted_energy_max_abs_error_J",
        max(
            abs(a - b)
            for a, b in zip(
                impact["fitted_energy_J"], requests[IMPACT].parameters["absorbed_energy_J"], strict=True
            )
        ),
        0.0,
    )
    cert = deepcopy(requests[CHEMISTRY].parameters["certificate"])
    verification = deepcopy(requests[CHEMISTRY].parameters["verification"])
    verification["element_intervals"]["Ni"] = None
    ref(
        "missing_assay_is_unknown",
        evaluate("missing_assay", changed(CHEMISTRY, verification=verification))["values"]["outcome"],
        "unknown",
    )
    verification["element_intervals"]["Ni"] = dict(lower=0.0, upper=0.2)
    ref(
        "censored_interval_crossing_limit_is_unknown",
        evaluate("censored_assay", changed(CHEMISTRY, verification=verification))["values"]["outcome"],
        "unknown",
    )
    verification["element_intervals"]["Ni"] = dict(lower=0.4, upper=0.5)
    ref(
        "outside_interval_is_not_satisfied",
        evaluate("outside_assay", changed(CHEMISTRY, verification=verification))["values"]["outcome"],
        "not_satisfied",
    )
    verification = deepcopy(requests[CHEMISTRY].parameters["verification"])
    verification["heat_id"] = "other-heat"
    ref(
        "heat_mismatch_is_not_satisfied",
        evaluate("heat_mismatch", changed(CHEMISTRY, verification=verification))["values"]["outcome"],
        "not_satisfied",
    )
    verification["heat_id"] = None
    verification["designation"] = None
    ref(
        "false_comparison_policy_is_preserved",
        evaluate(
            "explicit_false_policy",
            changed(
                CHEMISTRY,
                verification=verification,
                require_matching_heat_ids=False,
                require_verification_designation=False,
            ),
        )["values"]["outcome"],
        "satisfied",
    )
    zero = evaluate(
        "zero_composition",
        changed(PITTING, chromium_mass_percent=0.0, molybdenum_mass_percent=0.0, nitrogen_mass_percent=0.0),
    )
    ref("zero_is_numeric_index_zero", zero["values"]["composition_index"], 0.0)
    assert (
        evaluate("unknown_nitrogen", changed(PITTING, nitrogen_mass_percent=None))["status"] == "needs_data"
    )
    robust = evaluate("robust_loss", changed(IMPACT, loss="soft_l1"))
    assert robust["status"] == "computed"
    ref("robust_midpoint_K", robust["values"]["mid_shelf_temperature_K"], 300.0, tolerance=1e-5)
    no_target = evaluate("unobserved_target", changed(IMPACT, target_energy_J=1000.0))
    ref("no_target_extrapolation", no_target["values"]["target_energy_crossing_temperature_K"], None)
    temperatures = [290.0 + 2.5 * i for i in range(9)]
    narrow = evaluate(
        "insufficient_shelves",
        changed(
            IMPACT,
            temperature_K=temperatures,
            absorbed_energy_J=[5.0 + 100.0 / (1.0 + math.exp(-(t - 300.0) / 10.0)) for t in temperatures],
        ),
    )
    assert narrow["status"] == "needs_data" and narrow["native_receipt"] is not None
    ref("shelf_coverage_gate", narrow["values"]["shelf_coverage_satisfied"], False)
    assert (
        evaluate(
            "censored_impact", requests[IMPACT].model_copy(update={"impact_observations_complete": False})
        )["status"]
        == "needs_data"
    )
    assert (
        evaluate(
            "outside_temperature_support",
            requests[IMPACT].model_copy(update={"declared_temperature_support_K": (260.0, 340.0)}),
        )["status"]
        == "out_of_scope"
    )
    for method, request in requests.items():
        for label, update in (
            ("unreviewed", {"applicability_reviews": {}}),
            ("unresolved", {"parameters": None}),
        ):
            result = evaluate(label + "_" + method, request.model_copy(update=update))
            assert result["status"] == "needs_data" and result["native_receipt"] is None
    refuse("bool_is_not_measured_nitrogen", changed(PITTING, nitrogen_mass_percent=False))
    refuse("negative_energy", changed(IMPACT, absorbed_energy_J=[-1.0] * 9))
    refuse("duplicate_impact_specimens", changed(IMPACT, observation_ids=["same"] * 9))
    refuse(
        "unknown_temperature_unit",
        requests[IMPACT].model_copy(update={"input_units": {"temperature_K": "C"}}),
    )
    refuse(
        "percent_is_not_fraction",
        requests[CHEMISTRY].model_copy(update={"composition_basis": "mass_percent"}),
    )
    refuse("nonfinite_composition", changed(PITTING, nitrogen_mass_percent=float("nan")))
    refuse("wrong_source_pin", requests[PITTING].model_copy(update={"owner_source_tree_sha256": "0" * 64}))
    cert["physical_scope_id"] = "different-sheet"
    refuse("different_physical_chemistry_scope", changed(CHEMISTRY, certificate=cert))
    job = prepare_material_job(
        PITTING,
        requests[PITTING].parameters,
        context,
        location_id=requests[PITTING].location_id,
        evidence_ids=requests[PITTING].evidence_ids,
        client=client,
    )
    replay = evaluate("pinned_job_replay", requests[PITTING], pinned_job=job)
    assert replay["values"] == records[PITTING]["values"]
    refuse("pinned_job_input_changed", changed(PITTING, nitrogen_mass_percent=0.2), pinned_job=job)
    after = revision(args.materials)
    support_after = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in resources}
    assert before == after and support_before == support_after
    folder = Path("artifacts/material_quality")
    save(folder / "native_records.json", records)
    save(folder / "independent_references.json", refs)
    summary = dict(
        schema="pipeline_integrity.material_quality_verification.v1",
        status="passed",
        native_models={
            m: dict(
                model_version=records[m]["native_receipt"]["job"]["model_version"],
                input_schema_digest=records[m]["native_receipt"]["job"]["input_schema_digest"],
            )
            for m in requests
        },
        native_case_count=sum(row["native_receipt"] is not None for row in records.values()),
        case_status_counts={
            s: sum(row["status"] == s for row in records.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        independent_reference_count=len(refs),
        maximum_reference_absolute_error=max(row["absolute_error"] for row in refs.values()),
        refusals=refusals,
        refusal_count=len(refusals),
        pinned_job_replay_verified=True,
        no_target_temperature_extrapolation=True,
        missing_and_censored_assays_preserved=True,
        zero_none_false_nan_are_distinct=True,
        owner_fit_algorithm_reimplemented=False,
        external_source_unchanged=True,
        external_material_source_before=before,
        external_material_source_after=after,
        external_support_files_before=support_before,
        external_support_files_after=support_after,
        experimental_field_validation=False,
        public_data_calibration_performed=False,
        detailed_native_receipts_and_observations_in_public_summary=False,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/material_quality.py")),
        source_guard_sha256=file_digest(Path("src/pipeline_integrity/material_source.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/material_quality.py")),
    )
    save(Path("research/material_quality_results.json"), summary)
    print(
        json.dumps(
            dict(
                status="passed",
                models=3,
                native_cases=summary["native_case_count"],
                references=len(refs),
                refusals=len(refusals),
            )
        )
    )


if __name__ == "__main__":
    main()
