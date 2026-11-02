"""Prescribed nodal ground movement around the existing linear PyNite frame owner."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .structural import (
    FrameLineLoad,
    FrameNodeLoad,
    FrameRequest,
    FrameSupport,
    Translation,
    annular_frame_section,
    audit_frame_equilibrium,
    solve_frame,
)

_TRANSLATIONS = ("DX", "DY", "DZ")
_FORCES = ("FX", "FY", "FZ")


class SoilNodeSupport(Contract):
    node_id: Name
    stiffness_N_m: dict[Translation, Positive | None] = Field(min_length=1)
    ground_displacement_m: tuple[float, float, float] | None
    behavior: Literal["unreviewed", "bilateral", "compression_only", "gap"] = "unreviewed"
    stiffness_definition_id: Name
    evidence_id: Name
    reviewed: bool = False


class SoilPipeBodyState(Contract):
    member_id: Name
    outer_diameter_m: Positive
    inner_diameter_m: Nonnegative
    pipe_and_attached_mass_kg_m: Nonnegative | None
    contents_mass_kg_m: Nonnegative | None
    displaced_area_m2: Nonnegative | None
    immersed_fraction: Fraction | None
    external_density_kg_m3: Nonnegative | None
    evidence_id: Name
    reviewed: bool = False

    @model_validator(mode="after")
    def annular(self):
        if self.inner_diameter_m >= self.outer_diameter_m:
            raise ValueError("inner diameter must be smaller than the supplied outer diameter")
        return self


class SoilPipeRequest(Contract):
    id: Name
    frame_id: Name
    coordinate_definition_id: Name
    source_port_id: Name
    target_port_id: Name
    ground_event_id: Name
    frame: FrameRequest
    soil_supports: tuple[SoilNodeSupport, ...] = Field(min_length=1, max_length=2000)
    body_states: tuple[SoilPipeBodyState, ...] = Field(min_length=1, max_length=2000)
    gravity_m_s2: tuple[float, float, float]
    bilateral_discrete_soil_model_reviewed: bool = False
    geometry_material_and_buoyancy_basis_reviewed: bool = False
    evidence_id: Name

    @model_validator(mode="after")
    def topology(self):
        f = self.frame
        if self.source_port_id == self.target_port_id or math.hypot(*self.gravity_m_s2) == 0:
            raise ValueError("distinct ports and a nonzero declared gravity vector required")
        if f.node_loads or f.line_loads or f.initial_strains:
            raise ValueError(
                "supply an unloaded mechanical skeleton; pressure/thermal/general loads are not supported here"
            )
        if (
            len(f.load_cases) != 1
            or len(f.combinations) != 1
            or f.combinations[0].factors != {f.load_cases[0]: 1.0}
        ):
            raise ValueError("soil-pipe adapter needs exactly one unity-factor physical snapshot")
        if any(s.translation_springs_n_m or s.rotation_springs_nm_rad for s in f.supports):
            raise ValueError("declare all soil springs through soil_supports, not the anchor skeleton")
        if len({s.node_id for s in self.soil_supports}) != len(self.soil_supports):
            raise ValueError("combine soil axes into one declaration per node")
        if len({b.member_id for b in self.body_states}) != len(self.body_states) or {
            b.member_id for b in self.body_states
        } != {m.id for m in f.members}:
            raise ValueError("every explicit pipe member needs one body state")
        nodes = {n.id for n in f.nodes}
        anchor = {s.node_id: s for s in f.supports}
        for soil in self.soil_supports:
            if soil.node_id not in nodes:
                raise ValueError("soil spring references an unknown frame node")
            if soil.node_id in anchor and set(soil.stiffness_N_m) & set(anchor[soil.node_id].fixed_dofs):
                raise ValueError("a soil spring and fixed anchor cannot occupy the same DOF")
        sections = {s.id: s for s in f.sections}
        members = {m.id: m for m in f.members}
        for body in self.body_states:
            section = sections[members[body.member_id].section_id]
            expected = annular_frame_section(
                section.id, body.outer_diameter_m, body.inner_diameter_m, section.evidence_id
            )
            if any(
                not math.isclose(getattr(section, field), getattr(expected, field), rel_tol=1e-10, abs_tol=0)
                for field in ("area_m2", "iy_m4", "iz_m4", "torsion_constant_m4")
            ):
                raise ValueError("frame section differs from the supplied circular pipe annulus")
        return self


def soil_pipe_boundary_metadata(q):
    return {
        "id": q.id,
        "asset_id": q.frame.asset_id,
        "clock_id": q.frame.clock_id,
        "time_s": q.frame.time_s,
        "frame_id": q.frame_id,
        "coordinate_definition_id": q.coordinate_definition_id,
        "ports": [q.source_port_id, q.target_port_id],
        "ground_event_id": q.ground_event_id,
        "request_digest": digest(q.model_dump(mode="json")),
        "convention": "global Cartesian axes; ground and pipe displacement from the same undeformed datum",
    }


def _inputs(request, context):
    q = SoilPipeRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.frame.asset_id, q.frame.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "soil_pipe_boundary"
    ) != soil_pipe_boundary_metadata(q):
        raise ValueError("soil-pipe asset/clock/frame/ports/ground event differs from context")
    ids = [e.id for e in c.evidence]
    required = {q.evidence_id, q.frame.geometry_evidence_id}
    for rows in (
        q.soil_supports,
        q.body_states,
        q.frame.supports,
        q.frame.materials,
        q.frame.sections,
        q.frame.combinations,
    ):
        required.update(row.evidence_id for row in rows)
    if len(ids) != len(set(ids)) or required - set(ids):
        raise ValueError("soil, load, stiffness, anchor and geometry evidence must be present uniquely")
    return q, c


def _compile(q):
    f = q.frame
    supports = {s.node_id: s for s in f.supports}
    points, lines = [], []
    case = f.load_cases[0]
    for soil in q.soil_supports:
        original = supports.get(
            soil.node_id, FrameSupport(node_id=soil.node_id, evidence_id=soil.evidence_id)
        )
        supports[soil.node_id] = original.model_copy(update={"translation_springs_n_m": soil.stiffness_N_m})
        for dof, stiffness in soil.stiffness_N_m.items():
            index = _TRANSLATIONS.index(dof)
            points.append(
                FrameNodeLoad(
                    node_id=soil.node_id,
                    direction=_FORCES[index],
                    value=stiffness * soil.ground_displacement_m[index],
                    case_id=case,
                    evidence_id=soil.evidence_id,
                )
            )
    for state in q.body_states:
        mass = (
            state.pipe_and_attached_mass_kg_m
            + state.contents_mass_kg_m
            - state.immersed_fraction * state.external_density_kg_m3 * state.displaced_area_m2
        )
        for direction, gravity in zip(_FORCES, q.gravity_m_s2, strict=True):
            value = mass * gravity
            lines.append(
                FrameLineLoad(
                    member_id=state.member_id,
                    global_direction=direction,
                    start_n_m=value,
                    end_n_m=value,
                    case_id=case,
                    evidence_id=state.evidence_id,
                )
            )
    return FrameRequest.model_validate(
        f.model_copy(
            update={
                "supports": tuple(supports.values()),
                "node_loads": tuple(points),
                "line_loads": tuple(lines),
            }
        ).model_dump(mode="python")
    )


def evaluate_soil_pipe(request, context):
    """Compile a bilateral linear snapshot, then call and audit the existing owner."""
    q, c = _inputs(request, context)
    body = {
        "schema": "pipeline_integrity.soil_pipe.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "structural_owner_sha256": file_digest(Path(__file__).with_name("structural.py")),
        "runtime": {"python": platform.python_version(), "pydantic": version("pydantic")},
        "status": "needs_data",
        "compiled_frame": None,
        "structural_record": None,
        "solution": None,
        "body_load_ledger": None,
        "issues": [],
        "contact_separation_or_yield_solved": False,
        "geotechnical_or_pipe_strength_qualified": False,
        "verification_state": "execution is not regression or field qualification",
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    if any(s.behavior in {"compression_only", "gap"} for s in q.soil_supports):
        body.update(
            status="out_of_scope",
            issues=[
                "the shared linear frame adapter has bilateral springs only; separation/gap requires owner extension"
            ],
        )
        return seal()
    missing = []
    if not q.bilateral_discrete_soil_model_reviewed or not q.geometry_material_and_buoyancy_basis_reviewed:
        missing.append("reviewed bilateral soil and pipe/body-load model basis")
    for s in q.soil_supports:
        if (
            not s.reviewed
            or s.behavior != "bilateral"
            or s.ground_displacement_m is None
            or any(k is None for k in s.stiffness_N_m.values())
        ):
            missing.append("soil:" + s.node_id)
    for b in q.body_states:
        if not b.reviewed or any(
            getattr(b, field) is None
            for field in (
                "pipe_and_attached_mass_kg_m",
                "contents_mass_kg_m",
                "displaced_area_m2",
                "immersed_fraction",
                "external_density_kg_m3",
            )
        ):
            missing.append("body:" + b.member_id)
    if missing:
        body["issues"] = missing
        return seal()
    try:
        compiled = _compile(q)
        body["compiled_frame"] = compiled.model_dump(mode="json")
        body["body_load_ledger"] = []
        for state in q.body_states:
            buoyant_mass = state.immersed_fraction * state.external_density_kg_m3 * state.displaced_area_m2
            effective_mass = state.pipe_and_attached_mass_kg_m + state.contents_mass_kg_m - buoyant_mass
            body["body_load_ledger"].append(
                {
                    "member_id": state.member_id,
                    "pipe_and_attached_mass_kg_m": state.pipe_and_attached_mass_kg_m,
                    "contents_mass_kg_m": state.contents_mass_kg_m,
                    "displaced_fluid_mass_kg_m": buoyant_mass,
                    "effective_mass_kg_m": effective_mass,
                    "body_force_N_m": [effective_mass * g for g in q.gravity_m_s2],
                    "evidence_id": state.evidence_id,
                }
            )
        structural = solve_frame(compiled, c)
        body["structural_record"] = structural
        if structural["status"] != "computed":
            body.update(status=structural["status"], issues=structural["issues"])
            return seal()
        body["runtime"].update({name: version(name) for name in ("numpy", "scipy", "PyNiteFEA")})
        combo = compiled.combinations[0]
        result = structural["combinations"][combo.id]
        physical_reactions = {
            node_id: row["reaction_force_n"] + row["reaction_moment_nm"]
            for node_id, row in result["nodes"].items()
        }
        response = []
        spring_checks = []
        for soil in q.soil_supports:
            displacement = result["nodes"][soil.node_id]["translation_m"]
            axes = []
            for dof, stiffness in soil.stiffness_N_m.items():
                index = _TRANSLATIONS.index(dof)
                native = physical_reactions[soil.node_id][index]
                expected_native = -stiffness * displacement[index]
                offset = stiffness * soil.ground_displacement_m[index]
                force = -stiffness * (displacement[index] - soil.ground_displacement_m[index])
                limit = compiled.absolute_force_tolerance_n + compiled.relative_balance_tolerance * max(
                    abs(native), abs(expected_native), abs(force)
                )
                spring_checks.append(abs(native - expected_native) <= limit)
                physical_reactions[soil.node_id][index] = native + offset
                axes.append(
                    {
                        "axis": dof,
                        "stiffness_N_m": stiffness,
                        "pipe_displacement_m": displacement[index],
                        "ground_displacement_m": soil.ground_displacement_m[index],
                        "relative_displacement_m": displacement[index] - soil.ground_displacement_m[index],
                        "soil_force_on_pipe_N": force,
                        "pipe_force_on_soil_N": -force,
                        "native_zero_datum_spring_force_N": native,
                        "spring_law_residual_N": native - expected_native,
                    }
                )
            response.append({"node_id": soil.node_id, "axes": axes})
        # Reuse the owner's exact distributed-load and nodal moment audits after
        # removing fictitious k*u_ground forces and restoring physical reactions.
        physical = compiled.model_copy(update={"node_loads": ()})
        equilibrium = audit_frame_equilibrium(
            physical, combo.id, physical_reactions, c, member_results=result["members"]
        )
        balance = equilibrium["static_balance"]
        nodal = equilibrium["nodal_balance"]
        materials = {m.id: m for m in compiled.materials}
        sections = {s.id: s for s in compiled.sections}
        members = []
        for member in compiled.members:
            raw = result["members"][member.id]
            e = materials[member.material_id].young_modulus_pa
            section = sections[member.section_id]
            samples = [
                {
                    "fraction": s["fraction"],
                    "x_m": s["x_m"],
                    "moment_y_Nm": s["moment_y_nm"],
                    "moment_z_Nm": s["moment_z_nm"],
                    "moment_over_EIy_per_m": s["moment_y_nm"] / (e * section.iy_m4),
                    "moment_over_EIz_per_m": s["moment_z_nm"] / (e * section.iz_m4),
                }
                for s in raw["samples"]
            ]
            members.append(
                {
                    "member_id": member.id,
                    "global_to_local": raw["global_to_local"],
                    "samples": samples,
                    "sampled_bending_moment_peak_Nm": max(
                        math.hypot(s["moment_y_Nm"], s["moment_z_Nm"]) for s in samples
                    ),
                }
            )
        body["solution"] = {
            "soil_response": response,
            "members": members,
            "physical_reactions_by_node": physical_reactions,
            "physical_static_balance": balance,
            "physical_nodal_balance": nodal,
            "physical_equilibrium_audit_record": equilibrium,
            "spring_law_passed": all(spring_checks),
        }
        body["status"] = (
            "computed_diagnostic"
            if balance["passed"] and nodal["passed"] and all(spring_checks)
            else "out_of_scope"
        )
        if body["status"] == "out_of_scope":
            body["issues"] = ["physical soil-spring law or transformed force/moment balance failed"]
        digest(body)
    except (ArithmeticError, ValueError) as exc:
        body.update(status="out_of_scope", solution=None, issues=[str(exc)])
    return seal()


def verify_soil_pipe_record(record, *, request=None, context=None):
    """Replay current source/native physics; an unsigned seal does not authenticate history."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("soil-pipe record seal changed")
    q = SoilPipeRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and q != request or context is not None and c != context:
        raise ValueError("soil-pipe caller boundary differs")
    if evaluate_soil_pipe(q, c) != record:
        raise ValueError(
            "soil-pipe displacements, reactions, bending, balance or provenance differ on replay"
        )
    return True
