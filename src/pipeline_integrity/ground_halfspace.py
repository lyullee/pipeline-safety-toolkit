"""Unperturbed potential of surface point currents in a uniform conducting half-space."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Positive


class SurfacePointCurrent(Contract):
    id: Name
    horizontal_position_m: tuple[float,float]
    signed_current_into_ground_A: float | None


class GroundObservationPoint(Contract):
    id: Name
    position_xyz_depth_m: tuple[float,float,float]


class GroundHalfspaceRequest(Contract):
    uniform_resistivity_ohm_m: Positive | None
    coordinate_frame_id: Name
    sources: tuple[SurfacePointCurrent,...] = Field(min_length=1)
    observations: tuple[GroundObservationPoint,...] = Field(min_length=1)
    absolute_current_balance_tolerance_A: float = Field(default=0.,ge=0.)
    homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed: bool = False
    net_current_return_at_infinity_reviewed: bool = False


class GroundPointField(Contract):
    id: Name
    potential_V_relative_infinity: float
    electric_field_xyz_depth_V_m: tuple[float,float,float]
    current_density_xyz_depth_A_m2: tuple[float,float,float]


class GroundHalfspaceResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    coordinate_frame_id: Name
    fields: tuple[GroundPointField,...] = ()
    net_current_into_halfspace_A: float | None = None
    issues: tuple[str,...] = ()
    pipeline_or_electrode_potential_inferred: Literal[False] = False


def evaluate_ground_halfspace(q: GroundHalfspaceRequest) -> GroundHalfspaceResult:
    """Apply the half-space Laplace Green function and linear field superposition.

    Depth is positive into the conductor. Source current is positive into ground;
    a negative point is a return electrode. Infinity defines the common voltage
    reference. A nonzero net source requires a deliberately reviewed return at
    infinity, rather than silently dropping an unknown return path. Observed
    ground potential is not the potential of a coupled metallic pipeline.
    """
    if not isinstance(q,GroundHalfspaceRequest):raise TypeError('GroundHalfspaceRequest required')
    q=GroundHalfspaceRequest.model_validate(q.model_dump(mode='python'))
    base=dict(coordinate_frame_id=q.coordinate_frame_id)
    if len({s.id for s in q.sources})!=len(q.sources) or len({p.id for p in q.observations})!=len(q.observations):
        raise ValueError('source and observation identities must be unique within each set')
    if any(p.position_xyz_depth_m[2]<0 for p in q.observations):raise ValueError('observation depth must lie in the conducting half-space')
    if (q.uniform_resistivity_ohm_m is None
        or any(s.signed_current_into_ground_A is None for s in q.sources)
        or not q.homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed):
        return GroundHalfspaceResult(status='needs_data',**base,issues=('known uniform resistivity, all source currents and half-space point-source review required',))
    net=math.fsum(s.signed_current_into_ground_A for s in q.sources)
    if abs(net)>q.absolute_current_balance_tolerance_A and not q.net_current_return_at_infinity_reviewed:
        return GroundHalfspaceResult(status='needs_data',**base,net_current_into_halfspace_A=net,
            issues=('net point-source current requires explicit return-at-infinity review or complete finite return electrodes',))
    fields=[]
    for point in q.observations:
        potential=[];components=[[],[],[]]
        for source in q.sources:
            if source.signed_current_into_ground_A==0:continue
            dx=point.position_xyz_depth_m[0]-source.horizontal_position_m[0]
            dy=point.position_xyz_depth_m[1]-source.horizontal_position_m[1]
            dz=point.position_xyz_depth_m[2]
            radius=math.hypot(dx,dy,dz)
            if radius==0:
                return GroundHalfspaceResult(status='out_of_scope',**base,net_current_into_halfspace_A=net,
                    issues=('observation at an active point electrode is singular; finite electrode geometry is not inferred',))
            value=(q.uniform_resistivity_ohm_m/(2*math.pi))*(source.signed_current_into_ground_A/radius)
            potential.append(value)
            for terms,delta in zip(components,(dx,dy,dz)):terms.append((value/radius)*(delta/radius))
        v=math.fsum(potential);electric=tuple(math.fsum(values) for values in components)
        current=tuple(e/q.uniform_resistivity_ohm_m for e in electric)
        if not all(math.isfinite(x) for x in (v,*electric,*current)):
            raise ValueError('point-current field exceeds finite numerical support')
        fields.append(GroundPointField(id=point.id,potential_V_relative_infinity=v,
            electric_field_xyz_depth_V_m=electric,current_density_xyz_depth_A_m2=current))
    return GroundHalfspaceResult(status='computed',**base,fields=tuple(fields),net_current_into_halfspace_A=net)
