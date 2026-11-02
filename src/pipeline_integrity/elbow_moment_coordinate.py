"""Reviewed elbow moment/stress-index coordinate; no local shell solver."""
import math
from typing import Literal
from .contracts import Contract,Positive,Nonnegative,Name

class ElbowMomentCoordinateRequest(Contract):
    loading_basis: Literal['in_plane','out_of_plane','declared_srss_components']
    moment_components_nm: tuple[float,float,float]
    section_modulus_m3: Positive
    wall_thickness_m: Positive
    bend_centerline_radius_m: Positive
    mean_pipe_radius_m: Positive
    supplied_yield_stress_pa: Positive | None = None
    supplied_stress_index_numerator: Positive | None = None
    supplied_plastic_limit_to_first_yield_moment_ratio: Positive | None = None
    index_loading_axes_and_material_basis_id: Name
    geometry_index_loading_axes_and_limit_ratio_applicability_reviewed: bool = False

def evaluate_elbow_moment_coordinate(request: ElbowMomentCoordinateRequest):
    """Keep first-yield and supplied plastic-limit coordinates distinct.

    A stress index is not a shell stress tensor or a fatigue factor. The caller
    supplies its coefficient and applicability for the selected loading basis;
    no historical code coefficient or collapse multiplier is silently chosen.
    SRSS is only a declared screening combination, not a physical moment axis.
    """
    q=ElbowMomentCoordinateRequest.model_validate(request.model_dump())
    if q.wall_thickness_m>=2*q.mean_pipe_radius_m:raise ValueError('wall thickness must leave positive pipe bore')
    if q.loading_basis!='declared_srss_components' and (q.moment_components_nm[1]!=0 or q.moment_components_nm[2]!=0):
        raise ValueError('single-plane basis uses only first declared moment component')
    base=dict(basis_id=q.index_loading_axes_and_material_basis_id,loading_basis=q.loading_basis,
        actual_local_stress_field_plastic_collapse_or_code_acceptance_qualified=False)
    values=(q.supplied_yield_stress_pa,q.supplied_stress_index_numerator,q.supplied_plastic_limit_to_first_yield_moment_ratio)
    if not q.geometry_index_loading_axes_and_limit_ratio_applicability_reviewed or any(v is None for v in values):return dict(status='needs_data',**base)
    h=q.wall_thickness_m*q.bend_centerline_radius_m/q.mean_pipe_radius_m**2
    stress_index=q.supplied_stress_index_numerator/h**(2/3)
    moment=math.hypot(*q.moment_components_nm)
    first_yield=q.supplied_yield_stress_pa*q.section_modulus_m3/stress_index
    return dict(status='computed',**base,flexibility_geometry_coordinate=h,supplied_basis_stress_index=stress_index,
        nominal_peak_stress_coordinate_pa=stress_index*moment/q.section_modulus_m3,
        first_yield_moment_coordinate_nm=first_yield,
        supplied_ratio_plastic_limit_moment_coordinate_nm=first_yield*q.supplied_plastic_limit_to_first_yield_moment_ratio)
