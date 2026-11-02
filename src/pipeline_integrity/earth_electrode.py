"""Uniform-leakage slender line electrode averages in homogeneous ohmic soil.

The radius-regularized line self term is an approximation, not an exact finite
metal surface equilibrium. A common bus equates rod-averaged potentials while
each rod retains prescribed uniform axial leakage. Backfill and polarization
are not inferred from an electrode radius or an electrical connection.
"""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive
from .ground_halfspace import (GroundHalfspaceRequest,SurfacePointCurrent,GroundObservationPoint,
    GroundPointField,evaluate_ground_halfspace)


class SlenderEarthElectrode(Contract):
    id: Name
    start_xyz_depth_m: tuple[float,float,float]
    axis: Literal['vertical','horizontal']
    horizontal_direction_xy: tuple[float,float] = (1.,0.)
    length_m: Positive
    radius_m: Positive
    signed_current_into_ground_A: float | None = None

    @model_validator(mode='after')
    def geometry(self):
        if self.radius_m/self.length_m>.1:raise ValueError('line approximation requires radius/length<=0.1')
        if self.start_xyz_depth_m[2]<0:raise ValueError('electrodes must lie in the conducting half-space')
        if self.axis=='horizontal' and self.start_xyz_depth_m[2]<self.radius_m:raise ValueError('horizontal electrode core must be buried')
        if not math.isclose(math.hypot(*self.horizontal_direction_xy),1.,rel_tol=1e-12):raise ValueError('horizontal direction must be a unit vector')
        return self


class EarthElectrodeRequest(Contract):
    uniform_resistivity_ohm_m: Positive | None
    electrodes: tuple[SlenderEarthElectrode,...] = Field(min_length=1,max_length=64)
    mode: Literal['prescribed_currents','rod_average_common_bus']
    total_signed_current_A: float | None = None
    observations: tuple[GroundObservationPoint,...] = ()
    coordinate_frame_id: Name
    uniform_leakage_slender_line_and_homogeneous_soil_reviewed: bool = False
    current_return_at_infinity_reviewed: bool = False
    quadrature_relative_tolerance: Positive = Field(default=1e-7,le=1e-2)
    maximum_quadrature_order: int = Field(default=128,ge=16,le=256)


class EarthElectrodeResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    coordinate_frame_id: Name
    resistance_matrix_ohm: tuple[tuple[float,...],...] = ()
    signed_currents_A: tuple[float,...] = ()
    rod_average_potentials_V: tuple[float,...] = ()
    common_bus_resistance_ohm: Positive | None = None
    common_bus_potential_V: float | None = None
    model_ohmic_power_W: float | None = None
    fields: tuple[GroundPointField,...] = ()
    mutual_quadrature_order: int | None = None
    field_quadrature_order: int | None = None
    quadrature_converged: bool = False
    issues: tuple[str,...] = ()
    exact_finite_metal_surface_equipotential: Literal[False] = False
    electrode_polarization_or_backfill_inferred: Literal[False] = False


def _direction(rod):return np.array((0.,0.,1.) if rod.axis=='vertical' else (*rod.horizontal_direction_xy,0.))

def _line_average(length,separation):
    # Cancellation-safe double integral of a parallel softened 1/r kernel.
    return 2/length**2*(length*math.asinh(length/separation)-length**2/(math.hypot(length,separation)+separation))

def _closest_distance(a,b):
    p=np.array(a.start_xyz_depth_m);q=np.array(b.start_xyz_depth_m);u=_direction(a);v=_direction(b)
    values=[]
    for x in (p,p+a.length_m*u):
        y=q+np.clip(np.dot(x-q,v),0,b.length_m)*v;values.append(float(np.linalg.norm(x-y)))
    for y in (q,q+b.length_m*v):
        x=p+np.clip(np.dot(y-p,u),0,a.length_m)*u;values.append(float(np.linalg.norm(x-y)))
    matrix=np.column_stack((u,-v))
    if np.linalg.matrix_rank(matrix)==2:
        s,t=np.linalg.lstsq(matrix,q-p,rcond=None)[0]
        if 0<=s<=a.length_m and 0<=t<=b.length_m:values.append(float(np.linalg.norm(p+s*u-q-t*v)))
    return min(values)


def evaluate_earth_electrodes(q:EarthElectrodeRequest) -> EarthElectrodeResult:
    """Integrate the existing surface Green scale and solve rod-average coupling."""
    if not isinstance(q,EarthElectrodeRequest):raise TypeError('EarthElectrodeRequest required')
    q=EarthElectrodeRequest.model_validate(q.model_dump(mode='python'));base=dict(coordinate_frame_id=q.coordinate_frame_id)
    rods=q.electrodes;n=len(rods)
    if len({x.id for x in rods})!=n or len({x.id for x in q.observations})!=len(q.observations):raise ValueError('electrode/observation identities must be unique')
    if q.uniform_resistivity_ohm_m is None or not q.uniform_leakage_slender_line_and_homogeneous_soil_reviewed:
        return EarthElectrodeResult(status='needs_data',issues=('known homogeneous soil resistivity and reviewed slender uniform-leakage approximation required',),**base)
    if q.mode=='rod_average_common_bus':
        if q.total_signed_current_A is None or any(x.signed_current_into_ground_A is not None for x in rods):
            return EarthElectrodeResult(status='needs_data',issues=('common-bus mode needs total current and no imposed per-rod currents',),**base)
        net=q.total_signed_current_A
    else:
        if q.total_signed_current_A is not None:raise ValueError('prescribed currents must not be mixed with total-current control')
        if any(x.signed_current_into_ground_A is None for x in rods):return EarthElectrodeResult(status='needs_data',issues=('all rod currents required',),**base)
        net=math.fsum(x.signed_current_into_ground_A for x in rods)
    if (net!=0 or q.mode=='rod_average_common_bus') and not q.current_return_at_infinity_reviewed:
        return EarthElectrodeResult(status='needs_data',issues=('remote current return and infinity potential reference require review',),**base)
    for i,a in enumerate(rods):
        for b in rods[:i]:
            if _closest_distance(a,b)<=a.radius_m+b.radius_m:raise ValueError('electrode cores must not intersect or touch')
    # A one-metre, one-ampere kernel probe delegates the common rho/(2*pi)
    # normalization to the existing half-space Green owner. Image terms below
    # each carry half this scale, rather than introducing another soil law.
    probe=evaluate_ground_halfspace(GroundHalfspaceRequest(uniform_resistivity_ohm_m=q.uniform_resistivity_ohm_m,
        coordinate_frame_id=q.coordinate_frame_id,sources=(SurfacePointCurrent(id='kernel',horizontal_position_m=(0.,0.),signed_current_into_ground_A=1.),),
        observations=(GroundObservationPoint(id='unit-distance',position_xyz_depth_m=(1.,0.,0.)),),
        homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed=True,net_current_return_at_infinity_reviewed=True))
    scale=.5*probe.fields[0].potential_V_relative_infinity
    def samples(rod,order):
        x,w=np.polynomial.legendre.leggauss(order)
        return np.array(rod.start_xyz_depth_m)+(x[:,None]+1)*(.5*rod.length_m)*_direction(rod),w*.5
    def matrix(order):
        data=[samples(rod,order) for rod in rods];out=np.zeros((n,n))
        for i,rod in enumerate(rods):
            L=rod.length_m;a=rod.radius_m;h=rod.start_xyz_depth_m[2]
            direct=_line_average(L,a)
            if rod.axis=='horizontal':image=_line_average(L,math.hypot(a,2*h))
            elif h<=10*L:
                def H(z):return z*math.asinh(z/a)-math.hypot(z,a)
                image=(H(2*h+2*L)-2*H(2*h+L)+H(2*h))/L**2
            else:
                p,w=data[i];distance=p[:,None,2]+p[None,:,2]
                image=float(np.sum(w[:,None]*w[None,:]/np.hypot(distance,a)))
            out[i,i]=scale*(direct+image)
            p,w=data[i]
            for j in range(i):
                t,v=data[j];difference=p[:,None,:]-t[None,:,:];mirror=t.copy();mirror[:,2]*=-1
                radii=np.linalg.norm(difference,axis=2);images=np.linalg.norm(p[:,None,:]-mirror[None,:,:],axis=2)
                out[i,j]=out[j,i]=scale*float(np.sum(w[:,None]*v[None,:]*(1/radii+1/images)))
        return out
    previous=matrix(8);order=16;converged=False
    while order<=q.maximum_quadrature_order:
        resistance=matrix(order)
        if np.allclose(resistance,previous,rtol=q.quadrature_relative_tolerance,atol=0.):converged=True;break
        previous=resistance;order*=2
    if not converged:return EarthElectrodeResult(status='out_of_scope',issues=('mutual/self-image quadrature did not meet the declared relative tolerance',),**base)
    if np.min(np.linalg.eigvalsh(resistance))<=0 or np.linalg.cond(resistance)>1e12:
        return EarthElectrodeResult(status='out_of_scope',issues=('uniform-leakage resistance matrix is not a resolved positive-definite operator',),**base)
    busR=busV=None
    if q.mode=='rod_average_common_bus':
        conductance=np.linalg.solve(resistance,np.ones(n));busR=1/float(np.sum(conductance));busV=net*busR;currents=busV*conductance
        if net!=0 and np.min(currents/net)<-1e-12:
            return EarthElectrodeResult(status='out_of_scope',issues=('uniform-leakage common-bus approximation produces reversed electrode injection',),**base)
    else:currents=np.array([x.signed_current_into_ground_A for x in rods])
    potentials=resistance@currents;fields=[];field_order=None
    if q.observations:
        positions=np.array([p.position_xyz_depth_m for p in q.observations])
        if np.any(positions[:,2]<0):raise ValueError('observations must lie in the conducting half-space')
        for rod in rods:
            start=np.array(rod.start_xyz_depth_m);direction=_direction(rod)
            projection=np.clip((positions-start)@direction,0,rod.length_m)
            if np.any(np.linalg.norm(positions-start-projection[:,None]*direction,axis=1)<=rod.radius_m):raise ValueError('ground observations must remain outside electrode cores')
        def field_at(resolution):
            potential=np.zeros(len(positions));gradient=np.zeros_like(positions)
            for rod,current in zip(rods,currents):
                points,weights=samples(rod,resolution)
                for image in (False,True):
                    sources=points.copy()
                    if image:sources[:,2]*=-1
                    delta=positions[:,None,:]-sources[None,:,:];distance=np.linalg.norm(delta,axis=2)
                    potential+=scale*current*np.sum(weights[None,:]/distance,axis=1)
                    gradient+=scale*current*np.sum(weights[None,:,None]*delta/distance[:,:,None]**3,axis=1)
            return potential,gradient
        previous_field=field_at(8);field_order=16;field_ok=False
        while field_order<=q.maximum_quadrature_order:
            phi,electric=field_at(field_order)
            if all(np.allclose(new,old,rtol=q.quadrature_relative_tolerance,
                    atol=q.quadrature_relative_tolerance*float(np.max(np.abs(new)))*1e-6)
                    for new,old in zip((phi,electric),previous_field)):
                field_ok=True;break
            previous_field=(phi,electric);field_order*=2
        if not field_ok:return EarthElectrodeResult(status='out_of_scope',issues=('observation field quadrature did not meet declared relative tolerance',),**base)
        fields=[GroundPointField(id=p.id,potential_V_relative_infinity=float(phi[i]),electric_field_xyz_depth_V_m=tuple(electric[i]),current_density_xyz_depth_A_m2=tuple(electric[i]/q.uniform_resistivity_ohm_m)) for i,p in enumerate(q.observations)]
    return EarthElectrodeResult(status='computed',resistance_matrix_ohm=tuple(map(tuple,resistance)),signed_currents_A=tuple(currents),
        rod_average_potentials_V=tuple(potentials),common_bus_resistance_ohm=busR,common_bus_potential_V=busV,
        model_ohmic_power_W=float(currents@potentials),fields=tuple(fields),mutual_quadrature_order=order,
        field_quadrature_order=field_order,quadrature_converged=True,**base)
