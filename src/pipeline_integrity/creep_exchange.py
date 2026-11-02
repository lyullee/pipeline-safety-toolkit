"""Condition-bounded creep/ageing jobs and explicit period-risk handoff.

Native material engines own constitutive laws and censored-life inference.
Deterministic damage, population scatter and conditional first-failure probability
are retained as different quantities; none supplies demand HEP or annual counts.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import catalog, file_digest
from .material_exchange import MaterialReceipt, evaluate_material_job, prepare_material_job
from .risk_exchange import RiskProbability
from .units import _registry

REVIEWS = {
    "creep.norton": ("fixed_temperature_secondary_creep", "calibration_stress_time_basis"),
    "creep.larson_miller": ("rupture_endpoint_and_material_constant", "log_hours_convention"),
    "creep.time_fraction": ("rupture_lives_and_shared_mechanism", "linear_damage_no_interaction"),
    "creep.lognormal_life_fit": ("specimen_censoring_and_condition", "population_model_assumptions"),
    "creep.conditional_life": ("unchanged_history_and_age_origin", "material_support_and_model_basis"),
    "kinetics.arrhenius": ("reference_rate_units_and_calibration", "common_thermal_mechanism"),
    "degradation.parabolic_oxidation": ("oxide_growth_not_metal_loss", "constant_parabolic_coefficient"),
}
Method = Literal[
    "creep.norton",
    "creep.larson_miller",
    "creep.time_fraction",
    "creep.lognormal_life_fit",
    "creep.conditional_life",
    "kinetics.arrhenius",
    "degradation.parabolic_oxidation",
]


class CreepDomain(Contract):
    material_condition_id: Name
    temperature_k_range: tuple[Positive, Positive]
    stress_pa_range: tuple[Nonnegative, Nonnegative] | None
    maximum_exposure_s: Positive
    evidence_id: Name
    calibration_reviewed: bool = False

    @model_validator(mode="after")
    def ordered(self):
        for bounds in (self.temperature_k_range, self.stress_pa_range):
            if bounds is not None and bounds[0] > bounds[1]:
                raise ValueError("calibration support bounds must be ordered")
        return self


class CreepRequest(Contract):
    method: Method
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    parameters: dict
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    domain: CreepDomain | None = None
    evaluation_temperature_k: Positive | None = None
    reference_rate_unit: Name | None = None
    model_reference_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @field_validator("parameters")
    @classmethod
    def serializable(cls, value):
        return json_value(value)

    @model_validator(mode="after")
    def reviews(self):
        if set(self.applicability_reviews) - set(REVIEWS[self.method]):
            raise ValueError("review keys must belong to the selected creep/ageing method")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return self


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _verify(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("creep source or result changed after sealing")


def evaluate_creep(request: CreepRequest, context: RunContext, *, fit_record=None, client=None):
    """Check declared calibration/meaning, then call the existing strict MaterialJob API."""
    request = CreepRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("creep request and context asset/clock differ")
    evidence = {e.id for e in context.evidence}
    if set(request.evidence_ids) - evidence or request.domain and request.domain.evidence_id not in evidence:
        raise ValueError("creep input/calibration evidence is absent")
    result = dict(
        schema="pipeline_integrity.creep_exchange.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="computed",
        issues=[],
        values={},
        output_units={},
        probability_semantics={},
        native_receipt=None,
        fit_digest=None,
        output_origin="simulation",
        field_or_standard_qualification=False,
        damage_equals_failure_probability=False,
        annual_failure_rate_inferred=False,
        implementation_sha256=file_digest(Path(__file__)),
    )

    def withhold(status, issue):
        result.update(status=status, issues=[issue])
        return _seal(result)

    if any(not request.applicability_reviews.get(key, False) for key in REVIEWS[request.method]):
        return withhold("needs_data", "all method-specific applicability reviews are required")
    p = request.parameters
    if any(v is None for v in p.values()):
        return withhold("needs_data", "unresolved native input; no assumed defaults")
    bounded = request.method in ("creep.norton", "kinetics.arrhenius", "degradation.parabolic_oxidation")
    if bounded:
        domain = request.domain
        if domain is None or not domain.calibration_reviewed:
            return withhold(
                "needs_data", "reviewed material/temperature/stress/time calibration support is required"
            )
        if domain.material_condition_id != request.material_condition_id:
            return withhold("out_of_scope", "calibration material condition differs")
        temperature = p.get("temperature_K", request.evaluation_temperature_k)
        if temperature is None:
            return withhold("needs_data", "absolute evaluation temperature is unresolved")
        if not domain.temperature_k_range[0] <= temperature <= domain.temperature_k_range[1]:
            return withhold("out_of_scope", "temperature is outside declared calibration support")
        if request.method == "creep.norton":
            if domain.stress_pa_range is None:
                return withhold("needs_data", "Norton stress support is unresolved")
            if any(
                not domain.stress_pa_range[0] <= p[k] <= domain.stress_pa_range[1]
                for k in ("stress_Pa", "reference_stress_Pa")
            ):
                return withhold("out_of_scope", "Norton/reference stress is outside calibration support")
        if "exposure_s" in p and p["exposure_s"] > domain.maximum_exposure_s:
            return withhold("out_of_scope", "exposure exceeds declared calibration support")
        if request.method == "kinetics.arrhenius":
            if request.reference_rate_unit is None:
                return withhold("needs_data", "Arrhenius reference-rate units are unresolved")
            si_unit = _registry().Quantity(1.0, request.reference_rate_unit).to_base_units()
            if float(si_unit.magnitude) != 1.0:
                return withhold(
                    "needs_data", "convert the reference rate to declared SI units before evaluation"
                )
            if (
                not domain.temperature_k_range[0]
                <= p["reference_temperature_K"]
                <= domain.temperature_k_range[1]
            ):
                return withhold("out_of_scope", "reference temperature exceeds calibration support")
    if (
        request.method in ("creep.lognormal_life_fit", "creep.conditional_life")
        and p.get("material_condition_id") != request.material_condition_id
    ):
        return withhold("needs_data", "native and exchange material conditions must match")
    if request.method == "creep.conditional_life":
        model = p.get("model")
        if not isinstance(model, dict) or request.model_reference_digest is None:
            return withhold("needs_data", "conditional prediction requires a declared fitted-model digest")
        if digest(model) != request.model_reference_digest:
            raise ValueError("conditional-life fitted model changed")
        if fit_record is not None:
            _verify(fit_record)
            if (
                fit_record["request"]["method"] != "creep.lognormal_life_fit"
                or fit_record["status"] != "computed"
            ):
                return withhold("needs_data", "source life fit is not computed")
            if model != fit_record["values"].get("model"):
                raise ValueError("prediction model differs from its complete source fit")
            result["fit_digest"] = fit_record["record_digest"]
    elif fit_record is not None:
        raise ValueError("a source life fit belongs to conditional-life prediction only")
    job = prepare_material_job(
        request.method,
        p,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    native = evaluate_material_job(job, context, client=client)
    native.check_integrity()
    payload = native.native.payload
    result.update(
        status=payload["status"],
        values=payload["values"],
        output_units=payload["units"],
        probability_semantics=payload["probability_semantics"],
        native_receipt=native.model_dump(mode="json"),
        issues=list(payload["notes"]),
    )
    if request.method == "kinetics.arrhenius":
        result["reference_rate_unit"] = request.reference_rate_unit
    return _seal(result)


class CreepBlock(Contract):
    id: Name
    start_s: Nonnegative
    finish_s: Positive
    stress_pa: Nonnegative | None
    temperature_k: Positive | None
    rupture_life_s: Positive | None
    life_basis: Literal["observed_rupture", "qualified_reference", "conditional_model_quantile", "unresolved"]
    life_quantile: float | None = Field(default=None, gt=0, lt=1)
    evidence_id: Name

    @model_validator(mode="after")
    def support(self):
        if self.finish_s <= self.start_s:
            raise ValueError("creep block must have positive duration")
        if (self.life_basis == "conditional_model_quantile") != (self.life_quantile is not None):
            raise ValueError("model-derived reference lives require an explicit quantile")
        return self


class CreepSequence(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    start_s: Nonnegative
    finish_s: Positive
    blocks: tuple[CreepBlock, ...] = Field(min_length=1, max_length=100000)
    domain: CreepDomain
    stress_basis: Literal["initial_nominal_constant_load", "constant_true_stress"]
    temperature_basis: Literal["nominal_specified", "measured_representative"]
    blocks_life_and_linear_accumulation_reviewed: bool = False
    time_tolerance_s: Nonnegative = 0.0
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.finish_s <= self.start_s or len({b.id for b in self.blocks}) != len(self.blocks):
            raise ValueError("unique blocks and a positive declared history window are required")
        if any(b.start_s < a.finish_s for a, b in zip(self.blocks, self.blocks[1:], strict=False)):
            raise ValueError("creep blocks cannot overlap or be reordered")
        return self


def evaluate_creep_sequence(sequence: CreepSequence, context: RunContext, *, client=None):
    """Pass every declared exposure block to native time-fraction damage; do not infer rupture probability."""
    sequence = CreepSequence.model_validate_json(sequence.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (sequence.asset_id, sequence.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("creep sequence and context asset/clock differ")
    evidence = {e.id for e in context.evidence}
    if {
        sequence.domain.evidence_id,
        *sequence.evidence_ids,
        *(b.evidence_id for b in sequence.blocks),
    } - evidence:
        raise ValueError("creep block/calibration evidence is absent")
    result = dict(
        schema="pipeline_integrity.creep_sequence.v1",
        sequence=sequence.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="computed",
        issues=[],
        native_result=None,
        damage=None,
        exposed_duration_s=math.fsum(b.finish_s - b.start_s for b in sequence.blocks),
        declared_window_s=sequence.finish_s - sequence.start_s,
        gaps_s=[],
        damage_is_probability=False,
        load_order_interaction_modelled=False,
        output_origin="simulation",
        implementation_sha256=file_digest(Path(__file__)),
    )

    def withhold(status, issue):
        result.update(status=status, issues=[issue])
        return _seal(result)

    if not sequence.blocks_life_and_linear_accumulation_reviewed or not sequence.domain.calibration_reviewed:
        return withhold(
            "needs_data", "creep block lives, support and linear damage applicability need review"
        )
    d = sequence.domain
    if d.material_condition_id != sequence.material_condition_id:
        return withhold("out_of_scope", "creep-sequence calibration material differs")
    spans = [
        (sequence.start_s, sequence.blocks[0].start_s),
        *[(a.finish_s, b.start_s) for a, b in zip(sequence.blocks, sequence.blocks[1:], strict=False)],
        (sequence.blocks[-1].finish_s, sequence.finish_s),
    ]
    if any(b < a - sequence.time_tolerance_s for a, b in spans):
        raise ValueError("creep blocks extend beyond the declared history window")
    result["gaps_s"] = [[a, b] for a, b in spans if b - a > sequence.time_tolerance_s]
    if result["gaps_s"]:
        return withhold("needs_data", "unobserved history intervals cannot be filled or omitted")
    if any(
        b.stress_pa is None
        or b.temperature_k is None
        or b.rupture_life_s is None
        or b.life_basis == "unresolved"
        for b in sequence.blocks
    ):
        return withhold(
            "needs_data", "every stress/temperature/exposure block needs its declared reference life"
        )
    if d.stress_pa_range is None:
        return withhold("needs_data", "stress calibration support is unresolved")
    if result["declared_window_s"] > d.maximum_exposure_s or any(
        not d.temperature_k_range[0] <= b.temperature_k <= d.temperature_k_range[1]
        or not d.stress_pa_range[0] <= b.stress_pa <= d.stress_pa_range[1]
        for b in sequence.blocks
    ):
        return withhold("out_of_scope", "creep sequence exceeds declared stress/temperature/time support")
    request = CreepRequest(
        method="creep.time_fraction",
        asset_id=sequence.asset_id,
        location_id=sequence.location_id,
        clock_id=sequence.clock_id,
        material_condition_id=sequence.material_condition_id,
        parameters=dict(
            exposure_s=[b.finish_s - b.start_s for b in sequence.blocks],
            rupture_life_s=[b.rupture_life_s for b in sequence.blocks],
        ),
        applicability_reviews=dict.fromkeys(REVIEWS["creep.time_fraction"], True),
        evidence_ids=tuple(
            dict.fromkeys([*sequence.evidence_ids, d.evidence_id, *(b.evidence_id for b in sequence.blocks)])
        ),
    )
    native = evaluate_creep(request, context, client=client)
    result.update(
        status=native["status"],
        native_result=native,
        damage=native["values"].get("time_fraction_damage"),
        issues=native["issues"],
    )
    return _seal(result)


class CreepRiskMapping(Contract):
    endpoint: Literal["creep_rupture"] = "creep_rupture"
    expected_prediction_digest: Name
    event_id: Name
    physical_scope_id: Name
    condition_id: Name
    period_start_s: Nonnegative
    period_finish_s: Positive
    fresh_material_origin_s: float
    conditioning: Name
    failure_definition: Name
    age_origin_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    physical_event_and_age_mapping_reviewed: bool = False
    time_tolerance_s: Nonnegative = 1e-6


def creep_risk_probability(prediction: dict, mapping: CreepRiskMapping):
    """Bind a conditional first-failure period to existing mission-probability contracts, without annualization."""
    _verify(prediction)
    mapping = CreepRiskMapping.model_validate_json(mapping.model_dump_json())
    if (
        prediction.get("schema") != "pipeline_integrity.creep_exchange.v1"
        or prediction["request"]["method"] != "creep.conditional_life"
        or prediction["record_digest"] != mapping.expected_prediction_digest
    ):
        raise ValueError("risk source must be the complete bound conditional-life prediction")
    context = RunContext.model_validate_json(json.dumps(prediction["context"]))
    if {mapping.age_origin_evidence_id, *mapping.evidence_ids} - {e.id for e in context.evidence}:
        raise ValueError("risk event, age origin and period mapping require source evidence")
    result = dict(
        schema="pipeline_integrity.creep_risk_binding.v1",
        prediction_digest=prediction["record_digest"],
        mapping=mapping.model_dump(mode="json"),
        status=prediction["status"],
        issues=[],
        probability=None,
        expected_counts_or_demand_probability_inferred=False,
        loss_of_containment_model_included=False,
    )
    if prediction["status"] != "computed" or not mapping.physical_event_and_age_mapping_reviewed:
        result.update(
            status="needs_data" if prediction["status"] == "computed" else prediction["status"],
            issues=["computed prediction and reviewed physical event/age/period mapping required"],
        )
        return _seal(result)
    p = prediction["request"]["parameters"]
    native = MaterialReceipt.model_validate_json(json.dumps(prediction["native_receipt"]))
    native.check_integrity()
    if (
        native.job.model_id != "creep.conditional_life"
        or native.context != context
        or native.job.parameters != p
        or native.native.status != "computed"
        or native.native.payload["values"] != prediction["values"]
        or native.job.probability_semantics != prediction["probability_semantics"]
    ):
        raise ValueError("risk prediction differs from its original material receipt")
    age = mapping.period_start_s - mapping.fresh_material_origin_s
    duration = mapping.period_finish_s - mapping.period_start_s
    if (
        duration <= 0
        or age < 0
        or not math.isclose(age, p["current_age_s"], rel_tol=0, abs_tol=mapping.time_tolerance_s)
        or not math.isclose(duration, p["horizon_s"], rel_tol=0, abs_tol=mapping.time_tolerance_s)
    ):
        raise ValueError("risk period and fresh-material age do not match the native conditional interval")
    if (
        prediction["probability_semantics"].get("probability_given_survival")
        != "conditional_first_failure_in_period"
    ):
        raise ValueError("native probability meaning differs from conditional first failure")
    probability = prediction["values"]["probability_given_survival"]
    payload = dict(probability=probability, prediction=prediction, mapping=mapping.model_dump(mode="json"))
    event = RiskProbability(
        id=mapping.event_id,
        probability=probability,
        measure="mission_probability",
        condition_id=mapping.condition_id,
        conditioning=mapping.conditioning,
        opportunity_id=mapping.physical_scope_id,
        opportunity_definition=f"One declared creep first-failure interval [{mapping.period_start_s}, {mapping.period_finish_s}] s conditional on survival at its start",
        failure_definition=mapping.failure_definition,
        role="equipment",
        dependency_groups=("creep_model:" + p["model"]["training_digest"],),
        evidence_ids=tuple(dict.fromkeys([*mapping.evidence_ids, mapping.age_origin_evidence_id])),
        qualification="research",
        context=context,
        source_kind="derived",
        source_payload=payload,
        source_digest=digest(payload),
    )
    result.update(status="computed", probability=event.model_dump(mode="json"))
    return _seal(result)


def load_cmsx6_specimens(workbook):
    """Reuse the owner's primary-data reader, retaining each physical specimen and censor endpoint."""
    path = Path(workbook)
    dataset = next(d for d in catalog() if d.id == "cmsx6_creep")
    asset = next(a for a in dataset.assets if a.filename.endswith(".xlsx"))
    algorithm, expected = asset.checksum.split(":", 1)
    if file_digest(path, algorithm) != expected:
        raise ValueError("CMSX-6 workbook differs from the pinned supplier checksum")
    from matintegrity.creep_data import read_cmsx6_creep

    frame = read_cmsx6_creep(path)
    return _seal(
        dict(
            schema="pipeline_integrity.creep_specimens.v1",
            dataset_id=dataset.id,
            source=dataset.source,
            citation=dataset.citation,
            creators=list(dataset.creators),
            license_id=dataset.license_id,
            source_sha256=file_digest(path),
            supplier_checksum=asset.checksum,
            records=json_value(frame.to_dict(orient="records")),
            native_reader_metadata=dict(frame.attrs),
            native_reader_sha256=file_digest(Path(read_cmsx6_creep.__code__.co_filename)),
            specimen_rows_are_waveform_samples=False,
            field_transfer_qualified=False,
            implementation_sha256=file_digest(Path(__file__)),
        )
    )
