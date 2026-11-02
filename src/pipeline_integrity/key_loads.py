"""Nominal tangential force and average key shear/bearing on supplied engagement."""
import math
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Fraction,Calculation

class KeyEngagement(Contract):
    id: Name
    shear_width_m: Positive | None
    engaged_length_m: Positive | None
    actual_bearing_height_m: Positive | None
    torque_share: Fraction | None

class KeyLoadRequest(Contract):
    shaft_diameter_m: Positive | None
    applied_torque_N_m: float | None
    keys: tuple[KeyEngagement,...] = Field(min_length=1,max_length=256)
    nominal_radius_engagement_and_load_sharing_reviewed: bool = False
    @model_validator(mode='after')
    def identities(self):
        if len({k.id for k in self.keys})!=len(self.keys):raise ValueError('key identifiers must be unique')
        return self

def evaluate_key_loads(q: KeyLoadRequest) -> Calculation:
    """Shaft nominal radius converts torque to tangential force; areas are actual.

    Bearing height is engaged contact height, not automatically a whole key height
    or half of it. Shares are caller data; no equal sharing is inferred. These are
    average nominal stresses, not local peaks, material ratings or acceptance.
    """
    q=KeyLoadRequest.model_validate(q.model_dump(mode='python'))
    if not q.nominal_radius_engagement_and_load_sharing_reviewed or None in (q.shaft_diameter_m,q.applied_torque_N_m) or any(None in (k.shear_width_m,k.engaged_length_m,k.actual_bearing_height_m,k.torque_share) for k in q.keys):
        return Calculation(values={},status='needs_data',notes=('complete actual engagement, torque, diameter, shares and nominal model review required',))
    if not math.isclose(math.fsum(k.torque_share for k in q.keys),1.,rel_tol=0.,abs_tol=1e-12):raise ValueError('key torque shares must sum to one without normalization')
    radius=q.shaft_diameter_m/2
    if radius==0:raise ValueError('nominal radius is below finite support')
    force=q.applied_torque_N_m/radius;rows=[]
    for k in q.keys:
        a=k.shear_width_m*k.engaged_length_m;b=k.actual_bearing_height_m*k.engaged_length_m
        if min(a,b)<=0 or not all(math.isfinite(x) for x in (a,b)):raise ValueError('key stress areas exceed positive finite support')
        f=force*k.torque_share;shear=f/a;bearing=abs(f)/b
        if not all(math.isfinite(x) for x in (f,shear,bearing)):raise ValueError('key nominal stress exceeds finite support')
        rows.append(dict(key_id=k.id,signed_tangential_force_N=f,nominal_signed_shear_stress_Pa=shear,nominal_bearing_magnitude_Pa=bearing))
    error=math.fsum(x['signed_tangential_force_N']*radius for x in rows)-q.applied_torque_N_m
    return Calculation(values=dict(total_signed_tangential_force_N=force,key_results=rows,torque_budget_residual_N_m=error))
