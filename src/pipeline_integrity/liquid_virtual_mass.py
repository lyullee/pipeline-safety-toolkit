"""Explicit inertia-coordinate adapter for uniform liquid kinetic correction.

Physical density remains the caller's fluid density. The existing acoustic MOC
owner instead receives labelled mathematical coordinates: rho_i=alpha*rho,
g_i=g/alpha, c_i=c/sqrt(alpha), f_i=f/alpha. Their product rho_i*g_i equals
physical rho*g, so pressure, hydrostatic head and storage retain their units.
This is a uniform linear-acoustic inertia correction, not a fluid EOS change.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from pydantic import Field
from .contracts import Contract,Positive,RunContext,digest
from .transient import LiquidTransient,simulate_liquid_transient


class VirtualMassLiquidRequest(Contract):
    physical_specification: LiquidTransient
    uniform_inertia_factor: Positive = Field(ge=1.)
    uniform_kinetic_correction_reviewed: bool = False


@dataclass(frozen=True)
class VirtualMassCoordinates:
    owner_specification: LiquidTransient
    physical_density_kg_m3: float
    physical_gravity_m_s2: float
    uniform_inertia_factor: float


def virtual_mass_coordinates(request:VirtualMassLiquidRequest):
    """Map both hyperbolic impedance and distributed source acceleration.

    In physical coordinates the equations are p_t+rho*c²*v_x=0 and
    alpha*rho*v_t=-p_x-rho*g*z_x-f*rho*v*abs(v)/(2D).
    Head is always z+p_gauge/(rho*g). Pump/valve component laws require a
    separate reviewed mapping and are rejected; ordinary head/flow nodes and
    explicitly head-based emitter histories retain their physical meaning.
    """
    q=VirtualMassLiquidRequest.model_validate(request.model_dump(mode='python'))
    if not q.uniform_kinetic_correction_reviewed:raise ValueError('uniform linear-acoustic inertia correction requires explicit review')
    s=q.physical_specification;a=q.uniform_inertia_factor
    if s.pumps or s.valves:raise ValueError('pump/valve coordinate mappings are outside the pipe-inertia adapter')
    pipes=tuple(p.model_copy(update={'wave_speed_m_s':p.wave_speed_m_s/math.sqrt(a),
        'darcy_friction_factor':p.darcy_friction_factor/a}) for p in s.pipes)
    data=s.model_dump(mode='python');data.update(density_kg_m3=a*s.density_kg_m3,
        gravity_m_s2=s.gravity_m_s2/a,pipes=tuple(p.model_dump(mode='python') for p in pipes))
    owner=LiquidTransient.model_validate(data)
    return VirtualMassCoordinates(owner,s.density_kg_m3,s.gravity_m_s2,a)


def simulate_virtual_mass_liquid(request:VirtualMassLiquidRequest,context:RunContext):
    """Return transparent owner receipt and explicitly physical pressure traces.

    A supplied kinetic correction modifies inertial response and pressure-wave
    impedance while leaving steady Darcy/hydrostatic head loss unchanged.
    The pressure/velocity equations omit convective and variable-alpha terms;
    no turbulent-profile calibration or broad transient-fluid claim is made.
    """
    q=VirtualMassLiquidRequest.model_validate(request.model_dump(mode='python'))
    m=virtual_mass_coordinates(q);s=q.physical_specification
    result=simulate_liquid_transient(m.owner_specification,context)
    nodes={n.id:n for n in s.nodes};pressures={}
    for p in s.pipes:
        h=result['pipes'][p.id]
        pressures[p.id]={side+'_pressure_pa_abs':[
            s.ambient_pressure_pa_abs+s.density_kg_m3*s.gravity_m_s2*(v-nodes[node].elevation_m)
            for v in h[side+'_head_m']]
            for side,node in (('start',p.start_node),('end',p.end_node))}
    return dict(schema='pipeline_integrity.virtual_mass_liquid.v1',status=result['status'],
        adapter_source_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        specification_digest=digest(q),physical_specification_digest=digest(s),context_digest=digest(context),
        coordinate_specification_digest=digest(m.owner_specification),physical_pressure_traces=pressures,owner_result=result,
        coordinate_meaning=dict(physical_density_kg_m3=s.density_kg_m3,physical_gravity_m_s2=s.gravity_m_s2,
            uniform_inertia_factor=q.uniform_inertia_factor,
            owner_inertial_coordinate_density_kg_m3=m.owner_specification.density_kg_m3,
            owner_coordinate_gravity_m_s2=m.owner_specification.gravity_m_s2,
            coordinate_density_is_fluid_property=False,physical_head_preserved=True,
            wave_speed_multiplier=1/math.sqrt(q.uniform_inertia_factor),pressure_impedance_multiplier=math.sqrt(q.uniform_inertia_factor),
            source_acceleration_multiplier=1/q.uniform_inertia_factor),
        limitations=['Uniform supplied kinetic factor only; no spatial/time-dependent profile evolution or variable-alpha convective terms.',
            'Linear single-phase acoustic owner, constant supplied Darcy factors, fixed pipe geometry and inherited small-Mach/storage limits.',
            'No pump/valve mapping, cavitation, mixed free-surface sections, phase kinetics or fluid-property estimation.'])
