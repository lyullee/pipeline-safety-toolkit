"""Condition-bounded penetration, contact wear and same-exposure area maxima.

The existing material owner supplies the laws, fitting and bootstrap algorithms.
Spatial exceedance and deterministic loss are not period failure probabilities.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, MaterialReceipt, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard

REVIEWS = {
    "corrosion.power_law": (
        "calibrated_quantity_reference_time_and_exposure_origin",
        "unchanged_supported_material_environment_population",
    ),
    "degradation.archard_wear": (
        "matched_contact_regime_coefficient_and_hardness",
        "normal_load_accumulated_distance_and_distributed_area",
    ),
    "corrosion.gumbel_area_maximum": (
        "equivalent_independent_complete_block_maxima_at_one_exposure",
        "surface_population_area_transfer_and_tail_diagnostics",
    ),
}
INPUT_UNITS = {
    "corrosion.power_law": {
        "coefficient_m": "m",
        "exponent": "1",
        "exposure_s": "s",
        "reference_time_s": "s",
    },
    "degradation.archard_wear": {
        "dimensionless_wear_coefficient": "1",
        "normal_force_N": "N",
        "sliding_distance_m": "m",
        "hardness_Pa": "Pa",
        "distributed_area_m2": "m2",
    },
    "corrosion.gumbel_area_maximum": {
        "maximum_depth_m": "m",
        "block_area_m2": "m2",
        "depth_threshold_m": "m",
    },
}
GUMBEL_FIELDS = (
    "population_id",
    "exposure_condition_id",
    "block_ids",
    "maximum_depth_m",
    "block_area_m2",
    "target_block_count",
    "independent_equivalent_blocks",
    "same_exposure_and_surface_population",
    "complete_uncensored_block_maxima",
    "maximum_target_to_sampled_area_ratio",
    "depth_threshold_m",
    "quantile_probability",
    "confidence_level",
    "diagnostic_resamples",
    "parameter_resamples",
    "seed",
    "minimum_goodness_of_fit_pvalue",
    "maximum_negative_depth_probability",
)
FIELDS = {
    **{m: tuple(units) for m, units in INPUT_UNITS.items()},
    "corrosion.gumbel_area_maximum": GUMBEL_FIELDS,
}


class LossDomain(Contract):
    material_condition_id: Name
    environment_or_contact_id: Name
    population_id: Name
    quantity_meaning: Literal["mean_penetration", "maximum_depth", "wear_volume"]
    parameter_support_id: Name
    evidence_id: Name
    exposure_origin_id: Name | None = None
    exposure_range_s: tuple[Nonnegative, Nonnegative] | None = None
    normal_force_range_N: tuple[Nonnegative, Nonnegative] | None = None
    sliding_distance_range_m: tuple[Nonnegative, Nonnegative] | None = None
    hardness_range_Pa: tuple[Positive, Positive] | None = None
    calibration_reviewed: bool = False

    @model_validator(mode="after")
    def ordered(self):
        for bounds in (
            self.exposure_range_s,
            self.normal_force_range_N,
            self.sliding_distance_range_m,
            self.hardness_range_Pa,
        ):
            if bounds is not None and bounds[0] > bounds[1]:
                raise ValueError("loss calibration support bounds must be ordered")
        return self


class CorrosionWearRequest(Contract):
    method: Literal["corrosion.power_law", "degradation.archard_wear", "corrosion.gumbel_area_maximum"]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name | None
    environment_or_contact_id: Name | None
    population_id: Name | None
    quantity_meaning: Literal["mean_penetration", "maximum_depth", "wear_volume", "unresolved"]
    exposure_origin_id: Name | None = None
    exposure_condition_id: Name | None = None
    parameters: dict | None
    input_units: dict[str, str]
    domain: LossDomain | None = None
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_records: int = Field(default=5000, ge=1, le=5000)
    maximum_total_resamples: int = Field(default=3998, ge=198, le=3998)
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def fields(self):
        json_value(self.parameters)
        if self.input_units != INPUT_UNITS[self.method]:
            raise ValueError("loss inputs require the explicitly declared SI/fraction basis")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS[self.method]):
            raise ValueError("loss reviews must belong to the selected method")
        if self.parameters is not None and set(self.parameters) - set(FIELDS[self.method]):
            raise ValueError("unknown native loss-model parameter")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("loss evidence IDs must be distinct")
        return self


def _unknown(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_unknown(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_unknown(v) for v in value)
    return False


def evaluate_corrosion_wear(
    request: CorrosionWearRequest,
    context: RunContext,
    *,
    client=None,
    pinned_job: MaterialJob | None = None,
    owner_source_root=None,
):
    """Retain source support and scalar meanings before invoking the owner's model."""
    if not isinstance(request, CorrosionWearRequest) or not isinstance(context, RunContext):
        raise TypeError("CorrosionWearRequest and RunContext are required")
    if request.method == "corrosion.gumbel_area_maximum" and isinstance(request.parameters, dict):
        if any(
            isinstance(request.parameters.get(key), (list, tuple))
            and len(request.parameters[key]) > request.maximum_records
            for key in ("block_ids", "maximum_depth_m")
        ):
            raise ValueError("block observation budget exceeded before serialization")
    raw = json_value(request.model_dump(mode="python"))
    context_raw = json_value(context.model_dump(mode="python"))
    request = CorrosionWearRequest.model_validate_json(json.dumps(raw, allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(context_raw, allow_nan=False))
    p = request.parameters
    if p is not None and request.method == "corrosion.gumbel_area_maximum":
        if any(
            isinstance(p.get(k), (list, tuple)) and len(p[k]) > request.maximum_records
            for k in ("block_ids", "maximum_depth_m")
        ):
            raise ValueError("block observation budget exceeded without truncation")
        resamples = [p.get(k) for k in ("diagnostic_resamples", "parameter_resamples")]
        if all(type(v) is int for v in resamples) and sum(resamples) > request.maximum_total_resamples:
            raise ValueError("declared bootstrap/goodness-of-fit budget exceeded")
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("loss request and context asset/clock differ")
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)) or not set(request.evidence_ids) <= set(ids):
        raise ValueError("loss evidence must be unique and present in context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("review evidence must occur in the selected source evidence")
    domain = request.domain
    if domain is not None and domain.evidence_id not in request.evidence_ids:
        raise ValueError("loss calibration evidence is absent from selected source evidence")
    issues = []
    status = "needs_data"
    for key in REVIEWS[request.method]:
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids:
            issues.append(f"review and evidence required: {key}")
    if p is None or set(FIELDS[request.method]) - set(p) or _unknown(p):
        issues.append("unresolved native loss-model inputs; no defaults or imputation")
    if (
        any(
            v is None
            for v in (request.material_condition_id, request.environment_or_contact_id, request.population_id)
        )
        or request.quantity_meaning == "unresolved"
    ):
        issues.append("material/environment/population and physical quantity meaning are unresolved")
    if request.method == "corrosion.gumbel_area_maximum":
        if request.quantity_meaning not in ("maximum_depth", "unresolved"):
            raise ValueError("spatial block maxima cannot represent average loss or wear volume")
        if request.exposure_condition_id is None:
            issues.append("one explicit exposure condition is required for area maxima")
        if not issues and (
            p["population_id"] != request.population_id
            or p["exposure_condition_id"] != request.exposure_condition_id
        ):
            raise ValueError("native area population or exposure identity differs")
    else:
        if domain is None or not domain.calibration_reviewed:
            issues.append("reviewed material/contact/quantity calibration support is required")
        elif (
            all(
                v is not None
                for v in (
                    request.material_condition_id,
                    request.environment_or_contact_id,
                    request.population_id,
                )
            )
            and request.quantity_meaning != "unresolved"
            and (
                domain.material_condition_id,
                domain.environment_or_contact_id,
                domain.population_id,
                domain.quantity_meaning,
            )
            != (
                request.material_condition_id,
                request.environment_or_contact_id,
                request.population_id,
                request.quantity_meaning,
            )
        ):
            status = "out_of_scope"
            issues.append("calibration material, condition, population or endpoint differs")
        elif request.method == "corrosion.power_law":
            if request.quantity_meaning not in ("mean_penetration", "maximum_depth", "unresolved"):
                raise ValueError("power-law penetration cannot represent wear volume")
            if (
                request.exposure_origin_id is None
                or domain.exposure_origin_id is None
                or domain.exposure_range_s is None
            ):
                issues.append("reviewed physical exposure origin and exposure support are required")
            elif request.exposure_origin_id != domain.exposure_origin_id:
                status = "out_of_scope"
                issues.append("model and observation exposure origins differ")
            elif (
                p is not None
                and type(p.get("exposure_s")) in (float, int)
                and not domain.exposure_range_s[0] <= p["exposure_s"] <= domain.exposure_range_s[1]
            ):
                status = "out_of_scope"
                issues.append("exposure is outside declared calibration support")
        else:
            if request.quantity_meaning != "wear_volume" and request.quantity_meaning != "unresolved":
                raise ValueError("Archard volume cannot be relabelled as a maximum depth")
            for key, bounds in [
                ("normal_force_N", domain.normal_force_range_N),
                ("sliding_distance_m", domain.sliding_distance_range_m),
                ("hardness_Pa", domain.hardness_range_Pa),
            ]:
                if bounds is None:
                    issues.append(f"calibration support unresolved: {key}")
                elif (
                    p is not None
                    and type(p.get(key)) in (float, int)
                    and not bounds[0] <= p[key] <= bounds[1]
                ):
                    status = "out_of_scope"
                    issues.append(f"{key} is outside declared calibration support")
    body = {
        "schema": "pipeline_integrity.corrosion_wear.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": status,
        "values": {},
        "output_units": {},
        "probability_semantics": {},
        "issues": issues,
        "native_receipt": None,
        "source_pin": None,
        "output_origin": "simulation",
        "spatial_exceedance_is_period_failure_probability": False,
        "loss_is_HRA_or_observed_event_frequency": False,
        "field_or_standard_qualification": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        with material_source_guard(
            source_root=owner_source_root,
            expected_source_sha256=request.owner_source_tree_sha256,
            client=client,
        ) as source_pin:
            if pinned_job is None:
                job = prepare_material_job(
                    request.method,
                    p,
                    context,
                    location_id=request.location_id,
                    evidence_ids=request.evidence_ids,
                    client=client,
                )
            else:
                if not isinstance(pinned_job, MaterialJob):
                    raise TypeError("a native MaterialJob is required for loss-model pinning")
                job = MaterialJob.model_validate_json(
                    json.dumps(json_value(pinned_job.model_dump(mode="python")), allow_nan=False)
                )
                if (
                    job.model_id,
                    job.parameters,
                    job.asset_id,
                    job.location_id,
                    job.clock_id,
                    job.evidence_ids,
                ) != (
                    request.method,
                    p,
                    request.asset_id,
                    request.location_id,
                    request.clock_id,
                    request.evidence_ids,
                ):
                    raise ValueError("loss model job differs from its original request")
            receipt = evaluate_material_job(job, context, client=client)
            receipt.check_integrity()
            native = receipt.native.payload
            if (
                request.method == "corrosion.gumbel_area_maximum"
                and native["probability_semantics"].get("area_depth_exceedance_probability")
                != "spatial_size_exceedance_at_one_exposure"
            ):
                raise ValueError("native area exceedance probability semantics changed")
            body.update(
                status=native["status"],
                values=native["values"],
                output_units=native["units"],
                probability_semantics=native["probability_semantics"],
                issues=list(native["notes"]),
                native_receipt=receipt.model_dump(mode="json"),
            )
        body["source_pin"] = source_pin
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def verify_corrosion_wear_record(record):
    if record.get("schema") != "pipeline_integrity.corrosion_wear.v1":
        raise ValueError("unexpected loss-model source schema")
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("loss request or result changed after sealing")
    if any(
        record.get(key) is not False
        for key in (
            "spatial_exceedance_is_period_failure_probability",
            "loss_is_HRA_or_observed_event_frequency",
            "field_or_standard_qualification",
        )
    ):
        raise ValueError("loss output meaning or qualification was relabelled")
    request = CorrosionWearRequest.model_validate_json(
        json.dumps(json_value(record["request"]), allow_nan=False)
    )
    context = RunContext.model_validate_json(json.dumps(json_value(record["context"]), allow_nan=False))
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("loss record request and context asset/clock differ")
    if record.get("native_receipt") is not None:
        receipt = MaterialReceipt.model_validate_json(json.dumps(record["native_receipt"], allow_nan=False))
        receipt.check_integrity()
        if receipt.context != context or (
            receipt.job.model_id,
            receipt.job.asset_id,
            receipt.job.location_id,
            receipt.job.clock_id,
            receipt.job.parameters,
            receipt.job.evidence_ids,
        ) != (
            request.method,
            request.asset_id,
            request.location_id,
            request.clock_id,
            request.parameters,
            request.evidence_ids,
        ):
            raise ValueError("loss receipt no longer identifies its source request and context")
        if (
            receipt.native.status,
            receipt.native.payload["values"],
            receipt.native.payload["units"],
            receipt.native.payload["probability_semantics"],
        ) != (record["status"], record["values"], record["output_units"], record["probability_semantics"]):
            raise ValueError("loss output differs from the original native receipt")
    elif record.get("status") not in {"needs_data", "out_of_scope"} or any(
        record.get(key) != {} for key in ("values", "output_units", "probability_semantics")
    ):
        raise ValueError("withheld loss output requires a supported status and empty native results")
