"""Two-terminal series/parallel liquid paths built from existing loss primitives.

This adapter supplies topology and bracketed inverses. Darcy friction remains
with the existing liquid-pressure owner. Every local K retains its actual
reference diameter/velocity; no universal friction-factor multiplier is added.
No intermediate supply, pump, arbitrary graph or compressible relief-line
closure is implied by a set of paths sharing two endpoints.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive


class ReferencedLiquidLoss(Contract):
    """Irreversible mechanical-energy loss K at an actual local mean velocity."""
    coefficient: Nonnegative | None = None
    velocity_reference_diameter_m: Positive | None = None
    coefficient_basis_id: Name | None = None
    applicable_flow_sign: Literal["positive", "negative", "both"]
    dimensionless_velocity_head_definition_reviewed: bool = False


class LiquidPipePathElement(Contract):
    diameter_m: Positive
    length_m: Nonnegative
    roughness_m: Nonnegative
    local_losses: tuple[ReferencedLiquidLoss, ...] = ()


class LiquidSeriesPath(Contract):
    path_id: Name
    elements: tuple[LiquidPipePathElement, ...] = Field(min_length=1)


class LiquidEquivalentReferencePipe(Contract):
    diameter_m: Positive
    roughness_m: Nonnegative
    reference_basis_id: Name


class LiquidPathSystemRequest(Contract):
    paths: tuple[LiquidSeriesPath, ...] = Field(min_length=1)
    boundary: Literal["prescribed_total_flow", "prescribed_pressure_difference"]
    inlet_node_id: Name
    outlet_node_id: Name
    outlet_minus_inlet_elevation_m: float
    pressure_basis: Literal["mechanical_total_pressure", "static_with_endpoint_kinetic_state"]
    inlet_endpoint_flow_area_m2: Positive | None = None
    outlet_endpoint_flow_area_m2: Positive | None = None
    inlet_endpoint_kinetic_energy_factor: Positive | None = None
    outlet_endpoint_kinetic_energy_factor: Positive | None = None
    density_kg_m3: Positive | None = None
    dynamic_viscosity_pa_s: Positive | None = None
    liquid_state_basis_id: Name | None = None
    gravity_m_s2: Positive = 9.80665
    total_volume_flow_m3_s: float | None = None
    inlet_minus_outlet_pressure_pa: float | None = None
    branch_flow_brackets_m3_s: tuple[tuple[float, float], ...] | None = None
    common_total_pressure_bracket_pa: tuple[float, float] | None = None
    equivalent_reference_pipe: LiquidEquivalentReferencePipe | None = None
    no_intermediate_injection_and_common_end_nodes_reviewed: bool = False
    actual_single_liquid_state_and_all_loss_bases_reviewed: bool = False
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"
    flow_root_absolute_tolerance_m3_s: Positive = 1e-12
    pressure_residual_absolute_tolerance_pa: Positive = 1e-6
    pressure_residual_relative_tolerance: Nonnegative = 1e-9
    continuity_absolute_tolerance_m3_s: Positive = 1e-10

    @model_validator(mode="after")
    def boundary_and_brackets(self):
        if self.boundary == "prescribed_total_flow" and self.inlet_minus_outlet_pressure_pa is not None:
            raise ValueError("a total-flow boundary cannot prescribe pressure difference too")
        if self.boundary == "prescribed_pressure_difference" and self.total_volume_flow_m3_s is not None:
            raise ValueError("a pressure boundary cannot prescribe total flow too")
        if self.branch_flow_brackets_m3_s is not None:
            if len(self.branch_flow_brackets_m3_s) != len(self.paths):raise ValueError("one flow bracket per path is required")
            if any(a >= b for a,b in self.branch_flow_brackets_m3_s):raise ValueError("flow brackets must be ordered")
        if self.common_total_pressure_bracket_pa is not None and self.common_total_pressure_bracket_pa[0] >= self.common_total_pressure_bracket_pa[1]:
            raise ValueError("pressure bracket must be ordered")
        return self


class LiquidElementDrop(Contract):
    velocity_m_s: float
    reynolds: Nonnegative
    darcy_friction_factor: Nonnegative
    signed_pipe_friction_drop_pa: float
    signed_local_loss_drop_pa: float
    actual_local_reference_velocities_m_s: tuple[float, ...]


class LiquidPathResult(Contract):
    path_id: Name
    volume_flow_m3_s: float
    signed_friction_drop_pa: float
    static_drop_pa: float
    required_inlet_minus_outlet_total_pressure_pa: float
    hydraulic_dissipation_W: Nonnegative
    elements: tuple[LiquidElementDrop, ...]


class LiquidPathSystemResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    total_volume_flow_m3_s: float | None = None
    total_mass_flow_kg_s: float | None = None
    common_inlet_minus_outlet_pressure_pa: float | None = None
    pressure_basis: Literal["mechanical_total_pressure", "static_with_endpoint_kinetic_state"] | None = None
    common_inlet_minus_outlet_total_pressure_pa: float | None = None
    endpoint_kinetic_pressure_change_pa: float | None = None
    total_hydraulic_dissipation_W: Nonnegative | None = None
    continuity_residual_m3_s: float | None = None
    maximum_branch_total_pressure_residual_pa: Nonnegative | None = None
    pointwise_friction_secant_resistance_pa_s_m3: Nonnegative | None = None
    resistance_interpretation: Literal['pointwise_at_reported_flow_not_frozen_global_curve'] = 'pointwise_at_reported_flow_not_frozen_global_curve'
    equivalent_reference_status: Literal['not_requested','computed_at_reported_total_flow','undefined_zero_flow','out_of_scope'] = 'not_requested'
    equivalent_reference_pipe_length_m: Nonnegative | None = None
    equivalent_reference_basis_id: Name | None = None
    paths: tuple[LiquidPathResult, ...] = ()
    absolute_pressure_phase_or_cavitation_margin_inferred: bool = False
    arbitrary_graph_or_compressible_line_solved: bool = False
    field_calibration_qualified: bool = False
    issues: tuple[str, ...] = ()


def _path(q: LiquidPathSystemRequest, geometry: LiquidSeriesPath, flow: float) -> LiquidPathResult:
    from fluids.core import dP_from_K
    from .models.hydraulics import LiquidPressureLoss, liquid_pressure_loss
    rows=[];drops=[]
    for e in geometry.elements:
        native=liquid_pressure_loss(LiquidPressureLoss(diameter_m=e.diameter_m,length_m=e.length_m,
            roughness_m=e.roughness_m,volume_flow_m3_s=flow,density_kg_m3=q.density_kg_m3,
            viscosity_pa_s=q.dynamic_viscosity_pa_s,minor_loss_coefficient=0.,outlet_minus_inlet_elevation_m=0.,
            gravity_m_s2=q.gravity_m_s2,transition_policy=q.transition_policy,friction_method=q.friction_method))
        if native.status!='computed':raise ValueError('native Darcy path support: '+str(native.notes))
        local=[];velocities=[]
        for loss in e.local_losses:
            if (flow>0 and loss.applicable_flow_sign=='negative') or (flow<0 and loss.applicable_flow_sign=='positive'):
                raise ValueError('local coefficient is not reviewed for this flow direction')
            velocity=4*flow/(math.pi*loss.velocity_reference_diameter_m**2)
            magnitude=dP_from_K(loss.coefficient,q.density_kg_m3,velocity)
            if not math.isfinite(velocity) or not math.isfinite(magnitude):raise ArithmeticError
            if flow and (velocity==0 or (loss.coefficient>0 and magnitude==0)):raise ArithmeticError
            velocities.append(velocity);local.append(math.copysign(magnitude,flow) if flow else 0.)
        local_drop=math.fsum(local);pipe_drop=native.values['dissipative_pressure_loss_pa'];drops.extend((pipe_drop,local_drop))
        rows.append(LiquidElementDrop(velocity_m_s=native.values['velocity_m_s'],reynolds=native.values['reynolds'],
            darcy_friction_factor=native.values['darcy_friction_factor'],signed_pipe_friction_drop_pa=pipe_drop,
            signed_local_loss_drop_pa=local_drop,actual_local_reference_velocities_m_s=tuple(velocities)))
    friction=math.fsum(drops);static=q.density_kg_m3*q.gravity_m_s2*q.outlet_minus_inlet_elevation_m
    pressure=math.fsum((friction,static));power=friction*flow
    if not all(math.isfinite(v) for v in (friction,static,pressure,power)) or power<0:raise ArithmeticError
    active=any(e.length_m>0 or any(loss.coefficient>0 for loss in e.local_losses) for e in geometry.elements)
    if flow and active and (friction==0 or power<=0):raise ArithmeticError
    return LiquidPathResult(path_id=geometry.path_id,volume_flow_m3_s=flow,signed_friction_drop_pa=friction,
        static_drop_pa=static,required_inlet_minus_outlet_total_pressure_pa=pressure,hydraulic_dissipation_W=power,elements=tuple(rows))


def evaluate_liquid_path_system(request: LiquidPathSystemRequest) -> LiquidPathSystemResult:
    """Compose actual paths and invert only within supplied numerical brackets.

    A bracket is not a phase/transition exemption: every trial and final point
    must satisfy the native correlation policy and local-loss direction basis.
    Pressure and continuity tolerances are solver budgets, not sensor accuracy.
    Mechanical total pressure includes the actual kinetic-energy flux term.
    Static pressure additionally needs both common-node endpoint areas and
    their supplied kinetic-energy correction factors; none is inferred from
    the diameters or mean velocities of individual parallel branches.
    The reported secant resistance and optional reference-pipe length are
    pointwise at the solved total flow. Variable-Reynolds friction and local
    losses do not establish a globally frozen equivalent pipe curve.
    """
    if not isinstance(request,LiquidPathSystemRequest):raise TypeError('LiquidPathSystemRequest required')
    q=LiquidPathSystemRequest.model_validate(request.model_dump(mode='python'))
    missing=[]
    if any(getattr(q,n) is None for n in ('density_kg_m3','dynamic_viscosity_pa_s','liquid_state_basis_id')):
        missing.append('actual reviewed liquid density, viscosity and state basis are required')
    if not q.no_intermediate_injection_and_common_end_nodes_reviewed or not q.actual_single_liquid_state_and_all_loss_bases_reviewed:
        missing.append('two-terminal no-injection topology and single-liquid/all-loss bases require review')
    if q.pressure_basis=='static_with_endpoint_kinetic_state' and any(getattr(q,n) is None for n in (
            'inlet_endpoint_flow_area_m2','outlet_endpoint_flow_area_m2','inlet_endpoint_kinetic_energy_factor','outlet_endpoint_kinetic_energy_factor')):
        missing.append('static pressure requires actual common-node endpoint areas and reviewed kinetic-energy factors')
    for path in q.paths:
        for e in path.elements:
            for loss in e.local_losses:
                if any(getattr(loss,n) is None for n in ('coefficient','velocity_reference_diameter_m','coefficient_basis_id')) or not loss.dimensionless_velocity_head_definition_reviewed:
                    missing.append('every local K needs its actual velocity diameter, coefficient basis and review')
    prescribed=q.total_volume_flow_m3_s if q.boundary=='prescribed_total_flow' else q.inlet_minus_outlet_pressure_pa
    if prescribed is None:missing.append('selected boundary observation is missing')
    inverse=not(q.boundary=='prescribed_total_flow' and len(q.paths)==1)
    if inverse and q.branch_flow_brackets_m3_s is None:missing.append('inverse paths require explicit per-branch signed flow brackets')
    if len(q.paths)>1 and (q.boundary=='prescribed_total_flow' or q.pressure_basis=='static_with_endpoint_kinetic_state') and q.common_total_pressure_bracket_pa is None:
        missing.append('parallel flow/static-pressure inverse requires a mechanical-total-pressure bracket')
    if missing:return LiquidPathSystemResult(status='needs_data',issues=tuple(missing))
    if q.inlet_node_id==q.outlet_node_id or len({p.path_id for p in q.paths})!=len(q.paths):
        return LiquidPathSystemResult(status='out_of_scope',issues=('distinct common endpoints and unique path identities are required',))
    if inverse and any(all(e.length_m==0 and all(loss.coefficient==0 for loss in e.local_losses) for e in path.elements) for path in q.paths):
        return LiquidPathSystemResult(status='out_of_scope',issues=('zero-resistance branch constraints are outside this bracketed per-path inverse; no arbitrary flow is pinned',))
    try:
        from scipy.optimize import brentq
        def kinetic_change(total):
            if q.pressure_basis=='mechanical_total_pressure':return 0.
            change=.5*q.density_kg_m3*(q.outlet_endpoint_kinetic_energy_factor*(total/q.outlet_endpoint_flow_area_m2)**2
                -q.inlet_endpoint_kinetic_energy_factor*(total/q.inlet_endpoint_flow_area_m2)**2)
            if not math.isfinite(change):raise ArithmeticError
            return change
        def solve_at_pressure(pressure):
            rows=[]
            tolerance=q.pressure_residual_absolute_tolerance_pa+q.pressure_residual_relative_tolerance*abs(pressure)
            for path,(lower,upper) in zip(q.paths,q.branch_flow_brackets_m3_s):
                def residual(flow):return _path(q,path,flow).required_inlet_minus_outlet_total_pressure_pa-pressure
                flow=brentq(residual,lower,upper,xtol=q.flow_root_absolute_tolerance_m3_s,rtol=1e-12,maxiter=150)
                row=_path(q,path,flow)
                if abs(row.required_inlet_minus_outlet_total_pressure_pa-pressure)>tolerance:
                    raise ValueError('root does not close pressure budget; possible discontinuous or unsupported branch law')
                rows.append(row)
            return tuple(rows)
        if not inverse:
            rows=(_path(q,q.paths[0],q.total_volume_flow_m3_s),);pressure=rows[0].required_inlet_minus_outlet_total_pressure_pa
        elif q.boundary=='prescribed_pressure_difference' and q.pressure_basis=='mechanical_total_pressure':
            pressure=q.inlet_minus_outlet_pressure_pa;rows=solve_at_pressure(pressure)
        elif q.boundary=='prescribed_pressure_difference' and len(q.paths)==1:
            def static_residual(flow):return _path(q,q.paths[0],flow).required_inlet_minus_outlet_total_pressure_pa+kinetic_change(flow)-q.inlet_minus_outlet_pressure_pa
            flow=brentq(static_residual,*q.branch_flow_brackets_m3_s[0],xtol=q.flow_root_absolute_tolerance_m3_s,rtol=1e-12,maxiter=150)
            rows=(_path(q,q.paths[0],flow),);pressure=rows[0].required_inlet_minus_outlet_total_pressure_pa
            if abs(static_residual(flow))>q.pressure_residual_absolute_tolerance_pa+q.pressure_residual_relative_tolerance*abs(q.inlet_minus_outlet_pressure_pa):
                raise ValueError('static pressure inverse does not close energy budget')
        else:
            def outer_residual(pressure):
                total=math.fsum(row.volume_flow_m3_s for row in solve_at_pressure(pressure))
                if q.boundary=='prescribed_total_flow':return total-q.total_volume_flow_m3_s
                return pressure+kinetic_change(total)-q.inlet_minus_outlet_pressure_pa
            pressure=brentq(outer_residual,*q.common_total_pressure_bracket_pa,xtol=q.pressure_residual_absolute_tolerance_pa,rtol=1e-12,maxiter=150)
            rows=solve_at_pressure(pressure)
        total=math.fsum(row.volume_flow_m3_s for row in rows);power=math.fsum(row.hydraulic_dissipation_W for row in rows)
        continuity_residual=total-q.total_volume_flow_m3_s if q.boundary=='prescribed_total_flow' else None
        if continuity_residual is not None and abs(continuity_residual)>q.continuity_absolute_tolerance_m3_s:raise ValueError('parallel root does not close volume balance')
        common_pressure=pressure+kinetic_change(total)
        if q.boundary=='prescribed_pressure_difference' and abs(common_pressure-q.inlet_minus_outlet_pressure_pa)>q.pressure_residual_absolute_tolerance_pa+q.pressure_residual_relative_tolerance*abs(q.inlet_minus_outlet_pressure_pa):
            raise ValueError('common-node pressure/kinetic budget does not close')
        extra={}
        if total:
            # Dissipative power avoids subtracting two large gravity-offset
            # pressures merely to recover a small friction coordinate.
            effective_friction=power/total
            secant=abs(effective_friction/total)
            if not math.isfinite(secant):raise ArithmeticError
            extra['pointwise_friction_secant_resistance_pa_s_m3']=secant
        if q.equivalent_reference_pipe is not None:
            reference=q.equivalent_reference_pipe
            extra['equivalent_reference_basis_id']=reference.reference_basis_id
            if not total:
                extra['equivalent_reference_status']='undefined_zero_flow'
            else:
                try:
                    from .models.hydraulics import LiquidPressureLoss,liquid_pressure_loss
                    # One metre is the unit length of the declared reference
                    # pipe, at the actual solved total flow and liquid state.
                    per_metre=liquid_pressure_loss(LiquidPressureLoss(diameter_m=reference.diameter_m,length_m=1.,
                        roughness_m=reference.roughness_m,volume_flow_m3_s=total,density_kg_m3=q.density_kg_m3,
                        viscosity_pa_s=q.dynamic_viscosity_pa_s,gravity_m_s2=q.gravity_m_s2,
                        transition_policy=q.transition_policy,friction_method=q.friction_method))
                    if per_metre.status!='computed':raise ValueError('reference pipe correlation outside support')
                    gradient=per_metre.values['dissipative_pressure_loss_pa']
                    if not gradient:raise ArithmeticError
                    length=effective_friction/gradient
                    if not math.isfinite(length) or length<0:raise ArithmeticError
                    extra.update(equivalent_reference_status='computed_at_reported_total_flow',equivalent_reference_pipe_length_m=length)
                except (ArithmeticError,ValueError):
                    extra.update(equivalent_reference_status='out_of_scope',issues=('main path solution is retained; optional reference pipe is outside its reviewed/numerical correlation support',))
        return LiquidPathSystemResult(status='computed',total_volume_flow_m3_s=total,total_mass_flow_kg_s=q.density_kg_m3*total,
            common_inlet_minus_outlet_pressure_pa=common_pressure,pressure_basis=q.pressure_basis,
            common_inlet_minus_outlet_total_pressure_pa=pressure,
            endpoint_kinetic_pressure_change_pa=kinetic_change(total) if q.pressure_basis=='static_with_endpoint_kinetic_state' else None,
            total_hydraulic_dissipation_W=power,continuity_residual_m3_s=continuity_residual,
            maximum_branch_total_pressure_residual_pa=max(abs(row.required_inlet_minus_outlet_total_pressure_pa-pressure) for row in rows),paths=rows,**extra)
    except (ArithmeticError,ValueError) as error:
        return LiquidPathSystemResult(status='out_of_scope',issues=(str(error) or 'finite liquid-path arithmetic support exceeded',))
