"""Nominal circular-shaft elastic torsion with a supplied shear-stress ceiling.

The ceiling is reviewed input, not a material strength prediction or a generic
service factor. Optional declared elastic bending is combined at the extreme
surface fibre by a selected isotropic equivalent-stress criterion. Keyways,
fatigue, dynamics and shaft ratings remain outside this local response.
"""
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Name, Positive, Nonnegative, digest
from .structural import annular_frame_section


class ShaftTorsionRequest(Contract):
    id: Name
    outside_diameter_m: Positive
    inside_diameter_m: Nonnegative = 0.
    signed_torque_nm: float
    signed_angular_velocity_rad_s: float
    supplied_shear_stress_ceiling_pa: Positive | None = None
    ceiling_evidence_id: Name | None = None
    length_m: Positive | None = None
    shear_modulus_pa: Positive | None = None
    homogeneous_circular_saint_venant_torsion_reviewed: bool = False
    supplied_ceiling_basis_reviewed: bool = False
    signed_bending_moment_nm: float = 0.
    combined_bending_and_torsion_reviewed: bool = False
    combined_equivalent_stress_basis: Literal['von_mises','tresca'] = 'von_mises'
    supplied_combined_equivalent_stress_ceiling_pa: Positive | None = None
    combined_ceiling_evidence_id: Name | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.inside_diameter_m >= self.outside_diameter_m:
            raise ValueError('positive circular section required')
        if (self.length_m is None) != (self.shear_modulus_pa is None):
            raise ValueError('length and shear modulus must be supplied together')
        return self


def evaluate_shaft_torsion(q: ShaftTorsionRequest):
    """Return nominal shear/work response and optional conditional torque ceiling.

    Torque, rotation and angular velocity use one right-hand axis. Surface shear
    is signed; the stress ceiling bounds its magnitude. A zero speed permits
    nonzero torque and therefore zero transmitted power without zero capacity.
    """
    q = ShaftTorsionRequest.model_validate(q.model_dump())
    base = dict(request_digest=digest(q),field_or_rating_qualified=False,
                material_strength_predicted=False,legacy_service_factor_reproduced=False)
    if not q.homogeneous_circular_saint_venant_torsion_reviewed:
        return dict(status='needs_data',**base)
    if q.signed_bending_moment_nm != 0 and not q.combined_bending_and_torsion_reviewed:
        return dict(status='needs_data',reason='combined bending and torsion basis requires review',**base)
    section = annular_frame_section(q.id,q.outside_diameter_m,q.inside_diameter_m,q.id)
    j = section.torsion_constant_m4
    radius = q.outside_diameter_m/2
    if j <= 0 or not math.isfinite(j):
        raise ValueError('section exceeds finite support')
    stress = q.signed_torque_nm*radius/j
    values = dict(polar_second_moment_m4=j,signed_surface_shear_stress_pa=stress,
                  signed_transmitted_power_w=q.signed_torque_nm*q.signed_angular_velocity_rad_s)
    if q.combined_bending_and_torsion_reviewed:
        normal = q.signed_bending_moment_nm * radius / (j / 2)
        shear_weight = 3. if q.combined_equivalent_stress_basis == 'von_mises' else 4.
        equivalent = math.hypot(normal, math.sqrt(shear_weight) * stress)
        values.update(signed_extreme_bending_normal_stress_pa=normal,
                      combined_equivalent_stress_pa=equivalent)
        if q.supplied_combined_equivalent_stress_ceiling_pa is not None and q.combined_ceiling_evidence_id is not None:
            ceiling = q.supplied_combined_equivalent_stress_ceiling_pa
            admissible = abs(normal) <= ceiling
            remaining = max(0., (ceiling - abs(normal)) * (ceiling + abs(normal)))
            torque_ceiling = math.sqrt(remaining / shear_weight) * j / radius if admissible else None
            values.update(combined_equivalent_ceiling_residual_pa=ceiling-equivalent,
                          combined_maximum_absolute_torque_nm=torque_ceiling,
                          combined_maximum_absolute_power_w=None if torque_ceiling is None else torque_ceiling*abs(q.signed_angular_velocity_rad_s))
    if q.length_m is not None:
        twist = q.signed_torque_nm*q.length_m/(q.shear_modulus_pa*j)
        values.update(signed_twist_rad=twist,elastic_strain_energy_j=q.signed_torque_nm*twist/2)
    ceiling_ready = q.supplied_shear_stress_ceiling_pa is not None and q.supplied_ceiling_basis_reviewed and q.ceiling_evidence_id is not None
    if ceiling_ready:
        maximum = q.supplied_shear_stress_ceiling_pa*j/radius
        values.update(maximum_absolute_nominal_torque_nm=maximum,
                      maximum_absolute_transmitted_power_w=maximum*abs(q.signed_angular_velocity_rad_s),
                      supplied_shear_ceiling_residual_pa=q.supplied_shear_stress_ceiling_pa-abs(stress))
    else:
        values.update(maximum_absolute_nominal_torque_nm=None,maximum_absolute_transmitted_power_w=None,
                      supplied_shear_ceiling_residual_pa=None)
    if any(v is not None and not math.isfinite(v) for v in values.values()):
        raise ValueError('response exceeds finite support')
    return dict(status='computed',**base,ceiling_status='conditional_supplied_ceiling' if ceiling_ready else 'needs_data',values=values)
