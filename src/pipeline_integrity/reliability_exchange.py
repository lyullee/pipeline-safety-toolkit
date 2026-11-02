"""Physical event intensity and survival-period exchange around the existing material owner.

Posterior mean intensity is not a posterior predictive occurrence probability.
The period result is conditional first physical failure, never demand HEP.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialReceipt, evaluate_material_job, prepare_material_job
from .risk_exchange import RiskFrequency, RiskProbability

REVIEWS = {
    "reliability.gamma_poisson": (
        "common_physical_event_population_and_constant_intensity",
        "confirmed_counts_complete_ascertainment_and_exposure",
        "prior_shape_and_exposure_basis",
    ),
    "reliability.weibull_interval": (
        "first_failure_population_shape_scale_and_age_origin",
        "survival_condition_and_interval_support",
    ),
}
REQUIRED = {
    "reliability.gamma_poisson": (
        "prior_shape",
        "prior_exposure_s",
        "confirmed_events",
        "exposure_s",
        "complete_ascertainment",
    ),
    "reliability.weibull_interval": ("shape", "scale_s", "current_age_s", "horizon_s"),
}


class PhysicalReliabilityRequest(Contract):
    method: Literal["reliability.gamma_poisson", "reliability.weibull_interval"]
    asset_id: Name
    location_id: Name
    clock_id: Name
    physical_scope_id: Name
    event_definition: Name
    population_definition: Name
    parameters: dict
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def meaning(self):
        json_value(self.parameters)
        if set(self.applicability_reviews) - set(REVIEWS[self.method]):
            raise ValueError("physical reliability reviews must belong to the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("physical reliability evidence must be distinct")
        return self


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _scope(request, context, evidence_ids=()):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("physical reliability asset/clock and context differ")
    ids = [item.id for item in context.evidence]
    if len(ids) != len(set(ids)) or set((*request.evidence_ids, *evidence_ids)) - set(ids):
        raise ValueError("physical reliability evidence must be distinct and present in context")


def _ready(request):
    if any(not request.applicability_reviews.get(key, False) for key in REVIEWS[request.method]):
        return "all method-specific physical reliability reviews are required"
    if any(request.parameters.get(key) is None for key in REQUIRED[request.method]):
        return (
            "physical count, exposure, prior or survival parameters are unresolved; no defaults are supplied"
        )
    if (
        request.method == "reliability.gamma_poisson"
        and request.parameters["complete_ascertainment"] is not True
    ):
        return "incomplete or unknown event ascertainment requires a separate observation model"
    return None


def evaluate_physical_reliability(request: PhysicalReliabilityRequest, context: RunContext, *, client=None):
    request = PhysicalReliabilityRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    _scope(request, context)
    body = dict(
        schema="pipeline_integrity.physical_reliability.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="needs_data",
        issues=[],
        values={},
        output_units={},
        probability_semantics={},
        native_receipt=None,
        statistic="posterior_mean_event_intensity"
        if request.method == "reliability.gamma_poisson"
        else "conditional_first_physical_failure_in_period",
        posterior_predictive_occurrence_probability_computed=False,
        demand_hep_or_field_calibration=False,
        output_origin="simulation",
        implementation_sha256=file_digest(Path(__file__)),
    )
    issue = _ready(request)
    if issue:
        body["issues"] = [issue]
        return _seal(body)
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    receipt = evaluate_material_job(job, context, client=client)
    receipt.check_integrity()
    payload = receipt.native.payload
    body.update(
        status=payload["status"],
        values=payload["values"],
        output_units=payload["units"],
        probability_semantics=payload["probability_semantics"],
        native_receipt=receipt.model_dump(mode="json"),
        issues=list(payload["notes"]),
    )
    return _seal(body)


def _source(record, method, evidence_ids):
    if record.get("schema") != "pipeline_integrity.physical_reliability.v1" or record.get(
        "record_digest"
    ) != digest({key: value for key, value in record.items() if key != "record_digest"}):
        raise ValueError("physical reliability source or result changed after sealing")
    request = PhysicalReliabilityRequest.model_validate_json(json.dumps(record["request"]))
    context = RunContext.model_validate_json(json.dumps(record["context"]))
    _scope(request, context, evidence_ids)
    if request.method != method:
        raise ValueError("physical reliability mapping requires its matching source method")
    if record["status"] == "computed":
        receipt = MaterialReceipt.model_validate_json(json.dumps(record["native_receipt"]))
        receipt.check_integrity()
        payload = receipt.native.payload
        if (
            _ready(request) is not None
            or receipt.context != context
            or receipt.job.model_id != method
            or receipt.job.asset_id != request.asset_id
            or receipt.job.location_id != request.location_id
            or receipt.job.clock_id != request.clock_id
            or receipt.job.parameters != request.parameters
            or receipt.job.evidence_ids != request.evidence_ids
            or receipt.native.status != "computed"
            or payload["values"] != record["values"]
            or payload["units"] != record["output_units"]
            or payload["probability_semantics"] != record["probability_semantics"]
        ):
            raise ValueError("physical reliability source differs from its original material receipt")
    return request, context


class PhysicalFrequencyMapping(Contract):
    event_id: Name
    physical_scope_id: Name
    condition_id: Name
    conditioning: Name
    endpoint: Literal["event_occurrence", "loss_of_containment"]
    seconds_per_year: Positive
    year_basis_id: Name
    expected_source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    event_scope_endpoint_and_condition_reviewed: bool = False
    constant_intensity_projection_reviewed: bool = False
    year_basis_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("frequency mapping evidence must be distinct")
        return self


def physical_frequency(record, mapping: PhysicalFrequencyMapping):
    """Convert a reviewed posterior mean physical intensity to a declared year basis only."""
    mapping = PhysicalFrequencyMapping.model_validate_json(mapping.model_dump_json())
    request, context = _source(record, "reliability.gamma_poisson", mapping.evidence_ids)
    if mapping.physical_scope_id != request.physical_scope_id:
        raise ValueError("frequency physical scope differs from its source")
    if (
        mapping.expected_source_digest is not None
        and mapping.expected_source_digest != record["record_digest"]
    ):
        raise ValueError("frequency mapping source digest differs from its reviewed source")
    body = dict(
        schema="pipeline_integrity.physical_frequency_binding.v1",
        source_record_digest=record["record_digest"],
        mapping=mapping.model_dump(mode="json"),
        status="needs_data",
        issues=[],
        frequency=None,
        posterior_predictive_occurrence_probability_computed=False,
        field_calibration_or_observed_frequency=False,
    )
    if record["status"] != "computed" or not all(
        (
            mapping.event_scope_endpoint_and_condition_reviewed,
            mapping.constant_intensity_projection_reviewed,
            mapping.year_basis_reviewed,
            mapping.expected_source_digest is not None,
        )
    ):
        body["issues"] = [
            "computed intensity and reviewed physical scope, projection and year basis are required"
        ]
        return _seal(body)
    intensity = record["values"]["mean_frequency_per_s"]
    if (
        isinstance(intensity, bool)
        or not isinstance(intensity, (int, float))
        or not math.isfinite(intensity)
        or intensity < 0
        or record["output_units"].get("mean_frequency_per_s") != "1/s"
    ):
        raise ValueError("native intensity must be finite nonnegative physical events per second")
    annual = intensity * mapping.seconds_per_year
    if not math.isfinite(annual):
        raise ValueError("annual physical intensity exceeds finite numeric support")
    payload = dict(
        annual_frequency_per_year=annual,
        statistic="posterior_mean_event_intensity",
        event_definition=request.event_definition,
        population_definition=request.population_definition,
        prediction=record,
        mapping=mapping.model_dump(mode="json"),
    )
    frequency = RiskFrequency(
        id=mapping.event_id,
        annual_frequency_per_year=annual,
        condition_id=mapping.condition_id,
        endpoint=mapping.endpoint,
        consumed_event_ids=(mapping.event_id,),
        evidence_ids=tuple(dict.fromkeys((*request.evidence_ids, *mapping.evidence_ids))),
        context=context,
        source_payload=payload,
        source_digest=digest(payload),
    )
    body.update(status="computed", frequency=frequency.model_dump(mode="json"))
    return _seal(body)


class WeibullPeriodMapping(Contract):
    event_id: Name
    physical_scope_id: Name
    endpoint: Literal["first_physical_failure", "first_loss_of_containment"]
    condition_id: Name
    conditioning: Name
    period_id: Name
    period_definition: Name
    period_start_s: Nonnegative
    period_finish_s: Positive
    age_origin_s: float
    expected_source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    event_scope_and_period_reviewed: bool = False
    survival_and_age_origin_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def period(self):
        if self.period_finish_s <= self.period_start_s or self.age_origin_s > self.period_start_s:
            raise ValueError("physical survival period must be positive and follow the age origin")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("period mapping evidence must be distinct")
        return self


def physical_period_probability(record, mapping: WeibullPeriodMapping):
    """Retain the owner's conditional first-failure probability, age and actual clock interval."""
    mapping = WeibullPeriodMapping.model_validate_json(mapping.model_dump_json())
    request, context = _source(record, "reliability.weibull_interval", mapping.evidence_ids)
    if mapping.physical_scope_id != request.physical_scope_id:
        raise ValueError("period physical scope differs from its source")
    if (
        mapping.expected_source_digest is not None
        and mapping.expected_source_digest != record["record_digest"]
    ):
        raise ValueError("period mapping source digest differs from its reviewed source")
    body = dict(
        schema="pipeline_integrity.physical_period_binding.v1",
        source_record_digest=record["record_digest"],
        mapping=mapping.model_dump(mode="json"),
        status="needs_data",
        issues=[],
        probability=None,
        demand_failure_probability_or_annual_frequency_inferred=False,
        field_calibration=False,
    )
    if record["status"] != "computed" or not (
        mapping.event_scope_and_period_reviewed
        and mapping.survival_and_age_origin_reviewed
        and mapping.expected_source_digest is not None
    ):
        body["issues"] = [
            "computed conditional lifetime and reviewed event, survival, age and period are required"
        ]
        return _seal(body)
    p = request.parameters
    if not math.isclose(
        mapping.period_start_s - mapping.age_origin_s, p["current_age_s"], rel_tol=1e-12, abs_tol=1e-9
    ) or not math.isclose(
        mapping.period_finish_s - mapping.period_start_s, p["horizon_s"], rel_tol=1e-12, abs_tol=1e-9
    ):
        raise ValueError("physical period and age differ from the native survival interval")
    if (
        record["probability_semantics"].get("probability_given_survival")
        != "conditional_first_failure_in_period"
        or record["output_units"].get("probability_given_survival") != "1"
    ):
        raise ValueError(
            "native physical probability must retain its dimensionless conditional first-failure meaning"
        )
    probability = record["values"]["probability_given_survival"]
    payload = dict(
        probability=probability,
        event_definition=request.event_definition,
        population_definition=request.population_definition,
        prediction=record,
        mapping=mapping.model_dump(mode="json"),
    )
    event = RiskProbability(
        id=mapping.event_id,
        probability=probability,
        measure="mission_probability",
        condition_id=mapping.condition_id,
        conditioning=mapping.conditioning,
        opportunity_id=mapping.period_id,
        opportunity_definition=(
            f"{mapping.period_definition}; clock={context.clock_id}; "
            f"[{mapping.period_start_s}, {mapping.period_finish_s}] s conditional on survival at its start"
        ),
        failure_definition=request.event_definition,
        role="equipment",
        evidence_ids=tuple(dict.fromkeys((*request.evidence_ids, *mapping.evidence_ids))),
        qualification="research",
        context=context,
        source_kind="derived",
        source_payload=payload,
        source_digest=digest(payload),
    )
    body.update(status="computed", probability=event.model_dump(mode="json"))
    return _seal(body)
