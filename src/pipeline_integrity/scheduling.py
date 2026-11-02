"""Declared nonpreemptive operations planning through CP-SAT and an independent event-sweep audit."""

import json
import math
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest

Count = Annotated[int, Field(ge=0, le=1000000000)]
Demand = Annotated[int, Field(ge=1, le=1000000000)]
Event = Literal["start", "finish"]


def ticks(seconds, quantum):
    value = Decimal(str(seconds)) / Decimal(str(quantum))
    if value != value.to_integral_value() or not 0 <= value <= 1000000000:
        raise ValueError("planning times must lie exactly on the declared nonnegative time grid")
    return int(value)


def seconds(value, quantum):
    return float(Decimal(value) * Decimal(str(quantum)))


class CapacityWindow(Contract):
    start_s: Nonnegative
    finish_s: Nonnegative
    capacity: Count


class PlanningResource(Contract):
    id: Name
    capacity: Demand
    calendar: tuple[CapacityWindow, ...] = ()
    evidence_id: Name


class OperationMode(Contract):
    id: Name
    duration_s: Nonnegative
    resources: dict[Name, Demand] = Field(default_factory=dict)
    consumables: dict[Name, Demand] = Field(default_factory=dict)
    fixed_cost: Count = 0
    evidence_id: Name
    earliest_start_s: Nonnegative | None = None
    latest_finish_s: Nonnegative | None = None


class Operation(Contract):
    id: Name
    modes: tuple[OperationMode, ...] = Field(min_length=1)
    earliest_start_s: Nonnegative = 0.0
    latest_finish_s: Nonnegative | None = None
    due_s: Nonnegative | None = None
    completion_cost_per_tick: Count = 0
    tardiness_cost_per_tick: Count = 0


class TimingRelation(Contract):
    id: Name
    before: Name
    after: Name
    before_event: Event = "finish"
    after_event: Event = "start"
    minimum_lag_s: Nonnegative = 0.0
    maximum_lag_s: Nonnegative | None = None


class ResourceHold(Contract):
    id: Name
    from_operation: Name
    to_operation: Name
    from_event: Event = "start"
    to_event: Event = "finish"
    resources: dict[Name, Demand]
    evidence_id: Name


class ScheduleRequest(Contract):
    horizon_s: Positive
    time_quantum_s: Positive = 1.0
    operations: tuple[Operation, ...] = Field(min_length=1)
    resources: tuple[PlanningResource, ...] = ()
    relations: tuple[TimingRelation, ...] = ()
    holds: tuple[ResourceHold, ...] = ()
    consumable_budgets: dict[Name, Count] = Field(default_factory=dict)
    makespan_cost_per_tick: Count = 1
    objective_unit: Name = "declared_planning_cost"
    evidence_id: Name
    maximum_operations: int = Field(default=1000, ge=1, le=10000)
    maximum_intervals: int = Field(default=20000, ge=1, le=1000000)
    maximum_seconds: Positive = Field(default=30.0, le=3600)
    relative_gap_limit: Nonnegative = Field(default=0.0, le=1)
    workers: int = Field(default=1, ge=1, le=32)
    random_seed: int = Field(default=0, ge=0, le=2147483647)

    @model_validator(mode="after")
    def domain(self):
        horizon = ticks(self.horizon_s, self.time_quantum_s)
        if horizon < 1:
            raise ValueError("planning horizon needs at least one time tick")
        operations = {j.id for j in self.operations}
        resources = {r.id for r in self.resources}
        if len(operations) != len(self.operations) or len(resources) != len(self.resources):
            raise ValueError("operation and resource IDs must be unique")
        if len(self.operations) > self.maximum_operations:
            raise ValueError("operation budget exceeded")
        intervals = (
            sum(len(j.modes) for j in self.operations)
            + len(self.holds)
            + sum(len(r.calendar) for r in self.resources)
        )
        if intervals > self.maximum_intervals:
            raise ValueError("planning interval budget exceeded")
        upper_cost = horizon * self.makespan_cost_per_tick
        for j in self.operations:
            if len({m.id for m in j.modes}) != len(j.modes):
                raise ValueError("mode IDs must be unique within each operation")
            for t in (j.earliest_start_s, j.latest_finish_s):
                if t is not None and ticks(t, self.time_quantum_s) > horizon:
                    raise ValueError("hard operation windows must be inside the planning horizon")
            if j.due_s is not None:
                ticks(j.due_s, self.time_quantum_s)
            elif j.tardiness_cost_per_tick:
                raise ValueError("a tardiness cost requires an explicit due time")
            for m in j.modes:
                ticks(m.duration_s, self.time_quantum_s)
                for bound in (m.earliest_start_s, m.latest_finish_s):
                    if bound is not None and ticks(bound, self.time_quantum_s) > horizon:
                        raise ValueError("mode windows must lie on the time grid within the planning horizon")
                if (
                    m.earliest_start_s is not None
                    and m.latest_finish_s is not None
                    and m.latest_finish_s < m.earliest_start_s
                ):
                    raise ValueError("mode window bounds must be ordered")
                if set(m.resources) - resources or set(m.consumables) - self.consumable_budgets.keys():
                    raise ValueError("mode demand references an undeclared resource or consumable")
                if m.duration_s == 0 and (m.resources or m.consumables):
                    raise ValueError("zero-duration milestones cannot consume resources or parts")
            upper_cost += horizon * (j.completion_cost_per_tick + j.tardiness_cost_per_tick) + max(
                m.fixed_cost for m in j.modes
            )
        if upper_cost > 2**50:
            raise ValueError("integer objective exceeds the exact reporting budget")
        for r in self.resources:
            last = 0
            for window in r.calendar:
                a, b = ticks(window.start_s, self.time_quantum_s), ticks(window.finish_s, self.time_quantum_s)
                if a < last or b <= a or b > horizon or window.capacity > r.capacity:
                    raise ValueError(
                        "resource calendars need ordered disjoint windows within the base capacity"
                    )
                last = b
        if len({r.id for r in self.relations}) != len(self.relations) or len(
            {h.id for h in self.holds}
        ) != len(self.holds):
            raise ValueError("relation and hold IDs must be unique")
        for r in self.relations:
            if {r.before, r.after} - operations or r.before == r.after:
                raise ValueError("timing relations require distinct existing operations")
            ticks(r.minimum_lag_s, self.time_quantum_s)
            if r.maximum_lag_s is not None:
                ticks(r.maximum_lag_s, self.time_quantum_s)
                if r.maximum_lag_s < r.minimum_lag_s:
                    raise ValueError("timing lag bounds must be ordered")
        for h in self.holds:
            if (
                {h.from_operation, h.to_operation} - operations
                or not h.resources
                or set(h.resources) - resources
            ):
                raise ValueError("resource holds require known operations and resource demands")
        return self


class PlannedOperation(Contract):
    operation_id: Name
    mode_id: Name
    start_tick: Count
    finish_tick: Count


class ScheduleCandidate(Contract):
    specification_digest: Name
    native_status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    operations: tuple[PlannedOperation, ...] = ()
    objective: float | None = None
    best_bound: float | None = None
    provider: dict = Field(default_factory=dict)
    notes: tuple[str, ...] = ()


class ScheduleResult(Contract):
    schema_version: Literal["pipeline_integrity.schedule.v1"] = "pipeline_integrity.schedule.v1"
    status: Literal["optimal", "feasible", "infeasible", "unknown", "withheld"]
    context: RunContext
    specification_digest: Name
    adapter_sha256: Name
    time_quantum_s: Positive
    objective_unit: Name
    native: ScheduleCandidate
    audit: dict
    timeline: tuple[dict, ...]
    proven_optimal: bool
    origin: Literal["simulation"] = "simulation"
    work_authorized: Literal[False] = False
    failure_probability_calculated: Literal[False] = False
    result_digest: Name

    def check_integrity(self):
        ScheduleResult.model_validate(self.model_dump(mode="python"))
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the schedule receipt has been changed")


class CpSatSchedulingBackend:
    def solve(self, spec: ScheduleRequest):
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()

        def tick(value):
            return ticks(value, spec.time_quantum_s)

        horizon = tick(spec.horizon_s)
        starts, finishes, selected = {}, {}, {}
        intervals = {r.id: [] for r in spec.resources}
        demands = {r.id: [] for r in spec.resources}
        consumables = {name: [] for name in spec.consumable_budgets}
        objective = []
        for j in spec.operations:
            start = model.new_int_var(tick(j.earliest_start_s), horizon, f"start:{j.id}")
            finish = model.new_int_var(0, horizon, f"finish:{j.id}")
            starts[j.id], finishes[j.id] = start, finish
            if j.latest_finish_s is not None:
                model.add(finish <= tick(j.latest_finish_s))
            presence = []
            for m in j.modes:
                active = model.new_bool_var(f"mode:{j.id}:{m.id}")
                selected[j.id, m.id] = active
                interval = model.new_optional_interval_var(
                    start, tick(m.duration_s), finish, active, f"work:{j.id}:{m.id}"
                )
                if m.earliest_start_s is not None:
                    model.add(start >= tick(m.earliest_start_s)).only_enforce_if(active)
                if m.latest_finish_s is not None:
                    model.add(finish <= tick(m.latest_finish_s)).only_enforce_if(active)
                presence.append(active)
                for name, demand in m.resources.items():
                    intervals[name].append(interval)
                    demands[name].append(demand)
                for name, amount in m.consumables.items():
                    consumables[name].append(active * amount)
                objective.append(active * m.fixed_cost)
            model.add_exactly_one(presence)
            objective.append(finish * j.completion_cost_per_tick)
            if j.due_s is not None and j.tardiness_cost_per_tick:
                tardiness = model.new_int_var(0, horizon, f"tardy:{j.id}")
                model.add(tardiness >= finish - tick(j.due_s))
                objective.append(tardiness * j.tardiness_cost_per_tick)

        def at(name, event):
            return starts[name] if event == "start" else finishes[name]

        for r in spec.relations:
            lag = at(r.after, r.after_event) - at(r.before, r.before_event)
            model.add(lag >= tick(r.minimum_lag_s))
            if r.maximum_lag_s is not None:
                model.add(lag <= tick(r.maximum_lag_s))
        for h in spec.holds:
            a, b = at(h.from_operation, h.from_event), at(h.to_operation, h.to_event)
            duration = model.new_int_var(0, horizon, f"hold-duration:{h.id}")
            model.add(duration == b - a)
            interval = model.new_interval_var(a, duration, b, f"hold:{h.id}")
            for name, demand in h.resources.items():
                intervals[name].append(interval)
                demands[name].append(demand)
        for r in spec.resources:
            for i, w in enumerate(r.calendar):
                blocked = r.capacity - w.capacity
                if blocked:
                    interval = model.new_fixed_size_interval_var(
                        tick(w.start_s), tick(w.finish_s) - tick(w.start_s), f"calendar:{r.id}:{i}"
                    )
                    intervals[r.id].append(interval)
                    demands[r.id].append(blocked)
            model.add_cumulative(intervals[r.id], demands[r.id], r.capacity)
        for name, limit in spec.consumable_budgets.items():
            model.add(sum(consumables[name]) <= limit)
        makespan = model.new_int_var(0, horizon, "makespan")
        model.add_max_equality(makespan, list(finishes.values()))
        objective.append(makespan * spec.makespan_cost_per_tick)
        model.minimize(sum(objective))
        invalid = model.validate()
        if invalid:
            return ScheduleCandidate(
                specification_digest=digest(spec), native_status="MODEL_INVALID", notes=(invalid,)
            )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = spec.maximum_seconds
        solver.parameters.relative_gap_limit = spec.relative_gap_limit
        solver.parameters.num_search_workers = spec.workers
        solver.parameters.random_seed = spec.random_seed
        code = solver.solve(model)
        native_status = solver.status_name(code)
        feasible = native_status in {"OPTIMAL", "FEASIBLE"}
        rows = (
            tuple(
                PlannedOperation(
                    operation_id=j.id,
                    mode_id=next(m.id for m in j.modes if solver.boolean_value(selected[j.id, m.id])),
                    start_tick=solver.value(starts[j.id]),
                    finish_tick=solver.value(finishes[j.id]),
                )
                for j in spec.operations
            )
            if feasible
            else ()
        )
        return ScheduleCandidate(
            specification_digest=digest(spec),
            native_status=native_status,
            operations=rows,
            objective=float(solver.objective_value) if feasible else None,
            best_bound=float(solver.best_objective_bound) if native_status != "MODEL_INVALID" else None,
            provider={
                "id": "OR-Tools CP-SAT",
                "version": version("ortools"),
                "workers": spec.workers,
                "seed": spec.random_seed,
                "wall_time_s": solver.wall_time,
            },
        )


def audit_schedule(specification: ScheduleRequest, operations: tuple[PlannedOperation, ...]):
    """Check the original problem using event sweeps rather than the solver's constraint objects."""
    spec = ScheduleRequest.model_validate(specification.model_dump(mode="python"))
    operations = tuple(PlannedOperation.model_validate(r.model_dump(mode="python")) for r in operations)
    rows = {r.operation_id: r for r in operations}
    jobs = {j.id: j for j in spec.operations}
    errors = []
    if len(rows) != len(operations) or set(rows) != set(jobs):
        return dict(valid=False, violations=["operation_identity_set_mismatch"], objective=None)

    def tick(value):
        return ticks(value, spec.time_quantum_s)

    horizon = tick(spec.horizon_s)
    load_events = {r.id: {} for r in spec.resources}
    consumed = {name: 0 for name in spec.consumable_budgets}
    objective = 0

    def event(resource, start, finish, demand):
        if start < finish:
            changes = load_events[resource]
            changes[start] = changes.get(start, 0) + demand
            changes[finish] = changes.get(finish, 0) - demand

    def at(name, which):
        return rows[name].start_tick if which == "start" else rows[name].finish_tick

    for name, row in rows.items():
        job = jobs[name]
        modes = {m.id: m for m in job.modes}
        if row.mode_id not in modes:
            errors.append(f"unknown_mode:{name}")
            continue
        mode = modes[row.mode_id]
        if row.finish_tick - row.start_tick != tick(mode.duration_s):
            errors.append(f"duration:{name}")
        if (
            row.start_tick < tick(job.earliest_start_s)
            or row.finish_tick
            > tick(job.latest_finish_s if job.latest_finish_s is not None else spec.horizon_s)
            or row.finish_tick > horizon
        ):
            errors.append(f"window:{name}")
        if (mode.earliest_start_s is not None and row.start_tick < tick(mode.earliest_start_s)) or (
            mode.latest_finish_s is not None and row.finish_tick > tick(mode.latest_finish_s)
        ):
            errors.append(f"mode_window:{name}")
        for resource, demand in mode.resources.items():
            event(resource, row.start_tick, row.finish_tick, demand)
        for resource, amount in mode.consumables.items():
            consumed[resource] += amount
        objective += mode.fixed_cost + job.completion_cost_per_tick * row.finish_tick
        if job.due_s is not None:
            objective += job.tardiness_cost_per_tick * max(0, row.finish_tick - tick(job.due_s))
    for relation in spec.relations:
        lag = at(relation.after, relation.after_event) - at(relation.before, relation.before_event)
        if lag < tick(relation.minimum_lag_s) or (
            relation.maximum_lag_s is not None and lag > tick(relation.maximum_lag_s)
        ):
            errors.append(f"relation:{relation.id}")
    for hold in spec.holds:
        a, b = at(hold.from_operation, hold.from_event), at(hold.to_operation, hold.to_event)
        if b < a:
            errors.append(f"hold_order:{hold.id}")
        for resource, demand in hold.resources.items():
            event(resource, a, b, demand)
    resource_audit = {}
    for resource in spec.resources:
        changes = load_events[resource.id]
        capacity_changes = {}
        for window in resource.calendar:
            a, b = tick(window.start_s), tick(window.finish_s)
            capacity_changes[a] = capacity_changes.get(a, 0) + window.capacity - resource.capacity
            capacity_changes[b] = capacity_changes.get(b, 0) + resource.capacity - window.capacity
        active, capacity, peak = 0, resource.capacity, 0
        for time in sorted(set(changes) | set(capacity_changes)):
            active += changes.get(time, 0)
            capacity += capacity_changes.get(time, 0)
            peak = max(peak, active)
            if active < 0 or active > capacity:
                errors.append(f"resource_capacity:{resource.id}:{time}")
        resource_audit[resource.id] = dict(peak_demand=peak, final_load=active)
        if active != 0:
            errors.append(f"resource_unreleased:{resource.id}")
    for name, amount in consumed.items():
        if amount > spec.consumable_budgets[name]:
            errors.append(f"consumable_budget:{name}")
    makespan = max(r.finish_tick for r in rows.values())
    objective += spec.makespan_cost_per_tick * makespan
    return dict(
        valid=not errors,
        violations=errors,
        objective=objective,
        makespan_tick=makespan,
        resources=resource_audit,
        consumables=consumed,
    )


def solve_schedule(specification: ScheduleRequest, context: RunContext, *, backend=None):
    spec = ScheduleRequest.model_validate(specification.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    evidence = {e.id for e in context.evidence}
    required = (
        {spec.evidence_id}
        | {m.evidence_id for j in spec.operations for m in j.modes}
        | {r.evidence_id for r in spec.resources}
        | {h.evidence_id for h in spec.holds}
    )
    if len(evidence) != len(context.evidence) or required - evidence:
        raise ValueError("operation timing, resources, holds and objective need unique context evidence")
    native = (CpSatSchedulingBackend() if backend is None else backend).solve(spec)
    native = ScheduleCandidate.model_validate(native.model_dump(mode="python"))
    if native.specification_digest != digest(spec):
        raise ValueError("scheduling backend changed the requested model")
    audit, timeline, optimal = {}, (), False
    if native.native_status in {"OPTIMAL", "FEASIBLE"}:
        audit = audit_schedule(spec, native.operations)
        valid_bound = (
            native.best_bound is not None
            and native.objective is not None
            and 0 <= native.best_bound <= native.objective
        )
        if not audit["valid"] or native.objective != audit["objective"] or not valid_bound:
            status = "withheld"
        else:
            integer_bound = math.ceil(native.best_bound)
            optimal = integer_bound == audit["objective"]
            audit.update(
                integer_lower_bound=integer_bound, absolute_optimality_gap=audit["objective"] - integer_bound
            )
            status = "optimal" if optimal else "feasible"
            timeline = tuple(
                dict(
                    operation_id=r.operation_id,
                    mode_id=r.mode_id,
                    start_tick=r.start_tick,
                    finish_tick=r.finish_tick,
                    start_s=seconds(r.start_tick, spec.time_quantum_s),
                    finish_s=seconds(r.finish_tick, spec.time_quantum_s),
                )
                for r in native.operations
            )
    else:
        status = {"INFEASIBLE": "infeasible", "UNKNOWN": "unknown", "MODEL_INVALID": "withheld"}[
            native.native_status
        ]
        if native.operations:
            status = "withheld"
            audit = {"valid": False, "violations": ["unexpected_assignment_without_native_feasibility"]}
    data = dict(
        status=status,
        context=context.model_dump(mode="json"),
        specification_digest=digest(spec),
        adapter_sha256=file_digest(Path(__file__)),
        time_quantum_s=spec.time_quantum_s,
        objective_unit=spec.objective_unit,
        native=native.model_dump(mode="json"),
        audit=audit,
        timeline=timeline,
        proven_optimal=optimal,
        result_digest="pending",
    )
    initial = ScheduleResult.model_validate_json(json.dumps(data))
    data = initial.model_dump(mode="json", exclude={"result_digest"})
    return ScheduleResult.model_validate_json(json.dumps(data | {"result_digest": digest(data)}))
