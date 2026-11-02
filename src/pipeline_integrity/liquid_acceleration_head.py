"""Observed rigid-column inertia from actual common flow acceleration.

This pointwise momentum observer needs actual dQ/dt. Pump speed, cylinder
count and a fitted peak multiplier alone do not determine a flow waveform.
Elastic waves, cavitation, valve switching, friction and gravity belong to
their respective pressure-budget terms; they are not duplicated here.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Nonnegative,Positive


class LiquidAccelerationSegment(Contract):
    length_m: Nonnegative
    actual_flow_area_m2: Positive


class LiquidAccelerationHeadRequest(Contract):
    segments: tuple[LiquidAccelerationSegment,...] = Field(min_length=1)
    density_kg_m3: Positive | None = None
    actual_common_flow_derivative_m3_s2: float | None = None
    actual_common_flow_m3_s: float | None = None
    gravity_m_s2: Positive = 9.80665
    forcing_basis_id: Name | None = None
    actual_forcing_and_single_liquid_rigid_column_reviewed: bool = False


class LiquidAccelerationHeadResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    signed_inlet_minus_outlet_inertial_pressure_pa: float | None = None
    signed_inertial_head_m: float | None = None
    segment_signed_pressure_pa: tuple[float,...] = ()
    segment_actual_velocity_derivative_m_s2: tuple[float,...] = ()
    hydraulic_inertance_kg_m4: Nonnegative | None = None
    instantaneous_kinetic_energy_j: Nonnegative | None = None
    signed_pressure_work_rate_w: float | None = None
    dissipated_power_w: Nonnegative = 0.
    field_or_standard_qualified: bool = False


def observe_liquid_acceleration_head(request: LiquidAccelerationHeadRequest) -> LiquidAccelerationHeadResult:
    """Inlet-minus-outlet pressure supports signed common-flow acceleration.

    Positive flow is from inlet to outlet. Negative inertial pressure/power
    can accompany deceleration: reversible stored kinetic energy is returned,
    rather than reported as a positive friction loss. Different segment areas
    retain their actual local acceleration. No entrance/junction steady
    kinetic-pressure change is included in this inertial term.
    """
    def fail(state,reason):return LiquidAccelerationHeadResult(state=state,reason=reason)
    rho=request.density_kg_m3;rate=request.actual_common_flow_derivative_m3_s2;q=request.actual_common_flow_m3_s
    if not request.actual_forcing_and_single_liquid_rigid_column_reviewed or not request.forcing_basis_id or rho is None or rate is None:
        return fail('needs_data','actual liquid density, dQ/dt, rigid-column and forcing basis require review')
    if not all(math.isfinite(v) for v in (rate,*(() if q is None else (q,)))):
        return fail('out_of_scope','actual forcing must be finite')
    try:
        geometric=math.fsum(s.length_m/s.actual_flow_area_m2 for s in request.segments)
    except OverflowError:
        return fail('out_of_scope','column geometric inertance sum overflows')
    inertance=rho*geometric
    acceleration=tuple(rate/s.actual_flow_area_m2 for s in request.segments)
    drops=tuple(rho*s.length_m*a for s,a in zip(request.segments,acceleration))
    try:
        pressure=math.fsum(drops)
    except OverflowError:
        return fail('out_of_scope','column pressure sum overflows')
    from fluids.core import head_from_P
    head=head_from_P(P=pressure,rho=rho,g=request.gravity_m_s2)
    energy=None if q is None else .5*inertance*q*q
    work=None if q is None else pressure*q
    values=(geometric,inertance,pressure,head,*drops,*acceleration,*(() if q is None else (energy,work)))
    if not all(math.isfinite(v) for v in values) or (rate and geometric and pressure == 0):
        return fail('out_of_scope','forcing/geometry exceeds finite representable inertia coordinates')
    return LiquidAccelerationHeadResult(state='computed',reason='actual prescribed-flow column momentum',signed_inlet_minus_outlet_inertial_pressure_pa=pressure,signed_inertial_head_m=head,segment_signed_pressure_pa=drops,segment_actual_velocity_derivative_m_s2=acceleration,hydraulic_inertance_kg_m4=inertance,instantaneous_kinetic_energy_j=energy,signed_pressure_work_rate_w=work)
