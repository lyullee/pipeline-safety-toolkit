"""Linear clamped circular plate under uniform differential pressure."""
import math
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Calculation

class ClampedCircularPlateRequest(Contract):
    support_radius_m: Positive
    effective_thickness_m: Positive
    differential_pressure_Pa: float
    elastic_modulus_Pa: Positive | None
    poisson_ratio: float | None = Field(gt=-1,lt=.5)
    query_radii_m: tuple[Nonnegative,...] = Field(default=(),max_length=4096)
    maximum_reviewed_thickness_to_radius: Positive | None
    maximum_reviewed_center_deflection_to_thickness: Positive | None
    uniform_pressure_linear_elastic_small_deflection_clamp_reviewed: bool = False

def evaluate_clamped_circular_plate(q: ClampedCircularPlateRequest) -> Calculation:
    """Kirchhoff plate displacement and two surface bending stress observations.

    The support radius is the actual ideal clamped circle, not an automatic
    nominal pipe size or gasket radius. Pressure and displacement share a signed
    load direction. Thickness is the effective material section. No allowable
    stress, corrosion allowance, plastic strength, gasket behavior or code rating
    is chosen by this elastic boundary-value model.
    """
    q=ClampedCircularPlateRequest.model_validate(q.model_dump(mode='python'))
    a=q.support_radius_m;t=q.effective_thickness_m
    if any(r>a for r in q.query_radii_m):raise ValueError('observation radius lies outside the supported plate')
    if not q.uniform_pressure_linear_elastic_small_deflection_clamp_reviewed or None in (q.elastic_modulus_Pa,q.poisson_ratio,q.maximum_reviewed_thickness_to_radius,q.maximum_reviewed_center_deflection_to_thickness):
        return Calculation(values={},status='needs_data',notes=('material state, clamp and explicit thin/small-deflection support reviews required',))
    D=q.elastic_modulus_Pa*t*t*t/(12*(1-q.poisson_ratio*q.poisson_ratio))
    if not math.isfinite(D) or D<=0:raise ValueError('plate rigidity exceeds positive finite support')
    p=q.differential_pressure_Pa;aa=a*a;w0=p*aa*aa/(64*D)
    if not math.isfinite(w0):raise ValueError('plate deflection exceeds finite support')
    if t/a>q.maximum_reviewed_thickness_to_radius or abs(w0)/t>q.maximum_reviewed_center_deflection_to_thickness:
        return Calculation(values={},status='out_of_scope',notes=('plate thickness or deflection exceeds the caller reviewed linear thin-plate support',))
    rows=[]
    for r in q.query_radii_m:
        rr=r*r;nu=q.poisson_ratio
        mr=p*((1+nu)*aa-(3+nu)*rr)/16;mt=p*((1+nu)*aa-(1+3*nu)*rr)/16
        rows.append(dict(radius_m=r,displacement_along_load_m=p*(aa-rr)*(aa-rr)/(64*D),radial_bending_moment_N=mr,circumferential_bending_moment_N=mt,
            selected_face_radial_bending_stress_Pa=6*mr/(t*t),selected_face_circumferential_bending_stress_Pa=6*mt/(t*t)))
    vals=dict(flexural_rigidity_N_m=D,center_displacement_along_load_m=w0,maximum_surface_normal_bending_stress_magnitude_Pa=.75*abs(p)*aa/(t*t),
        edge_radial_shear_along_load_N_m=-p*a/2,total_pressure_force_along_load_N=p*math.pi*aa,total_support_reaction_along_load_N=-p*math.pi*aa,observations=rows)
    if not all(math.isfinite(x) for x in vals.values() if isinstance(x,(int,float))) or any(not math.isfinite(x) for row in rows for x in row.values()):raise ValueError('plate result exceeds finite support')
    return Calculation(values=vals,notes=('Opposite-face bending stresses have the opposite sign. No through-thickness transverse shear stress or material failure criterion is inferred.',))

class ClampedPlateStressCeilingRequest(Contract):
    support_radius_m: Positive
    differential_pressure_Pa: float
    supplied_nominal_bending_stress_ceiling_Pa: Positive
    additional_nonstructural_thickness_m: Nonnegative
    clamped_uniform_load_and_nominal_stress_basis_reviewed: bool = False

def invert_clamped_plate_stress_ceiling(q: ClampedPlateStressCeilingRequest) -> Calculation:
    """Invert the elastic edge bending moment for a supplied nominal stress ceiling.

    The additive thickness is caller data. This algebraic result still requires
    thin-plate/small-deflection/material/support review through the response API;
    it is not a pressure rating or manufacturing minimum.
    """
    q=ClampedPlateStressCeilingRequest.model_validate(q.model_dump(mode='python'))
    if not q.clamped_uniform_load_and_nominal_stress_basis_reviewed:return Calculation(values={},status='needs_data',notes=('clamp and supplied nominal stress basis require review',))
    t=q.support_radius_m*math.sqrt(.75*abs(q.differential_pressure_Pa)/q.supplied_nominal_bending_stress_ceiling_Pa)
    total=t+q.additional_nonstructural_thickness_m
    if not all(math.isfinite(x) for x in (t,total)):raise ValueError('inverted plate thickness exceeds finite support')
    return Calculation(values=dict(algebraic_effective_thickness_m=t,total_with_supplied_additional_thickness_m=total),notes=('Zero pressure gives zero algebraic stress-driven thickness only; a physical plate cannot be inferred from this zero-load limit.',))
