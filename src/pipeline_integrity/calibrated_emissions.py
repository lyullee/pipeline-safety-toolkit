"""Conditional tank and cascade sources with explicit mass, state and heat bases.

Empirical efficiencies and transfer coefficients are supplied with calibration
identities. No equipment correlation or constituent property table is embedded.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, json_value


class FillingVaporRequest(Contract):
    liquid_fill_rate_m3_s: Nonnegative
    tank_capacity_m3: Positive
    displacement_vapor_density_kg_m3: Positive
    displacement_volume_ratio: Nonnegative
    capacity_generation_coefficient_kg_m3_s: Nonnegative
    capacity_generation_exponent: float = 1.0
    reference_capacity_m3: Positive
    additional_thermal_power_W: Nonnegative = 0
    latent_enthalpy_J_kg: Positive
    maximum_liquid_supply_kg_s: Nonnegative
    calibration_id: Name
    vapor_state_id: Name
    calibrated_fill_and_capacity_domain_reviewed: Literal[True]


def evaluate_filling_vapor(q: FillingVaporRequest) -> dict:
    """Combine displaced vapor, calibrated capacity generation and supplied heat.

    The capacity coefficient refers to reference capacity and has kg/(m3 s).
    Thermal generation consumes liquid; displaced pre-existing vapor does not.
    """
    q = FillingVaporRequest.model_validate(q.model_dump(mode='python'))
    displacement = q.liquid_fill_rate_m3_s*q.displacement_volume_ratio*q.displacement_vapor_density_kg_m3
    capacity = q.capacity_generation_coefficient_kg_m3_s*q.reference_capacity_m3*(q.tank_capacity_m3/q.reference_capacity_m3)**q.capacity_generation_exponent
    heat_generation = q.additional_thermal_power_W/q.latent_enthalpy_J_kg
    requested = capacity+heat_generation
    generated = min(q.maximum_liquid_supply_kg_s, requested)
    return json_value(dict(displacement_vapor_rate_kg_s=displacement, generated_vapor_rate_kg_s=generated,
                total_vent_vapor_rate_kg_s=displacement+generated,
                generation_supply_limited=generated < requested,
                calibration_id=q.calibration_id, vapor_state_id=q.vapor_state_id,
                unused_generation_capacity_kg_s=requested-generated))


class TankLossEpisode(Contract):
    duration_s: Positive
    roof: Literal['fixed', 'external_floating', 'internal_floating']
    vapor_concentration_kg_m3: Nonnegative
    breathing_exchange_m3: Nonnegative = 0
    vapor_partial_pressure_difference_Pa: Nonnegative = 0
    rim_seal_mass_conductance_kg_s_Pa: Nonnegative = 0
    fittings_mass_conductance_kg_s_Pa: Nonnegative = 0
    working_displaced_gas_m3: Nonnegative = 0
    wetted_area_per_cycle_m2: Nonnegative = 0
    retained_liquid_film_m: Nonnegative = 0
    liquid_density_kg_m3: Positive
    wetting_evaporation_fraction: Fraction = 0
    turnover_cycles: Nonnegative = 0
    calibrated_geometry_environment_id: Name
    roof_mechanisms_and_factor_applicability_reviewed: Literal[True]

    @model_validator(mode='after')
    def roof_mechanisms(self):
        if self.roof == 'fixed' and self.rim_seal_mass_conductance_kg_s_Pa:
            raise ValueError('fixed roof has no floating rim seal mechanism')
        return self


class TankLossRequest(Contract):
    episodes: tuple[TankLossEpisode, ...] = Field(min_length=1)
    accounting_horizon_s: Positive
    calendar_basis_id: Name

    @model_validator(mode='after')
    def horizon(self):
        if not math.isclose(math.fsum(e.duration_s for e in self.episodes), self.accounting_horizon_s, rel_tol=1e-10):
            raise ValueError('episode durations must partition the accounting horizon')
        return self


def evaluate_tank_standing_working_loss(q: TankLossRequest) -> dict:
    """Sum distinct roof, breathing and turnover mechanisms over a stated year.

    Conductances encode reviewed seal/fitting geometry and environmental
    calibration. Film inventory is kept distinct from displaced vapor volume.
    """
    q = TankLossRequest.model_validate(q.model_dump(mode='python'))
    episodes = []
    for e in q.episodes:
        breathing = e.breathing_exchange_m3*e.vapor_concentration_kg_m3
        seal = e.duration_s*e.vapor_partial_pressure_difference_Pa*e.rim_seal_mass_conductance_kg_s_Pa
        fittings = e.duration_s*e.vapor_partial_pressure_difference_Pa*e.fittings_mass_conductance_kg_s_Pa
        displacement = e.working_displaced_gas_m3*e.vapor_concentration_kg_m3
        wetting = e.turnover_cycles*e.wetted_area_per_cycle_m2*e.retained_liquid_film_m*e.liquid_density_kg_m3*e.wetting_evaporation_fraction
        episodes.append(dict(roof=e.roof, standing_loss_kg=breathing+seal+fittings,
                             working_loss_kg=displacement+wetting, breathing_kg=breathing,
                             rim_seal_kg=seal, fittings_kg=fittings, displaced_vapor_kg=displacement,
                             evaporated_wetting_film_kg=wetting,
                             calibration_id=e.calibrated_geometry_environment_id))
    standing = math.fsum(e['standing_loss_kg'] for e in episodes)
    working = math.fsum(e['working_loss_kg'] for e in episodes)
    return json_value(dict(standing_loss_kg=standing, working_loss_kg=working, total_loss_kg=standing+working,
                accounting_horizon_s=q.accounting_horizon_s, calendar_basis_id=q.calendar_basis_id,
                episodes=episodes))


class CascadeComponent(Contract):
    id: Name
    liquid_volume_fraction: Fraction
    liquid_density_kg_m3: Positive
    molar_mass_kg_mol: Positive
    pure_vapor_pressure_Pa: Nonnegative


class CascadeSourceRequest(Contract):
    components: tuple[CascadeComponent, ...] = Field(min_length=1)
    liquid_mass_rate_kg_s: Nonnegative
    entrained_dry_air_mass_rate_kg_s: Nonnegative
    air_molar_mass_kg_mol: Positive
    total_pressure_Pa_abs: Positive
    common_temperature_K: Positive
    available_net_evaporation_heat_W: Nonnegative
    effective_latent_enthalpy_J_kg: Positive
    vapor_transfer_effectiveness: Fraction
    splash_aerosol_mass_fraction: Fraction
    calibration_id: Name
    common_state_ideal_equilibrium_and_heat_basis_reviewed: Literal[True]
    prescribed_interface_composition_and_vapor_selectivity_reviewed: Literal[True]
    source_duration_s: Nonnegative | None = None
    diluted_fuel_concentration_kg_m3: Positive | None = None
    cloud_depth_m: Positive | None = None

    @model_validator(mode='after')
    def composition(self):
        if len({c.id for c in self.components}) != len(self.components):
            raise ValueError('component IDs must be unique')
        if not math.isclose(math.fsum(c.liquid_volume_fraction for c in self.components), 1, rel_tol=0, abs_tol=1e-12):
            raise ValueError('same-state liquid volume fractions must sum to one')
        cloud = (self.source_duration_s, self.diluted_fuel_concentration_kg_m3, self.cloud_depth_m)
        if any(v is not None for v in cloud) and not all(v is not None for v in cloud):
            raise ValueError('cloud observation requires duration, fuel mass concentration and depth together')
        return self


def evaluate_cascade_source(q: CascadeSourceRequest) -> dict:
    """Close an ideal saturated-air capacity against heat and feed inventories.

    Splash aerosol and vapor remain separate phase rates. A supplied dilution
    concentration defines only a conditional homogeneous cloud geometry.
    Liquid interface composition and vapor selectivity are prescribed, not an
    outgoing liquid/vapor flash solution. Heat or finite contact can leave the
    actual carrier below its calculated interfacial saturation capacity.
    """
    q = CascadeSourceRequest.model_validate(q.model_dump(mode='python'))
    amounts = [c.liquid_volume_fraction*c.liquid_density_kg_m3/c.molar_mass_kg_mol for c in q.components]
    total = math.fsum(amounts)
    x = [n/total for n in amounts]
    partials = [fraction*c.pure_vapor_pressure_Pa for fraction, c in zip(x, q.components)]
    bubble = math.fsum(partials)
    if bubble >= q.total_pressure_Pa_abs:
        raise ValueError('air-carried sub-boiling closure requires bubble pressure below total pressure')
    loading = math.fsum(p*c.molar_mass_kg_mol for p, c in zip(partials, q.components))/(q.air_molar_mass_kg_mol*(q.total_pressure_Pa_abs-bubble))
    aerosol = q.liquid_mass_rate_kg_s*q.splash_aerosol_mass_fraction
    vapor_weights = [p*c.molar_mass_kg_mol for p, c in zip(partials, q.components)]
    vapor_weight_total = math.fsum(vapor_weights)
    vapor_fractions = [w/vapor_weight_total for w in vapor_weights] if vapor_weight_total else [0. for _ in vapor_weights]
    liquid_weights = [c.liquid_volume_fraction*c.liquid_density_kg_m3 for c in q.components]
    liquid_weight_total = math.fsum(liquid_weights)
    liquid_fractions = [w/liquid_weight_total for w in liquid_weights]
    species_supply_limit = min(((q.liquid_mass_rate_kg_s-aerosol)*liquid_fraction/vapor_fraction
                                for liquid_fraction, vapor_fraction in zip(liquid_fractions, vapor_fractions)
                                if vapor_fraction > 0), default=0.)
    vapor = min(species_supply_limit,
                q.entrained_dry_air_mass_rate_kg_s*loading*q.vapor_transfer_effectiveness,
                q.available_net_evaporation_heat_W/q.effective_latent_enthalpy_J_kg)
    residual = q.liquid_mass_rate_kg_s-aerosol-vapor
    result = dict(bubble_pressure_Pa=bubble, liquid_mole_fractions=dict(zip((c.id for c in q.components), x)),
                  saturated_fuel_air_mass_ratio=loading, vapor_mass_rate_kg_s=vapor,
                  aerosol_mass_rate_kg_s=aerosol, residual_liquid_mass_rate_kg_s=residual,
                  latent_heat_used_W=vapor*q.effective_latent_enthalpy_J_kg,
                  mass_balance_residual_kg_s=math.fsum((q.liquid_mass_rate_kg_s,-vapor,-aerosol,-residual)),
                  calibration_id=q.calibration_id, temperature_K=q.common_temperature_K)
    result['vapor_mass_fractions'] = dict(zip((c.id for c in q.components), vapor_fractions))
    result['available_evaporation_heat_not_used_W'] = q.available_net_evaporation_heat_W-result['latent_heat_used_W']
    result['heat_capacity_budget_residual_W'] = math.fsum((q.available_net_evaporation_heat_W,
        -result['latent_heat_used_W'], -result['available_evaporation_heat_not_used_W']))
    result['final_residual_liquid_equilibrium_inferred'] = False
    result['outlet_thermal_state_predicted'] = False
    result['residual_liquid_component_mass_rates_kg_s'] = {
        c.id: (q.liquid_mass_rate_kg_s-aerosol)*f-vapor*y
        for c, f, y in zip(q.components, liquid_fractions, vapor_fractions)}
    if q.source_duration_s is not None:
        vapor_mass = vapor*q.source_duration_s
        volume = vapor_mass/q.diluted_fuel_concentration_kg_m3
        result.update(vapor_cloud_inventory_kg=vapor_mass, conditional_cloud_volume_m3=volume,
                      conditional_cloud_radius_m=math.sqrt(volume/(math.pi*q.cloud_depth_m)))
    return json_value(result)
