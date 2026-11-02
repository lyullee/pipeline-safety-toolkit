"""Equilibrium inventory release using caller-reviewed phase states and reference composition."""
from __future__ import annotations

import math
from typing import Literal, Protocol

from .contracts import Contract, Fraction, Name, Nonnegative, Positive


class OffshorePhaseState(Contract):
    pressure_Pa: Positive
    temperature_K: Positive
    oil_density_kg_m3: Positive
    free_gas_density_kg_m3: Positive
    oil_volume_fraction: Fraction
    composition_id: Name
    property_basis_id: Name


class OffshorePhaseProvider(Protocol):
    def state(self, *, pressure_Pa: float, temperature_K: float,
              composition_id: str, property_basis_id: str) -> OffshorePhaseState: ...


class OffshoreInventoryRequest(Contract):
    pipe_volume_m3: Positive
    initial_pressure_Pa: Positive
    initial_temperature_K: Positive
    ambient_pressure_Pa: Positive
    ambient_temperature_K: Positive
    composition_id: Name
    property_basis_id: Name
    reference_volume_basis: Name
    reference_oil_density_kg_m3: Positive
    reference_gas_density_kg_m3: Positive
    reference_gas_oil_volume_ratio: Nonnegative
    pre_shutin_oil_reference_flow_m3_s: Nonnegative
    time_until_shutin_s: Nonnegative
    initial_state: OffshorePhaseState | None = None
    ambient_state: OffshorePhaseState | None = None
    equilibrium_phase_states_and_common_pvt_basis_reviewed: bool = False
    released_material_has_reference_gas_oil_ratio_reviewed: bool = False
    single_horizontal_full_bore_segment_reviewed: bool = False


class OffshoreInventoryResult(Contract):
    status: Literal["computed", "needs_data"]
    reference_volume_basis: Name
    initial_mass_kg: Nonnegative | None = None
    ambient_remaining_mass_kg: Nonnegative | None = None
    released_inventory_mass_kg: Nonnegative | None = None
    released_inventory_oil_mass_kg: Nonnegative | None = None
    released_inventory_gas_mass_kg: Nonnegative | None = None
    released_inventory_oil_reference_volume_m3: Nonnegative | None = None
    released_inventory_gas_reference_volume_m3: Nonnegative | None = None
    pre_shutin_oil_reference_release_m3: Nonnegative | None = None
    total_oil_reference_release_m3: Nonnegative | None = None
    phase_property_estimation_performed: Literal[False] = False
    time_resolved_discharge_computed: Literal[False] = False
    issues: tuple[str, ...] = ()


def _checked_state(state: OffshorePhaseState, q: OffshoreInventoryRequest,
                   pressure: float, temperature: float) -> OffshorePhaseState:
    if not isinstance(state, OffshorePhaseState):
        raise TypeError("provider must return OffshorePhaseState")
    state = OffshorePhaseState.model_validate(state.model_dump(mode="python"))
    if (state.pressure_Pa, state.temperature_K) != (pressure, temperature):
        raise ValueError("phase state does not match requested absolute pressure/temperature")
    if state.composition_id != q.composition_id or state.property_basis_id != q.property_basis_id:
        raise ValueError("phase states require common declared composition and property basis")
    return state


def evaluate_offshore_inventory_release(
    request: OffshoreInventoryRequest, *, provider: OffshorePhaseProvider | None = None,
) -> OffshoreInventoryResult:
    """Evaluate a conditional initial/final equilibrium inventory approximation.

    Phase densities and phase volume fractions come from reviewed caller data or
    a provider. Their composition, dissolved-gas closure and reference-volume
    conventions are caller responsibilities. The released total mass is allocated
    with the explicitly accepted reference gas/oil ratio, rather than with either
    endpoint's instantaneous free-gas volume fraction. This supplies no oil PVT
    correlation, empirical factor table, discharge duration or general spill model.
    """
    if not isinstance(request, OffshoreInventoryRequest):
        raise TypeError("OffshoreInventoryRequest required")
    q = OffshoreInventoryRequest.model_validate(request.model_dump(mode="python"))
    if q.initial_pressure_Pa < q.ambient_pressure_Pa:
        raise ValueError("outward depressurization requires initial pressure >= ambient pressure")
    issues = []
    for field in ("equilibrium_phase_states_and_common_pvt_basis_reviewed",
                  "released_material_has_reference_gas_oil_ratio_reviewed",
                  "single_horizontal_full_bore_segment_reviewed"):
        if not getattr(q, field):
            issues.append("review required: " + field)
    if issues:
        return OffshoreInventoryResult(status="needs_data", reference_volume_basis=q.reference_volume_basis,
                                       issues=tuple(issues))
    points = (("initial", q.initial_state, q.initial_pressure_Pa, q.initial_temperature_K),
              ("ambient", q.ambient_state, q.ambient_pressure_Pa, q.ambient_temperature_K))
    states = []
    for label, supplied, pressure, temperature in points:
        if supplied is None and provider is not None:
            supplied = provider.state(pressure_Pa=pressure, temperature_K=temperature,
                                      composition_id=q.composition_id, property_basis_id=q.property_basis_id)
        if supplied is None:
            issues.append("missing " + label + " phase state")
        else:
            states.append(_checked_state(supplied, q, pressure, temperature))
    if issues:
        return OffshoreInventoryResult(status="needs_data", reference_volume_basis=q.reference_volume_basis,
                                       issues=tuple(issues))
    masses = [q.pipe_volume_m3 * math.fsum((s.oil_volume_fraction * s.oil_density_kg_m3,
               (1 - s.oil_volume_fraction) * s.free_gas_density_kg_m3)) for s in states]
    initial, remaining = masses
    if not all(math.isfinite(x) for x in masses):
        raise ValueError("phase inventory exceeds finite numerical support")
    released = initial - remaining
    if released < 0:
        raise ValueError("ambient mass exceeds initial mass; reviewed outward release closure required")
    # Per unit reference oil volume, total mixture mass includes its associated
    # reference gas volume. This avoids subtracting a near-one gas mass fraction.
    mass_per_oil_reference_volume = math.fsum((q.reference_oil_density_kg_m3,
        q.reference_gas_density_kg_m3 * q.reference_gas_oil_volume_ratio))
    if not math.isfinite(mass_per_oil_reference_volume):
        raise ValueError("reference mixture mass coordinate exceeds finite numerical support")
    oil_volume = released / mass_per_oil_reference_volume
    gas_volume = oil_volume * q.reference_gas_oil_volume_ratio
    oil_mass = oil_volume * q.reference_oil_density_kg_m3
    gas_mass = gas_volume * q.reference_gas_density_kg_m3
    feed = q.pre_shutin_oil_reference_flow_m3_s * q.time_until_shutin_s
    total = math.fsum((oil_volume, feed))
    if not all(math.isfinite(x) for x in (oil_volume, gas_volume, oil_mass, gas_mass, feed, total)):
        raise ValueError("release coordinates exceed finite numerical support")
    return OffshoreInventoryResult(status="computed", reference_volume_basis=q.reference_volume_basis,
        initial_mass_kg=initial, ambient_remaining_mass_kg=remaining, released_inventory_mass_kg=released,
        released_inventory_oil_mass_kg=oil_mass, released_inventory_gas_mass_kg=gas_mass,
        released_inventory_oil_reference_volume_m3=oil_volume,
        released_inventory_gas_reference_volume_m3=gas_volume,
        pre_shutin_oil_reference_release_m3=feed, total_oil_reference_release_m3=total)
