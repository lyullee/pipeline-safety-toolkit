"""Source-preserving indentation geometry around the material owner's hardness models."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialReceipt, evaluate_material_job, prepare_material_job

REVIEWS = {
    "testing.brinell": (
        "spherical_ball_and_mean_indentation_geometry",
        "force_dwell_surface_and_length_metrology",
        "material_condition_location_and_method",
    ),
    "testing.vickers": (
        "vickers_geometry_and_two_measured_diagonals",
        "force_dwell_surface_and_length_metrology",
        "material_condition_location_and_method",
    ),
}
REQUIRED = {
    "testing.brinell": ("test_force_N", "ball_diameter_m", "mean_indentation_diameter_m"),
    "testing.vickers": ("test_force_N", "first_diagonal_m", "second_diagonal_m"),
}


class HardnessConditions(Contract):
    test_id: Name
    specimen_id: Name
    dwell_s: Positive | None
    surface_condition_id: Name | None
    procedure_id: Name | None
    force_evidence_id: Name | None
    length_evidence_id: Name | None
    conditions_evidence_id: Name | None
    geometry_applicable: bool | None


class HardnessRequest(Contract):
    method: Literal["testing.brinell", "testing.vickers"]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name | None
    parameters: dict
    conditions: HardnessConditions
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def review_scope(self):
        json_value(self.parameters)
        if set(self.applicability_reviews) - set(REVIEWS[self.method]):
            raise ValueError("hardness review keys must belong to the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("hardness evidence IDs must be distinct")
        return self


def _evidence_ids(request):
    conditions = request.conditions
    return tuple(
        dict.fromkeys(
            (
                *request.evidence_ids,
                *(
                    value
                    for value in (
                        conditions.force_evidence_id,
                        conditions.length_evidence_id,
                        conditions.conditions_evidence_id,
                    )
                    if value is not None
                ),
            )
        )
    )


def _scope(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("hardness asset/clock and context differ")
    ids = [item.id for item in context.evidence]
    if len(ids) != len(set(ids)) or set(_evidence_ids(request)) - set(ids):
        raise ValueError(
            "hardness force, geometry and condition evidence must be distinct and present in context"
        )


def _unknown_paths(value, prefix):
    if value is None:
        return [prefix]
    if isinstance(value, dict):
        return [path for key, item in value.items() for path in _unknown_paths(item, f"{prefix}.{key}")]
    if isinstance(value, list):
        return [
            path for index, item in enumerate(value) for path in _unknown_paths(item, f"{prefix}[{index}]")
        ]
    return []


def _unknown(request):
    values = request.model_dump(mode="json")
    return (
        _unknown_paths(values["parameters"], "parameters")
        + _unknown_paths(values["conditions"], "conditions")
        + _unknown_paths(request.material_condition_id, "material_condition_id")
        + [f"parameters.{key}" for key in REQUIRED[request.method] if key not in request.parameters]
    )


def _seal(body):
    return {**body, "record_digest": digest(body)}


def evaluate_hardness(request: HardnessRequest, context: RunContext, *, client=None):
    """Check declared test context, then delegate the SI indentation geometry without conversions to strength."""
    request_payload = json_value(request.model_dump(mode="python"))
    context_payload = json_value(context.model_dump(mode="python"))
    request = HardnessRequest.model_validate_json(json.dumps(request_payload, allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(context_payload, allow_nan=False))
    _scope(request, context)
    body = dict(
        schema="pipeline_integrity.hardness_exchange.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="needs_data",
        issues=[],
        unknown_input_paths=_unknown(request),
        values={},
        output_units={},
        probability_semantics={},
        native_receipt=None,
        output_origin="simulation",
        field_or_standard_qualification=False,
        acceptance_criteria_applied=False,
        strength_brittleness_or_failure_probability_inferred=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if any(not request.applicability_reviews.get(key, False) for key in REVIEWS[request.method]):
        body["issues"] = [
            "all method-specific hardness geometry, metrology and applicability reviews are required"
        ]
        return _seal(body)
    if body["unknown_input_paths"]:
        body["issues"] = [
            "unknown indentation or test conditions cannot be filled from nominal material or procedure defaults"
        ]
        return _seal(body)
    if not request.conditions.geometry_applicable:
        body.update(
            status="out_of_scope",
            issues=["the reviewed indentation geometry is outside the selected native method"],
        )
        return _seal(body)
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=_evidence_ids(request),
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


def verify_hardness_record(record):
    """Check a retained result against its complete source request and original material receipt."""
    if record.get("schema") != "pipeline_integrity.hardness_exchange.v1" or record.get(
        "record_digest"
    ) != digest({key: value for key, value in record.items() if key != "record_digest"}):
        raise ValueError("hardness source or result changed after sealing")
    request = HardnessRequest.model_validate_json(json.dumps(record["request"]))
    context = RunContext.model_validate_json(json.dumps(record["context"]))
    _scope(request, context)
    if (
        record["status"] not in {"computed", "needs_data", "out_of_scope"}
        or record["unknown_input_paths"] != _unknown(request)
        or any(
            record[key] is not False
            for key in (
                "field_or_standard_qualification",
                "acceptance_criteria_applied",
                "strength_brittleness_or_failure_probability_inferred",
            )
        )
    ):
        raise ValueError("hardness status, unknown inputs or qualification meaning changed")
    if record["native_receipt"] is None:
        if record["status"] == "computed" or record["values"] or record["output_units"]:
            raise ValueError("computed hardness requires its original material receipt")
        return
    receipt = MaterialReceipt.model_validate_json(json.dumps(record["native_receipt"]))
    receipt.check_integrity()
    payload = receipt.native.payload
    if (
        _unknown(request)
        or not request.conditions.geometry_applicable
        or any(not request.applicability_reviews.get(key, False) for key in REVIEWS[request.method])
        or receipt.context != context
        or receipt.job.model_id != request.method
        or receipt.job.asset_id != request.asset_id
        or receipt.job.location_id != request.location_id
        or receipt.job.clock_id != request.clock_id
        or receipt.job.parameters != request.parameters
        or receipt.job.evidence_ids != _evidence_ids(request)
        or receipt.native.status != record["status"]
        or payload["values"] != record["values"]
        or payload["units"] != record["output_units"]
        or payload["probability_semantics"] != record["probability_semantics"]
        or list(payload["notes"]) != record["issues"]
    ):
        raise ValueError("hardness source differs from its original material receipt")
