"""Selected installed liquid-valve lift inverse and local static sensitivity."""
from __future__ import annotations

import math
from typing import Callable, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive
from .models.library_models import LiquidControlValve, liquid_control_valve


class InstalledValveRequest(Contract):
    liquid_state: LiquidControlValve
    target_volume_flow_m3_s: Positive
    valve_outlet_pressure_pa_abs: Positive
    lift_lower: float = Field(ge=0, le=1)
    lift_upper: float = Field(gt=0, le=1)
    curve_basis_id: Name
    pressure_balance_basis_id: Name
    continuous_increasing_trim_on_bracket_reviewed: bool = False
    single_liquid_unchoked_turbulent_state_reviewed: bool = False
    same_flow_static_pressure_balance_reviewed: bool = False
    relative_derivative_step: float = Field(default=1e-3, gt=1e-7, le=0.01)
    derivative_relative_tolerance: float = Field(default=1e-4, gt=0, le=0.01)
    minimum_pressure_slope_pa_s_m3: Positive = 1e-9

    @model_validator(mode='after')
    def interval(self):
        if self.lift_lower >= self.lift_upper:
            raise ValueError('ordered operating lift interval required')
        return self


class InstalledValveResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','not_bracketed','singular_local_response']
    lift_fraction: float | None = None
    required_cv: float | None = None
    valve_pressure_drop_pa: float | None = None
    valve_inlet_pressure_pa_abs: float | None = None
    local_static_gain_m3_s_per_lift: float | None = None
    required_cv_flow_derivative_s_m3: float | None = None
    trim_cv_lift_derivative: float | None = None
    balance_residual_cv: float | None = None
    issues: tuple[str,...] = ()
    global_operating_point_uniqueness_proved: Literal[False] = False
    dynamic_stability_or_field_performance_inferred: Literal[False] = False


def evaluate_installed_valve(
    request: InstalledValveRequest, *,
    pump_pressure_rise_pa: Callable[[float], float] | None,
    nonvalve_system_pressure_requirement_pa: Callable[[float], float] | None,
    trim_cv_at_lift: Callable[[float], float] | None,
) -> InstalledValveResult:
    """Infer operating-range lift at prescribed actual flow, then a local dQ/dlift.

    The supplied pump pressure rise minus the nonvalve system requirement is
    the valve's static-pressure budget in Pa. The latter must already account
    for reservoir levels, unequal reference velocity heads and all other losses.
    Both curves use the same actual volumetric flow and constant liquid state.
    Cv is the conventional US water coefficient, not an area or SI conductance.

    Only equal inlet/valve/outlet bores and the native unchoked turbulent branch
    are admitted. No hypothetical zero-loss fully open valve, hard seat at zero
    lift, trim calibration, cavitation prediction or control dynamics is inferred.
    A selected lift bracket is essential; no global flow-root uniqueness follows.
    """
    from fluids.control_valve import Kv_to_Cv
    from scipy.optimize import brentq

    if not isinstance(request, InstalledValveRequest):
        raise TypeError('InstalledValveRequest required')
    q = InstalledValveRequest.model_validate(request.model_dump(mode='python'))
    if not all((q.continuous_increasing_trim_on_bracket_reviewed,
                q.single_liquid_unchoked_turbulent_state_reviewed,
                q.same_flow_static_pressure_balance_reviewed)) or any(
        v is None for v in (pump_pressure_rise_pa, nonvalve_system_pressure_requirement_pa, trim_cv_at_lift)
    ):
        return InstalledValveResult(status='needs_data',issues=('reviewed trim, state, static balance and all three supplied curves required',))
    p = q.liquid_state
    if p.vapour_pressure_pa_abs >= min(q.valve_outlet_pressure_pa_abs,p.critical_pressure_pa_abs):
        return InstalledValveResult(status='out_of_scope',issues=('reviewed subcritical liquid outlet must remain above supplied vapour pressure',))
    if not (p.inlet_diameter_m == p.valve_diameter_m == p.outlet_diameter_m):
        return InstalledValveResult(status='out_of_scope',issues=('this observer requires equal reference bores; supplied balance must exclude the valve',))

    def finite(value, label):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(label+' must be finite')
        return value

    def required(flow):
        rise = finite(pump_pressure_rise_pa(flow),'pump pressure rise')
        load = finite(nonvalve_system_pressure_requirement_pa(flow),'nonvalve pressure requirement')
        dp = rise-load
        if dp <= 0:
            raise ValueError('positive valve static-pressure budget required at every evaluated flow')
        p1 = q.valve_outlet_pressure_pa_abs+dp
        native = liquid_control_valve(LiquidControlValve.model_validate(p.model_copy(update={
            'volume_flow_m3_s':flow,'inlet_pressure_pa_abs':p1,
            'outlet_pressure_pa_abs':q.valve_outlet_pressure_pa_abs,
        }).model_dump(mode='python'))).values
        diagnostics = native['diagnostics']
        if diagnostics.get('choked') or diagnostics.get('laminar') or diagnostics.get('warning'):
            raise ValueError('outside independently selected unchoked turbulent native branch')
        return finite(Kv_to_Cv(native['kv']),'required Cv'), dp, p1

    try:
        cv, dp, p1 = required(q.target_volume_flow_m3_s)
    except ValueError as error:
        return InstalledValveResult(status='out_of_scope',issues=(str(error),))

    def trim(lift):
        value = finite(trim_cv_at_lift(lift),'trim Cv')
        if value <= 0:
            raise ValueError('positive operating-range Cv required; hard shutoff is a separate regime')
        return value

    lo, hi = q.lift_lower,q.lift_upper
    samples = [trim(lo+(hi-lo)*i/16) for i in range(17)]
    if any(b <= a for a,b in zip(samples,samples[1:])):
        raise ValueError('supplied trim contradicts reviewed increasing bracket')
    at_lo=math.isclose(cv,samples[0],rel_tol=2e-14,abs_tol=0)
    at_hi=math.isclose(cv,samples[-1],rel_tol=2e-14,abs_tol=0)
    if not samples[0] <= cv <= samples[-1] and not (at_lo or at_hi):
        return InstalledValveResult(status='not_bracketed',required_cv=cv,valve_pressure_drop_pa=dp,
            valve_inlet_pressure_pa_abs=p1,issues=('required Cv is outside the declared operating lift range',))
    lift = lo if at_lo else hi if at_hi else brentq(lambda h:trim(h)-cv,lo,hi,xtol=1e-13,rtol=1e-13)
    base = dict(lift_fraction=lift,required_cv=cv,valve_pressure_drop_pa=dp,
        valve_inlet_pressure_pa_abs=p1,balance_residual_cv=trim(lift)-cv)
    # A bracket endpoint has no guaranteed two-sided operating derivative.
    dh = min((hi-lo)*q.relative_derivative_step, (lift-lo)/2, (hi-lift)/2)
    if dh <= 1e-12:
        return InstalledValveResult(status='singular_local_response',**base,
            issues=('lift root is at operating-range boundary; two-sided local gain withheld',))
    flow = q.target_volume_flow_m3_s
    dq = flow*q.relative_derivative_step
    try:
        derivatives=[]
        for scale in (1,0.5):
            derivatives.append(((required(flow+dq*scale)[0]-required(flow-dq*scale)[0])/(2*dq*scale),
                (trim(lift+dh*scale)-trim(lift-dh*scale))/(2*dh*scale)))
    except ValueError as error:
        return InstalledValveResult(status='out_of_scope',**base,issues=(str(error),))
    broad,narrow=derivatives
    for a,b in zip(broad,narrow):
        if abs(a-b)>q.derivative_relative_tolerance*max(abs(a),abs(b),1e-30):
            return InstalledValveResult(status='singular_local_response',**base,
                issues=('local derivative fails step-refinement consistency; nonsmooth or unresolved curve',))
    dc_dq,dc_dh=narrow
    # Cv sensitivity is equivalent to a pressure-balance Jacobian only on the
    # declared turbulent nonchoked fixed-state branch.
    pressure_slope = 2*dp*dc_dq/cv
    if dc_dh <= 0 or dc_dq <= 0 or pressure_slope <= q.minimum_pressure_slope_pa_s_m3:
        return InstalledValveResult(status='singular_local_response',**base,
            issues=('turning, reversed or vanishing local operating-response Jacobian',))
    return InstalledValveResult(status='computed',**base,local_static_gain_m3_s_per_lift=dc_dh/dc_dq,
        required_cv_flow_derivative_s_m3=dc_dq,trim_cv_lift_derivative=dc_dh)
