"""Bind native HRA task identities to a reviewed planning interpretation without inferring HEP or observed times."""

import json
from typing import Literal

from .contracts import Contract, Name, RunContext, digest
from .scheduling import ScheduleRequest, ScheduleResult


class HRAPlanningBinding(Contract):
    schema_version: Literal["pipeline_integrity.hra_planning.v1"] = "pipeline_integrity.hra_planning.v1"
    native_task: dict
    native_task_digest: Name
    native_step_order: tuple[Name, ...]
    specification_digest: Name
    context_digest: Name
    step_to_operation: dict[Name, Name]
    actor_to_resource: dict[Name, Name]
    mapping_evidence_id: Name
    unapplied_deadlines_s: dict[Name, float]
    deadline_interpretation: Literal["preserved_for_separate_review"] = "preserved_for_separate_review"
    predecessor_interpretation: Literal["declared_finish_to_start"] = "declared_finish_to_start"
    human_failure_estimated: Literal[False] = False
    binding_digest: Name

    def check_integrity(self):
        HRAPlanningBinding.model_validate(self.model_dump(mode="python"))
        data = self.model_dump(mode="json", exclude={"binding_digest"})
        if digest(data) != self.binding_digest or digest(self.native_task) != self.native_task_digest:
            raise ValueError("the HRA/planning binding has been changed")


def bind_hra_task(
    task,
    specification: ScheduleRequest,
    context: RunContext,
    *,
    step_to_operation,
    actor_to_resource,
    mapping_evidence_id,
    task_factory=None,
):
    """Require an explicit mapping; the owner's graph semantics and quantitative estimates remain separate."""
    if task_factory is None:
        from human_reliability.contracts import Task

        task_factory = Task
    task = task_factory.model_validate(task.model_dump(mode="python"))
    spec = ScheduleRequest.model_validate(specification.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    steps = {s.id for s in task.steps}
    jobs = {j.id for j in spec.operations}
    actors = {s.actor for s in task.steps}
    resources = {r.id for r in spec.resources}
    if set(actor_to_resource) != actors or set(actor_to_resource.values()) - resources:
        raise ValueError("native actor roles need an explicit mapping to declared planning resources")
    if (
        set(step_to_operation) != steps
        or len(set(step_to_operation.values())) != len(steps)
        or set(step_to_operation.values()) - jobs
    ):
        raise ValueError("each native HRA step needs one distinct declared planning operation")
    if mapping_evidence_id not in {e.id for e in context.evidence}:
        raise ValueError("the timing/dependency interpretation needs context evidence")
    edges = {
        (r.before, r.after) for r in spec.relations if r.before_event == "finish" and r.after_event == "start"
    }
    for step in task.steps:
        job = next(j for j in spec.operations if j.id == step_to_operation[step.id])
        if any(m.resources.get(actor_to_resource[step.actor], 0) < 1 for m in job.modes):
            raise ValueError("each mapped HRA operation mode must reserve its declared actor resource")
        for parent in step.predecessors:
            if (step_to_operation[parent], step_to_operation[step.id]) not in edges:
                raise ValueError(
                    "the planning model omitted the declared finish/start interpretation of an HRA dependency"
                )
    data = dict(
        native_task=task.model_dump(mode="json"),
        native_task_digest=digest(task),
        native_step_order=tuple(s.id for s in task.ordered_steps()),
        specification_digest=digest(spec),
        context_digest=digest(context),
        step_to_operation=dict(step_to_operation),
        actor_to_resource=dict(actor_to_resource),
        mapping_evidence_id=mapping_evidence_id,
        unapplied_deadlines_s={s.id: s.deadline_s for s in task.steps if s.deadline_s is not None},
        binding_digest="pending",
    )
    initial = HRAPlanningBinding.model_validate_json(json.dumps(data))
    data = initial.model_dump(mode="json", exclude={"binding_digest"})
    return HRAPlanningBinding.model_validate_json(json.dumps(data | {"binding_digest": digest(data)}))


def planned_hra_steps(result: ScheduleResult, binding: HRAPlanningBinding):
    result.check_integrity()
    binding.check_integrity()
    if (
        result.specification_digest != binding.specification_digest
        or digest(result.context) != binding.context_digest
    ):
        raise ValueError("schedule and HRA binding refer to different planning contexts")
    if result.status not in {"optimal", "feasible"} or not result.audit.get("valid"):
        raise ValueError("only an independently qualified feasible plan can supply planned HRA times")
    rows = {r["operation_id"]: r for r in result.timeline}
    return dict(
        schema="pipeline_integrity.planned_hra_steps.v1",
        task_id=binding.native_task["id"],
        clock_id=result.context.clock_id,
        scenario_id=result.context.scenario_id,
        asset_id=result.context.asset_id,
        origin="simulation",
        time_meaning="planned_elapsed_seconds",
        schedule_result_digest=result.result_digest,
        binding_digest=binding.binding_digest,
        actor_to_resource=binding.actor_to_resource,
        steps=[
            dict(
                step_id=step,
                operation_id=binding.step_to_operation[step],
                planned_start_s=rows[binding.step_to_operation[step]]["start_s"],
                planned_finish_s=rows[binding.step_to_operation[step]]["finish_s"],
            )
            for step in binding.native_step_order
        ],
        deadlines_need_separate_review=bool(binding.unapplied_deadlines_s),
        human_failure_probability=None,
        work_authorized=False,
        observed_completion=False,
        physical_isolation_verified=False,
        task_preconditions_verified=False,
    )
