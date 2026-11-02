"""Calibrated post-buckle propagation and finite-arrester screening coordinates."""
import math
from .contracts import Contract,Positive,Nonnegative,Name

class BuckleArresterRequest(Contract):
    pipe_outside_diameter_m: Positive
    pipe_wall_thickness_m: Positive
    arrester_outside_diameter_m: Positive
    arrester_wall_thickness_m: Positive
    arrester_length_m: Nonnegative
    supplied_pipe_yield_stress_pa: Positive | None = None
    supplied_arrester_yield_stress_pa: Positive | None = None
    supplied_pipe_propagation_coefficient: Positive | None = None
    supplied_pipe_thickness_exponent: Positive | None = None
    supplied_arrester_propagation_coefficient: Positive | None = None
    supplied_arrester_thickness_exponent: Positive | None = None
    supplied_length_transfer_coefficient: Positive | None = None
    target_external_differential_pressure_pa: Nonnegative | None = None
    material_geometry_coefficient_and_postbuckled_basis_id: Name
    propagation_and_arrester_correlation_applicability_reviewed: bool = False

def evaluate_buckle_arrester_coordinate(request: BuckleArresterRequest):
    """Distinguish an existing buckle from initial intact-pipe collapse.

    Coefficients/exponents require an applicable material/geometry calibration.
    The length law interpolates between pipe and long-arrester propagation
    coordinates. It is not an eigenbuckling solver, collapse front simulation,
    weld qualification, allowable external pressure or installation approval.
    """
    q=BuckleArresterRequest.model_validate(request.model_dump())
    if 2*q.pipe_wall_thickness_m>=q.pipe_outside_diameter_m or 2*q.arrester_wall_thickness_m>=q.arrester_outside_diameter_m:
        raise ValueError('pipe and arrester require positive bores')
    base=dict(basis_id=q.material_geometry_coefficient_and_postbuckled_basis_id,actual_postbuckling_propagation_or_arrest_qualified=False)
    values=(q.supplied_pipe_yield_stress_pa,q.supplied_arrester_yield_stress_pa,q.supplied_pipe_propagation_coefficient,
        q.supplied_pipe_thickness_exponent,q.supplied_arrester_propagation_coefficient,q.supplied_arrester_thickness_exponent,q.supplied_length_transfer_coefficient)
    if not q.propagation_and_arrester_correlation_applicability_reviewed or any(v is None for v in values):return dict(status='needs_data',**base)
    pipe=q.supplied_pipe_propagation_coefficient*q.supplied_pipe_yield_stress_pa*(q.pipe_wall_thickness_m/q.pipe_outside_diameter_m)**q.supplied_pipe_thickness_exponent
    arrester=q.supplied_arrester_propagation_coefficient*q.supplied_arrester_yield_stress_pa*(q.arrester_wall_thickness_m/q.arrester_outside_diameter_m)**q.supplied_arrester_thickness_exponent
    if arrester<=pipe:return dict(status='out_of_scope',**base)
    decay=q.supplied_length_transfer_coefficient*q.arrester_wall_thickness_m/q.arrester_outside_diameter_m**2
    pressure=pipe+(arrester-pipe)*(-math.expm1(-decay*q.arrester_length_m))
    result=dict(status='computed',**base,pipe_propagation_pressure_coordinate_pa=pipe,
        long_arrester_propagation_pressure_coordinate_pa=arrester,finite_length_arrest_pressure_coordinate_pa=pressure)
    if q.target_external_differential_pressure_pa is not None:
        target=q.target_external_differential_pressure_pa
        if target>=arrester:result.update(target_reachable_at_finite_length=False,required_length_coordinate_m=None)
        else:result.update(target_reachable_at_finite_length=True,required_length_coordinate_m=0. if target<=pipe else -math.log1p(-(target-pipe)/(arrester-pipe))/decay)
    return result
