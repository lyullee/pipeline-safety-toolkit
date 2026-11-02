"""Evidence-bound inspection statistics and geometry delegated to their material owner.

Trial truth, missing cells and acquisition identities are inputs, never inferred labels.
No detection inference, confidence algorithm or geometry solution is implemented here.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import (
    MaterialJob,
    MaterialReceipt,
    evaluate_material_job,
    prepare_material_job,
)
from .material_source import material_source_guard

PARAMETER_UNITS = {
    "inspection.binomial_evidence": {
        "opportunities": "count",
        "positive_responses": "count",
        "confidence_level": "1",
        "response_kind": "category",
        "independent_trials_declared": "bool",
    },
    "inspection.hit_miss_pod": {
        "specimen_ids": "identifier",
        "flaw_size_m": "m",
        "detected": "bool",
        "query_sizes_m": "m",
        "target_probability": "1",
        "confidence_level": "1",
        "maximum_threshold_size_m": "m",
        "independent_trials_declared": "bool",
    },
    "inspection.tofd_depth": {
        "probe_index_separation_m": "m",
        "longitudinal_sound_speed_m_s": "m/s",
        "delay_from_lateral_wave_s": "s",
        "wall_thickness_m": "m",
    },
    "geometry.thickness_grid": {
        "cell_thickness_m": "m",
        "cell_size_x_m": "m",
        "cell_size_y_m": "m",
        "reference_thickness_m": "m",
        "reporting_threshold_m": "m",
    },
}
REVIEWS = {
    "inspection.binomial_evidence": (
        "independent_comparable_trials",
        "binary_truth_and_response_definition",
        "declared_size_or_verified_blank",
        "procedure_and_test_conditions",
    ),
    "inspection.hit_miss_pod": (
        "independent_physical_specimens",
        "binary_truth_and_known_flaw_sizes",
        "procedure_and_test_conditions",
        "increasing_log_size_model_and_query_support",
    ),
    "inspection.tofd_depth": (
        "symmetric_straight_path_and_midpoint_tip",
        "same_frame_lateral_reference_and_calibrated_delays",
        "probe_index_separation_velocity_and_wall_basis",
        "procedure_and_test_conditions",
    ),
    "geometry.thickness_grid": (
        "equal_area_cell_placement_and_original_order",
        "measured_thickness_and_missing_cells",
        "reference_and_reporting_threshold_basis",
        "procedure_and_test_conditions",
    ),
}


class InspectionTrial(Contract):
    observation_id: Name
    specimen_id: Name
    clock_id: Name
    truth_flawed: bool | None
    detected: bool | None
    flaw_size_m: Positive | None
    cluster_id: Name | None = None
    acquired_time_s: Nonnegative | None = None


class TOFDReference(Contract):
    clock_id: Name
    lateral_frame_id: Name
    diffraction_frame_id: Name
    frame_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transmit_probe_id: Name
    receive_probe_id: Name
    transmit_wedge_id: Name
    receive_wedge_id: Name
    separation_basis_id: Name
    velocity_basis_id: Name
    wall_thickness_basis_id: Name
    lateral_arrival_s: Nonnegative | None
    diffraction_arrival_s: Nonnegative | None
    backwall_arrival_s: Nonnegative | None = None

    @model_validator(mode="after")
    def distinct_probes(self):
        if self.transmit_probe_id == self.receive_probe_id:
            raise ValueError("TOFD needs distinct transmit and receive probe identities")
        return self


class ThicknessGridLayout(Contract):
    coordinate_frame_id: Name
    row_ids: tuple[Name, ...] = Field(min_length=1)
    column_ids: tuple[Name, ...] = Field(min_length=1)
    row_axis: Literal["y"] = "y"
    column_axis: Literal["x"] = "x"
    positive_x_direction: Name
    positive_y_direction: Name
    first_cell_center_x_m: float
    first_cell_center_y_m: float

    @model_validator(mode="after")
    def unique_axes(self):
        if len(set(self.row_ids)) != len(self.row_ids) or len(set(self.column_ids)) != len(self.column_ids):
            raise ValueError("grid row and column identities must each be unique")
        return self


class InspectionStatisticsRequest(Contract):
    method: Literal[
        "inspection.binomial_evidence",
        "inspection.hit_miss_pod",
        "inspection.tofd_depth",
        "geometry.thickness_grid",
    ]
    asset_id: Name
    location_id: Name
    clock_id: Name
    specimen_set_id: Name
    material_condition_id: Name
    procedure_id: Name
    procedure_revision: Name
    test_conditions_id: Name
    parameters: dict | None
    input_units: dict[str, str]
    trials: tuple[InspectionTrial, ...] = ()
    declared_detection_size_m: Positive | None = None
    tofd_reference: TOFDReference | None = None
    grid_layout: ThicknessGridLayout | None = None
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_observations: int = Field(default=100000, ge=1, le=1000000)
    maximum_grid_cells: int = Field(default=100000, ge=1, le=1000000)

    @model_validator(mode="after")
    def declared_contract(self):
        json_value(self.parameters)
        if self.input_units != PARAMETER_UNITS[self.method]:
            raise ValueError("inspection inputs need the exact SI, count, binary and category units")
        allowed = set(REVIEWS[self.method])
        if set(self.applicability_reviews) - allowed or set(self.review_evidence_ids) - allowed:
            raise ValueError("inspection reviews must match the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("inspection evidence identifiers must be distinct")
        if self.method not in {"inspection.binomial_evidence", "inspection.hit_miss_pod"} and self.trials:
            raise ValueError("trial observations belong only to binomial evidence or hit/miss POD")
        if self.method != "inspection.tofd_depth" and self.tofd_reference is not None:
            raise ValueError("TOFD references belong only to TOFD geometry")
        if self.method != "geometry.thickness_grid" and self.grid_layout is not None:
            raise ValueError("cell layouts belong only to thickness grids")
        if self.method != "inspection.binomial_evidence" and self.declared_detection_size_m is not None:
            raise ValueError("a declared single detection size belongs only to binomial evidence")
        return self


def _unknown(value):
    if isinstance(value, dict):
        return any(_unknown(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_unknown(v) for v in value)
    return value is None


def _budget(request):
    if len(request.trials) > request.maximum_observations:
        raise ValueError("inspection observation budget exceeded; no truncation")
    p = request.parameters or {}
    for key in ("specimen_ids", "flaw_size_m", "detected"):
        if isinstance(p.get(key), (list, tuple)) and len(p[key]) > request.maximum_observations:
            raise ValueError("inspection observation budget exceeded; no truncation")
    grid = p.get("cell_thickness_m")
    if isinstance(grid, (list, tuple)):
        count = sum(len(row) for row in grid if isinstance(row, (list, tuple)))
        if count > request.maximum_grid_cells:
            raise ValueError("thickness grid cell budget exceeded; no truncation")


def _trial_issues(request):
    trials, p = request.trials, request.parameters
    if not trials:
        return ["Actual binary truth and detection observations are required; no labels are inferred."]
    if len({t.observation_id for t in trials}) != len(trials):
        raise ValueError("inspection observation IDs must be unique")
    if any(t.clock_id != request.clock_id for t in trials):
        raise ValueError("trial acquisition clock differs from the request clock")
    if any(t.truth_flawed is None or t.detected is None for t in trials):
        return ["Unresolved binary truth or detection is not a miss or a negative response."]
    clusters = [t.cluster_id for t in trials if t.cluster_id is not None]
    if len({t.specimen_id for t in trials}) != len(trials) or len(set(clusters)) != len(clusters):
        return ["Repeated specimens or shared clusters require a separate dependence model."]
    if p.get("independent_trials_declared") is not True:
        return ["Independent physical opportunities must be explicitly declared and reviewed."]
    if request.method == "inspection.binomial_evidence":
        if type(p["opportunities"]) is not int or type(p["positive_responses"]) is not int:
            raise ValueError(
                "binomial opportunities and responses must be integer counts, excluding booleans"
            )
        if p["opportunities"] != len(trials) or p["positive_responses"] != sum(t.detected for t in trials):
            raise ValueError("binomial counts differ from the ordered observation witnesses")
        if p["response_kind"] == "detection_at_declared_size":
            if request.declared_detection_size_m is None:
                return ["Detection evidence needs a declared physical flaw size."]
            if any(not t.truth_flawed or t.flaw_size_m != request.declared_detection_size_m for t in trials):
                return ["Detection opportunities must have verified flaws at the single declared size."]
        elif p["response_kind"] == "false_call_on_blank":
            if request.declared_detection_size_m is not None:
                raise ValueError("blank false-call evidence cannot declare a flaw size")
            if any(t.truth_flawed or t.flaw_size_m is not None for t in trials):
                return ["False-call opportunities need verified blanks with no assigned flaw size."]
        else:
            raise ValueError("response_kind must identify detection at a size or false calls on blanks")
    else:
        if any(not t.truth_flawed or t.flaw_size_m is None for t in trials):
            return ["Hit/miss POD requires verified flawed specimens with known physical sizes."]
        if (
            p["specimen_ids"] != [t.specimen_id for t in trials]
            or p["flaw_size_m"] != [t.flaw_size_m for t in trials]
            or p["detected"] != [t.detected for t in trials]
        ):
            raise ValueError("POD parameter vectors differ from the original ordered observation witnesses")
        if any(type(v) is not bool for v in p["detected"]):
            raise ValueError("POD outcomes must be actual binary booleans")
    return []


def _geometry_issues(request):
    p = request.parameters
    if request.method == "inspection.tofd_depth":
        ref = request.tofd_reference
        if ref is None:
            return ["A frame-bound lateral-wave reference and physical TOFD geometry are required."]
        if ref.clock_id != request.clock_id:
            raise ValueError("TOFD reference acquisition clock differs from the request clock")
        if ref.lateral_frame_id != ref.diffraction_frame_id:
            return ["Lateral-wave and diffraction picks must use the same immutable frame."]
        if ref.lateral_arrival_s is None or ref.diffraction_arrival_s is None:
            return ["Unknown arrival picks are not fabricated or converted to zero delay."]
        if ref.diffraction_arrival_s < ref.lateral_arrival_s:
            return ["The diffraction arrival precedes its lateral reference; preserve the noncausal picks."]
        if ref.backwall_arrival_s is not None and ref.backwall_arrival_s <= ref.lateral_arrival_s:
            return ["The supplied backwall reference is not later than its lateral arrival."]
        delay = ref.diffraction_arrival_s - ref.lateral_arrival_s
        if not math.isclose(p["delay_from_lateral_wave_s"], delay, rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError("TOFD supplied delay differs from its original lateral-reference picks")
    elif request.method == "geometry.thickness_grid":
        layout, grid = request.grid_layout, p["cell_thickness_m"]
        if layout is None:
            return ["The original cell placement, coordinate basis and row/column identities are required."]
        if not isinstance(grid, list) or not grid or any(not isinstance(row, list) for row in grid):
            raise ValueError("thickness cells require the original rectangular row-major list")
        if len(grid) != len(layout.row_ids) or any(len(row) != len(layout.column_ids) for row in grid):
            raise ValueError("thickness grid dimensions differ from the original row/column placement")
        if all(cell is None for row in grid for cell in row):
            return ["No measured thickness cells are available; missing cells are not sound cells."]
    return []


def evaluate_inspection_statistics(
    request: InspectionStatisticsRequest,
    context: RunContext,
    *,
    client=None,
    pinned_job: MaterialJob | None = None,
    source_root=None,
    expected_source_sha256: str | None = None,
):
    """Preserve native numerical status and meanings after evidence-bound readiness checks."""
    if not isinstance(request, InspectionStatisticsRequest) or not isinstance(context, RunContext):
        raise TypeError("InspectionStatisticsRequest and RunContext are required")
    # Check Python values before Pydantic's JSON serializer can turn NaN/Infinity into null.
    json_value(request.model_dump(mode="python"))
    json_value(context.model_dump(mode="python"))
    if pinned_job is not None:
        if not isinstance(pinned_job, MaterialJob):
            raise TypeError("a pinned inspection job must be MaterialJob")
        json_value(pinned_job.model_dump(mode="python"))
    request = InspectionStatisticsRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    _budget(request)
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("inspection asset or source clock differs from its context")
    ids = [e.id for e in context.evidence]
    if len(set(ids)) != len(ids) or not set(request.evidence_ids) <= set(ids):
        raise ValueError("inspection evidence must be unique and present in context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("inspection review evidence must occur in selected context evidence")
    if (source_root is None) != (expected_source_sha256 is None):
        raise ValueError("source pin requires both source_root and expected_source_sha256")
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS[request.method]
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    p = request.parameters
    required = set(PARAMETER_UNITS[request.method])
    if p is not None and set(p) - required:
        raise ValueError("inspection parameters contain keys outside the selected native model")
    if p is None or required - set(p):
        issues.append("Native input fields are unresolved; native defaults are not substituted.")
    elif _unknown({k: v for k, v in p.items() if k != "cell_thickness_m"}):
        issues.append("Native inputs contain unknown values; missing is not zero or false.")
    elif request.method in {"inspection.binomial_evidence", "inspection.hit_miss_pod"}:
        issues.extend(_trial_issues(request))
    else:
        issues.extend(_geometry_issues(request))
    body = {
        "schema": "pipeline_integrity.inspection_statistics.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "values": {},
        "output_units": {},
        "probability_semantics": {},
        "issues": issues,
        "native_receipt": None,
        "source_pin": None,
        "period_failure_probability": None,
        "human_error_probability": None,
        "inspection_or_material_acceptance": False,
        "independent_field_validation": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        with material_source_guard(source_root, expected_source_sha256, client) as source_pin:
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
                    raise ValueError("the pinned inspection job differs from its reviewed request")
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
                source_pin=source_pin,
            )
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def check_inspection_statistics_record(record):
    """Detect later changes to nested records and duplicated native result projections."""
    json_value(record)
    if digest({k: v for k, v in record.items() if k != "record_digest"}) != record["record_digest"]:
        raise ValueError("inspection record changed")
    if record["native_receipt"] is not None:
        receipt = MaterialReceipt.model_validate_json(json.dumps(record["native_receipt"], allow_nan=False))
        receipt.check_integrity()
        payload = receipt.native.payload
        if any(
            record[key] != payload[native_key]
            for key, native_key in (
                ("status", "status"),
                ("values", "values"),
                ("output_units", "units"),
                ("probability_semantics", "probability_semantics"),
            )
        ):
            raise ValueError("inspection result projection differs from its native receipt")
    return True
