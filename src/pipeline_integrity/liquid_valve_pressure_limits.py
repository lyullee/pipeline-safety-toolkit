"""Separate liquid choking and declared cavitation-inception pressure criteria."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field,model_validator

from .contracts import Contract,Name,Nonnegative,Positive


class LiquidValvePressureLimitsRequest(Contract):
    inception_criterion: Literal['nominal_recovery_proxy','supplied_inception_index']
    inlet_pressure_pa_abs: Positive | None = None
    outlet_pressure_pa_abs: Positive | None = None
    vapour_pressure_pa_abs: Positive | None = None
    critical_pressure_pa_abs: Positive | None = None
    pressure_recovery_factor: float | None = Field(default=None,gt=0,le=1)
    supplied_inception_index: Positive | None = None
    inception_index_basis_id: Name | None = None
    pressure_state_and_factor_bases_reviewed: bool = False

    @model_validator(mode='after')
    def state(self):
        if self.inlet_pressure_pa_abs is not None and self.outlet_pressure_pa_abs is not None:
            if self.inlet_pressure_pa_abs<self.outlet_pressure_pa_abs:raise ValueError('directed liquid pressure drop must be nonnegative')
        if self.vapour_pressure_pa_abs is not None:
            if self.inlet_pressure_pa_abs is not None and self.inlet_pressure_pa_abs<=self.vapour_pressure_pa_abs:
                raise ValueError('liquid inlet must be above supplied vapour pressure')
            if self.critical_pressure_pa_abs is not None and self.vapour_pressure_pa_abs>=self.critical_pressure_pa_abs:
                raise ValueError('supplied subcritical vapour pressure must be below critical pressure')
        return self


class LiquidValvePressureLimitsResult(Contract):
    status: Literal['computed','needs_data']
    inception_criterion: Literal['nominal_recovery_proxy','supplied_inception_index']
    inception_index_basis_id: Name | None
    pressure_drop_pa: Nonnegative | None = None
    cavitation_index: Positive | None = None
    incipient_pressure_drop_limit_pa: Positive | None = None
    choked_pressure_drop_limit_pa: Positive | None = None
    at_or_above_declared_inception_limit: bool | None = None
    at_or_above_choked_limit: bool | None = None
    outlet_below_vapour_pressure: bool | None = None
    issues: tuple[str,...] = ()
    cavitation_damage_or_phase_fraction_inferred: Literal[False] = False


def evaluate_liquid_valve_pressure_limits(request: LiquidValvePressureLimitsRequest) -> LiquidValvePressureLimitsResult:
    """Keep a nominal FL-squared proxy separate from a supplied inception index.

    Choking uses the existing FL/FF pressure-limit kernel. The nominal proxy
    uses FL squared times the inlet-to-vapour pressure budget. A measured
    incipient index may replace that proxy only with its explicit basis ID.
    Neither option infers bubble populations, erosion, phase fraction or device
    acceptance. At zero pressure drop the index has no finite value; the finite
    pressure limits are still comparable without manufacturing infinity.
    """
    if not isinstance(request,LiquidValvePressureLimitsRequest):raise TypeError('LiquidValvePressureLimitsRequest required')
    q=LiquidValvePressureLimitsRequest.model_validate(request.model_dump(mode='python'))
    base=dict(inception_criterion=q.inception_criterion,inception_index_basis_id=q.inception_index_basis_id)
    required=('inlet_pressure_pa_abs','outlet_pressure_pa_abs','vapour_pressure_pa_abs')
    issues=['missing '+k for k in required if getattr(q,k) is None]
    if not q.pressure_state_and_factor_bases_reviewed:issues.append('liquid state and pressure-factor bases require review')
    if issues:return LiquidValvePressureLimitsResult(status='needs_data',issues=tuple(issues),**base)
    from fluids.control_valve import cavitation_index,control_valve_choke_P_l
    drop=q.inlet_pressure_pa_abs-q.outlet_pressure_pa_abs
    index=None if drop==0 else cavitation_index(q.inlet_pressure_pa_abs,q.outlet_pressure_pa_abs,q.vapour_pressure_pa_abs)
    choked_limit=None;choked=None;incipient_limit=None;incipient=None
    if q.critical_pressure_pa_abs is None or q.pressure_recovery_factor is None:
        issues.append('missing choking critical-pressure/recovery inputs')
    else:
        terminal=control_valve_choke_P_l(Psat=q.vapour_pressure_pa_abs,Pc=q.critical_pressure_pa_abs,
            FL=q.pressure_recovery_factor,P1=q.inlet_pressure_pa_abs)
        choked_limit=q.inlet_pressure_pa_abs-terminal
        choked=drop>=choked_limit
    if q.inception_criterion=='nominal_recovery_proxy':
        if q.pressure_recovery_factor is None:issues.append('missing recovery factor for selected nominal inception proxy')
        else:incipient_limit=q.pressure_recovery_factor**2*(q.inlet_pressure_pa_abs-q.vapour_pressure_pa_abs)
    elif q.supplied_inception_index is None or q.inception_index_basis_id is None:
        issues.append('missing supplied inception index or its calibration basis')
    else:incipient_limit=(q.inlet_pressure_pa_abs-q.vapour_pressure_pa_abs)/q.supplied_inception_index
    if incipient_limit is not None:
        if choked_limit is not None and incipient_limit>choked_limit*(1+1e-12):
            issues.append('supplied inception criterion would occur after choking; factor bases need reconciliation')
        else:incipient=drop>=incipient_limit
    numeric=[v for v in (index,choked_limit,incipient_limit) if v is not None]
    if not all(math.isfinite(v) and v>0 for v in numeric):raise ValueError('pressure criterion exceeds positive finite support')
    return LiquidValvePressureLimitsResult(status='needs_data' if issues else 'computed',**base,
        pressure_drop_pa=drop,cavitation_index=index,incipient_pressure_drop_limit_pa=incipient_limit,
        choked_pressure_drop_limit_pa=choked_limit,at_or_above_declared_inception_limit=incipient,
        at_or_above_choked_limit=choked,outlet_below_vapour_pressure=q.outlet_pressure_pa_abs<q.vapour_pressure_pa_abs,
        issues=tuple(issues))
