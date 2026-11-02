"""Exercise the real HRA Task/analyze_task API and pipeline identity binding."""
from human_reliability.contracts import Evidence, Task, TaskStep
from human_reliability.qualitative import analyze_task
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.schedule_exchange import bind_hra_task
from pipeline_integrity.scheduling import Operation, OperationMode, PlanningResource, ScheduleRequest, TimingRelation


def _task():
    evidence = Evidence(
        id="procedure-evidence", reference="synthetic authored task", origin="assumed",
        applicability="abstract isolation task", limitations=("not an approved field procedure",),
    )
    return Task(
        id="isolate_line", title="Abstract line isolation", procedure_version="synthetic-v1",
        evidence=(evidence,),
        steps=(
            TaskStep(id="verify-boundary", action="confirm boundary", kind="observe", target="valve state",
                     actor="operator", cue="indicator agrees", completion="two checks agree"),
            TaskStep(id="close-valve", action="close valve", kind="manipulate", target="isolation valve",
                     actor="operator", cue="verified boundary", completion="closed indication",
                     predecessors=("verify-boundary",), preconditions=("downstream path identified",),
                     latent_hazards=("bypass remains open",), deadline_s=20.0),
        ),
    )


def test_real_hra_owner_orders_task_and_emits_review_candidates_without_hep():
    task = _task()
    assert tuple(step.id for step in task.ordered_steps()) == ("verify-boundary", "close-valve")
    findings = analyze_task(task)
    assert findings
    assert {finding.task_id for finding in findings} == {"isolate_line"}
    assert {finding.step_id for finding in findings} <= {"verify-boundary", "close-valve"}
    latent = [finding for finding in findings if finding.rule_id == "latent_condition"]
    assert len(latent) == 1 and "bypass remains open" in latent[0].question
    assert all(finding.status == "candidate" for finding in findings)
    assert not any(hasattr(finding, "probability") for finding in findings)


def test_actual_task_binds_to_declared_operations_and_keeps_deadline_unapplied():
    context = RunContext(
        asset_id="synthetic-pipe", scenario_id="synthetic-isolation", clock_id="elapsed",
        origin="synthetic", evidence=(EvidenceRef(id="mapping", citation="authored mapping", origin="synthetic"),),
    )
    request = ScheduleRequest(
        horizon_s=60.0, time_quantum_s=1.0, evidence_id="mapping",
        resources=(PlanningResource(id="operator", capacity=1, evidence_id="mapping"),),
        operations=(
            Operation(id="confirm", modes=(OperationMode(id="m1", duration_s=5.0,
                resources={"operator": 1}, evidence_id="mapping"),)),
            Operation(id="isolate", modes=(OperationMode(id="m2", duration_s=8.0,
                resources={"operator": 1}, evidence_id="mapping"),)),
        ),
        relations=(TimingRelation(id="order", before="confirm", after="isolate"),),
    )
    binding = bind_hra_task(
        _task(), request, context,
        step_to_operation={"verify-boundary": "confirm", "close-valve": "isolate"},
        actor_to_resource={"operator": "operator"}, mapping_evidence_id="mapping",
    )
    binding.check_integrity()
    assert binding.native_step_order == ("verify-boundary", "close-valve")
    assert binding.unapplied_deadlines_s == {"close-valve": 20.0}
    assert binding.human_failure_estimated is False
