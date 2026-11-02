"""Physical timing constraints, independent tiny-instance enumeration and solver-certificate policy."""

import itertools

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.scheduling import (
    CapacityWindow,
    Operation,
    OperationMode,
    PlannedOperation,
    PlanningResource,
    ResourceHold,
    ScheduleCandidate,
    ScheduleRequest,
    TimingRelation,
    audit_schedule,
    solve_schedule,
)

CONTEXT = RunContext(
    asset_id="planning",
    scenario_id="declared",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(EvidenceRef(id="e", citation="Original scheduling reference", origin="synthetic"),),
)


def test_mode_specific_appointment_is_enforced_only_when_selected_and_audited():
    spec = request(
        (
            Operation(
                id="a",
                modes=(
                    mode(2, id="early", earliest_start_s=1.0, latest_finish_s=3.0, fixed_cost=2),
                    mode(2, id="late", earliest_start_s=5.0, latest_finish_s=7.0),
                ),
            ),
        )
    )
    result = solve_schedule(spec, CONTEXT)
    assert result.timeline[0]["mode_id"] == "early"  # makespan 3 + cost 2 beats makespan 7
    assert result.timeline[0]["start_s"] == 1.0
    bad = (PlannedOperation(operation_id="a", mode_id="early", start_tick=0, finish_tick=2),)
    assert "mode_window:a" in audit_schedule(spec, bad)["violations"]


def test_a_mode_window_too_short_is_an_infeasible_mode_not_a_rounded_duration():
    spec = request((Operation(id="a", modes=(mode(3, earliest_start_s=1.0, latest_finish_s=3.0),)),))
    assert solve_schedule(spec, CONTEXT).status == "infeasible"


@pytest.mark.parametrize(
    "bounds",
    [
        {"earliest_start_s": 10.5},
        {"latest_finish_s": 11.0},
        {"earliest_start_s": 5.0, "latest_finish_s": 4.0},
    ],
)
def test_invalid_mode_windows_fail_before_solver(bounds):
    with pytest.raises(ValueError):
        request((Operation(id="a", modes=(mode(1, **bounds),)),))


def mode(duration, *, id="normal", resources=None, **kwargs):
    return OperationMode(
        id=id, duration_s=float(duration), resources=resources or {}, evidence_id="e", **kwargs
    )


def request(jobs=None, **changes):
    return ScheduleRequest(
        **(
            dict(horizon_s=10.0, operations=jobs or (Operation(id="a", modes=(mode(2),)),), evidence_id="e")
            | changes
        )
    )


def solve(spec):
    result = solve_schedule(spec, CONTEXT)
    assert result.status == "optimal", result
    assert result.audit["valid"]
    return result


def test_declared_chain_and_independent_half_open_capacity():
    jobs = (
        Operation(id="a", modes=(mode(2, resources={"crew": 1}),)),
        Operation(id="b", modes=(mode(3, resources={"crew": 1}),)),
    )
    resource = PlanningResource(id="crew", capacity=1, evidence_id="e")
    spec = request(jobs, resources=(resource,), relations=(TimingRelation(id="ab", before="a", after="b"),))
    result = solve(spec)
    assert result.audit["makespan_tick"] == 5
    assert result.timeline[0]["finish_s"] == result.timeline[1]["start_s"] == 2.0
    assert not result.work_authorized and not result.failure_probability_calculated
    result.check_integrity()
    result.timeline[0]["finish_s"] = 100.0
    with pytest.raises(ValueError, match="changed"):
        result.check_integrity()


@pytest.mark.parametrize("capacity, expected", [(1, 5), (2, 3)])
def test_capacity_changes_the_feasible_parallelism(capacity, expected):
    jobs = tuple(
        Operation(id=name, modes=(mode(d, resources={"crew": 1}),)) for name, d in (("a", 2), ("b", 3))
    )
    spec = request(jobs, resources=(PlanningResource(id="crew", capacity=capacity, evidence_id="e"),))
    assert solve(spec).audit["makespan_tick"] == expected


def test_nonpreemptive_work_cannot_cross_an_off_shift_window():
    resource = PlanningResource(
        id="crew",
        capacity=1,
        evidence_id="e",
        calendar=(CapacityWindow(start_s=1.0, finish_s=3.0, capacity=0),),
    )
    spec = request((Operation(id="a", modes=(mode(2, resources={"crew": 1}),)),), resources=(resource,))
    result = solve(spec)
    assert result.timeline[0]["start_s"] == 3.0 and result.timeline[0]["finish_s"] == 5.0


def test_touching_calendar_windows_apply_the_new_capacity_at_the_shared_boundary():
    resource = PlanningResource(
        id="crew",
        capacity=2,
        evidence_id="e",
        calendar=(
            CapacityWindow(start_s=0.0, finish_s=2.0, capacity=0),
            CapacityWindow(start_s=2.0, finish_s=4.0, capacity=1),
        ),
    )
    jobs = tuple(Operation(id=name, modes=(mode(2, resources={"crew": 1}),)) for name in ("a", "b"))
    assert solve(request(jobs, resources=(resource,))).audit["makespan_tick"] == 6


@pytest.mark.parametrize(
    "before_event,after_event,lag,expected",
    [
        ("finish", "start", 2.0, 8),
        ("start", "start", 1.0, 4),
        ("finish", "finish", 0.0, 4),
    ],
)
def test_event_relations_and_maximum_lag(before_event, after_event, lag, expected):
    jobs = (Operation(id="a", modes=(mode(4),)), Operation(id="b", modes=(mode(2),)))
    relation = TimingRelation(
        id="timing",
        before="a",
        after="b",
        before_event=before_event,
        after_event=after_event,
        minimum_lag_s=lag,
        maximum_lag_s=lag,
    )
    assert solve(request(jobs, relations=(relation,))).audit["makespan_tick"] == expected


def test_modes_trade_duration_against_declared_cost_and_parts():
    job = Operation(
        id="a", modes=(mode(5, id="slow"), mode(2, id="fast", fixed_cost=4, consumables={"part": 1}))
    )
    spec = request((job,), consumable_budgets={"part": 1})
    assert solve(spec).timeline[0]["mode_id"] == "slow"
    assert solve(spec.model_copy(update={"makespan_cost_per_tick": 3})).timeline[0]["mode_id"] == "fast"
    assert (
        solve(
            spec.model_copy(update={"makespan_cost_per_tick": 3, "consumable_budgets": {"part": 0}})
        ).timeline[0]["mode_id"]
        == "slow"
    )


def test_hold_consumes_the_asset_during_the_gap_between_actions():
    jobs = (
        Operation(id="isolate", modes=(mode(1),)),
        Operation(id="restore", modes=(mode(1),)),
        Operation(id="other", modes=(mode(2, resources={"asset": 1}),)),
    )
    spec = request(
        jobs,
        resources=(PlanningResource(id="asset", capacity=1, evidence_id="e"),),
        relations=(TimingRelation(id="gap", before="isolate", after="restore", minimum_lag_s=2.0),),
    )
    assert solve(spec).audit["makespan_tick"] == 4
    hold = ResourceHold(
        id="asset_hold",
        from_operation="isolate",
        to_operation="restore",
        resources={"asset": 1},
        evidence_id="e",
    )
    assert solve(spec.model_copy(update={"holds": (hold,)})).audit["makespan_tick"] == 6


def test_soft_due_cost_does_not_become_a_hard_deadline():
    jobs = (
        Operation(id="a", modes=(mode(2, resources={"crew": 1}),), due_s=2.0, tardiness_cost_per_tick=10),
        Operation(id="b", modes=(mode(1, resources={"crew": 1}),), due_s=1.0, tardiness_cost_per_tick=1),
    )
    result = solve(request(jobs, resources=(PlanningResource(id="crew", capacity=1, evidence_id="e"),)))
    assert result.audit["objective"] == 5
    assert next(row for row in result.timeline if row["operation_id"] == "b")["finish_s"] == 3.0


def test_infeasible_and_unknown_are_distinct():
    spec = request(
        (Operation(id="a", modes=(mode(2, resources={"crew": 1}),)),),
        resources=(
            PlanningResource(
                id="crew",
                capacity=1,
                evidence_id="e",
                calendar=(CapacityWindow(start_s=0.0, finish_s=10.0, capacity=0),),
            ),
        ),
    )
    result = solve_schedule(spec, CONTEXT)
    assert result.status == "infeasible" and not result.timeline

    class Unknown:
        def solve(self, model):
            return ScheduleCandidate(specification_digest=digest(model), native_status="UNKNOWN")

    result = solve_schedule(spec, CONTEXT, backend=Unknown())
    assert result.status == "unknown" and not result.proven_optimal


@pytest.mark.parametrize("bound, expected", [(2.0, "feasible"), (3.5, "optimal"), (5.0, "withheld")])
def test_native_optimal_label_does_not_replace_the_integer_bound_certificate(bound, expected):
    spec = request()

    class Candidate:
        def solve(self, model):
            return ScheduleCandidate(
                specification_digest=digest(model),
                native_status="OPTIMAL",
                objective=4.0,
                best_bound=bound,
                operations=(
                    PlannedOperation(operation_id="a", mode_id="normal", start_tick=2, finish_tick=4),
                ),
            )

    result = solve_schedule(spec, CONTEXT, backend=Candidate())
    assert result.status == expected and result.proven_optimal == (expected == "optimal")


@pytest.mark.parametrize("problem", ["duration", "mode", "capacity", "parts", "objective", "duplicate"])
def test_bad_primal_plan_is_withheld_even_when_backend_claims_optimal(problem):
    jobs = tuple(
        Operation(id=name, modes=(mode(2, resources={"crew": 1}, consumables={"part": 1}),))
        for name in ("a", "b")
    )
    spec = request(
        jobs,
        resources=(PlanningResource(id="crew", capacity=1, evidence_id="e"),),
        consumable_budgets={"part": 2},
    )
    rows = (
        PlannedOperation(operation_id="a", mode_id="normal", start_tick=0, finish_tick=2),
        PlannedOperation(operation_id="b", mode_id="normal", start_tick=2, finish_tick=4),
    )
    if problem == "duration":
        rows = (rows[0].model_copy(update={"finish_tick": 1}), rows[1])
    elif problem == "mode":
        rows = (rows[0].model_copy(update={"mode_id": "unknown"}), rows[1])
    elif problem == "capacity":
        rows = (rows[0], rows[1].model_copy(update={"start_tick": 0, "finish_tick": 2}))
    elif problem == "parts":
        spec = spec.model_copy(update={"consumable_budgets": {"part": 1}})
    elif problem == "duplicate":
        rows = (rows[0], rows[0])

    class Candidate:
        def solve(self, model):
            return ScheduleCandidate(
                specification_digest=digest(model),
                native_status="OPTIMAL",
                objective=5.0 if problem == "objective" else 4.0,
                best_bound=0.0,
                operations=rows,
            )

    result = solve_schedule(spec, CONTEXT, backend=Candidate())
    assert result.status == "withheld" and not result.timeline


@pytest.mark.parametrize("problem", ["grid", "calendar", "evidence", "interval_budget", "integer_cost"])
def test_unreviewed_quantization_or_invalid_contracts_are_rejected(problem):
    spec = request()
    if problem == "grid":
        spec = spec.model_copy(update={"time_quantum_s": 3.0})
    elif problem == "calendar":
        spec = spec.model_copy(
            update={
                "resources": (
                    PlanningResource(
                        id="crew",
                        capacity=1,
                        evidence_id="e",
                        calendar=(
                            CapacityWindow(start_s=0.0, finish_s=5.0, capacity=0),
                            CapacityWindow(start_s=4.0, finish_s=6.0, capacity=0),
                        ),
                    ),
                )
            }
        )
    elif problem == "interval_budget":
        spec = spec.model_copy(
            update={
                "maximum_intervals": 1,
                "operations": (spec.operations[0], spec.operations[0].model_copy(update={"id": "b"})),
            }
        )
    elif problem == "integer_cost":
        spec = spec.model_copy(update={"makespan_cost_per_tick": True})
    with pytest.raises(ValueError):
        solve_schedule(
            spec, CONTEXT.model_copy(update={"evidence": ()}) if problem == "evidence" else CONTEXT
        )


@pytest.mark.parametrize("durations,release", [((1, 2, 1), 0), ((2, 1, 3), 1), ((1, 1, 2), 2)])
def test_cp_optimum_matches_exhaustive_starts_without_using_the_auditor(durations, release):
    horizon = 8
    jobs = tuple(
        Operation(
            id=str(i),
            modes=(mode(d, resources={"crew": 1}),),
            earliest_start_s=float(release if i == 1 else 0),
        )
        for i, d in enumerate(durations)
    )
    best = horizon + 1
    for starts in itertools.product(range(horizon), repeat=3):
        finishes = [a + d for a, d in zip(starts, durations, strict=True)]
        if starts[1] < release or max(finishes) > horizon:
            continue
        if any(
            not (finishes[i] <= starts[j] or finishes[j] <= starts[i])
            for i in range(3)
            for j in range(i + 1, 3)
        ):
            continue
        best = min(best, max(finishes))
    result = solve(
        request(
            jobs,
            horizon_s=float(horizon),
            resources=(PlanningResource(id="crew", capacity=1, evidence_id="e"),),
        )
    )
    assert result.audit["makespan_tick"] == best


def test_auditor_does_not_accept_an_incomplete_schedule():
    assert not audit_schedule(request(), ())["valid"]
