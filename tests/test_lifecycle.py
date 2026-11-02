"""Independent valuation references, basis/service boundaries and genuine upstream-quantity bindings."""

import json
import math
import runpy

import pytest
from pint.errors import DimensionalityError
from pydantic import ValidationError

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.lifecycle import LifecycleStudy, Money, evaluate_lifecycle
from pipeline_integrity.lifecycle_exchange import (
    EnergyValuation,
    ExpectedLossValuation,
    PowerIntegration,
    ScheduledOperationCharge,
    ScheduleValuation,
    integrate_power_frames,
    value_expected_loss,
    value_power_energy,
    value_schedule,
)
from pipeline_integrity.risk_exchange import RiskFrequency
from pipeline_integrity.scheduling import solve_schedule

EXAMPLE = runpy.run_path("examples/lifecycle_study.py")
YEAR = EXAMPLE["YEAR"]


def base():
    return EXAMPLE["make_case"]()


def test_existing_primitives_explicit_times_signed_prices_and_unknown_factors():
    engine = create_registry()
    _, context = base()
    result = engine.evaluate(
        "economics.cashflow",
        dict(
            cashflows=(100.0, -20.0, 50.0),
            times_s=(YEAR, 0.0, YEAR / 2),
            discount_rate_per_period=0.1,
            period_duration_s=YEAR,
            currency="REF",
            price_year=2026,
        ),
        context,
    )
    assert result.values["net_present_value"] == pytest.approx(
        100 / 1.1 - 20 + 50 / math.sqrt(1.1), abs=1e-12
    )
    assert result.model_version == "2"
    partial = engine.evaluate(
        "economics.energy",
        dict(
            electrical_power_w=1000.0,
            operating_duration_s=3600.0,
            price_per_kwh=-0.1,
            emission_kg_co2e_per_kwh=None,
            currency="REF",
            factor_source="reference",
        ),
        context,
    )
    assert partial.status == "needs_data" and partial.values["energy_kwh"] == 1.0
    assert partial.values["energy_cost"] == -0.1 and partial.values["declared_emissions_kg_co2e"] is None


@pytest.mark.parametrize("rate", [0.0, 0.05, -0.05])
def test_lifecycle_costs_match_independent_closed_form_and_emissions_not_discounted(rate):
    study, context = base()
    result = evaluate_lifecycle(study.model_copy(update={"annual_discount_rate": rate}), context)
    assert result["status"] == "computed"
    annuity = sum(1 / (1 + rate) ** year for year in range(1, 6))
    expected = dict(
        keep=1000 + 100 * annuity + 600 / (1 + rate) ** 3 - 100 / (1 + rate) ** 5,
        replace=1800 + 30 * annuity - 200 / (1 + rate) ** 5,
    )
    for name, value in expected.items():
        row = result["alternatives"][name]
        assert row["present_cost"] == pytest.approx(value, rel=1e-12)
        assert math.fsum(row["present_cost_by_category"].values()) == pytest.approx(value, rel=1e-12)
    assert result["alternatives"]["keep"]["emission_mass_kg_co2e"] == 1000.0
    assert result["alternatives"]["replace"]["emission_mass_kg_co2e"] == 1300.0
    assert result["comparisons"]["present_savings_vs_baseline"]["replace"] == pytest.approx(
        expected["keep"] - expected["replace"]
    )
    assert result["emissions_discounted"] is False
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})


def test_consistent_nominal_and_constant_price_studies_agree():
    study, context = base()
    real = evaluate_lifecycle(study, context)
    inflation = 0.03
    data = study.model_dump(mode="json")
    data["money_basis"]["prices"] = "nominal"
    data["discount_rate_basis"] = "nominal"
    data["annual_discount_rate"] = (1 + study.annual_discount_rate) * (1 + inflation) - 1
    for alternative in data["alternatives"]:
        for event in alternative["cost_events"]:
            event["money"]["basis"]["prices"] = "nominal"
            event["money"]["amount"] *= (1 + inflation) ** (event["time_s"] / YEAR)
        for recurring in alternative["recurring_costs"]:
            recurring["base_quote"]["basis"]["prices"] = "nominal"
            recurring["escalation_basis"] = "nominal"
            recurring["escalation_per_year"] = (1 + recurring["escalation_per_year"]) * (1 + inflation) - 1
    nominal = evaluate_lifecycle(LifecycleStudy.model_validate_json(json.dumps(data)), context)
    for key in ("keep", "replace"):
        assert nominal["alternatives"][key]["present_cost"] == pytest.approx(
            real["alternatives"][key]["present_cost"], abs=1e-9
        )


@pytest.mark.parametrize(
    "mutation", ["currency", "year", "discount_basis", "escalation_basis", "service", "horizon", "bool"]
)
def test_inconsistent_meanings_are_rejected(mutation):
    study, _ = base()
    data = study.model_dump(mode="json")
    if mutation == "currency":
        data["alternatives"][0]["cost_events"][0]["money"]["basis"]["currency"] = "OTHER"
    elif mutation == "year":
        data["alternatives"][0]["cost_events"][0]["money"]["basis"]["base_year"] = 2025
    elif mutation == "discount_basis":
        data["discount_rate_basis"] = "nominal"
    elif mutation == "escalation_basis":
        data["alternatives"][0]["recurring_costs"][0]["escalation_basis"] = "nominal"
    elif mutation == "service":
        data["alternatives"][1]["service_definition_id"] = "different_service"
    elif mutation == "horizon":
        data["alternatives"][0]["recurring_costs"][0]["occurrences"] = 6
    else:
        data["alternatives"][0]["cost_events"][0]["money"]["amount"] = True
    with pytest.raises(ValidationError):
        LifecycleStudy.model_validate_json(json.dumps(data))


def test_unknown_cost_suppresses_cost_selection_and_unknown_emissions_suppresses_pareto():
    study, context = base()
    first = study.alternatives[0]
    event = first.cost_events[0].model_copy(
        update={"money": first.cost_events[0].money.model_copy(update={"amount": None})}
    )
    changed = first.model_copy(update={"cost_events": (event, *first.cost_events[1:])})
    result = evaluate_lifecycle(
        study.model_copy(update={"alternatives": (changed, study.alternatives[1])}), context
    )
    assert result["status"] == "needs_data" and result["comparisons"]["least_present_cost_ids"] is None
    assert result["comparisons"]["nondominated_ids"] is None
    emission = first.emission_events[0].model_copy(update={"mass_kg_co2e": None})
    changed = first.model_copy(update={"emission_events": (emission, *first.emission_events[1:])})
    result = evaluate_lifecycle(
        study.model_copy(update={"alternatives": (changed, study.alternatives[1])}), context
    )
    assert (
        result["comparisons"]["least_present_cost_ids"]
        and result["comparisons"]["least_emission_ids"] is None
    )


def test_ties_empty_explicit_inventory_review_and_event_budget():
    study, context = base()
    first = study.alternatives[0]
    result = evaluate_lifecycle(
        study.model_copy(update={"alternatives": (first, first.model_copy(update={"id": "identical"}))}),
        context,
    )
    assert result["comparisons"]["least_present_cost_ids"] == ["keep", "identical"]
    assert result["comparisons"]["nondominated_ids"] == ["keep", "identical"]
    empty = first.model_copy(update={"cost_events": (), "recurring_costs": (), "emission_events": ()})
    result = evaluate_lifecycle(study.model_copy(update={"alternatives": (empty,)}), context)
    assert (
        result["alternatives"]["keep"]["present_cost"]
        == result["alternatives"]["keep"]["emission_mass_kg_co2e"]
        == 0.0
    )
    result = evaluate_lifecycle(
        study.model_copy(
            update={"alternatives": (empty.model_copy(update={"cost_inventory_reviewed": False}),)}
        ),
        context,
    )
    assert result["status"] == "needs_data"
    assert (
        evaluate_lifecycle(study.model_copy(update={"maximum_generated_cost_events": 1}), context)["status"]
        == "out_of_scope"
    )


def power_case(values=(1000.0, 2000.0, 3000.0), times=(0.0, 1.0, 2.0), sign="nonnegative_consumption"):
    context = RunContext(
        asset_id="rig",
        scenario_id="observed_interval",
        clock_id="rig_cycle",
        origin="laboratory",
        evidence=(EvidenceRef(id="power", citation="Authored power reference", origin="synthetic"),),
    )
    frames = tuple(
        SignalFrame(
            stream_id="meter",
            clock_id="rig_cycle",
            time_s=t,
            values={"power": p},
            units={"power": "W"},
            valid={"power": p is not None},
            origin="synthetic",
        )
        for p, t in zip(values, times, strict=True)
    )
    spec = PowerIntegration(
        channel="power", maximum_sample_interval_s=1.0, sign_convention=sign, evidence_id="power"
    )
    return frames, spec, context


def test_power_integration_covered_span_and_exact_zero_crossing():
    frames, spec, context = power_case()
    result = integrate_power_frames(frames, spec, context)
    assert result["status"] == "computed" and result["values"]["import_energy_j"] == 4000
    assert result["values"]["covered_duration_s"] == 2.0
    frames, spec, context = power_case((-1000.0, 1000.0), (0.0, 1.0), "signed_import_positive")
    result = integrate_power_frames(frames, spec, context)
    assert result["values"]["import_energy_j"] == result["values"]["export_energy_j"] == 250.0
    assert result["missing_intervals_or_terminal_tail_filled"] is False
    assert (
        integrate_power_frames(
            frames, spec.model_copy(update={"sign_convention": "nonnegative_consumption"}), context
        )["status"]
        == "out_of_scope"
    )


@pytest.mark.parametrize("kind", ["missing", "invalid", "gap", "clock", "units"])
def test_power_quality_and_clocks(kind):
    frames, spec, context = power_case()
    if kind == "missing":
        frames = (
            frames[0],
            frames[1].model_copy(update={"values": {"power": None}, "valid": {"power": False}}),
            frames[2],
        )
    elif kind == "invalid":
        frames = (frames[0], frames[1].model_copy(update={"valid": {"power": False}}), frames[2])
    elif kind == "gap":
        frames = (frames[0], frames[1], frames[2].model_copy(update={"time_s": 10.0}))
    elif kind == "clock":
        frames = (frames[0], frames[1].model_copy(update={"clock_id": "other"}), frames[2])
    else:
        frames = tuple(f.model_copy(update={"units": {"power": "Pa"}}) for f in frames)
    if kind in ("clock", "units"):
        with pytest.raises((ValueError, DimensionalityError)):
            integrate_power_frames(frames, spec, context)
    else:
        result = integrate_power_frames(frames, spec, context)
        assert result["status"] == "needs_data" and result["skipped_interval_indices"]


def test_energy_valuation_reuses_existing_budget_and_keeps_unknowns_and_source_seal():
    frames, spec, power_context = power_case()
    integral = integrate_power_frames(frames, spec, power_context)
    study, context = base()
    valuation = EnergyValuation(
        id="meter_cost",
        expected_power_receipt_digest=integral["record_digest"],
        source_asset_id="rig",
        time_s=YEAR,
        occurrence_multiplier=100.0,
        money_basis=study.money_basis,
        import_price_per_kwh=-0.2,
        export_price_per_kwh=0.0,
        import_emissions_kg_co2e_kwh=0.4,
        export_emission_credit_kg_co2e_kwh=0.0,
        emission_metric_id=study.emission_metric_id,
        evidence_ids=("reference",),
        asset_duty_tariff_and_emission_mapping_reviewed=True,
    )
    cost, emissions = value_power_energy(integral, valuation, context)
    assert cost.money.amount == pytest.approx(4000 / 3.6e6 * 100 * (-0.2))
    assert emissions.mass_kg_co2e == pytest.approx(4000 / 3.6e6 * 100 * 0.4)
    assert cost.source.source_payload["native_energy_budgets"][0]["model_version"] == "2"
    unknown, _ = value_power_energy(
        integral, valuation.model_copy(update={"import_price_per_kwh": None}), context
    )
    assert unknown.money.amount is None
    changed = cost.model_copy(update={"money": cost.money.model_copy(update={"amount": 1.0})})
    with pytest.raises(ValidationError):
        type(cost).model_validate(changed.model_dump())
    first = study.alternatives[0]
    linked = first.model_copy(update={"cost_events": (*first.cost_events, cost)})
    with pytest.raises(ValueError, match="valuation context"):
        evaluate_lifecycle(
            study.model_copy(update={"alternatives": (linked, study.alternatives[1])}),
            context.model_copy(update={"scenario_id": "other_scenario"}),
        )


def frequency_case():
    _, context = base()
    context = context.model_copy(update={"clock_id": "declared_annual_exposure"})
    payload = {
        "annual_frequency_per_year": 2.0,
        "basis": "authored expected count, not a measured failure rate",
    }
    frequency = RiskFrequency(
        id="event-rate",
        annual_frequency_per_year=2.0,
        condition_id="condition",
        endpoint="event_occurrence",
        evidence_ids=("reference",),
        context=context,
        source_payload=payload,
        source_digest=digest(payload),
    )
    return frequency


def loss_spec(frequency):
    study, _ = base()
    return ExpectedLossValuation(
        id="expected_loss",
        expected_frequency_binding_digest=frequency.binding_digest,
        source_asset_id=frequency.context.asset_id,
        condition_id="condition",
        endpoint="event_occurrence",
        loss_component_id="declared_rework",
        interval_start_s=0.0,
        interval_finish_s=YEAR,
        payment_time_s=YEAR,
        year_duration_s=YEAR,
        conditional_mean_loss=Money(amount=20.0, basis=study.money_basis),
        evidence_ids=("reference",),
        constant_intensity_conditional_loss_and_payment_reviewed=True,
    )


def test_risk_mean_loss_preserves_frequency_and_blocks_duplicate_intervals():
    study, context = base()
    frequency = frequency_case()
    specification = loss_spec(frequency)
    cost = value_expected_loss(frequency, specification, context)
    assert cost.money.amount == 40.0
    assert cost.source.source_payload["source_frequency"]["observed_annual_frequency"] is False
    first = study.alternatives[0]
    duplicated = value_expected_loss(frequency, specification.model_copy(update={"id": "duplicate"}), context)
    with pytest.raises(ValidationError, match="overlapping"):
        evaluate_lifecycle(
            study.model_copy(
                update={
                    "alternatives": (
                        first.model_copy(update={"cost_events": (*first.cost_events, cost, duplicated)}),
                        study.alternatives[1],
                    )
                }
            ),
            context,
        )
    unknown = value_expected_loss(
        None, specification.model_copy(update={"expected_frequency_binding_digest": None}), context
    )
    assert unknown.money.amount is None
    with pytest.raises(ValueError):
        value_expected_loss(
            frequency, specification.model_copy(update={"endpoint": "loss_of_containment"}), context
        )
    with pytest.raises(TypeError):
        value_expected_loss(0.1, specification, context)


def test_schedule_cost_uses_tariffs_and_planned_times_not_objective():
    pytest.importorskip("ortools")
    source = runpy.run_path("examples/operations_schedule.py")
    request, planning_context = source["make_case"]()
    result = solve_schedule(request, planning_context)
    assert result.status in {"feasible", "optimal"}
    study, context = base()
    row = result.timeline[0]
    quote = ScheduledOperationCharge(
        id="scheduled",
        operation_id=row["operation_id"],
        expected_mode_id=row["mode_id"],
        fixed_cost=Money(amount=10.0, basis=study.money_basis),
        duration_rate_per_s=Money(amount=0.5, basis=study.money_basis),
        evidence_id="reference",
    )
    mapping = ScheduleValuation(
        expected_result_digest=result.result_digest,
        source_asset_id=planning_context.asset_id,
        valuation_clock_id=context.clock_id,
        planning_zero_at_valuation_time_s=YEAR,
        payment_at="finish",
        charges=(quote,),
        mapping_evidence_id="reference",
        planning_to_valuation_mapping_reviewed=True,
    )
    event = value_schedule(result, mapping, context)[0]
    assert event.time_s == YEAR + row["finish_s"]
    assert event.money.amount == 10 + 0.5 * (row["finish_s"] - row["start_s"])
    assert event.source.source_payload["planned_not_observed_work"] is True
    assert (
        value_schedule(
            result, mapping.model_copy(update={"planning_to_valuation_mapping_reviewed": False}), context
        )[0].money.amount
        is None
    )


def test_unsupported_power_source_cannot_be_valued_or_selected():
    frames, spec, source_context = power_case(values=(-1000.0, 1000.0, 1000.0))
    record = integrate_power_frames(frames, spec, source_context)
    study, context = base()
    mapping = EnergyValuation(
        id="unsupported",
        expected_power_receipt_digest=record["record_digest"],
        source_asset_id="rig",
        time_s=YEAR,
        occurrence_multiplier=1.0,
        money_basis=study.money_basis,
        import_price_per_kwh=0.2,
        export_price_per_kwh=0.1,
        import_emissions_kg_co2e_kwh=0.4,
        export_emission_credit_kg_co2e_kwh=0.0,
        emission_metric_id=study.emission_metric_id,
        evidence_ids=("reference",),
        asset_duty_tariff_and_emission_mapping_reviewed=True,
    )
    cost, emission = value_power_energy(record, mapping, context)
    assert cost.money.amount is None and cost.source.valuation_status == "out_of_scope"
    first = study.alternatives[0]
    changed = first.model_copy(
        update={
            "cost_events": (*first.cost_events, cost),
            "emission_events": (*first.emission_events, emission),
        }
    )
    result = evaluate_lifecycle(
        study.model_copy(update={"alternatives": (changed, study.alternatives[1])}), context
    )
    assert result["status"] == "out_of_scope" and result["comparisons"]["least_present_cost_ids"] is None


def test_floating_point_limits_and_unknown_discount_are_explicit():
    study, context = base()
    result = create_registry().evaluate(
        "economics.cashflow",
        dict(
            cashflows=(100.0,),
            times_s=(1e308,),
            discount_rate_per_period=0.1,
            period_duration_s=1.0,
            currency="REF",
            price_year=2026,
        ),
        context,
    )
    assert result.status == "out_of_scope" and result.values == {}
    unknown = evaluate_lifecycle(study.model_copy(update={"annual_discount_rate": None}), context)
    assert unknown["status"] == "needs_data" and unknown["comparisons"]["least_present_cost_ids"] is None
