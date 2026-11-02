"""Separate calibrated force and energy coordinates for local pipe indentation."""
import math
from typing import Literal
from .contracts import Contract,Positive,Nonnegative,Name

class LocalIndentationRequest(Contract):
    coordinate_basis: Literal['square_root_force_law','diameter_scaled_energy_law']
    pipe_wall_thickness_m: Positive
    supplied_yield_stress_pa: Positive | None = None
    supplied_contact_coefficient: Positive | None = None
    force_magnitude_n: Nonnegative | None = None
    impact_energy_j: Nonnegative | None = None
    pipe_outside_diameter_m: Positive | None = None
    maximum_reviewed_indentation_m: Positive
    edge_material_geometry_and_coefficient_basis_id: Name
    selected_coordinate_and_contact_applicability_reviewed: bool = False

def evaluate_local_indentation_coordinate(request: LocalIndentationRequest):
    """Evaluate one explicitly chosen correlation, without merging two laws.

    The square-root force branch implies work by integration of its own force
    law. The separate diameter-scaled energy branch implies a different force
    derivative. Their coefficients and geometry dependence cannot silently be
    interchanged. Actual coating, trawl/soil stiffness, impact inertia, unloading,
    residual dent shape, rupture and field acceptance are not represented.
    """
    q=LocalIndentationRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.edge_material_geometry_and_coefficient_basis_id,coordinate_basis=q.coordinate_basis,
        actual_contact_field_impact_history_or_calibration_qualified=False)
    data=[q.supplied_yield_stress_pa,q.supplied_contact_coefficient]
    data += [q.force_magnitude_n] if q.coordinate_basis=='square_root_force_law' else [q.impact_energy_j,q.pipe_outside_diameter_m]
    if not q.selected_coordinate_and_contact_applicability_reviewed or any(v is None for v in data):return dict(status='needs_data',**base)
    if q.coordinate_basis=='square_root_force_law':
        scale=q.supplied_contact_coefficient*q.supplied_yield_stress_pa*q.pipe_wall_thickness_m**1.5
        if not math.isfinite(scale) or scale<=0:raise ValueError('force-law scale outside finite positive arithmetic support')
        try:
            depth=(q.force_magnitude_n/scale)**2
            force=q.force_magnitude_n;work=2*force*depth/3
        except OverflowError as exc:raise ValueError('force coordinate exceeds finite arithmetic support') from exc
    else:
        if 2*q.pipe_wall_thickness_m>=q.pipe_outside_diameter_m:raise ValueError('wall thickness must leave positive bore')
        scale=q.supplied_contact_coefficient*q.supplied_yield_stress_pa*q.pipe_wall_thickness_m**2/q.pipe_outside_diameter_m**.5
        if not math.isfinite(scale) or scale<=0:raise ValueError('energy-law scale outside finite positive arithmetic support')
        depth=(q.impact_energy_j/scale)**(2/3)
        work=q.impact_energy_j;force=0. if depth==0 else 1.5*work/depth
    if not all(math.isfinite(v) for v in (depth,force,work)):
        raise ValueError('indentation coordinates exceed finite arithmetic support')
    applied=q.force_magnitude_n if q.coordinate_basis=='square_root_force_law' else q.impact_energy_j
    if applied>0 and (depth==0 or work==0 or force==0):
        raise ValueError('nonzero indentation response underflow is not a stationary state')
    if depth>q.maximum_reviewed_indentation_m:return dict(status='out_of_scope',**base)
    return dict(status='computed',**base,indentation_coordinate_m=depth,selected_law_force_coordinate_n=force,
                selected_law_integrated_work_coordinate_j=work)
