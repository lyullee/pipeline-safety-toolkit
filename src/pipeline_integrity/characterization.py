"""Specimen-bound exchange of original material-test transformations and cyclic response.

Geometry, uniform deformation and population support remain explicit inputs.
The owner's equations and branch/intersection algorithms are not reproduced.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, evaluate_material_job, prepare_material_job

PARAMETER_UNITS = {
    "materials.tensile_point": {
        "force_N": "N",
        "initial_area_m2": "m2",
        "extension_m": "m",
        "gauge_length_m": "m",
        "uniform_deformation_declared": "bool",
    },
    "materials.ductility_retention": {
        "baseline_elongation_fraction": "1",
        "exposed_elongation_fraction": "1",
    },
    "materials.offset_yield": {
        "engineering_strain": "1",
        "engineering_stress_Pa": "Pa",
        "elastic_modulus_Pa": "Pa",
        "offset_strain": "1",
    },
    "materials.ramberg_osgood": {
        "stress_amplitude_Pa": "Pa",
        "elastic_modulus_Pa": "Pa",
        "cyclic_strength_coefficient_Pa": "Pa",
        "cyclic_strain_hardening_exponent": "1",
    },
}
REVIEWS = {
    "materials.tensile_point": (
        "original_area_and_gauge_extension",
        "tension_sign_and_deformation_interpretation",
    ),
    "materials.ductility_retention": ("matched_specimen_conditions_and_elongation_definition",),
    "materials.offset_yield": ("explicit_monotonic_loading_branch", "elastic_modulus_and_offset_basis"),
    "materials.ramberg_osgood": (
        "cyclic_amplitude_and_calibrated_material_condition",
        "parameter_and_stress_support",
    ),
}


class CharacterizationRequest(Contract):
    method: Literal[
        "materials.tensile_point",
        "materials.ductility_retention",
        "materials.offset_yield",
        "materials.ramberg_osgood",
    ]
    asset_id: Name
    specimen_id: Name
    material_condition_id: Name
    location_id: Name
    clock_id: Name
    parameters: dict | None
    input_units: dict[str, str]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_branch_samples: int = Field(default=100000, ge=3, le=1000000)

    @model_validator(mode="after")
    def declared_contract(self):
        json_value(self.parameters)
        if set(self.applicability_reviews) - set(REVIEWS[self.method]) or set(self.review_evidence_ids) - set(
            REVIEWS[self.method]
        ):
            raise ValueError("characterization reviews must match the selected method")
        if self.input_units != PARAMETER_UNITS[self.method]:
            raise ValueError("characterization inputs require the exact declared SI/fraction/boolean basis")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("characterization evidence IDs must be distinct")
        return self


def _unknown(value):
    return (
        value is None
        or isinstance(value, (list, tuple))
        and any(_unknown(v) for v in value)
        or isinstance(value, dict)
        and any(_unknown(v) for v in value.values())
    )


def evaluate_characterization(
    request: CharacterizationRequest,
    context: RunContext,
    *,
    client=None,
    pinned_job: MaterialJob | None = None,
):
    """Calculate using the live native schema; missing rows/geometry are never filled."""
    if not isinstance(request, CharacterizationRequest) or not isinstance(context, RunContext):
        raise TypeError("CharacterizationRequest and RunContext are required")
    json_value(request.model_dump(mode="python"))
    json_value(context.model_dump(mode="python"))
    if request.parameters is not None and request.method == "materials.offset_yield":
        for name in ("engineering_strain", "engineering_stress_Pa"):
            if (
                isinstance(request.parameters.get(name), (list, tuple))
                and len(request.parameters[name]) > request.maximum_branch_samples
            ):
                raise ValueError("characterization branch sample budget exceeded; no truncation")
    request = CharacterizationRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("characterization asset or source clock differs from its context")
    ids = [e.id for e in context.evidence]
    if len(set(ids)) != len(ids) or not set(request.evidence_ids) <= set(ids):
        raise ValueError("characterization evidence must be unique and present in context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("review evidence must occur in the selected context evidence")
    issues = []
    for review in REVIEWS[request.method]:
        if not request.applicability_reviews.get(review, False) or review not in request.review_evidence_ids:
            issues.append(f"review and evidence required: {review}")
    if (
        request.parameters is None
        or set(PARAMETER_UNITS[request.method]) - set(request.parameters)
        or _unknown(request.parameters)
    ):
        issues.append("specimen inputs contain unresolved geometry, values or declarations")
    body = {
        "schema": "pipeline_integrity.characterization.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "values": {},
        "output_units": {},
        "issues": issues,
        "native_receipt": None,
        "output_origin": "simulation",
        "material_or_test_acceptance": False,
        "field_or_population_validation": False,
        "cyclic_parameters_fitted_from_monotonic_data": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        if pinned_job is None:
            job = prepare_material_job(
                request.method,
                request.parameters,
                context,
                location_id=request.location_id,
                evidence_ids=request.evidence_ids,
                client=client,
            )
        else:
            if not isinstance(pinned_job, MaterialJob):
                raise TypeError("a pinned characterization job must be MaterialJob")
            job = MaterialJob.model_validate_json(pinned_job.model_dump_json())
            if (
                job.model_id,
                job.asset_id,
                job.location_id,
                job.clock_id,
                job.parameters,
                job.evidence_ids,
            ) != (
                request.method,
                request.asset_id,
                request.location_id,
                request.clock_id,
                request.parameters,
                request.evidence_ids,
            ):
                raise ValueError("the pinned characterization job differs from its specimen request")
        receipt = evaluate_material_job(job, context, client=client)
        receipt.check_integrity()
        payload = receipt.native.payload
        body.update(
            status=payload["status"],
            values=payload["values"],
            output_units=payload["units"],
            issues=list(payload["notes"]),
            native_receipt=receipt.model_dump(mode="json"),
        )
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))
