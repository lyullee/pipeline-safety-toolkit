"""Observed signed gas inventory loss from explicit endpoint P/T states.

Existing inventory and thermodynamic providers own density and reference
volume. The observer removes temperature-driven pressure changes from a
rigid uniform-volume mass budget; it does not infer a leak opening, heat
transfer path, future loss, measurement uncertainty or isolation integrity.
"""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Nonnegative,Positive
from .models.thermal import Inventory,inventory
from .gas_valve_reference import _GasPhaseProvider
from .thermo import CoolPropProvider


class IsolatedGasInventoryLossRequest(Contract):
    fluid: Name
    actual_internal_volume_m3: Positive | None = None
    initial_pressure_pa_abs: Positive
    initial_temperature_K: Positive
    final_pressure_pa_abs: Positive
    final_temperature_K: Positive
    elapsed_time_s: Positive
    reference_pressure_pa_abs: Positive
    reference_temperature_K: Positive
    property_and_reference_basis_id: Name | None = None
    isolated_same_composition_no_other_mass_exchange_reviewed: bool = False
    rigid_volume_and_uniform_actual_endpoint_states_reviewed: bool = False
    reporting_interval_s: Positive | None = None
    constant_mean_reporting_extrapolation_reviewed: bool = False


class IsolatedGasInventoryLossResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    initial_density_kg_m3: Positive | None = None
    final_density_kg_m3: Positive | None = None
    reference_density_kg_m3: Positive | None = None
    initial_mass_kg: Nonnegative | None = None
    final_mass_kg: Nonnegative | None = None
    signed_observed_mass_decrease_kg: float | None = None
    signed_mean_unaccounted_outflow_kg_s: float | None = None
    signed_reference_volume_decrease_m3: float | None = None
    signed_mean_reference_volume_outflow_m3_s: float | None = None
    conditional_constant_mean_reporting_volume_m3: float | None = None
    mass_budget_roundoff_residual_kg: float | None = None
    density_subtraction_condition_number: Nonnegative | None = None
    half_ulp_endpoint_density_precision_kg_m3: Nonnegative | None = None
    leak_cause_or_opening_identified: bool = False
    field_or_standard_qualified: bool = False


def observe_isolated_gas_inventory_loss(request:IsolatedGasInventoryLossRequest,*,property_provider=None) -> IsolatedGasInventoryLossResult:
    """Signed endpoint inventory loss; thermal pressure decrease alone is no leak.

    The same explicit reference P/T and gas composition apply to both states.
    A negative result is an observed mass gain and is retained diagnostically,
    rather than clipped to zero or called positive leakage. Provider failures
    and inconsistent physical states propagate as diagnostic exceptions; no
    substitute ideal state is silently introduced.
    """
    def fail(state,reason):return IsolatedGasInventoryLossResult(state=state,reason=reason)
    q=request
    if q.actual_internal_volume_m3 is None or q.property_and_reference_basis_id is None or not (q.isolated_same_composition_no_other_mass_exchange_reviewed and q.rigid_volume_and_uniform_actual_endpoint_states_reviewed):
        return fail('needs_data','actual volume and reviewed uniform isolation/composition/reference basis are required')
    provider=_GasPhaseProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False))
    snapshots=[]
    for p,t in [(q.initial_pressure_pa_abs,q.initial_temperature_K),(q.final_pressure_pa_abs,q.final_temperature_K)]:
        snapshots.append(inventory(Inventory(fluid=q.fluid,pressure_pa_abs=p,temperature_k=t,internal_volume_m3=q.actual_internal_volume_m3,reference_pressure_pa_abs=q.reference_pressure_pa_abs,reference_temperature_k=q.reference_temperature_K),property_provider=provider).values)
    initial,final=snapshots
    rhoi,rhof=initial['density_kg_m3'],final['density_kg_m3']
    refi=initial['mass_kg']/initial['reference_volume_m3'];reff=final['mass_kg']/final['reference_volume_m3']
    if not math.isclose(refi,reff,rel_tol=1e-12):raise ValueError('same-composition reference density changed between endpoint inventory calls')
    difference=rhoi-rhof
    decrease=q.actual_internal_volume_m3*difference
    mean=decrease/q.elapsed_time_s;reference_decrease=decrease/refi;reference_mean=reference_decrease/q.elapsed_time_s
    reported=None
    if q.reporting_interval_s is not None and q.constant_mean_reporting_extrapolation_reviewed:
        reported=reference_mean*q.reporting_interval_s
    residual=math.fsum((initial['mass_kg'],-final['mass_kg'],-decrease))
    condition=(rhoi+rhof)/abs(difference) if difference else None
    precision=(math.ulp(rhoi)+math.ulp(rhof))/2
    vals=(refi,decrease,mean,reference_decrease,reference_mean,residual,precision,*(() if reported is None else (reported,)))
    if not all(math.isfinite(x) for x in vals) or (difference and (decrease==0 or mean==0 or reference_mean==0)):
        return fail('out_of_scope','observed loss lies outside representable finite inventory coordinates')
    if condition is not None and not math.isfinite(condition):condition=None
    return IsolatedGasInventoryLossResult(state='computed',reason='signed actual-state inventory decrease, not identified leak cause',initial_density_kg_m3=rhoi,final_density_kg_m3=rhof,reference_density_kg_m3=refi,initial_mass_kg=initial['mass_kg'],final_mass_kg=final['mass_kg'],signed_observed_mass_decrease_kg=decrease,signed_mean_unaccounted_outflow_kg_s=mean,signed_reference_volume_decrease_m3=reference_decrease,signed_mean_reference_volume_outflow_m3_s=reference_mean,conditional_constant_mean_reporting_volume_m3=reported,mass_budget_roundoff_residual_kg=residual,density_subtraction_condition_number=condition,half_ulp_endpoint_density_precision_kg_m3=precision)
