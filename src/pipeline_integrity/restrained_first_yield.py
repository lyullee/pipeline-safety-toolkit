"""Pressure-only first elastic J2 yield with prescribed zero axial strain."""
import math
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,RunContext,digest
from .fully_restrained_cylinder import RestrainedCylinderRequest,evaluate_fully_restrained_cylinder


class RestrainedFirstYieldRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    inner_radius_m: Positive
    outer_radius_m: Positive
    young_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=.5)
    supplied_uniaxial_yield_strength_pa: Positive | None = None
    material_evidence_id: Name
    pressure_only_zero_axial_strain_isotropic_j2_basis_reviewed: bool = False

    @model_validator(mode='after')
    def geometry(self):
        if self.outer_radius_m<=self.inner_radius_m:raise ValueError('positive annular wall required')
        return self


def evaluate_restrained_first_yield(q: RestrainedFirstYieldRequest,c: RunContext,*,client=None):
    """Scale the existing constrained elastic field to first bore J2 yield.

    Uniform temperature change and exterior traction are zero. With pressure
    alone, every stress scales linearly and the squared J2 stress decreases
    outward: its radius-dependent term is three times the squared Lame B/r².
    This is an explicit isotropic elastic substitute, not a fitted material
    ellipse, plastic expansion, proof-test acceptance or soil-restraint model.
    """
    q=RestrainedFirstYieldRequest.model_validate(q.model_dump());c=RunContext.model_validate(c.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('request/context identity differs')
    if q.material_evidence_id not in {e.id for e in c.evidence}:raise ValueError('material evidence absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),axial_strain=0.,
        yield_basis='isotropic von Mises first elastic yield',field_or_test_acceptance_qualified=False)
    if not q.pressure_only_zero_axial_strain_isotropic_j2_basis_reviewed or q.supplied_uniaxial_yield_strength_pa is None:
        return dict(status='needs_data',**base)
    def field(p):
        return evaluate_fully_restrained_cylinder(RestrainedCylinderRequest(asset_id=q.asset_id,location_id=q.location_id,
            clock_id=q.clock_id,inner_radius_m=q.inner_radius_m,outer_radius_m=q.outer_radius_m,
            internal_pressure_relative_zero_exterior_pa=p,elastic_modulus_pa=q.young_modulus_pa,
            poisson_ratio=q.poisson_ratio,expansion_coefficient_per_k=0.,uniform_temperature_change_k=0.,
            material_evidence_id=q.material_evidence_id,isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed=True),c,client=client)
    unit=field(1.)
    if unit['status']!='computed':return dict(status='solver_withheld',**base)
    def stresses(r):
        native=r['native_pressure_receipt']['native']['payload']['values']
        return native['radial_stress_Pa'],native['hoop_stress_Pa'],r['axial_stress_tension_positive_pa']
    sr,sh,sz=stresses(unit)
    vm=math.hypot(sr-sh,sh-sz,sz-sr)/math.sqrt(2.)
    p=q.supplied_uniaxial_yield_strength_pa/vm
    if not math.isfinite(p) or p<=0:raise ValueError('pressure exceeds finite support')
    threshold=field(p)
    if threshold['status']!='computed':return dict(status='solver_withheld',**base)
    sr,sh,sz=stresses(threshold)
    return dict(status='computed',**base,internal_pressure_relative_zero_exterior_pa=p,
        first_yield_radius_m=q.inner_radius_m,bore_radial_stress_pa=sr,bore_hoop_stress_pa=sh,
        bore_axial_stress_pa=sz,bore_von_mises_stress_pa=math.hypot(sr-sh,sh-sz,sz-sr)/math.sqrt(2.),
        elastic_field_receipt=threshold)
