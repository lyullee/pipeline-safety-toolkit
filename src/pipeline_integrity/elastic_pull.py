"""Finite-EA suspended spans using the actual MoorPy catenary solver.

Only the fully suspended native branch is retained. Endpoint motion is a
sequence of static equilibria, with external work checked against potential
and axial strain energy; pipe bending and contact laws are not substituted.
"""
from __future__ import annotations

import importlib
import inspect
import json
import math
from importlib.metadata import distribution, version
from pathlib import Path

import numpy as np
from pydantic import Field, model_validator
from scipy.integrate import simpson

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, json_value, require_context_extension
from .datasets import file_digest

Vector=tuple[float,float,float]
SOURCE_URL="https://github.com/NatLabRockies/MoorPy"


class ElasticPullKnot(Contract):
    id: Name
    time_s: Nonnegative
    start_position_m: Vector
    end_position_m: Vector


class ElasticPullRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    frame_id: Name
    coordinate_definition_id: Name
    start_port_id: Name
    end_port_id: Name
    unstretched_length_m: Positive
    axial_rigidity_EA_N: Positive
    distributed_force_N_per_unstretched_m: Vector
    knots: tuple[ElasticPullKnot,...] = Field(min_length=1,max_length=1000)
    subdivisions_per_motion_interval: int = Field(default=16,ge=1,le=1000)
    profile_nodes: int = Field(default=129,ge=5,le=10001)
    maximum_native_calls: int = Field(default=5000,ge=1,le=100000)
    maximum_profile_points: int = Field(default=1000000,ge=5,le=10000000)
    maximum_native_iterations: int = Field(default=100,ge=5,le=1000)
    position_tolerance_m: Positive = 1e-6
    force_tolerance_N: Positive = 1e-5
    absolute_work_energy_tolerance_J: Nonnegative = 0.01
    relative_work_energy_tolerance: Nonnegative = 1e-4
    maximum_axial_strain: Positive = Field(le=0.25)
    reviewed_tension_limit_N: Positive | None = None
    limit_evidence_id: Name | None = None
    geometry_evidence_id: Name
    axial_rigidity_evidence_id: Name
    load_evidence_id: Name
    fully_suspended_linear_axial_quasistatic_model_reviewed: bool = False
    native_backend_pins: dict
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def scope(self):
        if self.start_port_id==self.end_port_id or len({k.id for k in self.knots})!=len(self.knots):
            raise ValueError("elastic span needs distinct ports and unique motion-knot IDs")
        if any(b.time_s<=a.time_s for a,b in zip(self.knots,self.knots[1:])):
            raise ValueError("quasi-static endpoint motion follows a strictly increasing clock")
        if math.hypot(*self.distributed_force_N_per_unstretched_m)==0:
            raise ValueError("a nonzero uniform conservative line load is required")
        count=1+(len(self.knots)-1)*self.subdivisions_per_motion_interval
        if count>self.maximum_native_calls or count*self.profile_nodes>self.maximum_profile_points:
            raise ValueError("elastic span motion/profile exceeds its declared work budget")
        if (self.profile_nodes-1)%4:
            raise ValueError("profile_nodes must equal 4*k+1 for nested energy quadrature")
        if (self.reviewed_tension_limit_N is None)!=(self.limit_evidence_id is None):
            raise ValueError("a tension limit and its separate reviewed evidence must be supplied together")
        return self


def elastic_pull_backend_metadata():
    """Explicit discovery without running a catenary solve or installing a package."""
    native=importlib.import_module("moorpy.Catenary")
    helpers=importlib.import_module("moorpy.helpers")
    expected=("XF","ZF","L","EA","W","CB","alpha","HF0","VF0","Tol","nNodes","MaxIter","plots","depth")
    if tuple(inspect.signature(native.catenary).parameters)!=expected:
        raise ValueError("installed MoorPy catenary signature differs from the inspected API")
    dist=distribution("MoorPy")
    candidates=[Path(dist.locate_file(p)) for p in dist.files or () if Path(p).name.lower() in {"license.txt","license"}]
    root=Path(native.__file__).resolve().parent
    candidates.extend(parent/"LICENSE.txt" for parent in (root,root.parent))
    license_path=next((p for p in candidates if p.is_file() and "BSD 3-Clause" in p.read_text(encoding="utf-8")),None)
    if license_path is None:
        raise ValueError("the native MoorPy BSD-3-Clause notice must remain available")
    return dict(provider="MoorPy.catenary",version=dist.version,source_url=SOURCE_URL,license="BSD-3-Clause",
        catenary_sha256=file_digest(Path(native.__file__)),helpers_sha256=file_digest(Path(helpers.__file__)),
        license_sha256=file_digest(license_path),numpy_version=np.__version__,scipy_version=version("scipy"))


def elastic_pull_boundary(request):
    return dict(id=request.id,frame_id=request.frame_id,coordinate_definition_id=request.coordinate_definition_id,
        ports=[request.start_port_id,request.end_port_id],unstretched_length_m=request.unstretched_length_m,
        axial_rigidity_EA_N=request.axial_rigidity_EA_N,
        load_N_per_unstretched_m=list(request.distributed_force_N_per_unstretched_m),knots_digest=digest(request.knots))


def bind_elastic_pull_context(request: ElasticPullRequest, context: RunContext):
    q=ElasticPullRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("elastic span asset/clock differs from context")
    return RunContext.model_validate({**c.model_dump(mode="python"),"metadata":{**c.metadata,"elastic_pull_boundary":elastic_pull_boundary(q)}})


def _dot(a,b):
    return math.fsum(x*y for x,y in zip(a,b))


def evaluate_elastic_pull(request: ElasticPullRequest, context: RunContext):
    q=ElasticPullRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id) or c.metadata.get("elastic_pull_boundary")!=elastic_pull_boundary(q):
        raise ValueError("elastic span frame, ports, motion or clock binding differs")
    ids=[e.id for e in c.evidence]
    required={q.geometry_evidence_id,q.axial_rigidity_evidence_id,q.load_evidence_id}
    if q.limit_evidence_id is not None:
        required.add(q.limit_evidence_id)
    if len(ids)!=len(set(ids)) or not required<=set(ids):
        raise ValueError("elastic span geometry, EA, loading and limit evidence must exist unambiguously")
    if not q.fully_suspended_linear_axial_quasistatic_model_reviewed:
        raise ValueError("fully suspended finite-EA quasi-static applicability requires explicit review")
    backend=elastic_pull_backend_metadata()
    if backend!=q.native_backend_pins:
        raise ValueError("MoorPy source/runtime/license differs from its reviewed pins")
    native=importlib.import_module("moorpy.Catenary")
    source=file_digest(Path(__file__))
    weight=math.hypot(*q.distributed_force_N_per_unstretched_m)
    up=tuple(-v/weight for v in q.distributed_force_N_per_unstretched_m)
    positions=[dict(time_s=q.knots[0].time_s,start=q.knots[0].start_position_m,end=q.knots[0].end_position_m)]
    for left,right in zip(q.knots,q.knots[1:]):
        for i in range(1,q.subdivisions_per_motion_interval+1):
            f=i/q.subdivisions_per_motion_interval
            positions.append(dict(time_s=right.time_s if i==q.subdivisions_per_motion_interval else left.time_s+f*(right.time_s-left.time_s),
                start=tuple(a+f*(b-a) for a,b in zip(left.start_position_m,right.start_position_m)),
                end=tuple(a+f*(b-a) for a,b in zip(left.end_position_m,right.end_position_m))))
    if any(b["time_s"]<=a["time_s"] for a,b in zip(positions,positions[1:])):
        raise ValueError("elastic span clock cannot represent distinct path subdivisions")
    rows=[]
    byte_count=0
    endpoint_work=0.0
    for position in positions:
        start,end=position["start"],position["end"]
        delta=tuple(b-a for a,b in zip(start,end))
        rise=_dot(delta,up)
        horizontal=tuple(v-rise*u for v,u in zip(delta,up))
        distance=math.hypot(*horizontal)
        if distance<=max(q.position_tolerance_m,0.0001*abs(rise)):
            raise ValueError("near-vertical native approximations are outside this fully suspended catenary adapter")
        direction=tuple(v/distance for v in horizontal)
        # This numerical plane lies beyond any path satisfying the accepted strain bound.
        floor_drop=q.unstretched_length_m*(1+q.maximum_axial_strain)+abs(rise)+1.0
        # MoorPy tests the two endpoint equations component-wise.  Give it a
        # stricter tolerance so their combined Euclidean residual remains
        # within the caller's declared position tolerance.
        native_position_tolerance=q.position_tolerance_m/2.0
        inputs=dict(XF=distance,ZF=rise,L=q.unstretched_length_m,EA=q.axial_rigidity_EA_N,W=weight,CB=-floor_drop,
                    alpha=0,HF0=0,VF0=0,Tol=native_position_tolerance,nNodes=q.profile_nodes,MaxIter=q.maximum_native_iterations,plots=1,depth=0)
        fx_a,fz_a,fx_b,fz_b,info=native.catenary(**inputs)
        if info.get("error") is not False or info.get("ProfileType")!=1 or info.get("LBot")!=0:
            raise ValueError("native catenary did not return the exact fully suspended branch; approximations/contact are withheld")
        local=[np.asarray(info[key],dtype=float) for key in ("s","X","Z","Te")]
        s,x,z,tension=local
        if any(v.shape!=(q.profile_nodes,) or not np.isfinite(v).all() for v in local) or not np.all(np.diff(s)>0):
            raise ValueError("native catenary profile has invalid finite material-coordinate arrays")
        if s[0]!=0 or s[-1]!=q.unstretched_length_m or np.any(tension<=0):
            raise ValueError("native profile does not span the unstretched tensile line")
        errors=np.asarray(info.get("catenary",{}).get("err",()),dtype=float)
        if errors.shape!=(2,) or not np.isfinite(errors).all() or np.max(np.abs(errors))>native_position_tolerance:
            raise ValueError("native catenary endpoint residual exceeds the declared displacement tolerance")
        force_a=tuple(float(fx_a)*h+float(fz_a)*u for h,u in zip(direction,up))
        force_b=tuple(float(fx_b)*h+float(fz_b)*u for h,u in zip(direction,up))
        if not all(math.isfinite(v) for v in (*force_a,*force_b)):
            raise ValueError("native catenary endpoint forces are not finite")
        force_residual=tuple(a+b-load*q.unstretched_length_m for a,b,load in zip(force_a,force_b,q.distributed_force_N_per_unstretched_m))
        if math.hypot(*force_residual)>q.force_tolerance_N:
            raise ValueError("native catenary end forces do not balance the uniform reference-length load")
        peak=max(math.hypot(*force_a),math.hypot(*force_b))
        if max(abs(float(tension[0])-math.hypot(*force_a)),abs(float(tension[-1])-math.hypot(*force_b)))>q.force_tolerance_N or float(np.max(tension))>peak+q.force_tolerance_N:
            raise ValueError("native distributed tension differs from its end-force envelope")
        if peak/q.axial_rigidity_EA_N>q.maximum_axial_strain:
            raise ValueError("native elastic span exceeds its reviewed linear axial strain domain")
        coordinates=np.asarray([[a+float(xx)*h+float(zz)*u for a,h,u in zip(start,direction,up)] for xx,zz in zip(x,z)])
        endpoint_error=max(math.dist(coordinates[0],start),math.dist(coordinates[-1],end))
        if endpoint_error>q.position_tolerance_m:
            raise ValueError("native catenary profile changed the declared endpoint coordinates")
        energy_density=tension**2/(2*q.axial_rigidity_EA_N)
        potential_density=-coordinates@np.asarray(q.distributed_force_N_per_unstretched_m)
        elastic=float(simpson(energy_density,x=s))
        potential=float(simpson(potential_density,x=s))
        coarse=float(simpson(energy_density[::2]+potential_density[::2],x=s[::2]))
        stored=elastic+potential
        extension=float(simpson(tension/q.axial_rigidity_EA_N,x=s))
        work_step=0.0
        if rows:
            previous=rows[-1]
            work_step=-0.5*math.fsum((a+b)*(now-old) for a,b,now,old in
                zip(previous["force_on_start_support_N"],force_a,start,previous["start_position_m"]))
            work_step-=0.5*math.fsum((a+b)*(now-old) for a,b,now,old in
                zip(previous["force_on_end_support_N"],force_b,end,previous["end_position_m"]))
            endpoint_work+=work_step
        residual=stored-(rows[0]["stored_plus_potential_energy_J"] if rows else stored)-endpoint_work
        scale=max(abs(stored-(rows[0]["stored_plus_potential_energy_J"] if rows else stored)),abs(endpoint_work),1.0)
        within=abs(residual)<=q.absolute_work_energy_tolerance_J+q.relative_work_energy_tolerance*scale
        row=json_value(dict(time_s=position["time_s"],start_position_m=start,end_position_m=end,
            native_inputs=inputs,native_profile_type=info["ProfileType"],native_position_residual_m=errors.tolist(),
            force_on_start_support_N=force_a,force_on_end_support_N=force_b,force_balance_residual_N=force_residual,
            profile_material_coordinate_m=s.tolist(),profile_position_m=coordinates.tolist(),profile_tension_N=tension.tolist(),
            exact_uniform_load_peak_tension_N=peak,maximum_axial_strain=peak/q.axial_rigidity_EA_N,
            stretched_length_m=q.unstretched_length_m+extension,axial_extension_m=extension,
            elastic_strain_energy_J=elastic,distributed_load_potential_J=potential,stored_plus_potential_energy_J=stored,
            nested_spatial_quadrature_difference_J=stored-coarse,endpoint_work_step_J=work_step,
            cumulative_endpoint_work_J=endpoint_work,work_energy_residual_J=residual,work_energy_within_tolerance=within,
            within_reviewed_tension_limit=None if q.reviewed_tension_limit_N is None else peak<=q.reviewed_tension_limit_N))
        byte_count+=len(json.dumps(row,allow_nan=False).encode("utf-8"))
        if byte_count>q.maximum_result_bytes:
            raise ValueError("elastic span retained profile/trajectory byte budget exceeded")
        rows.append(row)
    if elastic_pull_backend_metadata()!=backend or file_digest(Path(__file__))!=source:
        raise ValueError("elastic span source/runtime changed during native solves")
    body=json_value(dict(schema="pipeline_integrity.elastic_pull.v1",status="computed_diagnostic" if all(r["work_energy_within_tolerance"] for r in rows) else "withheld",
        request=q,context=c,states=rows,backend=backend,implementation_sha256=source,native_calls=len(rows),
        axial_extension_solved=True,endpoint_forces="cable_on_support; external_work_uses_opposite_force",
        distributed_force_reference="per_unstretched_length",trajectory="piecewise_linear_endpoint_paths_with_static_equilibria",
        work_quadrature="composite_trapezoid_on_endpoint_path",energy_quadrature="nested_Simpson_on_material_coordinate",
        bending_EI_solved=False,ground_contact_solved=False,pipe_wall_or_pressure_effects_inferred=False,
        numerical_validation_completed=False,field_validation_claim=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("elastic span result exceeds its retained byte budget")
    return body


def verify_elastic_pull_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("elastic span receipt changed")
    q=ElasticPullRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("elastic span caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("elastic span caller context differs")
    if evaluate_elastic_pull(q,c)!=record:
        raise ValueError("elastic span native catenary/work replay differs")
    return True


def prepare_elastic_span_guide_pull(record, template, context: RunContext, *, state_index: int,
                                    mapping_evidence_id: str, static_effective_tension_transfer_reviewed=False):
    """Transmit a collinear free-span end tension to the original guide solver.

    This prepares a static force boundary. Endpoint-motion work does not become
    material-throughput power, and the guide does not inherit a finite-EI law.
    """
    from .pulling import PullingRequest, boundary_metadata
    verify_elastic_pull_record(record)
    if record["status"]!="computed_diagnostic" or not static_effective_tension_transfer_reviewed:
        raise ValueError("guide transfer requires an accepted static span and reviewed effective-tension meaning")
    source=ElasticPullRequest.model_validate_json(json.dumps(record["request"]))
    original=RunContext.model_validate_json(json.dumps(record["context"]))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    require_context_extension(original,c)
    q=PullingRequest.model_validate(template.model_dump(mode="python"))
    evidence={e.id:e for e in c.evidence}
    if mapping_evidence_id not in evidence or evidence[mapping_evidence_id].sha256 is None:
        raise ValueError("elastic-span/guide transfer requires a source-pinned mapping review")
    if type(state_index) is not int or not 0<=state_index<len(record["states"]):
        raise ValueError("select an actual retained elastic-span state")
    row=record["states"][state_index]
    if (q.asset_id,q.clock_id,q.frame_id,q.coordinate_definition_id,q.trailing_port_id,q.time_s)!=(
        source.asset_id,source.clock_id,source.frame_id,source.coordinate_definition_id,source.end_port_id,row["time_s"]):
        raise ValueError("elastic-span/guide asset, clock, frame, port or snapshot time differs")
    force=row["force_on_end_support_N"]
    tension=math.hypot(*force)
    tangent=tuple(-v/tension for v in force)
    if math.dist(q.segments[0].start_m,row["end_position_m"])>q.position_tolerance_m or math.dist(q.segments[0].initial_tangent,tangent)>q.tangent_tolerance:
        raise ValueError("free span and prescribed guide do not meet in position and tangent; contact forces are unresolved")
    derived=PullingRequest.model_validate({**q.model_dump(mode="python"),"trailing_tension_N":tension})
    metadata={**c.metadata,"pulling_boundary":boundary_metadata(derived),"elastic_span_guide_transfer":dict(
        source_record_digest=record["record_digest"],state_index=state_index,mapping_evidence_id=mapping_evidence_id,
        effective_trailing_tension_N=tension,source_time_s=row["time_s"],source_port_id=source.end_port_id)}
    bound=RunContext.model_validate({**c.model_dump(mode="python"),"metadata":metadata})
    receipt=json_value(dict(schema="pipeline_integrity.elastic_span_guide_transfer.v1",source_record_digest=record["record_digest"],
        template_digest=digest(q),derived_request_digest=digest(derived),context_digest=digest(bound),
        mapping_evidence_id=mapping_evidence_id,state_index=state_index,
        snapshot_force_transfer_only=True,throughput_power_from_endpoint_work_inferred=False))
    receipt["record_digest"]=digest(receipt)
    return derived,bound,receipt
