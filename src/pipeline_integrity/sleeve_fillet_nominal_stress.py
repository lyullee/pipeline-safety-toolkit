"""Thin circumferential sleeve-joint nominal transfer with supplied peak factors."""
import math
from .contracts import Contract,Name,Positive,Nonnegative

class SleeveFilletStressRequest(Contract):
    header_outside_diameter_m: Positive
    radial_header_to_sleeve_outer_surface_distance_m: Positive
    radial_header_to_sleeve_inner_surface_gap_m: Nonnegative
    longitudinal_joint_transfer_force_n: float | None = None
    supplied_peak_shear_to_nominal_factor: Positive | None = None
    supplied_peak_principal_to_nominal_factor: Positive | None = None
    maximum_reviewed_effective_width_to_diameter: Positive
    peak_factor_geometry_material_support_basis_id: Name
    thin_joint_geometry_transfer_force_and_peak_factor_support_reviewed: bool = False

def evaluate_sleeve_fillet_nominal_stress(request: SleeveFilletStressRequest):
    """Keep a nominal traction coordinate separate from calibrated local peaks.

    Effective width is the outer stand-off less the inner clearance. The
    reference transfer area uses header circumference times effective width;
    this is a thin-joint coordinate, not an exact resolved fillet stress field.
    Peak factors are caller-supplied reviewed providers with no defaults. Neither
    actual weld shape, toe/root concentration, strength nor allowable is inferred.
    """
    q=SleeveFilletStressRequest.model_validate(request.model_dump())
    width=q.radial_header_to_sleeve_outer_surface_distance_m-q.radial_header_to_sleeve_inner_surface_gap_m
    if width<=0:raise ValueError('positive effective joint width required after clearance')
    base=dict(effective_joint_width_m=width,peak_factor_geometry_material_support_basis_id=q.peak_factor_geometry_material_support_basis_id,
        peak_factor_calibration_local_stress_field_or_acceptance_qualified=False)
    if width/q.header_outside_diameter_m>q.maximum_reviewed_effective_width_to_diameter:
        return dict(status='out_of_scope',**base)
    if not q.thin_joint_geometry_transfer_force_and_peak_factor_support_reviewed or any(v is None for v in
        (q.longitudinal_joint_transfer_force_n,q.supplied_peak_shear_to_nominal_factor,q.supplied_peak_principal_to_nominal_factor)):
        return dict(status='needs_data',**base)
    area=math.pi*q.header_outside_diameter_m*width
    nominal=q.longitudinal_joint_transfer_force_n/area
    return dict(status='computed',**base,thin_joint_reference_transfer_area_m2=area,signed_nominal_shear_coordinate_pa=nominal,
        provider_scaled_peak_shear_magnitude_pa=q.supplied_peak_shear_to_nominal_factor*abs(nominal),
        provider_scaled_peak_principal_magnitude_pa=q.supplied_peak_principal_to_nominal_factor*abs(nominal))
