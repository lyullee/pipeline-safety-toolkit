"""Finite fixed-batch operating/investment screening through shared native engines."""

from __future__ import annotations

import json
import math
import platform
from copy import deepcopy
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .lifecycle import LifecycleStudy, evaluate_lifecycle
from .scheduling import (
    Operation,
    OperationMode,
    PlanningResource,
    ScheduleRequest,
    ScheduleResult,
    solve_schedule,
    ticks,
)


class TransportWindow(Contract):
    id: Name
    start_s: Nonnegative
    finish_s: Positive
    required_delivered_mass_kg: Nonnegative | None
    forecast_id: Name
    forecast_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    evidence_id: Name
    reviewed: bool = False


class SuppliedOperatingBatch(Contract):
    id: Name
    window_id: Name
    duration_s: Positive
    delivered_mass_kg: Nonnegative | None
    electrical_energy_J: Nonnegative | None
    resource_demands: dict[Name, int] = Field(default_factory=dict)
    performance_source_id: Name
    performance_payload: dict
    performance_digest: Name
    evidence_id: Name
    reviewed: bool = False

    @model_validator(mode="after")
    def receipt(self):
        if digest(self.performance_payload) != self.performance_digest:
            raise ValueError("supplied batch performance payload changed")
        if any(not 0 < v <= 1000000000 for v in self.resource_demands.values()):
            raise ValueError("resource demands must be positive bounded integer units")
        return self


class OperatingPlan(Contract):
    id: Name
    lifecycle_alternative_id: Name
    batches: tuple[SuppliedOperatingBatch, ...] = Field(min_length=1, max_length=1000)
    evidence_id: Name
    fixed_batch_cost_and_service_mapping_reviewed: bool = False

    @model_validator(mode="after")
    def identifiers(self):
        if len({r.id for r in self.batches}) != len(self.batches):
            raise ValueError("operating batch IDs must be unique within a plan")
        return self


class OperatingOptimizationRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    frame_id: Name
    source_port_id: Name
    delivery_port_id: Name
    transport_service_definition_id: Name
    deterministic_case_id: Name
    windows: tuple[TransportWindow, ...] = Field(min_length=1, max_length=1000)
    plans: tuple[OperatingPlan, ...] = Field(min_length=1, max_length=50)
    financial_study: LifecycleStudy
    resources: tuple[PlanningResource, ...] = ()
    electrical_energy_budget_J: Nonnegative | None
    energy_quantum_J: Positive
    time_quantum_s: Positive
    maximum_seconds_per_plan: Positive = Field(default=10, le=300)
    case_forecast_cost_timing_and_equivalence_reviewed: bool = False
    evidence_id: Name

    @model_validator(mode="after")
    def domain(self):
        if (self.asset_id, self.clock_id) != (self.financial_study.asset_id, self.financial_study.clock_id):
            raise ValueError("operating and financial asset/clock differ")
        if self.transport_service_definition_id != self.financial_study.service_definition_id:
            raise ValueError("transport and financial service definitions differ")
        if self.source_port_id == self.delivery_port_id:
            raise ValueError("source and delivered-service ports must be distinct")
        for rows in (self.windows, self.plans, self.resources):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError("window, plan and resource IDs must be unique")
        if len({p.lifecycle_alternative_id for p in self.plans}) != len(self.plans) or {
            p.lifecycle_alternative_id for p in self.plans
        } != {a.id for a in self.financial_study.alternatives}:
            raise ValueError("every supplied plan needs exactly one complete lifecycle alternative")
        windows = {w.id: w for w in self.windows}
        ticks(self.financial_study.horizon_s, self.time_quantum_s)
        for w in self.windows:
            if w.finish_s <= w.start_s or w.finish_s > self.financial_study.horizon_s:
                raise ValueError("ordered transport windows must lie within the financial horizon")
            ticks(w.start_s, self.time_quantum_s)
            ticks(w.finish_s, self.time_quantum_s)
        for plan in self.plans:
            for batch in plan.batches:
                if batch.window_id not in windows:
                    raise ValueError("batch references an unknown transport window")
                if set(batch.resource_demands) - {r.id for r in self.resources}:
                    raise ValueError("batch references an unknown resource")
                ticks(batch.duration_s, self.time_quantum_s)
        return self


def operating_boundary_metadata(q):
    # Financial valuation receipts can already contain this stable boundary.
    # Do not make their enclosing request digest recursively depend on itself.
    return {
        "id": q.id,
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "frame_id": q.frame_id,
        "ports": [q.source_port_id, q.delivery_port_id],
        "transport_service_definition_id": q.transport_service_definition_id,
        "deterministic_case_id": q.deterministic_case_id,
        "time_basis": "all window, batch and payment times relative to the same valuation-clock zero",
    }


def _energy_units(value, quantum):
    units = Decimal(str(value)) / Decimal(str(quantum))
    if units != units.to_integral_value() or not 0 <= units <= 1000000000:
        raise ValueError("energy must lie exactly on the declared bounded integer quantum; no rounding")
    return int(units)


def operating_batch_binding(q, batch):
    window = next(w for w in q.windows if w.id == batch.window_id)
    return {
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "frame_id": q.frame_id,
        "ports": [q.source_port_id, q.delivery_port_id],
        "case_id": q.deterministic_case_id,
        "transport_service_definition_id": q.transport_service_definition_id,
        "forecast_id": window.forecast_id,
        "forecast_sha256": window.forecast_sha256,
        "window_id": window.id,
        "window_start_s": window.start_s,
        "window_finish_s": window.finish_s,
        "required_delivered_mass_kg": window.required_delivered_mass_kg,
        "batch_id": batch.id,
        "performance_source_id": batch.performance_source_id,
        "duration_s": batch.duration_s,
        "delivered_mass_kg": batch.delivered_mass_kg,
        "electrical_energy_J": batch.electrical_energy_J,
        "resource_demands": batch.resource_demands,
    }


def _inputs(request, context):
    q = OperatingOptimizationRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "operating_boundary"
    ) != operating_boundary_metadata(q):
        raise ValueError("operating asset/clock/frame/ports/case context differs")
    needed = {q.evidence_id, q.financial_study.finance_evidence_id}
    for rows in (q.windows, q.plans, q.resources, q.financial_study.alternatives):
        needed.update(row.evidence_id for row in rows)
    needed.update(b.evidence_id for p in q.plans for b in p.batches)
    needed.update(
        e.evidence_id
        for a in q.financial_study.alternatives
        for e in (*a.cost_events, *a.recurring_costs, *a.emission_events)
    )
    available = [e.id for e in c.evidence]
    if len(available) != len(set(available)) or needed - set(available):
        raise ValueError("forecast/performance/cost/resource evidence is absent or duplicated")
    evidence = {e.id: e for e in c.evidence}
    for window in q.windows:
        actual_hash = evidence[window.evidence_id].sha256
        if actual_hash is not None and actual_hash != window.forecast_sha256:
            raise ValueError("forecast checksum conflicts with its actual selected evidence")
    for plan in q.plans:
        for batch in plan.batches:
            if batch.performance_payload.get("operating_batch_binding") != operating_batch_binding(q, batch):
                raise ValueError(
                    "batch mass/energy/window/forecast/ports differ from its imported performance binding"
                )
    return q, c


def optimize_operating_plans(request, context):
    """Use shared lifecycle and CP-SAT engines; do not infer flow or energy physics."""
    q, c = _inputs(request, context)
    body = {
        "schema": "pipeline_integrity.operating_optimization.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "owner_source_sha256": {
            "lifecycle": file_digest(Path(__file__).with_name("lifecycle.py")),
            "scheduling": file_digest(Path(__file__).with_name("scheduling.py")),
        },
        "runtime": {"python": platform.python_version(), "pydantic": version("pydantic")},
        "status": "needs_data",
        "plan_results": [],
        "financial_record": None,
        "selected_plan_ids": None,
        "finite_supplied_plan_optimum_proven": False,
        "continuous_dispatch_or_hydraulics_optimized": False,
        "uncertainty_or_correlation_solved": False,
        "investment_authorized": False,
        "verification_state": "execution is not regression or field qualification",
        "issues": [],
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    missing = []
    if not q.case_forecast_cost_timing_and_equivalence_reviewed:
        missing.append("reviewed common forecast, fixed batch cost timing and equivalence")
    if q.electrical_energy_budget_J is None:
        missing.append("electrical_energy_budget_J")
    for w in q.windows:
        if not w.reviewed or w.required_delivered_mass_kg is None:
            missing.append("window:" + w.id)
    for p in q.plans:
        if not p.fixed_batch_cost_and_service_mapping_reviewed:
            missing.append("plan:" + p.id)
        for b in p.batches:
            if not b.reviewed or b.delivered_mass_kg is None or b.electrical_energy_J is None:
                missing.append("batch:" + p.id + ":" + b.id)
    if missing:
        body["issues"] = missing
        return seal()
    finance = evaluate_lifecycle(q.financial_study, c)
    body["financial_record"] = finance
    if set(finance["alternatives"]) != {a.id for a in q.financial_study.alternatives}:
        body.update(status="out_of_scope", issues=["shared lifecycle owner did not return all alternatives"])
        return seal()
    windows = {w.id: w for w in q.windows}
    unknown = False
    feasible = []
    try:
        budget = _energy_units(q.electrical_energy_budget_J, q.energy_quantum_J)
        for plan in q.plans:
            balances = []
            for w in q.windows:
                supplied = math.fsum(b.delivered_mass_kg for b in plan.batches if b.window_id == w.id)
                if not math.isfinite(supplied):
                    raise ValueError("delivered mass sum is not representable")
                balances.append(
                    {
                        "window_id": w.id,
                        "required_mass_kg": w.required_delivered_mass_kg,
                        "supplied_delivered_mass_kg": supplied,
                        "shortfall_kg": max(0, w.required_delivered_mass_kg - supplied),
                    }
                )
            cost = finance["alternatives"][plan.lifecycle_alternative_id]
            row = {
                "plan_id": plan.id,
                "window_balances": balances,
                "schedule_specification": None,
                "schedule_record": None,
                "present_cost": cost["present_cost"],
                "cost_status": cost["cost_status"],
                "status": "needs_data",
                "total_electrical_energy_J": math.fsum(b.electrical_energy_J for b in plan.batches),
                "electrical_energy_budget_J": q.electrical_energy_budget_J,
            }
            body["plan_results"].append(row)
            if any(r["shortfall_kg"] > 0 for r in balances):
                row["status"] = "infeasible_delivered_service"
                continue
            operations = tuple(
                Operation(
                    id=b.id,
                    earliest_start_s=windows[b.window_id].start_s,
                    latest_finish_s=windows[b.window_id].finish_s,
                    modes=(
                        OperationMode(
                            id="fixed_supplied_batch",
                            duration_s=b.duration_s,
                            resources=b.resource_demands,
                            consumables={
                                "electrical_energy_quantum": _energy_units(
                                    b.electrical_energy_J, q.energy_quantum_J
                                )
                            }
                            if b.electrical_energy_J
                            else {},
                            evidence_id=b.evidence_id,
                        ),
                    ),
                )
                for b in plan.batches
            )
            spec = ScheduleRequest(
                horizon_s=q.financial_study.horizon_s,
                time_quantum_s=q.time_quantum_s,
                operations=operations,
                resources=q.resources,
                consumable_budgets={"electrical_energy_quantum": budget},
                makespan_cost_per_tick=0,
                objective_unit="fixed_plan_feasibility_zero_objective",
                evidence_id=plan.evidence_id,
                maximum_seconds=q.maximum_seconds_per_plan,
                workers=1,
                random_seed=0,
            )
            row["schedule_specification"] = spec.model_dump(mode="json")
            scheduled = solve_schedule(spec, c)
            row["schedule_record"] = scheduled.model_dump(mode="json")
            if scheduled.status == "infeasible":
                row["status"] = "infeasible_supplied_schedule"
                continue
            if scheduled.status not in {"optimal", "feasible"} or cost["cost_status"] != "computed":
                unknown = True
                continue
            row["status"] = "feasible_supplied_plan"
            feasible.append(row)
    except (ArithmeticError, ValueError) as exc:
        body.update(status="out_of_scope", issues=[str(exc)])
        return seal()
    if unknown:
        body["issues"] = [
            "an unresolved cost or native feasibility result may change the finite-plan optimum"
        ]
    elif not feasible:
        body.update(status="no_feasible_supplied_plan", selected_plan_ids=[])
    else:
        minimum = min(row["present_cost"] for row in feasible)
        tolerance = q.financial_study.absolute_cost_tie_tolerance
        body.update(
            status="computed_diagnostic",
            selected_plan_ids=sorted(
                row["plan_id"] for row in feasible if row["present_cost"] <= minimum + tolerance
            ),
            finite_supplied_plan_optimum_proven=True,
        )
    return seal()


def verify_operating_optimization_record(record, *, request=None, context=None):
    """Recompute through the current native owners; unsigned replay is not authentication."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("operating record seal changed")
    q = OperatingOptimizationRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and request != q or context is not None and context != c:
        raise ValueError("operating caller request/context differs")

    def stable(value):
        value = deepcopy(value)
        value.pop("record_digest", None)
        for row in value["plan_results"]:
            schedule = row["schedule_record"]
            if schedule is None:
                continue
            ScheduleResult.model_validate_json(json.dumps(schedule, allow_nan=False)).check_integrity()
            # Elapsed time is telemetry, not an engineering or optimality output.
            timing = schedule["native"]["provider"].pop("wall_time_s", None)
            if timing is not None and (not math.isfinite(timing) or timing < 0):
                raise ValueError("invalid native runtime telemetry")
            schedule["result_digest"] = digest({k: v for k, v in schedule.items() if k != "result_digest"})
        return value

    current = optimize_operating_plans(q, c)
    if stable(current) != stable(record):
        raise ValueError("operating costs, assignment, owner receipts or provenance differ on replay")
    return True
