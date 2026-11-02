"""Reviewed circumferential-loss axial-tension correlation coordinate."""
import math
from .contracts import Contract, Positive, Nonnegative, Name

class CircumferentialLossTensionRequest(Contract):
    nominal_wall_thickness_m: Positive
    loss_depth_m: Nonnegative
    circumferential_loss_width_m: Nonnegative
    supplied_reference_failure_stress_pa: Positive | None = None
    geometry_material_and_tension_only_basis_id: Name
    isolated_blunt_loss_away_from_discontinuities_and_tension_only_reviewed: bool = False

def evaluate_circumferential_loss_tension(request: CircumferentialLossTensionRequest):
    """Return a nominal axial stress coordinate, not a combined-load allowable.

    The reference failure stress and correlation applicability require caller
    review. No flow-stress convention, bending capacity, pressure interaction,
    crack behavior or weld-quality acceptance is inferred from loss geometry.
    """
    q=CircumferentialLossTensionRequest.model_validate(request.model_dump())
    t=q.nominal_wall_thickness_m; d=q.loss_depth_m; b=q.circumferential_loss_width_m
    if d>=t:raise ValueError('loss depth must leave positive nominal ligament')
    base=dict(basis_id=q.geometry_material_and_tension_only_basis_id,
              correlation_calibration_or_field_acceptance_qualified=False)
    if not q.isolated_blunt_loss_away_from_discontinuities_and_tension_only_reviewed or q.supplied_reference_failure_stress_pa is None:
        return dict(status='needs_data',**base)
    # Rational form avoids subtracting nearly equal effective-depth terms.
    shape=math.hypot(1.,b/t/math.sqrt(2.))
    ratio=(1.-d/t)/(1.-d/t/shape)
    return dict(status='computed',**base,nominal_axial_failure_stress_coordinate_pa=q.supplied_reference_failure_stress_pa*ratio,
                remaining_nominal_stress_fraction=ratio,effective_loss_depth_m=t*(1.-ratio),width_shape_coordinate=shape)
