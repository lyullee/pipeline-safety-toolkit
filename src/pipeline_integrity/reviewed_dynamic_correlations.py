"""Explicitly calibrated dynamic-strength and vent-area scalar coordinates."""
import math
from typing import Literal
from .contracts import Contract,Positive,Nonnegative,Name

class DynamicStrengthCoordinateRequest(Contract):
    law: Literal['log_rate_ratio','power_rate_ratio']
    strain_rate_magnitude_per_s: Nonnegative
    reference_strain_rate_per_s: Positive
    minimum_reviewed_rate_per_s: Nonnegative
    maximum_reviewed_rate_per_s: Positive
    supplied_static_reference_strength_pa: Positive | None = None
    supplied_ratio_intercept: float | None = None
    supplied_ratio_multiplier: float | None = None
    supplied_power_exponent: Positive | None = None
    material_temperature_rate_and_strength_kind_basis_id: Name
    material_temperature_rate_law_and_units_reviewed: bool = False

def evaluate_dynamic_strength_coordinate(request: DynamicStrengthCoordinateRequest):
    """Use a calibrated coordinate; do not increase an allowable automatically.

    A logarithmic law uses log10(rate/reference rate). A power law uses that
    dimensionless ratio raised to the supplied exponent. Yield and ultimate
    strength require separate requests and coefficient evidence. No toughness,
    maximum-strain acceptance, history constitutive integration or default
    room-temperature material calibration is inferred.
    """
    q=DynamicStrengthCoordinateRequest.model_validate(request.model_dump())
    if q.minimum_reviewed_rate_per_s>q.maximum_reviewed_rate_per_s:raise ValueError('reviewed strain-rate range is reversed')
    base=dict(basis_id=q.material_temperature_rate_and_strength_kind_basis_id,material_calibration_or_dynamic_allowable_qualified=False)
    rate=q.strain_rate_magnitude_per_s
    if not q.minimum_reviewed_rate_per_s<=rate<=q.maximum_reviewed_rate_per_s or (q.law=='log_rate_ratio' and rate==0):return dict(status='out_of_scope',**base)
    data=(q.supplied_static_reference_strength_pa,q.supplied_ratio_intercept,q.supplied_ratio_multiplier)
    if not q.material_temperature_rate_law_and_units_reviewed or any(v is None for v in data) or (q.law=='power_rate_ratio' and q.supplied_power_exponent is None):
        return dict(status='needs_data',**base)
    x=rate/q.reference_strain_rate_per_s
    coordinate=math.log10(x) if q.law=='log_rate_ratio' else x**q.supplied_power_exponent
    ratio=q.supplied_ratio_intercept+q.supplied_ratio_multiplier*coordinate
    if ratio<=0:return dict(status='out_of_scope',**base)
    return dict(status='computed',**base,dynamic_to_reference_strength_ratio=ratio,dynamic_strength_coordinate_pa=ratio*q.supplied_static_reference_strength_pa)

class DeflagrationVentCoordinateRequest(Contract):
    enclosure_internal_surface_area_m2: Positive
    reduced_pressure_differential_pa: Positive
    supplied_fuel_geometry_coefficient_sqrt_pa: Positive | None = None
    fuel_geometry_pressure_reference_and_coefficient_units_basis_id: Name
    empirical_vent_correlation_applicability_reviewed: bool = False

def evaluate_deflagration_vent_coordinate(request: DeflagrationVentCoordinateRequest):
    """Calculate a supplied inverse-square-root pressure vent coordinate.

    The coefficient carries sqrt(Pa); a number calibrated against psi cannot
    be passed unchanged. Pressure is a declared differential reference. Neither
    fuel data nor current design-standard acceptance is selected automatically.
    This empirical coordinate is not a detonation-venting or relief-nozzle law.
    """
    q=DeflagrationVentCoordinateRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.fuel_geometry_pressure_reference_and_coefficient_units_basis_id,
        fuel_calibration_dynamic_venting_or_code_acceptance_qualified=False)
    if not q.empirical_vent_correlation_applicability_reviewed or q.supplied_fuel_geometry_coefficient_sqrt_pa is None:return dict(status='needs_data',**base)
    try:
        area=q.supplied_fuel_geometry_coefficient_sqrt_pa*q.enclosure_internal_surface_area_m2/math.sqrt(q.reduced_pressure_differential_pa)
        if not math.isfinite(area) or area<=0:raise ArithmeticError
        return dict(status='computed',**base,vent_area_coordinate_m2=area)
    except ArithmeticError:
        return dict(status='out_of_scope',**base,issues=('positive vent-coordinate area is outside finite support',))
