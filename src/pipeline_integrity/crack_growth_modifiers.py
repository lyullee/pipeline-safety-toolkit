"""Threshold and critical-intensity modifiers of a supplied baseline growth rate."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative


class CrackGrowthModifierRequest(Contract):
    baseline_rate_m_cycle: Nonnegative | None = None
    intensity_range_pa_sqrt_m: Nonnegative | None = None
    maximum_intensity_pa_sqrt_m: Positive | None = None
    threshold_range_pa_sqrt_m: Nonnegative | None = None
    critical_intensity_pa_sqrt_m: Positive | None = None
    threshold_exponent: Positive | None = None
    critical_exponent: Positive | None = None
    calibration_basis_id: Name | None = None
    baseline_and_modifiers_same_calibration_reviewed: bool = False


class CrackGrowthModifierResult(Contract):
    status: Literal['computed','needs_data','critical_boundary_reached']
    calibration_basis_id: Name | None
    modified_rate_m_cycle: Nonnegative | None = None
    threshold_factor: Nonnegative | None = None
    critical_factor: Positive | None = None
    at_or_below_threshold: bool | None = None
    issues: tuple[str,...] = ()
    fracture_event_or_remaining_life_inferred: Literal[False] = False


def evaluate_crack_growth_modifiers(request: CrackGrowthModifierRequest) -> CrackGrowthModifierResult:
    """Modify an existing/caller baseline without duplicating its growth solver.

    All intensities use Pa sqrt(m) and the rate uses metres per whole cycle.
    Below-threshold arrest is a declared scalar-law branch. At the supplied
    critical boundary no divergent or negative-denominator rate is returned;
    that boundary alone does not establish a component fracture event.
    """
    if not isinstance(request,CrackGrowthModifierRequest):raise TypeError('CrackGrowthModifierRequest required')
    q=CrackGrowthModifierRequest.model_validate(request.model_dump(mode='python'))
    base=dict(calibration_basis_id=q.calibration_basis_id)
    required=('baseline_rate_m_cycle','intensity_range_pa_sqrt_m','maximum_intensity_pa_sqrt_m',
        'threshold_range_pa_sqrt_m','critical_intensity_pa_sqrt_m','threshold_exponent','critical_exponent','calibration_basis_id')
    missing=['missing '+k for k in required if getattr(q,k) is None]
    if not q.baseline_and_modifiers_same_calibration_reviewed:missing.append('baseline and modifier calibration require review')
    if missing:return CrackGrowthModifierResult(status='needs_data',issues=tuple(missing),**base)
    if q.maximum_intensity_pa_sqrt_m>=q.critical_intensity_pa_sqrt_m:
        return CrackGrowthModifierResult(status='critical_boundary_reached',**base)
    arrested=q.intensity_range_pa_sqrt_m<=q.threshold_range_pa_sqrt_m
    threshold=0. if arrested else math.exp(q.threshold_exponent*math.log1p(-q.threshold_range_pa_sqrt_m/q.intensity_range_pa_sqrt_m))
    critical=math.exp(-q.critical_exponent*math.log1p(-q.maximum_intensity_pa_sqrt_m/q.critical_intensity_pa_sqrt_m))
    rate=q.baseline_rate_m_cycle*threshold*critical
    if not all(math.isfinite(v) for v in (threshold,critical,rate)):raise ValueError('growth modifier exceeds finite numeric support')
    return CrackGrowthModifierResult(status='computed',**base,modified_rate_m_cycle=rate,
        threshold_factor=threshold,critical_factor=critical,at_or_below_threshold=arrested)
