"""Explicit smooth-specimen coordinates through the existing fatigue owner."""
from typing import Literal
from .contracts import Contract,Name,Positive,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job

class StrainLifeCoordinateRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    coordinate: Literal['total_strain_amplitude','total_strain_range','elastic_pseudo_stress_amplitude']
    value: Positive | None = None
    elastic_modulus_pa: Positive
    fatigue_strength_coefficient_pa: Positive
    fatigue_strength_exponent: float
    fatigue_ductility_coefficient: Positive
    fatigue_ductility_exponent: float
    minimum_valid_cycles: Positive
    maximum_valid_cycles: Positive
    calibration_basis_id: Name
    calibration_evidence_id: Name
    fully_reversed_same_measure_modern_calibration_reviewed: bool = False

def evaluate_strain_life_coordinate(request: StrainLifeCoordinateRequest,context: RunContext,*,client=None):
    """Keep amplitude/range and whole-cycle/reversal conventions explicit.

    Pseudo-stress is a coordinate E*strain_amplitude, not a claim that the
    actual cyclic stress is elastic. Two-term calibration is supplied by the
    caller; no endurance-offset curve or material table is inferred.
    """
    q=StrainLifeCoordinateRequest.model_validate(request.model_dump())
    c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('coordinate/context identity differs')
    if q.calibration_evidence_id not in {e.id for e in c.evidence}:raise ValueError('calibration evidence absent')
    if q.fatigue_strength_exponent>=0 or q.fatigue_ductility_exponent>=0:raise ValueError('decreasing calibration requires negative exponents')
    if q.maximum_valid_cycles<q.minimum_valid_cycles:raise ValueError('cycle support reversed')
    base=dict(request_digest=digest(q),context_digest=digest(c),calibration_basis_id=q.calibration_basis_id,
              actual_stress_or_source_endurance_curve_inferred=False)
    if q.value is None or not q.fully_reversed_same_measure_modern_calibration_reviewed:
        return dict(status='needs_data',**base)
    amplitude=q.value/2 if q.coordinate=='total_strain_range' else q.value/q.elastic_modulus_pa if q.coordinate=='elastic_pseudo_stress_amplitude' else q.value
    parameters=dict(strain_amplitude=amplitude,elastic_modulus_Pa=q.elastic_modulus_pa,
        fatigue_strength_coefficient_Pa=q.fatigue_strength_coefficient_pa,fatigue_strength_exponent=q.fatigue_strength_exponent,
        fatigue_ductility_coefficient=q.fatigue_ductility_coefficient,fatigue_ductility_exponent=q.fatigue_ductility_exponent,
        minimum_valid_cycles=q.minimum_valid_cycles,maximum_valid_cycles=q.maximum_valid_cycles)
    client=MaterialClient() if client is None else client
    job=prepare_material_job('fatigue.strain_life',parameters,c,location_id=q.location_id,evidence_ids=(q.calibration_evidence_id,),client=client)
    receipt=evaluate_material_job(job,c,client=client)
    return dict(status=receipt.native.status,strain_amplitude=amplitude,**base,native_material_receipt=receipt.model_dump(mode='json'),
                values=receipt.native.payload.get('values',{}))
