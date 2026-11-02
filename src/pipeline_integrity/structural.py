"""Traceable SI frame requests and independent static balance around the optional PyNite solver."""

import contextlib
import inspect
import io
import json
import math
import warnings
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .units import convert_quantity

Translation = Literal["DX", "DY", "DZ"]
Rotation = Literal["RX", "RY", "RZ"]
DOF = Literal["DX", "DY", "DZ", "RX", "RY", "RZ"]
LoadDirection = Literal["FX", "FY", "FZ", "MX", "MY", "MZ"]
Vector = tuple[float, float, float]
DOFS = ("DX", "DY", "DZ", "RX", "RY", "RZ")
FORCES = ("FX", "FY", "FZ", "MX", "MY", "MZ")


class FrameNode(Contract):
    id: Name
    position_m: Vector


class FrameSection(Contract):
    id: Name
    area_m2: Positive
    iy_m4: Positive
    iz_m4: Positive
    torsion_constant_m4: Positive
    evidence_id: Name


def annular_frame_section(id: str, outer_diameter_m: float, inner_diameter_m: float, evidence_id: str):
    """Derive elastic section properties of the declared concentric circular annulus."""

    class Annulus(Contract):
        outer: Positive
        inner: Nonnegative

        @model_validator(mode="after")
        def ordered(self):
            if self.inner >= self.outer:
                raise ValueError("a circular annulus needs inner diameter smaller than outer diameter")
            return self

    dimensions = Annulus(outer=outer_diameter_m, inner=inner_diameter_m)
    area = math.pi * (dimensions.outer**2 - dimensions.inner**2) / 4
    inertia = math.pi * (dimensions.outer**4 - dimensions.inner**4) / 64
    return FrameSection(
        id=id,
        area_m2=area,
        iy_m4=inertia,
        iz_m4=inertia,
        torsion_constant_m4=2 * inertia,
        evidence_id=evidence_id,
    )


class FrameMaterial(Contract):
    id: Name
    young_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1, lt=0.5)
    evidence_id: Name


class FrameMember(Contract):
    id: Name
    i_node: Name
    j_node: Name
    section_id: Name
    material_id: Name
    rotation_deg: float = 0.0
    rotation_releases: tuple[Literal["Ryi", "Rzi", "Ryj", "Rzj"], ...] = ()
    sample_fractions: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

    @model_validator(mode="after")
    def sampling(self):
        if (
            self.i_node == self.j_node
            or len(set(self.rotation_releases)) != len(self.rotation_releases)
            or len(self.sample_fractions) < 2
            or len(self.sample_fractions) > 1001
            or self.sample_fractions[0] != 0
            or self.sample_fractions[-1] != 1
            or any(b <= a for a, b in zip(self.sample_fractions, self.sample_fractions[1:], strict=False))
        ):
            raise ValueError(
                "distinct member ends, unique releases and increasing complete span samples required"
            )
        return self


class FrameSupport(Contract):
    node_id: Name
    fixed_dofs: tuple[DOF, ...] = ()
    translation_springs_n_m: dict[Translation, Positive] = Field(default_factory=dict)
    rotation_springs_nm_rad: dict[Rotation, Positive] = Field(default_factory=dict)
    imposed_translations_m: dict[Translation, float] = Field(default_factory=dict)
    imposed_rotations_rad: dict[Rotation, float] = Field(default_factory=dict)
    evidence_id: Name
    one_way_translation_directions: dict[Translation, Literal["positive_displacement", "negative_displacement"]] = Field(default_factory=dict)
    zero_gap_one_way_support_applicability_reviewed: bool = False

    @model_validator(mode="after")
    def compatible(self):
        springs = set(self.translation_springs_n_m) | set(self.rotation_springs_nm_rad)
        imposed = set(self.imposed_translations_m) | set(self.imposed_rotations_rad)
        if (
            len(set(self.fixed_dofs)) != len(self.fixed_dofs)
            or set(self.fixed_dofs) & springs
            or imposed - set(self.fixed_dofs)
        ):
            raise ValueError("spring and fixed DOFs are exclusive; imposed movements require fixed DOFs")
        if not set(self.one_way_translation_directions)<=set(self.translation_springs_n_m):
            raise ValueError("one-way support directions require an explicitly supplied translational spring stiffness")
        return self


class FrameNodeLoad(Contract):
    node_id: Name
    direction: LoadDirection
    value: float | None
    case_id: Name
    evidence_id: Name


class FrameLineLoad(Contract):
    member_id: Name
    global_direction: Literal["FX", "FY", "FZ"]
    start_n_m: float
    end_n_m: float
    start_fraction: float = Field(default=0.0, ge=0, lt=1)
    end_fraction: float = Field(default=1.0, gt=0, le=1)
    case_id: Name
    evidence_id: Name

    @model_validator(mode="after")
    def ordered(self):
        if self.end_fraction <= self.start_fraction:
            raise ValueError("line loads occupy an ordered nonempty member interval")
        return self


class FrameInitialStrain(Contract):
    id: Name
    member_id: Name
    case_id: Name
    value: float | None
    kind: Literal["thermal", "pressure_poisson", "declared_uniform_initial"]
    evidence_id: Name
    uniform_axial_applicability_reviewed: bool = False


class FrameInitialCurvature(Contract):
    """Uniform stress-free local rotation gradients of an unreleased member.

    Positive y/z curvature is d(rotation_y/z)/dx in the member's right-handed
    axes. The engineering beam strain convention is epsilon_x=epsilon_0
    + z*kappa_y - y*kappa_z.
    """
    id: Name
    member_id: Name
    case_id: Name
    curvature_y_per_m: float | None
    curvature_z_per_m: float | None
    kind: Literal["thermal", "declared_uniform_initial"] = "thermal"
    evidence_id: Name
    uniform_curvature_applicability_reviewed: bool = False


class FrameCombination(Contract):
    id: Name
    factors: dict[Name, float] = Field(min_length=1)
    evidence_id: Name


class FrameRequest(Contract):
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    coordinate_basis: Literal["declared_cartesian_metres"] = "declared_cartesian_metres"
    nodes: tuple[FrameNode, ...] = Field(min_length=2, max_length=2000)
    members: tuple[FrameMember, ...] = Field(min_length=1, max_length=2000)
    sections: tuple[FrameSection, ...] = Field(min_length=1)
    materials: tuple[FrameMaterial, ...] = Field(min_length=1)
    supports: tuple[FrameSupport, ...]
    load_cases: tuple[Name, ...] = Field(min_length=1)
    node_loads: tuple[FrameNodeLoad, ...] = ()
    line_loads: tuple[FrameLineLoad, ...] = ()
    initial_strains: tuple[FrameInitialStrain, ...] = ()
    initial_curvatures: tuple[FrameInitialCurvature, ...] = ()
    combinations: tuple[FrameCombination, ...] = Field(min_length=1, max_length=100)
    geometry_evidence_id: Name
    geometry_and_connectivity_reviewed: bool = False
    stiffness_and_loads_reviewed: bool = False
    supports_reviewed: bool = False
    linear_small_displacement_applicability_reviewed: bool = False
    absolute_force_tolerance_n: Positive = 1e-6
    absolute_moment_tolerance_nm: Positive = 1e-6
    relative_balance_tolerance: Positive = 1e-8
    maximum_translation_to_member_length: Positive = 0.05
    maximum_node_rotation_rad: Positive = 0.05
    maximum_axial_strain_magnitude: Positive = 0.01
    maximum_initial_curvature_rotation_rad: Positive = 0.05
    support_active_set_max_iterations: int = Field(default=100,ge=1,le=10000)
    support_switch_displacement_tolerance_m: Nonnegative = 0.0
    moment_reference_point_m: Vector = (0.0, 0.0, 0.0)

    @model_validator(mode="after")
    def topology(self):
        for values in (self.nodes, self.members, self.sections, self.materials, self.combinations):
            if len({item.id for item in values}) != len(values):
                raise ValueError("IDs within each frame collection must be unique")
        if len(set(self.load_cases)) != len(self.load_cases):
            raise ValueError("load case IDs must be unique")
        nodes = {item.id: np.array(item.position_m) for item in self.nodes}
        if len({item.position_m for item in self.nodes}) != len(nodes):
            raise ValueError("coincident nodes must have an explicit common node identity")
        sections, materials = {s.id for s in self.sections}, {m.id for m in self.materials}
        used = set()
        for member in self.members:
            if {member.i_node, member.j_node} - nodes.keys():
                raise ValueError("member endpoints must exist")
            if member.section_id not in sections or member.material_id not in materials:
                raise ValueError("member section and material must exist")
            used.update((member.i_node, member.j_node))
            start = nodes[member.i_node]
            delta = nodes[member.j_node] - start
            length = float(np.linalg.norm(delta))
            if length == 0:
                raise ValueError("zero-length frame members are unsupported")
            for node_id, point in nodes.items():
                fraction = float(np.dot(point - start, delta) / length**2)
                if (
                    node_id not in (member.i_node, member.j_node)
                    and 0 < fraction < 1
                    and np.linalg.norm(point - start - fraction * delta) <= 1e-10 * (1 + length)
                ):
                    raise ValueError("split members explicitly at interior nodes before frame analysis")
        if used != nodes.keys():
            raise ValueError("all frame nodes must belong to declared members")
        if len({s.node_id for s in self.supports}) != len(self.supports):
            raise ValueError("combine support declarations per node")
        member_ids = {m.id for m in self.members}
        if len({s.id for s in self.initial_strains}) != len(self.initial_strains):
            raise ValueError("initial-strain term IDs must be unique")
        if any(
            s.member_id not in member_ids or s.case_id not in self.load_cases for s in self.initial_strains
        ):
            raise ValueError("initial strains must reference declared members and load cases")
        if len({s.id for s in self.initial_curvatures}) != len(self.initial_curvatures):
            raise ValueError("initial-curvature term IDs must be unique")
        for curvature in self.initial_curvatures:
            if curvature.member_id not in member_ids or curvature.case_id not in self.load_cases:
                raise ValueError("initial curvatures must reference declared members and load cases")
            member = next(m for m in self.members if m.id == curvature.member_id)
            if member.rotation_releases:
                raise ValueError("initial curvature currently requires unreleased member rotations")
        if any(s.node_id not in nodes for s in self.supports):
            raise ValueError("support nodes must exist")
        if any(load.node_id not in nodes for load in self.node_loads):
            raise ValueError("load nodes must exist")
        if any(load.member_id not in member_ids for load in self.line_loads):
            raise ValueError("loaded members must exist")
        if any(load.case_id not in self.load_cases for load in (*self.node_loads, *self.line_loads)):
            raise ValueError("every load must reference a declared case")
        if any(set(combo.factors) - set(self.load_cases) for combo in self.combinations):
            raise ValueError("load combinations must reference declared cases")
        return self


def _record(request, context, status, issues=(), *, results=None, backend=None):
    body = dict(
        schema="pipeline_integrity.frame.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=status,
        issues=list(issues),
        combinations={} if results is None else results,
        backend=backend,
        implementation_sha256=file_digest(Path(__file__)),
        output_origin="simulation",
        analysis=("first_order_elastic_with_zero_gap_unilateral_support_active_set"
                  if any(s.one_way_translation_directions for s in request.supports)
                  else "first_order_linear_elastic_without_transverse_shear_deformation"),
        thermal_eigenstrain_or_pressure_thrust_automatically_applied=False,
        declared_uniform_initial_strains_supported=True,
        declared_uniform_initial_curvatures_supported=True,
        initial_curvature_axes="local_d_rotation_y_z_dx",
        initial_strain_equivalent_forces_excluded_from_external_balance=True,
        initial_curvature_equivalent_moments_excluded_from_external_balance=True,
        zero_gap_unilateral_translation_supports_supported=True,
        finite_gap_friction_or_plastic_supports_solved=False,
        self_weight_automatically_applied=False,
        plasticity_or_geometric_nonlinearity_solved=False,
        elbow_flexibility_stress_intensification_ovalization_or_buckling_qualified=False,
        code_or_field_acceptance=False,
        failure_probability_calculated=False,
    )
    body["record_digest"] = digest(body)
    return body


def _balance(request, combination, reactions):
    """Integrate the declared external loads, independently of native stiffness/load vectors."""
    origin = np.array(request.moment_reference_point_m)
    points = {n.id: np.array(n.position_m) - origin for n in request.nodes}
    members = {m.id: m for m in request.members}
    force, moment = np.zeros(3), np.zeros(3)
    force_scale = moment_scale = 0.0

    def accumulate(f, m):
        nonlocal force, moment, force_scale, moment_scale
        force += f
        moment += m
        force_scale += float(np.linalg.norm(f))
        moment_scale += float(np.linalg.norm(m))

    for load in request.node_loads:
        vec = np.zeros(3)
        index = FORCES.index(load.direction)
        vec[index % 3] = load.value * combination.factors.get(load.case_id, 0.0)
        accumulate(
            vec if index < 3 else np.zeros(3), np.cross(points[load.node_id], vec) if index < 3 else vec
        )
    for load in request.line_loads:
        member = members[load.member_id]
        delta = points[member.j_node] - points[member.i_node]
        length = float(np.linalg.norm(delta))
        start, span = length * load.start_fraction, length * (load.end_fraction - load.start_fraction)
        factor = combination.factors.get(load.case_id, 0.0)
        resultant = factor * span * (load.start_n_m + load.end_n_m) / 2
        first_moment = start * resultant + factor * span**2 * (load.start_n_m + 2 * load.end_n_m) / 6
        unit = np.zeros(3)
        unit[FORCES.index(load.global_direction)] = 1.0
        m = np.cross(points[member.i_node], unit * resultant) + np.cross(delta / length, unit) * first_moment
        accumulate(unit * resultant, m)
    for node_id, values in reactions.items():
        f, m = np.array(values[:3]), np.array(values[3:])
        accumulate(f, m + np.cross(points[node_id], f))
    force_limit = request.absolute_force_tolerance_n + request.relative_balance_tolerance * force_scale
    moment_limit = request.absolute_moment_tolerance_nm + request.relative_balance_tolerance * moment_scale
    return dict(
        force_residual_n=force.tolist(),
        moment_residual_nm=moment.tolist(),
        force_limit_n=force_limit,
        moment_limit_nm=moment_limit,
        passed=bool(np.max(np.abs(force)) <= force_limit and np.max(np.abs(moment)) <= moment_limit),
        reference_point_m=list(request.moment_reference_point_m),
    )


def _physical_section_resultants(request, combination, spec, axes, left_actions, x, length):
    """Recover right-face resultants by equilibrium of the explicit left segment."""
    distributed = np.zeros(3)
    first_moment = np.zeros(3)
    for load in request.line_loads:
        if load.member_id != spec.id:
            continue
        factor = combination.factors.get(load.case_id, 0.0)
        start, end = length*load.start_fraction, min(x, length*load.end_fraction)
        if end <= start or factor == 0:
            continue
        full_span = length*(load.end_fraction-load.start_fraction)
        span = end-start
        slope = (load.end_n_m-load.start_n_m)/full_span
        resultant = load.start_n_m*span+slope*span**2/2
        moment = start*resultant+load.start_n_m*span**2/2+slope*span**3/3
        global_direction = np.zeros(3)
        global_direction[FORCES.index(load.global_direction)] = factor
        local_direction = axes @ global_direction
        distributed += local_direction*resultant
        first_moment += local_direction*moment
    force = -np.asarray(left_actions[:3])-distributed
    ex = np.array([1.0, 0.0, 0.0])
    moment = (-np.asarray(left_actions[3:6]) + np.cross(ex, x*np.asarray(left_actions[:3]))
              + np.cross(ex, x*distributed-first_moment))
    return force, moment


def _nodal_balance(request, combination, reactions, member_results):
    """Check recovered physical member-end actions at each node against physical point loads and reactions."""
    end_actions = {n.id: np.zeros(6) for n in request.nodes}
    point_actions = {n.id: np.zeros(6) for n in request.nodes}
    for member in request.members:
        actions = member_results[member.id]["initial_strain_adjusted_global_end_actions"]
        end_actions[member.i_node] += np.array(actions[:6])
        end_actions[member.j_node] += np.array(actions[6:])
    for load in request.node_loads:
        point_actions[load.node_id][FORCES.index(load.direction)] += load.value * combination.factors.get(
            load.case_id, 0.0
        )
    nodes, passed = {}, True
    for node in request.nodes:
        internal, external, reaction = (
            end_actions[node.id],
            point_actions[node.id],
            np.array(reactions[node.id]),
        )
        residual = internal - external - reaction
        limits = []
        for span, absolute in (
            (slice(0, 3), request.absolute_force_tolerance_n),
            (slice(3, 6), request.absolute_moment_tolerance_nm),
        ):
            scale = sum(float(np.linalg.norm(v[span])) for v in (internal, external, reaction))
            limits.append(absolute + request.relative_balance_tolerance * scale)
        okay = bool(np.max(np.abs(residual[:3])) <= limits[0] and np.max(np.abs(residual[3:])) <= limits[1])
        nodes[node.id] = dict(
            force_residual_n=residual[:3].tolist(),
            moment_residual_nm=residual[3:].tolist(),
            force_limit_n=limits[0],
            moment_limit_nm=limits[1],
            passed=okay,
        )
        passed = passed and okay
    return dict(
        nodes=nodes, passed=passed, equivalent_initial_strain_forces_treated_as_physical_external_loads=False
    )


def _frame_inputs(specification, context):
    request = FrameRequest.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if request.asset_id != context.asset_id or request.clock_id != context.clock_id:
        raise ValueError("frame and context asset or clock differ")
    evidence_ids = {e.id for e in context.evidence}
    needed = {request.geometry_evidence_id} | {
        item.evidence_id
        for item in (
            *request.sections,
            *request.materials,
            *request.supports,
            *request.node_loads,
            *request.line_loads,
            *request.initial_strains,
            *request.initial_curvatures,
            *request.combinations,
        )
    }
    if needed - evidence_ids or len(evidence_ids) != len(context.evidence):
        raise ValueError("frame geometry, stiffness, supports, loads or combination evidence is absent")
    return request, context


def _native_input_signature(model):
    """Snapshot declared native inputs; solver states and numbering are excluded."""
    return {
        "nodes": {
            n.name: {
                "position_m": [n.X, n.Y, n.Z],
                "loads": list(n.NodeLoads),
                "fixed": [getattr(n, "support_" + d) for d in DOFS],
                "springs": {d: list(getattr(n, "spring_" + d)[:2]) for d in DOFS},
                "imposed": {d: getattr(n, "Enforced" + d) for d in DOFS},
            }
            for n in model.nodes.values()
        },
        "members": {
            m.name: {
                "ends": [m.i_node.name, m.j_node.name],
                "material": m.material.name,
                "section": m.section.name,
                "rotation_deg": m.rotation,
                "releases": list(m.Releases),
                "point_loads": list(m.PtLoads),
                "line_loads": list(m.DistLoads),
                "tension_only": m.tension_only,
                "compression_only": m.comp_only,
            }
            for m in model.members.values()
        },
        "materials": {m.name: [m.E, m.G, m.nu, m.rho, m.fy] for m in model.materials.values()},
        "sections": {s.name: [s.A, s.Iy, s.Iz, s.J] for s in model.sections.values()},
        "combinations": {c.name: dict(c.factors) for c in model.load_combos.values()},
        "extra_entities": {key: sorted(getattr(model, key).keys()) for key in ("springs", "plates", "quads")},
    }


@dataclass(frozen=True)
class NativeFrameAssembly:
    """In-process model handle plus a serializable, versioned assembly receipt."""

    request: FrameRequest
    context: RunContext
    native_model: object | None
    record: dict

    def check_integrity(self):
        if self.record.get("record_digest") != digest(
            {k: v for k, v in self.record.items() if k != "record_digest"}
        ):
            raise ValueError("native frame assembly receipt changed")
        if self.record["request"] != self.request.model_dump(mode="json") or self.record[
            "context"
        ] != self.context.model_dump(mode="json"):
            raise ValueError("native frame assembly request/context changed")
        if self.record["implementation_sha256"] != file_digest(Path(__file__)):
            raise ValueError("native frame assembly owner source changed")
        if self.native_model is None or self.record["status"] != "ready":
            raise ValueError("native frame assembly is not ready")
        if digest(_native_input_signature(self.native_model)) != self.record["native_input_digest"]:
            raise ValueError("native frame model inputs changed after assembly")
        return True


def _frame_assembly(request, context, status, issues, *, model=None, backend=None):
    fixed = {s.node_id: set(s.fixed_dofs) for s in request.supports}
    signature = None if model is None else _native_input_signature(model)
    body = {
        "schema": "pipeline_integrity.frame_assembly.v1",
        "status": status,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "issues": list(issues),
        "implementation_sha256": file_digest(Path(__file__)),
        "native_backend": backend,
        "dof_order": [f"{n.id}:{d}" for n in request.nodes for d in DOFS],
        "fixed_dof_indices": [
            i
            for i, (n, d) in enumerate((n, d) for n in request.nodes for d in DOFS)
            if d in fixed.get(n.id, set())
        ],
        "dof_units": [unit for n in request.nodes for unit in ("m", "m", "m", "rad", "rad", "rad")],
        "conjugate_force_units": [
            unit for n in request.nodes for unit in ("N", "N", "N", "N*m", "N*m", "N*m")
        ],
        "supports": [s.model_dump(mode="json") for s in request.supports],
        "load_cases": list(request.load_cases),
        "native_load_cases": None if model is None else list(model.load_cases),
        "native_input_signature": signature,
        "native_input_digest": None if signature is None else digest(signature),
        "numbering": "declared node order; stiffness extraction separately checks actual native node IDs",
        "self_weight_automatically_applied": False,
        "constitutive_model": "shared first-order linear elastic frame",
    }
    # Canonicalize native tuple containers for replay and JSON transport.
    body = json.loads(json.dumps(body, allow_nan=False))
    body["record_digest"] = digest(body)
    return NativeFrameAssembly(request, context, model, body)


def audit_frame_equilibrium(specification, combination_id, reactions, context, *, member_results=None):
    """Versioned independent SI static/nodal audit; no native solver is invoked."""
    request, context = _frame_inputs(specification, context)
    combination = next((c for c in request.combinations if c.id == combination_id), None)
    if combination is None or set(reactions) != {n.id for n in request.nodes}:
        raise ValueError("audit requires an actual combination and every explicit node reaction")
    digest(reactions)
    if any(
        len(row) != 6 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in row)
        for row in reactions.values()
    ):
        raise ValueError("audit reactions must be six finite SI force/moment components per node")
    if any(load.value is None for load in request.node_loads):
        raise ValueError("physical external nodal loads are unresolved")
    if member_results is not None:
        if set(member_results) != {m.id for m in request.members}:
            raise ValueError("nodal audit requires every explicit member")
        digest(member_results)
        if any(
            len(m["initial_strain_adjusted_global_end_actions"]) != 12
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in m["initial_strain_adjusted_global_end_actions"]
            )
            for m in member_results.values()
        ):
            raise ValueError("each member needs twelve physical global end actions")
    static = _balance(request, combination, reactions)
    nodal = (
        None if member_results is None else _nodal_balance(request, combination, reactions, member_results)
    )
    body = {
        "schema": "pipeline_integrity.frame_equilibrium_audit.v1",
        "request_digest": digest(request),
        "context": context.model_dump(mode="json"),
        "combination_id": combination_id,
        "reaction_digest": digest(reactions),
        "member_results_digest": None if member_results is None else digest(member_results),
        "implementation_sha256": file_digest(Path(__file__)),
        "force_unit": "N",
        "moment_unit": "N*m",
        "static_balance": static,
        "nodal_balance": nodal,
        "passed": static["passed"] and (nodal is None or nodal["passed"]),
        "native_execution_or_physical_qualification_established": False,
    }
    body["record_digest"] = digest(body)
    return body


def build_native_frame(specification: FrameRequest, context: RunContext):
    """Build the versioned native model once; unresolved requests do not import PyNite."""
    request, context = _frame_inputs(specification, context)
    reviews = (
        request.geometry_and_connectivity_reviewed,
        request.stiffness_and_loads_reviewed,
        request.supports_reviewed,
        request.linear_small_displacement_applicability_reviewed,
    )
    if not all(reviews):
        return _frame_assembly(
            request, context, "needs_data", ("frame applicability reviews remain unresolved",)
        )
    if any(load.value is None for load in request.node_loads):
        return _frame_assembly(request, context, "needs_data", ("a nodal force or moment is unresolved",))
    if any(s.value is None or not s.uniform_axial_applicability_reviewed for s in request.initial_strains):
        return _frame_assembly(
            request,
            context,
            "needs_data",
            ("an initial strain or its uniform axial applicability is unresolved",),
        )
    if any(s.curvature_y_per_m is None or s.curvature_z_per_m is None
           or not s.uniform_curvature_applicability_reviewed for s in request.initial_curvatures):
        return _frame_assembly(request, context, "needs_data", ("uniform stress-free curvature or its applicability is unresolved",))
    if any(s.one_way_translation_directions and not s.zero_gap_one_way_support_applicability_reviewed for s in request.supports):
        return _frame_assembly(request, context, "needs_data", ("zero-gap unilateral support applicability is unresolved",))
    try:
        backend_version = version("PyNiteFEA")
    except PackageNotFoundError as error:
        raise ImportError(
            "install pipeline-integrity-toolkit[structural] for the PyNite frame solver"
        ) from error
    if backend_version != "3.2.0":
        return _frame_assembly(
            request, context, "out_of_scope", ("this adapter has reviewed PyNite 3.2.0 only",)
        )
    from Pynite import FEModel3D

    model = FEModel3D()
    for node in request.nodes:
        model.add_node(node.id, *node.position_m)
    for material in request.materials:
        e, nu = material.young_modulus_pa, material.poisson_ratio
        model.add_material(material.id, E=e, G=e / (2 * (1 + nu)), nu=nu, rho=0.0)
    for section in request.sections:
        model.add_section(
            section.id, A=section.area_m2, Iy=section.iy_m4, Iz=section.iz_m4, J=section.torsion_constant_m4
        )
    for member in request.members:
        model.add_member(
            member.id,
            member.i_node,
            member.j_node,
            member.material_id,
            member.section_id,
            rotation=member.rotation_deg,
        )
        if member.rotation_releases:
            model.def_releases(member.id, **{name: True for name in member.rotation_releases})
    for support in request.supports:
        model.def_support(support.node_id, **{f"support_{dof}": True for dof in support.fixed_dofs})
        for dof, stiffness in (support.translation_springs_n_m | support.rotation_springs_nm_rad).items():
            direction=support.one_way_translation_directions.get(dof)
            native_direction=None if direction is None else "+" if direction=="positive_displacement" else "-"
            model.def_support_spring(support.node_id, dof, stiffness, direction=native_direction)
        for dof, value in (support.imposed_translations_m | support.imposed_rotations_rad).items():
            model.def_node_disp(support.node_id, dof, value)
    for load in request.node_loads:
        model.add_node_load(load.node_id, load.direction, load.value, case=load.case_id)
    for load in request.line_loads:
        length = model.members[load.member_id].L()
        model.add_member_dist_load(
            load.member_id,
            load.global_direction,
            load.start_n_m,
            load.end_n_m,
            load.start_fraction * length,
            load.end_fraction * length,
            load.case_id,
        )
    for combo in request.combinations:
        model.add_load_combo(combo.id, dict(combo.factors))
    material_by_id = {m.id: m for m in request.materials}
    section_by_id = {s.id: s for s in request.sections}
    member_by_id = {m.id: m for m in request.members}
    for strain in request.initial_strains:
        spec = member_by_id[strain.member_id]
        member = model.members[strain.member_id]
        ea = material_by_id[spec.material_id].young_modulus_pa * section_by_id[spec.section_id].area_m2
        axis = np.asarray(member.T())[0, :3]
        for node_id, sign in ((spec.i_node, -1), (spec.j_node, 1)):
            for direction, component in zip(FORCES[:3], axis, strict=True):
                model.add_node_load(
                    node_id, direction, float(sign * ea * strain.value * component), case=strain.case_id
                )
    for curvature in request.initial_curvatures:
        spec = member_by_id[curvature.member_id]
        member = model.members[curvature.member_id]
        material = material_by_id[spec.material_id]
        section = section_by_id[spec.section_id]
        axes = np.asarray(member.T())[:3, :3]
        eigenmoment = (material.young_modulus_pa * section.iy_m4 * curvature.curvature_y_per_m * axes[1]
                       + material.young_modulus_pa * section.iz_m4 * curvature.curvature_z_per_m * axes[2])
        for node_id, sign in ((spec.i_node, -1), (spec.j_node, 1)):
            for direction, component in zip(FORCES[3:], eigenmoment, strict=True):
                model.add_node_load(node_id, direction, float(sign*component), case=curvature.case_id)
    package_root = Path(inspect.getfile(FEModel3D)).parent
    package_sources = {
        path.relative_to(package_root).as_posix(): file_digest(path)
        for path in sorted(package_root.rglob("*.py"))
    }
    backend = dict(
        distribution="PyNiteFEA",
        version=backend_version,
        license="MIT",
        sparse=True,
        source_tree_sha256=digest(package_sources),
        source_tree_files=len(package_sources),
        requires_first_order_support_active_set=any(s.one_way_translation_directions for s in request.supports),
    )
    return _frame_assembly(request, context, "ready", (), model=model, backend=backend)


def assemble_frame_stiffness(
    assembly: NativeFrameAssembly,
    combination_id: str,
    *,
    reference_record: dict,
    include_geometric: bool = True,
    maximum_full_dofs: int = 600,
    absolute_translation_tolerance_m: float = 1e-10,
    absolute_rotation_tolerance_rad: float = 1e-10,
):
    """Versioned native Ke/Kg extraction bound to an independently audited frame run."""
    assembly.check_integrity()
    request, context, model = assembly.request, assembly.context, assembly.native_model
    if any(s.one_way_translation_directions for s in request.supports):
        raise ValueError("unilateral support tangent stiffness requires a separate active-state contract; linear reanalysis is not permitted")
    if (
        not isinstance(maximum_full_dofs, int)
        or not 0 < maximum_full_dofs <= 12000
        or len(request.nodes) * 6 > maximum_full_dofs
    ):
        raise ValueError("native dense matrix extraction exceeds the explicit DOF budget")
    if any(
        not math.isfinite(v) or v <= 0
        for v in (absolute_translation_tolerance_m, absolute_rotation_tolerance_rad)
    ):
        raise ValueError("positive finite translation/rotation comparison tolerances required")
    if reference_record.get("record_digest") != digest(
        {k: v for k, v in reference_record.items() if k != "record_digest"}
    ):
        raise ValueError("reference frame receipt changed")
    if (
        reference_record.get("status") != "computed"
        or reference_record.get("request") != request.model_dump(mode="json")
        or reference_record.get("context") != context.model_dump(mode="json")
        or reference_record.get("implementation_sha256") != file_digest(Path(__file__))
        or reference_record.get("backend", {}).get("assembly_record") != assembly.record
    ):
        raise ValueError("stiffness assembly requires the matching current-owner audited reference frame")
    if combination_id not in {c.id for c in request.combinations}:
        raise ValueError("matrix reference combination is absent")
    if include_geometric and (
        request.initial_strains
        or request.initial_curvatures
        or request.line_loads
        or any(m.rotation_releases for m in request.members)
        or any(
            v != 0
            for s in request.supports
            for v in (*s.imposed_translations_m.values(), *s.imposed_rotations_rad.values())
        )
    ):
        raise ValueError(
            "reviewed geometric stiffness extraction needs unreleased nodal-load frames without initial strain/imposed settlement"
        )
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), warnings.catch_warnings(record=True) as native_warnings:
        warnings.simplefilter("always")
        model.analyze_linear(log=False, check_stability=True, check_statics=False, sparse=True)
    assembly.check_integrity()
    reference = reference_record["combinations"][combination_id]
    translation_delta = rotation_delta = 0.0
    for node in request.nodes:
        expected = reference["nodes"][node.id]["translation_m"] + reference["nodes"][node.id]["rotation_rad"]
        for dof, value in zip(DOFS, expected, strict=True):
            actual = float(getattr(model.nodes[node.id], dof)[combination_id])
            if not math.isfinite(actual):
                raise ValueError("native assembly reference displacement is nonfinite")
            delta = abs(actual - value)
            if dof.startswith("D"):
                translation_delta = max(translation_delta, delta)
            else:
                rotation_delta = max(rotation_delta, delta)
    if (
        translation_delta > absolute_translation_tolerance_m
        or rotation_delta > absolute_rotation_tolerance_rad
    ):
        raise ValueError("native assembly displacement differs from the audited reference frame")
    axial = []
    if include_geometric:
        for member_spec in request.members:
            physical = model.members[member_spec.id]
            if len(physical.sub_members) != 1:
                raise ValueError("native geometric stiffness segmentation differs from explicit topology")
            member = next(iter(physical.sub_members.values()))
            d = member.d(combination_id)
            tension = member.material.E * member.section.A / member.L() * (d[6, 0] - d[0, 0])
            for sample in reference["members"][member_spec.id]["samples"]:
                expected = sample["elastic_axial_force_tension_positive_n"]
                limit = request.absolute_force_tolerance_n + request.relative_balance_tolerance * max(
                    abs(tension), abs(expected)
                )
                if not math.isfinite(tension) or abs(tension - expected) > limit:
                    raise ValueError(
                        "native geometric stiffness axial force differs from audited physical axial force"
                    )
            axial.append(
                {
                    "member_id": member_spec.id,
                    "geometric_stiffness_axial_force_tension_positive_N": float(tension),
                }
            )
    native_nodes = sorted(model.nodes.values(), key=lambda n: n.ID)
    order = [f"{n.name}:{d}" for n in native_nodes for d in DOFS]
    fixed = [n.ID * 6 + i for n in native_nodes for i, d in enumerate(DOFS) if getattr(n, "support_" + d)]
    if order != assembly.record["dof_order"] or fixed != assembly.record["fixed_dof_indices"]:
        raise ValueError("actual native DOF numbering/supports differ from the declared assembly basis")
    ke = np.asarray(model.Ke(combination_id, log=False, check_stability=True, sparse=False), dtype=float)
    kg = (
        None
        if not include_geometric
        else np.asarray(model.Kg(combination_id, log=False, sparse=False, first_step=False), dtype=float)
    )
    if not np.isfinite(ke).all() or kg is not None and not np.isfinite(kg).all():
        raise ValueError("native assembled stiffness contains nonfinite entries")
    body = {
        "schema": "pipeline_integrity.frame_stiffness_assembly.v1",
        "assembly_record": assembly.record,
        "reference_record_digest": reference_record["record_digest"],
        "combination_id": combination_id,
        "implementation_sha256": file_digest(Path(__file__)),
        "dof_order": order,
        "fixed_dof_indices": fixed,
        "elastic_stiffness": ke.tolist(),
        "signed_geometric_stiffness": None if kg is None else kg.tolist(),
        "elastic_stiffness_sha256": digest(ke.tolist()),
        "signed_geometric_stiffness_sha256": None if kg is None else digest(kg.tolist()),
        "geometric_convention": None
        if kg is None
        else "native Ke+lambda*Kg; EA/L axial extension force is tension-positive",
        "geometric_first_step": None if kg is None else False,
        "prestress_audit": {
            "reference_state_matched": True,
            "maximum_shared_frame_translation_difference_m": translation_delta,
            "maximum_shared_frame_rotation_difference_rad": rotation_delta,
            "members": axial,
        },
        "warnings": [{"category": w.category.__name__, "message": str(w.message)} for w in native_warnings],
        "native_log": captured.getvalue().splitlines(),
        "force_unit": "N",
        "moment_unit": "N*m",
    }
    body["record_digest"] = digest(body)
    return body


def solve_frame(specification: FrameRequest, context: RunContext):
    """Solve reviewed explicit segments; retain native signs, alternate combinations and raw failures."""
    requested, requested_context = _frame_inputs(specification,context)
    one_way=any(s.one_way_translation_directions for s in requested.supports)
    if one_way and len(requested.combinations)>1:
        combined={}
        problems=[]
        backends={}
        receipts={}
        statuses=[]
        for combination in requested.combinations:
            payload=requested.model_dump(mode="python")
            payload["combinations"]=(combination,)
            single=solve_frame(FrameRequest.model_validate(payload),requested_context)
            statuses.append(single["status"])
            combined.update(single["combinations"])
            problems.extend(f"{combination.id}: {issue}" for issue in single["issues"])
            backends[combination.id]=single["backend"]
            receipts[combination.id]=single["record_digest"]
        status="computed" if all(s=="computed" for s in statuses) else "needs_data" if "needs_data" in statuses else "out_of_scope"
        return _record(requested,requested_context,status,tuple(problems),results=combined,
                       backend=dict(independent_native_active_sets_per_combination=True,
                                    combination_backends=backends,combination_record_digests=receipts))
    assembly = build_native_frame(requested, requested_context)
    request, context = assembly.request, assembly.context
    if assembly.record["status"] != "ready":
        return _record(request, context, assembly.record["status"], assembly.record["issues"])
    assembly.check_integrity()
    model = assembly.native_model
    backend = dict(assembly.record["native_backend"])
    backend["assembly_record"] = assembly.record
    backend["support_solution_method"] = "native_first_order_active_set" if one_way else "native_linear"
    material_by_id = {m.id: m for m in request.materials}
    section_by_id = {s.id: s for s in request.sections}
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), warnings.catch_warnings(record=True) as native_warnings:
            warnings.simplefilter("always")
            if one_way:
                model.analyze(log=False,check_stability=True,check_statics=False,sparse=True,
                              max_iter=request.support_active_set_max_iterations,
                              spring_tolerance=request.support_switch_displacement_tolerance_m,num_steps=1)
            else:
                model.analyze_linear(log=False, check_stability=True, check_statics=False, sparse=True)
        assembly.check_integrity()
        backend["warnings"] = [
            dict(category=w.category.__name__, message=str(w.message)) for w in native_warnings
        ]
    except Exception as error:
        if not any(word in str(error).lower() for word in ("singular", "unstable", "instability", "converg", "diverg")):
            raise
        backend["failure"] = dict(type=type(error).__name__, message=str(error))
        backend["diagnostic_output"] = captured.getvalue().splitlines()
        return _record(
            request, context, "out_of_scope", ("native solver reported instability or nonconvergence",), backend=backend
        )
    backend["diagnostic_output"] = captured.getvalue().splitlines()
    results, issues = {}, []
    minimum_length = min(member.L() for member in model.members.values())
    for combo in request.combinations:
        nodes, reactions, members = {}, {}, {}
        for node_id, node in model.nodes.items():
            displacement = [float(getattr(node, dof)[combo.id]) for dof in DOFS]
            reaction = [float(getattr(node, "Rxn" + force)[combo.id]) for force in FORCES]
            nodes[node_id] = dict(
                translation_m=displacement[:3],
                rotation_rad=displacement[3:],
                reaction_force_n=reaction[:3],
                reaction_moment_nm=reaction[3:],
            )
            reactions[node_id] = reaction
        support_states=[]
        for support in request.supports:
            node=model.nodes[support.node_id]
            for dof,direction in support.one_way_translation_directions.items():
                index=DOFS.index(dof)
                displacement=nodes[support.node_id]["translation_m"][index]
                active=bool(getattr(node,"spring_"+dof)[2])
                switch=request.support_switch_displacement_tolerance_m
                expected_active=displacement>=switch if direction=="positive_displacement" else displacement<=-switch
                stiffness=support.translation_springs_n_m[dof]
                predicted=-stiffness*displacement if active else 0.0
                actual=nodes[support.node_id]["reaction_force_n"][index]
                limit=request.absolute_force_tolerance_n+request.relative_balance_tolerance*max(abs(predicted),abs(actual))
                valid=active==expected_active and abs(actual-predicted)<=limit
                support_states.append(dict(node_id=support.node_id,dof=dof,direction=direction,
                    displacement_m=displacement,active=active,expected_active=expected_active,
                    reaction_force_n=actual,constitutive_reaction_force_n=predicted,
                    reaction_residual_n=actual-predicted,reaction_limit_n=limit,
                    activation_displacement_tolerance_m=switch,
                    stored_spring_energy_J=0.5*stiffness*displacement**2 if active else 0.0,
                    constitutive_check_passed=valid))
                if not valid:
                    issues.append(f"{combo.id}: unilateral support state or constitutive reaction differs at {support.node_id}/{dof}")
        max_sample_ratio = 0.0
        max_initial_strain = max_elastic_strain = max_total_strain = 0.0
        max_initial_curvature_rotation = 0.0
        for spec in request.members:
            member = model.members[spec.id]
            if len(member.sub_members) != 1:
                return _record(
                    request,
                    context,
                    "out_of_scope",
                    ("native segmentation differs from the explicit topology",),
                    backend=backend,
                )
            native_member = next(iter(member.sub_members.values()))
            ea = material_by_id[spec.material_id].young_modulus_pa * section_by_id[spec.section_id].area_m2
            initial_strain = sum(
                s.value * combo.factors.get(s.case_id, 0.0)
                for s in request.initial_strains
                if s.member_id == spec.id
            )
            max_initial_strain = max(max_initial_strain, abs(initial_strain))
            curvature_y = sum(s.curvature_y_per_m*combo.factors.get(s.case_id, 0.0)
                              for s in request.initial_curvatures if s.member_id == spec.id)
            curvature_z = sum(s.curvature_z_per_m*combo.factors.get(s.case_id, 0.0)
                              for s in request.initial_curvatures if s.member_id == spec.id)
            max_initial_curvature_rotation = max(max_initial_curvature_rotation,
                                                math.hypot(curvature_y, curvature_z)*member.L())
            eiy = material_by_id[spec.material_id].young_modulus_pa*section_by_id[spec.section_id].iy_m4
            eiz = material_by_id[spec.material_id].young_modulus_pa*section_by_id[spec.section_id].iz_m4
            corrected_local = np.asarray(native_member.f(combo.id)).ravel().copy()
            corrected_local[0] += ea*initial_strain
            corrected_local[6] -= ea*initial_strain
            corrected_local[4] += eiy*curvature_y
            corrected_local[10] -= eiy*curvature_y
            corrected_local[5] += eiz*curvature_z
            corrected_local[11] -= eiz*curvature_z
            axes = np.asarray(member.T())[:3, :3]
            if not np.allclose(axes @ axes.T, np.eye(3), atol=1e-12, rtol=0) or not math.isclose(
                float(np.linalg.det(axes)), 1.0, abs_tol=1e-12
            ):
                raise RuntimeError("native member axes are not a proper orthonormal basis")
            samples = []
            for fraction in spec.sample_fractions:
                x = float(fraction * member.L())
                d = [float(member.deflection(direction, x, combo.id)) for direction in ("dx", "dy", "dz")]
                max_sample_ratio = max(max_sample_ratio, float(np.linalg.norm(d)) / member.L())
                elastic_axial_force = -float(member.axial(x, combo.id)) - ea * initial_strain
                max_elastic_strain = max(max_elastic_strain, abs(elastic_axial_force / ea))
                max_total_strain = max(max_total_strain, abs(initial_strain + elastic_axial_force / ea))
                physical_force, physical_moment = _physical_section_resultants(
                    request, combo, spec, axes, corrected_local[:6], x, member.L())
                samples.append(
                    dict(
                        fraction=fraction,
                        x_m=x,
                        axial_n=float(member.axial(x, combo.id)),
                        elastic_axial_force_tension_positive_n=elastic_axial_force,
                        mean_elastic_axial_stress_pa=elastic_axial_force
                        / section_by_id[spec.section_id].area_m2,
                        shear_y_n=float(member.shear("Fy", x, combo.id)),
                        shear_z_n=float(member.shear("Fz", x, combo.id)),
                        torque_nm=float(member.torque(x, combo.id)),
                        moment_y_nm=float(member.moment("My", x, combo.id)),
                        moment_z_nm=float(member.moment("Mz", x, combo.id)),
                        physical_section_force_local_n=physical_force.tolist(),
                        physical_section_moment_local_nm=physical_moment.tolist(),
                        elastic_bending_curvature_y_per_m=float(physical_moment[1]/eiy),
                        elastic_bending_curvature_z_per_m=float(physical_moment[2]/eiz),
                        total_bending_curvature_y_per_m=float(physical_moment[1]/eiy+curvature_y),
                        total_bending_curvature_z_per_m=float(physical_moment[2]/eiz+curvature_z),
                        local_translation_m=d,
                    )
                )
            corrected_global = np.asarray(member.T()).T @ corrected_local
            members[spec.id] = dict(
                length_m=float(member.L()),
                global_to_local=axes.tolist(),
                native_local_end_actions=np.asarray(native_member.f(combo.id)).ravel().tolist(),
                native_global_end_actions=np.asarray(native_member.F(combo.id)).ravel().tolist(),
                initial_strain_adjusted_local_end_actions=corrected_local.tolist(),
                initial_strain_adjusted_global_end_actions=corrected_global.tolist(),
                uniform_stress_free_axial_strain=initial_strain,
                uniform_stress_free_curvature_y_per_m=curvature_y,
                uniform_stress_free_curvature_z_per_m=curvature_z,
                native_axial_n_is_compression_positive_and_before_initial_strain_correction=True,
                native_moment_fields_include_uncorrected_curvature_load_effects=bool(request.initial_curvatures),
                samples=samples,
            )
        flat = [number for n in nodes.values() for values in n.values() for number in values]
        flat.extend(number for m in members.values() for number in m["native_local_end_actions"])
        flat.extend(
            number for m in members.values() for number in m["initial_strain_adjusted_global_end_actions"]
        )
        flat.extend(
            number
            for m in members.values()
            for sample in m["samples"]
            for number in (
                sample["axial_n"],
                sample["elastic_axial_force_tension_positive_n"],
                sample["mean_elastic_axial_stress_pa"],
                sample["shear_y_n"],
                sample["shear_z_n"],
                sample["torque_nm"],
                sample["moment_y_nm"],
                sample["moment_z_nm"],
                *sample["physical_section_force_local_n"],
                *sample["physical_section_moment_local_nm"],
                sample["elastic_bending_curvature_y_per_m"],
                sample["elastic_bending_curvature_z_per_m"],
                sample["total_bending_curvature_y_per_m"],
                sample["total_bending_curvature_z_per_m"],
                *sample["local_translation_m"],
            )
        )
        if not all(math.isfinite(number) for number in flat):
            return _record(
                request,
                context,
                "out_of_scope",
                ("native solution contains nonfinite values",),
                backend=backend,
            )
        equilibrium_audit = audit_frame_equilibrium(
            request, combo.id, reactions, context, member_results=members
        )
        balance = equilibrium_audit["static_balance"]
        nodal_balance = equilibrium_audit["nodal_balance"]
        max_node_ratio = (
            max(float(np.linalg.norm(n["translation_m"])) for n in nodes.values()) / minimum_length
        )
        max_rotation = max(float(np.linalg.norm(n["rotation_rad"])) for n in nodes.values())
        gates = dict(
            maximum_node_translation_over_minimum_member_length=max_node_ratio,
            maximum_sampled_member_translation_over_length=max_sample_ratio,
            maximum_node_rotation_rad=max_rotation,
            maximum_stress_free_axial_strain=max_initial_strain,
            maximum_stress_free_curvature_rotation_rad=max_initial_curvature_rotation,
            maximum_mean_elastic_axial_strain=max_elastic_strain,
            maximum_mean_total_axial_strain=max_total_strain,
        )
        if not balance["passed"]:
            issues.append(f"{combo.id}: external force/moment balance failed")
        if not nodal_balance["passed"]:
            issues.append(f"{combo.id}: recovered member-end and physical nodal force balance failed")
        if (
            max(max_initial_strain, max_elastic_strain, max_total_strain)
            > request.maximum_axial_strain_magnitude
        ):
            issues.append(f"{combo.id}: declared axial strain limit exceeded")
        if max_initial_curvature_rotation > request.maximum_initial_curvature_rotation_rad:
            issues.append(f"{combo.id}: declared stress-free curvature rotation limit exceeded")
        if (
            max(max_node_ratio, max_sample_ratio) > request.maximum_translation_to_member_length
            or max_rotation > request.maximum_node_rotation_rad
        ):
            issues.append(f"{combo.id}: declared small-displacement limits exceeded")
        results[combo.id] = dict(
            nodes=nodes,
            members=members,
            static_balance=balance,
            nodal_balance=nodal_balance,
            equilibrium_audit_record=equilibrium_audit,
            applicability_checks=gates,
            unilateral_support_states=support_states,
        )
    return _record(
        request, context, "out_of_scope" if issues else "computed", issues, results=results, backend=backend
    )


def frame_signal_frame(record: dict, combination_id: str, context: RunContext):
    """Export one static hypothesis at its stated time; combinations are separate streams."""
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("frame result was changed after sealing")
    if record.get("schema") != "pipeline_integrity.frame.v1" or record.get("context") != context.model_dump(
        mode="json"
    ):
        raise ValueError("frame result and signal context differ")
    request = FrameRequest.model_validate_json(json.dumps(record["request"]))
    if request.asset_id != context.asset_id or request.clock_id != context.clock_id:
        raise ValueError("frame signal identities or clocks differ")
    if combination_id not in {combo.id for combo in request.combinations}:
        raise ValueError("combination is not declared in the frame request")
    nodes = record["combinations"].get(combination_id, {}).get("nodes", {})
    values, units = {}, {}
    for node in request.nodes:
        for key, labels, unit in (
            ("translation_m", ("dx", "dy", "dz"), "m"),
            ("rotation_rad", ("rx", "ry", "rz"), "rad"),
            ("reaction_force_n", ("fx", "fy", "fz"), "N"),
            ("reaction_moment_nm", ("mx", "my", "mz"), "N*m"),
        ):
            vector = nodes.get(node.id, {}).get(key, [None] * 3)
            for i, label in enumerate(labels):
                channel = f"{node.id}.{label}"
                values[channel], units[channel] = vector[i], unit
    return SignalFrame(
        stream_id=f"{context.asset_id}:frame:{combination_id}",
        clock_id=context.clock_id,
        time_s=request.time_s,
        values=values,
        units=units,
        valid={key: record["status"] == "computed" and value is not None for key, value in values.items()},
        origin="simulation",
        operating_mode=combination_id,
    )


class FrameSignalLoad(Contract):
    source_stream_id: Name
    source_channel: Name
    source_evidence_id: Name
    node_load_index: int = Field(ge=0)
    quasistatic_snapshot_hypothesis_reviewed: bool = False


def solve_frame_signal_snapshot(
    specification: FrameRequest, observation: SignalFrame, mapping: FrameSignalLoad, context: RunContext
):
    """Replace one declared nodal load from an observed quantity without changing sign or sample clock."""
    request = FrameRequest.model_validate(specification.model_dump())
    frame = SignalFrame.model_validate(observation.model_dump())
    mapping = FrameSignalLoad.model_validate(mapping.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if (
        request.asset_id != context.asset_id
        or request.clock_id != context.clock_id
        or frame.clock_id != context.clock_id
    ):
        raise ValueError("load observation, frame model and context identities or clocks differ")
    if mapping.source_stream_id != frame.stream_id or mapping.source_channel not in frame.values:
        raise ValueError("source load stream or channel differs")
    if mapping.source_evidence_id not in {e.id for e in context.evidence}:
        raise ValueError("source load evidence is absent")
    if mapping.node_load_index >= len(request.node_loads):
        raise ValueError("the observed load must replace an explicit nodal load slot")
    target = request.node_loads[mapping.node_load_index]
    unit = "N" if target.direction.startswith("F") else "N*m"
    value = frame.values[mapping.source_channel]
    converted = None if value is None else convert_quantity(value, frame.units[mapping.source_channel], unit)
    if not frame.valid[mapping.source_channel]:
        converted = None
    loads = list(request.node_loads)
    loads[mapping.node_load_index] = target.model_copy(
        update={"value": converted, "evidence_id": mapping.source_evidence_id}
    )
    snapshot = request.model_copy(update={"time_s": frame.time_s, "node_loads": tuple(loads)})
    if mapping.quasistatic_snapshot_hypothesis_reviewed:
        body = solve_frame(snapshot, context)
    else:
        body = _record(
            snapshot, context, "needs_data", ("quasistatic snapshot load-path hypothesis is unreviewed",)
        )
    body.pop("record_digest")
    body["source_signal_binding"] = dict(
        mapping=mapping.model_dump(mode="json"),
        observation=frame.model_dump(mode="json"),
        observation_digest=digest(frame),
        template_digest=digest(request),
        applied_load_value=converted,
        applied_load_unit=unit,
        source_value_sorted_resampled_clipped_or_absolute_valued=False,
        structural_fixture_or_dynamic_experiment_validated=False,
    )
    body["record_digest"] = digest(body)
    return body
