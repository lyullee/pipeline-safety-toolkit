"""Single-liquid coolant flow from a declared heat duty and enthalpy rise."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Name, Nonnegative, Positive
from .thermo import CheckedProvider, CoolPropProvider, StateRequest, ThermodynamicState


class LiquidHeatDutyRequest(Contract):
    fluid: Name
    backend: Literal['HEOS', 'IF97', 'PR', 'SRK'] = 'HEOS'
    pressure_Pa_abs: Positive
    inlet_temperature_K: Positive
    outlet_temperature_K: Positive
    heat_to_coolant_W: Nonnegative | None
    property_basis_id: Name
    same_composition_single_liquid_isobaric_path_reviewed: bool = False
    steady_no_shaft_work_negligible_kinetic_potential_change_reviewed: bool = False

    @model_validator(mode='after')
    def warming_path(self):
        if self.outlet_temperature_K <= self.inlet_temperature_K:
            raise ValueError('coolant outlet temperature must exceed inlet temperature')
        return self


class LiquidHeatDutyResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    property_basis_id: Name
    coolant_mass_flow_kg_s: Nonnegative | None = None
    inlet_volume_flow_m3_s: Nonnegative | None = None
    outlet_volume_flow_m3_s: Nonnegative | None = None
    enthalpy_rise_J_kg: Positive | None = None
    energy_residual_W: float | None = None
    inlet_state: ThermodynamicState | None = None
    outlet_state: ThermodynamicState | None = None
    issues: tuple[str, ...] = ()
    engine_heat_release_inferred: Literal[False] = False


def size_liquid_heat_duty_flow(q: LiquidHeatDutyRequest, *, property_provider=None) -> LiquidHeatDutyResult:
    """Invert the steady coolant enthalpy balance using the existing state provider.

    Heat transferred into the coolant is a supplied thermal duty. Engine power,
    jacket losses, exchanger effectiveness and manufacturer limits are not inferred.
    Both volume rates refer to their actual endpoint states, not a reference gallon.
    The caller reviews the full liquid path; endpoint phase checks alone cannot
    certify an arbitrary mixture's interior phase behavior.
    """
    if not isinstance(q, LiquidHeatDutyRequest):
        raise TypeError('LiquidHeatDutyRequest required')
    q = LiquidHeatDutyRequest.model_validate(q.model_dump(mode='python'))
    base = dict(property_basis_id=q.property_basis_id)
    if (q.heat_to_coolant_W is None
        or not q.same_composition_single_liquid_isobaric_path_reviewed
        or not q.steady_no_shaft_work_negligible_kinetic_potential_change_reviewed):
        return LiquidHeatDutyResult(status='needs_data', **base,
            issues=('supplied heat duty, same-liquid path and steady energy-boundary review required',))
    provider = CheckedProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False))
    states = []
    for temperature in (q.inlet_temperature_K, q.outlet_temperature_K):
        state = provider.resolve(StateRequest(fluid=q.fluid, backend=q.backend,
            inputs={'P': q.pressure_Pa_abs, 'T': temperature}))
        if not (math.isclose(state.pressure_pa_abs, q.pressure_Pa_abs, rel_tol=1e-10)
            and math.isclose(state.temperature_k, temperature, rel_tol=1e-10)):
            raise ValueError('property provider changed a prescribed endpoint state')
        if state.phase != 'liquid':
            return LiquidHeatDutyResult(status='out_of_scope', **base,
                issues=('both prescribed endpoints must resolve as liquid',))
        states.append(state)
    inlet, outlet = states
    rise = outlet.enthalpy_j_kg - inlet.enthalpy_j_kg
    if rise <= 0:
        return LiquidHeatDutyResult(status='out_of_scope', **base,
            issues=('positive supplied duty requires positive liquid enthalpy rise',))
    mass_flow = q.heat_to_coolant_W / rise
    inlet_flow = mass_flow / inlet.density_kg_m3
    outlet_flow = mass_flow / outlet.density_kg_m3
    residual = math.fsum((mass_flow * rise, -q.heat_to_coolant_W))
    if not all(math.isfinite(v) for v in (rise, mass_flow, inlet_flow, outlet_flow, residual)):
        raise ValueError('heat-duty flow exceeds finite numerical support')
    if q.heat_to_coolant_W > 0 and min(mass_flow, inlet_flow, outlet_flow) <= 0:
        raise ValueError('positive coolant flow is below finite numerical support')
    return LiquidHeatDutyResult(status='computed', **base, coolant_mass_flow_kg_s=mass_flow,
        inlet_volume_flow_m3_s=inlet_flow, outlet_volume_flow_m3_s=outlet_flow,
        enthalpy_rise_J_kg=rise, energy_residual_W=residual, inlet_state=inlet, outlet_state=outlet)
