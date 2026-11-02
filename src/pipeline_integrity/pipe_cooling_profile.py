"""Local constant-property pipe temperature observations via shared cooling owner."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Positive,Nonnegative
from .models.thermal import Cooling,cooling


class PipeCoolingProfileRequest(Contract):
    inlet_temperature_K: Positive
    ambient_temperature_K: Positive
    mass_flow_kg_s: Positive
    specific_heat_J_kg_K: Positive
    length_m: Positive
    overall_U_W_m2_K: Nonnegative
    U_reference_area_diameter_m: Positive
    observation_positions_m: tuple[Nonnegative,...] = ()
    constant_properties_uniform_ambient_and_U_area_basis_reviewed: bool = False

    @model_validator(mode='after')
    def domain(self):
        if any(x>self.length_m for x in self.observation_positions_m):
            raise ValueError('observation position exceeds pipe length')
        return self


class PipeCoolingProfileResult(Contract):
    status: Literal['computed','needs_data']
    observation_positions_m: tuple[Nonnegative,...] = ()
    observed_temperatures_K: tuple[Positive,...] = ()
    outlet_temperature_K: Positive | None = None
    heat_loss_W: float | None = None
    total_conductance_W_K: Nonnegative | None = None
    issues: tuple[str,...] = ()
    joule_thomson_or_pressure_work_included: Literal[False] = False


def evaluate_pipe_cooling_profile(request: PipeCoolingProfileRequest) -> PipeCoolingProfileResult:
    """Resolve U*pi*D_reference*x into the existing plug-flow cooling owner.

    The reference diameter must match the area basis of supplied U. It is not
    inferred to be a hydraulic inner diameter or a coated pipe outer diameter.
    Negative reported heat loss denotes heating from the ambient. This local
    constant-property energy balance excludes pressure work and JT effects.
    """
    q=PipeCoolingProfileRequest.model_validate(request.model_dump(mode='python'))
    if not q.constant_properties_uniform_ambient_and_U_area_basis_reviewed:
        return PipeCoolingProfileResult(status='needs_data',issues=('constant properties, uniform ambient and matched U-area basis require review',))
    conductance_per_length=q.overall_U_W_m2_K*math.pi*q.U_reference_area_diameter_m
    if not math.isfinite(conductance_per_length*q.length_m):raise ValueError('conductance exceeds finite support')
    def at(x):
        return cooling(Cooling(inlet_temperature_k=q.inlet_temperature_K,ambient_temperature_k=q.ambient_temperature_K,
            mass_flow_kg_s=q.mass_flow_kg_s,specific_heat_j_kg_k=q.specific_heat_J_kg_K,
            conductance_w_k=conductance_per_length*x)).values
    final=at(q.length_m)
    observations=tuple(at(x)['outlet_temperature_k'] for x in q.observation_positions_m)
    if not all(math.isfinite(v) for v in (*observations,*final.values())):raise ValueError('cooling response exceeds finite support')
    return PipeCoolingProfileResult(status='computed',observation_positions_m=q.observation_positions_m,
        observed_temperatures_K=observations,outlet_temperature_K=final['outlet_temperature_k'],heat_loss_W=final['heat_loss_w'],
        total_conductance_W_K=conductance_per_length*q.length_m)
