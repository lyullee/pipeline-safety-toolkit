"""Deterministic screening of explicitly supplied finite construction-route candidates."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, RunContext, digest
from .datasets import file_digest
from .lifecycle import MoneyBasis

Metric = Literal["length_m", "earthwork_m3", "disturbed_area_m2", "emissions_kg_co2e", "cost", "duration_s"]


class RouteQuote(Contract):
    id: Name
    scope_component_id: Name
    quantity: Nonnegative | None
    quantity_unit: Literal["m", "m2", "m3", "count", "s", "kg", "lump"]
    unit_rate: Nonnegative | None
    evidence_id: Name


class RouteCandidate(Contract):
    id: Name
    geometry_definition_id: Name
    length_m: Nonnegative | None
    earthwork_m3: Nonnegative | None
    disturbed_area_m2: Nonnegative | None
    emissions_kg_co2e: Nonnegative | None
    duration_s: Nonnegative | None
    crossing_counts: dict[Name, int | None]
    conditions: dict[Name, bool | None]
    cost_items: tuple[RouteQuote, ...]
    evidence_id: Name
    geometry_and_quantities_reviewed: bool = False
    cost_inventory_reviewed: bool = False
    environmental_inventory_reviewed: bool = False
    conditions_reviewed: bool = False

    @model_validator(mode="after")
    def consistency(self):
        if any(v is not None and v < 0 for v in self.crossing_counts.values()):
            raise ValueError("crossing counts must be nonnegative or explicitly unknown")
        for field in ("id", "scope_component_id"):
            if len({getattr(q, field) for q in self.cost_items}) != len(self.cost_items):
                raise ValueError("cost IDs and scope components must be unique within a route")
        return self


class RoutePlanningRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    frame_id: Name
    coordinate_definition_id: Name
    start_port_id: Name
    finish_port_id: Name
    common_service_definition_id: Name
    common_cost_boundary_id: Name
    common_environmental_boundary_id: Name
    emission_metric_id: Name
    supplied_case_id: Name
    money_basis: MoneyBasis
    candidates: tuple[RouteCandidate, ...] = Field(min_length=1, max_length=500)
    objectives: tuple[Metric, ...] = Field(min_length=1, max_length=6)
    maximum_metrics: dict[Metric, Nonnegative] = Field(default_factory=dict)
    maximum_crossings: dict[Name, int] = Field(default_factory=dict)
    required_conditions: tuple[Name, ...] = ()
    selection_mode: Literal["pareto_only", "lexicographic"] = "pareto_only"
    selection_order: tuple[Metric, ...] = ()
    case_and_comparability_reviewed: bool = False
    deterministic_supplied_case_only: Literal[True] = True
    evidence_id: Name

    @model_validator(mode="after")
    def consistency(self):
        if self.start_port_id == self.finish_port_id:
            raise ValueError("route boundary ports must be distinct")
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError("route candidate IDs must be unique")
        if len(set(self.objectives)) != len(self.objectives) or len(set(self.required_conditions)) != len(
            self.required_conditions
        ):
            raise ValueError("objective and condition names must be unique")
        if any(v < 0 for v in self.maximum_crossings.values()):
            raise ValueError("crossing constraints must be nonnegative")
        if self.selection_mode == "lexicographic":
            if len(self.selection_order) != len(self.objectives) or set(self.selection_order) != set(
                self.objectives
            ):
                raise ValueError("lexicographic order must contain every objective exactly once")
        elif self.selection_order:
            raise ValueError("selection order has no meaning in Pareto-only mode")
        return self


def route_boundary_metadata(q: RoutePlanningRequest):
    return {
        "request_id": q.id,
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "time_s": q.time_s,
        "frame_id": q.frame_id,
        "coordinate_definition_id": q.coordinate_definition_id,
        "ports": [q.start_port_id, q.finish_port_id],
        "request_digest": digest(q.model_dump(mode="json")),
    }


def _inputs(request, context):
    q = RoutePlanningRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("route asset/clock differs from run context")
    if c.metadata.get("route_boundary") != route_boundary_metadata(q):
        raise ValueError("route frame/port/case/candidate context binding differs")
    evidence = [e.id for e in c.evidence]
    needed = {q.evidence_id} | {r.evidence_id for r in q.candidates}
    needed |= {line.evidence_id for r in q.candidates for line in r.cost_items}
    if len(evidence) != len(set(evidence)) or needed - set(evidence):
        raise ValueError("route and quote evidence is absent or duplicated")
    return q, c


def evaluate_routes(request: RoutePlanningRequest, context: RunContext):
    """Screen a finite supplied case. Unknown contenders prevent a final choice."""
    q, c = _inputs(request, context)
    body = {
        "schema": "pipeline_integrity.route_planning.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "runtime": {"python": platform.python_version(), "pydantic": version("pydantic")},
        "status": "needs_data",
        "candidate_results": [],
        "pareto_candidate_ids": None,
        "selected_candidate_ids": None,
        "global_terrain_optimum": False,
        "permit_compliance_established": False,
        "uncertainty_or_correlation_solved": False,
        "qualification": "finite deterministic supplied-route screening",
        "verification_state": "execution is not regression or field qualification",
        "issues": [],
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    if not q.case_and_comparability_reviewed:
        body["issues"] = [
            "common service, monetary/environmental boundaries and supplied deterministic case need review"
        ]
        return seal()
    feasible = []
    pending = False
    used_metrics = set(q.objectives) | set(q.maximum_metrics)
    for candidate in q.candidates:
        metrics = {
            key: getattr(candidate, key)
            for key in ("length_m", "earthwork_m3", "disturbed_area_m2", "emissions_kg_co2e", "duration_s")
        }
        ledger = []
        unknown = []
        violations = []
        try:
            for line in candidate.cost_items:
                amount = (
                    None
                    if line.quantity is None or line.unit_rate is None
                    else line.quantity * line.unit_rate
                )
                if amount is not None and not math.isfinite(amount):
                    raise ValueError("cost multiplication is not representable")
                ledger.append(
                    {
                        "quote_id": line.id,
                        "scope_component_id": line.scope_component_id,
                        "quantity": line.quantity,
                        "quantity_unit": line.quantity_unit,
                        "unit_rate": line.unit_rate,
                        "amount": amount,
                        "evidence_id": line.evidence_id,
                    }
                )
            metrics["cost"] = (
                math.fsum(line["amount"] for line in ledger)
                if all(line["amount"] is not None for line in ledger)
                else None
            )
            if metrics["cost"] is not None and not math.isfinite(metrics["cost"]):
                raise ValueError("total cost is not representable")
        except (OverflowError, ValueError) as exc:
            body["status"] = "out_of_scope"
            body["issues"] = [str(exc)]
            return seal()
        if not candidate.geometry_and_quantities_reviewed:
            unknown.append("geometry_and_quantities_reviewed")
        if not candidate.cost_inventory_reviewed or metrics["cost"] is None:
            unknown.append("reviewed complete cost inventory")
        if not candidate.environmental_inventory_reviewed:
            unknown.append("environmental_inventory_reviewed")
        for key in used_metrics:
            if metrics[key] is None:
                unknown.append(key)
        if not candidate.conditions_reviewed:
            unknown.append("conditions_reviewed")
        for condition in q.required_conditions:
            value = candidate.conditions.get(condition)
            if value is None or not candidate.conditions_reviewed:
                unknown.append("condition:" + condition)
            elif not value:
                violations.append("condition:" + condition)
        for kind, maximum in q.maximum_crossings.items():
            count = candidate.crossing_counts.get(kind)
            if count is None or not candidate.geometry_and_quantities_reviewed:
                unknown.append("crossing:" + kind)
            elif count > maximum:
                violations.append("crossing:" + kind)
        for key, maximum in q.maximum_metrics.items():
            reviewed = (
                candidate.cost_inventory_reviewed
                if key == "cost"
                else candidate.environmental_inventory_reviewed
                if key == "emissions_kg_co2e"
                else candidate.geometry_and_quantities_reviewed
            )
            if metrics[key] is not None and reviewed and metrics[key] > maximum:
                violations.append(key)
        state = (
            "infeasible_supplied_constraints"
            if violations
            else "needs_data"
            if unknown
            else "feasible_supplied_constraints"
        )
        result = {
            "candidate_id": candidate.id,
            "status": state,
            "metrics": metrics,
            "cost_ledger": ledger,
            "crossing_counts": candidate.crossing_counts,
            "violations": violations,
            "missing": sorted(set(unknown)),
        }
        body["candidate_results"].append(result)
        if violations:
            continue
        if unknown:
            pending = True
        else:
            feasible.append(result)
    if pending:
        body["issues"] = [
            "an unresolved candidate may change the comparison; final Pareto set and choice withheld"
        ]
        return seal()
    front = []
    for item in feasible:
        values = item["metrics"]
        dominated = any(
            all(other["metrics"][key] <= values[key] for key in q.objectives)
            and any(other["metrics"][key] < values[key] for key in q.objectives)
            for other in feasible
        )
        if not dominated:
            front.append(item)
    body["pareto_candidate_ids"] = sorted(item["candidate_id"] for item in front)
    if not front:
        body.update(status="no_feasible_supplied_candidate", selected_candidate_ids=[])
    else:
        body["status"] = "computed_diagnostic"
        if q.selection_mode == "lexicographic":
            best = min(tuple(item["metrics"][key] for key in q.selection_order) for item in front)
            body["selected_candidate_ids"] = sorted(
                item["candidate_id"]
                for item in front
                if tuple(item["metrics"][key] for key in q.selection_order) == best
            )
    return seal()


def verify_route_record(record, *, request=None, context=None):
    """Replay the current implementation; an unsigned digest is not authentication."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("route record seal changed")
    q = RoutePlanningRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and request != q or context is not None and context != c:
        raise ValueError("route caller request/context differs")
    if evaluate_routes(q, c) != record:
        raise ValueError(
            "route quantities, constraints, Pareto set, selection or source pins differ on replay"
        )
    return True
