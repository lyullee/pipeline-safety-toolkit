"""Reviewed elastic tensile-mean fatigue coordinates through one existing owner."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job


class MeanStressCoordinateRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    time_s: Nonnegative
    correction: Literal['goodman','soderberg','gerber']
    alternating_amplitude_pa: Nonnegative | None = None
    tensile_mean_stress_pa: float | None = None
    yield_stress_pa: Positive | None = None
    ultimate_strength_pa: Positive | None = None
    maximum_supported_equivalent_amplitude_pa: Positive | None = None
    calibration_basis_id: Name | None = None
    calibration_evidence_id: Name
    elastic_tensile_mean_and_correction_calibration_reviewed: bool = False


class MeanStressCoordinateResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','solver_withheld']
    request_digest: str
    context_digest: str
    correction: Literal['goodman','soderberg','gerber']
    calibration_basis_id: Name | None
    equivalent_fully_reversed_amplitude_pa: Nonnegative | None = None
    native_material_receipt: dict | None = None
    native_mean_is_virtual_coordinate: bool = False
    native_strength_is_virtual_normalization: bool = False
    issues: tuple[str,...] = ()
    physical_stress_or_fatigue_life_predicted: Literal[False] = False


def evaluate_mean_stress_coordinate(request: MeanStressCoordinateRequest,context: RunContext,*,client=None) -> MeanStressCoordinateResult:
    """Map reviewed elastic cycles to an equivalent curve amplitude.

    One existing linear-mean kernel supplies all three declared coordinates.
    Soderberg substitutes yield strength only as the kernel normalization;
    Gerber uses a virtual mean that preserves its quadratic penalty. Neither
    substitution relabels an actual material strength or stress. No fatigue
    curve, cycle life, compressive-mean benefit or plastic correction is chosen.
    """
    if not isinstance(request,MeanStressCoordinateRequest):raise TypeError('MeanStressCoordinateRequest required')
    q=MeanStressCoordinateRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('coordinate and context asset/clock differ')
    if q.calibration_evidence_id not in {e.id for e in c.evidence}:raise ValueError('correction calibration evidence is absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),correction=q.correction,calibration_basis_id=q.calibration_basis_id)
    required=['alternating_amplitude_pa','tensile_mean_stress_pa','yield_stress_pa','maximum_supported_equivalent_amplitude_pa','calibration_basis_id']
    if q.correction!='soderberg':required.append('ultimate_strength_pa')
    missing=['missing '+k for k in required if getattr(q,k) is None]
    if not q.elastic_tensile_mean_and_correction_calibration_reviewed:missing.append('elastic tensile-mean correction and calibration require review')
    if missing:return MeanStressCoordinateResult(status='needs_data',issues=tuple(missing),**base)
    if q.ultimate_strength_pa is not None and q.ultimate_strength_pa<q.yield_stress_pa:
        raise ValueError('supplied ultimate strength is below supplied yield strength')
    if q.tensile_mean_stress_pa<0 or q.tensile_mean_stress_pa+q.alternating_amplitude_pa>=q.yield_stress_pa:
        return MeanStressCoordinateResult(status='out_of_scope',issues=('the declared branch requires nonnegative mean and tensile peak below supplied yield',),**base)
    strength=q.yield_stress_pa if q.correction=='soderberg' else q.ultimate_strength_pa
    native_mean=q.tensile_mean_stress_pa
    if q.correction=='gerber':native_mean=(native_mean/strength)*native_mean
    parameters=dict(alternating_stress_Pa=q.alternating_amplitude_pa,mean_stress_Pa=native_mean,ultimate_tensile_strength_Pa=strength)
    client=MaterialClient() if client is None else client
    job=prepare_material_job('fatigue.goodman',parameters,c,location_id=q.location_id,evidence_ids=(q.calibration_evidence_id,),client=client)
    receipt=evaluate_material_job(job,c,client=client)
    if receipt.native.status!='computed':return MeanStressCoordinateResult(status='solver_withheld',issues=('existing mean-correction owner withheld output',),**base)
    amplitude=receipt.native.payload['values']['equivalent_fully_reversed_amplitude_Pa']
    if not math.isfinite(amplitude) or amplitude<0:raise ValueError('native equivalent amplitude is not finite/nonnegative')
    if amplitude>q.maximum_supported_equivalent_amplitude_pa:
        return MeanStressCoordinateResult(status='out_of_scope',issues=('equivalent coordinate exceeds supplied curve/calibration support',),**base)
    return MeanStressCoordinateResult(status='computed',**base,equivalent_fully_reversed_amplitude_pa=amplitude,
        native_material_receipt=receipt.model_dump(mode='json'),native_mean_is_virtual_coordinate=q.correction=='gerber',
        native_strength_is_virtual_normalization=q.correction=='soderberg')
