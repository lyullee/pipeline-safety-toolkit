"""Reviewed dynamic-trajectory comparisons; no static-toughness surrogate for arrest."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .fracture_exchange import FractureIdentity

SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "coupled_dynamic_decompression_and_crack_driving_force",
    "dynamic_resistance_material_orientation_temperature_and_rate",
    "geometry_crack_path_and_shared_space_time_support",
    "sampled_energy_velocity_and_pressure_envelope_meaning",
)


class ArrestPoint(Contract):
    id: Name
    time_s: Nonnegative
    crack_path_position_m: float | None
    local_pressure_pa_abs: Positive | None
    temperature_k: Positive | None
    propagation_velocity_m_s: Nonnegative | None
    driving_energy_release_j_m2: Nonnegative | None
    dynamic_resistance_j_m2: Positive | None
    arrest_pressure_envelope_pa_abs: Positive | None
    valid: bool | None


class ArrestTrajectory(Contract):
    status: Literal["computed", "withheld"]
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    asset_id: Name
    clock_id: Name
    event_id: Name
    coordinate_frame_id: Name
    crack_path_id: Name
    material_condition_id: Name
    material_orientation_id: Name
    provider_id: Name
    provider_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    provider_configuration_sha256: str = Field(pattern=SHA)
    decompression_source_sha256: str = Field(pattern=SHA)
    resistance_definition: Literal["dynamic_energy_per_new_crack_area"]
    new_crack_area_basis_id: Name
    pressure_envelope_definition_id: Name
    points: tuple[ArrestPoint, ...] = Field(min_length=2, max_length=100000)
    native_receipt: dict = Field(default_factory=dict)
    evaluation_sha256: str = Field(pattern=SHA)

    @model_validator(mode="after")
    def ordered(self):
        if len({p.id for p in self.points}) != len(self.points) or any(
            b.time_s <= a.time_s for a, b in zip(self.points, self.points[1:], strict=False)
        ):
            raise ValueError("trajectory point IDs must be distinct and source times strictly increase")
        _receipt_budget(self.native_receipt)
        json_value(self.native_receipt)
        return self


class FractureArrestRequest(FractureIdentity):
    id: Name
    event_id: Name
    trajectory_origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    coordinate_frame_id: Name
    crack_path_id: Name
    material_orientation_id: Name | None
    new_crack_area_basis_id: Name | None
    pressure_envelope_definition_id: Name | None
    gas_composition_id: Name | None
    eos_basis_id: Name | None
    pipe_diameter_m: Positive | None
    wall_thickness_m: Positive | None
    path_position_limits_m: tuple[float, float]
    propagation_direction: Literal["increasing_path", "decreasing_path"]
    time_limits_s: tuple[Nonnegative, Positive]
    supported_temperature_k: tuple[Positive, Positive]
    source_mode: Literal["reviewed_trajectory", "provider"]
    provider_id: Name
    provider_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    provider_configuration_sha256: str = Field(pattern=SHA)
    decompression_source_sha256: str | None = Field(pattern=SHA)
    expected_trajectory_payload_sha256: str | None = Field(default=None, pattern=SHA)
    stationary_velocity_tolerance_m_s: Nonnegative
    stationary_position_tolerance_m: Nonnegative
    minimum_sampled_window_s: Positive
    maximum_sample_gap_s: Positive
    maximum_points: int = Field(default=20000, ge=2, le=100000)
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)

    @model_validator(mode="after")
    def support(self):
        if (set(self.reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("review keys differ from dynamic trajectory applicability boundary")
        if (
            any(b <= a for a, b in (self.path_position_limits_m, self.time_limits_s))
            or self.supported_temperature_k[1] < self.supported_temperature_k[0]
        ):
            raise ValueError("declared space/time/temperature support is reversed or empty")
        if (
            self.pipe_diameter_m is not None
            and self.wall_thickness_m is not None
            and 2 * self.wall_thickness_m >= self.pipe_diameter_m
        ):
            raise ValueError("declared outer pipe diameter must exceed twice the wall thickness")
        if self.source_mode == "reviewed_trajectory" and self.expected_trajectory_payload_sha256 is None:
            raise ValueError(
                "reviewed trajectory requires a preselected payload digest excluding evaluation seal"
            )
        return self


class ArrestProvider(Protocol):
    def __call__(self, request: FractureArrestRequest, context: RunContext) -> ArrestTrajectory: ...


def _receipt_budget(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100000 or depth > 16:
            raise ValueError("native receipt node/depth budget exceeded")
        if isinstance(item, (dict, tuple, list)):
            if len(item) + count > 100000:
                raise ValueError("native receipt work budget exceeded")
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))


def trajectory_payload_sha256(trajectory):
    return digest(trajectory.model_dump(mode="python", exclude={"evaluation_sha256"}))


def boundary_metadata(q):
    return {
        "request_sha256": digest(q),
        "meaning": "sampled dynamic trajectory compatibility; no proven arrest",
    }


def trajectory_binding(request, context, trajectory):
    return digest(
        {"request": request, "context": context, "trajectory_payload": trajectory_payload_sha256(trajectory)}
    )


def assess_fracture_arrest(
    request: FractureArrestRequest, context: RunContext, *, trajectory=None, provider=None
):
    if trajectory is not None:
        if len(trajectory.points) > request.maximum_points or len(trajectory.points) > 100000:
            raise ValueError("trajectory point budget exceeded before cloning")
        _receipt_budget(trajectory.native_receipt)
    q = FractureArrestRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id, q.trajectory_origin) != (c.asset_id, c.clock_id, c.origin) or c.metadata.get(
        "fracture_arrest_boundary"
    ) != boundary_metadata(q):
        raise ValueError("context must bind the exact fracture trajectory request, asset and clock")
    evidence = [e.id for e in c.evidence]
    if len(set(evidence)) != len(evidence) or (
        set(q.evidence_ids) | set(q.review_evidence_ids.values())
    ) - set(evidence):
        raise ValueError("all material/decompression/geometry/review evidence must be selected")
    missing = [key for key in REVIEWS if q.reviews.get(key) is not True or key not in q.review_evidence_ids]
    if any(
        v is None
        for v in (
            q.material_orientation_id,
            q.new_crack_area_basis_id,
            q.pressure_envelope_definition_id,
            q.gas_composition_id,
            q.eos_basis_id,
            q.pipe_diameter_m,
            q.wall_thickness_m,
            q.decompression_source_sha256,
        )
    ):
        missing.append("known material orientation, gas/EOS, geometry and decompression source")
    body = {
        "schema": "fracture_arrest.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "status": "needs_data",
        "missing": missing,
        "out_of_scope": [],
        "trajectory": None,
        "point_comparisons": [],
        "sampled_stationary_compatible_windows": [],
        "dynamic_arrest_proven": False,
        "continuous_hold_proven": False,
        "field_or_code_acceptance": False,
        "failure_probability": None,
        "output_origin": "simulation",
        "module_sha256": file_digest(Path(__file__)),
        "dependency_source_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py"))
            for name in ("contracts", "fracture_exchange", "material_exchange")
        },
        "runtime": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "pydantic_core": version("pydantic-core"),
        },
    }

    def seal():
        json_value(body)
        return dict(body, record_sha256=digest(body))

    if missing:
        return seal()
    if q.source_mode == "provider":
        if trajectory is not None:
            raise ValueError("provider mode cannot replace engine output with a supplied trajectory")
        if provider is None:
            body["missing"].append("explicit original dynamic trajectory provider")
            return seal()
        trajectory = provider(q, c)
    elif provider is not None:
        raise ValueError("reviewed trajectory mode cannot silently switch to another provider")
    if trajectory is None:
        body["missing"].append("reviewed simultaneous dynamic trajectory")
        return seal()
    if not isinstance(trajectory, ArrestTrajectory):
        raise TypeError("provider must return ArrestTrajectory")
    if len(trajectory.points) > q.maximum_points:
        raise ValueError("provider output exceeds declared point work budget")
    _receipt_budget(trajectory.native_receipt)
    p = ArrestTrajectory.model_validate(trajectory.model_dump(mode="python"))
    if (
        p.asset_id,
        p.clock_id,
        p.event_id,
        p.coordinate_frame_id,
        p.crack_path_id,
        p.material_condition_id,
        p.material_orientation_id,
        p.provider_id,
        p.provider_version,
        p.provider_source_sha256,
        p.provider_configuration_sha256,
        p.decompression_source_sha256,
    ) != (
        q.asset_id,
        q.clock_id,
        q.event_id,
        q.coordinate_frame_id,
        q.crack_path_id,
        q.material_condition_id,
        q.material_orientation_id,
        q.provider_id,
        q.provider_version,
        q.provider_source_sha256,
        q.provider_configuration_sha256,
        q.decompression_source_sha256,
    ):
        raise ValueError("trajectory physical/source/configuration identity differs from selected request")
    if (p.origin, p.new_crack_area_basis_id, p.pressure_envelope_definition_id) != (
        q.trajectory_origin,
        q.new_crack_area_basis_id,
        q.pressure_envelope_definition_id,
    ):
        raise ValueError("trajectory origin or energy-area/pressure-envelope physical basis differs")
    if (
        q.expected_trajectory_payload_sha256 is not None
        and trajectory_payload_sha256(p) != q.expected_trajectory_payload_sha256
    ):
        raise ValueError("preselected trajectory payload changed")
    if p.evaluation_sha256 != trajectory_binding(q, c, p):
        raise ValueError("trajectory input/output/evidence evaluation binding changed")
    body["trajectory"] = p.model_dump(mode="json")
    if p.status != "computed":
        body["missing"].append("original trajectory provider withheld computation")
        return seal()
    if (p.points[0].time_s, p.points[-1].time_s) != q.time_limits_s:
        raise ValueError("trajectory endpoints must match the exact requested time support")
    missing_point = any(
        v is None
        for row in p.points
        for v in (
            row.crack_path_position_m,
            row.local_pressure_pa_abs,
            row.temperature_k,
            row.propagation_velocity_m_s,
            row.driving_energy_release_j_m2,
            row.dynamic_resistance_j_m2,
            row.arrest_pressure_envelope_pa_abs,
        )
    ) or any(row.valid is not True for row in p.points)
    if missing_point:
        body["missing"].append(
            "known valid simultaneous pressure, velocity, energy, resistance, position and temperature at every point"
        )
        return seal()
    sign = 1 if q.propagation_direction == "increasing_path" else -1
    for left, right in zip(p.points, p.points[1:], strict=False):
        if sign * (right.crack_path_position_m - left.crack_path_position_m) < 0:
            raise ValueError("crack position reverses the declared one-direction propagation path")
    for row in p.points:
        if (
            not q.path_position_limits_m[0] <= row.crack_path_position_m <= q.path_position_limits_m[1]
            or not q.supported_temperature_k[0] <= row.temperature_k <= q.supported_temperature_k[1]
        ):
            body["out_of_scope"].append(row.id)
    if body["out_of_scope"]:
        body["status"] = "out_of_scope"
        return seal()
    start = None
    for i, row in enumerate(p.points):
        gap = row.dynamic_resistance_j_m2 - row.driving_energy_release_j_m2
        pressure_gap = row.arrest_pressure_envelope_pa_abs - row.local_pressure_pa_abs
        if not math.isfinite(gap) or not math.isfinite(pressure_gap):
            raise ValueError("comparison arithmetic cannot represent finite energy/pressure margins")
        compatible = (
            gap >= 0
            and pressure_gap >= 0
            and row.propagation_velocity_m_s <= q.stationary_velocity_tolerance_m_s
        )
        body["point_comparisons"].append(
            {
                "point_id": row.id,
                "energy_margin_j_m2": gap,
                "pressure_margin_pa": pressure_gap,
                "sampled_stationary_compatible": compatible,
            }
        )
        if i and row.time_s - p.points[i - 1].time_s > q.maximum_sample_gap_s:
            start = None
        if not compatible:
            start = None
            continue
        if start is None:
            start = i
        # Position is checked against the start of this sampled window, without velocity integration.
        while (
            start < i
            and abs(row.crack_path_position_m - p.points[start].crack_path_position_m)
            > q.stationary_position_tolerance_m
        ):
            start += 1
        duration = row.time_s - p.points[start].time_s
        if duration >= q.minimum_sampled_window_s:
            body["sampled_stationary_compatible_windows"].append(
                {
                    "start_point_id": p.points[start].id,
                    "end_point_id": row.id,
                    "sample_span_s": duration,
                    "sample_count": i - start + 1,
                    "meaning": "criteria hold at provided samples only; unobserved intervals and restart remain unresolved",
                }
            )
    body["status"] = "computed"
    return seal()


def verify_fracture_arrest_record(record, *, provider=None, request=None, context=None):
    if digest({k: v for k, v in record.items() if k != "record_sha256"}) != record.get("record_sha256"):
        raise ValueError("fracture trajectory record seal changed")
    q = FractureArrestRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if (
        request is not None
        and digest(request) != digest(q)
        or context is not None
        and digest(context) != digest(c)
    ):
        raise ValueError("caller boundary differs from stored fracture trajectory request")
    trajectory = None
    if q.source_mode == "reviewed_trajectory" and record["trajectory"] is not None:
        trajectory = ArrestTrajectory.model_validate_json(json.dumps(record["trajectory"]))
    if assess_fracture_arrest(q, c, trajectory=trajectory, provider=provider) != record:
        raise ValueError("fracture trajectory source/runtime/native replay differs")
    return True
