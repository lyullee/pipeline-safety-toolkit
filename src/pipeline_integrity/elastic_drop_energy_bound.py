"""Energy-equivalent linear-target deflection bound using the existing oscillator."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Positive,Nonnegative
from .harmonic_response import HarmonicOscillator,sample_response


class ElasticDropEnergyBoundRequest(Contract):
    falling_mass_kg: Positive | None = None
    supported_gravity_mass_kg: Nonnegative | None = None
    lumped_dynamic_mass_kg: Positive | None = None
    target_stiffness_n_m: Positive | None = None
    gravity_m_s2: Positive | None = None
    fall_height_above_undeflected_target_m: Nonnegative | None = None
    maximum_reviewed_linear_target_deflection_m: Positive | None = None
    linear_target_and_energy_equivalent_envelope_reviewed: bool = False


class ElasticDropEnergyBoundResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    target_initial_static_deflection_m: Nonnegative | None = None
    peak_total_target_deflection_m: Positive | None = None
    peak_spring_reaction_n: Positive | None = None
    equivalent_oscillator_peak_time_s: Positive | None = None
    equivalent_initial_velocity_m_s: Nonnegative | None = None
    energy_balance_residual_j: float | None = None
    issues: tuple[str,...] = ()
    reference_height: Literal['above_zero_deflection_target_position'] = 'above_zero_deflection_target_position'
    actual_collision_momentum_rebound_or_contact_force_predicted: Literal[False] = False


def evaluate_elastic_drop_energy_bound(request: ElasticDropEnergyBoundRequest) -> ElasticDropEnergyBoundResult:
    """Transfer an idealized drop's energy into the existing undamped oscillator.

    The target starts at its own gravity equilibrium. Drop height is measured
    above its undeflected position, so the preloaded target adds a physical gap.
    All falling gravitational energy is assigned to one oscillator coordinate;
    an inelastic collision's momentum loss, rebound, contact deformation and
    actual distributed target modes are excluded. The supplied dynamic mass
    sets an equivalent oscillator clock, not a measured impact duration.
    The returned force is spring reaction at maximum deflection, not an inferred
    contact-force waveform. Linear deflection support remains caller supplied.
    """
    if not isinstance(request,ElasticDropEnergyBoundRequest):raise TypeError('ElasticDropEnergyBoundRequest required')
    q=ElasticDropEnergyBoundRequest.model_validate(request.model_dump(mode='python'))
    fields=('falling_mass_kg','supported_gravity_mass_kg','lumped_dynamic_mass_kg','target_stiffness_n_m',
        'gravity_m_s2','fall_height_above_undeflected_target_m','maximum_reviewed_linear_target_deflection_m')
    missing=['missing '+k for k in fields if getattr(q,k) is None]
    if not q.linear_target_and_energy_equivalent_envelope_reviewed:missing.append('linear target and energy-equivalent impact envelope require review')
    if missing:return ElasticDropEnergyBoundResult(status='needs_data',issues=tuple(missing))
    weight=q.falling_mass_kg*q.gravity_m_s2
    preload=q.supported_gravity_mass_kg*q.gravity_m_s2
    stiffness=q.target_stiffness_n_m;mass=q.lumped_dynamic_mass_kg
    initial=preload/stiffness
    equilibrium=(weight+preload)/stiffness
    velocity=math.sqrt(2*(weight/mass)*(q.fall_height_above_undeflected_target_m+initial))
    natural=math.sqrt(stiffness)/math.sqrt(mass)
    peak_time=(math.pi-math.atan2(velocity,natural*weight/stiffness))/natural
    model=HarmonicOscillator(mass,stiffness,0.,0.,0.)
    peak=sample_response(model,(peak_time,),initial_displacement_m=-weight/stiffness,initial_velocity_m_s=velocity)[0]
    displacement=equilibrium+peak.displacement_m
    if not all(math.isfinite(v) for v in (initial,equilibrium,velocity,peak_time,displacement)):
        raise ValueError('energy-equivalent impact exceeds finite numerical support')
    if displacement>q.maximum_reviewed_linear_target_deflection_m:
        return ElasticDropEnergyBoundResult(status='out_of_scope',issues=('energy-bound deflection exceeds caller-reviewed linear target support',))
    reaction=stiffness*displacement
    # Initial target spring/gravity balance and the falling object's potential
    # use the same undeflected reference; peak kinetic energy is zero.
    residual=.5*stiffness*displacement**2-(weight+preload)*displacement-weight*q.fall_height_above_undeflected_target_m+.5*preload*initial
    return ElasticDropEnergyBoundResult(status='computed',target_initial_static_deflection_m=initial,
        peak_total_target_deflection_m=displacement,peak_spring_reaction_n=reaction,
        equivalent_oscillator_peak_time_s=peak_time,equivalent_initial_velocity_m_s=velocity,energy_balance_residual_j=residual)
