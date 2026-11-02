"""Bind a simulated pipe-wall temperature snapshot to explicit frame mesh pieces."""
from __future__ import annotations

import json
from pathlib import Path
from pydantic import Field
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, require_context_extension
from .datasets import file_digest
from .liquid_thermal_chain import verify_liquid_thermal_chain_record
from .thermal_expansion import ExpansionCurve, integrate_thermal_expansion
from .structural import FrameRequest
from .structural_thermal_mesh import ThermalBeamPiece, ThermalMemberMesh, ThermalMeshRequest


class WallCellPieceMapping(Contract):
    source_cell_id: Name
    start_fraction: float = Field(ge=0,lt=1)
    end_fraction: float = Field(gt=0,le=1)
    stress_free_temperature_K: Positive
    expansion_curve: ExpansionCurve
    evidence_id: Name


class WallMemberMapping(Contract):
    member_id: Name
    case_id: Name
    section_y_extent_m: tuple[float,float]
    section_z_extent_m: tuple[float,float]
    pieces: tuple[WallCellPieceMapping, ...] = Field(min_length=1,max_length=500)
    uniform_section_temperature_and_axial_cell_mapping_reviewed: bool = False


def prepare_wall_temperature_frame(source_record: dict, frame: FrameRequest,
                                   mappings: tuple[WallMemberMapping, ...], context: RunContext, *, state_index: int):
    """Prepare a frame request without inferring a cross-section gradient.

    The thermal chain has one lumped wall temperature per cell. Each mapped
    structural piece therefore receives a uniform cross-section temperature;
    curvature requires a separate field with actual cross-section information.
    """
    verify_liquid_thermal_chain_record(source_record)
    base=FrameRequest.model_validate(frame.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    source_context=RunContext.model_validate_json(json.dumps(source_record["context"]))
    require_context_extension(source_context,c)
    if (base.asset_id,base.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("thermal-to-frame asset/clock differs")
    if type(state_index) is not int or not 0<=state_index<len(source_record["states"]):
        raise ValueError("a valid thermal chain snapshot index is required")
    state=source_record["states"][state_index]
    if base.time_s!=state["time_s"]:
        raise ValueError("frame time must equal the selected original thermal snapshot time")
    cell_ids=source_record["cell_order"]
    temperatures=dict(zip(cell_ids,state["wall_temperature_K"]))
    members={m.id:m for m in base.members}
    evidence={e.id:e for e in c.evidence}
    meshes=[]
    mapping_receipts=[]
    for raw in mappings:
        mapping=WallMemberMapping.model_validate(raw.model_dump(mode="python"))
        if not mapping.uniform_section_temperature_and_axial_cell_mapping_reviewed:
            raise ValueError("wall-cell to structural-piece applicability review is required")
        if mapping.member_id not in members:
            raise ValueError("wall mapping identifies an absent frame member")
        pieces=[]
        for piece in mapping.pieces:
            if piece.source_cell_id not in temperatures:
                raise ValueError("wall mapping identifies an absent thermal cell")
            if piece.expansion_curve.material_id!=members[mapping.member_id].material_id:
                raise ValueError("wall expansion curve material differs from frame member material")
            for identity in (piece.evidence_id,piece.expansion_curve.evidence_id):
                if identity not in evidence or evidence[identity].sha256 is None:
                    raise ValueError("thermal-to-structural mappings require source-hash-pinned evidence")
            temperature=temperatures[piece.source_cell_id]
            expansion=integrate_thermal_expansion(piece.expansion_curve,piece.stress_free_temperature_K,temperature)
            pieces.append(ThermalBeamPiece(start_fraction=piece.start_fraction,end_fraction=piece.end_fraction,
                centroid_temperature_K=temperature,stress_free_temperature_K=piece.stress_free_temperature_K,
                expansion_coefficient_per_K=expansion["equivalent_expansion_per_K"],
                coefficient_temperature_support_K=(piece.expansion_curve.knots[0].temperature_K,piece.expansion_curve.knots[-1].temperature_K),
                evidence_id=piece.evidence_id))
            mapping_receipts.append(dict(member_id=mapping.member_id,source_cell_id=piece.source_cell_id,
                start_fraction=piece.start_fraction,end_fraction=piece.end_fraction,
                mapping_evidence_id=piece.evidence_id,expansion=expansion))
        meshes.append(ThermalMemberMesh(member_id=mapping.member_id,case_id=mapping.case_id,
            section_y_extent_m=mapping.section_y_extent_m,section_z_extent_m=mapping.section_z_extent_m,
            pieces=tuple(pieces),piecewise_uniform_axial_and_linear_section_temperature_reviewed=True,
            frozen_elastic_properties_and_constant_expansion_per_piece_reviewed=True))
    derived=ThermalMeshRequest(id=source_record["request"]["id"]+":frame_snapshot",frame=base,members=tuple(meshes))
    body=dict(schema="pipeline_integrity.wall_temperature_frame_binding.v1",source_record_digest=source_record["record_digest"],
        source_state_index=state_index,source_time_s=state["time_s"],base_frame_digest=digest(base),
        context_digest=digest(c),mapping_records=mapping_receipts,derived_request=derived.model_dump(mode="json"),
        cross_section_gradient_inferred=False,implementation_sha256=file_digest(Path(__file__)))
    body["record_digest"]=digest(body)
    return derived,body
