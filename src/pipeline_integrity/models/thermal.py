"""Thermal and inventory budgets with explicit boundary states."""

import math

from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Nonnegative, Positive
from ..thermo import CoolPropProvider, StateRequest


class Inventory(Contract):
    fluid: str = Field(min_length=1)
    pressure_pa_abs: Positive
    temperature_k: Positive
    internal_volume_m3: Positive
    reference_pressure_pa_abs: Positive
    reference_temperature_k: Positive


def inventory(p: Inventory, *, property_provider=None):
    provider = property_provider if property_provider is not None else CoolPropProvider()
    actual = provider.resolve(
        StateRequest(fluid=p.fluid, inputs={"P": p.pressure_pa_abs, "T": p.temperature_k})
    )
    reference = provider.resolve(
        StateRequest(fluid=p.fluid, inputs={"P": p.reference_pressure_pa_abs, "T": p.reference_temperature_k})
    )
    mass = actual.density_kg_m3 * p.internal_volume_m3
    return Calculation(
        values={
            "density_kg_m3": actual.density_kg_m3,
            "mass_kg": mass,
            "reference_volume_m3": mass / reference.density_kg_m3,
            "specific_enthalpy_j_kg": actual.enthalpy_j_kg,
        },
        notes=("Uniform state inventory; spatially varying pipe states require segment integration.",),
    )


class InventorySegment(Contract):
    id: str = Field(min_length=1)
    pressure_pa_abs: Positive
    temperature_k: Positive
    internal_volume_m3: Positive


class SegmentedInventory(Contract):
    fluid: str = Field(min_length=1)
    segments: tuple[InventorySegment, ...] = Field(min_length=1)
    reference_pressure_pa_abs: Positive
    reference_temperature_k: Positive

    @model_validator(mode="after")
    def unique_segments(self):
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("inventory segment IDs must be unique")
        return self


def segmented_inventory(p: SegmentedInventory, *, property_provider=None):
    provider = property_provider if property_provider is not None else CoolPropProvider()
    reference = provider.resolve(
        StateRequest(fluid=p.fluid, inputs={"P": p.reference_pressure_pa_abs, "T": p.reference_temperature_k})
    )
    rows = []
    for segment in p.segments:
        state = provider.resolve(
            StateRequest(fluid=p.fluid, inputs={"P": segment.pressure_pa_abs, "T": segment.temperature_k})
        )
        mass = state.density_kg_m3 * segment.internal_volume_m3
        rows.append(
            {
                "id": segment.id,
                "volume_m3": segment.internal_volume_m3,
                "mass_kg": mass,
                "density_kg_m3": state.density_kg_m3,
                "enthalpy_j_kg": state.enthalpy_j_kg,
                "internal_energy_j": mass * state.enthalpy_j_kg
                - state.pressure_pa_abs * segment.internal_volume_m3,
                "phase": state.phase,
            }
        )
    mass = math.fsum(row["mass_kg"] for row in rows)
    return Calculation(
        values={
            "total_mass_kg": mass,
            "total_internal_volume_m3": math.fsum(s.internal_volume_m3 for s in p.segments),
            "reference_volume_m3": mass / reference.density_kg_m3,
            "mass_weighted_enthalpy_j_kg": math.fsum(row["mass_kg"] * row["enthalpy_j_kg"] for row in rows)
            / mass,
            "total_internal_energy_j": math.fsum(row["internal_energy_j"] for row in rows),
            "segment_results": rows,
        },
        notes=(
            "Snapshot sum of resolved uniform segments; pressure and temperature gradients come from the caller.",
            "Stored internal energy uses u=h-P/rho on a common property reference; kinetic and gravitational energy are excluded.",
            "This inventory integration is not a transient transport or phase-interface solver.",
        ),
    )


class Cooling(Contract):
    inlet_temperature_k: Positive
    ambient_temperature_k: Positive
    mass_flow_kg_s: Positive
    specific_heat_j_kg_k: Positive
    conductance_w_k: Nonnegative


def cooling(p: Cooling):
    capacity = p.mass_flow_kg_s * p.specific_heat_j_kg_k
    temperature = p.ambient_temperature_k + (p.inlet_temperature_k - p.ambient_temperature_k) * math.exp(
        -p.conductance_w_k / capacity
    )
    return Calculation(
        values={
            "outlet_temperature_k": temperature,
            "heat_loss_w": capacity * (p.inlet_temperature_k - temperature),
        }
    )


class Expansion(Contract):
    reference_length_m: Positive
    linear_expansion_per_k: Nonnegative
    reference_temperature_k: Positive
    operating_temperature_k: Positive


def expansion(p: Expansion):
    change = (
        p.reference_length_m
        * p.linear_expansion_per_k
        * (p.operating_temperature_k - p.reference_temperature_k)
    )
    if p.reference_length_m + change <= 0:
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("Linear expansion approximation leaves the positive-length domain.",),
        )
    return Calculation(
        values={"free_length_change_m": change, "operating_length_m": p.reference_length_m + change},
        notes=("Unrestrained linear expansion; support loads and stress belong to a structural model.",),
    )
