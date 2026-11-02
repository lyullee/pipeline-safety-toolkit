"""Endpoint-density mass balance of a reviewed reciprocating liquid chamber.

The trapped residual is a fixed mass while both valves are closed. This is
not a gas polytropic-clearance model: actual liquid endpoint densities are
required. Density data may come from the existing thermodynamic provider or
measurements. No historical liquid-property table or slip estimate is built in.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive


class ChamberLiquidEndpoint(Contract):
    pressure_pa_abs: Positive
    temperature_K: Positive
    density_kg_m3: Positive | None = None
    phase: Literal['liquid','gas','two_phase','supercritical','unknown']
    property_basis_id: Name | None = None


class LiquidChamberCycleRequest(Contract):
    suction: ChamberLiquidEndpoint
    discharge: ChamberLiquidEndpoint
    end_discharge_dead_volume_m3: Nonnegative
    swept_volume_m3: Positive
    slip_fraction_of_swept_suction_volume: float | None = Field(default=None,ge=0,le=1)
    single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed: bool = False
    common_fluid_and_slip_reference_reviewed: bool = False

    @model_validator(mode='after')
    def pump_pressure_order(self):
        if self.discharge.pressure_pa_abs < self.suction.pressure_pa_abs:
            raise ValueError('pump discharge pressure must not be below suction pressure')
        return self


class LiquidChamberCycleResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    discharge_to_suction_density_ratio: Positive | None = None
    residual_liquid_mass_kg: Nonnegative | None = None
    suction_valve_opening_chamber_volume_m3: Nonnegative | None = None
    closed_valve_reexpansion_volume_m3: Nonnegative | None = None
    gross_suction_volume_m3_per_cycle: Nonnegative | None = None
    net_suction_reference_volume_m3_per_cycle: Nonnegative | None = None
    net_discharge_reference_volume_m3_per_cycle: Nonnegative | None = None
    gross_transferred_mass_kg_per_cycle: Nonnegative | None = None
    supplied_slip_mass_kg_per_cycle: Nonnegative | None = None
    net_transferred_mass_kg_per_cycle: Nonnegative | None = None
    suction_referenced_volumetric_efficiency: Nonnegative | None = None
    field_or_standard_qualified: bool = False


def evaluate_liquid_chamber_cycle(request: LiquidChamberCycleRequest) -> LiquidChamberCycleResult:
    """Return per-cycle delivered volumes on distinct suction/discharge bases.

    A reviewed slip fraction is lost mass divided by rho_s*Vs. It is not
    automatically a fraction of gross intake or discharge volume. No stroke
    frequency, pump flow, valve dynamics, thermal path or leakage is inferred.
    Insufficient stroke for reexpansion is diagnosed rather than clipped into
    an apparently functioning pump. The endpoints alone do not qualify a
    physical liquid EOS or a chamber temperature/pressure trajectory.
    """
    def fail(state,reason):return LiquidChamberCycleResult(state=state,reason=reason)
    if not (request.single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed and request.common_fluid_and_slip_reference_reviewed):
        return fail('needs_data','actual closed-mass, endpoint and slip-basis applicability need review')
    s,d=request.suction,request.discharge
    if s.density_kg_m3 is None or d.density_kg_m3 is None or not s.property_basis_id or not d.property_basis_id or request.slip_fraction_of_swept_suction_volume is None or 'unknown' in (s.phase,d.phase):
        return fail('needs_data','both actual endpoint liquid densities/bases and supplied slip are required')
    if s.phase != 'liquid' or d.phase != 'liquid':
        return fail('out_of_scope','both endpoint states must be single liquid')
    rho_s,rho_d=s.density_kg_m3,d.density_kg_m3
    if rho_d < rho_s:
        return fail('out_of_scope','these endpoints do not describe residual-liquid expansion toward suction')
    vc,vs=request.end_discharge_dead_volume_m3,request.swept_volume_m3
    ratio=rho_d/rho_s
    # Difference of actual endpoint densities preserves the incompressible
    # limit without subtracting an already rounded near-unity ratio.
    expansion=vc*((rho_d-rho_s)/rho_s)
    intake=vs-expansion
    if intake < 0:
        return fail('out_of_scope','stroke cannot reach the suction-valve opening state')
    slip_volume=vs*request.slip_fraction_of_swept_suction_volume
    net_volume=intake-slip_volume
    if net_volume < 0:
        return fail('out_of_scope','supplied lost mass exceeds available gross transfer')
    residual=rho_d*vc;gross_mass=rho_s*intake;slip_mass=rho_s*slip_volume;net_mass=rho_s*net_volume
    out_volume=net_mass/rho_d;eta=net_volume/vs;opening=vc+expansion
    values=(ratio,expansion,intake,slip_volume,net_volume,residual,gross_mass,slip_mass,net_mass,out_volume,eta,opening)
    if not all(math.isfinite(v) for v in values) or (net_volume > 0 and (net_mass == 0 or out_volume == 0 or eta == 0)):
        return fail('out_of_scope','cycle arithmetic lies outside representable finite physical coordinates')
    return LiquidChamberCycleResult(state='computed',reason='reviewed single-liquid endpoint mass cycle',discharge_to_suction_density_ratio=ratio,residual_liquid_mass_kg=residual,suction_valve_opening_chamber_volume_m3=opening,closed_valve_reexpansion_volume_m3=expansion,gross_suction_volume_m3_per_cycle=intake,net_suction_reference_volume_m3_per_cycle=net_volume,net_discharge_reference_volume_m3_per_cycle=out_volume,gross_transferred_mass_kg_per_cycle=gross_mass,supplied_slip_mass_kg_per_cycle=slip_mass,net_transferred_mass_kg_per_cycle=net_mass,suction_referenced_volumetric_efficiency=eta)
