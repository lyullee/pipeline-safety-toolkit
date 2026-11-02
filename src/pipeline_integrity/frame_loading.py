"""Prepared thermal/pressure frame contributions with immutable input bindings and owner receipts."""

import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .material_exchange import MaterialReceipt, PressureSection, evaluate_material_job, prepare_material_job
from .structural import (
    FORCES,
    FrameInitialStrain,
    FrameInitialCurvature,
    FrameNodeLoad,
    FrameRequest,
    _record,
    annular_frame_section,
    solve_frame,
)


class UniformFrameTemperature(Contract):
    id: Name
    member_id: Name
    material_id: Name
    case_id: Name
    temperature_k: Positive | None
    stress_free_temperature_k: Positive | None
    expansion_coefficient_per_k: Nonnegative | None
    coefficient_temperature_support_k: tuple[Positive, Positive] | None
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    uniform_temperature_and_constant_coefficient_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if (
            self.coefficient_temperature_support_k is not None
            and self.coefficient_temperature_support_k[0] > self.coefficient_temperature_support_k[1]
        ):
            raise ValueError("thermal expansion temperature support must be ordered")
        return self


class PressureFrameMapping(Contract):
    id: Name
    member_id: Name
    case_id: Name
    source_location_id: Name
    expected_pressure_receipt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    straight_uniform_cylinder_applicability_reviewed: bool = False
    end_condition_and_total_pressure_reviewed: bool = False
    poisson_coupling_reviewed: bool = False


class GradientFrameTemperature(Contract):
    """Temperature uniform along a member and linear over its local section."""
    id: Name
    member_id: Name
    material_id: Name
    case_id: Name
    centroid_temperature_K: Positive
    stress_free_temperature_K: Positive
    temperature_gradient_y_K_m: float
    temperature_gradient_z_K_m: float
    expansion_coefficient_per_K: Nonnegative
    coefficient_temperature_support_K: tuple[Positive, Positive]
    section_y_extent_m: tuple[float, float]
    section_z_extent_m: tuple[float, float]
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    linear_section_temperature_and_uniform_axial_field_reviewed: bool = False
    small_strain_constant_coefficient_reviewed: bool = False

    @model_validator(mode="after")
    def section_support(self):
        for lower, upper in (self.section_y_extent_m, self.section_z_extent_m):
            if not lower < upper or lower > 0 or upper < 0:
                raise ValueError("local section extents must increase and enclose the centroid")
        if self.coefficient_temperature_support_K[0] >= self.coefficient_temperature_support_K[1]:
            raise ValueError("thermal expansion coefficient support must increase")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("thermal gradient evidence IDs must be unique")
        return self


class FrameLoadContribution(Contract):
    schema_version: Literal["pipeline_integrity.frame_loading.v1"] = "pipeline_integrity.frame_loading.v1"
    id: Name
    kind: Literal["thermal", "pressure"]
    member_id: Name
    case_id: Name
    input_frame_digest: str
    context_digest: str
    preparation_status: Literal["ready", "needs_data", "out_of_scope"]
    issues: tuple[str, ...]
    specification: dict
    node_loads: tuple[FrameNodeLoad, ...] = ()
    initial_strains: tuple[FrameInitialStrain, ...] = ()
    initial_curvatures: tuple[FrameInitialCurvature, ...] = ()
    source_records: tuple[dict, ...] = ()
    implementation_sha256: str
    record_digest: str

    def check_integrity(self):
        FrameLoadContribution.model_validate(self.model_dump())
        if self.record_digest != digest(self.model_dump(mode="json", exclude={"record_digest"})):
            raise ValueError("prepared frame contribution changed after sealing")


def _prepared(frame, context, specification, kind, status, issues=(), *, nodes=(), strains=(), curvatures=(), sources=()):
    provisional = FrameLoadContribution(
        id=specification.id,
        kind=kind,
        member_id=specification.member_id,
        case_id=specification.case_id,
        input_frame_digest=digest(frame),
        context_digest=digest(context),
        preparation_status=status,
        issues=tuple(issues),
        specification=specification.model_dump(mode="json"),
        node_loads=tuple(nodes),
        initial_strains=tuple(strains),
        initial_curvatures=tuple(curvatures),
        source_records=tuple(sources),
        implementation_sha256=file_digest(Path(__file__)),
        record_digest="pending",
    )
    return provisional.model_copy(
        update={"record_digest": digest(provisional.model_dump(mode="json", exclude={"record_digest"}))}
    )


def _target(frame, specification, context):
    frame = FrameRequest.model_validate(frame.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if frame.asset_id != context.asset_id or frame.clock_id != context.clock_id:
        raise ValueError("frame-loading asset or clock differs from context")
    if set(specification.evidence_ids) - {e.id for e in context.evidence}:
        raise ValueError("frame-loading evidence is absent")
    members = {m.id: m for m in frame.members}
    if specification.member_id not in members or specification.case_id not in frame.load_cases:
        raise ValueError("frame loading must target an existing member and load case")
    if any(
        load.case_id == specification.case_id
        for load in (*frame.node_loads, *frame.line_loads, *frame.initial_strains, *frame.initial_curvatures)
    ):
        raise ValueError("prepared thermal/pressure loading requires an unused load case in the base frame")
    member = members[specification.member_id]
    material = next(m for m in frame.materials if m.id == member.material_id)
    return frame, context, member, material


def _reviewed(frame):
    return all(
        (
            frame.geometry_and_connectivity_reviewed,
            frame.stiffness_and_loads_reviewed,
            frame.supports_reviewed,
            frame.linear_small_displacement_applicability_reviewed,
        )
    )


def prepare_uniform_thermal_frame(
    frame: FrameRequest, specification: UniformFrameTemperature, context: RunContext, *, client=None
):
    """Ask the material owner for free thermal strain; the frame solver determines actual restraint."""
    s = UniformFrameTemperature.model_validate(specification.model_dump())
    frame, context, member, material = _target(frame, s, context)
    if s.material_id != member.material_id:
        raise ValueError("thermal expansion law and target frame material differ")
    if (
        not _reviewed(frame)
        or not s.uniform_temperature_and_constant_coefficient_reviewed
        or any(
            value is None
            for value in (
                s.temperature_k,
                s.stress_free_temperature_k,
                s.expansion_coefficient_per_k,
                s.coefficient_temperature_support_k,
            )
        )
    ):
        return _prepared(
            frame,
            context,
            s,
            "thermal",
            "needs_data",
            ("uniform thermal definition, coefficient, reference or frame review is unresolved",),
        )
    lower, upper = s.coefficient_temperature_support_k
    if not all(lower <= value <= upper for value in (s.temperature_k, s.stress_free_temperature_k)):
        return _prepared(
            frame,
            context,
            s,
            "thermal",
            "out_of_scope",
            ("thermal reference or current temperature lies outside the declared coefficient support",),
        )
    job = prepare_material_job(
        "mechanics.thermal_restraint",
        dict(
            young_modulus_Pa=material.young_modulus_pa,
            expansion_coefficient_per_K=s.expansion_coefficient_per_k,
            temperature_change_K=s.temperature_k - s.stress_free_temperature_k,
            restraint_fraction=0.0,
        ),
        context,
        location_id=s.member_id,
        evidence_ids=s.evidence_ids,
        client=client,
    )
    receipt = evaluate_material_job(job, context, client=client)
    source = receipt.model_dump(mode="json")
    if receipt.native.status != "computed":
        return _prepared(
            frame,
            context,
            s,
            "thermal",
            receipt.native.status,
            ("material owner did not compute thermal strain",),
            sources=(source,),
        )
    values = receipt.native.payload["values"]
    if values["restrained_uniaxial_stress_Pa"] != 0:
        raise ValueError("free thermal input must not include pre-applied restraint stress")
    strain = FrameInitialStrain(
        id=s.id,
        member_id=s.member_id,
        case_id=s.case_id,
        value=values["free_thermal_strain"],
        kind="thermal",
        evidence_id=s.evidence_ids[0],
        uniform_axial_applicability_reviewed=True,
    )
    return _prepared(frame, context, s, "thermal", "ready", strains=(strain,), sources=(source,))


def prepare_gradient_thermal_frame(frame: FrameRequest, specification: GradientFrameTemperature,
                                   context: RunContext, *, client=None):
    """Prepare mean expansion and two thermal bending curvatures without pre-restraint loads."""
    s = GradientFrameTemperature.model_validate(specification.model_dump(mode="python"))
    frame, context, member, material = _target(frame, s, context)
    if s.material_id != member.material_id:
        raise ValueError("thermal-gradient material differs from the frame member")
    if member.rotation_releases:
        return _prepared(frame, context, s, "thermal", "out_of_scope",
                         ("thermal curvature currently requires unreleased end rotations",))
    if not (s.linear_section_temperature_and_uniform_axial_field_reviewed
            and s.small_strain_constant_coefficient_reviewed and _reviewed(frame)):
        return _prepared(frame, context, s, "thermal", "needs_data",
                         ("linear section temperature, axial uniformity or frame applicability is unresolved",))
    temperatures = [s.stress_free_temperature_K]
    temperatures.extend(s.centroid_temperature_K+s.temperature_gradient_y_K_m*y+s.temperature_gradient_z_K_m*z
                        for y in s.section_y_extent_m for z in s.section_z_extent_m)
    low, high = s.coefficient_temperature_support_K
    if any(not math.isfinite(t) or not low <= t <= high for t in temperatures):
        return _prepared(frame, context, s, "thermal", "out_of_scope",
                         ("section or reference temperature exceeds the supplied coefficient support",))
    mean_spec = UniformFrameTemperature(
        id=s.id+":mean",member_id=s.member_id,material_id=s.material_id,case_id=s.case_id,
        temperature_k=s.centroid_temperature_K,stress_free_temperature_k=s.stress_free_temperature_K,
        expansion_coefficient_per_k=s.expansion_coefficient_per_K,
        coefficient_temperature_support_k=s.coefficient_temperature_support_K,
        evidence_ids=s.evidence_ids,uniform_temperature_and_constant_coefficient_reviewed=True)
    mean = prepare_uniform_thermal_frame(frame, mean_spec, context, client=client)
    if mean.preparation_status != "ready":
        return _prepared(frame, context, s, "thermal", mean.preparation_status, mean.issues,
                         sources=mean.source_records)
    curvature = FrameInitialCurvature(
        id=s.id+":curvature",member_id=s.member_id,case_id=s.case_id,
        curvature_y_per_m=s.expansion_coefficient_per_K*s.temperature_gradient_z_K_m,
        curvature_z_per_m=-s.expansion_coefficient_per_K*s.temperature_gradient_y_K_m,
        kind="thermal",evidence_id=s.evidence_ids[0],uniform_curvature_applicability_reviewed=True)
    return _prepared(frame, context, s, "thermal", "ready",strains=mean.initial_strains,
                     curvatures=(curvature,),sources=mean.source_records)


def prepare_pressure_frame(
    frame: FrameRequest,
    pressure_section_record: dict,
    specification: PressureFrameMapping,
    context: RunContext,
):
    """Map a reviewed straight-cylinder total-pressure field into cap tractions and axial Poisson strain."""
    s = PressureFrameMapping.model_validate(specification.model_dump())
    frame, context, member, material = _target(frame, s, context)
    record = json.loads(json.dumps(pressure_section_record, allow_nan=False))
    if (
        record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"})
        or record["record_digest"] != s.expected_pressure_receipt_digest
    ):
        raise ValueError("pressure section changed or differs from the explicitly bound receipt")
    if record.get("schema") != "pipeline_integrity.pressure_section.v1" or record.get(
        "context"
    ) != context.model_dump(mode="json"):
        raise ValueError("pressure section schema or context differs")
    section = PressureSection.model_validate_json(json.dumps(record["specification"]))
    if (section.asset_id, section.clock_id, section.time_s, section.location_id) != (
        frame.asset_id,
        frame.clock_id,
        frame.time_s,
        s.source_location_id,
    ):
        raise ValueError("pressure section asset, clock, exact sample time or source location differs")
    if {section.geometry_evidence_id, section.pressure_evidence_id} - {e.id for e in context.evidence}:
        raise ValueError("pressure section evidence is absent")
    if not _reviewed(frame) or not all(
        (
            s.straight_uniform_cylinder_applicability_reviewed,
            s.end_condition_and_total_pressure_reviewed,
            s.poisson_coupling_reviewed,
        )
    ):
        return _prepared(
            frame,
            context,
            s,
            "pressure",
            "needs_data",
            ("straight-cylinder pressure load path or Poisson applicability is unreviewed",),
            sources=(record,),
        )
    if record["status"] != "computed":
        status = "needs_data" if record["status"] == "needs_data" else "out_of_scope"
        return _prepared(
            frame,
            context,
            s,
            "pressure",
            status,
            ("source pressure section is not computed",),
            sources=(record,),
        )
    if (
        section.internal_pressure is None
        or section.external_pressure is None
        or record.get("total_stresses_include_absolute_pressure") is not True
    ):
        raise ValueError("computed pressure section needs both explicit absolute surface tractions")
    if any(
        sum(node in (m.i_node, m.j_node) for m in frame.members) != 1
        for node in (member.i_node, member.j_node)
    ):
        return _prepared(
            frame,
            context,
            s,
            "pressure",
            "out_of_scope",
            ("automatic pressure mapping is qualified only for an isolated straight span",),
            sources=(record,),
        )
    annulus = annular_frame_section(
        "source", 2 * section.outer_radius_m, 2 * section.inner_radius_m, section.geometry_evidence_id
    )
    target_section = next(p for p in frame.sections if p.id == member.section_id)
    if any(
        not math.isclose(getattr(annulus, key), getattr(target_section, key), rel_tol=1e-10, abs_tol=0)
        for key in ("area_m2", "iy_m4", "iz_m4", "torsion_constant_m4")
    ):
        raise ValueError("pressure annulus and frame section geometry differ")
    records = record["material_records"]
    if len(records) != len(section.evaluation_radii_m):
        raise ValueError("pressure source records do not cover the declared radial samples")
    pi, pe = section.internal_pressure.absolute_pa(), section.external_pressure.absolute_pa()
    values = []
    for raw, radius in zip(records, section.evaluation_radii_m, strict=True):
        receipt = MaterialReceipt.model_validate_json(json.dumps(raw))
        receipt.check_integrity()
        expected = dict(
            inner_radius_m=section.inner_radius_m,
            outer_radius_m=section.outer_radius_m,
            evaluation_radius_m=radius,
            internal_pressure_Pa=pi,
            external_pressure_Pa=pe,
            closed_ends=section.pressure_loaded_closed_ends,
        )
        if (
            receipt.job.model_id != "mechanics.lame_cylinder"
            or receipt.context != context
            or receipt.job.asset_id != frame.asset_id
            or receipt.job.clock_id != frame.clock_id
            or receipt.job.location_id != section.location_id
            or receipt.native.owner != "matintegrity"
            or receipt.native.asset_id != frame.asset_id
            or receipt.native.status != "computed"
            or receipt.native.payload.get("status") != "computed"
            or receipt.native.payload.get("units") != receipt.job.output_units
            or digest(receipt.job.parameters) != digest(expected)
            or digest(receipt.native.payload["inputs"]) != digest(expected)
        ):
            raise ValueError("nested material pressure inputs or identities differ")
        if any(
            receipt.job.output_units.get(key) != "Pa"
            for key in ("hoop_stress_Pa", "radial_stress_Pa", "axial_stress_Pa")
        ):
            raise ValueError("native pressure stress units differ")
        values.append(receipt.native.payload["values"])
    stress_sum = values[0]["hoop_stress_Pa"] + values[0]["radial_stress_Pa"]
    if any(
        not math.isclose(v["hoop_stress_Pa"] + v["radial_stress_Pa"], stress_sum, rel_tol=1e-10, abs_tol=1e-6)
        for v in values
    ):
        return _prepared(
            frame,
            context,
            s,
            "pressure",
            "out_of_scope",
            ("source radial plus hoop stress is not uniform",),
            sources=(record,),
        )
    cap_force = (
        (pi * math.pi * section.inner_radius_m**2 - pe * math.pi * section.outer_radius_m**2)
        if section.pressure_loaded_closed_ends
        else 0.0
    )
    if any(
        not math.isclose(
            v["axial_stress_Pa"] * target_section.area_m2, cap_force, rel_tol=1e-10, abs_tol=1e-6
        )
        for v in values
    ):
        return _prepared(
            frame,
            context,
            s,
            "pressure",
            "out_of_scope",
            ("native axial stress and declared cap traction do not agree",),
            sources=(record,),
        )
    points = {n.id: np.array(n.position_m) for n in frame.nodes}
    delta = points[member.j_node] - points[member.i_node]
    axis = delta / np.linalg.norm(delta)
    loads = tuple(
        FrameNodeLoad(
            node_id=node,
            direction=direction,
            value=float(sign * cap_force * component),
            case_id=s.case_id,
            evidence_id=s.evidence_ids[0],
        )
        for node, sign in ((member.i_node, -1), (member.j_node, 1))
        for direction, component in zip(FORCES[:3], axis, strict=True)
    )
    poisson_strain = -material.poisson_ratio * stress_sum / material.young_modulus_pa
    strain = FrameInitialStrain(
        id=s.id,
        member_id=s.member_id,
        case_id=s.case_id,
        value=poisson_strain,
        kind="pressure_poisson",
        evidence_id=s.evidence_ids[0],
        uniform_axial_applicability_reviewed=True,
    )
    return _prepared(
        frame, context, s, "pressure", "ready", nodes=loads, strains=(strain,), sources=(record,)
    )


def solve_loaded_frame(
    specification: FrameRequest, contributions: tuple[FrameLoadContribution, ...], context: RunContext
):
    """Compose independently prepared loads from this exact base frame; keep every upstream record."""
    frame = FrameRequest.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if frame.asset_id != context.asset_id or frame.clock_id != context.clock_id:
        raise ValueError("loaded-frame asset or clock differs")
    contributions = tuple(FrameLoadContribution.model_validate(c.model_dump()) for c in contributions)
    seen, ids = set(), set()
    for contribution in contributions:
        contribution.check_integrity()
        if contribution.input_frame_digest != digest(frame) or contribution.context_digest != digest(context):
            raise ValueError("frame or context changed after load preparation")
        key = (contribution.kind, contribution.member_id, contribution.case_id)
        if key in seen or contribution.id in ids:
            raise ValueError("a prepared load effect is duplicated")
        seen.add(key)
        ids.add(contribution.id)
    unresolved = [c for c in contributions if c.preparation_status != "ready"]
    if unresolved:
        status = (
            "needs_data" if any(c.preparation_status == "needs_data" for c in unresolved) else "out_of_scope"
        )
        body = _record(frame, context, status, tuple(issue for c in unresolved for issue in c.issues))
    else:
        assembled = frame.model_copy(
            update={
                "node_loads": frame.node_loads + tuple(load for c in contributions for load in c.node_loads),
                "initial_strains": frame.initial_strains
                + tuple(strain for c in contributions for strain in c.initial_strains),
                "initial_curvatures": frame.initial_curvatures
                + tuple(curvature for c in contributions for curvature in c.initial_curvatures),
            }
        )
        body = solve_frame(assembled, context)
    body.pop("record_digest")
    body["loading_contributions"] = [c.model_dump(mode="json") for c in contributions]
    body["unloaded_template_digest"] = digest(frame)
    body["load_preparation_implementation_sha256"] = file_digest(Path(__file__))
    body["record_digest"] = digest(body)
    return body
