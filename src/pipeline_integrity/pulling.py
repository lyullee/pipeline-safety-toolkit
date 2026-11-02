"""Quasi-static flexible-line pulling on reviewed prescribed smooth guide paths."""

from __future__ import annotations

import inspect
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest

Vector = tuple[float, float, float]
_BASIS_TOLERANCE = 1e-12


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


class PullingSegment(Contract):
    id: Name
    start_m: Vector
    initial_tangent: Vector
    length_m: Positive
    bend_radius_m: Positive | None = None
    initial_curve_normal: Vector | None = None
    sliding_friction: Nonnegative | None
    immersed_fraction: Fraction | None
    external_fluid_density_kg_m3: Nonnegative | None
    axial_resistance_N_m: Nonnegative | None
    evidence_id: Name
    sample_intervals: int = Field(default=32, ge=2, le=4096)

    @model_validator(mode="after")
    def smooth_curve(self):
        if abs(math.hypot(*self.initial_tangent) - 1) > _BASIS_TOLERANCE:
            raise ValueError("the declared tangent must be unit length; no geometry repair is performed")
        if self.bend_radius_m is None:
            if self.initial_curve_normal is not None:
                raise ValueError("a straight segment has no unused curve normal")
        else:
            normal = self.initial_curve_normal
            if normal is None or abs(math.hypot(*normal) - 1) > _BASIS_TOLERANCE:
                raise ValueError("a circular segment needs an explicit unit principal normal")
            if abs(_dot(self.initial_tangent, normal)) > _BASIS_TOLERANCE:
                raise ValueError("the circular tangent and principal normal must be perpendicular")
            if self.length_m / self.bend_radius_m > math.pi:
                raise ValueError("each prescribed circular segment is limited to a half turn")
        return self


class PullingLimits(Contract):
    effective_axial_tension_limit_N: Positive | None = None
    minimum_bend_radius_m: Positive | None = None
    maximum_geometric_outer_fiber_strain: Positive | None = None
    applicability_id: Name
    evidence_id: Name
    reviewed: bool = False


class PullingRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    frame_id: Name
    coordinate_definition_id: Name
    trailing_port_id: Name
    puller_port_id: Name
    outer_diameter_m: Positive
    pipe_mass_kg_m: Nonnegative | None
    contents_mass_kg_m: Nonnegative | None
    displaced_area_m2: Nonnegative | None
    gravity_m_s2: Vector
    trailing_tension_N: Positive | None
    pull_speed_m_s: Positive | None = None
    power_budget_required: bool = True
    segments: tuple[PullingSegment, ...] = Field(min_length=1, max_length=256)
    limits: PullingLimits | None = None
    operating_case_id: Name
    geometry_evidence_id: Name
    loading_evidence_id: Name
    geometry_reviewed: bool = False
    flexible_line_model_reviewed: bool = False
    steady_sliding_and_guide_contact_reviewed: bool = False
    load_and_buoyancy_basis_reviewed: bool = False
    position_tolerance_m: Positive = 1e-6
    tangent_tolerance: Positive = Field(default=1e-10, le=1e-3)
    relative_solver_tolerance: Positive = Field(default=1e-8, le=1e-3)
    absolute_force_tolerance_N: Positive = 1e-7
    maximum_step_m: Positive
    numerical_tension_ceiling_N: Positive = 1e12
    maximum_rhs_evaluations: int = Field(default=100000, ge=32, le=2000000)

    @model_validator(mode="after")
    def identities(self):
        if self.trailing_port_id == self.puller_port_id:
            raise ValueError("trailing and puller ports must be distinct")
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("guide segment IDs must be unique")
        if math.hypot(*self.gravity_m_s2) == 0:
            raise ValueError("an explicit nonzero gravity vector is required")
        return self


def _geometry(segment, s):
    tangent = segment.initial_tangent
    if segment.bend_radius_m is None:
        return (
            tuple(x + s * u for x, u in zip(segment.start_m, tangent, strict=True)),
            tangent,
            (0.0, 0.0, 0.0),
        )
    radius, normal = segment.bend_radius_m, segment.initial_curve_normal
    angle = s / radius
    sine, cosine = math.sin(angle), math.cos(angle)
    return (
        tuple(
            x + radius * (sine * t + (1 - cosine) * n)
            for x, t, n in zip(segment.start_m, tangent, normal, strict=True)
        ),
        tuple(cosine * t + sine * n for t, n in zip(tangent, normal, strict=True)),
        tuple((-sine * t + cosine * n) / radius for t, n in zip(tangent, normal, strict=True)),
    )


def boundary_metadata(q: PullingRequest):
    return {
        "id": q.id,
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "time_s": q.time_s,
        "frame_id": q.frame_id,
        "coordinate_definition_id": q.coordinate_definition_id,
        "operating_case_id": q.operating_case_id,
        "ports": [q.trailing_port_id, q.puller_port_id],
        "segment_geometry_digest": digest(
            [
                s.model_dump(
                    mode="json",
                    exclude={
                        "sliding_friction",
                        "immersed_fraction",
                        "external_fluid_density_kg_m3",
                        "axial_resistance_N_m",
                    },
                )
                for s in q.segments
            ]
        ),
        "convention": "positive arc length and steady material motion from trailing port toward puller",
    }


def _inputs(request, context):
    q = PullingRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("pulling asset/clock differs from the actual context")
    if c.metadata.get("pulling_boundary") != boundary_metadata(q):
        raise ValueError("pulling frame/guide/port/operating-case context binding differs")
    ids = [e.id for e in c.evidence]
    required = {q.geometry_evidence_id, q.loading_evidence_id}
    required.update(s.evidence_id for s in q.segments)
    if q.limits:
        required.add(q.limits.evidence_id)
    if len(set(ids)) != len(ids) or required - set(ids):
        raise ValueError("all pulling source and capacity evidence must occur uniquely in context")
    for left, right in zip(q.segments, q.segments[1:], strict=False):
        end, tangent, _ = _geometry(left, left.length_m)
        if math.dist(end, right.start_m) > q.position_tolerance_m:
            raise ValueError("prescribed guide segments are not position-continuous")
        if math.dist(tangent, right.initial_tangent) > q.tangent_tolerance:
            raise ValueError("a zero-radius guide kink requires a separate contact model")
    return q, c


def _force_envelope(initial_upper, segment, weight):
    """A continuum differential-inequality upper bound, not an interpolated peak."""
    if initial_upper is None:
        return None
    mu = segment.sliding_friction
    if segment.bend_radius_m is None:
        tangent = segment.initial_tangent
        along = _dot(weight, tangent)
        normal = math.hypot(*(w - along * t for w, t in zip(weight, tangent, strict=True)))
        gradient = mu * normal + segment.axial_resistance_N_m - along
        value = initial_upper + max(0.0, gradient * segment.length_m)
        return value if math.isfinite(value) else None
    weight_magnitude = math.hypot(*weight)
    # The factor also covers the accepted input-basis norm tolerance. It is a
    # mathematical model enclosure, not a machine-certified interval solver.
    basis_bound = 1 + 4 * _BASIS_TOLERANCE
    a = 0.0 if segment.bend_radius_m is None else mu * basis_bound / segment.bend_radius_m
    b = (mu + 1) * weight_magnitude * basis_bound + segment.axial_resistance_N_m
    try:
        value = (
            initial_upper + b * segment.length_m
            if a == 0
            else initial_upper * math.exp(a * segment.length_m) + b * math.expm1(a * segment.length_m) / a
        )
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def evaluate_pulling(request: PullingRequest, context: RunContext):
    """Compute nominal steady sliding forces; never infer pipe EI/contact approval."""
    q, c = _inputs(request, context)
    body = {
        "schema": "pipeline_integrity.pulling.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "native_called": False,
        "runtime": {"python": platform.python_version()},
        "solution": None,
        "segment_prefix": [],
        "capacity_checks": None,
        "issues": [],
        "qualification": "prescribed-guide quasi-static flexible-line force diagnostic",
        "pipe_bending_rigidity_or_buckling_solved": False,
        "field_qualified": False,
        "verification_state": "runtime execution does not establish regression or field qualification",
        "backend": None,
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    reviews = (
        q.geometry_reviewed,
        q.flexible_line_model_reviewed,
        q.steady_sliding_and_guide_contact_reviewed,
        q.load_and_buoyancy_basis_reviewed,
    )
    missing = [
        k
        for k in ("pipe_mass_kg_m", "contents_mass_kg_m", "displaced_area_m2", "trailing_tension_N")
        if getattr(q, k) is None
    ]
    for s in q.segments:
        missing += [
            f"{s.id}:{key}"
            for key in (
                "sliding_friction",
                "immersed_fraction",
                "external_fluid_density_kg_m3",
                "axial_resistance_N_m",
            )
            if getattr(s, key) is None
        ]
    if not all(reviews) or missing:
        body["issues"] = [
            "reviewed geometry, flexible-line/steady-contact and complete load basis required",
            *missing,
        ]
        return seal()

    import numpy as np
    from scipy.integrate import solve_ivp
    from scipy.integrate._ivp import dop853_coefficients
    from scipy.integrate._ivp.rk import DOP853

    body["runtime"].update({name: version(name) for name in ("numpy", "scipy", "pydantic", "pydantic-core")})
    body["backend"] = {
        "id": "scipy.integrate.solve_ivp",
        "method": "DOP853",
        "source_sha256": file_digest(Path(inspect.getfile(solve_ivp))),
        "method_source_sha256": file_digest(Path(inspect.getfile(DOP853))),
        "coefficient_source_sha256": file_digest(Path(dop853_coefficients.__file__)),
    }
    evaluations = 0
    tension = q.trailing_tension_N
    global_upper = q.trailing_tension_N
    peak_sample = q.trailing_tension_N
    total_friction = total_drag = total_body = 0.0
    maximum_strain = 0.0
    minimum_radius = None
    try:
        for segment in q.segments:
            effective_mass = (
                q.pipe_mass_kg_m
                + q.contents_mass_kg_m
                - segment.immersed_fraction * segment.external_fluid_density_kg_m3 * q.displaced_area_m2
            )
            weight = tuple(effective_mass * v for v in q.gravity_m_s2)

            def terms(s, current_tension, segment=segment, weight=weight):
                _, tangent, curvature = _geometry(segment, s)
                tangent_weight = _dot(weight, tangent)
                normal_load = tuple(
                    current_tension * k + w - tangent_weight * t
                    for k, w, t in zip(curvature, weight, tangent, strict=True)
                )
                normal_magnitude = math.hypot(*normal_load)
                friction = segment.sliding_friction * normal_magnitude
                return normal_magnitude, friction, segment.axial_resistance_N_m, -tangent_weight

            def rhs(s, y, terms=terms):
                nonlocal evaluations
                evaluations += 1
                if evaluations > q.maximum_rhs_evaluations:
                    raise ArithmeticError("declared RHS work budget exceeded")
                if not math.isfinite(float(y[0])) or y[0] <= 0 or y[0] >= q.numerical_tension_ceiling_N:
                    raise ArithmeticError("taut tensile-only/numerical tension domain exceeded; no clipping")
                _, friction, drag, gravity = terms(s, y[0])
                result = [friction + drag + gravity, friction, drag, gravity]
                if not all(math.isfinite(v) for v in result):
                    raise ArithmeticError("distributed force terms are not representable")
                return result

            body["native_called"] = True
            start_tension = tension
            sol = solve_ivp(
                rhs,
                (0.0, segment.length_m),
                [tension, 0.0, 0.0, 0.0],
                method="DOP853",
                t_eval=np.linspace(0.0, segment.length_m, segment.sample_intervals + 1),
                rtol=q.relative_solver_tolerance,
                atol=q.absolute_force_tolerance_N,
                max_step=min(q.maximum_step_m, segment.length_m),
            )
            if (
                not sol.success
                or sol.t[-1] != segment.length_m
                or not np.isfinite(sol.y).all()
                or np.any(sol.y[0] <= 0)
            ):
                raise ArithmeticError(
                    "native integration did not reach the complete positive-tension guide segment"
                )
            tension, friction, drag, gravity = (float(v) for v in sol.y[:, -1])
            if friction < 0 or drag < 0:
                raise ArithmeticError("native friction/drag dissipation became negative; no clipping")
            endpoint, _, _ = _geometry(segment, segment.length_m)
            exact_body = -_dot(weight, tuple(b - a for a, b in zip(segment.start_m, endpoint, strict=True)))
            residual = tension - start_tension - friction - drag - gravity
            tolerance = q.absolute_force_tolerance_N + q.relative_solver_tolerance * max(
                abs(tension), abs(start_tension), abs(friction), abs(gravity), abs(exact_body)
            )
            if (
                abs(residual) > tolerance
                or abs(gravity - exact_body) > tolerance
                or abs(drag - segment.axial_resistance_N_m * segment.length_m) > tolerance
            ):
                raise ArithmeticError(
                    "force ledger or independent body-force endpoint integral does not close"
                )
            upper = _force_envelope(global_upper, segment, weight)
            sampled_peak = float(np.max(sol.y[0]))
            peak_sample = max(peak_sample, sampled_peak)
            if upper is not None and sampled_peak > upper + tolerance:
                raise ArithmeticError("sampled tension exceeds the conservative model force enclosure")
            global_upper = upper
            radius = segment.bend_radius_m
            strain = 0.0 if radius is None else q.outer_diameter_m / (2 * radius)
            maximum_strain = max(maximum_strain, strain)
            if radius is not None:
                minimum_radius = radius if minimum_radius is None else min(minimum_radius, radius)
            row = {
                "segment_id": segment.id,
                "length_m": segment.length_m,
                "effective_mass_kg_m": effective_mass,
                "body_force_N_m": list(weight),
                "input_tension_N": start_tension,
                "output_tension_N": tension,
                "integrated_friction_N": friction,
                "integrated_axial_resistance_N": drag,
                "integrated_opposed_body_force_N": gravity,
                "independent_opposed_body_force_N": exact_body,
                "force_residual_N": residual,
                "force_tolerance_N": tolerance,
                "sampled_tension_peak_N": sampled_peak,
                "whole_prefix_tension_upper_bound_N": upper,
                "geometric_outer_fiber_strain": strain,
                "native_nfev": sol.nfev,
                "samples": [
                    {
                        "local_s_m": float(s),
                        "tension_N": float(t),
                        "normal_guide_load_N_m": terms(float(s), float(t))[0],
                    }
                    for s, t in zip(sol.t, sol.y[0], strict=True)
                ],
            }
            body["segment_prefix"].append(row)
            total_friction += friction
            total_drag += drag
            total_body += gravity
        speed = q.pull_speed_m_s
        power = (
            None
            if speed is None
            else {
                "puller_input_W": tension * speed,
                "trailing_boundary_output_W": q.trailing_tension_N * speed,
                "friction_dissipation_W": total_friction * speed,
                "axial_resistance_dissipation_W": total_drag * speed,
                "effective_gravity_power_W": total_body * speed,
                "power_residual_W": (
                    tension - q.trailing_tension_N - total_friction - total_drag - total_body
                )
                * speed,
            }
        )
        checks = {
            "axial_tension": "needs_data",
            "bend_radius": "needs_data",
            "geometric_outer_fiber_strain": "needs_data",
        }
        limits = q.limits
        if limits and limits.reviewed:
            if limits.effective_axial_tension_limit_N is not None:
                limit = limits.effective_axial_tension_limit_N
                checks["axial_tension"] = (
                    "exceeds_supplied_limit"
                    if peak_sample > limit
                    else "within_model_upper_bound"
                    if global_upper is not None and global_upper <= limit
                    else "indeterminate_conservative_enclosure"
                )
            if limits.minimum_bend_radius_m is not None:
                checks["bend_radius"] = (
                    "within_supplied_limit"
                    if minimum_radius is None or minimum_radius >= limits.minimum_bend_radius_m
                    else "exceeds_supplied_limit"
                )
            if limits.maximum_geometric_outer_fiber_strain is not None:
                checks["geometric_outer_fiber_strain"] = (
                    "within_supplied_limit"
                    if maximum_strain <= limits.maximum_geometric_outer_fiber_strain
                    else "exceeds_supplied_limit"
                )
        body.update(
            status="computed_diagnostic",
            solution={
                "puller_tension_N": tension,
                "trailing_tension_N": q.trailing_tension_N,
                "sampled_tension_peak_N": peak_sample,
                "whole_path_tension_upper_bound_N": global_upper,
                "maximum_geometric_outer_fiber_strain": maximum_strain,
                "minimum_curve_radius_m": minimum_radius,
                "total_friction_N": total_friction,
                "total_axial_resistance_N": total_drag,
                "total_opposed_body_force_N": total_body,
                "power_budget": power,
                "force_residual_N": tension - q.trailing_tension_N - total_friction - total_drag - total_body,
                "rhs_evaluations": evaluations,
            },
            capacity_checks=checks,
        )
        if any(v == "needs_data" for v in checks.values()) or q.power_budget_required and power is None:
            body.update(
                status="needs_data",
                issues=[
                    "nominal forces retained; reviewed capacity comparisons or required speed/power data are incomplete"
                ],
            )
        elif any(v == "indeterminate_conservative_enclosure" for v in checks.values()):
            body["issues"] = [
                "sampled forces are below the limit but the continuum model upper bound does not prove a whole-path pass"
            ]
        digest(body)
    except (ArithmeticError, ValueError, RuntimeError) as exc:
        body.update(status="out_of_scope", solution=None, capacity_checks=None, issues=[str(exc)])
    return seal()


def verify_pulling_record(record, *, request=None, context=None):
    """Numerically replay current inputs; this is not historical execution authentication."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("pulling record seal changed")
    q = PullingRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and q != request or context is not None and c != context:
        raise ValueError("pulling caller boundary differs from the retained request/context")
    if evaluate_pulling(q, c) != record:
        raise ValueError("pulling native forces, guide, capacities or qualification differ on replay")
    return True
