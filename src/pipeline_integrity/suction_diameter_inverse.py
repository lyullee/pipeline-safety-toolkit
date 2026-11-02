"""Bracketed suction diameter observation through existing Darcy/NPSH budgets."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative,Calculation
from .models.hydraulics import LiquidHeadLoss,liquid_head_loss
from .models.equipment import PumpNPSH,pump_npsh


class SuctionDiameterRequest(Contract):
    id: Name
    volume_flow_m3_s: Positive
    straight_length_m: Positive
    absolute_roughness_m: Nonnegative
    fixed_minor_loss_coefficient: Nonnegative
    density_kg_m3: Positive
    dynamic_viscosity_pa_s: Positive
    vapor_pressure_pa_abs: Positive
    reservoir_surface_pressure_pa_abs: Positive
    reservoir_surface_above_pump_m: float
    vendor_required_npsh_m: Nonnegative
    additional_required_margin_m: Nonnegative
    gravity_m_s2: Positive
    diameter_bracket_m: tuple[Positive,Positive]
    friction_method: Literal['Clamond','Colebrook','Haaland','Swamee_Jain_1976']='Clamond'
    fluid_state_basis_id: Name
    hydraulic_and_vendor_basis_id: Name
    prescribed_state_fixed_flow_reservoir_total_npsh_and_geometry_reviewed: bool=False
    diameter_absolute_tolerance_m: Positive=1e-10

    @model_validator(mode='after')
    def bracket(self):
        if self.diameter_bracket_m[1]<=self.diameter_bracket_m[0]:raise ValueError('strictly increasing diameter bracket required')
        if self.diameter_absolute_tolerance_m>=self.diameter_bracket_m[1]-self.diameter_bracket_m[0]:raise ValueError('diameter tolerance must resolve the supplied bracket')
        return self


def evaluate_suction_diameter(q:SuctionDiameterRequest)->Calculation:
    """Invert one supplied-state, fixed-flow NPSH equality on a declared bracket.

    Reservoir free-surface kinetic head is neglected. NPSH is total inlet head
    above saturation; inlet static pressure is reported separately after velocity
    head subtraction. Neither state properties nor vendor NPSHr are inferred.
    Straight length, absolute roughness and supplied minor K stay fixed as D varies.
    The whole bracket must stay in laminar or fully turbulent correlation support.
    """
    from scipy.optimize import brentq
    q=SuctionDiameterRequest.model_validate(q.model_dump(mode='python'))
    if not q.prescribed_state_fixed_flow_reservoir_total_npsh_and_geometry_reviewed:
        return Calculation(status='needs_data',values={},notes=('fluid state, fixed-flow loss geometry and vendor total-NPSH basis require review',))
    lo,hi=q.diameter_bracket_m;nu=q.dynamic_viscosity_pa_s/q.density_kg_m3
    if not math.isfinite(nu) or nu<=0:raise ValueError('kinematic viscosity is not positively representable')
    try:reynolds_bounds=[4*q.volume_flow_m3_s/(math.pi*d*nu) for d in (hi,lo)]
    except ArithmeticError as exc:raise ValueError('Reynolds number exceeds numeric support') from exc
    if not all(math.isfinite(v) and v>0 for v in reynolds_bounds):raise ValueError('Reynolds number exceeds support')
    if not (reynolds_bounds[1]<2040. or reynolds_bounds[0]>4000.):
        return Calculation(status='out_of_scope',values={},notes=('whole diameter bracket must stay below Re 2040 or above Re 4000; transition-spanning inverse withheld',))
    if q.absolute_roughness_m/lo>.1:
        return Calculation(status='out_of_scope',values={},notes=('relative roughness exceeds pipe correlation support within bracket',))
    if q.reservoir_surface_pressure_pa_abs<=q.vapor_pressure_pa_abs:
        return Calculation(status='out_of_scope',values={},notes=('reservoir surface state does not support the reviewed subcooled liquid assumption',))
    calls=0
    class Unavailable(Exception):pass
    def budget(d):
        nonlocal calls
        calls+=1
        loss=liquid_head_loss(LiquidHeadLoss(diameter_m=d,length_m=q.straight_length_m,roughness_m=q.absolute_roughness_m,
            volume_flow_m3_s=q.volume_flow_m3_s,minor_loss_coefficient=q.fixed_minor_loss_coefficient,
            kinematic_viscosity_m2_s=nu,gravity_m_s2=q.gravity_m_s2,friction_method=q.friction_method))
        if loss.status!='computed':raise Unavailable('; '.join(loss.notes))
        result=pump_npsh(PumpNPSH(surface_pressure_pa_abs=q.reservoir_surface_pressure_pa_abs,vapour_pressure_pa_abs=q.vapor_pressure_pa_abs,
            density_kg_m3=q.density_kg_m3,surface_above_pump_m=q.reservoir_surface_above_pump_m,
            suction_head_loss_m=loss.values['dissipative_head_loss_m'],vendor_required_npsh_m=q.vendor_required_npsh_m,gravity_m_s2=q.gravity_m_s2))
        residual=result.values['declared_margin_m']-q.additional_required_margin_m
        if not math.isfinite(residual):raise Unavailable('NPSH equality exceeds finite support')
        return residual,loss.values,result.values
    try:
        flo,_,_=budget(lo);fhi,_,_=budget(hi)
        if flo>0 or fhi<0:
            return Calculation(status='out_of_scope',values=dict(lower_margin_residual_m=flo,upper_margin_residual_m=fhi),notes=('equality diameter is outside the supplied bracket',))
        diameter=lo if flo==0 else hi if fhi==0 else brentq(lambda d:budget(d)[0],lo,hi,xtol=q.diameter_absolute_tolerance_m,rtol=1e-12)
        residual,loss,npsh=budget(diameter)
    except Unavailable as exc:return Calculation(status='out_of_scope',values={},notes=(str(exc),))
    velocity_head=loss['velocity_m_s']**2/(2*q.gravity_m_s2)
    static=q.reservoir_surface_pressure_pa_abs+q.density_kg_m3*q.gravity_m_s2*(q.reservoir_surface_above_pump_m-loss['dissipative_head_loss_m']-velocity_head)
    return Calculation(values=dict(equality_diameter_m=diameter,available_npsh_m=npsh['available_npsh_m'],
        residual_against_vendor_plus_margin_m=residual,suction_loss_receipt=loss,suction_velocity_head_m=velocity_head,
        inferred_pump_inlet_static_pressure_pa_abs=static,static_pressure_above_vapor_pa=static-q.vapor_pressure_pa_abs,
        static_liquid_pressure_feasible=static>q.vapor_pressure_pa_abs,reynolds_bracket=reynolds_bounds,hydraulic_evaluations=calls,
        fluid_state_basis_id=q.fluid_state_basis_id,hydraulic_and_vendor_basis_id=q.hydraulic_and_vendor_basis_id,
        commercial_size_vendor_curve_cavitation_or_field_acceptance_qualified=False),
        notes=('Equality inverse only. Inlet static feasibility is separate from total-NPSH equality; no cavitation inception or head-dependent vendor curve is predicted.',))
