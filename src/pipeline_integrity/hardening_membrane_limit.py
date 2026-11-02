"""Pressure limit point of a supplied power-hardening membrane idealization."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative


class HardeningMembraneLimitRequest(Contract):
    transformation_model: Literal['j2_plane_strain','supplied_work_conjugate_factor']
    reference_diameter_m: Positive | None = None
    reference_wall_m: Positive | None = None
    strength_coefficient_pa: Positive | None = None
    hardening_exponent: Positive | None = None
    supplied_transformation_factor: Positive | None = None
    transformation_basis_id: Name | None = None
    calibration_basis_id: Name | None = None
    maximum_supported_equivalent_strain: Positive | None = None
    maximum_reviewed_wall_diameter_ratio: Positive | None = None
    diameter_is_reference_membrane_coordinate: bool = False
    incompressible_plane_strain_power_hardening_reviewed: bool = False


class HardeningMembraneLimitResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    transformation_model: Literal['j2_plane_strain','supplied_work_conjugate_factor']
    calibration_basis_id: Name | None
    transformation_basis_id: Name | None
    transformation_factor: Positive | None = None
    pressure_limit_pa: Positive | None = None
    hoop_logarithmic_strain: Positive | None = None
    equivalent_strain: Positive | None = None
    current_diameter_m: Positive | None = None
    current_wall_m: Positive | None = None
    hoop_stress_pa: Positive | None = None
    issues: tuple[str,...] = ()
    elastic_first_yield_or_actual_rupture_qualified: Literal[False] = False


def evaluate_hardening_membrane_limit(request: HardeningMembraneLimitRequest) -> HardeningMembraneLimitResult:
    """Find the pressure turning point, keeping strain transformation explicit.

    A work-conjugate factor maps hoop strain to the supplied equivalent strain
    and equivalent stress back to hoop stress. J2 associated plane strain uses
    2/sqrt(3); a different historical or calibrated factor requires its own basis.
    Incompressibility changes diameter and wall reciprocally at fixed length.
    The computed turning point is neither elastic onset nor a fracture model.
    """
    if not isinstance(request,HardeningMembraneLimitRequest):raise TypeError('HardeningMembraneLimitRequest required')
    q=HardeningMembraneLimitRequest.model_validate(request.model_dump(mode='python'))
    base=dict(transformation_model=q.transformation_model,calibration_basis_id=q.calibration_basis_id,transformation_basis_id=q.transformation_basis_id)
    required=('reference_diameter_m','reference_wall_m','strength_coefficient_pa','hardening_exponent',
        'calibration_basis_id','maximum_supported_equivalent_strain','maximum_reviewed_wall_diameter_ratio')
    missing=['missing '+k for k in required if getattr(q,k) is None]
    if q.transformation_model=='supplied_work_conjugate_factor':missing += ['missing '+k for k in ('supplied_transformation_factor','transformation_basis_id') if getattr(q,k) is None]
    if not q.diameter_is_reference_membrane_coordinate:missing.append('reference membrane diameter convention requires review')
    if not q.incompressible_plane_strain_power_hardening_reviewed:missing.append('strain constraint and hardening calibration require review')
    if missing:return HardeningMembraneLimitResult(status='needs_data',issues=tuple(missing),**base)
    factor=2/math.sqrt(3) if q.transformation_model=='j2_plane_strain' else q.supplied_transformation_factor
    strain=q.hardening_exponent/2
    equivalent=factor*strain
    if q.reference_wall_m/q.reference_diameter_m>=.5 or q.reference_wall_m/q.reference_diameter_m>q.maximum_reviewed_wall_diameter_ratio:
        return HardeningMembraneLimitResult(status='out_of_scope',**base,issues=('geometry exceeds reviewed membrane ratio',))
    if equivalent>q.maximum_supported_equivalent_strain:
        return HardeningMembraneLimitResult(status='out_of_scope',**base,issues=('turning point exceeds supplied strain support',))
    diameter=q.reference_diameter_m*math.exp(strain);wall=q.reference_wall_m*math.exp(-strain)
    hoop=factor*q.strength_coefficient_pa*equivalent**q.hardening_exponent
    pressure=2*wall/diameter*hoop
    if not all(math.isfinite(v) and v>0 for v in (diameter,wall,hoop,pressure)):raise ValueError('hardening limit exceeds positive finite support')
    return HardeningMembraneLimitResult(status='computed',**base,transformation_factor=factor,pressure_limit_pa=pressure,
        hoop_logarithmic_strain=strain,equivalent_strain=equivalent,current_diameter_m=diameter,current_wall_m=wall,hoop_stress_pa=hoop)
