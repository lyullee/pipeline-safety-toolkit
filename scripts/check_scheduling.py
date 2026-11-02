"""Verify real CP-SAT plans, native HRA identities and declared tasks attached to public gas topology."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.gaslib import read_gaslib
from pipeline_integrity.schedule_exchange import bind_hra_task, planned_hra_steps
from pipeline_integrity.scheduling import (
    CapacityWindow,
    Operation,
    OperationMode,
    PlanningResource,
    ScheduleRequest,
    solve_schedule,
)


def summary(result):
    return dict(
        status=result.status,
        proven_optimal=result.proven_optimal,
        native_status=result.native.native_status,
        native_bound=result.native.best_bound,
        objective=result.audit.get("objective"),
        makespan_ticks=result.audit.get("makespan_tick"),
        independent_primal_valid=result.audit.get("valid"),
        result_digest=result.result_digest,
        work_authorized=result.work_authorized,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.hra / "src").resolve()), str(Path("examples").resolve())]
    from human_reliability.contracts import Evidence, HumanFailureEvent, Task, TaskStep
    from human_reliability.qualitative import DeviationRule, analyze_task
    from operations_schedule import make_case
    from ortools.sat.python import cp_model, cp_model_helper

    before = revision(args.hra)
    root = Path("artifacts/scheduling")
    root.mkdir(parents=True, exist_ok=True)
    spec, context = make_case()
    records = {}

    def evaluate(name, request):
        result = solve_schedule(request, context)
        result.check_integrity()
        records[name] = result
        (root / (name + ".json")).write_text(
            json.dumps(
                {
                    "specification": request.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"case": name, **summary(result)}), flush=True)
        return result

    reference = evaluate("reference", spec)
    assert reference.status == "optimal" and reference.audit["makespan_tick"] == 8
    inspect = next(j for j in spec.operations if j.id == "inspect")
    fast = inspect.model_copy(
        update={"modes": tuple(m.model_copy(update={"fixed_cost": 0}) for m in inspect.modes)}
    )
    fast_spec = spec.model_copy(
        update={"operations": tuple(fast if j.id == "inspect" else j for j in spec.operations)}
    )
    assert evaluate("zero_fast_mode_cost", fast_spec).audit["makespan_tick"] == 7
    assert (
        evaluate("no_spare", fast_spec.model_copy(update={"consumable_budgets": {"spare": 0}})).audit[
            "makespan_tick"
        ]
        == 8
    )
    shifted = spec.model_copy(
        update={
            "resources": tuple(
                r.model_copy(update={"calendar": (CapacityWindow(start_s=0.0, finish_s=240.0, capacity=0),)})
                if r.id == "operator"
                else r
                for r in spec.resources
            )
        }
    )
    assert evaluate("later_operator_shift", shifted).audit["makespan_tick"] == 9
    assert (
        evaluate("hold_omitted_counterfactual", spec.model_copy(update={"holds": ()})).audit["makespan_tick"]
        == 6
    )
    infeasible = spec.model_copy(
        update={
            "operations": tuple(
                j.model_copy(update={"latest_finish_s": 240.0}) if j.id == "restore" else j
                for j in spec.operations
            )
        }
    )
    assert evaluate("deadline_infeasible", infeasible).status == "infeasible"
    evaluate("tiny_search_budget", spec.model_copy(update={"maximum_seconds": 0.000001}))
    gap = evaluate("gap_stopped", spec.model_copy(update={"relative_gap_limit": 0.5}))
    assert gap.audit["objective"] >= reference.audit["objective"] and gap.audit["valid"]
    if gap.native.best_bound < gap.audit["objective"] - 1:
        assert gap.status == "feasible" and not gap.proven_optimal
    flat = evaluate("zero_time_objective", spec.model_copy(update={"makespan_cost_per_tick": 0}))
    assert flat.status == "optimal" and flat.audit["objective"] == 0

    source = "declared_operations_reference"
    actions = [
        (
            "isolate",
            "manipulate",
            "operator",
            (),
            "Request isolation",
            "Declared isolation command has been issued",
        ),
        (
            "verify",
            "verify",
            "inspector",
            ("isolate",),
            "Review pressure state",
            "Declared review evidence is recorded",
        ),
        (
            "inspect",
            "observe",
            "inspector",
            ("verify",),
            "Collect inspection information",
            "Required inspection record is complete",
        ),
        (
            "restore",
            "restore",
            "operator",
            ("inspect",),
            "Request return to operation",
            "Declared restoration command has been issued",
        ),
    ]
    task = Task(
        id="authored_inspection_task",
        title="Declared task for planning exchange",
        procedure_version="research-1",
        steps=tuple(
            TaskStep(
                id=id,
                kind=kind,
                actor=actor,
                predecessors=parents,
                action=action,
                target="example_pipe",
                cue="Declared task request and preceding record",
                completion=completion,
                deadline_s=120.0 if id == "inspect" else None,
                preconditions=("reviewed_work_authorization",) if id == "isolate" else (),
                latent_hazards=("retained_pressure",) if id == "verify" else (),
            )
            for id, kind, actor, parents, action, completion in actions
        ),
        evidence=(
            Evidence(
                id=source,
                reference="Original hypothetical task and timing interpretation",
                origin="assumed",
                applicability="Research example; not a field procedure",
            ),
        ),
    )
    rule = DeviationRule(
        id="declared_completion_review",
        kinds=("*",),
        concern="completion_evidence",
        question="What supports the required action at {target} after {cue}?",
        effect_to_check="Review the explicitly declared completion condition",
    )
    findings = analyze_task(task, rules=(rule,))
    task_digest = digest(task)
    binding = bind_hra_task(
        task,
        spec,
        context,
        step_to_operation={s.id: s.id for s in task.steps},
        actor_to_resource={"operator": "operator", "inspector": "inspector"},
        mapping_evidence_id=source,
    )
    planned = planned_hra_steps(reference, binding)
    event = HumanFailureEvent(
        id="declared_verification_failure",
        task_id=task.id,
        step_ids=("verify",),
        role="response",
        failure_definition="Required pressure-state review is omitted or incorrectly accepted",
        opportunity_definition="One declared execution of the authored task; no exposure count supplied",
        conditioning="Research task with explicitly declared evidence; no field failure probability",
    )
    assert planned["deadlines_need_separate_review"] and planned["human_failure_probability"] is None
    assert not planned["observed_completion"] and not planned["task_preconditions_verified"]
    assert len(findings) == 5 and all(f.status == "candidate" for f in findings)
    assert task_digest == digest(task) and digest(findings) == digest(analyze_task(task, rules=(rule,)))
    assert set(event.step_ids) <= binding.step_to_operation.keys()
    (root / "hra_binding.json").write_text(binding.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "planned_hra_steps.json").write_text(json.dumps(planned, indent=2) + "\n", encoding="utf-8")

    dataset = next(d for d in catalog() if d.id == "gaslib11")
    path = Path("data/raw/gaslib11/GasLib-11-v1-20211130.zip")
    assert dataset.assets[0].checksum == "sha256:" + file_digest(path)
    topology = read_gaslib(path)
    node_ids = sorted({n for p in topology["pipes"].values() for n in (p["start_node"], p["end_node"])})
    assumptions = "declared_topology_planning"
    data_context = RunContext(
        asset_id="gaslib11_pipe_planning_exercise",
        scenario_id="declared_shared_node_exclusion",
        clock_id="planning_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="gaslib11",
                citation=dataset.citation,
                origin="simulation",
                sha256=file_digest(path),
                license_id=dataset.license_id,
            ),
            EvidenceRef(
                id=assumptions,
                citation="Hypothetical durations, crew calendar and shared-node exclusions; not field maintenance data",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    resources = (
        PlanningResource(
            id="crew",
            capacity=2,
            evidence_id=assumptions,
            calendar=(CapacityWindow(start_s=0.0, finish_s=600.0, capacity=1),),
        ),
    ) + tuple(PlanningResource(id="node:" + n, capacity=1, evidence_id=assumptions) for n in node_ids)
    jobs = []
    for name, pipe in topology["pipes"].items():
        modes = tuple(
            OperationMode(
                id=id,
                duration_s=duration,
                evidence_id=assumptions,
                resources={"crew": crew, "node:" + pipe["start_node"]: 1, "node:" + pipe["end_node"]: 1},
            )
            for id, duration, crew in (("one_crew", 600.0, 1), ("two_crews", 300.0, 2))
        )
        jobs.append(Operation(id=name, modes=modes))
    data_spec = ScheduleRequest(
        horizon_s=7200.0,
        time_quantum_s=60.0,
        operations=tuple(jobs),
        resources=resources,
        evidence_id=assumptions,
    )
    applied = solve_schedule(data_spec, data_context)
    assert applied.status == "optimal" and applied.audit["makespan_tick"] == 45
    assert all(applied.audit["resources"]["node:" + n]["peak_demand"] <= 1 for n in node_ids)
    (root / "gaslib_declared_planning.json").write_text(
        json.dumps(
            {
                "specification": data_spec.model_dump(mode="json"),
                "context": data_context.model_dump(mode="json"),
                "result": applied.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    job = {"specification": spec.model_dump(mode="json"), "context": context.model_dump(mode="json")}
    Path("examples/operations_schedule.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    call = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline_integrity.cli",
            "schedule",
            "examples/operations_schedule.json",
            "--output",
            str(root / "cli_result.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert call.returncode == 0, call.stderr
    cli = json.loads((root / "cli_result.json").read_text(encoding="utf-8"))
    assert (
        cli["status"] == "optimal"
        and cli["audit"]["objective"] == reference.audit["objective"]
        and cli["audit"]["valid"]
    )
    assert before["source_tree_sha256"] == revision(args.hra)["source_tree_sha256"]
    result = dict(
        schema="pipeline_integrity.scheduling_checks.v1",
        cases={n: summary(r) for n, r in records.items()},
        modules_sha256={
            n: file_digest(Path("src/pipeline_integrity") / (n + ".py"))
            for n in ("scheduling", "schedule_exchange")
        },
        verification_script_sha256=file_digest(Path(__file__)),
        solver=reference.native.provider,
        cp_model_module_sha256=file_digest(Path(cp_model.__file__)),
        cp_sat_binary_sha256=file_digest(Path(cp_model_helper.__file__)),
        hra=dict(
            source=before,
            native_task_digest=task_digest,
            binding_digest=binding.binding_digest,
            actual_steps=len(task.steps),
            planned_steps=len(planned["steps"]),
            qualitative_candidate_count=len(findings),
            failure_event_definition_only=event.model_dump(mode="json"),
            deadlines_applied=False,
            task_unchanged=True,
            observed_completion=False,
            human_failure_probability=None,
        ),
        gaslib=dict(
            source_sha256=topology["archive_sha256"],
            citation=dataset.citation,
            creators=dataset.creators,
            license_id=dataset.license_id,
            operations=len(jobs),
            shared_node_resources=len(node_ids),
            result=summary(applied),
            crew_work_s=4800,
            capacity_lower_bound_makespan_s=2700,
            declared_durations_and_calendar=True,
            shared_node_conflicts_are_declared_policy=True,
            actual_maintenance_durations_inferred=False,
            complete_network_physics_status=topology["complete_network_run_status"],
        ),
        cli_objective_and_primal_audit_match=True,
        full_result_hashes_vary_with_solver_runtime=True,
        original_topology_and_locally_transformed_tables_excluded_from_release=True,
        field_schedule_validation=False,
        work_authorized=False,
    )
    Path("research/scheduling_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "native_cases": len(records),
                "native_hra_steps": len(task.steps),
                "gaslib_jobs": len(jobs),
                "gaslib_makespan_s": 2700,
                "work_authorized": False,
            }
        )
    )


if __name__ == "__main__":
    main()
