"""Directional point-light contributions at one explicitly oriented surface point."""
import math
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Fraction,Calculation

class PointLight(Contract):
    id: Name
    position_m: tuple[float,float,float]
    directional_luminous_intensity_cd: Nonnegative | None
    transmission_fraction: Fraction | None

class PointIlluminanceRequest(Contract):
    surface_point_m: tuple[float,float,float]
    outward_surface_normal: tuple[float,float,float]
    sources: tuple[PointLight,...] = Field(min_length=1,max_length=4096)
    point_source_far_field_and_directional_intensities_reviewed: bool = False
    @model_validator(mode='after')
    def unique(self):
        if len({s.id for s in self.sources})!=len(self.sources):raise ValueError('light identifiers must be unique')
        return self

def evaluate_point_illuminance(q: PointIlluminanceRequest) -> Calculation:
    """Sum I(direction)*transmission*cos(incidence)/distance**2 in lux.

    The normal selects the illuminated side; rear-facing sources contribute zero.
    Directional intensities and transmission are caller observations or reviewed
    photometric data. No beam distribution, occlusion scene, reflection, lamp
    aging, required illumination level or luminaire count is estimated.
    """
    q=PointIlluminanceRequest.model_validate(q.model_dump(mode='python'))
    normal=math.hypot(*q.outward_surface_normal)
    if normal==0 or not math.isfinite(normal):raise ValueError('surface normal must have a finite representable direction')
    if not q.point_source_far_field_and_directional_intensities_reviewed or any(s.directional_luminous_intensity_cd is None or s.transmission_fraction is None for s in q.sources):
        return Calculation(values={},status='needs_data',notes=('directional intensities, transmission and far-field review required',))
    rows=[]
    for s in q.sources:
        delta=tuple(a-b for a,b in zip(s.position_m,q.surface_point_m));r=math.hypot(*delta)
        if r==0 or not math.isfinite(r):raise ValueError('point source separation must be finite and nonsingular')
        cosine=math.fsum((x/normal)*(y/r) for x,y in zip(q.outward_surface_normal,delta));cosine=max(0.,min(1.,cosine))
        lux=(s.directional_luminous_intensity_cd/r)/r*s.transmission_fraction*cosine
        if not math.isfinite(lux):raise ValueError('illuminance exceeds finite numerical support')
        rows.append(dict(source_id=s.id,distance_m=r,front_face_incidence_cosine=cosine,illuminance_lux=lux))
    total=math.fsum(x['illuminance_lux'] for x in rows)
    if not math.isfinite(total):raise ValueError('total illuminance exceeds finite support')
    return Calculation(values=dict(total_illuminance_lux=total,source_contributions=rows))
