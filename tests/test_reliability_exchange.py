"""Physical source, observation and time meaning; numerical owner checks run separately."""

import json
import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.reliability_exchange import (
    REQUIRED,
    REVIEWS,
    evaluate_physical_reliability,
    physical_frequency,
    physical_period_probability,
)
from pipeline_integrity.risk_exchange import DemandExposure, HRARiskClient, RiskProbability


def base():
    return runpy.run_path("examples/physical_reliability.py")["make_cases"]()


class OwnerSpy:
    """Fixed owner responses exercise delegation, without copying the native statistical models."""

    def __init__(
        self, *, semantics="conditional_first_failure_in_period", intensity_unit="1/s", probability_unit="1"
    ):
        self.calls = []
        self.semantics = semantics
        self.intensity_unit, self.probability_unit = intensity_unit, probability_unit

    def describe(self, method):
        gamma = method == "reliability.gamma_poisson"
        return dict(
            model_id=method,
            model_version="1.0.0",
            input_schema=dict(type="object"),
            output_units=dict(mean_frequency_per_s=self.intensity_unit)
            if gamma
            else dict(probability_given_survival=self.probability_unit),
            probability_semantics={} if gamma else dict(probability_given_survival=self.semantics),
        )

    def evaluate(self, method, parameters, context, *, location_id, input_mode):
        self.calls.append((method, deepcopy(parameters), input_mode))
        description = self.describe(method)
        payload = dict(
            schema_version="matintegrity.assessment.v1",
            model_id=method,
            model_version="1.0.0",
            inputs=deepcopy(parameters),
            context=dict(
                asset_id=context.asset_id,
                location_id=location_id,
                time_basis=context.clock_id,
                metadata=dict(
                    pipeline_context_digest=digest(context),
                    pipeline_scenario_id=context.scenario_id,
                    pipeline_origin=context.origin,
                    pipeline_input_validation_mode=input_mode,
                ),
            ),
            units=description["output_units"],
            probability_semantics=description["probability_semantics"],
            values=dict(mean_frequency_per_s=0.001)
            if method == "reliability.gamma_poisson"
            else dict(probability_given_survival=0.25),
            status="computed",
            notes=[],
        )
        payload["input_digest"] = digest(
            {key: payload[key] for key in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


def records(*, client=None):
    cases, frequency, period, context = base()
    client = OwnerSpy() if client is None else client
    gamma = evaluate_physical_reliability(cases["reliability.gamma_poisson"], context, client=client)
    weibull = evaluate_physical_reliability(cases["reliability.weibull_interval"], context, client=client)
    return (
        gamma,
        frequency.model_copy(update={"expected_source_digest": gamma["record_digest"]}),
        weibull,
        period.model_copy(update={"expected_source_digest": weibull["record_digest"]}),
    )


@pytest.mark.parametrize("method", list(REVIEWS))
def test_unreviewed_methods_withhold_before_loading_the_owner(method):
    cases, _, _, context = base()
    result = evaluate_physical_reliability(
        cases[method].model_copy(update={"applicability_reviews": {}}), context
    )
    assert result["status"] == "needs_data" and result["native_receipt"] is None


@pytest.mark.parametrize("method,key", [(method, key) for method, keys in REQUIRED.items() for key in keys])
def test_missing_count_exposure_prior_or_survival_inputs_do_not_gain_defaults(method, key):
    cases, _, _, context = base()
    request = cases[method]
    parameters = request.parameters.copy()
    parameters.pop(key)
    result = evaluate_physical_reliability(request.model_copy(update={"parameters": parameters}), context)
    assert result["status"] == "needs_data" and result["values"] == {}


@pytest.mark.parametrize("value", [None, False])
def test_incomplete_or_unknown_event_capture_never_becomes_a_physical_rate(value):
    cases, _, _, context = base()
    request = cases["reliability.gamma_poisson"]
    result = evaluate_physical_reliability(
        request.model_copy(update={"parameters": request.parameters | dict(complete_ascertainment=value)}),
        context,
    )
    assert result["status"] == "needs_data" and result["native_receipt"] is None


def test_zero_events_and_prior_are_delegated_unchanged_using_strict_json():
    cases, _, _, context = base()
    request = cases["reliability.gamma_poisson"]
    parameters = request.parameters | dict(confirmed_events=0)
    owner = OwnerSpy()
    result = evaluate_physical_reliability(
        request.model_copy(update={"parameters": parameters}), context, client=owner
    )
    assert owner.calls == [(request.method, parameters, "json")]
    assert result["native_receipt"]["job"]["parameters"] == parameters
    assert result["request"]["parameters"]["prior_shape"] == 2.0
    assert not result["posterior_predictive_occurrence_probability_computed"]
    assert not result["demand_hep_or_field_calibration"]


def test_asset_clock_evidence_and_method_review_scope_are_not_relabelled():
    cases, _, _, context = base()
    request = cases["reliability.gamma_poisson"]
    with pytest.raises(ValueError, match="asset/clock"):
        evaluate_physical_reliability(request, context.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError, match="evidence"):
        evaluate_physical_reliability(request.model_copy(update={"evidence_ids": ("missing",)}), context)
    with pytest.raises(ValueError, match="selected method"):
        evaluate_physical_reliability(
            request.model_copy(update={"applicability_reviews": {"wrong": True}}), context
        )


@pytest.mark.parametrize(
    "field",
    [
        "event_scope_endpoint_and_condition_reviewed",
        "constant_intensity_projection_reviewed",
        "year_basis_reviewed",
        "expected_source_digest",
    ],
)
def test_frequency_requires_independent_mapping_and_year_reviews(field):
    gamma, mapping, _, _ = records()
    value = None if field == "expected_source_digest" else False
    result = physical_frequency(gamma, mapping.model_copy(update={field: value}))
    assert result["status"] == "needs_data" and result["frequency"] is None


def test_frequency_uses_the_declared_year_and_keeps_the_original_mean_intensity_source():
    gamma, mapping, _, _ = records()
    result = physical_frequency(gamma, mapping.model_copy(update={"seconds_per_year": 31536000.0}))
    frequency = result["frequency"]
    assert frequency["annual_frequency_per_year"] == pytest.approx(31536.0)
    assert frequency["source_payload"]["prediction"] == gamma
    assert frequency["source_payload"]["statistic"] == "posterior_mean_event_intensity"
    assert not result["posterior_predictive_occurrence_probability_computed"]


@pytest.mark.parametrize(
    "field", ["event_scope_and_period_reviewed", "survival_and_age_origin_reviewed", "expected_source_digest"]
)
def test_survival_mapping_requires_period_age_and_pinned_source_reviews(field):
    _, _, weibull, mapping = records()
    value = None if field == "expected_source_digest" else False
    result = physical_period_probability(weibull, mapping.model_copy(update={field: value}))
    assert result["status"] == "needs_data" and result["probability"] is None


@pytest.mark.parametrize("change", [{"age_origin_s": 0.0}, {"period_finish_s": 160.0}])
def test_period_and_age_cannot_change_the_original_native_survival_interval(change):
    _, _, weibull, mapping = records()
    with pytest.raises(ValueError, match="survival interval"):
        physical_period_probability(weibull, mapping.model_copy(update=change))


def test_joint_clock_shift_preserves_age_horizon_and_survival_probability():
    _, _, weibull, mapping = records()
    first = physical_period_probability(weibull, mapping)
    shifted = mapping.model_copy(
        update={"period_start_s": 200.0, "period_finish_s": 250.0, "age_origin_s": 150.0}
    )
    second = physical_period_probability(weibull, shifted)
    assert first["probability"]["probability"] == second["probability"]["probability"] == 0.25
    assert first["probability"]["measure"] == "mission_probability"
    assert first["probability"]["role"] == "equipment"


def test_period_probability_cannot_be_annualized_as_demand_hep():
    _, _, weibull, mapping = records()
    event = RiskProbability.model_validate_json(
        json.dumps(physical_period_probability(weibull, mapping)["probability"])
    )
    client = HRARiskClient(api=object(), provider=dict(id="never_called"))
    result = client.annualize(
        event,
        DemandExposure(
            opportunities_per_year=1.0,
            opportunity_id=event.opportunity_id,
            opportunity_definition=event.opportunity_definition,
            condition_id=event.condition_id,
            evidence_id="authored",
        ),
    )
    assert result["status"] != "exchange_checked" and not result["frequencies"]


def test_wrong_source_method_physical_scope_or_pinned_digest_is_rejected():
    gamma, frequency, weibull, period = records()
    with pytest.raises(ValueError, match="matching source method"):
        physical_frequency(weibull, frequency)
    with pytest.raises(ValueError, match="physical scope"):
        physical_period_probability(weibull, period.model_copy(update={"physical_scope_id": "other"}))
    with pytest.raises(ValueError, match="source digest"):
        physical_frequency(gamma, frequency.model_copy(update={"expected_source_digest": "0" * 64}))


def test_source_and_nested_receipt_mutations_are_detected_even_if_outer_hash_is_recomputed():
    gamma, mapping, _, _ = records()
    changed = deepcopy(gamma)
    changed["values"]["mean_frequency_per_s"] = 0.5
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="original material receipt"):
        physical_frequency(changed, mapping)
    changed = deepcopy(gamma)
    changed["native_receipt"]["native"]["payload"]["values"]["mean_frequency_per_s"] = 0.5
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="payload changed"):
        physical_frequency(changed, mapping)


def test_unscoped_native_probability_cannot_be_relabelled_as_conditional_survival():
    _, _, weibull, mapping = records(client=OwnerSpy(semantics="unscoped_event_probability"))
    with pytest.raises(ValueError, match="conditional first-failure"):
        physical_period_probability(weibull, mapping)


@pytest.mark.parametrize("method", ["frequency", "period"])
def test_owner_unit_changes_cannot_be_silently_accepted_by_a_risk_mapping(method):
    gamma, frequency, weibull, period = records(
        client=OwnerSpy(intensity_unit="1/year", probability_unit="s")
    )
    with pytest.raises(ValueError):
        physical_frequency(gamma, frequency) if method == "frequency" else physical_period_probability(
            weibull, period
        )


def test_withheld_source_cannot_publish_a_frequency_even_with_mapping_reviews():
    cases, mapping, _, context = base()
    request = cases["reliability.gamma_poisson"]
    record = evaluate_physical_reliability(
        request.model_copy(update={"parameters": request.parameters | dict(complete_ascertainment=False)}),
        context,
    )
    result = physical_frequency(
        record, mapping.model_copy(update={"expected_source_digest": record["record_digest"]})
    )
    assert result["status"] == "needs_data" and result["frequency"] is None
