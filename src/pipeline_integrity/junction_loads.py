"""Quasi-steady branch pressure/momentum resultants for an existing frame node.

Each port uses the fluid flow area, not the structural wall-section area. Its
control-volume outward normal points from the selected junction toward the
other endpoint of the connected frame member. Concurrent port force lines,
uniform exterior ambient pressure and negligible junction-local moments are
explicit applicability assumptions. The original frame owner solves response.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest
from .structural import FrameRequest, FrameNodeLoad, solve_frame


REVIEWS=(
    "steady_single_phase_port_states_and_mass_balance",
    "fluid_flow_areas_and_outward_port_axes",
    "common_exterior_ambient_and_static_pressure_reference",
    "small_control_volume_and_concurrent_force_lines",
    "fluid_inventory_weight_and_absent_local_acceleration",
    "no_duplicate_end_thrust_or_fluid_weight",
    "structural_load_case_and_combination_mapping",
)


class JunctionPort(Contract):
    id: Name
    member_id: Name
    physical_port_id: Name
    clock_id: Name
    time_s: Nonnegative
    fluid_flow_area_m2: Positive
    static_pressure_pa_abs: Positive
    pressure_interpretation: Literal["section_mean_static_absolute"] = "section_mean_static_absolute"
    density_kg_m3: Positive
    mass_flow_out_of_control_volume_kg_s: float
    evidence_id: Name


class JunctionLoadRequest(Contract):
    id: Name
    frame: FrameRequest
    junction_node_id: Name
    case_id: Name
    ports: tuple[JunctionPort, ...] = Field(min_length=2,max_length=64)
    exterior_ambient_pressure_pa_abs: Positive
    fluid_mass_in_control_volume_kg: Nonnegative
    gravity_vector_m_s2: tuple[float,float,float] = (0.0,0.0,-9.80665)
    control_volume_force_lines: Literal["concurrent_at_junction_node"] = "concurrent_at_junction_node"
    momentum_profile_assumption: Literal["uniform_axial_mean_velocity_with_unit_momentum_factor"] = "uniform_axial_mean_velocity_with_unit_momentum_factor"
    gravity_application_assumption: Literal["fluid_centroid_at_junction_or_local_weight_moment_negligible"] = "fluid_centroid_at_junction_or_local_weight_moment_negligible"
    absolute_mass_balance_tolerance_kg_s: Nonnegative = 1e-9
    relative_mass_balance_tolerance: Nonnegative = Field(default=1e-8,le=1e-3)
    port_clock_tolerance_s: Nonnegative = 0.0
    fluid_inventory_and_gravity_evidence_id: Name
    ambient_pressure_evidence_id: Name
    reviews: dict[str,bool] = Field(default_factory=lambda:dict.fromkeys(REVIEWS,False))
    review_evidence_ids: dict[str,Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    solve_prepared_frame: bool = False

    @model_validator(mode="after")
    def identifiers(self):
        if set(self.reviews)!=set(REVIEWS) or set(self.review_evidence_ids)-set(REVIEWS):
            raise ValueError("junction pressure/momentum reviews must use the named applicability fields")
        for values in ((p.id for p in self.ports),(p.member_id for p in self.ports),
                       (p.physical_port_id for p in self.ports),self.evidence_ids):
            items=list(values)
            if len(set(items))!=len(items):
                raise ValueError("junction ports, connected members and selected evidence must have unique IDs")
        return self


def _policy(request,context):
    q=JunctionLoadRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.frame.asset_id,q.frame.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("junction frame asset/clock differs from context")
    by_id={e.id:e for e in c.evidence}
    if len(by_id)!=len(c.evidence):
        raise ValueError("junction context evidence IDs must be unique")
    required=set(q.evidence_ids)|{q.frame.geometry_evidence_id,q.fluid_inventory_and_gravity_evidence_id,
        q.ambient_pressure_evidence_id}|{p.evidence_id for p in q.ports}|set(q.review_evidence_ids.values())
    if not required<=set(by_id):
        raise ValueError("junction geometry, port, ambient, inventory and review evidence must occur in context")
    issues=[]
    for review in REVIEWS:
        evidence=by_id.get(q.review_evidence_ids.get(review))
        if not q.reviews[review] or evidence is None or evidence.sha256 is None:
            issues.append("source-hash-pinned review required: "+review)
    if q.case_id not in q.frame.load_cases:
        raise ValueError("junction load case must already exist in the frame")
    existing=(*q.frame.node_loads,*q.frame.line_loads,*q.frame.initial_strains,
              *getattr(q.frame,"initial_curvatures",()))
    if any(load.case_id==q.case_id for load in existing):
        raise ValueError("junction pressure/momentum loads require an unused load case; existing end loads or strains cannot be reused")
    if not any(combination.factors.get(q.case_id,0)!=0 for combination in q.frame.combinations):
        raise ValueError("at least one explicit frame combination must apply the junction load case")
    for port in q.ports:
        if port.clock_id!=q.frame.clock_id or abs(port.time_s-q.frame.time_s)>q.port_clock_tolerance_s:
            raise ValueError("junction port pressures and flows must use the frame snapshot clock and time")
    return q,c,issues


def _base(q,c):
    return dict(schema="pipeline_integrity.junction_loads.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="needs_data",issues=[],
        implementation_sha256=file_digest(Path(__file__)),
        structural_owner_sha256=file_digest(Path(__file__).with_name("structural.py")),
        base_frame_digest=digest(q.frame),prepared_frame=None,frame_solution=None,
        structural_solve_executed=False,numerical_validation_completed=False,
        local_fitting_stress_or_flexibility_computed=False,
        fluid_pressure_induced_member_strain_automatically_added=False,
        force_direction="fluid_on_structural_junction",pressure_reference="static_absolute_minus_common_exterior_ambient",
        mass_flow_sign="positive_out_of_fluid_control_volume")


def _seal(body):
    body=json_value(body)
    body["record_digest"]=digest(body)
    return body


def _prepare(q,c):
    body=_base(q,c)
    nodes={n.id:n for n in q.frame.nodes}
    members={m.id:m for m in q.frame.members}
    sections={s.id:s for s in q.frame.sections}
    junction=nodes.get(q.junction_node_id)
    if junction is None:
        raise ValueError("junction load target must be an existing frame node")
    ports=[]
    for port in q.ports:
        member=members.get(port.member_id)
        if member is None or q.junction_node_id not in (member.i_node,member.j_node):
            raise ValueError("every junction port must use a member connected to the selected junction")
        other=member.j_node if member.i_node==q.junction_node_id else member.i_node
        delta=[b-a for a,b in zip(junction.position_m,nodes[other].position_m)]
        length=math.hypot(*delta)
        if not math.isfinite(length) or length<=0:
            raise ValueError("junction port axis cannot be normalized from its member geometry")
        normal=[v/length for v in delta]
        area=port.fluid_flow_area_m2
        denominator=port.density_kg_m3*area
        if not math.isfinite(denominator) or denominator<=0:
            raise ValueError("junction port density-area product is outside finite arithmetic")
        velocity=port.mass_flow_out_of_control_volume_kg_s/denominator
        pressure_force=(port.static_pressure_pa_abs-q.exterior_ambient_pressure_pa_abs)*area
        momentum_flux=port.mass_flow_out_of_control_volume_kg_s*velocity
        resultant=math.fsum((pressure_force,momentum_flux))
        if not all(math.isfinite(v) for v in (velocity,pressure_force,momentum_flux,resultant)):
            raise ValueError("junction pressure or momentum force exceeds finite arithmetic")
        if port.mass_flow_out_of_control_volume_kg_s!=0 and (velocity==0 or momentum_flux==0):
            raise ValueError("nonzero junction momentum flux underflow is not an exact zero-flow state")
        if port.static_pressure_pa_abs!=q.exterior_ambient_pressure_pa_abs and pressure_force==0:
            raise ValueError("nonzero junction pressure force underflow is not an exact ambient-pressure balance")
        ports.append(dict(port_id=port.id,physical_port_id=port.physical_port_id,member_id=port.member_id,
            outward_normal=normal,fluid_flow_area_m2=area,
            structural_section_area_m2_not_used_in_fluid_force=sections[member.section_id].area_m2,
            signed_outward_velocity_m_s=velocity,gauge_pressure_force_magnitude_N=pressure_force,
            momentum_flux_magnitude_N=momentum_flux,
            pressure_plus_momentum_outward_vector_N=[resultant*v for v in normal],
            fluid_on_wall_port_contribution_N=[-resultant*v for v in normal],evidence_id=port.evidence_id))
    mass_residual=math.fsum(p.mass_flow_out_of_control_volume_kg_s for p in q.ports)
    mass_scale=math.fsum(abs(p.mass_flow_out_of_control_volume_kg_s) for p in q.ports)
    tolerance=q.absolute_mass_balance_tolerance_kg_s+q.relative_mass_balance_tolerance*mass_scale
    if not all(math.isfinite(v) for v in (mass_residual,mass_scale,tolerance)):
        raise ValueError("junction mass-balance scaling exceeds finite arithmetic")
    body.update(port_force_terms=ports,mass_balance_residual_kg_s=mass_residual,
                mass_balance_tolerance_kg_s=tolerance,flow_adjustment_applied=False)
    if abs(mass_residual)>tolerance:
        body.update(status="out_of_scope",issues=["quasi-steady junction ports do not conserve mass within the declared tolerance"])
        return None,body
    weight=[q.fluid_mass_in_control_volume_kg*g for g in q.gravity_vector_m_s2]
    force=[math.fsum([weight[i],*(p["fluid_on_wall_port_contribution_N"][i] for p in ports)]) for i in range(3)]
    if not all(math.isfinite(v) for v in (*weight,*force)):
        raise ValueError("junction resultant or fluid weight exceeds finite arithmetic")
    evidence_id=q.review_evidence_ids["structural_load_case_and_combination_mapping"]
    loads=tuple(FrameNodeLoad(node_id=q.junction_node_id,direction=axis,value=value,
        case_id=q.case_id,evidence_id=evidence_id) for axis,value in zip(("FX","FY","FZ"),force))
    payload=q.frame.model_dump(mode="python")
    payload["node_loads"]=(*q.frame.node_loads,*loads)
    prepared=FrameRequest.model_validate(payload)
    body.update(status="ready",fluid_weight_vector_N=weight,fluid_on_wall_force_vector_N=force,
        applied_moment_vector_N_m=[0.0,0.0,0.0],node_loads=[v.model_dump(mode="json") for v in loads],
        prepared_frame=prepared.model_dump(mode="json"),prepared_frame_digest=digest(prepared))
    return prepared,body


def prepare_junction_frame(request: JunctionLoadRequest, context: RunContext):
    """Return the frame with a new isolated junction load case, without solving it."""
    q,c,issues=_policy(request,context)
    if q.solve_prepared_frame:
        raise ValueError("prepare_junction_frame requires solve_prepared_frame=False; use evaluate_junction_loads for a structural solve")
    if issues:
        raise ValueError("junction applicability is unresolved: "+"; ".join(issues))
    prepared,body=_prepare(q,c)
    if prepared is None:
        raise ValueError("junction loading is outside its quasi-steady mass-balance scope")
    return prepared,_seal(body)


def evaluate_junction_loads(request: JunctionLoadRequest, context: RunContext):
    """Prepare global frame loads and optionally call the original frame solver."""
    q,c,issues=_policy(request,context)
    if issues:
        body=_base(q,c)
        body["issues"]=issues
        return _seal(body)
    prepared,body=_prepare(q,c)
    if prepared is not None and q.solve_prepared_frame:
        solution=solve_frame(prepared,c)
        if file_digest(Path(__file__).with_name("structural.py"))!=body["structural_owner_sha256"]:
            raise ValueError("structural owner source changed while solving the junction frame")
        body.update(frame_solution=solution,structural_solve_executed=True,status=solution["status"])
    return _seal(body)


def verify_junction_load_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("junction load receipt changed")
    q=JunctionLoadRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("junction caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("junction caller context differs")
    if evaluate_junction_loads(q,c)!=record:
        raise ValueError("junction pressure/momentum or structural replay differs")
    return True
