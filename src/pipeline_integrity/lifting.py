"""Rigid-pose sling equilibrium and caller-reviewed effective capacity checks."""

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest

Vector = tuple[float, float, float]
Target = Literal["sling", "attachment", "anchor"]


class LiftingFrame(Contract):
    id: Name
    definition_id: Name
    axes: Literal["right_handed_local_xyz_m"] = "right_handed_local_xyz_m"
    evidence_id: Name


class Sling(Contract):
    id: Name
    attachment_id: Name
    attachment_m: Vector
    anchor_id: Name
    anchor_m: Vector
    geometry_evidence_id: Name

    @model_validator(mode="after")
    def separated(self):
        if self.attachment_m == self.anchor_m:
            raise ValueError("sling ends must be distinct")
        return self


class ExternalLiftingLoad(Contract):
    id: Name
    position_m: Vector
    force_n: Vector | None
    moment_nm: Vector | None = (0.0, 0.0, 0.0)
    evidence_id: Name


class EffectiveLiftingCapacity(Contract):
    """An already reviewed effective force limit, never a built-in WLL table."""

    target_kind: Target
    target_id: Name
    force_limit_n: Positive | None
    direction_policy: Literal["all_directions", "within_cone"]
    rated_direction: Vector | None = None
    maximum_angle_rad: float | None = Field(default=None, ge=0, le=math.pi)
    applicability_id: Name
    evidence_id: Name
    reviewed: bool = False

    @model_validator(mode="after")
    def direction(self):
        if self.direction_policy == "within_cone":
            if self.rated_direction is None or self.maximum_angle_rad is None:
                raise ValueError("a directional force limit requires a rated direction and cone angle")
            if math.hypot(*self.rated_direction) == 0:
                raise ValueError("rated force direction cannot be zero")
        elif self.rated_direction is not None or self.maximum_angle_rad is not None:
            raise ValueError("an all-direction limit cannot carry an unused directional cone")
        return self


class LiftingRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    frame: LiftingFrame
    center_of_mass_m: Vector
    mass_kg: Positive | None
    gravity_m_s2: Vector
    slings: tuple[Sling, ...] = Field(min_length=1, max_length=64)
    external_loads: tuple[ExternalLiftingLoad, ...] = Field(default=(), max_length=256)
    capacities: tuple[EffectiveLiftingCapacity, ...] = Field(default=(), max_length=192)
    load_multiplier: Positive = 1.0
    load_basis_evidence_id: Name
    geometry_reviewed: bool = False
    complete_free_body_reviewed: bool = False
    moment_reference_m: Vector = (0.0, 0.0, 0.0)
    moment_length_scale_m: Positive
    rank_relative_cutoff: float = Field(default=1e-12, gt=0, lt=1)
    maximum_condition_number: float = Field(default=1e10, ge=1)
    force_residual_tolerance_n: Positive = 1e-7
    moment_residual_tolerance_nm: Positive = 1e-7

    @model_validator(mode="after")
    def coherent(self):
        if math.hypot(*self.gravity_m_s2) == 0:
            raise ValueError("declare a nonzero gravity vector")
        if len({s.id for s in self.slings}) != len(self.slings):
            raise ValueError("sling IDs must be unique")
        if len({s.id for s in self.external_loads}) != len(self.external_loads):
            raise ValueError("external load IDs must be unique")
        anchors, attachments = {}, {}
        for sling in self.slings:
            for mapping, name, point in (
                (anchors, sling.anchor_id, sling.anchor_m),
                (attachments, sling.attachment_id, sling.attachment_m),
            ):
                if name in mapping and mapping[name] != point:
                    raise ValueError("a shared physical point ID must retain the same coordinates")
                mapping[name] = point
        targets = {("sling", s.id) for s in self.slings}
        targets.update(("anchor", name) for name in anchors)
        targets.update(("attachment", name) for name in attachments)
        supplied = [(c.target_kind, c.target_id) for c in self.capacities]
        if len(set(supplied)) != len(supplied) or set(supplied) - targets:
            raise ValueError("capacity targets must be unique existing physical components")
        return self


def boundary_metadata(request: LiftingRequest):
    return {
        "request_id": request.id,
        "asset_id": request.asset_id,
        "clock_id": request.clock_id,
        "time_s": request.time_s,
        "frame": request.frame.model_dump(mode="json"),
        "convention": "sling_force_on_payload_toward_anchor; anchor_reaction_opposite",
        "pose": "caller_declared_fixed_rigid_pose",
    }


def _strict(request, context):
    request = LiftingRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("lifting request and context asset/clock differ")
    if context.metadata.get("lifting_boundary") != boundary_metadata(request):
        raise ValueError("lifting frame, pose and force convention require an exact context binding")
    required = {request.frame.evidence_id, request.load_basis_evidence_id}
    required.update(s.geometry_evidence_id for s in request.slings)
    required.update(s.evidence_id for s in request.external_loads)
    required.update(c.evidence_id for c in request.capacities)
    if required - {e.id for e in context.evidence}:
        raise ValueError("lifting input evidence is not present in context")
    return request, context


def _runtime():
    return {
        "python": platform.python_version(),
        **{name: version(name) for name in ("numpy", "pydantic", "pydantic-core")},
    }


def _seal(record):
    record["record_digest"] = digest(record)
    return record


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _sum_vectors(vectors):
    return tuple(math.fsum(row[i] for row in vectors) for i in range(3))


def _capacity_checks(request, forces):
    supplied = {(c.target_kind, c.target_id): c for c in request.capacities}
    results = []
    for (kind, name), vector in sorted(forces.items()):
        magnitude = math.hypot(*vector)
        capacity = supplied.get((kind, name))
        check = {
            "target_kind": kind,
            "target_id": name,
            "force_vector_n": list(vector),
            "force_n": magnitude,
            "force_limit_n": None,
            "utilization": None,
            "within_limit": None,
            "direction_angle_rad": None,
            "status": "needs_data",
            "reason": "reviewed effective capacity is missing",
        }
        if capacity is not None and capacity.reviewed and capacity.force_limit_n is not None:
            check["force_limit_n"] = capacity.force_limit_n
            applicable = True
            if capacity.direction_policy == "within_cone" and magnitude != 0:
                rated = capacity.rated_direction
                rated_norm = math.hypot(*rated)
                # atan2 avoids cancellation near parallel/antiparallel directions.
                unit = tuple(v / magnitude for v in vector)
                rated_unit = tuple(v / rated_norm for v in rated)
                angle = math.atan2(
                    math.hypot(*_cross(unit, rated_unit)),
                    math.fsum(a * b for a, b in zip(unit, rated_unit, strict=True)),
                )
                check["direction_angle_rad"] = angle
                applicable = angle <= capacity.maximum_angle_rad
            if applicable:
                utilization = magnitude / capacity.force_limit_n
                if not math.isfinite(utilization) or (magnitude > 0 and utilization == 0):
                    raise ArithmeticError(
                        "force utilization cannot be represented without overflow/underflow"
                    )
                check.update(
                    status="computed",
                    reason="caller-reviewed effective force limit",
                    utilization=utilization,
                    within_limit=magnitude <= capacity.force_limit_n,
                )
            else:
                check.update(
                    status="out_of_scope", reason="force direction is outside the supplied capacity cone"
                )
        results.append(check)
    return results


def evaluate_lifting(request: LiftingRequest, context: RunContext):
    """Solve only uniquely determined tensile forces at the supplied rigid pose."""
    request, context = _strict(request, context)
    record = {
        "schema": "pipeline_integrity.lifting.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "runtime": _runtime(),
        "status": "needs_data",
        "issues": [],
        "native_solver_called": False,
        "solution": None,
        "capacity_checks": [],
        "acceptance": "not_evaluated",
        "qualification": "static_force_screening_at_declared_pose",
        "configuration_stability_solved": False,
        "dynamic_load_history_solved": False,
        "field_qualified": False,
    }
    if not request.geometry_reviewed or not request.complete_free_body_reviewed:
        record["issues"] = ["geometry and complete free body require review; no missing load is assumed zero"]
        return _seal(record)
    if request.mass_kg is None or any(
        s.force_n is None or s.moment_nm is None for s in request.external_loads
    ):
        record["issues"] = ["payload mass or a declared external load is unknown"]
        return _seal(record)

    import numpy as np

    try:
        directions, columns = [], []
        for sling in request.slings:
            delta = _subtract(sling.anchor_m, sling.attachment_m)
            length = math.hypot(*delta)
            if length == 0 or not math.isfinite(length):
                raise ArithmeticError("sling direction is not representable")
            unit = tuple(v / length for v in delta)
            moment = _cross(_subtract(sling.attachment_m, request.moment_reference_m), unit)
            column = (*unit, *(v / request.moment_length_scale_m for v in moment))
            if not all(math.isfinite(v) for v in column):
                raise ArithmeticError("moment arms exceed numerical range")
            directions.append(unit)
            columns.append(column)
        weight = tuple(request.mass_kg * v * request.load_multiplier for v in request.gravity_m_s2)
        external_forces = [weight]
        external_moments = [_cross(_subtract(request.center_of_mass_m, request.moment_reference_m), weight)]
        for load in request.external_loads:
            force = tuple(v * request.load_multiplier for v in load.force_n)
            external_forces.append(force)
            arm_moment = _cross(_subtract(load.position_m, request.moment_reference_m), force)
            external_moments.append(
                tuple(
                    v + c * request.load_multiplier for v, c in zip(arm_moment, load.moment_nm, strict=True)
                )
            )
        force = _sum_vectors(external_forces)
        moment = _sum_vectors(external_moments)
        rhs = tuple(-v for v in force) + tuple(-v / request.moment_length_scale_m for v in moment)
        matrix = np.asarray(columns, dtype=float).T
        if not all(math.isfinite(v) for v in rhs):
            raise ArithmeticError("the external load is outside numerical range")
        # Scaling the RHS retains small finite loads instead of LAPACK flushing them.
        load_scale = max(abs(v) for v in rhs)
        if load_scale == 0:
            raise ArithmeticError("a zero external resultant does not establish a taut lifting configuration")
        record["native_solver_called"] = True
        scaled, _, rank, singular = np.linalg.lstsq(
            matrix, np.asarray(rhs) / load_scale, rcond=request.rank_relative_cutoff
        )
        if rank < len(request.slings):
            raise ArithmeticError(
                "sling tensions are not uniquely determined; stiffness/pretension sharing is not inferred"
            )
        condition = float(singular[0] / singular[-1])
        if not math.isfinite(condition) or condition > request.maximum_condition_number:
            raise ArithmeticError("the scaled equilibrium matrix exceeds the declared condition limit")
        tensions = [float(v) * load_scale for v in scaled]
        if any(not math.isfinite(v) or v < 0 for v in tensions):
            raise ArithmeticError(
                "a tensile-only sling cannot supply the reconstructed compressive/nonfinite force"
            )
        if any(v != 0 and t == 0 for v, t in zip(scaled, tensions, strict=True)):
            raise ArithmeticError("a nonzero sling tension underflowed")
        sling_forces = [tuple(t * v for v in u) for t, u in zip(tensions, directions, strict=True)]
        net_force = _sum_vectors([force, *sling_forces])
        net_moment = _sum_vectors(
            [
                moment,
                *(
                    _cross(_subtract(s.attachment_m, request.moment_reference_m), f)
                    for s, f in zip(request.slings, sling_forces, strict=True)
                ),
            ]
        )
        if (
            math.hypot(*net_force) > request.force_residual_tolerance_n
            or math.hypot(*net_moment) > request.moment_residual_tolerance_nm
        ):
            raise ArithmeticError(
                "the supplied rigid pose and loads do not satisfy force and moment tolerances"
            )
        forces = {}
        for sling, vector in zip(request.slings, sling_forces, strict=True):
            forces[("sling", sling.id)] = vector
        for kind in ("attachment", "anchor"):
            names = {getattr(s, f"{kind}_id") for s in request.slings}
            for name in names:
                sign = 1 if kind == "attachment" else -1
                forces[(kind, name)] = tuple(
                    sign * v
                    for v in _sum_vectors(
                        [
                            f
                            for s, f in zip(request.slings, sling_forces, strict=True)
                            if getattr(s, f"{kind}_id") == name
                        ]
                    )
                )
        checks = _capacity_checks(request, forces)
        record.update(
            status="computed",
            solution={
                "sling_tensions_n": {s.id: t for s, t in zip(request.slings, tensions, strict=True)},
                "component_forces_n": [
                    {"target_kind": k, "target_id": n, "vector_n": list(v)}
                    for (k, n), v in sorted(forces.items())
                ],
                "external_force_n": list(force),
                "external_moment_nm": list(moment),
                "net_force_n": list(net_force),
                "net_moment_nm": list(net_moment),
                "rank": int(rank),
                "condition_number": condition,
                "scaled_equilibrium_matrix": matrix.tolist(),
                "scaled_singular_values": singular.tolist(),
            },
            capacity_checks=checks,
        )
        if any(c["status"] == "out_of_scope" for c in checks):
            record.update(
                status="out_of_scope",
                acceptance="not_applicable",
                issues=["a supplied capacity direction is outside its reviewed domain"],
            )
        elif any(c["within_limit"] is False for c in checks):
            record["acceptance"] = "exceeds_supplied_limits"
            if any(c["status"] == "needs_data" for c in checks):
                record.update(
                    status="needs_data",
                    issues=["a known limit is exceeded and other capacity checks remain unknown"],
                )
        elif any(c["status"] == "needs_data" for c in checks):
            record.update(
                status="needs_data",
                acceptance="needs_data",
                issues=["nominal forces are known but reviewed component capacities are incomplete"],
            )
        else:
            record["acceptance"] = "within_supplied_limits"
        digest(record)
    except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
        record.update(
            status="out_of_scope",
            issues=[str(exc)],
            solution=None,
            capacity_checks=[],
            acceptance="not_evaluated",
        )
    return _seal(record)


def verify_lifting_record(
    record, *, request: LiftingRequest | None = None, context: RunContext | None = None
):
    """Replay current statics/capacity semantics; a hash alone is not authority."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("lifting record digest is invalid")
    stored_request = LiftingRequest.model_validate_json(json.dumps(record["request"]))
    stored_context = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != stored_request.model_dump(mode="json"):
        raise ValueError("lifting record differs from caller request")
    if context is not None and context.model_dump(mode="json") != stored_context.model_dump(mode="json"):
        raise ValueError("lifting record differs from caller context")
    expected = evaluate_lifting(stored_request, stored_context)
    if record != expected:
        raise ValueError("lifting record differs from current static equilibrium and capacity replay")
    return True


def to_signal_frame(record, context: RunContext):
    """Export declared-pose tensions; these are computed loads, not observations."""
    verify_lifting_record(record, context=context)
    if record["status"] != "computed" or record["solution"] is None:
        raise ValueError("only a complete static screening record can be exported")
    values = {f"sling:{key}:tension": v for key, v in record["solution"]["sling_tensions_n"].items()}
    return SignalFrame(
        stream_id=f"{context.asset_id}:{context.scenario_id}:{record['request']['id']}:lifting",
        clock_id=context.clock_id,
        time_s=record["request"]["time_s"],
        values=values,
        units={k: "N" for k in values},
        valid={k: True for k in values},
        origin="synthetic" if context.origin == "synthetic" else "simulation",
        operating_mode="declared_pose_static_force_screening",
    )
