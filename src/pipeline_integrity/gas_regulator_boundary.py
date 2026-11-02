"""Steady pressure-control feasibility with finite reviewed gas valve capacity.

The controller either meets its pressure target with an attainable flow below
maximum capacity, or saturates and solves the actual mass balance. Upstream
backpressure control and downstream reduction have opposite pressure-search
directions. This is not actuator dynamics, an unlimited pressure source or a
prediction of thermal phase change and mechanical stem position.
"""
from __future__ import annotations
import math
from typing import Callable,Literal,Protocol
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive


class GasValveCapacityPoint(Contract):
    upstream_pressure_pa_abs: Positive
    downstream_pressure_pa_abs: Positive
    maximum_mass_flow_kg_s: Nonnegative | None
    fluid_and_mass_basis_id: Name
    characteristic_basis_id: Name


class GasExternalFlowPoint(Contract):
    controlled_pressure_pa_abs: Positive
    role: Literal['outgoing_load','incoming_supply']
    mass_flow_kg_s: Nonnegative | None
    fluid_and_mass_basis_id: Name
    characteristic_basis_id: Name


class GasCapacityProvider(Protocol):
    def __call__(self,upstream_pressure_pa_abs:float,downstream_pressure_pa_abs:float) -> GasValveCapacityPoint:...


class GasExternalFlowProvider(Protocol):
    def __call__(self,controlled_pressure_pa_abs:float) -> GasExternalFlowPoint:...


class GasRegulatorBoundaryRequest(Contract):
    controlled_side: Literal['downstream','upstream']
    fixed_other_side_pressure_pa_abs: Positive
    target_controlled_pressure_pa_abs: Positive
    saturated_controlled_pressure_bounds_pa_abs: tuple[Positive,Positive]
    fluid_and_mass_basis_id: Name | None = None
    valve_capacity_basis_id: Name | None = None
    external_flow_basis_id: Name | None = None
    single_gas_steady_capacity_and_external_boundary_reviewed: bool = False
    continuously_modulatable_zero_to_maximum_capacity_reviewed: bool = False
    strict_saturated_mass_residual_monotonicity_reviewed: bool = False
    absolute_pressure_root_tolerance_pa: Positive = 1e-6
    absolute_mass_balance_tolerance_kg_s: Positive = 1e-10
    relative_mass_balance_tolerance: Positive = 1e-8
    maximum_root_iterations: int = Field(default=100,ge=1,le=10000)

    @model_validator(mode='after')
    def pressure_support(self):
        lo,hi=self.saturated_controlled_pressure_bounds_pa_abs
        if lo>=hi:raise ValueError('saturated pressure support must be strictly ordered')
        target=self.target_controlled_pressure_pa_abs;fixed=self.fixed_other_side_pressure_pa_abs
        if self.controlled_side=='downstream':
            if hi!=target or target>=fixed:raise ValueError('downstream target must be below supply; support ends at target')
        elif lo!=target or target<=fixed:
            raise ValueError('upstream target must exceed outlet; support starts at target')
        return self


class GasRegulatorBoundaryResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    operating_mode: Literal['target_controlled','maximum_capacity_saturated','unresolved'] = 'unresolved'
    controlled_pressure_pa_abs: Positive | None = None
    upstream_pressure_pa_abs: Positive | None = None
    downstream_pressure_pa_abs: Positive | None = None
    valve_mass_flow_kg_s: Nonnegative | None = None
    external_mass_flow_kg_s: Nonnegative | None = None
    maximum_valve_mass_flow_kg_s: Nonnegative | None = None
    mass_balance_residual_kg_s: float | None = None
    attained_maximum_capacity_fraction: Nonnegative | None = None
    required_effective_Kv: Nonnegative | None = None
    pressure_target_attained: bool = False
    evaluated_pressure_state_count: int = 0
    stem_position_or_thermal_dynamics_solved: bool = False
    field_or_standard_qualified: bool = False


class _MissingState(Exception):pass


def solve_gas_regulator_boundary(request:GasRegulatorBoundaryRequest,*,capacity_provider:GasCapacityProvider,external_flow_provider:GasExternalFlowProvider,required_effective_Kv_provider:Callable[[float,float,float],float]|None=None) -> GasRegulatorBoundaryResult:
    """Close a steady directed gas mass boundary, including a saturated valve.

    For downstream control the supplied external flow is outgoing demand;
    for upstream control it is incoming supply. A maximum-capacity fraction
    is a flow utilization coordinate, not stem travel or a linear Kv fraction.
    An optional reviewed native sizing adapter can return actual required Kv.
    The saturated residual must be strictly monotone over reviewed support;
    a choked plateau with constant demand may otherwise be underdetermined.
    """
    if not isinstance(request,GasRegulatorBoundaryRequest):
        raise TypeError('GasRegulatorBoundaryRequest required')
    q=GasRegulatorBoundaryRequest.model_validate(request.model_dump(mode='python'));cache={}
    def fail(state,reason):return GasRegulatorBoundaryResult(state=state,reason=reason,evaluated_pressure_state_count=len(cache))
    if not (q.fluid_and_mass_basis_id and q.valve_capacity_basis_id and q.external_flow_basis_id and q.single_gas_steady_capacity_and_external_boundary_reviewed and q.continuously_modulatable_zero_to_maximum_capacity_reviewed):
        return fail('needs_data','actual gas/capacity/load bases and steady modulation applicability need review')
    role='outgoing_load' if q.controlled_side=='downstream' else 'incoming_supply'
    def point(p):
        if p in cache:return cache[p]
        up,down=(q.fixed_other_side_pressure_pa_abs,p) if q.controlled_side=='downstream' else (p,q.fixed_other_side_pressure_pa_abs)
        cap=GasValveCapacityPoint.model_validate(capacity_provider(up,down).model_dump())
        external=GasExternalFlowPoint.model_validate(external_flow_provider(p).model_dump())
        for actual,wanted in [(cap.upstream_pressure_pa_abs,up),(cap.downstream_pressure_pa_abs,down),(external.controlled_pressure_pa_abs,p)]:
            if not math.isclose(actual,wanted,rel_tol=1e-12,abs_tol=1e-7):raise ValueError('provider returned a different actual pressure state')
        if cap.fluid_and_mass_basis_id!=q.fluid_and_mass_basis_id or external.fluid_and_mass_basis_id!=q.fluid_and_mass_basis_id or cap.characteristic_basis_id!=q.valve_capacity_basis_id or external.characteristic_basis_id!=q.external_flow_basis_id or external.role!=role:
            raise ValueError('provider changed fluid/mass/capacity/external-flow basis or direction')
        if cap.maximum_mass_flow_kg_s is None or external.mass_flow_kg_s is None:raise _MissingState('actual capacity or external flow is unavailable at a required trial state')
        value=(up,down,cap.maximum_mass_flow_kg_s,external.mass_flow_kg_s)
        cache[p]=value
        return value
    try:
        target=q.target_controlled_pressure_pa_abs
        up,down,maximum,demand=point(target)
        if maximum==0 and demand==0:return fail('out_of_scope','zero maximum capacity and zero flow do not identify a regulated pressure')
        controlled=demand<=maximum
        pressure=target
        if not controlled:
            if not q.strict_saturated_mass_residual_monotonicity_reviewed:return fail('needs_data','saturated pressure uniqueness requires reviewed strict residual monotonicity')
            def residual(p):
                _,_,capacity,flow=point(p)
                return capacity-flow
            lo,hi=q.saturated_controlled_pressure_bounds_pa_abs
            rlo,rhi=residual(lo),residual(hi)
            directed=(rlo>=0 and rhi<=0) if q.controlled_side=='downstream' else (rlo<=0 and rhi>=0)
            if not directed:return fail('out_of_scope','no feasible saturated steady state brackets inside supplied pressure support')
            from scipy.optimize import brentq
            try:pressure=brentq(residual,lo,hi,xtol=q.absolute_pressure_root_tolerance_pa,rtol=4*math.ulp(1.),maxiter=q.maximum_root_iterations)
            except RuntimeError:return fail('out_of_scope','saturated pressure root did not converge within supplied iteration budget')
            up,down,maximum,demand=point(pressure)
        valve=demand if controlled else maximum
        mass_residual=valve-demand
        tolerance=q.absolute_mass_balance_tolerance_kg_s+q.relative_mass_balance_tolerance*max(valve,demand)
        if abs(mass_residual)>tolerance:return fail('out_of_scope','actual saturated mass balance exceeds the declared error budget')
        # Check observed monotonicity of actual visited residuals, while retaining
        # the caller's whole-support review instead of inventing a global proof.
        if not controlled:
            values=[(p,v[2]-v[3]) for p,v in sorted(cache.items())]
            for (pa,ra),(pb,rb) in zip(values,values[1:]):
                if (q.controlled_side=='downstream' and rb>ra+tolerance) or (q.controlled_side=='upstream' and rb<ra-tolerance):
                    raise ValueError('observed capacity/load residual contradicts the reviewed monotonic direction')
        setting=None
        if required_effective_Kv_provider is not None:
            setting=required_effective_Kv_provider(up,down,valve)
            if not math.isfinite(setting) or setting<0:raise ValueError('required effective valve setting is not finite nonnegative Kv')
        fraction=valve/maximum if maximum else 0.
        return GasRegulatorBoundaryResult(state='computed',reason='feasible pressure target' if controlled else 'finite-capacity saturated mass equilibrium',operating_mode='target_controlled' if controlled else 'maximum_capacity_saturated',controlled_pressure_pa_abs=pressure,upstream_pressure_pa_abs=up,downstream_pressure_pa_abs=down,valve_mass_flow_kg_s=valve,external_mass_flow_kg_s=demand,maximum_valve_mass_flow_kg_s=maximum,mass_balance_residual_kg_s=mass_residual,attained_maximum_capacity_fraction=fraction,required_effective_Kv=setting,pressure_target_attained=controlled,evaluated_pressure_state_count=len(cache))
    except _MissingState as exc:return fail('needs_data',str(exc))
