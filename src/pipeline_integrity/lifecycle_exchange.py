"""Explicit observation, schedule and risk-to-valuation mappings; upstream engineering meanings stay intact."""

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import _aligned_frames
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .lifecycle import CostEvent, EmissionEvent, Money, MoneyBasis, ValuationSource
from .quadrature import integrate_linear_interval
from .risk_exchange import RiskFrequency
from .scheduling import ScheduleResult
from .units import convert_quantity


def _bind(event, kind, payload, *, unsupported=False):
    value = event.money.amount if isinstance(event, CostEvent) else event.mass_kg_co2e
    status = "out_of_scope" if unsupported else "needs_data" if value is None else "computed"
    payload = {**payload, "valuation_status": status}
    source = ValuationSource(
        kind=kind,
        valuation_status=status,
        source_payload=payload,
        source_digest=digest(payload),
        event_digest=digest(event.model_dump(mode="json", exclude={"source"})),
    )
    return type(event).model_validate(event.model_copy(update={"source": source}).model_dump())


def _seal(body):
    return {**body, "record_digest": digest(body)}


class PowerIntegration(Contract):
    channel: Name
    maximum_sample_interval_s: Positive
    time_tolerance_s: Nonnegative = 1e-9
    sign_convention: Literal["nonnegative_consumption", "signed_import_positive"]
    evidence_id: Name


def integrate_power_frames(observations, specification: PowerIntegration, context: RunContext):
    """Integrate piecewise-linear signed samples over covered intervals; do not synthesize missing tails."""
    spec = PowerIntegration.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    frames, channels = _aligned_frames(
        tuple(SignalFrame.model_validate(f.model_dump()) for f in observations)
    )
    if len(frames) < 2 or spec.channel not in channels or frames[0].clock_id != context.clock_id:
        raise ValueError("power integration requires a matching clock, channel and at least two samples")
    if spec.evidence_id not in {e.id for e in context.evidence}:
        raise ValueError("power observation evidence is absent")
    unit = frames[0].units[spec.channel]
    convert_quantity(0.0, unit, "W")
    powers = [
        None if f.values[spec.channel] is None else convert_quantity(f.values[spec.channel], unit, "W")
        for f in frames
    ]
    imported, exported, durations, skipped = [], [], [], []
    issues = []
    for index, (first, last) in enumerate(zip(frames, frames[1:], strict=False)):
        dt = last.time_s - first.time_s
        p, q = powers[index], powers[index + 1]
        if (
            p is None
            or q is None
            or not first.valid[spec.channel]
            or not last.valid[spec.channel]
            or dt > spec.maximum_sample_interval_s + spec.time_tolerance_s
        ):
            skipped.append(index)
            continue
        incoming, outgoing = integrate_linear_interval(p, q, dt)
        imported.append(incoming)
        exported.append(outgoing)
        durations.append(dt)
    status = "computed"
    if skipped:
        status = "needs_data"
        issues.append("missing/invalid samples or long gaps leave unvalued intervals")
    if spec.sign_convention == "nonnegative_consumption" and any(p is not None and p < 0 for p in powers):
        status = "out_of_scope"
        issues.append("negative power requires an explicitly reviewed signed import/export meaning")
    values = dict(
        import_energy_j=math.fsum(imported),
        export_energy_j=math.fsum(exported),
        covered_duration_s=math.fsum(durations),
        observed_span_s=frames[-1].time_s - frames[0].time_s,
    )
    if any(not math.isfinite(v) for v in values.values()):
        raise ValueError("power integration exceeded finite arithmetic")
    return _seal(
        dict(
            schema="pipeline_integrity.power_integration.v1",
            specification=spec.model_dump(mode="json"),
            context=context.model_dump(mode="json"),
            source_stream_id=frames[0].stream_id,
            source_origin=frames[0].origin,
            source_frames_digest=digest(frames),
            source_samples=len(frames),
            first_time_s=frames[0].time_s,
            last_time_s=frames[-1].time_s,
            status=status,
            issues=issues,
            values=values,
            skipped_interval_indices=skipped,
            integration="piecewise_linear_split_at_zero_crossings",
            clock_sorted_or_resampled=False,
            missing_intervals_or_terminal_tail_filled=False,
            annual_duty_inferred=False,
            implementation_sha256=file_digest(Path(__file__)),
            quadrature_sha256=file_digest(Path(__file__).with_name("quadrature.py")),
        )
    )


class EnergyValuation(Contract):
    id: Name
    expected_power_receipt_digest: str
    source_asset_id: Name
    time_s: Nonnegative
    occurrence_multiplier: Nonnegative | None
    money_basis: MoneyBasis
    import_price_per_kwh: float | None
    export_price_per_kwh: float | None
    import_emissions_kg_co2e_kwh: Nonnegative | None
    export_emission_credit_kg_co2e_kwh: Nonnegative | None
    emission_metric_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    asset_duty_tariff_and_emission_mapping_reviewed: bool = False


def value_power_energy(record: dict, specification: EnergyValuation, context: RunContext, *, engine=None):
    spec = EnergyValuation.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if (
        record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"})
        or record["record_digest"] != spec.expected_power_receipt_digest
    ):
        raise ValueError("power integration receipt differs from its valuation binding")
    if (
        record.get("schema") != "pipeline_integrity.power_integration.v1"
        or record["context"]["asset_id"] != spec.source_asset_id
    ):
        raise ValueError("power source schema or explicitly mapped asset differs")
    if set(spec.evidence_ids) - {e.id for e in context.evidence}:
        raise ValueError("energy valuation assumptions need context evidence")
    amount = emissions = None
    native = []
    if (
        record["status"] == "computed"
        and spec.asset_duty_tariff_and_emission_mapping_reviewed
        and spec.occurrence_multiplier is not None
    ):
        if engine is None:
            from . import create_registry

            engine = create_registry()
        duration = record["values"]["covered_duration_s"]
        for energy_key, price, factor in (
            ("import_energy_j", spec.import_price_per_kwh, spec.import_emissions_kg_co2e_kwh),
            ("export_energy_j", spec.export_price_per_kwh, spec.export_emission_credit_kg_co2e_kwh),
        ):
            receipt = engine.evaluate(
                "economics.energy",
                dict(
                    electrical_power_w=record["values"][energy_key] / duration,
                    operating_duration_s=duration * spec.occurrence_multiplier,
                    price_per_kwh=price,
                    emission_kg_co2e_per_kwh=factor,
                    currency=spec.money_basis.currency,
                    factor_source=spec.evidence_ids[0],
                ),
                context,
            )
            native.append(receipt.model_dump(mode="json"))
        if all(r["status"] != "out_of_scope" for r in native):
            cost_values = [r["values"]["energy_cost"] for r in native]
            emission_values = [r["values"]["declared_emissions_kg_co2e"] for r in native]
            if all(v is not None for v in cost_values):
                amount = cost_values[0] - cost_values[1]
            if all(v is not None for v in emission_values):
                emissions = emission_values[0] - emission_values[1]
    payload = dict(
        specification=spec.model_dump(mode="json"),
        source_power=record,
        native_energy_budgets=native,
        valuation_context=context.model_dump(mode="json"),
        export_emission_credit_is_declared=True,
        observed_tariff_or_annual_operation_claim=False,
    )
    unsupported = record["status"] == "out_of_scope" or any(r["status"] == "out_of_scope" for r in native)
    cost = _bind(
        CostEvent(
            id=spec.id,
            time_s=spec.time_s,
            category="energy",
            money=Money(amount=amount, basis=spec.money_basis),
            evidence_id=spec.evidence_ids[0],
        ),
        "energy",
        payload,
        unsupported=unsupported,
    )
    emission = _bind(
        EmissionEvent(
            id=spec.id,
            time_s=spec.time_s,
            mass_kg_co2e=emissions,
            metric_id=spec.emission_metric_id,
            evidence_id=spec.evidence_ids[0],
        ),
        "energy",
        payload,
        unsupported=unsupported,
    )
    return cost, emission


class ExpectedLossValuation(Contract):
    id: Name
    expected_frequency_binding_digest: str | None
    source_asset_id: Name
    condition_id: Name
    endpoint: Name
    loss_component_id: Name
    interval_start_s: Nonnegative
    interval_finish_s: Nonnegative
    payment_time_s: Nonnegative
    year_duration_s: Positive
    conditional_mean_loss: Money
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    constant_intensity_conditional_loss_and_payment_reviewed: bool = False

    @model_validator(mode="after")
    def interval(self):
        if self.interval_finish_s < self.interval_start_s or (
            self.conditional_mean_loss.amount is not None and self.conditional_mean_loss.amount < 0
        ):
            raise ValueError("expected-loss interval must be ordered and conditional loss nonnegative")
        return self


def value_expected_loss(
    frequency: RiskFrequency | None, specification: ExpectedLossValuation, context: RunContext
):
    spec = ExpectedLossValuation.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if set(spec.evidence_ids) - {e.id for e in context.evidence}:
        raise ValueError("conditional loss and exposure valuation evidence is absent")
    amount = None
    if frequency is not None:
        if not isinstance(frequency, RiskFrequency):
            raise TypeError("expected event count per year is required; a probability is not a frequency")
        frequency = RiskFrequency.model_validate(frequency.model_dump())
        if (
            frequency.binding_digest,
            frequency.context.asset_id,
            frequency.condition_id,
            frequency.endpoint,
        ) != (spec.expected_frequency_binding_digest, spec.source_asset_id, spec.condition_id, spec.endpoint):
            raise ValueError("risk frequency binding, asset, conditioning or endpoint differs")
        if (
            spec.constant_intensity_conditional_loss_and_payment_reviewed
            and spec.conditional_mean_loss.amount is not None
        ):
            amount = (
                frequency.annual_frequency_per_year
                * ((spec.interval_finish_s - spec.interval_start_s) / spec.year_duration_s)
                * spec.conditional_mean_loss.amount
            )
            if not math.isfinite(amount):
                raise ValueError("expected loss exceeded finite arithmetic")
    payload = dict(
        specification=spec.model_dump(mode="json"),
        source_frequency=None if frequency is None else frequency.model_dump(mode="json"),
        valuation_context=context.model_dump(mode="json"),
        expected_value_only=True,
        loss_quantiles_or_failure_probability_inferred=False,
    )
    return _bind(
        CostEvent(
            id=spec.id,
            time_s=spec.payment_time_s,
            category="risk_expectation",
            money=Money(amount=amount, basis=spec.conditional_mean_loss.basis),
            evidence_id=spec.evidence_ids[0],
        ),
        "risk",
        payload,
    )


class ScheduledOperationCharge(Contract):
    id: Name
    operation_id: Name
    expected_mode_id: Name
    fixed_cost: Money
    duration_rate_per_s: Money
    evidence_id: Name


class ScheduleValuation(Contract):
    expected_result_digest: str
    source_asset_id: Name
    valuation_clock_id: Name
    planning_zero_at_valuation_time_s: Nonnegative
    payment_at: Literal["start", "finish"]
    charges: tuple[ScheduledOperationCharge, ...] = Field(min_length=1)
    mapping_evidence_id: Name
    planning_to_valuation_mapping_reviewed: bool = False


def value_schedule(schedule: ScheduleResult, specification: ScheduleValuation, context: RunContext):
    schedule = ScheduleResult.model_validate(schedule.model_dump())
    schedule.check_integrity()
    spec = ScheduleValuation.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if (schedule.result_digest, schedule.context.asset_id, spec.valuation_clock_id) != (
        spec.expected_result_digest,
        spec.source_asset_id,
        context.clock_id,
    ):
        raise ValueError("schedule result, mapped asset or valuation clock differs")
    if len({c.id for c in spec.charges}) != len(spec.charges) or len(
        {c.operation_id for c in spec.charges}
    ) != len(spec.charges):
        raise ValueError("scheduled operations must be charged once per explicit mapping")
    if {spec.mapping_evidence_id, *(c.evidence_id for c in spec.charges)} - {e.id for e in context.evidence}:
        raise ValueError("schedule tariff or clock-mapping evidence is absent")
    timeline = {row["operation_id"]: row for row in schedule.timeline}
    events = []
    for charge in spec.charges:
        if charge.fixed_cost.basis != charge.duration_rate_per_s.basis:
            raise ValueError("fixed and duration-rate currency/year/price basis differ")
        amount, time = None, spec.planning_zero_at_valuation_time_s
        if (
            schedule.status in {"feasible", "optimal"}
            and schedule.audit.get("valid")
            and spec.planning_to_valuation_mapping_reviewed
        ):
            if (
                charge.operation_id not in timeline
                or timeline[charge.operation_id]["mode_id"] != charge.expected_mode_id
            ):
                raise ValueError("scheduled operation or selected mode differs from its tariff mapping")
            row = timeline[charge.operation_id]
            time += row["start_s"] if spec.payment_at == "start" else row["finish_s"]
            if charge.fixed_cost.amount is not None and charge.duration_rate_per_s.amount is not None:
                amount = (
                    charge.fixed_cost.amount
                    + (row["finish_s"] - row["start_s"]) * charge.duration_rate_per_s.amount
                )
                if not math.isfinite(amount):
                    raise ValueError("scheduled cost exceeded finite arithmetic")
        payload = dict(
            schedule=schedule.model_dump(mode="json"),
            specification=spec.model_dump(mode="json"),
            charge=charge.model_dump(mode="json"),
            valuation_context=context.model_dump(mode="json"),
            planned_not_observed_work=True,
            optimizer_objective_is_not_currency=True,
        )
        events.append(
            _bind(
                CostEvent(
                    id=charge.id,
                    time_s=time,
                    category="maintenance",
                    money=Money(amount=amount, basis=charge.fixed_cost.basis),
                    evidence_id=charge.evidence_id,
                ),
                "schedule",
                payload,
                unsupported=schedule.status in {"infeasible", "withheld"},
            )
        )
    return tuple(events)
