"""Declared-service lifecycle cost/emission comparisons using the existing cashflow registry."""

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest

CostCategory = Literal[
    "capital",
    "inspection",
    "maintenance",
    "replacement",
    "energy",
    "downtime",
    "decommissioning",
    "salvage",
    "risk_expectation",
    "carbon_price",
    "other",
]


class MoneyBasis(Contract):
    currency: Name
    base_year: int = Field(ge=1900)
    prices: Literal["constant", "nominal"]


class Money(Contract):
    amount: float | None
    basis: MoneyBasis


class ValuationSource(Contract):
    kind: Literal["energy", "schedule", "risk"]
    valuation_status: Literal["computed", "needs_data", "out_of_scope"]
    source_payload: dict
    source_digest: str
    event_digest: str

    @model_validator(mode="after")
    def sealed(self):
        if digest(self.source_payload) != self.source_digest:
            raise ValueError("valuation source payload changed")
        if self.source_payload.get("valuation_status") != self.valuation_status:
            raise ValueError("valuation source status differs from its sealed payload")
        return self


class CostEvent(Contract):
    id: Name
    time_s: Nonnegative
    category: CostCategory
    money: Money
    evidence_id: Name
    source: ValuationSource | None = None

    @model_validator(mode="after")
    def bound(self):
        if self.source is not None and self.source.event_digest != digest(
            self.model_dump(mode="json", exclude={"source"})
        ):
            raise ValueError("cost amount, timing, basis or definition changed after source binding")
        return self


class RecurringCost(Contract):
    id: Name
    first_time_s: Nonnegative
    interval_s: Positive
    occurrences: int = Field(ge=1, le=1000000)
    category: CostCategory
    base_quote: Money
    escalation_per_year: float = Field(gt=-1)
    escalation_basis: Literal["real", "nominal"]
    evidence_id: Name


class EmissionEvent(Contract):
    id: Name
    time_s: Nonnegative
    mass_kg_co2e: float | None
    metric_id: Name
    evidence_id: Name
    source: ValuationSource | None = None

    @model_validator(mode="after")
    def bound(self):
        if self.source is not None and self.source.event_digest != digest(
            self.model_dump(mode="json", exclude={"source"})
        ):
            raise ValueError("emission quantity, timing or metric changed after source binding")
        return self


class LifecycleAlternative(Contract):
    id: Name
    service_definition_id: Name
    cost_boundary_id: Name
    emissions_boundary_id: Name
    cost_events: tuple[CostEvent, ...]
    recurring_costs: tuple[RecurringCost, ...]
    emission_events: tuple[EmissionEvent, ...]
    evidence_id: Name
    cost_inventory_reviewed: bool = False
    emissions_inventory_reviewed: bool = False

    @model_validator(mode="after")
    def identifiers(self):
        ids = [row.id for row in (*self.cost_events, *self.recurring_costs)]
        if len(ids) != len(set(ids)) or len({r.id for r in self.emission_events}) != len(
            self.emission_events
        ):
            raise ValueError("cost component and emission event IDs must be unique within each alternative")
        risk_intervals = []
        for event in self.cost_events:
            if event.source is None or event.source.kind != "risk":
                continue
            source = event.source.source_payload
            frequency = source.get("source_frequency")
            if frequency is None:
                continue
            spec = source["specification"]
            key = (frequency["binding_digest"], spec["loss_component_id"])
            start, finish = spec["interval_start_s"], spec["interval_finish_s"]
            if any(k == key and max(a, start) < min(b, finish) for k, a, b in risk_intervals):
                raise ValueError("the same risk frequency and loss component cover overlapping intervals")
            risk_intervals.append((key, start, finish))
        return self


class LifecycleStudy(Contract):
    asset_id: Name
    clock_id: Name
    horizon_s: Positive
    year_duration_s: Positive
    money_basis: MoneyBasis
    annual_discount_rate: float | None = Field(gt=-1)
    discount_rate_basis: Literal["real", "nominal"]
    service_definition_id: Name
    cost_boundary_id: Name
    emissions_boundary_id: Name
    emission_metric_id: Name
    alternatives: tuple[LifecycleAlternative, ...] = Field(min_length=1, max_length=1000)
    baseline_alternative_id: Name
    finance_evidence_id: Name
    functional_equivalence_reviewed: bool = False
    absolute_cost_tie_tolerance: Nonnegative = 0.01
    absolute_emission_tie_tolerance_kg: Nonnegative = 1e-6
    horizon_tolerance_s: Nonnegative = 1e-6
    maximum_generated_cost_events: int = Field(default=100000, ge=1, le=1000000)

    @model_validator(mode="after")
    def consistent(self):
        ids = {a.id for a in self.alternatives}
        if len(ids) != len(self.alternatives) or self.baseline_alternative_id not in ids:
            raise ValueError("unique alternatives and a declared baseline are required")
        basis = "real" if self.money_basis.prices == "constant" else "nominal"
        if self.discount_rate_basis != basis:
            raise ValueError("constant prices require real rates; nominal prices require nominal rates")
        for alternative in self.alternatives:
            if (
                alternative.service_definition_id,
                alternative.cost_boundary_id,
                alternative.emissions_boundary_id,
            ) != (self.service_definition_id, self.cost_boundary_id, self.emissions_boundary_id):
                raise ValueError("alternatives must share service and declared cost/emission boundaries")
            for event in alternative.cost_events:
                if (
                    event.money.basis != self.money_basis
                    or event.time_s > self.horizon_s + self.horizon_tolerance_s
                ):
                    raise ValueError("cost currency/year/price basis or payment time differs from the study")
            for recurring in alternative.recurring_costs:
                if recurring.base_quote.basis != self.money_basis or recurring.escalation_basis != basis:
                    raise ValueError("recurring quote and escalation basis must match the study")
                if (
                    recurring.first_time_s + (recurring.occurrences - 1) * recurring.interval_s
                    > self.horizon_s + self.horizon_tolerance_s
                ):
                    raise ValueError(
                        "every explicitly requested recurrence must lie within the study horizon"
                    )
            for event in alternative.emission_events:
                if (
                    event.metric_id != self.emission_metric_id
                    or event.time_s > self.horizon_s + self.horizon_tolerance_s
                ):
                    raise ValueError("emission metric or inventory time differs from the study")
        return self


def _evidence(study, context):
    if study.asset_id != context.asset_id or study.clock_id != context.clock_id:
        raise ValueError("lifecycle study asset or valuation clock differs from context")
    available = {e.id for e in context.evidence}
    required = {study.finance_evidence_id}
    for alternative in study.alternatives:
        required.add(alternative.evidence_id)
        required.update(
            row.evidence_id
            for row in (*alternative.cost_events, *alternative.recurring_costs, *alternative.emission_events)
        )
        for event in (*alternative.cost_events, *alternative.emission_events):
            if event.source is not None and event.source.source_payload.get(
                "valuation_context"
            ) != context.model_dump(mode="json"):
                raise ValueError(
                    "bound cost/emission valuation context changed; explicitly rebind its source"
                )
    if required - available or len(available) != len(context.evidence):
        raise ValueError("lifecycle finance, inventory and service evidence must exist and be unique")


def _record(study, context, status, alternatives, *, comparisons=None, issues=()):
    body = dict(
        schema="pipeline_integrity.lifecycle.v1",
        study=study.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=status,
        issues=list(issues),
        alternatives=alternatives,
        comparisons=comparisons,
        implementation_sha256=file_digest(Path(__file__)),
        output_origin="simulation",
        cost_sign="positive_outflow_negative_credit",
        emissions_discounted=False,
        currency_conversion_or_tax_rules_inferred=False,
        service_life_or_replacement_need_predicted=False,
        investment_or_work_authorization=False,
        failure_probability_inferred=False,
    )
    body["record_digest"] = digest(body)
    return body


def _cost_ledger(alternative, study):
    rows = [
        dict(
            id=f"event:{e.id}",
            time_s=e.time_s,
            category=e.category,
            amount=e.money.amount,
            evidence_id=e.evidence_id,
        )
        for e in alternative.cost_events
    ]
    for recurring in alternative.recurring_costs:
        for index in range(recurring.occurrences):
            time = recurring.first_time_s + index * recurring.interval_s
            amount = recurring.base_quote.amount
            original = amount
            if amount is not None and amount != 0 and recurring.escalation_per_year != 0:
                amount *= math.exp(math.log1p(recurring.escalation_per_year) * time / study.year_duration_s)
            if amount is not None and (not math.isfinite(amount) or (amount == 0 and original != 0)):
                raise ArithmeticError("escalated recurring cost is outside finite arithmetic")
            rows.append(
                dict(
                    id=f"recurring:{recurring.id}:{index}",
                    component_id=recurring.id,
                    time_s=time,
                    category=recurring.category,
                    amount=amount,
                    evidence_id=recurring.evidence_id,
                )
            )
    return rows


def evaluate_lifecycle(specification: LifecycleStudy, context: RunContext, *, engine=None):
    """Compare declared inventories on identical service/basis; reuse one cashflow calculation per alternative."""
    study = LifecycleStudy.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    _evidence(study, context)
    event_count = sum(
        len(a.cost_events) + sum(r.occurrences for r in a.recurring_costs) for a in study.alternatives
    )
    if event_count > study.maximum_generated_cost_events:
        return _record(study, context, "out_of_scope", {}, issues=("generated cost-event budget exceeded",))
    if engine is None:
        from . import create_registry

        engine = create_registry()
    alternatives = {}
    for alternative in study.alternatives:
        issues, native = [], None
        try:
            ledger = _cost_ledger(alternative, study)
        except (ArithmeticError, OverflowError):
            alternatives[alternative.id] = dict(
                cost_status="out_of_scope",
                emissions_status="needs_data",
                cost_ledger=[],
                present_cost=None,
                emission_mass_kg_co2e=None,
                issues=["cost escalation exceeded finite arithmetic"],
                native_cashflow=None,
            )
            continue
        cost_status = "computed"
        if (
            not study.functional_equivalence_reviewed
            or not alternative.cost_inventory_reviewed
            or study.annual_discount_rate is None
            or any(row["amount"] is None for row in ledger)
        ):
            cost_status = "needs_data"
            issues.append("cost inventory, functional equivalence, amount or discount rate is unresolved")
        source_states = {e.source.valuation_status for e in alternative.cost_events if e.source is not None}
        if "out_of_scope" in source_states:
            cost_status = "out_of_scope"
            issues.append("a bound cost source is outside its supported range")
        elif "needs_data" in source_states:
            cost_status = "needs_data"
        present, by_category = None, {}
        if cost_status == "computed":
            if ledger:
                result = engine.evaluate(
                    "economics.cashflow",
                    dict(
                        cashflows=tuple(row["amount"] for row in ledger),
                        times_s=tuple(row["time_s"] for row in ledger),
                        discount_rate_per_period=study.annual_discount_rate,
                        period_duration_s=study.year_duration_s,
                        currency=study.money_basis.currency,
                        price_year=study.money_basis.base_year,
                    ),
                    context,
                )
                native = result.model_dump(mode="json")
                cost_status = result.status
                if cost_status == "computed":
                    present = result.values["net_present_value"]
                    for row, value in zip(ledger, result.values["discounted_cashflows"], strict=True):
                        row["present_cost"] = value
                    try:
                        for category in {row["category"] for row in ledger}:
                            by_category[category] = math.fsum(
                                row["present_cost"] for row in ledger if row["category"] == category
                            )
                    except (ArithmeticError, OverflowError):
                        cost_status, present, by_category = "out_of_scope", None, {}
                        issues.append("category cost sum exceeded finite arithmetic")
            else:
                present = 0.0
        emission_status = (
            "computed"
            if study.functional_equivalence_reviewed
            and alternative.emissions_inventory_reviewed
            and all(row.mass_kg_co2e is not None for row in alternative.emission_events)
            else "needs_data"
        )
        mass = None
        emission_source_states = {
            e.source.valuation_status for e in alternative.emission_events if e.source is not None
        }
        if "out_of_scope" in emission_source_states:
            emission_status = "out_of_scope"
        elif "needs_data" in emission_source_states:
            emission_status = "needs_data"
        if emission_status == "computed":
            try:
                mass = math.fsum(row.mass_kg_co2e for row in alternative.emission_events)
                if not math.isfinite(mass):
                    raise ArithmeticError("emission sum is nonfinite")
            except (ArithmeticError, OverflowError):
                emission_status = "out_of_scope"
                mass = None
        alternatives[alternative.id] = dict(
            cost_status=cost_status,
            emissions_status=emission_status,
            cost_ledger=ledger,
            present_cost=present,
            present_cost_by_category=by_category,
            emission_mass_kg_co2e=mass,
            issues=issues,
            native_cashflow=native,
            empty_cost_inventory_explicitly_declared=not ledger,
            empty_emissions_inventory_explicitly_declared=not alternative.emission_events,
        )
    cost_ready = all(row["cost_status"] == "computed" for row in alternatives.values())
    emission_ready = all(row["emissions_status"] == "computed" for row in alternatives.values())
    comparisons = dict(
        least_present_cost_ids=None,
        least_emission_ids=None,
        nondominated_ids=None,
        present_savings_vs_baseline=None,
    )
    if cost_ready:
        minimum = min(row["present_cost"] for row in alternatives.values())
        comparisons["least_present_cost_ids"] = [
            name
            for name, row in alternatives.items()
            if row["present_cost"] <= minimum + study.absolute_cost_tie_tolerance
        ]
        baseline = alternatives[study.baseline_alternative_id]["present_cost"]
        savings = {name: baseline - row["present_cost"] for name, row in alternatives.items()}
        if all(math.isfinite(value) for value in savings.values()):
            comparisons["present_savings_vs_baseline"] = savings
    if emission_ready:
        minimum = min(row["emission_mass_kg_co2e"] for row in alternatives.values())
        comparisons["least_emission_ids"] = [
            name
            for name, row in alternatives.items()
            if row["emission_mass_kg_co2e"] <= minimum + study.absolute_emission_tie_tolerance_kg
        ]
    if cost_ready and emission_ready:
        comparisons["nondominated_ids"] = [
            name
            for name, row in alternatives.items()
            if not any(
                other["present_cost"] <= row["present_cost"]
                and other["emission_mass_kg_co2e"] <= row["emission_mass_kg_co2e"]
                and (
                    other["present_cost"] < row["present_cost"]
                    or other["emission_mass_kg_co2e"] < row["emission_mass_kg_co2e"]
                )
                for key, other in alternatives.items()
                if key != name
            )
        ]
    statuses = {row[key] for row in alternatives.values() for key in ("cost_status", "emissions_status")}
    status = (
        "computed"
        if statuses == {"computed"}
        else "needs_data"
        if "needs_data" in statuses
        else "out_of_scope"
    )
    arithmetic_issues = ()
    if cost_ready and comparisons["present_savings_vs_baseline"] is None:
        status = "out_of_scope"
        arithmetic_issues = ("incremental savings exceeded finite arithmetic",)
    return _record(study, context, status, alternatives, comparisons=comparisons, issues=arithmetic_issues)
