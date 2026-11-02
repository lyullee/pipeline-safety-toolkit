"""Authored uncertain pump duty, model multiplier and lifecycle inventory.

The distributions, correlation, duty, tariff and loss are fictional assumptions.
An optional existing HRA client annualizes an assumed conditional probability;
neither the distribution nor its average is an observed human error frequency.
"""

import json
import runpy
from pathlib import Path

from pydantic import ValidationError

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import SignalFrame, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.lifecycle import Money, evaluate_lifecycle
from pipeline_integrity.lifecycle_exchange import (
    EnergyValuation,
    ExpectedLossValuation,
    PowerIntegration,
    integrate_power_frames,
    value_expected_loss,
    value_power_energy,
)
from pipeline_integrity.risk_exchange import DemandExposure, RiskFrequency, RiskProbability
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleNode,
    EnsembleRequest,
    EnsembleValues,
    Marginal,
    UncertainVariable,
    evaluate_ensemble,
    generate_ensemble,
)


def make_request(*, samples=512, include_risk=False):
    def variable(id, unit, layer, family, **parameters):
        return UncertainVariable(
            id=id,
            unit=unit,
            layer=layer,
            evidence_id="reference",
            marginal=Marginal(family=family, parameters=parameters),
        )

    variables = (
        variable("flow", "m3/s", "measurement", "normal", mean=0.01, standard_deviation=0.001),
        variable("head", "m", "input", "normal", mean=40.0, standard_deviation=4.0),
        variable("power_multiplier", "1", "model", "uniform", lower=0.95, upper=1.05),
    )
    matrix = ((1.0, 0.8, 0.0), (0.8, 1.0, 0.0), (0.0, 0.0, 1.0))
    if include_risk:
        variables += (
            variable(
                "conditional_error_probability",
                "1",
                "input",
                "beta",
                alpha=2.0,
                beta=18.0,
                lower=0.0,
                upper=1.0,
            ),
        )
        matrix = ((1.0, 0.8, 0.0, 0.24), (0.8, 1.0, 0.0, 0.3), (0.0, 0.0, 1.0, 0.0), (0.24, 0.3, 0.0, 1.0))
    return EnsembleRequest(
        id="fictional_shared_duty",
        variables=variables,
        method="sobol",
        interpretation="epistemic",
        dependence="gaussian_copula",
        latent_correlation=matrix,
        samples=samples,
        seed=736,
        evidence_id="reference",
        definition_reviewed=True,
    )


def make_node(*, risk_client=None):
    study, context = runpy.run_path(str(Path(__file__).with_name("lifecycle_study.py")))["make_case"]()
    engine = create_registry()
    names = {
        "adjusted_power_w": ("W", "quantity"),
        "keep_present_cost": ("REF", "cost"),
        "replace_present_cost": ("REF", "cost"),
        "replace_minus_keep": ("REF", "cost"),
    }
    if risk_client is not None:
        names |= {
            "conditional_error_probability": ("1", "conditional_probability"),
            "annual_expected_events": ("year^-1", "expected_count_rate"),
        }
    metrics = tuple(
        EnsembleMetric(
            id=key,
            unit=unit,
            meaning=meaning,
            evidence_id="reference",
            **({"support_lower": 0.0, "support_upper": 1.0} if meaning == "conditional_probability" else {}),
        )
        for key, (unit, meaning) in names.items()
    )

    def evaluate(values, ctx):
        if any(v is None for v in values.values()):
            return EnsembleValues(
                context=ctx,
                values=dict.fromkeys(names),
                units={k: v[0] for k, v in names.items()},
                statuses=dict.fromkeys(names, "needs_data"),
                native_payload={"issue": "unresolved shared draw"},
            )
        try:
            pump = engine.evaluate(
                "equipment.pump_power",
                dict(
                    volume_flow_m3_s=values["flow"],
                    density_kg_m3=1000.0,
                    head_m=values["head"],
                    pump_efficiency=0.75,
                    motor_efficiency=0.9,
                ),
                ctx,
            )
        except ValidationError as error:
            return EnsembleValues(
                context=ctx,
                values=dict.fromkeys(names),
                units={k: v[0] for k, v in names.items()},
                statuses=dict.fromkeys(names, "out_of_scope"),
                native_payload={"issue": str(error)},
            )
        adjusted = pump.values["electrical_power_w"] * values["power_multiplier"]
        duration = 2000.0 * 3600
        frames = tuple(
            SignalFrame(
                stream_id="authored_constant_duty",
                clock_id=ctx.clock_id,
                time_s=t,
                values={"power": adjusted},
                units={"power": "W"},
                valid={"power": True},
                origin="synthetic",
            )
            for t in (0.0, duration)
        )
        integrated = integrate_power_frames(
            frames,
            PowerIntegration(
                channel="power",
                maximum_sample_interval_s=duration,
                sign_convention="nonnegative_consumption",
                evidence_id="reference",
            ),
            ctx,
        )
        costs, emissions = [], []
        frequency, risk_receipt = None, None
        if risk_client is not None:
            payload = dict(
                probability=values["conditional_error_probability"],
                interpretation="fictional_epistemic_parameter",
                ensemble=ctx.metadata["ensemble"],
            )
            event = RiskProbability(
                id="assumed_response",
                probability=values["conditional_error_probability"],
                measure="demand_failure_probability",
                condition_id="authored_condition",
                conditioning="Declared conditions; correlated with head only by scenario assumption",
                opportunity_id="authored_demand",
                opportunity_definition="One demand in the fictional comparison",
                failure_definition="The declared response is not completed",
                role="response",
                evidence_ids=("reference",),
                qualification="assumed",
                context=ctx,
                source_kind="declared",
                source_payload=payload,
                source_digest=digest(payload),
            )
            exposure = DemandExposure(
                opportunities_per_year=30.0,
                opportunity_id=event.opportunity_id,
                opportunity_definition=event.opportunity_definition,
                condition_id=event.condition_id,
                evidence_id="reference",
            )
            risk_receipt = risk_client.annualize(event, exposure)
            if risk_receipt["status"] != "exchange_checked":
                raise ValueError("the existing HRA engine withheld the assumed frequency")
            frequency = RiskFrequency.model_validate_json(json.dumps(risk_receipt["frequencies"][0]))
        for year in range(1, 6):
            cost, emission = value_power_energy(
                integrated,
                EnergyValuation(
                    id=f"energy_{year}",
                    expected_power_receipt_digest=integrated["record_digest"],
                    source_asset_id=ctx.asset_id,
                    time_s=year * study.year_duration_s,
                    occurrence_multiplier=1.0,
                    money_basis=study.money_basis,
                    import_price_per_kwh=0.2,
                    export_price_per_kwh=0.0,
                    import_emissions_kg_co2e_kwh=0.4,
                    export_emission_credit_kg_co2e_kwh=0.0,
                    emission_metric_id=study.emission_metric_id,
                    evidence_ids=("reference",),
                    asset_duty_tariff_and_emission_mapping_reviewed=True,
                ),
                ctx,
                engine=engine,
            )
            costs.append(cost)
            emissions.append(emission)
            if frequency is not None:
                costs.append(
                    value_expected_loss(
                        frequency,
                        ExpectedLossValuation(
                            id=f"loss_{year}",
                            expected_frequency_binding_digest=frequency.binding_digest,
                            source_asset_id=ctx.asset_id,
                            condition_id=frequency.condition_id,
                            endpoint=frequency.endpoint,
                            loss_component_id="authored_unplanned_rework",
                            interval_start_s=(year - 1) * study.year_duration_s,
                            interval_finish_s=year * study.year_duration_s,
                            payment_time_s=year * study.year_duration_s,
                            year_duration_s=study.year_duration_s,
                            conditional_mean_loss=Money(amount=25.0, basis=study.money_basis),
                            evidence_ids=("reference",),
                            constant_intensity_conditional_loss_and_payment_reviewed=True,
                        ),
                        ctx,
                    )
                )
        alternatives = tuple(
            a.model_copy(
                update={
                    "cost_events": (*a.cost_events, *costs),
                    "emission_events": (*a.emission_events, *emissions),
                }
            )
            for a in study.alternatives
        )
        native = evaluate_lifecycle(
            study.model_copy(update={"alternatives": alternatives}), ctx, engine=engine
        )
        output = dict(
            adjusted_power_w=adjusted,
            keep_present_cost=native["alternatives"]["keep"]["present_cost"],
            replace_present_cost=native["alternatives"]["replace"]["present_cost"],
        )
        output["replace_minus_keep"] = (
            None
            if native["status"] != "computed"
            else output["replace_present_cost"] - output["keep_present_cost"]
        )
        if frequency is not None:
            output |= dict(
                conditional_error_probability=values["conditional_error_probability"],
                annual_expected_events=frequency.annual_frequency_per_year,
            )
        return EnsembleValues(
            context=ctx,
            values=output,
            units={k: v[0] for k, v in names.items()},
            statuses={
                k: "computed" if value is not None else native["status"] for k, value in output.items()
            },
            native_payload=dict(
                pump=pump.model_dump(mode="json"),
                power_integration=integrated,
                risk=risk_receipt,
                lifecycle=native,
                model_multiplier_is_declared=True,
            ),
        )

    configuration = dict(
        study_digest=digest(study),
        density_kg_m3=1000.0,
        pump_efficiency=0.75,
        motor_efficiency=0.9,
        annual_duration_s=7200000.0,
        tariff_REF_per_kwh=0.2,
        model_discrepancy="declared multiplicative power factor",
        uncertainty_not_estimated_from_observations=True,
        same_source_costs_applied_to_both_alternatives=True,
        risk_provider=None if risk_client is None else risk_client.provider,
    )
    return EnsembleNode(
        id="pump_lifecycle",
        version="1",
        source_digest=file_digest(Path(__file__)),
        configuration=configuration,
        metrics=metrics,
        evaluate=evaluate,
    ), context


if __name__ == "__main__":
    node, context = make_node()
    design = generate_ensemble(make_request(), context)
    result = evaluate_ensemble(design, (node,))
    print(
        json.dumps(
            {k: result[k] for k in ("status", "summaries", "paired_covariances", "record_digest")}, indent=2
        )
    )
