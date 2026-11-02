"""Reviewed cyclic quantities delegated to their material-model owner.

No cycle counter, material relation, numerical root finder or work integrator lives here.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import MaterialClient
from .contracts import Contract, Name, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard

PARAMETER_UNITS = {
    "fatigue.goodman": {
        "alternating_stress_Pa": "Pa",
        "mean_stress_Pa": "Pa",
        "ultimate_tensile_strength_Pa": "Pa",
    },
    "fatigue.hysteresis_path": {
        "stress_Pa": "Pa",
        "strain_fraction": "1",
        "cycle_boundary_declared": "bool",
        "linear_closure_declared": "bool",
        "maximum_endpoint_stress_difference_Pa": "Pa",
        "maximum_endpoint_strain_difference": "1",
    },
    "fatigue.strain_life": {
        "strain_amplitude": "1",
        "elastic_modulus_Pa": "Pa",
        "fatigue_strength_coefficient_Pa": "Pa",
        "fatigue_strength_exponent": "1",
        "fatigue_ductility_coefficient": "1",
        "fatigue_ductility_exponent": "1",
        "minimum_valid_cycles": "cycle",
        "maximum_valid_cycles": "cycle",
    },
}
REVIEWS = {
    "fatigue.goodman": (
        "tensile_mean_and_half_range_amplitude",
        "strength_measure_material_condition_and_support",
    ),
    "fatigue.hysteresis_path": (
        "ordered_aligned_uniaxial_path_and_work_conjugacy",
        "cycle_boundary_closure_and_endpoint_tolerances",
    ),
    "fatigue.strain_life": (
        "fully_reversed_constant_amplitude_and_two_reversals",
        "cyclic_coefficients_population_and_calibrated_cycle_support",
    ),
}


class CyclicFatigueRequest(Contract):
    method: Literal["fatigue.goodman", "fatigue.hysteresis_path", "fatigue.strain_life"]
    asset_id: Name
    specimen_id: Name
    material_condition_id: Name
    population_id: Name | None
    parameter_support_id: Name | None
    load_history_id: Name
    location_id: Name
    clock_id: Name
    stress_measure: Literal["engineering", "true"] | None
    strain_measure: Literal["engineering", "logarithmic", "not_used"] | None
    response_regime: Literal["cyclic", "monotonic"] | None
    stress_sign_convention: Literal["tension_positive"] | None
    amplitude_definition: Literal["half_range"] | None
    cycle_definition: Literal["two_reversals"] | None
    parameters: dict | None
    input_units: dict[str, str]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    path_sample_ids: tuple[Name, ...] = ()
    sample_time_s: tuple[Nonnegative | None, ...] | None = None
    observation_period_s: tuple[Nonnegative, Nonnegative] | None = None
    maximum_path_samples: int = Field(default=100000, ge=3, le=1000000)
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def declared_contract(self):
        json_value(self.parameters)
        if self.input_units != PARAMETER_UNITS[self.method]:
            raise ValueError("cyclic inputs require the exact SI, fraction, cycle and boolean units")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS[self.method]):
            raise ValueError("cyclic reviews must match the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("cyclic evidence identifiers must be unique")
        if len(set(self.path_sample_ids)) != len(self.path_sample_ids):
            raise ValueError("path sample identifiers must be unique without reordering")
        if self.method != "fatigue.hysteresis_path" and (
            self.path_sample_ids or self.sample_time_s is not None
        ):
            raise ValueError("path samples belong only to the hysteresis method")
        if (
            self.observation_period_s is not None
            and self.observation_period_s[1] < self.observation_period_s[0]
        ):
            raise ValueError("observation period end must not precede its start")
        return self


def _unknown(value):
    if isinstance(value, dict):
        return any(_unknown(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return any(_unknown(v) for v in value)
    return value is None


def evaluate_cyclic_fatigue(
    request: CyclicFatigueRequest,
    context: RunContext,
    *,
    client=None,
    pinned_job: MaterialJob | None = None,
    owner_source_root: str | Path | None = None,
):
    """Keep unresolved declarations local; execute complete inputs through the live owner schema."""
    if not isinstance(request, CyclicFatigueRequest) or not isinstance(context, RunContext):
        raise TypeError("CyclicFatigueRequest and RunContext are required")
    # Reject nonfinite Python values before a JSON serializer can replace them with null.
    json_value(request.model_dump(mode="python"))
    json_value(context.model_dump(mode="python"))
    if pinned_job is not None:
        if not isinstance(pinned_job, MaterialJob):
            raise TypeError("pinned_job must be MaterialJob")
        json_value(pinned_job.model_dump(mode="python"))
    request = CyclicFatigueRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("cyclic asset or clock differs from the supplied context")
    ids = [e.id for e in context.evidence]
    if len(set(ids)) != len(ids) or not set(request.evidence_ids) <= set(ids):
        raise ValueError("cyclic evidence must be unique and present in context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("review evidence must occur in the selected evidence")
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS[request.method]
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    if (
        request.parameters is None
        or set(PARAMETER_UNITS[request.method]) - set(request.parameters)
        or _unknown(request.parameters)
    ):
        issues.append("unresolved native parameters; no filling, filtering or extrapolation")
    if any(
        getattr(request, key) is None
        for key in (
            "population_id",
            "parameter_support_id",
            "stress_measure",
            "strain_measure",
            "response_regime",
            "stress_sign_convention",
            "amplitude_definition",
            "cycle_definition",
        )
    ):
        issues.append("unresolved quantity, population or calibrated-support meaning")
    if request.response_regime == "monotonic":
        issues.append("monotonic coefficients or paths are not qualified cyclic inputs")
    if request.method == "fatigue.hysteresis_path" and request.parameters is not None:
        p = request.parameters
        for name in ("stress_Pa", "strain_fraction"):
            if isinstance(p.get(name), (tuple, list)) and len(p[name]) > request.maximum_path_samples:
                raise ValueError("cyclic path sample budget exceeded; no truncation")
        count = len(p["stress_Pa"]) if isinstance(p.get("stress_Pa"), (tuple, list)) else None
        if count is not None:
            if not request.path_sample_ids:
                issues.append("unresolved path sample identities; source rows retained")
            elif len(request.path_sample_ids) != count:
                raise ValueError("path sample IDs must align with every supplied stress-strain row")
            if request.sample_time_s is not None:
                if len(request.sample_time_s) != count:
                    raise ValueError("path sample clocks must align with source rows")
                if _unknown(request.sample_time_s):
                    issues.append("unresolved path sample clock; source rows retained")
                elif any(
                    b < a for a, b in zip(request.sample_time_s, request.sample_time_s[1:], strict=False)
                ):
                    raise ValueError("path clock decreases; no sorting or repair")
                elif request.observation_period_s is not None and any(
                    t < request.observation_period_s[0] or t > request.observation_period_s[1]
                    for t in request.sample_time_s
                ):
                    raise ValueError("path clock lies outside the declared observation period")
    if request.method == "fatigue.goodman" and request.strain_measure not in {None, "not_used"}:
        raise ValueError("Goodman has no strain input; use the not_used declaration")
    if request.method != "fatigue.goodman" and request.strain_measure == "not_used":
        raise ValueError("strain measure must be supplied for strain or path inputs")
    body = {
        "schema": "pipeline_integrity.cyclic_fatigue.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "values": {},
        "output_units": {},
        "issues": issues,
        "native_receipt": None,
        "owner_source_tree_sha256": None,
        "source_pin": None,
        "output_origin": "simulation",
        "failure_probability": None,
        "remaining_service_life_s": None,
        "cumulative_damage": None,
        "material_or_code_acceptance": False,
        "field_or_population_validation": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        client = MaterialClient() if client is None else client
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
                job = MaterialJob.model_validate_json(pinned_job.model_dump_json())
                if (
                    job.model_id,
                    job.asset_id,
                    job.location_id,
                    job.clock_id,
                    json_value(job.parameters),
                    job.evidence_ids,
                ) != (
                    request.method,
                    request.asset_id,
                    request.location_id,
                    request.clock_id,
                    json_value(request.parameters),
                    request.evidence_ids,
                ):
                    raise ValueError("pinned cyclic job differs from the reviewed request")
            receipt = evaluate_material_job(job, context, client=client)
            json_value(receipt.model_dump(mode="python"))
            receipt.check_integrity()
            payload = receipt.native.payload
            body.update(
                status=payload["status"],
                values=payload["values"],
                output_units=payload["units"],
                issues=list(payload["notes"]),
                native_receipt=receipt.model_dump(mode="json"),
                owner_source_tree_sha256=request.owner_source_tree_sha256,
            )
        body["source_pin"] = pin
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))
