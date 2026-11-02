"""Explicit thermal mesh refinement around the existing linear frame solver.

Each supplied axial piece has a uniform mean temperature, linear local-section
gradient and frozen elastic properties. Continuity is enforced by shared mesh
nodes; this is a piecewise beam discretization, not a hidden interpolation of a
continuous temperature field or a nonlinear material-state iteration.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .structural import (FrameRequest, FrameNode, FrameMember, FrameMaterial,
                         FrameInitialStrain, FrameInitialCurvature, FrameLineLoad, solve_frame)


class ThermalBeamPiece(Contract):
    start_fraction: float = Field(ge=0,lt=1)
    end_fraction: float = Field(gt=0,le=1)
    centroid_temperature_K: Positive
    stress_free_temperature_K: Positive
    temperature_gradient_y_K_m: float = 0.0
    temperature_gradient_z_K_m: float = 0.0
    expansion_coefficient_per_K: Nonnegative
    coefficient_temperature_support_K: tuple[Positive,Positive]
    young_modulus_pa: Positive | None = None
    poisson_ratio: float | None = Field(default=None,gt=-1,lt=0.5)
    elastic_property_evidence_id: Name | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def coherent(self):
        if self.start_fraction>=self.end_fraction:
            raise ValueError("thermal beam piece fractions must increase")
        if self.coefficient_temperature_support_K[0]>=self.coefficient_temperature_support_K[1]:
            raise ValueError("thermal coefficient temperature support must increase")
        if (self.young_modulus_pa is None)!=(self.poisson_ratio is None):
            raise ValueError("frozen elastic modulus and Poisson ratio must be supplied together")
        if self.young_modulus_pa is not None and self.elastic_property_evidence_id is None:
            raise ValueError("frozen temperature-specific elastic properties require evidence")
        return self


class ThermalMemberMesh(Contract):
    member_id: Name
    case_id: Name
    section_y_extent_m: tuple[float,float]
    section_z_extent_m: tuple[float,float]
    pieces: tuple[ThermalBeamPiece,...] = Field(min_length=1,max_length=500)
    piecewise_uniform_axial_and_linear_section_temperature_reviewed: bool = False
    frozen_elastic_properties_and_constant_expansion_per_piece_reviewed: bool = False

    @model_validator(mode="after")
    def complete(self):
        if self.pieces[0].start_fraction!=0 or self.pieces[-1].end_fraction!=1:
            raise ValueError("thermal pieces must cover both member endpoints")
        if any(a.end_fraction!=b.start_fraction for a,b in zip(self.pieces,self.pieces[1:])):
            raise ValueError("thermal pieces must cover the source member without overlap or gaps")
        for a,b in (self.section_y_extent_m,self.section_z_extent_m):
            if not a<b or a>0 or b<0:
                raise ValueError("thermal section extents must enclose the local centroid")
        return self


class ThermalMeshRequest(Contract):
    id: Name
    frame: FrameRequest
    members: tuple[ThermalMemberMesh,...] = Field(min_length=1,max_length=2000)
    maximum_refined_members: int = Field(default=2000,ge=1,le=2000)
    maximum_refined_nodes: int = Field(default=2000,ge=2,le=2000)

    @model_validator(mode="after")
    def references(self):
        if len({m.member_id for m in self.members})!=len(self.members):
            raise ValueError("one thermal mesh declaration is allowed per source member")
        source={m.id:m for m in self.frame.members}
        for mesh in self.members:
            if mesh.member_id not in source or mesh.case_id not in self.frame.load_cases:
                raise ValueError("thermal mesh must reference existing members and load cases")
            if source[mesh.member_id].rotation_releases:
                raise ValueError("thermal mesh currently requires unreleased source member rotations")
            if any(s.member_id==mesh.member_id and s.case_id==mesh.case_id and s.kind=="thermal"
                   for s in (*self.frame.initial_strains,*self.frame.initial_curvatures)):
                raise ValueError("source thermal strain/curvature would duplicate the new thermal field")
        refined=len(self.frame.members)-len(self.members)+sum(len(m.pieces) for m in self.members)
        nodes=len(self.frame.nodes)+sum(len(m.pieces)-1 for m in self.members)
        if refined>self.maximum_refined_members or nodes>self.maximum_refined_nodes:
            raise ValueError("thermal mesh exceeds its explicit topology budget")
        return self


def prepare_thermal_mesh(request: ThermalMeshRequest, context: RunContext):
    q=ThermalMeshRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    source=q.frame
    if (source.asset_id,source.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("thermal mesh asset/clock differs from context")
    evidence={e.id for e in c.evidence}
    required={p.evidence_id for m in q.members for p in m.pieces}
    required.update(p.elastic_property_evidence_id for m in q.members for p in m.pieces if p.elastic_property_evidence_id is not None)
    if not required<=evidence:
        raise ValueError("thermal mesh properties and temperature sources require context evidence")
    if any(not (m.piecewise_uniform_axial_and_linear_section_temperature_reviewed
                and m.frozen_elastic_properties_and_constant_expansion_per_piece_reviewed) for m in q.members):
        raise ValueError("thermal mesh field, material and interpolation reviews are required")
    meshes={m.member_id:m for m in q.members}
    node_by_id={n.id:n for n in source.nodes}
    nodes=list(source.nodes)
    materials=list(source.materials)
    members=[]
    strains=[]
    curvatures=[]
    line_loads=[]
    mappings=[]
    used_nodes={n.id for n in nodes}
    used_members={m.id for m in source.members}
    used_materials={m.id for m in materials}
    splits={}
    for member in source.members:
        mesh=meshes.get(member.id)
        if mesh is None:
            members.append(member)
            splits[member.id]=[(member.id,0.0,1.0)]
            continue
        a=node_by_id[member.i_node].position_m
        b=node_by_id[member.j_node].position_m
        sequence=[member.i_node]
        for index,piece in enumerate(mesh.pieces[:-1]):
            identity=f"{q.id}:{member.id}:thermal_node:{index+1}"
            if identity in used_nodes:
                raise ValueError("generated thermal node ID collides with source topology")
            used_nodes.add(identity)
            position=tuple(x+(y-x)*piece.end_fraction for x,y in zip(a,b))
            nodes.append(FrameNode(id=identity,position_m=position))
            sequence.append(identity)
        sequence.append(member.j_node)
        splits[member.id]=[]
        for index,piece in enumerate(mesh.pieces):
            identity=f"{q.id}:{member.id}:thermal_member:{index}"
            if identity in used_members:
                raise ValueError("generated thermal member ID collides with source topology")
            used_members.add(identity)
            material_id=member.material_id
            if piece.young_modulus_pa is not None:
                material_id=identity+":material"
                if material_id in used_materials:
                    raise ValueError("generated thermal material ID collides with source materials")
                used_materials.add(material_id)
                materials.append(FrameMaterial(id=material_id,young_modulus_pa=piece.young_modulus_pa,
                    poisson_ratio=piece.poisson_ratio,evidence_id=piece.elastic_property_evidence_id))
            payload=member.model_dump(mode="python")
            samples={0.0,1.0}
            samples.update((f-piece.start_fraction)/(piece.end_fraction-piece.start_fraction)
                           for f in member.sample_fractions if piece.start_fraction<=f<=piece.end_fraction)
            payload.update(id=identity,i_node=sequence[index],j_node=sequence[index+1],material_id=material_id,
                           sample_fractions=tuple(sorted(samples)))
            members.append(FrameMember.model_validate(payload))
            splits[member.id].append((identity,piece.start_fraction,piece.end_fraction))
            low,high=piece.coefficient_temperature_support_K
            temperatures=[piece.stress_free_temperature_K]
            temperatures.extend(piece.centroid_temperature_K+piece.temperature_gradient_y_K_m*y+piece.temperature_gradient_z_K_m*z
                                for y in mesh.section_y_extent_m for z in mesh.section_z_extent_m)
            if any(not math.isfinite(t) or not low<=t<=high for t in temperatures):
                raise ValueError("a thermal section/reference temperature exceeds its supplied property support")
            strain=piece.expansion_coefficient_per_K*(piece.centroid_temperature_K-piece.stress_free_temperature_K)
            ky=piece.expansion_coefficient_per_K*piece.temperature_gradient_z_K_m
            kz=-piece.expansion_coefficient_per_K*piece.temperature_gradient_y_K_m
            strains.append(FrameInitialStrain(id=identity+":thermal_strain",member_id=identity,case_id=mesh.case_id,
                value=strain,kind="thermal",evidence_id=piece.evidence_id,uniform_axial_applicability_reviewed=True))
            curvatures.append(FrameInitialCurvature(id=identity+":thermal_curvature",member_id=identity,case_id=mesh.case_id,
                curvature_y_per_m=ky,curvature_z_per_m=kz,kind="thermal",evidence_id=piece.evidence_id,
                uniform_curvature_applicability_reviewed=True))
            mappings.append(dict(source_member_id=member.id,refined_member_id=identity,
                source_fraction_bounds=[piece.start_fraction,piece.end_fraction],material_id=material_id,
                centroid_temperature_K=piece.centroid_temperature_K,stress_free_strain=strain,
                stress_free_curvature_y_per_m=ky,stress_free_curvature_z_per_m=kz))
    for initial in source.initial_strains:
        for target,_,_ in splits[initial.member_id]:
            data=initial.model_dump(mode="python")
            data.update(id=initial.id+":"+target,member_id=target)
            strains.append(FrameInitialStrain.model_validate(data))
    for initial in source.initial_curvatures:
        for target,_,_ in splits[initial.member_id]:
            data=initial.model_dump(mode="python")
            data.update(id=initial.id+":"+target,member_id=target)
            curvatures.append(FrameInitialCurvature.model_validate(data))
    for load in source.line_loads:
        for target,a,b in splits[load.member_id]:
            left,right=max(a,load.start_fraction),min(b,load.end_fraction)
            if right<=left:
                continue
            slope=(load.end_n_m-load.start_n_m)/(load.end_fraction-load.start_fraction)
            line_loads.append(FrameLineLoad(member_id=target,global_direction=load.global_direction,
                start_n_m=load.start_n_m+slope*(left-load.start_fraction),
                end_n_m=load.start_n_m+slope*(right-load.start_fraction),
                start_fraction=(left-a)/(b-a),end_fraction=(right-a)/(b-a),
                case_id=load.case_id,evidence_id=load.evidence_id))
    payload=source.model_dump(mode="python")
    payload.update(nodes=tuple(nodes),members=tuple(members),materials=tuple(materials),
                   initial_strains=tuple(strains),initial_curvatures=tuple(curvatures),line_loads=tuple(line_loads))
    refined=FrameRequest.model_validate(payload)
    receipt=dict(schema="pipeline_integrity.thermal_frame_mesh.v1",request=q.model_dump(mode="json"),
        context_digest=digest(c),refined_frame_digest=digest(refined),member_mapping=mappings,
        temperature_field="piecewise_axial_constant_linear_section",elastic_properties="frozen_per_piece",
        thermal_target_member_rotations_released=False,implementation_sha256=file_digest(Path(__file__)),
        structural_owner_sha256=file_digest(Path(__file__).with_name("structural.py")))
    receipt["record_digest"]=digest(receipt)
    return refined,receipt


def evaluate_thermal_mesh_frame(request: ThermalMeshRequest, context: RunContext):
    q=ThermalMeshRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    sources={name:file_digest(Path(__file__).with_name(name+".py"))
             for name in ("structural_thermal_mesh","structural")}
    input_digest=digest(dict(request=q,context=c))
    refined,preparation=prepare_thermal_mesh(q,c)
    native=solve_frame(refined,c)
    if sources!={name:file_digest(Path(__file__).with_name(name+".py")) for name in sources} or input_digest!=digest(dict(request=q,context=c)):
        raise ValueError("thermal mesh source or input/context changed during evaluation")
    body=dict(schema="pipeline_integrity.thermal_mesh_frame.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status=native["status"],preparation=preparation,
        refined_frame=refined.model_dump(mode="json"),result=native,source_pins=sources,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_thermal_mesh_frame_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("thermal mesh frame receipt changed")
    q=ThermalMeshRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("thermal mesh caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("thermal mesh caller context differs")
    if evaluate_thermal_mesh_frame(q,c)!=record:
        raise ValueError("thermal mesh frame replay differs")
    return True
