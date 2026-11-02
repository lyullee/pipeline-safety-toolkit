"""Forward liquid flow from a supplied conventional valve coefficient.

The existing library-backed sizing kernel remains the sole liquid valve law.
A caller-reviewed flow bracket permits nonlinear viscosity and recovery
branches without assuming that coefficient and flow are universally linear.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator
from scipy.optimize import brentq

from .contracts import Contract, Nonnegative, Positive
from .models.library_models import LiquidControlValve, liquid_control_valve


class LiquidValveForwardCapacityRequest(Contract):
    sizing_reference_case: LiquidControlValve
    coefficient_scale: Literal['Kv', 'Cv']
    supplied_coefficient: Nonnegative
    volume_flow_bracket_m3_s: tuple[Positive, Positive]
    increasing_single_liquid_sizing_branch_reviewed: bool = False
    relative_coefficient_tolerance: Positive = Field(default=1e-8, le=1e-3)
    absolute_flow_tolerance_m3_s: Positive = 1e-13

    @model_validator(mode='after')
    def bracket(self):
        if self.volume_flow_bracket_m3_s[0] >= self.volume_flow_bracket_m3_s[1]:
            raise ValueError('positive increasing flow bracket required')
        return self


def evaluate_liquid_valve_forward_capacity(request: LiquidValveForwardCapacityRequest):
    """Invert and replay the existing Kv sizing model on the reviewed bracket."""
    from fluids.control_valve import Cv_to_Kv, Kv_to_Cv

    q=LiquidValveForwardCapacityRequest.model_validate(request.model_dump())
    if not q.increasing_single_liquid_sizing_branch_reviewed:
        raise ValueError('reviewed single-liquid increasing sizing branch required')
    target=q.supplied_coefficient if q.coefficient_scale=='Kv' else Cv_to_Kv(q.supplied_coefficient)
    if target==0:
        return dict(volume_flow_m3_s=0.,mass_flow_kg_s=0.,kv=0.,cv=0.,
                    coefficient_residual=0.,diagnostics={'zero_coefficient':True},
                    device_or_calibration_qualification=False)

    def sizing(flow):
        case=LiquidControlValve.model_validate(q.sizing_reference_case.model_copy(
            update={'volume_flow_m3_s':flow}).model_dump())
        result=liquid_control_valve(case)
        if result.status!='computed' or not math.isfinite(result.values['kv']):
            raise ValueError('liquid sizing trial has no finite coefficient')
        if result.values['diagnostics'].get('warning'):
            raise ValueError('sizing kernel warning prevents a supported forward root')
        return result.values

    lo,hi=q.volume_flow_bracket_m3_s
    # Verify the declared monotone bracket at more than its two endpoints.
    samples=[sizing(lo+(hi-lo)*i/8)['kv'] for i in range(9)]
    if any(right<=left for left,right in zip(samples,samples[1:])):
        raise ValueError('sizing trials contradict the reviewed increasing branch')
    if not samples[0]<=target<=samples[-1]:
        raise ValueError('supplied coefficient is outside the reviewed flow bracket')
    flow=brentq(lambda value:sizing(value)['kv']-target,lo,hi,
                xtol=q.absolute_flow_tolerance_m3_s,rtol=1e-12)
    replay=sizing(flow)
    residual=replay['kv']-target
    if abs(residual)>q.relative_coefficient_tolerance*target:
        raise ValueError('forward capacity replay did not recover supplied coefficient')
    return dict(volume_flow_m3_s=flow,mass_flow_kg_s=flow*q.sizing_reference_case.density_kg_m3,
                kv=replay['kv'],cv=Kv_to_Cv(replay['kv']),coefficient_residual=residual,
                diagnostics=replay['diagnostics'],device_or_calibration_qualification=False)
