"""Caller-calibrated saddle reaction-capacity screening coordinate."""
import math
import sys
from .contracts import Contract,Positive,Name

class SaddleCapacityRequest(Contract):
    wall_thickness_m: Positive
    pipe_reference_radius_m: Positive
    supplied_yield_stress_pa: Positive | None = None
    supplied_contact_geometry_coefficient: Positive | None = None
    maximum_reviewed_wall_thickness_to_radius: Positive
    material_contact_angle_and_geometry_basis_id: Name
    local_saddle_correlation_applicability_reviewed: bool = False

def evaluate_saddle_capacity(request: SaddleCapacityRequest):
    """Calculate a reviewed local reaction bound, not a support selection.

    Contact geometry coefficient and yield basis must come from an applicable
    caller-owned correlation or data provider. Contact angle is deliberately not
    converted using an unreviewed historical default. No wear, fatigue, uplift,
    rigid-strut capacity or design-code acceptance follows from this coordinate.
    """
    q=SaddleCapacityRequest.model_validate(request.model_dump())
    ratio=q.wall_thickness_m/q.pipe_reference_radius_m
    base=dict(basis_id=q.material_contact_angle_and_geometry_basis_id,local_contact_field_or_correlation_calibration_qualified=False)
    if ratio>=1 or ratio>q.maximum_reviewed_wall_thickness_to_radius:return dict(status='out_of_scope',**base)
    if not q.local_saddle_correlation_applicability_reviewed or q.supplied_yield_stress_pa is None or q.supplied_contact_geometry_coefficient is None:
        return dict(status='needs_data',**base)
    radial_integral=math.log(q.pipe_reference_radius_m)-math.log(q.wall_thickness_m)
    force_log=(math.log(2.)+math.log(q.supplied_yield_stress_pa)+2*math.log(q.wall_thickness_m)
               -math.log(q.supplied_contact_geometry_coefficient)-math.log(radial_integral))
    if not math.log(sys.float_info.min)<=force_log<=math.log(sys.float_info.max):
        return dict(status='out_of_scope',reason='positive saddle reaction is not numerically representable',**base)
    force=math.exp(force_log)
    return dict(status='computed',**base,nominal_saddle_reaction_capacity_coordinate_n=force)
