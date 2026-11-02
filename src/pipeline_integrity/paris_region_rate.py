"""A scalar Region-II rate observation using the shared growth calibration.

The existing material owner supplies ordered depth integration. This leaf
exposes its constitutive input/output without constructing a growth history.
Material applicability and units are caller-reviewed; no fitted data are chosen.
"""
from __future__ import annotations

import math
import sys
from typing import Literal

from pydantic import Field

from .contracts import Contract, Name, Nonnegative, Positive, digest
from .fracture_exchange import GrowthCalibration


class ParisRegionRateRequest(Contract):
    id: Name
    material_condition_id: Name
    intensity_range_pa_sqrt_m: Nonnegative | None = None
    temperature_k: Positive | None = None
    stress_ratio: float | None = Field(default=None, ge=0, lt=1)
    calibration: GrowthCalibration | None = None
    region_ii_mode_i_opening_and_same_calibration_reviewed: bool = False


class ParisRegionRateResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','numerically_unrepresentable']
    request_digest: str
    growth_rate_m_per_whole_cycle: Nonnegative | None = None
    coefficient_dimension: Literal['m / (whole_cycle * (Pa*sqrt(m))**exponent)'] = 'm / (whole_cycle * (Pa*sqrt(m))**exponent)'
    issues: tuple[str,...] = ()
    integrated_crack_evolution_predicted: Literal[False] = False
    threshold_arrest_or_fast_fracture_predicted: Literal[False] = False
    material_calibration_or_field_acceptance_qualified: Literal[False] = False


def evaluate_paris_region_rate(request: ParisRegionRateRequest) -> ParisRegionRateResult:
    """Return da/dN in metres per whole cycle on a declared Region-II support.

    Temperature, stress ratio and material-condition bounds reuse the same
    calibration contract as the ordered-growth adapter. A state outside this
    range is withheld; this leaf cannot diagnose threshold arrest or fracture.
    """
    if not isinstance(request,ParisRegionRateRequest):
        raise TypeError('ParisRegionRateRequest required')
    q=ParisRegionRateRequest.model_validate(request.model_dump(mode='python'))
    base={'request_digest':digest(q)}
    issues=[]
    for name in ('intensity_range_pa_sqrt_m','temperature_k','stress_ratio','calibration'):
        if getattr(q,name) is None:issues.append('missing '+name)
    if not q.region_ii_mode_i_opening_and_same_calibration_reviewed:
        issues.append('Region-II opening and calibration basis require review')
    c=q.calibration
    if c is not None:
        if not c.applicability_reviewed:issues.append('calibration applicability requires review')
        for name in ('coefficient_m_cycle_per_pa_sqrt_m_power','exponent'):
            if getattr(c,name) is None:issues.append('missing calibration '+name)
    if issues:return ParisRegionRateResult(status='needs_data',issues=tuple(issues),**base)
    if c.material_condition_id!=q.material_condition_id:
        issues.append('material condition differs from calibration')
    if not c.minimum_temperature_k<=q.temperature_k<=c.maximum_temperature_k:
        issues.append('temperature outside calibration support')
    if not c.minimum_stress_ratio<=q.stress_ratio<=c.maximum_stress_ratio:
        issues.append('stress ratio outside calibration support')
    dk=q.intensity_range_pa_sqrt_m
    if not c.minimum_delta_k_pa_sqrt_m<=dk<=c.maximum_delta_k_pa_sqrt_m:
        issues.append('intensity range outside calibration support')
    if dk<=c.threshold_delta_k_pa_sqrt_m:
        issues.append('not an admitted Region-II intensity; arrest is not inferred')
    if issues:return ParisRegionRateResult(status='out_of_scope',issues=tuple(issues),**base)
    # Log coordinates avoid a spurious overflow of delta_K**m when the final
    # product is representable. Reject subnormal underflow as unresolved rather
    # than returning a false physical zero rate.
    logarithm=math.log(c.coefficient_m_cycle_per_pa_sqrt_m_power)+c.exponent*math.log(dk)
    if not math.log(sys.float_info.min)<=logarithm<=math.log(sys.float_info.max):
        return ParisRegionRateResult(status='numerically_unrepresentable',
            issues=('rate outside resolved normal floating-point support',),**base)
    try:rate=math.exp(logarithm)
    except OverflowError:rate=math.inf
    if not math.isfinite(rate) or rate<=0:
        return ParisRegionRateResult(status='numerically_unrepresentable',
            issues=('rate not resolved as a finite positive value',),**base)
    return ParisRegionRateResult(status='computed',growth_rate_m_per_whole_cycle=rate,**base)
