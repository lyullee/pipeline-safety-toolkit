"""Two-state effective current from the cycle integral of squared current."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Fraction,Name,Nonnegative,Positive


class TwoStateCurrentRequest(Contract):
    active_state_rms_current_A: Nonnegative | None = None
    idle_state_rms_current_A: Nonnegative | None = None
    active_time_fraction: Fraction | None = None
    cycle_period_s: Positive | None = None
    cycle_clock_id: Name | None = None
    constant_resistance_ohm: Nonnegative | None = None
    two_state_rms_and_time_fraction_basis_reviewed: bool = False

    @model_validator(mode='after')
    def clock_basis(self):
        if self.cycle_period_s is not None and self.cycle_clock_id is None:
            raise ValueError('cycle period requires an explicit clock identity')
        return self


class TwoStateCurrentResult(Contract):
    status: Literal['computed','needs_data']
    effective_rms_current_A: Nonnegative | None = None
    mean_squared_current_A2: Nonnegative | None = None
    cycle_current_squared_integral_A2_s: Nonnegative | None = None
    cycle_clock_id: Name | None = None
    mean_joule_power_W: Nonnegative | None = None
    cycle_joule_energy_J: Nonnegative | None = None
    issues: tuple[str,...] = ()


def observe_two_state_current(q:TwoStateCurrentRequest)->TwoStateCurrentResult:
    """Combine state RMS values by duration, not by averaging their magnitudes.

    The optional resistor is constant over both states. No voltage, conductor
    temperature, protection setting or equipment acceptance follows from RMS.
    A missing zero-duration state's value is irrelevant, not assigned a value.
    """
    q=TwoStateCurrentRequest.model_validate(q.model_dump(mode='python'))
    common=dict(cycle_clock_id=q.cycle_clock_id)
    if not q.two_state_rms_and_time_fraction_basis_reviewed or q.active_time_fraction is None:
        return TwoStateCurrentResult(status='needs_data',issues=('time-fraction and state RMS basis required',),**common)
    weights=(q.active_time_fraction,1-q.active_time_fraction)
    currents=(q.active_state_rms_current_A,q.idle_state_rms_current_A)
    if any(w>0 and i is None for w,i in zip(weights,currents)):
        return TwoStateCurrentResult(status='needs_data',issues=('every positive-duration state needs its RMS current',),**common)
    rms=math.hypot(*(math.sqrt(w)*i for w,i in zip(weights,currents) if w>0))
    squared=rms*rms
    power=q.constant_resistance_ohm*squared if q.constant_resistance_ohm is not None else None
    return TwoStateCurrentResult(status='computed',effective_rms_current_A=rms,
        mean_squared_current_A2=squared,
        cycle_current_squared_integral_A2_s=squared*q.cycle_period_s if q.cycle_period_s is not None else None,
        mean_joule_power_W=power,
        cycle_joule_energy_J=power*q.cycle_period_s if power is not None and q.cycle_period_s is not None else None,
        **common)
