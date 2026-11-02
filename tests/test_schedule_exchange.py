"""Planning binding policy; the separate research runner uses the actual HRA Task class."""

import pytest

from pipeline_integrity.contracts import Contract, Name, Positive, RunContext
from pipeline_integrity.schedule_exchange import bind_hra_task, planned_hra_steps
from pipeline_integrity.scheduling import (
    Operation,
    OperationMode,
    PlanningResource,
    ScheduleRequest,
    TimingRelation,
    solve_schedule,
)


class StepSpy(Contract):
    id: Name
    actor: Name = "role"
    predecessors: tuple[Name, ...] = ()
    deadline_s: Positive | None = None


class TaskSpy(Contract):
    id: Name
    steps: tuple[StepSpy, ...]

    def ordered_steps(self):
        return self.steps


def case():
    from test_scheduling import CONTEXT

    task = TaskSpy(id="task", steps=(StepSpy(id="a"), StepSpy(id="b", predecessors=("a",), deadline_s=1.0)))
    spec = ScheduleRequest(
        horizon_s=10.0,
        evidence_id="e",
        resources=(PlanningResource(id="crew", capacity=1, evidence_id="e"),),
        operations=tuple(
            Operation(
                id=n,
                modes=(OperationMode(id="mode", duration_s=2.0, resources={"crew": 1}, evidence_id="e"),),
            )
            for n in ("a", "b")
        ),
        relations=(TimingRelation(id="ab", before="a", after="b"),),
    )
    return task, spec, CONTEXT


def bind(task, spec, context, **kwargs):
    arguments = dict(
        step_to_operation={"a": "a", "b": "b"},
        actor_to_resource={"role": "crew"},
        mapping_evidence_id="e",
        task_factory=TaskSpy,
    )
    return bind_hra_task(task, spec, context, **(arguments | kwargs))


def test_planned_times_are_not_observed_completion_deadline_satisfaction_or_hep():
    task, spec, context = case()
    binding = bind(task, spec, context)
    result = solve_schedule(spec, context)
    exported = planned_hra_steps(result, binding)
    assert exported["origin"] == "simulation" and exported["time_meaning"] == "planned_elapsed_seconds"
    assert exported["steps"][1]["planned_finish_s"] == 4.0
    assert exported["deadlines_need_separate_review"] and binding.unapplied_deadlines_s == {"b": 1.0}
    assert exported["human_failure_probability"] is None and not exported["observed_completion"]
    assert not exported["work_authorized"] and not exported["physical_isolation_verified"]


@pytest.mark.parametrize("problem", ["mapping", "actor", "precedence", "capacity_mapping", "evidence"])
def test_missing_or_implicit_interpretations_are_rejected(problem):
    task, spec, context = case()
    kwargs = {}
    if problem == "mapping":
        kwargs["step_to_operation"] = {"a": "a", "b": "a"}
    elif problem == "actor":
        kwargs["actor_to_resource"] = {}
    elif problem == "precedence":
        spec = spec.model_copy(update={"relations": ()})
    elif problem == "capacity_mapping":
        job = spec.operations[0]
        spec = spec.model_copy(
            update={
                "operations": (
                    job.model_copy(update={"modes": (job.modes[0].model_copy(update={"resources": {}}),)}),
                    spec.operations[1],
                )
            }
        )
    else:
        kwargs["mapping_evidence_id"] = "absent"
    with pytest.raises(ValueError):
        bind(task, spec, context, **kwargs)


def test_context_mismatch_and_nested_receipt_changes_are_rejected():
    task, spec, context = case()
    binding = bind(task, spec, context)
    different = RunContext.model_validate(context.model_dump(mode="python") | {"clock_id": "another_clock"})
    result = solve_schedule(spec, different)
    with pytest.raises(ValueError, match="different planning contexts"):
        planned_hra_steps(result, binding)
    binding.native_task["steps"][0]["id"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        binding.check_integrity()
