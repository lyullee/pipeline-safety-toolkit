"""Run native material likelihoods and CP-SAT, keeping public geometry separate from planning assumptions."""

import argparse
import inspect
import itertools
import json
import math
import runpy
import sys
from importlib.metadata import version
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.ili import ILILoadRequest, load_ili_run
from pipeline_integrity.inspection import InspectionRun
from pipeline_integrity.inspection_planning import (
    DeclaredInspectionPOD,
    InspectionCampaign,
    InspectionHypothesis,
    InspectionOption,
    InspectionTarget,
    plan_inspections,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--archive", type=Path, default=Path("data/raw/ili/ili-v1.zip"))
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    import matintegrity

    example = runpy.run_path("examples/inspection_campaign.py")
    original, context = example["make_case"]()
    root = Path("artifacts/inspection_planning")
    root.mkdir(parents=True, exist_ok=True)
    cases = {}

    def run(key, campaign=original, context=context):
        result = plan_inspections(campaign, context)
        path = root / f"{key}.json"
        path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        cases[key] = {
            "status": result.status,
            "unrounded_model_score": result.unrounded_model_score,
            "rounded_objective": result.schedule.audit.get("objective") if result.schedule else None,
            "model_score_lower_bound": result.model_score_lower_bound,
            "rounding_error_upper_bound": result.rounding_error_upper_bound,
            "selected_targets": [
                r["operation_id"] for r in result.selected if r["mode_id"] != "not_selected"
            ],
            "issues": result.issues,
            "native_material_calls": len(result.native_material_results),
            "record_sha256": file_digest(path),
        }
        return result

    reference = run("reference")
    assert reference.status == "optimal" and math.isclose(reference.unrounded_model_score, 1.0, abs_tol=1e-14)
    assert set(cases["reference"]["selected_targets"]) == {"A", "B"}
    assert math.isclose(
        run(
            "zero_budget", original.model_copy(update={"budgets": {"campaign_units": 0}})
        ).unrounded_model_score,
        3.0,
    )
    assert math.isclose(
        run(
            "one_budget", original.model_copy(update={"budgets": {"campaign_units": 1}})
        ).unrounded_model_score,
        1.8,
    )
    assert math.isclose(
        run(
            "three_budget", original.model_copy(update={"budgets": {"campaign_units": 3}})
        ).unrounded_model_score,
        0.6,
    )
    run("coarse_rounding", original.model_copy(update={"score_quantum": 0.7}))
    a, b, c = original.targets
    a_half = a.model_copy(
        update={"options": tuple(o.model_copy(update={"coverage_fraction": 0.5}) for o in a.options)}
    )
    half = run(
        "partial_coverage",
        original.model_copy(update={"targets": (a_half, b, c), "budgets": {"campaign_units": 1}}),
    )
    assert cases["partial_coverage"]["selected_targets"] == ["B"] and math.isclose(
        half.unrounded_model_score, 2.2
    )
    mandatory = tuple(t.model_copy(update={"required": True, "options": (t.options[0],)}) for t in (a, b))
    assert (
        run("required_conflicting_appointments", original.model_copy(update={"targets": mandatory})).status
        == "infeasible"
    )
    bad_option = a.options[0].model_copy(update={"sizes_m": {"absent": 0.0, "flaw": 0.02}})
    bad = a.model_copy(update={"options": (bad_option,)})
    assert (
        run("unsupported_detection_size", original.model_copy(update={"targets": (bad,)})).status
        == "needs_data"
    )
    perfect = a.model_copy(
        update={
            "required": True,
            "hypotheses": tuple(
                h.model_copy(update={"prior_mass": 0.0 if h.id == "absent" else 1.0}) for h in a.hypotheses
            ),
            "options": (
                a.options[0].model_copy(
                    update={
                        "detection": DeclaredInspectionPOD(
                            probabilities={"absent": 0.0, "flaw": 1.0}, evidence_id="reference"
                        )
                    }
                ),
            ),
        }
    )
    impossible = run("zero_probability_no_indication", original.model_copy(update={"targets": (perfect,)}))
    assert impossible.unrounded_model_score == 0.0
    assert impossible.option_scores[0]["posterior_given_no_indication"] is None
    assert impossible.native_material_results[-1].status == "out_of_scope"

    archive_sha = file_digest(args.archive)
    cache = json.loads(Path("artifacts/ili_application/scan_1.json").read_text(encoding="utf-8"))
    assert cache["payload_digest"] == digest({k: v for k, v in cache.items() if k != "payload_digest"})
    member = "Dataset (.xlsx)/External Features Year One.xlsx"
    request = ILILoadRequest(
        run_id="ili_label_1",
        asset_id="anonymized_ili_line",
        coordinate_frame_id="joint_start_instrument_clock_label_1",
        source_id="doi:10.17632/c2h2jf5c54.1",
        reported_time_label="1",
        joint_ids=("14",),
        maximum_source_rows=None,
    )
    expected_cache_key = digest(
        {
            "archive": archive_sha,
            "request": request,
            "member": member,
            "loader_code": file_digest(Path(inspect.getfile(load_ili_run))),
            "run_schema": InspectionRun.model_json_schema(),
            "openpyxl": version("openpyxl"),
        }
    )
    assert cache["cache_key"] == expected_cache_key, "regenerate the ILI scan with apply_ili.py"
    source = InspectionRun.model_validate_json(json.dumps(cache["run"]))
    assert source.source_sha256 == archive_sha and source.source_scan_complete
    assert source.inspection_time_s is None and source.clock_id is None
    ili_context = RunContext(
        asset_id=source.asset_id,
        scenario_id="reported_geometry_with_authored_planning_assumptions",
        clock_id="hypothetical_campaign_elapsed",
        origin="user_declared",
        evidence=context.evidence
        + (
            EvidenceRef(
                id="ili",
                citation="Yarveisy, Khan and Abbassi; In Line Inspection Dataset, version 1",
                locator="https://doi.org/10.17632/c2h2jf5c54.1",
                sha256=archive_sha,
                origin="measured",
                license_id="CC-BY-4.0",
                limitations=(
                    "Reported indications have no independent truth/miss set or actual elapsed inspection times.",
                ),
            ),
        ),
    )
    ili_targets = []
    for i, feature in enumerate(source.features):
        ili_targets.append(
            InspectionTarget(
                id=feature.feature_id,
                location_id=feature.feature_id,
                hypotheses=(
                    InspectionHypothesis(
                        id="absent", prior_mass=0.5, unrevealed_weight=0.0, evidence_id="reference"
                    ),
                    InspectionHypothesis(
                        id="flaw", prior_mass=0.5, unrevealed_weight=1.0, evidence_id="reference"
                    ),
                ),
                options=(
                    InspectionOption(
                        id="authored_reinspection",
                        method_id="unqualified_depth_curve",
                        start_s=float(i % 4),
                        duration_s=1.0,
                        coverage_fraction=1.0,
                        sizes_m={"absent": 0.0, "flaw": feature.maximum_depth_m},
                        detection=a.options[0].detection,
                        resources={"crew": 1},
                        budget_use={"campaign_units": 1},
                        evidence_id="reference",
                    ),
                ),
            )
        )
    illustrative = InspectionCampaign(
        horizon_s=8.0,
        targets=tuple(ili_targets),
        resources=original.resources,
        budgets={"campaign_units": 4},
        score_quantum=0.0001,
        criterion_unit="authored_unrevealed_reported_feature_weight",
        evidence_id="reference",
    )
    full_ili = run("ili_all_reported_candidates", illustrative, ili_context)
    assert full_ili.status == "needs_data" and len(full_ili.issues) == 4
    eligible = tuple(
        t
        for t in ili_targets
        if all(
            size == 0
            or a.options[0].detection.supported_minimum_size_m
            <= size
            <= a.options[0].detection.supported_maximum_size_m
            for size in t.options[0].sizes_m.values()
        )
    )
    excluded = [t.id for t in ili_targets if t not in eligible]
    assert len(eligible) == 9 and len(excluded) == 4
    ili_result = run(
        "ili_assumed_curve_domain_subset", illustrative.model_copy(update={"targets": eligible}), ili_context
    )
    assert ili_result.status == "optimal"
    costs = {(r["target_id"], r["option_id"]): r["rounded_cost"] for r in ili_result.option_scores}
    enumerated = []
    for selection in itertools.product([False, True], repeat=len(eligible)):
        if sum(selection) > 4:
            continue
        slots = [t.options[0].start_s for t, chosen in zip(eligible, selection, strict=True) if chosen]
        if len(set(slots)) != len(slots):
            continue
        enumerated.append(
            sum(
                costs[t.id, "authored_reinspection" if chosen else "not_selected"]
                for t, chosen in zip(eligible, selection, strict=True)
            )
        )
    assert min(enumerated) == ili_result.schedule.audit["objective"]
    assert before == revision(args.materials), "the material source project was modified"
    summary = {
        "schema": "pipeline_integrity.inspection_planning_verification.v1",
        "materials": before | {"version": matintegrity.__version__},
        "scheduler_provider": reference.schedule.native.provider,
        "native_model_versions": {
            r.payload["model_id"]: r.payload["model_version"] for r in reference.native_material_results
        },
        "cases": cases,
        "native_model_ids": sorted({r.payload["model_id"] for r in reference.native_material_results}),
        "ili": {
            "source": "https://doi.org/10.17632/c2h2jf5c54.1",
            "license_id": "CC-BY-4.0",
            "creators": ["Rioshar Yarveisy", "Faisal Khan", "Rouzbeh Abbassi"],
            "archive_sha256": archive_sha,
            "scan_cache_key": expected_cache_key,
            "reported_candidates": len(ili_targets),
            "separate_hypothetical_subset_candidates": len(eligible),
            "excluded_from_subset_for_missing_detection_support": excluded,
            "original_data_application_status": "needs_data",
            "detection_curve_qualified": False,
            "priors_resources_appointments_and_weights_are_authored": True,
            "elapsed_time_inferred_from_year_labels": False,
            "misses_or_growth_inferred": False,
            "independently_enumerated_feasible_menus": len(enumerated),
        },
        "module_sha256": file_digest(Path("src/pipeline_integrity/inspection_planning.py")),
        "scheduler_sha256": file_digest(Path("src/pipeline_integrity/scheduling.py")),
        "script_sha256": file_digest(Path(__file__)),
        "field_calibrated": False,
        "physical_failure_probability_calculated": False,
        "work_authorized": False,
    }
    Path("research/inspection_planning_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(cases),
                "ili_candidates": len(ili_targets),
                "enumerated_menus": len(enumerated),
                "original_data_application_status": "needs_data",
                "field_calibrated": False,
            }
        )
    )


if __name__ == "__main__":
    main()
