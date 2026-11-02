"""Observed compressor energy boundaries and gas recycle-state reconstruction."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract, Name, Nonnegative, Positive, RunContext
from .energy_network import EnergyMixingNode, EnergyMixingEdge, EnergyNetworkRequest, evaluate_energy_network
from .thermo import CheckedProvider, CoolPropProvider, StateRequest, ThermodynamicState
from .gas_thermal_observation import checked_gas_thermal_state


class GasRecycleInletRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    fluid: Name
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    fresh_mass_flow_kg_s: Positive
    return_mass_flow_kg_s: Nonnegative | None
    inlet_pressure_Pa_abs: Positive
    fresh_temperature_K: Positive
    return_pressure_Pa_abs: Positive | None
    return_temperature_K: Positive | None
    hydraulic_source_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    energy_reference_id: Name
    evidence_id: Name
    same_composition_steady_adiabatic_throttle_mix_negligible_KE_PE_reviewed: bool = False


class GasRecycleInletResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    total_impeller_mass_flow_kg_s: Positive | None = None
    mixed_inlet_state: ThermodynamicState | None = None
    fresh_state: ThermodynamicState | None = None
    return_state_before_throttle: ThermodynamicState | None = None
    return_state_after_throttle: ThermodynamicState | None = None
    mixing_energy_residual_W: float | None = None
    mixing_receipt: dict | None = None
    issues: tuple[str,...] = ()
    seal_leakage_or_compressor_performance_predicted: Literal[False] = False


def reconstruct_gas_recycle_inlet(q: GasRecycleInletRequest, context: RunContext,
    *, property_provider=None) -> GasRecycleInletResult:
    """Reuse the existing junction-energy solve, then the existing P/h state resolver.

    Return mass flow is supplied, not estimated from clearance. A reviewed
    adiabatic pressure reduction preserves return enthalpy. Mixing does not
    average temperature or assign an efficiency decrement. No material/property
    table, labyrinth coefficient or compressor performance map is embedded.
    """
    q=GasRecycleInletRequest.model_validate(q.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('recycle context asset/clock mismatch')
    if q.evidence_id not in {e.id for e in c.evidence}:raise ValueError('recycle basis evidence is absent')
    if (q.return_mass_flow_kg_s is None
        or not q.same_composition_steady_adiabatic_throttle_mix_negligible_KE_PE_reviewed
        or (q.return_mass_flow_kg_s>0 and None in (q.return_pressure_Pa_abs,q.return_temperature_K))):
        return GasRecycleInletResult(status='needs_data',issues=('reviewed gas path and complete active return mass/state required',))
    if q.return_mass_flow_kg_s>0 and q.return_pressure_Pa_abs<q.inlet_pressure_Pa_abs:
        return GasRecycleInletResult(status='out_of_scope',issues=('return state cannot reach inlet pressure by passive throttle',))
    provider=CheckedProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False))
    identity=[]
    def resolve(inputs):
        s=checked_gas_thermal_state(StateRequest(fluid=q.fluid,backend=q.backend,inputs=inputs),property_provider=provider,
            expected_provider_identity=identity[0] if identity else None)
        key=(s.provider_version,s.provider_revision)
        identity.append(key);return s
    fresh=resolve({'P':q.inlet_pressure_Pa_abs,'T':q.fresh_temperature_K})
    returned=throttled=None
    nodes=[EnergyMixingNode(id='fresh',supplied_mass_flow_kg_s=q.fresh_mass_flow_kg_s,
        supplied_specific_total_energy_J_kg=fresh.enthalpy_j_kg,evidence_id=q.evidence_id)]
    edges=[EnergyMixingEdge(id='fresh-to-inlet',upstream_node_id='fresh',downstream_node_id='inlet',
        mass_flow_kg_s=q.fresh_mass_flow_kg_s,evidence_id=q.evidence_id)]
    if q.return_mass_flow_kg_s>0:
        returned=resolve({'P':q.return_pressure_Pa_abs,'T':q.return_temperature_K})
        throttled=resolve({'P':q.inlet_pressure_Pa_abs,'Hmass':returned.enthalpy_j_kg})
        nodes.append(EnergyMixingNode(id='return',supplied_mass_flow_kg_s=q.return_mass_flow_kg_s,
            supplied_specific_total_energy_J_kg=throttled.enthalpy_j_kg,evidence_id=q.evidence_id))
        edges.append(EnergyMixingEdge(id='return-to-inlet',upstream_node_id='return',downstream_node_id='inlet',
            mass_flow_kg_s=q.return_mass_flow_kg_s,evidence_id=q.evidence_id))
    mass=q.fresh_mass_flow_kg_s+q.return_mass_flow_kg_s
    if not math.isfinite(mass):raise ValueError('total recycle mass flow exceeds finite support')
    nodes.append(EnergyMixingNode(id='inlet',withdrawn_mass_flow_kg_s=mass,evidence_id=q.evidence_id))
    receipt=evaluate_energy_network(EnergyNetworkRequest(id=q.id,asset_id=q.asset_id,clock_id=q.clock_id,
        time_s=q.time_s,fluid_composition_id=q.fluid,total_energy_reference_id=q.energy_reference_id,
        hydraulic_source_digest=q.hydraulic_source_digest,nodes=tuple(nodes),edges=tuple(edges),
        fixed_flow_complete_mixing_and_common_energy_basis_reviewed=True,evidence_ids=(q.evidence_id,)),c)
    mixed_h=next(n['specific_total_energy_J_kg'] for n in receipt['nodes'] if n['node_id']=='inlet')
    mixed=resolve({'P':q.inlet_pressure_Pa_abs,'Hmass':mixed_h})
    return GasRecycleInletResult(status='computed',total_impeller_mass_flow_kg_s=mass,
        mixed_inlet_state=mixed,fresh_state=fresh,return_state_before_throttle=returned,
        return_state_after_throttle=throttled,mixing_energy_residual_W=receipt['global_energy_residual_W'],mixing_receipt=receipt)
