"""Reviewed chemistry and one-series impact exchange with the existing material owner."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, MaterialReceipt, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard, verify_material_source_pin

CHEMISTRY = "materials.chemistry_reconciliation"
PITTING = "materials.pitting_composition_index"
IMPACT = "materials.impact_transition"
INPUT_UNITS = {
    CHEMISTRY: {
        "certificate.element_intervals": "mass fraction",
        "verification.element_intervals": "mass fraction",
        "requirements.element_limits": "mass fraction",
        "comparison_policy": "bool",
    },
    PITTING: {
        "chromium_mass_percent": "mass percent",
        "molybdenum_mass_percent": "mass percent",
        "nitrogen_mass_percent": "mass percent",
        "molybdenum_coefficient": "1",
        "nitrogen_coefficient": "1",
    },
    IMPACT: {
        "observation_ids": "identifier",
        "temperature_K": "K",
        "absorbed_energy_J": "J",
        "target_energy_J": "J",
        "minimum_relative_shelf_coverage": "1",
        "loss": "identifier",
        "residual_scale_J": "J",
    },
}
REVIEWS = {
    CHEMISTRY: (
        "physical_scope_identity_and_assay_intervals",
        "requirements_rights_basis_and_comparison_policy",
    ),
    PITTING: ("same_assay_element_measurements_and_mass_percent", "coefficient_support_and_index_meaning"),
    IMPACT: (
        "one_material_geometry_orientation_and_uncensored_series",
        "temperature_energy_support_and_loss_policy",
    ),
}
REQUIRED = {
    CHEMISTRY: (
        "certificate",
        "verification",
        "requirements",
        "require_matching_heat_ids",
        "require_verification_designation",
        "require_certificate_verification_interval_overlap",
    ),
    PITTING: tuple(INPUT_UNITS[PITTING]),
    IMPACT: (
        "series_id",
        "specimen_geometry_id",
        "material_condition_id",
        "orientation_id",
        "observational_unit_description",
        *INPUT_UNITS[IMPACT],
    ),
}


def _budget(parameters, *, observations, elements, nodes):
    if not isinstance(parameters, dict):
        return
    for key in ("observation_ids", "temperature_K", "absorbed_energy_J"):
        value = parameters.get(key)
        if isinstance(value, (list, tuple)) and len(value) > observations:
            raise ValueError("material quality observation budget exceeded; no truncation")
    for key, field in (
        ("certificate", "element_intervals"),
        ("verification", "element_intervals"),
        ("requirements", "element_limits"),
    ):
        record = parameters.get(key)
        if isinstance(record, dict) and isinstance(record.get(field), dict) and len(record[field]) > elements:
            raise ValueError("material quality element budget exceeded")
    stack, visited = [(parameters, 0)], 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > nodes or depth > 20:
            raise ValueError("material quality nested payload budget exceeded before serialization")
        if isinstance(value, (list, tuple, dict)):
            if len(value) > nodes - visited:
                raise ValueError("material quality nested payload budget exceeded before serialization")
            stack.extend((v, depth + 1) for v in (value.values() if isinstance(value, dict) else value))


class MaterialQualityRequest(Contract):
    method: Literal[
        "materials.chemistry_reconciliation",
        "materials.pitting_composition_index",
        "materials.impact_transition",
    ]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    population_id: Name
    physical_scope_id: Name
    series_or_assay_id: Name
    composition_basis: Literal["mass_fraction", "mass_percent", "not_applicable"]
    parameters: dict | None
    input_units: dict[str, str]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    element_assay_ids: dict[str, Name] = Field(default_factory=dict)
    impact_observations_complete: bool | None = None
    declared_temperature_support_K: tuple[Positive, Positive] | None = None
    maximum_observations: int = Field(default=2000, ge=8, le=10000)
    maximum_elements: int = Field(default=118, ge=1, le=118)
    maximum_payload_nodes: int = Field(default=100000, ge=100, le=500000)
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def contract(self):
        _budget(
            self.parameters,
            observations=self.maximum_observations,
            elements=self.maximum_elements,
            nodes=self.maximum_payload_nodes,
        )
        json_value(self.parameters)
        if self.input_units != INPUT_UNITS[self.method]:
            raise ValueError("material quality inputs require the exact declared fraction/percent/K/J basis")
        basis = {CHEMISTRY: "mass_fraction", PITTING: "mass_percent", IMPACT: "not_applicable"}[self.method]
        if self.composition_basis != basis:
            raise ValueError("composition basis differs from the selected native model")
        if set(self.applicability_reviews) - set(REVIEWS[self.method]) or set(self.review_evidence_ids) - set(
            REVIEWS[self.method]
        ):
            raise ValueError("material quality review identifiers differ from the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("material quality evidence IDs must be unique")
        support = self.declared_temperature_support_K
        if support is not None and support[1] <= support[0]:
            raise ValueError("declared temperature support must have positive width")
        return self


def _bind(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("material quality asset or clock differs from context")
    evidence = {row.id: row for row in context.evidence}
    if len(evidence) != len(context.evidence) or not set(request.evidence_ids) <= set(evidence):
        raise ValueError("material quality evidence must be unique and present in context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("material quality review evidence must belong to the selected evidence")
    return evidence


def _issues(request, context):
    evidence = _bind(request, context)
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS[request.method]
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    p = request.parameters
    if p is None or any(key not in p or p[key] is None for key in REQUIRED[request.method]):
        return issues + ["required material inputs are unresolved"], "needs_data"
    if request.method == CHEMISTRY:
        for key in (
            "require_matching_heat_ids",
            "require_verification_designation",
            "require_certificate_verification_interval_overlap",
        ):
            if type(p[key]) is not bool:
                raise ValueError("chemistry comparison policies require explicit booleans")
        for key in ("certificate", "verification", "requirements"):
            row = p[key]
            if not isinstance(row, dict):
                raise ValueError("native chemistry records must be objects")
            if key != "requirements" and row.get("physical_scope_id") != request.physical_scope_id:
                raise ValueError("chemistry record physical scope differs from its request")
            for item in row.get("evidence", []):
                parent = evidence.get(item.get("source_id")) if isinstance(item, dict) else None
                if (
                    parent is None
                    or parent.id not in request.evidence_ids
                    or (item.get("citation"), item.get("license_id"), item.get("sha256"))
                    != (parent.citation, parent.license_id, parent.sha256)
                ):
                    raise ValueError("embedded chemistry evidence differs from selected context evidence")
        # Missing, one-sided/censored intervals and designation/heat IDs are the
        # native reconciler's unknown states; they are never filled or normalized.
    elif request.method == PITTING:
        if any(isinstance(p[key], bool) or not isinstance(p[key], (int, float)) for key in REQUIRED[PITTING]):
            raise ValueError("composition and coefficients require numeric values, not booleans")
        required = {"Cr", "Mo", "N"}
        if set(request.element_assay_ids) != required or any(
            value != request.series_or_assay_id for value in request.element_assay_ids.values()
        ):
            issues.append("Cr, Mo and N require one explicit same-assay identity")
    else:
        if (p["series_id"], p["material_condition_id"]) != (
            request.series_or_assay_id,
            request.material_condition_id,
        ):
            raise ValueError("impact series or material condition differs from its request")
        if request.impact_observations_complete is not True:
            issues.append("complete uncensored absorbed-energy observations require review")
        support = request.declared_temperature_support_K
        if support is None:
            issues.append("declared observed temperature support is unresolved")
        for key in ("temperature_K", "absorbed_energy_J"):
            if not isinstance(p[key], list) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) for v in p[key]
            ):
                raise ValueError("impact temperature/energy vectors require numeric observations")
        if support is not None and any(not support[0] <= value <= support[1] for value in p["temperature_K"]):
            return issues + ["impact observations lie outside declared temperature support"], "out_of_scope"
    return issues, "needs_data"


def _job_matches(job, request):
    return (job.model_id, job.asset_id, job.location_id, job.clock_id, job.parameters, job.evidence_ids) == (
        request.method,
        request.asset_id,
        request.location_id,
        request.clock_id,
        request.parameters,
        request.evidence_ids,
    )


def evaluate_material_quality(request, context, *, client=None, pinned_job=None, owner_source_root=None):
    """Use live owner contracts; no composition index, interval comparison or fitting is copied."""
    if not isinstance(request, MaterialQualityRequest) or not isinstance(context, RunContext):
        raise TypeError("MaterialQualityRequest and RunContext are required")
    _budget(
        request.parameters,
        observations=request.maximum_observations,
        elements=request.maximum_elements,
        nodes=request.maximum_payload_nodes,
    )
    request = MaterialQualityRequest.model_validate_json(
        json.dumps(json_value(request.model_dump(mode="python")))
    )
    context = RunContext.model_validate_json(json.dumps(json_value(context.model_dump(mode="python"))))
    issues, withheld_status = _issues(request, context)
    body = dict(
        schema="pipeline_integrity.material_quality.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=withheld_status,
        values={},
        output_units={},
        probability_semantics={},
        issues=issues,
        native_receipt=None,
        source_pin=None,
        output_origin="simulation",
        composition_index_is_corrosion_rate=False,
        transition_is_fracture_toughness_or_allowable_temperature=False,
        field_or_standard_qualification=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if not issues:
        with material_source_guard(owner_source_root, request.owner_source_tree_sha256, client) as pin:
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
                    raise TypeError("a native MaterialJob is required for material quality pinning")
                job = MaterialJob.model_validate_json(pinned_job.model_dump_json())
                if not _job_matches(job, request):
                    raise ValueError("pinned material quality job differs from its source request")
            receipt = evaluate_material_job(job, context, client=client)
            receipt.check_integrity()
            payload = receipt.native.payload
            body.update(
                status=payload["status"],
                values=payload["values"],
                output_units=payload["units"],
                probability_semantics=payload["probability_semantics"],
                issues=list(payload["notes"]),
                native_receipt=receipt.model_dump(mode="json"),
            )
        body["source_pin"] = pin
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def verify_material_quality_record(record):
    if record.get("schema") != "pipeline_integrity.material_quality.v1" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("material quality request or result changed after sealing")
    for key in (
        "composition_index_is_corrosion_rate",
        "transition_is_fracture_toughness_or_allowable_temperature",
        "field_or_standard_qualification",
    ):
        if record.get(key) is not False:
            raise ValueError("material quality output meaning was relabelled")
    request = MaterialQualityRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    issues, status = _issues(request, context)
    raw_receipt = record.get("native_receipt")
    if raw_receipt is None:
        if (
            not issues
            or record["status"] != status
            or record.get("issues") != issues
            or record.get("source_pin") is not None
            or any(record.get(k) != {} for k in ("values", "output_units", "probability_semantics"))
        ):
            raise ValueError("withheld material quality record has unsupported results or status")
        return
    receipt = MaterialReceipt.model_validate_json(json.dumps(raw_receipt, allow_nan=False))
    receipt.check_integrity()
    if issues or receipt.context != context or not _job_matches(receipt.job, request):
        raise ValueError("material quality receipt differs from its original request or context")
    payload = receipt.native.payload
    if (
        receipt.native.owner != "matintegrity"
        or receipt.native.owner_schema != "matintegrity.assessment.v1"
        or payload.get("model_id") != receipt.job.model_id
        or payload.get("model_version") != receipt.job.model_version
        or payload.get("units") != receipt.job.output_units
        or payload.get("probability_semantics") != receipt.job.probability_semantics
        or payload.get("status") != receipt.native.status
        or record.get("issues") != list(payload.get("notes", []))
    ):
        raise ValueError("material quality native model, status, notes or units differ from its job")
    if (receipt.native.status, payload["values"], payload["units"], payload["probability_semantics"]) != (
        record["status"],
        record["values"],
        record["output_units"],
        record["probability_semantics"],
    ):
        raise ValueError("material quality output differs from the original native receipt")
    verify_material_source_pin(record.get("source_pin"), request.owner_source_tree_sha256)
    if request.method == IMPACT:
        values = payload["values"]
        crossing = values.get("target_energy_crossing_temperature_K")
        observed = values.get("observed_temperature_domain_K")
        if crossing is not None and (
            not isinstance(observed, list) or len(observed) != 2 or not observed[0] <= crossing <= observed[1]
        ):
            raise ValueError("native impact target crossing was extrapolated outside observations")
        if record["status"] == "computed" and (
            values.get("shelf_coverage_satisfied") is not True
            or values.get("numerical_parameter_bound_reached") is not False
        ):
            raise ValueError("computed impact result lacks native shelf and numerical support")
    json_value(record)
