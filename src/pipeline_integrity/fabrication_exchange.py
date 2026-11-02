"""Reviewed fabrication inputs around three material-owned calculation models.

No thread table, torque factor, thinning rule or structural wall requirement is
supplied here.  Calculation status and caller requirement outcomes stay separate.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, MaterialReceipt, evaluate_material_job, prepare_material_job

REVIEWS = {
    "fabrication.bend_strain": (
        "outside_diameter_and_centerline_radius",
        "kinematic_strain_scope_excludes_thinning_ovalization",
    ),
    "fabrication.bolt_preload": (
        "torque_is_applied_tightening_torque",
        "nut_factor_matches_thread_bearing_lubrication",
        "effective_tensile_area_basis",
    ),
    "fabrication.thickness_budget": (
        "manufacturing_then_forming_loss_basis",
        "future_corrosion_and_required_wall_basis",
        "measured_wall_excludes_repeat_manufacturing_and_forming_loss",
    ),
}
SI_INPUT_UNITS = {
    "fabrication.bend_strain": {"outside_diameter_m": "m", "centerline_bend_radius_m": "m"},
    "fabrication.bolt_preload": {
        "torque_N_m": "N*m",
        "nominal_diameter_m": "m",
        "nut_factor": "1",
        "tensile_area_m2": "m2",
    },
    "fabrication.thickness_budget": {
        "nominal_thickness_m": "m",
        "manufacturing_minus_tolerance_m": "m",
        "forming_loss_fraction": "1",
        "corrosion_allowance_m": "m",
        "required_structural_thickness_m": "m",
        "measured_postfabrication_thickness_m": "m",
    },
}
_OPTIONAL = {"fabrication.thickness_budget": {"measured_postfabrication_thickness_m"}}
_OUTPUT_UNITS = {
    "fabrication.bend_strain": {"outer_fiber_strain": "1"},
    "fabrication.bolt_preload": {"preload_N": "N", "nominal_tensile_stress_Pa": "Pa"},
    "fabrication.thickness_budget": {
        "minimum_supplied_thickness_m": "m",
        "minimum_formed_thickness_m": "m",
        "nominal_budget_structural_thickness_m": "m",
        "nominal_budget_margin_m": "m",
        "measured_structural_thickness_m": "m",
        "measured_margin_m": "m",
    },
}


class FabricationRequirement(Contract):
    id: Name
    output_key: Name
    unit: Name
    relation: Literal["le", "ge"]
    threshold: float
    evidence_id: Name
    interpretation_reviewed: bool = False


class FabricationRequest(Contract):
    method: Literal["fabrication.bend_strain", "fabrication.bolt_preload", "fabrication.thickness_budget"]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    parameters: dict | None
    input_units: dict[str, str]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    parameter_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    requirements: tuple[FabricationRequirement, ...] = ()

    @model_validator(mode="after")
    def meanings(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("fabrication evidence IDs must be unique")
        if len({rule.id for rule in self.requirements}) != len(self.requirements):
            raise ValueError("fabrication requirements need unique IDs")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS[self.method]):
            raise ValueError("review keys must belong to the chosen fabrication method")
        expected = SI_INPUT_UNITS[self.method]
        if (set(self.input_units) | set(self.parameter_evidence_ids)) - set(expected):
            raise ValueError("input unit or parameter evidence keys do not belong to the native method")
        if any(unit != expected[key] for key, unit in self.input_units.items()):
            raise ValueError("fabrication inputs require explicit canonical SI units without conversion")
        if self.parameters is not None:
            json_value(self.parameters)
            if set(self.parameters) - set(expected):
                raise ValueError("fabrication parameters contain unknown native schema keys")
            if any(
                value is not None and (type(value) not in (int, float) or not math.isfinite(value))
                for value in self.parameters.values()
            ):
                raise ValueError("fabrication parameters must be finite JSON numbers or explicit unknowns")
        for rule in self.requirements:
            if (
                rule.output_key not in _OUTPUT_UNITS[self.method]
                or rule.unit != _OUTPUT_UNITS[self.method][rule.output_key]
            ):
                raise ValueError("requirement output and unit must match the chosen native quantity")
        return self


class FabricationResult(Contract):
    schema_version: Literal["pipeline_integrity.fabrication_exchange.v1"] = (
        "pipeline_integrity.fabrication_exchange.v1"
    )
    request: FabricationRequest
    context: RunContext
    status: Literal["computed", "needs_data", "out_of_scope"]
    values: dict
    output_units: dict[str, str]
    native_receipt: MaterialReceipt | None
    requirement_outcome: Literal["not_requested", "satisfied", "not_satisfied", "unknown"]
    requirement_records: tuple[dict, ...]
    issues: tuple[str, ...]
    output_origin: Literal["calculated_indicator"] = "calculated_indicator"
    field_or_code_acceptance: Literal[False] = False
    pressure_capacity_calculated: Literal[False] = False
    leak_tightness_demonstrated: Literal[False] = False
    human_error_probability_calculated: Literal[False] = False
    friction_coefficient_or_material_strength_inferred: Literal[False] = False
    input_units_converted: Literal[False] = False
    implementation_sha256: str
    result_digest: str

    def check_integrity(self):
        checked = FabricationResult.model_validate_json(self.model_dump_json())
        if digest(checked.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("fabrication request, context, requirements or result changed")
        if self.native_receipt is not None:
            self.native_receipt.check_integrity()
            native = self.native_receipt.native.payload
            if (
                self.native_receipt.context != self.context
                or self.native_receipt.job.model_id != self.request.method
                or self.native_receipt.job.asset_id != self.request.asset_id
                or self.native_receipt.job.location_id != self.request.location_id
                or self.native_receipt.job.clock_id != self.request.clock_id
                or self.native_receipt.job.parameters != self.request.parameters
                or self.values != native["values"]
                or self.output_units != native["units"]
                or self.status != native["status"]
            ):
                raise ValueError("fabrication result differs from its pinned material receipt")


def _requirements(request, status, values):
    records = []
    for rule in request.requirements:
        actual = values.get(rule.output_key)
        outcome = "unknown"
        if status == "computed" and rule.interpretation_reviewed and actual is not None:
            if type(actual) not in (int, float) or not math.isfinite(actual):
                raise ValueError("native fabrication requirement quantity is not a finite scalar")
            satisfies = actual <= rule.threshold if rule.relation == "le" else actual >= rule.threshold
            outcome = "satisfied" if satisfies else "not_satisfied"
        records.append(dict(requirement=rule.model_dump(mode="json"), actual=actual, outcome=outcome))
    outcomes = {row["outcome"] for row in records}
    overall = (
        "not_requested"
        if not records
        else "not_satisfied"
        if "not_satisfied" in outcomes
        else "unknown"
        if "unknown" in outcomes
        else "satisfied"
    )
    return tuple(records), overall


def evaluate_fabrication(
    request: FabricationRequest, context: RunContext, *, client=None, pinned_job: MaterialJob | None = None
) -> FabricationResult:
    """Delegate reviewed SI inputs; optionally require a previously reviewed job pin.

    An absent postfabrication measurement is optional.  Other missing inputs,
    units, method reviews or parameter bases withhold the owner calculation.
    """
    if not isinstance(request, FabricationRequest) or not isinstance(context, RunContext):
        raise TypeError("fabrication evaluation requires FabricationRequest and RunContext")
    # A cloned dict may bypass validation; do not let the JSON encoder turn a
    # cloned NaN into null before the finite-input contract has checked it.
    json_value(request.model_dump(mode="python"))
    json_value(context.model_dump(mode="python"))
    request = FabricationRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("fabrication asset/clock and context differ")
    evidence = (
        set(request.evidence_ids)
        | set(request.review_evidence_ids.values())
        | set(request.parameter_evidence_ids.values())
        | {rule.evidence_id for rule in request.requirements}
    )
    actual = [row.id for row in context.evidence]
    if len(actual) != len(set(actual)) or evidence - set(actual):
        raise ValueError("fabrication evidence must be unique and actually present in context")
    issues = []
    for review in REVIEWS[request.method]:
        if not request.applicability_reviews.get(review, False) or review not in request.review_evidence_ids:
            issues.append(f"method review and evidence unresolved: {review}")
    parameters = request.parameters
    required = set(SI_INPUT_UNITS[request.method]) - _OPTIONAL.get(request.method, set())
    if parameters is None:
        issues.append("fabrication parameters are unknown; no torque, geometry or loss defaults supplied")
    else:
        for key in sorted(required):
            if parameters.get(key) is None:
                issues.append(f"required parameter is unknown: {key}")
        for key, value in parameters.items():
            if value is not None and (
                key not in request.input_units or key not in request.parameter_evidence_ids
            ):
                issues.append(f"parameter SI unit or evidence unresolved: {key}")
    receipt, values, units, status = None, {}, {}, "needs_data"
    if not issues:
        if pinned_job is None:
            job = prepare_material_job(
                request.method,
                parameters,
                context,
                location_id=request.location_id,
                evidence_ids=tuple(sorted(evidence)),
                client=client,
            )
        else:
            if not isinstance(pinned_job, MaterialJob):
                raise TypeError("a pinned fabrication job must be MaterialJob")
            json_value(pinned_job.model_dump(mode="python"))
            job = MaterialJob.model_validate_json(pinned_job.model_dump_json())
            if (
                job.model_id != request.method
                or job.asset_id != request.asset_id
                or job.location_id != request.location_id
                or job.clock_id != request.clock_id
                or job.parameters != parameters
                or job.evidence_ids != tuple(sorted(evidence))
            ):
                raise ValueError("pinned fabrication job differs from its reviewed request or evidence")
        if job.output_units != _OUTPUT_UNITS[request.method] or job.probability_semantics:
            raise ValueError("native fabrication units or non-probability meaning changed")
        receipt = evaluate_material_job(job, context, client=client)
        receipt.check_integrity()
        native = receipt.native.payload
        status, values, units = native["status"], native["values"], native["units"]
        issues.extend(native["notes"])
    requirement_records, requirement_outcome = _requirements(request, status, values)
    provisional = FabricationResult(
        request=request,
        context=context,
        status=status,
        values=values,
        output_units=units,
        native_receipt=receipt,
        requirement_outcome=requirement_outcome,
        requirement_records=requirement_records,
        issues=tuple(issues),
        implementation_sha256=file_digest(Path(__file__)),
        result_digest="pending",
    )
    result = provisional.model_copy(
        update={"result_digest": digest(provisional.model_dump(mode="json", exclude={"result_digest"}))},
        deep=True,
    )
    result.check_integrity()
    return result
