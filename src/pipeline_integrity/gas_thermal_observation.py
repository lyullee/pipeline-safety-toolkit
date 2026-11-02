"""Shared checked gas-state observations and adiabatic pressure-reduction states."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Name,Positive
from .thermo import CheckedProvider,CoolPropProvider,StateRequest,ThermodynamicState


class GasThermalStateOutOfScope(ValueError):
    pass


def checked_gas_thermal_state(request: StateRequest, *, property_provider,
    expected_provider_identity: tuple[str,str] | None = None) -> ThermodynamicState:
    """Check one prescribed P/T or P/h coordinate on a common provider revision."""
    if frozenset(request.inputs) not in (frozenset(('P','T')),frozenset(('P','Hmass'))):
        raise ValueError('gas thermal observation requires a prescribed P/T or P/h pair')
    state=CheckedProvider(property_provider).resolve(request)
    if state.phase not in ('gas','supercritical_gas','supercritical'):
        raise GasThermalStateOutOfScope('single gas or supercritical fluid state required; condensed/two-phase paths are not inferred')
    if not math.isclose(state.pressure_pa_abs,request.inputs['P'],rel_tol=1e-10,abs_tol=1e-5):
        raise ValueError('provider changed prescribed pressure')
    actual=state.temperature_k if 'T' in request.inputs else state.enthalpy_j_kg
    expected=request.inputs.get('T',request.inputs.get('Hmass'))
    if not math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-6):
        raise ValueError('provider changed prescribed thermal coordinate')
    if expected_provider_identity is not None and (state.provider_version,state.provider_revision)!=expected_provider_identity:
        raise ValueError('property provider revision changed across common enthalpy basis')
    return state


class GasPressureReductionRequest(Contract):
    fluid: Name
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    inlet_pressure_Pa_abs: Positive | None
    outlet_pressure_Pa_abs: Positive | None
    inlet_temperature_K: Positive | None
    property_basis_id: Name
    same_composition_steady_adiabatic_no_work_negligible_KE_PE_and_single_phase_path_reviewed: bool = False

    @model_validator(mode='after')
    def pressure_direction(self):
        if self.inlet_pressure_Pa_abs is not None and self.outlet_pressure_Pa_abs is not None:
            if self.outlet_pressure_Pa_abs>self.inlet_pressure_Pa_abs:raise ValueError('passive pressure reduction cannot raise pressure')
        return self


class GasPressureReductionResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    property_basis_id: Name
    inlet_state: ThermodynamicState | None = None
    outlet_state: ThermodynamicState | None = None
    signed_temperature_change_K: float | None = None
    temperature_drop_K: float | None = None
    specific_enthalpy_residual_J_kg: float | None = None
    issues: tuple[str,...] = ()
    device_capacity_or_hydrate_margin_inferred: Literal[False] = False


def observe_gas_pressure_reduction(q: GasPressureReductionRequest, *, property_provider=None) -> GasPressureReductionResult:
    """Resolve the outlet P/h state without a fixed cooling-per-pressure factor.

    Heating is retained as a negative temperature drop. The caller reviews the
    whole single-phase path; endpoint checks cannot prove that an arbitrary
    mixture stays single phase in between. No valve flow or hydrate/dew-point
    margin is identified by this energy-state observation.
    """
    if not isinstance(q,GasPressureReductionRequest):raise TypeError('GasPressureReductionRequest required')
    q=GasPressureReductionRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_basis_id=q.property_basis_id)
    if (None in (q.inlet_pressure_Pa_abs,q.outlet_pressure_Pa_abs,q.inlet_temperature_K)
        or not q.same_composition_steady_adiabatic_no_work_negligible_KE_PE_and_single_phase_path_reviewed):
        return GasPressureReductionResult(status='needs_data',**base,issues=('complete reviewed inlet state, reduced pressure and adiabatic energy boundary required',))
    provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
    try:
        inlet=checked_gas_thermal_state(StateRequest(fluid=q.fluid,backend=q.backend,
            inputs={'P':q.inlet_pressure_Pa_abs,'T':q.inlet_temperature_K},single_phase_required=False),property_provider=provider)
        outlet=checked_gas_thermal_state(StateRequest(fluid=q.fluid,backend=q.backend,
            inputs={'P':q.outlet_pressure_Pa_abs,'Hmass':inlet.enthalpy_j_kg},single_phase_required=False),property_provider=provider,
            expected_provider_identity=(inlet.provider_version,inlet.provider_revision))
    except GasThermalStateOutOfScope as exc:
        return GasPressureReductionResult(status='out_of_scope',**base,issues=(str(exc),))
    change=outlet.temperature_k-inlet.temperature_k
    residual=outlet.enthalpy_j_kg-inlet.enthalpy_j_kg
    return GasPressureReductionResult(status='computed',**base,inlet_state=inlet,outlet_state=outlet,
        signed_temperature_change_K=change,temperature_drop_K=-change,specific_enthalpy_residual_J_kg=residual)
