"""Conditional isothermal free-water depletion on a pure-water vacuum plateau.

The finite-volume mass/energy ledger stops at liquid depletion. Spatial drying,
heat-transfer coefficients, desorption, freezing and final pumpdown are separate.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Positive,Nonnegative
from .thermo import CheckedProvider,CoolPropProvider,StateRequest
from .vacuum_pumpdown import VacuumPumpdownRequest,evaluate_vacuum_pumpdown
from .water_vapor_observation import observe_water_saturation


class VacuumWaterEvaporationRequest(Contract):
    internal_volume_m3: Positive
    initial_free_water_mass_kg: Nonnegative | None
    plateau_temperature_K: Positive | None
    external_pressure_Pa_abs: Positive
    pump_speed_m3_s: Nonnegative | None = None
    line_conductances_m3_s: tuple[Nonnegative | None,...] | None = None
    thermal_boundary: Literal['prescribed_isothermal_reservoir','finite_net_heat_with_pump_throttle'] | None = None
    available_net_evaporation_heat_W: Nonnegative | None = None
    pure_water_uniform_saturation_and_constant_coefficients_reviewed: bool = False
    prescribed_thermal_boundary_and_pump_control_reviewed: bool = False
    elapsed_times_s: tuple[Nonnegative,...] = Field(default=(0.,),min_length=1,max_length=10000)
    clock_id: Name
    start_time_s: float


class VacuumWaterEvaporationPoint(Contract):
    elapsed_time_s: Nonnegative
    time_s: float
    plateau_elapsed_time_s: Nonnegative
    phase_status: Literal['liquid_present','at_depletion','post_depletion_not_predicted','stalled']
    free_water_remaining_kg: Nonnegative
    plateau_evaporated_liquid_kg: Nonnegative
    plateau_removed_vapor_kg: Nonnegative
    vapor_mass_at_plateau_endpoint_kg: Nonnegative
    latent_heat_supplied_J: Nonnegative
    outflow_enthalpy_J: float
    internal_energy_at_plateau_endpoint_J: float
    mass_budget_residual_kg: float
    energy_budget_residual_J: float


class VacuumWaterEvaporationResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    clock_id: Name
    saturation_pressure_Pa_abs: Positive | None = None
    liquid_density_kg_m3: Positive | None = None
    vapor_density_kg_m3: Positive | None = None
    latent_enthalpy_J_kg: Positive | None = None
    pump_capacity_effective_speed_m3_s: Nonnegative | None = None
    controlled_effective_speed_m3_s: Nonnegative | None = None
    pump_capacity_vapor_removal_kg_s: Nonnegative | None = None
    liquid_evaporation_rate_kg_s: Nonnegative | None = None
    vapor_removal_rate_kg_s: Nonnegative | None = None
    required_latent_heat_power_W: Nonnegative | None = None
    total_latent_heat_to_depletion_J: Nonnegative | None = None
    depletion_status: Literal['unknown','at_start','finite_time','unreachable'] = 'unknown'
    time_to_free_water_depletion_s: Nonnegative | None = None
    depletion_clock_time_s: float | None = None
    low_vapor_density_limit_time_at_pump_capacity_s: Nonnegative | None = None
    limiting_capacity: Literal['pump','net_heat','zero_inventory'] | None = None
    points: tuple[VacuumWaterEvaporationPoint,...] = ()
    property_provider_version: Name | None = None
    property_provider_revision: Name | None = None
    issues: tuple[str,...] = ()
    final_drying_or_field_acceptance_predicted: Literal[False] = False


def evaluate_vacuum_water_evaporation(q: VacuumWaterEvaporationRequest, *, property_provider=None) -> VacuumWaterEvaporationResult:
    """Deplete liquid in a rigid pure-water equilibrium chamber at prescribed T.

    Pump speed and line conductances are constant capacities at the actual
    suction state, not standard-volume ratings. The existing pumpdown owner
    supplies only their series-speed observation at zero elapsed time; its
    ideal-gas mass coordinate is not used as a saturated-water EOS.

    At constant saturation densities, finite liquid displacement gives
    m_out=(1-rho_v/rho_l)*m_evaporated. Unlike a dilute-vapor inventory estimate,
    the growing vapor storage is retained. Energy closes with Q=m_evap*h_fg
    and the enthalpy of the extracted vapor. An explicitly prescribed thermal
    reservoir supplies that heat, or supplied net heat limits the rate while
    the pump is throttled. Missing heat closure is never replaced by zero.
    Values beyond depletion report the plateau endpoint only; they do not
    continue constant-pressure evaporation with negative liquid inventory.
    """
    from CoolProp.CoolProp import PropsSI
    if not isinstance(q,VacuumWaterEvaporationRequest):raise TypeError('VacuumWaterEvaporationRequest required')
    q=VacuumWaterEvaporationRequest.model_validate(q.model_dump(mode='python'));base=dict(clock_id=q.clock_id)
    if (q.initial_free_water_mass_kg is None or q.plateau_temperature_K is None or q.thermal_boundary is None
        or not q.pure_water_uniform_saturation_and_constant_coefficients_reviewed
        or not q.prescribed_thermal_boundary_and_pump_control_reviewed
        or (q.thermal_boundary=='finite_net_heat_with_pump_throttle' and q.available_net_evaporation_heat_W is None)):
        return VacuumWaterEvaporationResult(status='needs_data',**base,issues=('complete inventory/state and explicit reviewed thermal/pump-control boundary required',))
    if q.thermal_boundary=='prescribed_isothermal_reservoir' and q.available_net_evaporation_heat_W is not None:
        raise ValueError('finite heat capacity requires the finite-net-heat boundary')
    provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
    try:
        sat=observe_water_saturation(q.plateau_temperature_K,'liquid_water',property_provider=provider)
        liquid=sat.liquid_boundary_state
        vapor=CheckedProvider(provider).resolve(StateRequest(fluid='Water',backend='HEOS',inputs={'T':q.plateau_temperature_K,'Q':1.},single_phase_required=False))
    except (ValueError,RuntimeError) as exc:
        return VacuumWaterEvaporationResult(status='out_of_scope',**base,issues=(str(exc),))
    if (vapor.phase!='twophase' or vapor.fluid!='Water' or liquid.fluid!='Water'
        or not math.isclose(vapor.pressure_pa_abs,sat.equilibrium_vapor_pressure_Pa_abs,rel_tol=1e-10)
        or not math.isclose(vapor.temperature_k,q.plateau_temperature_K,rel_tol=1e-12)
        or (vapor.provider_version,vapor.provider_revision)!=(liquid.provider_version,liquid.provider_revision)):
        raise ValueError('same-provider pure-water saturated endpoints at prescribed T/P required')
    pressure=sat.equilibrium_vapor_pressure_Pa_abs;rl=liquid.density_kg_m3;rv=vapor.density_kg_m3;latent=vapor.enthalpy_j_kg-liquid.enthalpy_j_kg
    if pressure>=q.external_pressure_Pa_abs or rv>=rl or latent<=0:
        return VacuumWaterEvaporationResult(status='out_of_scope',**base,issues=('subcritical liquid-vapor vacuum state with positive latent enthalpy required',))
    initial=q.initial_free_water_mass_kg;vapor_volume=q.internal_volume_m3-initial/rl
    if vapor_volume<=0:raise ValueError('positive initial vapor space required; liquid cannot fill/exceed internal volume')
    # Reuse the established constant pump/line relation, requesting no gas path.
    speed_observation=evaluate_vacuum_pumpdown(VacuumPumpdownRequest(volume_m3=q.internal_volume_m3,
        initial_pressure_Pa_abs=pressure,pump_speed_m3_s=q.pump_speed_m3_s,line_conductances_m3_s=q.line_conductances_m3_s,
        pump_ultimate_pressure_Pa_abs=0,constant_gas_load_Pa_m3_s=0,gas_temperature_K=q.plateau_temperature_K,
        specific_gas_constant_J_kg_K=float(PropsSI('gas_constant','Water')/PropsSI('molar_mass','Water')),
        elapsed_times_s=(0,),clock_id=q.clock_id,pump_start_time_s=q.start_time_s,
        isothermal_fixed_composition_and_constant_linear_throughput_reviewed=True))
    if speed_observation.status!='computed':return VacuumWaterEvaporationResult(status='needs_data',**base,issues=speed_observation.issues)
    speed=speed_observation.effective_speed_m3_s;ratio=rv/rl;alpha=1-ratio;capacity_out=rv*speed;evap=capacity_out/alpha;limit='pump'
    if q.thermal_boundary=='finite_net_heat_with_pump_throttle' and q.available_net_evaporation_heat_W/latent<evap:
        evap=q.available_net_evaporation_heat_W/latent;limit='net_heat'
    out_rate=alpha*evap;actual_speed=out_rate/rv;power=evap*latent
    if initial==0:depletion=0.;event='at_start';limit='zero_inventory'
    elif evap==0:depletion=None;event='unreachable'
    else:depletion=initial/evap;event='finite_time'
    ml0=initial;mv0=rv*vapor_volume;ul=liquid.enthalpy_j_kg-pressure/rl;uv=vapor.enthalpy_j_kg-pressure/rv;u0=ml0*ul+mv0*uv
    rows=[]
    for elapsed in q.elapsed_times_s:
        active=elapsed if depletion is None else min(elapsed,depletion)
        evaporated=min(initial,evap*active);remaining=max(0.,initial-evaporated);removed=alpha*evaporated;mv=mv0+ratio*evaporated
        heat=evaporated*latent;out_h=removed*vapor.enthalpy_j_kg;u=remaining*ul+mv*uv
        at_event=depletion is not None and math.isclose(elapsed,depletion,rel_tol=0,abs_tol=4*math.ulp(depletion))
        phase='stalled' if event=='unreachable' else ('at_depletion' if at_event else 'liquid_present' if elapsed<depletion else 'post_depletion_not_predicted')
        rows.append(VacuumWaterEvaporationPoint(elapsed_time_s=elapsed,time_s=q.start_time_s+elapsed,plateau_elapsed_time_s=active,
            phase_status=phase,free_water_remaining_kg=remaining,plateau_evaporated_liquid_kg=evaporated,plateau_removed_vapor_kg=removed,
            vapor_mass_at_plateau_endpoint_kg=mv,latent_heat_supplied_J=heat,outflow_enthalpy_J=out_h,internal_energy_at_plateau_endpoint_J=u,
            mass_budget_residual_kg=math.fsum([ml0,mv0,-remaining,-mv,-removed]),energy_budget_residual_J=math.fsum([u0,heat,-out_h,-u])))
    vals=[evap,out_rate,power,initial*latent,actual_speed,*([depletion] if depletion is not None else [])]
    if not all(math.isfinite(x) for x in vals):raise ValueError('evaporation scale exceeds finite numerical support')
    return VacuumWaterEvaporationResult(status='computed',**base,saturation_pressure_Pa_abs=pressure,liquid_density_kg_m3=rl,vapor_density_kg_m3=rv,
        latent_enthalpy_J_kg=latent,pump_capacity_effective_speed_m3_s=speed,controlled_effective_speed_m3_s=actual_speed,
        pump_capacity_vapor_removal_kg_s=capacity_out,liquid_evaporation_rate_kg_s=evap,vapor_removal_rate_kg_s=out_rate,
        required_latent_heat_power_W=power,total_latent_heat_to_depletion_J=initial*latent,depletion_status=event,time_to_free_water_depletion_s=depletion,
        depletion_clock_time_s=None if depletion is None else q.start_time_s+depletion,
        low_vapor_density_limit_time_at_pump_capacity_s=None if capacity_out==0 else initial/capacity_out,
        limiting_capacity=limit,points=tuple(rows),property_provider_version=liquid.provider_version,property_provider_revision=liquid.provider_revision)
