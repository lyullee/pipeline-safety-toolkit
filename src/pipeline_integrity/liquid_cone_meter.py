"""Liquid cone differential meter using the native actual-cone geometry leaf.

Coefficients and tap applicability are supplied calibration data. The native
Fluids mass-flow primitive receives the actual cone-end diameter; a fictitious
circular opening, gas exponent or default discharge coefficient is not used.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive


class LiquidConeMeterRequest(Contract):
    actual_pipe_inner_diameter_m: Positive
    actual_cone_end_diameter_m: Positive
    upstream_pressure_pa_abs: Positive
    downstream_pressure_pa_abs: Positive
    density_kg_m3: Positive | None = None
    dynamic_viscosity_pa_s: Positive | None = None
    discharge_coefficient: Positive | None = Field(default=None,le=1)
    coefficient_basis_id: Name | None = None
    actual_tap_geometry_basis_id: Name | None = None
    beta_support: tuple[Positive,Positive] | None = None
    pipe_reynolds_support: tuple[Nonnegative,Positive] | None = None
    actual_single_liquid_incompressible_nonflashing_and_taps_reviewed: bool = False
    coefficient_calibration_support_reviewed: bool = False

    @model_validator(mode='after')
    def geometry_and_support(self):
        if self.actual_cone_end_diameter_m >= self.actual_pipe_inner_diameter_m:
            raise ValueError('actual cone end must lie strictly inside actual pipe bore')
        for bounds in (self.beta_support,self.pipe_reynolds_support):
            if bounds is not None and bounds[0] > bounds[1]:raise ValueError('support bounds must be ordered')
        if self.beta_support is not None and self.beta_support[1] >= 1:
            raise ValueError('beta support must be strictly below one')
        return self


class LiquidConeMeterResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    actual_open_annulus_area_m2: Positive | None = None
    area_equivalent_beta: Positive | None = None
    volume_flow_m3_s: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    pipe_reynolds: Nonnegative | None = None
    measured_pressure_difference_pa: Nonnegative | None = None
    field_or_standard_qualified: bool = False


def evaluate_liquid_cone_meter(request: LiquidConeMeterRequest) -> LiquidConeMeterResult:
    """Return actual liquid flow for the reviewed upstream-to-cone tap pair.

    The observed tap differential is not the unrecoverable permanent pipe
    pressure loss. Reverse flow requires a different reviewed geometry/tap
    calibration and is withheld here. Expansibility is exactly one only in
    the caller-reviewed incompressible single-liquid domain.
    """
    def fail(state,reason):return LiquidConeMeterResult(state=state,reason=reason)
    r=request
    if not (r.actual_single_liquid_incompressible_nonflashing_and_taps_reviewed and r.coefficient_calibration_support_reviewed):
        return fail('needs_data','actual liquid/tap domain and coefficient calibration support need review')
    if any(v is None for v in (r.density_kg_m3,r.dynamic_viscosity_pa_s,r.discharge_coefficient,r.coefficient_basis_id,r.actual_tap_geometry_basis_id,r.beta_support,r.pipe_reynolds_support)):
        return fail('needs_data','actual liquid state, calibrated coefficient/taps and support are required')
    if r.downstream_pressure_pa_abs > r.upstream_pressure_pa_abs:
        return fail('out_of_scope','reverse tap ordering is outside the supplied forward cone calibration')
    from fluids.flow_meter import flow_meter_discharge,diameter_ratio_cone_meter,CONE_METER
    D,Dc=r.actual_pipe_inner_diameter_m,r.actual_cone_end_diameter_m
    beta=diameter_ratio_cone_meter(D,Dc)
    if not r.beta_support[0] <= beta <= r.beta_support[1]:
        return fail('out_of_scope','actual area-equivalent beta leaves the supplied calibration support')
    try:
        mass=flow_meter_discharge(D=D,Do=Dc,P1=r.upstream_pressure_pa_abs,P2=r.downstream_pressure_pa_abs,rho=r.density_kg_m3,C=r.discharge_coefficient,expansibility=1.,meter_type=CONE_METER)
    except (ArithmeticError,ValueError):
        return fail('out_of_scope','native cone flow leaves representable arithmetic')
    volume=mass/r.density_kg_m3
    re=4*mass/(math.pi*D*r.dynamic_viscosity_pa_s)
    if not r.pipe_reynolds_support[0] <= re <= r.pipe_reynolds_support[1]:
        return fail('out_of_scope','actual pipe Reynolds number leaves supplied coefficient support')
    annulus=math.pi/4*(D-Dc)*(D+Dc)
    dp=r.upstream_pressure_pa_abs-r.downstream_pressure_pa_abs
    if not all(math.isfinite(v) for v in (mass,volume,re,annulus)) or annulus <= 0 or (dp and (mass <= 0 or volume <= 0)):
        return fail('out_of_scope','actual geometry/pressure gives unrepresentable finite flow')
    return LiquidConeMeterResult(state='computed',reason='native actual-cone liquid mass-flow coordinate',actual_open_annulus_area_m2=annulus,area_equivalent_beta=beta,volume_flow_m3_s=volume,mass_flow_kg_s=mass,pipe_reynolds=re,measured_pressure_difference_pa=dp)
