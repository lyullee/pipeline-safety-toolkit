"""Reproduce original lifecycle comparisons and bind real power/CP-SAT/HRA quantities without relabelling them."""

import argparse
import json
import math
import runpy
import sys
import zipfile
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.adapters import hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, SignalFrame, digest
from pipeline_integrity.datasets import catalog, file_digest
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
from pipeline_integrity.loaders import hydraulic_cycle
from pipeline_integrity.risk_exchange import DemandExposure, HRARiskClient, RiskFrequency, RiskProbability
from pipeline_integrity.scheduling import solve_schedule


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/hydraulic"))
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in ("hra", "monitoring")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((path / "src").resolve()) for path in roots.values()]
    example = runpy.run_path("examples/lifecycle_study.py")
    study, context = example["make_case"]()
    year = study.year_duration_s
    private = Path("artifacts/lifecycle")
    private.mkdir(parents=True, exist_ok=True)
    cases = {}

    def run(name, request=study):
        result = evaluate_lifecycle(request, context)
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
        (private / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        cases[name] = dict(
            status=result["status"],
            comparisons=result["comparisons"],
            result_digest=result["record_digest"],
            alternatives={
                key: dict(
                    cost_status=value["cost_status"],
                    emissions_status=value["emissions_status"],
                    present_cost=value["present_cost"],
                    emissions_kg_co2e=value["emission_mass_kg_co2e"],
                    category_costs=value.get("present_cost_by_category", {}),
                )
                for key, value in result["alternatives"].items()
            },
        )
        return result

    baseline = run("declared_reference")
    assert baseline["status"] == "computed" and baseline["comparisons"]["least_present_cost_ids"] == [
        "replace"
    ]
    independent = {}
    for name, rate in (("zero_discount", 0.0), ("negative_discount", -0.05), ("high_discount", 0.12)):
        result = run(name, study.model_copy(update={"annual_discount_rate": rate}))
        annuity = sum((1 + rate) ** (-t) for t in range(1, 6))
        expected = dict(
            keep=1000 + 100 * annuity + 600 / (1 + rate) ** 3 - 100 / (1 + rate) ** 5,
            replace=1800 + 30 * annuity - 200 / (1 + rate) ** 5,
        )
        for key, value in expected.items():
            actual = result["alternatives"][key]["present_cost"]
            assert math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-9)
            independent[f"{name}:{key}"] = dict(
                expected=value, actual=actual, absolute_error=abs(actual - value)
            )
    assert cases["high_discount"]["comparisons"]["least_present_cost_ids"] == ["keep"]
    inflation = 0.03
    data = study.model_dump(mode="json")
    data["money_basis"]["prices"] = "nominal"
    data["discount_rate_basis"] = "nominal"
    data["annual_discount_rate"] = (1 + study.annual_discount_rate) * (1 + inflation) - 1
    for alternative in data["alternatives"]:
        for event in alternative["cost_events"]:
            event["money"]["basis"]["prices"] = "nominal"
            event["money"]["amount"] *= (1 + inflation) ** (event["time_s"] / year)
        for recurring in alternative["recurring_costs"]:
            recurring["base_quote"]["basis"]["prices"] = "nominal"
            recurring["escalation_basis"] = "nominal"
            recurring["escalation_per_year"] = (1 + recurring["escalation_per_year"]) * (1 + inflation) - 1
    nominal = run("consistent_nominal", LifecycleStudy.model_validate_json(json.dumps(data)))
    for key in ("keep", "replace"):
        assert math.isclose(
            nominal["alternatives"][key]["present_cost"],
            baseline["alternatives"][key]["present_cost"],
            abs_tol=1e-9,
        )
    assert (
        run("unknown_discount", study.model_copy(update={"annual_discount_rate": None}))["status"]
        == "needs_data"
    )
    alternative = study.alternatives[0]
    missing = alternative.cost_events[0].model_copy(
        update={"money": alternative.cost_events[0].money.model_copy(update={"amount": None})}
    )
    incomplete = alternative.model_copy(update={"cost_events": (missing, *alternative.cost_events[1:])})
    assert (
        run(
            "unknown_capital", study.model_copy(update={"alternatives": (incomplete, study.alternatives[1])})
        )["comparisons"]["least_present_cost_ids"]
        is None
    )
    sensitivity = []
    for rate in np.linspace(0, 0.2, 21):
        result = evaluate_lifecycle(study.model_copy(update={"annual_discount_rate": float(rate)}), context)
        assert result["status"] == "computed"
        sensitivity.append(
            dict(
                annual_discount_rate=float(rate),
                present_cost={name: row["present_cost"] for name, row in result["alternatives"].items()},
            )
        )

    asset = next(row for row in catalog() if row.id == "hydraulic")
    archive = args.raw_root / "source.zip"
    assert file_digest(archive) == asset.assets[0].checksum.split(":", 1)[1]
    indices = (0, 100, 1100, 2204)
    raw = {}
    with zipfile.ZipFile(archive) as source:
        with source.open("EPS1.txt") as stream:
            for index, line in enumerate(stream):
                if index in indices:
                    raw[index] = np.fromstring(line.decode("ascii"), sep="\t")
    power_reviews = []
    for index in indices:
        data = hydraulic_cycle(archive, index, sensors=("EPS1",))["sensors"]["EPS1"]
        assert np.array_equal(data["values"], raw[index])
        source_context = context.model_copy(
            update={
                "asset_id": "hydraulic_test_rig",
                "clock_id": data["clock_id"],
                "origin": "laboratory",
                "evidence": (
                    EvidenceRef(
                        id="hydraulic",
                        citation=asset.citation,
                        locator=asset.source,
                        sha256=file_digest(archive),
                        origin="laboratory",
                        license_id=asset.license_id,
                    ),
                ),
            }
        )
        frames = tuple(
            SignalFrame(
                stream_id=f"EPS1:cycle:{index}",
                clock_id=data["clock_id"],
                time_s=float(t),
                values={"EPS1": float(p)},
                units={"EPS1": "W"},
                valid={"EPS1": bool(valid)},
                origin="laboratory",
            )
            for t, p, valid in zip(data["time_s"], data["values"], data["valid"], strict=True)
        )
        record = integrate_power_frames(
            frames,
            PowerIntegration(
                channel="EPS1",
                maximum_sample_interval_s=0.01,
                sign_convention="nonnegative_consumption",
                evidence_id="hydraulic",
            ),
            source_context,
        )
        assert record["status"] == "computed" and math.isclose(
            record["values"]["covered_duration_s"], 59.99, abs_tol=1e-10
        )
        independently_integrated = float(np.trapezoid(raw[index], dx=0.01))
        assert math.isclose(
            record["values"]["import_energy_j"], independently_integrated, rel_tol=1e-12, abs_tol=1e-6
        )
        valuation = EnergyValuation(
            id=f"cycle_{index}_energy",
            expected_power_receipt_digest=record["record_digest"],
            source_asset_id="hydraulic_test_rig",
            time_s=year,
            occurrence_multiplier=100000.0,
            money_basis=study.money_basis,
            import_price_per_kwh=0.2,
            export_price_per_kwh=0.0,
            import_emissions_kg_co2e_kwh=0.4,
            export_emission_credit_kg_co2e_kwh=0.0,
            emission_metric_id=study.emission_metric_id,
            evidence_ids=("reference",),
            asset_duty_tariff_and_emission_mapping_reviewed=True,
        )
        cost, emission = value_power_energy(record, valuation, context)
        assert math.isclose(cost.money.amount, independently_integrated / 3.6e6 * 100000 * 0.2, rel_tol=1e-12)
        updated = tuple(
            a.model_copy(
                update={
                    "cost_events": (*a.cost_events, cost),
                    "emission_events": (*a.emission_events, emission),
                }
            )
            for a in study.alternatives
        )
        linked = evaluate_lifecycle(study.model_copy(update={"alternatives": updated}), context)
        assert linked["status"] == "computed"
        batch = monitoring_batch(frames)
        series = hra_signal_series(frames, "EPS1")
        assert np.array_equal(batch.time_s, data["time_s"]) and tuple(series.time_s) == tuple(data["time_s"])
        assert batch.origin == series.origin == "laboratory"
        power_reviews.append(
            dict(
                cycle_index=index,
                samples=len(frames),
                source_clock_id=data["clock_id"],
                covered_duration_s=record["values"]["covered_duration_s"],
                nominal_source_cycle_duration_s=60.0,
                unfilled_terminal_duration_s=60.0 - record["values"]["covered_duration_s"],
                source_numeric_equality=True,
                integration_error_j=abs(record["values"]["import_energy_j"] - independently_integrated),
                actual_tariff_or_duty_or_emission_factor_observed=False,
                valuation_uses_declared_REF_assumptions=True,
                monitoring_rows=len(batch.time_s),
                hra_samples=len(series.time_s),
            )
        )
        (private / f"power_cycle_{index}.json").write_text(
            json.dumps(
                dict(
                    integration=record,
                    cost=cost.model_dump(mode="json"),
                    emission=emission.model_dump(mode="json"),
                    lifecycle=linked,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    planning = runpy.run_path("examples/operations_schedule.py")
    plan_spec, plan_context = planning["make_case"]()
    plan = solve_schedule(plan_spec, plan_context)
    assert plan.status in {"optimal", "feasible"} and plan.audit["valid"]
    charges = tuple(
        ScheduledOperationCharge(
            id="planned_" + row["operation_id"],
            operation_id=row["operation_id"],
            expected_mode_id=row["mode_id"],
            fixed_cost=Money(amount=10.0, basis=study.money_basis),
            duration_rate_per_s=Money(amount=0.5, basis=study.money_basis),
            evidence_id="reference",
        )
        for row in plan.timeline
    )
    mapping = ScheduleValuation(
        expected_result_digest=plan.result_digest,
        source_asset_id=plan_context.asset_id,
        valuation_clock_id=context.clock_id,
        planning_zero_at_valuation_time_s=year,
        payment_at="finish",
        charges=charges,
        mapping_evidence_id="reference",
        planning_to_valuation_mapping_reviewed=True,
    )
    planned_costs = value_schedule(plan, mapping, context)
    for event, row in zip(planned_costs, plan.timeline, strict=True):
        assert event.money.amount == 10 + 0.5 * (row["finish_s"] - row["start_s"])
        assert event.time_s == year + row["finish_s"]
    scheduled = tuple(
        a.model_copy(update={"cost_events": (*a.cost_events, *planned_costs)}) for a in study.alternatives
    )
    assert (
        run(
            "planned_operation_tariffs",
            study.model_copy(update={"alternatives": scheduled}),
        )["status"]
        == "computed"
    )
    source_context = context.model_copy(update={"clock_id": "declared_demand_exposure"})
    probability_payload = dict(probability=0.2, basis="authored conditional demand failure assumption")
    probability = RiskProbability(
        id="declared_failure",
        probability=0.2,
        measure="demand_failure_probability",
        condition_id="reference_condition",
        conditioning="Declared demand conditions",
        opportunity_id="declared_demand",
        opportunity_definition="One demand in the authored scenario",
        failure_definition="The declared function is not completed",
        role="equipment",
        evidence_ids=("reference",),
        qualification="assumed",
        context=source_context,
        source_kind="declared",
        source_payload=probability_payload,
        source_digest=digest(probability_payload),
    )
    client = HRARiskClient()
    exposure = DemandExposure(
        opportunities_per_year=30.0,
        opportunity_id=probability.opportunity_id,
        opportunity_definition=probability.opportunity_definition,
        condition_id=probability.condition_id,
        evidence_id="reference",
    )
    annualized = client.annualize(probability, exposure)
    assert annualized["status"] == "exchange_checked"
    frequency = RiskFrequency.model_validate_json(json.dumps(annualized["frequencies"][0]))
    assert frequency.annual_frequency_per_year == 6.0
    loss_mapping = ExpectedLossValuation(
        id="conditional_rework",
        expected_frequency_binding_digest=frequency.binding_digest,
        source_asset_id=frequency.context.asset_id,
        condition_id=frequency.condition_id,
        endpoint=frequency.endpoint,
        loss_component_id="declared_rework",
        interval_start_s=0.0,
        interval_finish_s=year,
        payment_time_s=year,
        year_duration_s=year,
        conditional_mean_loss=Money(amount=25.0, basis=study.money_basis),
        evidence_ids=("reference",),
        constant_intensity_conditional_loss_and_payment_reviewed=True,
    )
    loss = value_expected_loss(frequency, loss_mapping, context)
    assert loss.money.amount == 150.0
    risk_alternative = tuple(
        a.model_copy(update={"cost_events": (*a.cost_events, loss)}) for a in study.alternatives
    )
    assert (
        run(
            "expected_rework_loss",
            study.model_copy(update={"alternatives": risk_alternative}),
        )["status"]
        == "computed"
    )
    assert before == {key: revision(path) for key, path in roots.items()}
    summary = dict(
        schema="pipeline_integrity.lifecycle_verification.v1",
        authored_cases=cases,
        independent_references=independent,
        authored_discount_sensitivity=sensitivity,
        power_source=dict(
            source=asset.source,
            creators=asset.creators,
            license_id=asset.license_id,
            archive_sha256=file_digest(archive),
            checksum_origin=asset.assets[0].checksum_origin,
            cycles=power_reviews,
            total_samples=sum(row["samples"] for row in power_reviews),
            native_power_source_reused=True,
            annual_duty_and_tariff_and_emission_factors_are_hypotheses=True,
            source_linked_additions_shared_across_alternatives_and_excluded_from_base_quotes=True,
            original_rows_in_distribution=False,
        ),
        scheduling=dict(
            native_status=plan.native.native_status,
            result_status=plan.status,
            independent_audit_passed=plan.audit["valid"],
            charged_operations=len(planned_costs),
            plan_result_digest=plan.result_digest,
            objective_used_as_money=False,
            observed_work_claim=False,
        ),
        risk=dict(
            native_provider=client.provider,
            annual_expected_count=frequency.annual_frequency_per_year,
            declared_conditional_mean_loss=25.0,
            expected_interval_loss=loss.money.amount,
            frequency_binding_digest=frequency.binding_digest,
            observed_failure_rate_or_loss_claim=False,
        ),
        source_versions=before,
        module_sha256=file_digest(Path("src/pipeline_integrity/lifecycle.py")),
        exchange_module_sha256=file_digest(Path("src/pipeline_integrity/lifecycle_exchange.py")),
        economic_primitive_sha256=file_digest(Path("src/pipeline_integrity/models/economics.py")),
        example_sha256=file_digest(Path("examples/lifecycle_study.py")),
        script_sha256=file_digest(Path(__file__)),
        market_tariff_recommended_or_investment_authorized=False,
    )
    Path("research/lifecycle_results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                authored_cases=len(cases),
                power_cycles=len(indices),
                power_samples=summary["power_source"]["total_samples"],
                planned_operations=len(planned_costs),
                native_expected_annual_count=frequency.annual_frequency_per_year,
            )
        )
    )


if __name__ == "__main__":
    main()
