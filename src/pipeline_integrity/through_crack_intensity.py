"""Length-based through-crack coordinates through the existing Mode-I owner."""
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job

class ThroughCrackIntensityRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    half_crack_length_m: Positive
    opening_stress_pa: Nonnegative | None = None
    geometry_factor: Positive | None = None
    fracture_toughness_pa_sqrt_m: Positive | None = None
    minimum_supported_half_length_m: Positive
    maximum_supported_half_length_m: Positive
    geometry_solution_id: Name
    evidence_id: Name
    length_coordinate_linear_elastic_geometry_and_toughness_reviewed: bool = False

def evaluate_through_crack_intensity(request: ThroughCrackIntensityRequest,context: RunContext,*,client=None):
    """The crack coordinate is half length, never penetration through the wall.

    Geometry factors and opening stresses are reviewed caller providers. This
    adapter does not derive a pipe curvature factor or apply a surface-crack
    depth/thickness gate to a through-wall crack length.
    """
    q=ThroughCrackIntensityRequest.model_validate(request.model_dump())
    c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('fracture/context identity differs')
    if q.evidence_id not in {e.id for e in c.evidence}:raise ValueError('geometry/toughness evidence absent')
    if q.maximum_supported_half_length_m<q.minimum_supported_half_length_m:raise ValueError('half-length support reversed')
    base=dict(request_digest=digest(q),context_digest=digest(c),geometry_solution_id=q.geometry_solution_id,
        crack_coordinate='through_crack_half_length',critical_length_qualification_inferred=False)
    if not q.length_coordinate_linear_elastic_geometry_and_toughness_reviewed or any(v is None for v in (q.opening_stress_pa,q.geometry_factor,q.fracture_toughness_pa_sqrt_m)):
        return dict(status='needs_data',**base)
    if not q.minimum_supported_half_length_m<=q.half_crack_length_m<=q.maximum_supported_half_length_m:
        return dict(status='out_of_scope',**base)
    client=MaterialClient() if client is None else client
    job=prepare_material_job('fracture.stress_intensity',dict(crack_depth_m=q.half_crack_length_m,
        tensile_stress_Pa=q.opening_stress_pa,geometry_factor=q.geometry_factor,
        fracture_toughness_Pa_sqrt_m=q.fracture_toughness_pa_sqrt_m),c,location_id=q.location_id,evidence_ids=(q.evidence_id,),client=client)
    receipt=evaluate_material_job(job,c,client=client)
    return dict(status=receipt.native.status,**base,values=receipt.native.payload.get('values',{}),native_material_receipt=receipt.model_dump(mode='json'))
