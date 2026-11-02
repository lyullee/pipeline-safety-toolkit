"""Unit-explicit supplied empirical notch and impact-toughness coordinates."""
import math
import sys
from .contracts import Contract,Positive,Nonnegative,Name

class NotchThresholdRequest(Contract):
    notch_root_radius_m: Positive
    yield_stress_pa: Positive
    reference_notch_radius_m: Positive
    reference_yield_stress_pa: Positive
    supplied_reference_threshold_pa_sqrt_m: Positive | None = None
    supplied_radius_strength_exponent: Positive | None = None
    material_temperature_notch_and_unit_basis_id: Name
    notch_threshold_correlation_support_reviewed: bool = False

def evaluate_notch_threshold_coordinate(request: NotchThresholdRequest):
    """Use normalized radius/strength so no legacy-unit coefficient is hidden."""
    q=NotchThresholdRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.material_temperature_notch_and_unit_basis_id,actual_threshold_or_material_calibration_qualified=False)
    if not q.notch_threshold_correlation_support_reviewed or q.supplied_reference_threshold_pa_sqrt_m is None or q.supplied_radius_strength_exponent is None:return dict(status='needs_data',**base)
    logarithm=(math.log(q.supplied_reference_threshold_pa_sqrt_m)+q.supplied_radius_strength_exponent*(
        math.log(q.notch_root_radius_m)-math.log(q.reference_notch_radius_m)
        +math.log(q.yield_stress_pa)-math.log(q.reference_yield_stress_pa)))
    if not math.log(sys.float_info.min)<=logarithm<=math.log(sys.float_info.max):
        return dict(status='out_of_scope',reason='positive notch threshold is not numerically representable',**base)
    return dict(status='computed',**base,threshold_intensity_range_coordinate_pa_sqrt_m=math.exp(logarithm))

class ImpactToughnessRequest(Contract):
    impact_energy_j: Nonnegative
    yield_stress_pa: Positive
    supplied_energy_strength_coefficient_per_m2: Positive | None = None
    supplied_squared_toughness_over_strength_offset_m: Nonnegative | None = None
    material_temperature_specimen_and_unit_basis_id: Name
    impact_to_elastic_toughness_correlation_support_reviewed: bool = False

class NormalizedImpactToughnessRequest(Contract):
    """Measured energy/strength ratio without inventing an absolute material state.

    A joule divided by a pascal is a cubic metre. The squared toughness/strength
    ratio is a length. Static and dynamic yield strengths, temperature and shelf
    conditions remain distinct caller-reviewed material contracts.
    """
    impact_energy_over_yield_strength_m3: Nonnegative
    supplied_energy_strength_coefficient_per_m2: Positive | None = None
    supplied_squared_toughness_over_strength_offset_m: Nonnegative | None = None
    material_temperature_specimen_and_unit_basis_id: Name
    impact_to_elastic_toughness_correlation_support_reviewed: bool = False

def _impact_squared_ratio(energy_over_strength,coefficient,offset):
    product=coefficient*energy_over_strength
    value=product-offset
    if not math.isfinite(value):
        raise ValueError('impact-toughness coordinate exceeds finite numerical support')
    # A threshold assembled as offset/coefficient has division and product
    # rounding. An unresolved positive remainder cannot support a toughness.
    resolution=math.ulp(product)+coefficient*math.ulp(energy_over_strength)
    return value,resolution

def evaluate_normalized_impact_toughness_coordinate(request: NormalizedImpactToughnessRequest):
    """Return a yield-normalized empirical estimate; no material curve is inferred."""
    q=NormalizedImpactToughnessRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.material_temperature_specimen_and_unit_basis_id,
        measured_toughness_or_material_calibration_qualified=False)
    if not q.impact_to_elastic_toughness_correlation_support_reviewed or q.supplied_energy_strength_coefficient_per_m2 is None or q.supplied_squared_toughness_over_strength_offset_m is None:
        return dict(status='needs_data',**base)
    squared,resolution=_impact_squared_ratio(q.impact_energy_over_yield_strength_m3,
        q.supplied_energy_strength_coefficient_per_m2,q.supplied_squared_toughness_over_strength_offset_m)
    if squared<=resolution:return dict(status='out_of_scope',**base,
        support_reason='nonpositive_or_numerically_unresolved_radicand',radicand_resolution_guard_m=resolution)
    return dict(status='computed',**base,
        estimated_squared_toughness_over_strength_m=squared,
        estimated_toughness_over_strength_sqrt_m=math.sqrt(squared))

def evaluate_impact_toughness_coordinate(request: ImpactToughnessRequest):
    """Keep a calibrated estimate distinct from a measured fracture toughness.

    The coefficient is 1/m² and the squared K/strength offset is metres.
    Coefficients expressed for ksi, inch and foot-pound conventions require an
    explicit conversion by the caller. Negative radicands are outside support;
    they are not clipped to a fictitious zero toughness.
    """
    q=ImpactToughnessRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.material_temperature_specimen_and_unit_basis_id,measured_toughness_or_material_calibration_qualified=False)
    if not q.impact_to_elastic_toughness_correlation_support_reviewed or q.supplied_energy_strength_coefficient_per_m2 is None or q.supplied_squared_toughness_over_strength_offset_m is None:return dict(status='needs_data',**base)
    squared,resolution=_impact_squared_ratio(q.impact_energy_j/q.yield_stress_pa,
        q.supplied_energy_strength_coefficient_per_m2,q.supplied_squared_toughness_over_strength_offset_m)
    if squared<=resolution:return dict(status='out_of_scope',**base)
    return dict(status='computed',**base,estimated_elastic_toughness_coordinate_pa_sqrt_m=q.yield_stress_pa*math.sqrt(squared))
