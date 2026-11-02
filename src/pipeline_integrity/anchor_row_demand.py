"""Pure-moment tension-row and equal-share shear demand with supplied bearing line."""
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Name

class AnchorRowDemandRequest(Contract):
    total_anchor_count: int = Field(gt=0)
    tension_row_anchor_count: int = Field(gt=0)
    bending_moment_magnitude_nm: Nonnegative | None = None
    resultant_shear_n: tuple[float,float] | None = None
    tension_row_to_compression_resultant_lever_arm_m: Positive | None = None
    compression_resultant_basis_id: Name
    pure_moment_equal_tension_row_equal_shear_and_bearing_location_reviewed: bool = False

def evaluate_anchor_row_demand(request: AnchorRowDemandRequest):
    """Compute demand after the compression resultant location is reviewed.

    No axial force, friction, prying or flexible-plate contact law is inferred.
    The bearing location can represent a caller-reviewed contact distribution;
    its lever arm is required rather than silently selecting a concrete block.
    Sign and position of the tension row are caller-defined by the chosen
    bending direction. Resultants alone do not establish anchor/concrete capacity.
    """
    q=AnchorRowDemandRequest.model_validate(request.model_dump())
    if q.tension_row_anchor_count>q.total_anchor_count:raise ValueError('tension row exceeds total anchor count')
    if not q.pure_moment_equal_tension_row_equal_shear_and_bearing_location_reviewed or any(v is None for v in
        (q.bending_moment_magnitude_nm,q.resultant_shear_n,q.tension_row_to_compression_resultant_lever_arm_m)):
        return dict(status='needs_data',compression_resultant_basis_id=q.compression_resultant_basis_id)
    tension=q.bending_moment_magnitude_nm/q.tension_row_to_compression_resultant_lever_arm_m
    return dict(status='computed',tension_per_tension_row_anchor_n=tension/q.tension_row_anchor_count,
        total_tension_row_reaction_n=tension,compression_resultant_magnitude_n=tension,
        equal_share_shear_per_anchor_n=tuple(v/q.total_anchor_count for v in q.resultant_shear_n),
        compression_resultant_basis_id=q.compression_resultant_basis_id,
        contact_distribution_prying_or_anchor_capacity_calculated=False)
