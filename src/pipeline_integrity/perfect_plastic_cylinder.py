"""Associated-J2 full-wall plastic pressure limit in fixed annular geometry."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Positive


class PerfectPlasticCylinderRequest(Contract):
    axial_condition: Literal['closed_ends_zero_axial_plastic_strain','open_ends','imposed_axial_load']
    inside_radius_m: Positive | None = None
    outside_radius_m: Positive | None = None
    yield_stress_pa: Positive | None = None
    sample_radii_m: tuple[Positive,...] = ()
    homogeneous_isotropic_associated_j2_fixed_geometry_reviewed: bool = False

    @model_validator(mode='after')
    def geometry(self):
        if self.inside_radius_m is not None and self.outside_radius_m is not None:
            if self.inside_radius_m>=self.outside_radius_m:raise ValueError('a positive annular wall is required')
            if any(r<self.inside_radius_m or r>self.outside_radius_m for r in self.sample_radii_m):
                raise ValueError('all requested sample radii must lie in the annulus')
        return self


class PlasticCylinderStressPoint(Contract):
    radius_m: Positive
    radial_stress_pa: float
    hoop_stress_pa: float
    axial_stress_pa: float


class PerfectPlasticCylinderResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    full_wall_plastic_pressure_difference_pa: Positive | None = None
    stress_points: tuple[PlasticCylinderStressPoint,...] = ()
    issues: tuple[str,...] = ()
    stress_reference: Literal['relative_to_uniform_exterior_hydrostatic_stress'] = 'relative_to_uniform_exterior_hydrostatic_stress'
    yield_criterion: Literal['isotropic_associated_J2_perfect_plasticity'] = 'isotropic_associated_J2_perfect_plasticity'
    elastic_first_yield_hardening_or_finite_deformation_burst_qualified: Literal[False] = False
    external_pressure_buckling_or_fracture_qualified: Literal[False] = False


def evaluate_perfect_plastic_cylinder(request: PerfectPlasticCylinderRequest) -> PerfectPlasticCylinderResult:
    """Return a limit load and its statically admissible full-plastic stress field.

    The annulus is long, homogeneous and perfectly plastic with associated J2
    flow. Closed ends carry pressure thrust with zero axial plastic strain rate.
    Fixed reference geometry excludes finite-strain pressure/diameter evolution.
    Stresses are relative to the exterior hydrostatic stress: radial traction is
    zero at the outside and minus the pressure difference at the bore. Uniform
    exterior pressure can be restored as a common compressive stress shift.
    No hardening curve, rupture strain, fracture limit or design factor is chosen.
    """
    if not isinstance(request,PerfectPlasticCylinderRequest):raise TypeError('PerfectPlasticCylinderRequest required')
    q=PerfectPlasticCylinderRequest.model_validate(request.model_dump(mode='python'))
    missing=['missing '+k for k in ('inside_radius_m','outside_radius_m','yield_stress_pa') if getattr(q,k) is None]
    if not q.homogeneous_isotropic_associated_j2_fixed_geometry_reviewed:
        missing.append('homogeneous associated-J2 fixed-geometry idealization requires review')
    if missing:return PerfectPlasticCylinderResult(status='needs_data',issues=tuple(missing))
    if q.axial_condition!='closed_ends_zero_axial_plastic_strain':
        return PerfectPlasticCylinderResult(status='out_of_scope',issues=('the full-plastic branch requires closed-cap pressure thrust and zero axial plastic strain rate',))
    ri=q.inside_radius_m;ro=q.outside_radius_m
    coefficient=2/math.sqrt(3)*q.yield_stress_pa
    relative_wall=(ro-ri)/ri
    log_ratio=math.log1p(relative_wall) if math.isfinite(relative_wall) else math.log(ro)-math.log(ri)
    pressure=coefficient*log_ratio
    points=[]
    for r in sorted({ri,ro,*q.sample_radii_m}):
        relative=(r-ro)/ro
        log_fraction=math.log1p(relative) if relative>-1 else math.log(r)-math.log(ro)
        radial=coefficient*log_fraction
        hoop=radial+coefficient;axial=radial+coefficient/2
        if not all(math.isfinite(v) for v in (radial,hoop,axial)):
            raise ValueError('plastic stress field exceeds finite numerical support')
        points.append(PlasticCylinderStressPoint(radius_m=r,radial_stress_pa=radial,hoop_stress_pa=hoop,axial_stress_pa=axial))
    if not math.isfinite(pressure) or pressure<=0:raise ValueError('plastic pressure limit exceeds positive finite numerical support')
    return PerfectPlasticCylinderResult(status='computed',full_wall_plastic_pressure_difference_pa=pressure,stress_points=tuple(points))
