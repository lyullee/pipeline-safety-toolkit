"""Rigid gas receiver sizing from reviewed endpoint density inventories."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive
from .models.thermal import Inventory,inventory
from .gas_valve_reference import _GasPhaseProvider
from .thermo import CoolPropProvider


class GasReceiverSizingRequest(Contract):
    fluid: Name
    high_pressure_Pa_abs: Positive
    low_pressure_Pa_abs: Positive
    high_temperature_K: Positive
    low_temperature_K: Positive
    demand_reference_pressure_Pa_abs: Positive
    demand_reference_temperature_K: Positive
    demand_reference_volume_flow_m3_s: Nonnegative | None = None
    duration_per_demand_s: Nonnegative | None = None
    demand_count: int = Field(default=1,ge=0)
    property_and_reference_basis_id: Name
    rigid_uniform_same_composition_and_endpoint_states_reviewed: bool = False

    @model_validator(mode='after')
    def pressure_range(self):
        if self.high_pressure_Pa_abs<=self.low_pressure_Pa_abs:
            raise ValueError('receiver pressure must decrease between declared endpoints')
        return self


class GasReceiverSizingResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    property_and_reference_basis_id: Name
    required_receiver_volume_m3: Nonnegative | None = None
    demand_reference_volume_m3: Nonnegative | None = None
    demand_mass_kg: Nonnegative | None = None
    high_density_kg_m3: Positive | None = None
    low_density_kg_m3: Positive | None = None
    reference_density_kg_m3: Positive | None = None
    initial_receiver_mass_kg: Nonnegative | None = None
    residual_receiver_mass_kg: Nonnegative | None = None
    mass_capacity_residual_kg: float | None = None
    issues: tuple[str,...] = ()
    geometry_or_pressure_rating_selected: Literal[False] = False


def size_gas_receiver(q: GasReceiverSizingRequest, *, property_provider=None) -> GasReceiverSizingResult:
    """Invert the existing uniform gas inventory for a demanded mass withdrawal.

    Every demand-volume rate refers to its stated reference P/T. Receiver high
    and low temperatures are independently supplied endpoint states; an adiabatic
    or other withdrawal temperature path is not predicted. Geometry, minimum
    delivery-rate performance, leakage losses and pressure rating remain outside
    this inventory sizing relation.
    """
    if not isinstance(q,GasReceiverSizingRequest):raise TypeError('GasReceiverSizingRequest required')
    q=GasReceiverSizingRequest.model_validate(q.model_dump(mode='python'))
    common=dict(property_and_reference_basis_id=q.property_and_reference_basis_id)
    if q.demand_reference_volume_flow_m3_s is None or q.duration_per_demand_s is None or not q.rigid_uniform_same_composition_and_endpoint_states_reviewed:
        return GasReceiverSizingResult(status='needs_data',issues=('reviewed endpoint/reference basis and demand rate/duration required',),**common)
    provider=_GasPhaseProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False))
    snapshots=[]
    for p,t in [(q.high_pressure_Pa_abs,q.high_temperature_K),(q.low_pressure_Pa_abs,q.low_temperature_K)]:
        snapshots.append(inventory(Inventory(fluid=q.fluid,pressure_pa_abs=p,temperature_k=t,internal_volume_m3=1.,
            reference_pressure_pa_abs=q.demand_reference_pressure_Pa_abs,reference_temperature_k=q.demand_reference_temperature_K),
            property_provider=provider).values)
    high,low=snapshots
    rh=high['density_kg_m3'];rl=low['density_kg_m3']
    reference_high=rh/high['reference_volume_m3'];reference_low=rl/low['reference_volume_m3']
    if not math.isclose(reference_high,reference_low,rel_tol=1e-12):raise ValueError('reference density changed between endpoint inventory calls')
    difference=rh-rl
    if difference<=0:
        return GasReceiverSizingResult(status='out_of_scope',issues=('declared endpoints provide no positive gas mass withdrawal per unit vessel volume',),**common)
    reference_volume=q.demand_reference_volume_flow_m3_s*q.duration_per_demand_s*q.demand_count
    demand_mass=reference_volume*reference_high
    volume=demand_mass/difference
    initial=volume*rh;remaining=volume*rl
    residual=math.fsum((initial,-remaining,-demand_mass))
    if not all(math.isfinite(x) for x in (reference_high,reference_volume,demand_mass,volume,initial,remaining,residual)):
        raise ValueError('receiver inventory sizing exceeds finite numerical support')
    return GasReceiverSizingResult(status='computed',required_receiver_volume_m3=volume,
        demand_reference_volume_m3=reference_volume,demand_mass_kg=demand_mass,
        high_density_kg_m3=rh,low_density_kg_m3=rl,reference_density_kg_m3=reference_high,
        initial_receiver_mass_kg=initial,residual_receiver_mass_kg=remaining,mass_capacity_residual_kg=residual,**common)
