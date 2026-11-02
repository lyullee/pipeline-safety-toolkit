"""Replay actual owner APIs, independent small references and rehashed semantic attacks."""

import argparse
import json
import math
import runpy
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.spatial_risk import (
    JointCasualtyState,
    evaluate_spatial_risk,
    native_backend_metadata,
    to_signal_frames,
    verify_spatial_risk_record,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = runpy.run_path(str(ROOT / "examples/spatial_risk.py"))


NATIVE_ATTACKS = (
    "ignition_and_harm_coherent",
    "harm_only",
    "receptor_position",
    "source_clock",
    "presence_already_applied",
    "provider_version",
    "ignition_request_result_changed_case_unchanged",
    "ignition_request_native_digest_changed_case_unchanged",
    "receptor_height_changed_native_facility_digest_stale",
    "receptor_height_changed_rehashed_facility",
    "known_domain_truncation_promoted",
    "native_failed_ok_false_promoted",
    "branch_harm_changed_thermal_harm_unchanged",
    "facility_property_basis_changed",
    "facility_port_position_changed",
    "source_asset_context_changed",
)


def native_attack(original, attack):
    """Reproducible coherent upstream forgeries, including independent review counterexamples."""
    from lh2_tno.consequences import ConsequenceRequest, resolved_source_context
    from lh2_tno.contracts import Facility, canonical_digest

    forged = deepcopy(original)
    data = forged["native_result"]["data"]
    if attack.startswith("ignition_request_"):
        forged["native_request"]["ignition"]["direct_probability"] = 0.9
        native_request = ConsequenceRequest.model_validate_json(json.dumps(forged["native_request"]))
        data["ignition_branch_probabilities"] = native_request.ignition.probabilities()
        if attack == "ignition_request_native_digest_changed_case_unchanged":
            payload = native_request.model_dump(mode="json")
            payload["source_input"] = resolved_source_context(
                native_request.resolved_release, native_request.weather
            )
            payload["receptors"] = forged["case"]["facility"]["receptors"]
            forged["native_result"]["request_digest"] = canonical_digest(payload)
    elif attack == "ignition_and_harm_coherent":
        data["ignition_branch_probabilities"] = {
            key: 0.9 if key == "direct_fire" else 0.1 if key == "no_ignition" else 0.0
            for key in data["ignition_branch_probabilities"]
        }
    elif attack == "harm_only":
        data["receptors"][0]["harm_given_observed_release_low_high"] = [0.9, 0.9]
    elif attack == "receptor_position":
        data["receptors"][0]["position_enu_m"][0] += 0.01
    elif attack == "source_clock":
        forged["source_record"]["context"]["clock_id"] = "another_clock"
    elif attack == "presence_already_applied":
        forged["native_request"]["occupancy_probability"] = 0.5
    elif attack == "provider_version":
        forged["provider"]["source_sha256"] = "0" * 64
    elif attack.startswith("receptor_height_"):
        forged["case"]["facility"]["receptors"][0]["position_enu_m"][2] += 0.4
        data["receptors"][0]["position_enu_m"][2] += 0.4
        if attack == "receptor_height_changed_rehashed_facility":
            forged["native_result"]["facility_digest"] = Facility.model_validate_json(
                json.dumps(forged["case"]["facility"])
            ).digest()
    elif attack == "known_domain_truncation_promoted":
        data["explosion"]["domain_truncated"] = True
        data["explosion"]["domain_truncation_reasons"] = ["injected_unresolved_support"]
    elif attack == "native_failed_ok_false_promoted":
        forged["native_result"]["ok"] = False
    elif attack == "branch_harm_changed_thermal_harm_unchanged":
        for row in data["receptors"]:
            row["branch_harm_low_high"] = [
                {key: 0.0 if key == "no_ignition" else value for key in row["branch_harm_low_high"][0]}
                for value in (0.4, 0.8)
            ]
    elif attack in {"facility_property_basis_changed", "facility_port_position_changed"}:
        if attack == "facility_property_basis_changed":
            forged["case"]["facility"]["components"][0]["property_basis"].update(
                fluid="ParaHydrogen", energy_reference="CoolProp-HEOS:ParaHydrogen:native"
            )
        else:
            forged["case"]["facility"]["ports"][0]["position_enu_m"][0] += 0.1
        forged["native_result"]["facility_digest"] = Facility.model_validate_json(
            json.dumps(forged["case"]["facility"])
        ).digest()
    elif attack == "source_asset_context_changed":
        forged["context"]["asset_id"] = "another_asset"
        forged["source_record"]["context"]["asset_id"] = "another_asset"
    else:
        raise ValueError("unrecognized native attack")
    if attack.startswith("ignition_") or attack == "branch_harm_changed_thermal_harm_unchanged":
        for row in data["receptors"]:
            row["harm_given_observed_release_low_high"] = [
                math.fsum(
                    data["ignition_branch_probabilities"][key] * branch[key]
                    for key in data["ignition_branch_probabilities"]
                )
                for branch in row["branch_harm_low_high"]
            ]
    forged["result_digest"] = digest({k: v for k, v in forged.items() if k != "result_digest"})
    return forged


def change_condition(request, index=0, **changes):
    event = request.events[0]
    conditions = list(event.conditions)
    conditions[index] = conditions[index].model_copy(update=changes)
    return request.model_copy(
        update={"events": (event.model_copy(update={"conditions": tuple(conditions)}),)}
    )


def rehashed(record):
    record["result_digest"] = digest({k: v for k, v in record.items() if k != "result_digest"})
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prism", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--consequence-directory", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "research/spatial_risk_results.json")
    args = parser.parse_args()
    for source in (args.prism, args.hra):
        sys.path.insert(0, str((source / "src").resolve()))
    before = native_backend_metadata()
    output_dir = ROOT / "artifacts/spatial_risk"
    output_dir.mkdir(parents=True, exist_ok=True)
    criteria, records, examples = [], [], {}

    def check(name, passed, kind, observed=None):
        criteria.append({"id": name, "passed": bool(passed), "kind": kind, "observed": observed})
        if not passed:
            raise AssertionError(name)

    def run(name, request, context):
        result = evaluate_spatial_risk(request, context)
        check(
            f"{name}:semantic_replay",
            verify_spatial_risk_record(result, request=request, context=context)["valid"],
            "contract_replay",
        )
        path = output_dir / f"{name}.json"
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )
        records.append(
            {
                "id": name,
                "status": result["status"],
                "sha256": file_digest(path),
                "result_digest": result["result_digest"],
                "path": path.relative_to(ROOT).as_posix(),
            }
        )
        examples[name] = result
        return result

    make = EXAMPLE["make_case"]
    q, c = make(reviewed=True)
    base = run("two_cell_joint_reference", q, c)
    expected = ((0.01, 0.00625, 0.0125), (0.02, 0.0025, 0.0025))
    for row, reference in zip(base["maps"], expected, strict=True):
        for channel, value in zip(
            ("fixed_location_ir_per_year", "present_person_ir_per_year", "expected_fatalities_per_year"),
            reference,
            strict=True,
        ):
            check(
                f"independent:{row['cell_id']}:{channel}",
                math.isclose(row[channel], value, rel_tol=0, abs_tol=1e-14),
                "independent_authored_scalar",
                row[channel],
            )
    for threshold, value in {"1": 0.01125, "2": 0.00375, "3": 0.0}.items():
        check(
            f"independent:integer_FN:{threshold}",
            base["integer_casualty_fn_curve"]["value"][threshold] == value,
            "independent_integer_joint_law",
            value,
        )
    check(
        "independent:expected_death_threshold_is_different",
        base["expected_death_threshold_curve"]["value"]
        == {"expected_deaths": [1.5], "frequency_per_year": [0.005]},
        "independent_expected_threshold",
        base["expected_death_threshold_curve"]["value"],
    )
    condition = q.events[0].conditions[0]
    states = (
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 2, "cell:0:1": 1}, probability=0.5),
    )
    source = {
        **condition.joint_casualties.source_record,
        "states": [s.model_dump(mode="json") for s in states],
    }
    joint = condition.joint_casualties.model_copy(
        update={"states": states, "source_record": source, "source_digest": digest(source)}
    )
    alternative = run("same_marginals_different_dependence", change_condition(q, joint_casualties=joint), c)
    check(
        "independent:joint_dependence_FN",
        alternative["maps"] == base["maps"]
        and alternative["integer_casualty_fn_curve"]["value"] == {"1": 0.01, "2": 0.0025, "3": 0.0025},
        "independent_integer_joint_law",
    )
    population = tuple(
        p.model_copy(update={"persons": 1, "presence_probability": 1.0}) for p in condition.population
    )
    field_source = deepcopy(condition.field.source_record)
    for row, p in zip(field_source["receptors"], (0.9, 0.1), strict=True):
        row["probability"] = p
    field = condition.field.model_copy(
        update={"source_record": field_source, "source_digest": digest(field_source)}
    )
    states = (
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 1, "cell:0:1": 1}, probability=0.5),
    )
    source = {
        **condition.joint_casualties.source_record,
        "field_source_digest": field.source_digest,
        "population_digest": digest(population),
        "states": [s.model_dump(mode="json") for s in states],
    }
    joint = condition.joint_casualties.model_copy(
        update={"states": states, "source_record": source, "source_digest": digest(source)}
    )
    refused = False
    try:
        evaluate_spatial_risk(
            change_condition(q, population=population, field=field, joint_casualties=joint), c
        )
    except ValueError as error:
        refused = "per-cell casualty mean" in str(error)
    check(
        "independent:impossible_joint_correct_global_mean_refused",
        refused,
        "independent_joint_feasibility_counterexample",
    )
    frames = to_signal_frames(base, c)
    check(
        "signal:actual_clock_units_origin",
        all(
            f.clock_id == c.clock_id
            and f.time_s == 10.0
            and f.origin == c.origin
            and f.units["expected_fatalities_per_year"] == "person/year"
            for f in frames
        ),
        "contract_export",
    )
    qm, cm = make(reviewed=True, origin="measured")
    modeled = run("measured_context_derived_signal", qm, cm)
    check(
        "signal:derived_origin_and_asset",
        modeled["context"]["origin"] == "measured"
        and to_signal_frames(modeled, cm)[0].origin == "simulation"
        and to_signal_frames(modeled, cm)[0].stream_id.startswith(f"{cm.asset_id}/{cm.scenario_id}/"),
        "contract_export",
    )
    no_joint, _ = make(reviewed=True, with_joint=False)
    no_joint_result = run("no_joint_distribution", no_joint, c)
    check(
        "no_joint:FN_withheld_expected_available",
        no_joint_result["integer_casualty_fn_curve"]["value"] is None
        and no_joint_result["maps"][0]["expected_fatalities_per_year"] == 0.0125,
        "unknown_meaning",
    )
    for field in ("persons", "presence_probability", "receptor_id"):
        pop = list(no_joint.events[0].conditions[0].population)
        pop[0] = pop[0].model_copy(update={field: None})
        result = run(f"unknown_{field}", change_condition(no_joint, population=tuple(pop)), c)
        check(
            f"unknown:{field}:not_zero",
            result["maps"][0]["expected_fatalities_per_year"] is None
            and result["maps"][0]["known_expected_fatalities_per_year"] == 0.0075,
            "unknown_meaning",
        )
        check(
            f"unknown:{field}:partial_signal",
            to_signal_frames(result, c, allow_partial=True)[0].valid["expected_fatalities_per_year"] is False,
            "contract_export",
        )
    first = no_joint.events[0].conditions[0]
    source = deepcopy(first.field.source_record)
    source["receptors"][0]["probability"] = None
    unknown_field = first.field.model_copy(update={"source_record": source, "source_digest": digest(source)})
    result = run("unknown_harm", change_condition(no_joint, field=unknown_field), c)
    check(
        "unknown:conditional_harm_not_zero",
        result["maps"][0]["fixed_location_ir_per_year"] is None,
        "unknown_meaning",
    )
    missing_rate = no_joint.model_copy(
        update={"events": (no_joint.events[0].model_copy(update={"frequency": None}),)}
    )
    result = run("missing_initiating_rate", missing_rate, c)
    check(
        "unknown:missing_rate_withheld",
        result["integer_casualty_fn_curve"]["value"] is None
        and all(r["fixed_location_ir_per_year"] is None for r in result["maps"]),
        "unknown_meaning",
    )
    conditions = tuple(
        x.model_copy(
            update={
                "population": tuple(p.model_copy(update={"presence_probability": 0.0}) for p in x.population)
            }
        )
        for x in no_joint.events[0].conditions
    )
    zero_presence = no_joint.model_copy(
        update={"events": (no_joint.events[0].model_copy(update={"conditions": conditions}),)}
    )
    result = run("zero_presence", zero_presence, c)
    check(
        "zero_presence:fixed_ir_unchanged",
        [r["fixed_location_ir_per_year"] for r in result["maps"]] == [0.01, 0.02]
        and all(
            r["present_person_ir_per_year"] == 0.0 and r["expected_fatalities_per_year"] == 0.0
            for r in result["maps"]
        ),
        "independent_authored_scalar",
    )
    conditions = tuple(
        x.model_copy(
            update={
                "population": tuple(p.model_copy(update={"persons": p.persons * 2}) for p in x.population)
            }
        )
        for x in no_joint.events[0].conditions
    )
    doubled = no_joint.model_copy(
        update={"events": (no_joint.events[0].model_copy(update={"conditions": conditions}),)}
    )
    result = run("double_population", doubled, c)
    check(
        "population:counts_not_occupancy_squared",
        all(
            math.isclose(
                row["expected_fatalities_per_year"],
                2 * original["expected_fatalities_per_year"],
                rel_tol=0,
                abs_tol=1e-14,
            )
            and row["present_person_ir_per_year"] == original["present_person_ir_per_year"]
            for row, original in zip(result["maps"], base["maps"], strict=True)
        ),
        "independent_authored_scalar",
    )
    grid = no_joint.grid.model_copy(update={"y_edges_m": (-1.0, 1.0)})
    conditions = []
    for condition in no_joint.events[0].conditions:
        source = deepcopy(condition.field.source_record)
        source["grid_digest"] = digest(grid)
        conditions.append(
            condition.model_copy(
                update={
                    "field": condition.field.model_copy(
                        update={"source_record": source, "source_digest": digest(source)}
                    )
                }
            )
        )
    bigger_cells = no_joint.model_copy(
        update={
            "grid": grid,
            "events": (no_joint.events[0].model_copy(update={"conditions": tuple(conditions)}),),
        }
    )
    result = run("double_cell_area_same_counts", bigger_cells, c)
    check(
        "area:persons_not_density",
        [r["expected_fatalities_per_year"] for r in result["maps"]]
        == [r["expected_fatalities_per_year"] for r in base["maps"]]
        and result["cells"][0]["area_m2"] == 2.0,
        "independent_geometry_and_count_units",
    )
    event = q.events[0]
    rate_source = {**event.frequency.source_payload, "annual_frequency_per_year": 2.0}
    large_rate = event.frequency.model_copy(
        update={
            "annual_frequency_per_year": 2.0,
            "source_payload": rate_source,
            "source_digest": digest(rate_source),
            "binding_digest": None,
        }
    )
    result = run(
        "rate_not_probability",
        q.model_copy(update={"events": (event.model_copy(update={"frequency": large_rate}),)}),
        c,
    )
    check(
        "rate:greater_than_one_is_not_clipped",
        result["maps"][1]["fixed_location_ir_per_year"] == 2.0
        and result["integer_casualty_fn_curve"]["value"]["1"] == 1.125,
        "independent_authored_scalar",
    )
    q0, c0 = make()
    run("withheld_before_optional", q0, c0)

    native_applications = []
    native_adversarial = []
    if args.consequence_directory:
        for name in ("wind_270", "wind_180", "unadapted"):
            original = json.loads((args.consequence_directory / f"{name}.json").read_text(encoding="utf-8"))
            native_q, native_c = EXAMPLE["make_native_case"](original, reviewed=True)
            result = run(f"retained_process_{name}", native_q, native_c)
            harm = (
                None
                if original["status"] != "computed_diagnostic"
                else next(r for r in original["native_result"]["data"]["receptors"] if r["id"] == "near")[
                    "harm_given_observed_release_low_high"
                ][0]
            )
            check(
                f"native_process:{name}:projection",
                result["maps"][0]["fixed_location_ir_per_year"] is None
                if harm is None
                else math.isclose(
                    result["maps"][0]["fixed_location_ir_per_year"], 0.02 * harm, rel_tol=0, abs_tol=1e-14
                ),
                "actual_native_projection_not_independent_physics",
                {"harm": harm, "rate": result["maps"][0]["fixed_location_ir_per_year"]},
            )
            check(
                f"native_process:{name}:joint_not_inferred",
                result["integer_casualty_fn_curve"]["value"] is None,
                "unknown_meaning",
            )
            native_applications.append(
                {
                    "id": name,
                    "original_result_digest": original["result_digest"],
                    "original_status": original["status"],
                    "original_issues": list(original["issues"]),
                    "source_file_sha256": file_digest(args.consequence_directory / f"{name}.json"),
                    "original_provider": original["provider"],
                }
            )
            if original["status"] == "computed_diagnostic":
                for attack in NATIVE_ATTACKS:
                    forged = native_attack(original, attack)
                    refused = False
                    try:
                        forged_request, forged_context = EXAMPLE["make_native_case"](forged, reviewed=True)
                        evaluate_spatial_risk(forged_request, forged_context)
                    except ValueError:
                        refused = True
                    check(f"rehashed_native:{attack}", refused, "original_native_source_semantic_refusal")
                    native_adversarial.append({"id": attack, "refused": refused})

    adversarial = []
    for key in (
        "map",
        "event_ledger",
        "integer_FN",
        "expected_curve",
        "geometry",
        "source_pin",
        "meaning",
        "qualification",
        "population",
        "clock",
        "status",
    ):
        bad = deepcopy(base)
        if key == "map":
            bad["maps"][0]["present_person_ir_per_year"] *= 2
        elif key == "event_ledger":
            bad["native_receipts"][0]["event_tree"][0]["annual_frequency_per_year"] *= 2
        elif key == "integer_FN":
            bad["integer_casualty_fn_curve"]["value"]["1"] = 0.005
        elif key == "expected_curve":
            bad["expected_death_threshold_curve"]["value"]["expected_deaths"] = [2.0]
        elif key == "geometry":
            bad["cells"][0]["area_m2"] = 100.0
        elif key == "source_pin":
            bad["provider"]["packages"]["human_reliability"]["source_sha256"] = "0" * 64
        elif key == "meaning":
            bad["semantics"]["fixed_location_ir"] = "annual probability"
        elif key == "qualification":
            bad["complete_facility_risk_claim"] = True
        elif key == "population":
            bad["request"]["events"][0]["conditions"][0]["population"][0]["persons"] = 200
        elif key == "clock":
            bad["request"]["window_start_s"] = 0.0
        else:
            bad["status"] = "qualified"
        rehashed(bad)
        refused = not verify_spatial_risk_record(bad)["valid"]
        check(f"rehashed:{key}", refused, "semantic_adversarial_replay")
        adversarial.append({"id": key, "refused": refused})
    check("native_pins:before_after", native_backend_metadata() == before, "source_and_runtime_integrity")
    summary = {
        "schema": "pipeline_integrity.spatial_risk_research.v1",
        "status": "passed_bounded_native_accounting_not_field_qualification",
        "provider": before,
        "records": records,
        "counts": {
            "records": len(records),
            "statuses": dict(Counter(x["status"] for x in records)),
            "criteria": len(criteria),
            "criteria_kinds": dict(Counter(x["kind"] for x in criteria)),
            "adversarial_refusals": len(adversarial) + len(native_adversarial),
        },
        "criteria": criteria,
        "adversarial": adversarial,
        "original_native_adversarial": native_adversarial,
        "retained_native_process_applications": native_applications,
        "limitations": [
            "local ENU rectangular representative cells only; no continuous contour interpolation",
            "population is authored disjoint integer counts, not a calibrated public exposure map",
            "true FN needs an externally supplied reviewed joint integer per-cell count law with each cell mean matching its own presence and harm marginals; dependence is never inferred",
            "finite-window harm annualized by supplied occurrence rate; no full-facility QRA qualification",
            "WorldPop WGS84 fractional modeled people/pixel is not silently converted or rounded into this integer/local-metre contract",
        ],
        "code_sha256": {
            p: file_digest(ROOT / p)
            for p in (
                "src/pipeline_integrity/spatial_risk.py",
                "tests/test_spatial_risk.py",
                "examples/spatial_risk.py",
                "scripts/check_spatial_risk.py",
                "docs/SPATIAL_RISK_KO.md",
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
