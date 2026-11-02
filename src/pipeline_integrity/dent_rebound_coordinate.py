"""Reviewed dent rebound coordinate with existing stress-life owner linkage."""
import math
from .contracts import Contract,Positive,Nonnegative,Name
from .stress_life_coordinate import StressLifeCoordinateRequest,evaluate_stress_life_coordinate

class DentReboundRequest(Contract):
    observed_pressurized_dent_depth_m: Nonnegative
    operating_hoop_stress_pa: Positive
    supplied_flow_stress_pa: Positive | None = None
    supplied_rebound_log_coefficient: Positive | None = None
    minimum_reviewed_hoop_to_flow_ratio: Positive
    maximum_reviewed_hoop_to_flow_ratio: Positive
    plain_dent_material_pressure_and_calibration_basis_id: Name
    plain_dent_rebound_correlation_applicability_reviewed: bool = False
    pressure_cycle_range_pa: Nonnegative | None = None
    supplied_dent_pressure_to_stress_range_factor: Positive | None = None
    fatigue_curve: StressLifeCoordinateRequest | None = None

def evaluate_dent_rebound_coordinate(request: DentReboundRequest,context=None,*,client=None):
    """Keep empirical rebound and caller-calibrated fatigue separate.

    The original dent depth is inferred only inside a reviewed pressure/stress
    domain. Fatigue delegates to the existing power-curve owner; its reference
    point, slope and full-range convention are supplied by the caller. No dent
    shape factor, gouge/crack behavior, endurance curve or acceptance threshold
    is selected automatically.
    """
    q=DentReboundRequest.model_validate(request.model_dump())
    if q.minimum_reviewed_hoop_to_flow_ratio>q.maximum_reviewed_hoop_to_flow_ratio:raise ValueError('reviewed stress-ratio interval is reversed')
    base=dict(basis_id=q.plain_dent_material_pressure_and_calibration_basis_id,
        actual_residual_dent_shape_or_field_fatigue_qualified=False)
    if not q.plain_dent_rebound_correlation_applicability_reviewed or q.supplied_flow_stress_pa is None or q.supplied_rebound_log_coefficient is None:return dict(status='needs_data',**base)
    ratio=q.operating_hoop_stress_pa/q.supplied_flow_stress_pa
    if not q.minimum_reviewed_hoop_to_flow_ratio<=ratio<=q.maximum_reviewed_hoop_to_flow_ratio or ratio>=1:return dict(status='out_of_scope',**base)
    original=q.observed_pressurized_dent_depth_m/(-q.supplied_rebound_log_coefficient*math.log(ratio))
    if original<q.observed_pressurized_dent_depth_m:return dict(status='out_of_scope',**base)
    result=dict(status='computed',**base,original_dent_depth_coordinate_m=original,fatigue_status='not_requested')
    if q.fatigue_curve is not None:
        if q.pressure_cycle_range_pa is None or q.supplied_dent_pressure_to_stress_range_factor is None or context is None:
            result['fatigue_status']='needs_data'
        elif q.pressure_cycle_range_pa==0:
            result.update(fatigue_status='zero_range_no_finite_life_query',stress_cycle_range_coordinate_pa=0.)
        else:
            curve=q.fatigue_curve
            if curve.coordinate_semantics!='full_range' or curve.supplied_query_stress_multiplier!=1:raise ValueError('dent fatigue linkage requires full-range curve with no additional query multiplier')
            stress=q.pressure_cycle_range_pa*q.supplied_dent_pressure_to_stress_range_factor
            receipt=evaluate_stress_life_coordinate(curve.model_copy(update={'query_stress_coordinate_pa':stress}),context,client=client)
            result.update(stress_cycle_range_coordinate_pa=stress,fatigue_status=receipt.status,fatigue_curve_receipt=receipt.model_dump(mode='json'))
    return result
