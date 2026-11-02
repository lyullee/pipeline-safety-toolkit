"""Elastic equal-stiffness planar weld-line force/moment distribution."""
import math
import numpy as np
from pydantic import Field
from .contracts import Contract,Positive

class WeldLineSegment(Contract):
    start_m: tuple[float,float]
    end_m: tuple[float,float]

class WeldLineGroupRequest(Contract):
    segments: tuple[WeldLineSegment,...] = Field(min_length=2,max_length=1000)
    force_n: tuple[float,float,float] | None = None
    moment_about_line_centroid_nm: tuple[float,float,float] | None = None
    uniform_effective_throat_m: Positive | None = None
    planar_equal_stiffness_elastic_line_group_reviewed: bool = False

def evaluate_weld_line_group(request: WeldLineGroupRequest):
    """Return nominal line tractions, never a local toe stress or code allowable.

    Coordinates are x/y in the weld plane; positive z is normal to it. Moments
    use the right-hand convention about the line centroid. Equal weld stiffness
    per length and rigid connected plate define the elastic distribution.
    Segment endpoints are enough for componentwise linear traction fields.
    Overlapping/duplicated lines must be resolved by the caller before review.
    """
    q=WeldLineGroupRequest.model_validate(request.model_dump())
    if not q.planar_equal_stiffness_elastic_line_group_reviewed or q.force_n is None or q.moment_about_line_centroid_nm is None:
        return dict(status='needs_data')
    if not all(math.isfinite(v) for s in q.segments for p in (s.start_m,s.end_m) for v in p) or not all(math.isfinite(v) for v in (*q.force_n,*q.moment_about_line_centroid_nm)):
        raise ValueError('finite geometry and resultants required')
    lengths=np.array([math.dist(s.start_m,s.end_m) for s in q.segments])
    if np.any(lengths<=0):raise ValueError('positive segment lengths required')
    starts=np.array([s.start_m for s in q.segments]);ends=np.array([s.end_m for s in q.segments])
    length=float(sum(lengths));centroid=np.sum(lengths[:,None]*(starts+ends)/2,axis=0)/length
    # Exact second moments of linear segment coordinates by two Gauss points.
    gauss=(.5-.5/math.sqrt(3),.5+.5/math.sqrt(3));second=np.zeros((2,2))
    for start,end,l in zip(starts,ends,lengths):
        for xi in gauss:
            r=start+(end-start)*xi-centroid;second+=l/2*np.outer(r,r)
    if np.linalg.eigvalsh(second)[0]<=1e-12*np.linalg.eigvalsh(second)[1]:
        raise ValueError('nondegenerate two-dimensional line group required')
    fx,fy,fz=q.force_n;mx,my,mz=q.moment_about_line_centroid_nm
    ix,iy,ixy=second[1,1],second[0,0],second[0,1];polar=ix+iy
    a,b=np.linalg.solve(np.array([[ixy,ix],[-iy,-ixy]]),np.array([mx,my]))
    def traction(point):
        x,y=np.asarray(point)-centroid
        return (float(fx/length-mz*y/polar),float(fy/length+mz*x/polar),float(fz/length+a*x+b*y))
    values=[(traction(s.start_m),traction(s.end_m)) for s in q.segments]
    result=dict(status='computed',line_length_m=length,line_centroid_m=tuple(float(v) for v in centroid),
        line_second_moment_m3=((float(second[0,0]),float(second[0,1])),(float(second[1,0]),float(second[1,1]))),
        endpoint_traction_n_m=tuple(values),maximum_endpoint_resultant_n_m=max(math.hypot(*p) for pair in values for p in pair),
        local_weld_toe_stress_contact_or_design_acceptance_calculated=False)
    if q.uniform_effective_throat_m is not None:
        result['maximum_nominal_resultant_stress_pa']=result['maximum_endpoint_resultant_n_m']/q.uniform_effective_throat_m
    return result
