"""Authored statics/capacity criteria; detailed records stay outside releases."""

import copy
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.lifting import make_case  # noqa: E402
from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import file_digest  # noqa: E402
from pipeline_integrity.lifting import (  # noqa: E402
    ExternalLiftingLoad,
    LiftingRequest,
    boundary_metadata,
    evaluate_lifting,
    to_signal_frame,
    verify_lifting_record,
)


def main():
    directory = ROOT / "artifacts/lifting"
    directory.mkdir(parents=True, exist_ok=True)
    records, criteria = {}, {}

    def run(name, *, reviewed=True, changes=None):
        request, context = make_case(reviewed=reviewed)
        if changes:
            request = LiftingRequest.model_validate(
                request.model_copy(update=changes(request)).model_dump(mode="python")
            )
            context = context.model_copy(
                update={"metadata": {"lifting_boundary": boundary_metadata(request)}}
            )
        record = evaluate_lifting(request, context)
        criteria[f"replay:{name}"] = verify_lifting_record(record, request=request, context=context)
        (directory / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        records[name] = record
        return request, context, record

    request, context, positive = run("symmetric")
    weight = 1000 * 9.80665
    expected = weight / math.sqrt(2)
    criteria["closed_form_two_leg_tension"] = all(
        math.isclose(t, expected, rel_tol=1e-13) for t in positive["solution"]["sling_tensions_n"].values()
    )
    criteria["computed_not_field_qualified"] = (
        positive["status"] == "computed" and not positive["field_qualified"]
    )
    anchor = next(c for c in positive["capacity_checks"] if c["target_kind"] == "anchor")
    criteria["shared_hook_resultant_not_sum_of_tension_magnitudes"] = (
        math.isclose(anchor["force_n"], weight, rel_tol=1e-13) and anchor["force_vector_n"][2] < 0
    )
    criteria["five_hardware_checks"] = len(positive["capacity_checks"]) == 5
    criteria["force_and_moment_residuals"] = (
        math.hypot(*positive["solution"]["net_force_n"]) < 1e-9
        and math.hypot(*positive["solution"]["net_moment_nm"]) < 1e-9
    )
    frame = to_signal_frame(positive, context)
    criteria["signal_computation_origin_clock"] = (
        frame.origin == "synthetic" and frame.clock_id == request.clock_id
    )
    _, _, unreviewed = run("unreviewed", reviewed=False)
    criteria["default_no_solve"] = (
        unreviewed["status"] == "needs_data" and not unreviewed["native_solver_called"]
    )
    _, _, missing = run("missing_capacity", changes=lambda r: {"capacities": ()})
    criteria["capacity_unknown_keeps_nominal_and_withholds_acceptance"] = (
        missing["status"] == "needs_data"
        and missing["solution"] is not None
        and missing["acceptance"] == "needs_data"
    )
    _, _, overload = run("overload", changes=lambda r: {"load_multiplier": 3.0})
    criteria["overload_not_solver_failure_or_approval"] = (
        overload["status"] == "computed" and overload["acceptance"] == "exceeds_supplied_limits"
    )
    _, _, unknown = run("unknown_mass", changes=lambda r: {"mass_kg": None})
    criteria["unknown_mass_no_solver"] = (
        unknown["status"] == "needs_data" and not unknown["native_solver_called"]
    )
    _, _, redundant = run(
        "redundant_sling",
        changes=lambda r: {
            "slings": (*r.slings, r.slings[0].model_copy(update={"id": "extra"})),
            "capacities": (),
        },
    )
    criteria["redundant_no_minimum_norm_sharing"] = (
        redundant["status"] == "out_of_scope" and redundant["solution"] is None
    )
    load = ExternalLiftingLoad(
        id="cross_load", position_m=(0.0, 0.0, 0.0), force_n=(0.0, 100.0, 0.0), evidence_id="authored"
    )
    _, _, unbalanced = run("unbalanced", changes=lambda r: {"external_loads": (load,)})
    criteria["least_squares_residual_not_accepted_as_equilibrium"] = (
        unbalanced["status"] == "out_of_scope" and unbalanced["solution"] is None
    )
    _, _, domain = run(
        "anchor_wrong_direction",
        changes=lambda r: {
            "capacities": tuple(
                c.model_copy(
                    update={
                        "direction_policy": "within_cone",
                        "rated_direction": (0.0, 0.0, 1.0),
                        "maximum_angle_rad": 0.1,
                    }
                )
                if c.target_kind == "anchor"
                else c
                for c in r.capacities
            )
        },
    )
    criteria["wrong_anchor_capacity_direction_withheld"] = (
        domain["status"] == "out_of_scope" and domain["acceptance"] == "not_applicable"
    )
    for target in ("tension", "anchor", "acceptance", "stability", "runtime"):
        forged = copy.deepcopy(positive)
        if target == "tension":
            forged["solution"]["sling_tensions_n"]["left"] *= 2
        elif target == "anchor":
            forged["capacity_checks"][0]["force_vector_n"][2] *= -1
        elif target == "acceptance":
            forged["acceptance"] = "approved"
        elif target == "stability":
            forged["configuration_stability_solved"] = True
        else:
            forged["runtime"]["numpy"] = "forged"
        forged["record_digest"] = digest({k: v for k, v in forged.items() if k != "record_digest"})
        try:
            verify_lifting_record(forged, request=request, context=context)
        except ValueError:
            criteria[f"rehashed_refusal:{target}"] = True
        else:
            criteria[f"rehashed_refusal:{target}"] = False
    result = {
        "schema": "pipeline_integrity.lifting_verification.v1",
        "checks": criteria,
        "passed": sum(criteria.values()),
        "total": len(criteria),
        "records": len(records),
        "statuses": dict(Counter(r["status"] for r in records.values())),
        "rehashed_mutations_refused": sum(
            v for k, v in criteria.items() if k.startswith("rehashed_refusal:")
        ),
        "file_sha256": {
            p: file_digest(ROOT / p)
            for p in (
                "src/pipeline_integrity/lifting.py",
                "tests/test_lifting.py",
                "examples/lifting.py",
                "scripts/check_lifting.py",
                "docs/LIFTING_KO.md",
            )
        },
        "criteria_mix_contract_numeric_and_replay_checks": True,
        "actual_supplier_or_field_data_applied": False,
        "configuration_stability_or_dynamics_qualified": False,
    }
    (ROOT / "research/lifting_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "total": result["total"],
                "records": result["records"],
                "statuses": result["statuses"],
            }
        )
    )
    if not all(criteria.values()):
        raise SystemExit("lifting verification criteria failed")


if __name__ == "__main__":
    main()
