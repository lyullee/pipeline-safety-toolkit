"""Ideal sonic-exit momentum contribution from supplied mass and temperature.

This observes a jet contribution, not a whole valve or support force. An outlet
pressure/area contribution is available only when explicitly supplied, and no
flow capacity or sonic applicability is inferred from mass flow alone.
"""
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive,Calculation
from .gas_acoustic_pipe import IdealGasInternalEnergyEOS
from .gas_basis import R_SI_J_MOL_K
from .ideal_gas_opening_observer import IdealOpeningGas
from .port_momentum_load import MomentumPort,MomentumLoadRequest,evaluate_port_momentum_load


class SonicJetReactionRequest(Contract):
    gas: IdealOpeningGas
    outward_mass_flow_kg_s: Nonnegative | None
    temperature_K: Positive | None
    temperature_definition: Literal['upstream_stagnation','throat_static']
    outward_unit_direction: tuple[float,float,float]
    coordinate_frame_id: Name
    asset_id: Name
    clock_id: Name
    observation_time_s: Nonnegative
    reviewed_sonic_exit_and_temperature_definition: bool = False
    actual_exit_area_m2: Positive | None = None
    exit_pressure_Pa_abs: Positive | None = None
    ambient_pressure_Pa_abs: Positive | None = None
    evidence_ids: tuple[Name,...] = Field(min_length=1)

    @model_validator(mode='after')
    def direction_and_pressure(self):
        if not math.isclose(math.hypot(*self.outward_unit_direction),1.,rel_tol=0.,abs_tol=1e-12):
            raise ValueError('outlet direction must be a unit vector in the named frame')
        present=[v is not None for v in (self.actual_exit_area_m2,self.exit_pressure_Pa_abs,self.ambient_pressure_Pa_abs)]
        if any(present) and not all(present):
            raise ValueError('pressure thrust needs actual exit area, absolute exit and ambient pressures together')
        return self


def observe_sonic_jet_reaction(request:SonicJetReactionRequest) -> Calculation:
    q=SonicJetReactionRequest.model_validate(request.model_dump(mode='python'))
    if (q.outward_mass_flow_kg_s is None or q.temperature_K is None
            or q.gas.molar_mass_kg_mol is None or q.gas.heat_capacity_ratio is None
            or not q.reviewed_sonic_exit_and_temperature_definition
            or not q.gas.dry_calorically_perfect_gas_and_molar_mass_reviewed):
        return Calculation(values={},status='needs_data',notes=('Supplied mass, absolute temperature definition, reviewed dry ideal gas and independent sonic applicability are required.',))
    gamma=q.gas.heat_capacity_ratio
    eos=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=R_SI_J_MOL_K/q.gas.molar_mass_kg_mol,gamma=gamma)
    throat_t=q.temperature_K*2/(gamma+1) if q.temperature_definition=='upstream_stagnation' else q.temperature_K
    total_t=throat_t*(gamma+1)/2
    # Density is an internal normalization of the ideal EOS only: sound speed
    # depends on T, not this density. No pressure/density is inferred or returned.
    point=eos.evaluate(1.,eos.internal_energy_from_temperature(throat_t))
    velocity=tuple(point.sound_speed_m_s*d for d in q.outward_unit_direction)
    momentum=tuple(-q.outward_mass_flow_kg_s*v for v in velocity)
    if not all(math.isfinite(v) for v in (*momentum,*velocity,total_t)):
        raise ValueError('sonic reaction exceeds finite arithmetic support')
    values=dict(asset_id=q.asset_id,clock_id=q.clock_id,observation_time_s=q.observation_time_s,
        coordinate_frame_id=q.coordinate_frame_id,composition_id=q.gas.composition_id,property_basis_id=q.gas.property_basis_id,
        outward_mass_flow_kg_s=q.outward_mass_flow_kg_s,upstream_stagnation_temperature_K=total_t,
        sonic_static_temperature_K=throat_t,sonic_exit_velocity_m_s=velocity,momentum_reaction_contribution_N=momentum,
        pressure_reaction_contribution_N=None,combined_open_port_contribution_N=None,
        sonic_capacity_or_applicability_predicted=False,whole_hardware_force_identified=False,
        field_or_standard_qualified=False,evidence_ids=q.evidence_ids)
    if q.actual_exit_area_m2 is not None:
        port=MomentumPort(id='sonic-exit',outward_unit_normal=q.outward_unit_direction,area_m2=q.actual_exit_area_m2,
            pressure_Pa_abs=q.exit_pressure_Pa_abs,outward_mass_flow_kg_s=q.outward_mass_flow_kg_s,
            fluid_velocity_m_s=velocity if q.outward_mass_flow_kg_s else (0.,0.,0.))
        load=evaluate_port_momentum_load(MomentumLoadRequest(coordinate_frame_id=q.coordinate_frame_id,
            ambient_pressure_Pa_abs=q.ambient_pressure_Pa_abs,ports=(port,),uniform_port_states_and_common_frame_reviewed=True))
        values.update(pressure_reaction_contribution_N=load.values['port_contributions'][0]['pressure_contribution_N'],
            combined_open_port_contribution_N=load.values['summed_open_port_contribution_N'])
    return Calculation(values=values,notes=('Sonic ideal exit momentum contribution only. Supplied pressure thrust is separate; inlet, body force and stored momentum are needed for a whole hardware load. No real-gas, steam, expanding plume, support stress or legacy dimensional coefficient identity is inferred.',))
