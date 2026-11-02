"""Reviewed one-dimensional loss profiles around the existing owner area-scan model."""

import json
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Pressure, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, MaterialReceipt, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard, verify_material_source_pin

MODEL_ID = "structural.profile_capacity"
REVIEWS = (
    "profile_coordinates_reference_wall_and_sizing_basis",
    "complete_axial_extent_blunt_loss_and_internal_pressure_scope",
    "licensed_reference_capacity_and_bulging_provider_support",
)
INPUT_UNITS = {
    "reference_wall_thickness_m": "m",
    "axial_position_m": "m",
    "measured_remaining_thickness_m": "m",
    "reference_capacity.reference_pressure_difference_Pa": "Pa",
    "bulging_table.window_length_m": "m",
    "bulging_table.factor": "1",
    "maximum_supported_depth_fraction": "1",
}
FIELDS = {
    "geometry_id",
    "material_condition_id",
    "reference_wall_thickness_m",
    "axial_position_m",
    "measured_remaining_thickness_m",
    "reference_capacity",
    "bulging_table",
    "maximum_supported_depth_fraction",
    "full_axial_extent_confirmed",
    "blunt_metal_loss_confirmed",
    "internal_pressure_dominant_confirmed",
    "provider_applicability_reviewed",
}


class ProfileCapacityRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    geometry_id: Name
    material_condition_id: Name
    profile_id: Name
    coordinate_basis_id: Name
    source_observation_id: Name
    source_point_ids: tuple[Name, ...] = Field(default=(), max_length=500)
    parameters: dict | None
    input_units: dict[str, str]
    demand: Pressure | None = None
    demand_evidence_id: Name | None = None
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_points: int = Field(default=500, ge=2, le=500)
    maximum_provider_knots: int = Field(default=5000, ge=2, le=5000)
    maximum_nodal_windows: int = Field(default=124750, ge=1, le=124750)
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def fields(self):
        _budgets(self)
        json_value(self.parameters)
        if self.input_units != INPUT_UNITS:
            raise ValueError("profile capacity requires explicitly declared SI/dimensionless inputs")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("profile reviews must use the declared review keys")
        if self.parameters is not None and set(self.parameters) - FIELDS:
            raise ValueError("unknown native profile-capacity parameter")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("profile evidence identifiers must be distinct")
        if len(set(self.source_point_ids)) != len(self.source_point_ids):
            raise ValueError("profile source point identifiers must be distinct")
        if self.demand is not None and (self.demand.reference != "differential" or self.demand.value < 0):
            raise ValueError("demand must be a nonnegative internal-minus-external pressure difference")
        return self


def _unknown(value):
    if value is None:
        return True
    if isinstance(value, dict):
        # A source checksum is optional provenance, not an imputed physical parameter.
        return any(_unknown(v) for k, v in value.items() if k != "sha256")
    if isinstance(value, (list, tuple)):
        return any(_unknown(v) for v in value)
    return False


def _budgets(request):
    p = request.parameters
    if not isinstance(p, dict):
        return
    for key in ("axial_position_m", "measured_remaining_thickness_m"):
        values = p.get(key)
        if isinstance(values, (list, tuple)) and (
            len(values) > request.maximum_points
            or len(values) * (len(values) - 1) // 2 > request.maximum_nodal_windows
        ):
            raise ValueError("profile point/window budget exceeded before serialization; no points trimmed")
    provider = p.get("bulging_table")
    if isinstance(provider, dict) and any(
        isinstance(provider.get(key), (list, tuple)) and len(provider[key]) > request.maximum_provider_knots
        for key in ("window_length_m", "factor")
    ):
        raise ValueError("bulging-provider knot budget exceeded without truncation")


def _comparison(request, status, values):
    if status == "computed":
        if values.get("pressure_basis") != "internal_minus_external":
            raise ValueError("native profile capacity changed its pressure basis")
        if values["declared_model_pressure_difference_capacity_Pa"] <= 0:
            raise ValueError("computed profile capacity must be positive")
    if request.demand is None:
        return {"status": "not_requested"}
    if status != "computed":
        return {"status": "withheld", "reason": "a computed native capacity is required"}
    capacity = values["declared_model_pressure_difference_capacity_Pa"]
    demand = request.demand.pascals()
    return {
        "status": "computed",
        "demand_pressure_difference_Pa": demand,
        "declared_model_pressure_difference_capacity_Pa": capacity,
        "demand_to_declared_capacity_ratio": demand / capacity,
        "demand_not_above_declared_model_capacity": demand <= capacity,
        "standard_or_design_acceptance": False,
    }


def evaluate_profile_capacity(request, context, *, client=None, pinned_job=None, owner_source_root=None):
    """Keep provider and profile identities; the owner alone performs the nodal area scan."""
    if not isinstance(request, ProfileCapacityRequest) or not isinstance(context, RunContext):
        raise TypeError("ProfileCapacityRequest and RunContext are required")
    _budgets(request)
    request = ProfileCapacityRequest.model_validate_json(
        json.dumps(json_value(request.model_dump(mode="python")), allow_nan=False)
    )
    context = RunContext.model_validate_json(
        json.dumps(json_value(context.model_dump(mode="python")), allow_nan=False)
    )
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("profile request and context asset/clock differ")
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence) or set(request.evidence_ids) - evidence.keys():
        raise ValueError("profile evidence must be unique and present in context")
    if set(request.review_evidence_ids.values()) - set(request.evidence_ids):
        raise ValueError("profile reviews require selected source evidence")
    p = request.parameters
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    if p is None or FIELDS - set(p) or _unknown(p):
        issues.append("native profile or provider inputs are unresolved; no defaults or clipping")
    if not request.source_point_ids:
        issues.append("original profile point identities are required")
    if p is not None:
        if any(
            p.get(k) is not None and p[k] != getattr(request, k)
            for k in ("geometry_id", "material_condition_id")
        ):
            raise ValueError("native profile geometry or material differs from its source request")
        if (
            isinstance(p.get("axial_position_m"), (list, tuple))
            and request.source_point_ids
            and len(request.source_point_ids) != len(p["axial_position_m"])
        ):
            raise ValueError("source point identities must match every original profile position")
        for key in ("reference_capacity", "bulging_table"):
            provider = p.get(key)
            if not isinstance(provider, dict):
                continue
            for item in provider.get("evidence") or []:
                if not isinstance(item, dict):
                    raise ValueError("native provider evidence must be a declared evidence object")
                source_id = item.get("source_id")
                if source_id not in request.evidence_ids:
                    raise ValueError("provider evidence is absent from selected source evidence")
                original = evidence[source_id]
                if (item.get("citation"), item.get("license_id"), item.get("sha256")) != (
                    original.citation,
                    original.license_id,
                    original.sha256,
                ):
                    raise ValueError("provider citation, license or checksum differs from source evidence")
    if request.demand is not None and request.demand_evidence_id not in request.evidence_ids:
        raise ValueError("pressure-difference demand requires selected evidence")
    body = {
        "schema": "pipeline_integrity.profile_capacity.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "values": {},
        "output_units": {},
        "probability_semantics": {},
        "issues": issues,
        "native_receipt": None,
        "source_pin": None,
        "nodal_scan_is_continuous_endpoint_optimization": False,
        "capacity_is_remaining_life_or_failure_probability": False,
        "field_or_standard_qualification": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        with material_source_guard(owner_source_root, request.owner_source_tree_sha256, client=client) as pin:
            if pinned_job is None:
                job = prepare_material_job(
                    MODEL_ID,
                    p,
                    context,
                    location_id=request.location_id,
                    evidence_ids=request.evidence_ids,
                    client=client,
                )
            else:
                if not isinstance(pinned_job, MaterialJob):
                    raise TypeError("a native MaterialJob is required for profile pinning")
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
                    MODEL_ID,
                    p,
                    request.asset_id,
                    request.location_id,
                    request.clock_id,
                    request.evidence_ids,
                ):
                    raise ValueError("profile job differs from its reviewed source request")
            receipt = evaluate_material_job(job, context, client=client)
            receipt.check_integrity()
            native = receipt.native.payload
            body.update(
                status=native["status"],
                values=native["values"],
                output_units=native["units"],
                probability_semantics=native["probability_semantics"],
                issues=list(native["notes"]),
                native_receipt=receipt.model_dump(mode="json"),
            )
        body["source_pin"] = pin
    body["demand_comparison"] = _comparison(request, body["status"], body["values"])
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def verify_profile_capacity_record(record):
    if record.get("schema") != "pipeline_integrity.profile_capacity.v1" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("profile capacity source or record changed")
    if any(
        record.get(k) is not False
        for k in (
            "nodal_scan_is_continuous_endpoint_optimization",
            "capacity_is_remaining_life_or_failure_probability",
            "field_or_standard_qualification",
        )
    ):
        raise ValueError("profile capacity meaning or qualification changed")
    request = ProfileCapacityRequest.model_validate_json(
        json.dumps(json_value(record["request"]), allow_nan=False)
    )
    context = RunContext.model_validate_json(json.dumps(json_value(record["context"]), allow_nan=False))
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("profile source asset or clock changed")
    verify_material_source_pin(
        record.get("source_pin"),
        request.owner_source_tree_sha256,
        executed=record.get("native_receipt") is not None,
    )
    if record.get("native_receipt") is None:
        if record.get("status") != "needs_data" or any(
            record.get(k) != {} for k in ("values", "output_units", "probability_semantics")
        ):
            raise ValueError("withheld profile output must retain empty native values")
    else:
        receipt = MaterialReceipt.model_validate_json(
            json.dumps(json_value(record["native_receipt"]), allow_nan=False)
        )
        receipt.check_integrity()
        if receipt.context != context or (
            receipt.job.model_id,
            receipt.job.asset_id,
            receipt.job.location_id,
            receipt.job.clock_id,
            receipt.job.parameters,
            receipt.job.evidence_ids,
        ) != (
            MODEL_ID,
            request.asset_id,
            request.location_id,
            request.clock_id,
            request.parameters,
            request.evidence_ids,
        ):
            raise ValueError("profile receipt differs from its original source request")
        if (
            receipt.native.status,
            receipt.native.payload["values"],
            receipt.native.payload["units"],
            receipt.native.payload["probability_semantics"],
        ) != (record["status"], record["values"], record["output_units"], record["probability_semantics"]):
            raise ValueError("profile result differs from its original native receipt")
    if record.get("demand_comparison") != _comparison(request, record["status"], record["values"]):
        raise ValueError("pressure comparison differs from its request and native capacity")
