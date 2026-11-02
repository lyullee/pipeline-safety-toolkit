"""Calibrated stress-life curve coordinates through the existing Basquin owner."""
from __future__ import annotations

import math
from typing import Literal

from .adapters import MaterialClient
from .contracts import Contract,Name,Nonnegative,Positive,RunContext,digest
from .material_exchange import evaluate_material_job,prepare_material_job

StressCoordinate=Literal['effective_amplitude','full_range','maximum_stress_same_cycle_basis']


class StressLifeCoordinateRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    time_s: Nonnegative
    coordinate_semantics: StressCoordinate
    query_stress_coordinate_pa: Positive | None = None
    reference_stress_coordinate_pa: Positive | None = None
    reference_life_cycles: Positive | None = None
    positive_inverse_log_slope: Positive | None = None
    supplied_query_stress_multiplier: Positive = 1.
    calibration_basis_id: Name | None = None
    curve_evidence_id: Name
    curve_and_cycle_coordinate_reviewed: bool = False


class StressLifeCoordinateResult(Contract):
    status: Literal['computed','needs_data','solver_withheld']
    request_digest: str
    context_digest: str
    coordinate_semantics: StressCoordinate
    calibration_basis_id: Name | None
    time_s: Nonnegative
    curve_life_cycles: Positive | None = None
    native_range_is_virtual_curve_coordinate: bool = False
    native_material_receipt: dict | None = None
    issues: tuple[str,...] = ()
    cycles_are_whole_cycles: Literal[True] = True
    field_or_material_fatigue_qualification: Literal[False] = False


def evaluate_stress_life_coordinate(request: StressLifeCoordinateRequest,
                                     context: RunContext,*,client=None) -> StressLifeCoordinateResult:
    """Map a reviewed reference point and slope to the owner's stress-life curve.

    Amplitudes are doubled to full ranges at both the query and reference point;
    cycle counts remain whole cycles. A maximum-stress calibration can use the
    same scalar curve only on its explicitly supplied common cycle basis. In
    that mode the native range field is a virtual curve coordinate, not an
    inferred physical stress range. No mean-stress correction or material curve
    is selected, fitted or extrapolated into another cycle convention.
    """
    if not isinstance(request,StressLifeCoordinateRequest):raise TypeError('StressLifeCoordinateRequest required')
    q=StressLifeCoordinateRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('curve and context asset/clock differ')
    if q.curve_evidence_id not in {e.id for e in c.evidence}:raise ValueError('curve calibration evidence is absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),coordinate_semantics=q.coordinate_semantics,
        calibration_basis_id=q.calibration_basis_id,time_s=q.time_s)
    fields=('query_stress_coordinate_pa','reference_stress_coordinate_pa','reference_life_cycles',
            'positive_inverse_log_slope','calibration_basis_id')
    issues=['missing '+name for name in fields if getattr(q,name) is None]
    if not q.curve_and_cycle_coordinate_reviewed:issues.append('curve and cycle-coordinate basis require review')
    if issues:return StressLifeCoordinateResult(status='needs_data',issues=tuple(issues),**base)
    factor=2. if q.coordinate_semantics=='effective_amplitude' else 1.
    query=factor*q.query_stress_coordinate_pa*q.supplied_query_stress_multiplier
    reference=factor*q.reference_stress_coordinate_pa
    if not math.isfinite(query) or not math.isfinite(reference):raise ValueError('stress coordinate exceeds finite support')
    client=MaterialClient() if client is None else client
    parameters=dict(stress_range_Pa=query,reference_range_Pa=reference,
        reference_cycles=q.reference_life_cycles,exponent=q.positive_inverse_log_slope)
    job=prepare_material_job('fatigue.basquin',parameters,c,location_id=q.location_id,
        evidence_ids=(q.curve_evidence_id,),client=client)
    receipt=evaluate_material_job(job,c,client=client)
    if receipt.native.status!='computed':
        return StressLifeCoordinateResult(status='solver_withheld',**base,issues=('existing curve engine withheld calculation',))
    life=receipt.native.payload['values']['cycles_to_failure']
    if not math.isfinite(life) or life<=0:raise ValueError('native cycle life is not finite and positive')
    return StressLifeCoordinateResult(status='computed',**base,curve_life_cycles=life,
        native_range_is_virtual_curve_coordinate=q.coordinate_semantics=='maximum_stress_same_cycle_basis',
        native_material_receipt=receipt.model_dump(mode='json'))
