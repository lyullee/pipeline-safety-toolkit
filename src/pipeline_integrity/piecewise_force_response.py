"""Exact propagation of explicitly held force stages through the existing oscillator."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative
from .harmonic_response import HarmonicOscillator,sample_response


class HeldForceStage(Contract):
    start_s: Nonnegative
    force_n: float | None


class GeneralizedResponseRequest(Contract):
    mass_kg: Positive | None = None
    stiffness_n_m: Positive | None = None
    damping_n_s_m: Nonnegative | None = None
    initial_displacement_m: float = 0.
    initial_velocity_m_s: float = 0.
    force_stages: tuple[HeldForceStage,...] = Field(min_length=1,max_length=10000)
    output_times_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=10000)
    load_and_coordinate_basis_id: Name | None = None
    linear_generalized_coordinate_and_held_force_basis_reviewed: bool = False

    @model_validator(mode='after')
    def times(self):
        if self.force_stages[0].start_s!=0:raise ValueError('held force history must start at time zero')
        if any(b.start_s<=a.start_s for a,b in zip(self.force_stages,self.force_stages[1:])):
            raise ValueError('held force stages must increase strictly')
        if any(b<=a for a,b in zip(self.output_times_s,self.output_times_s[1:])) or self.output_times_s[-1]<=0:
            raise ValueError('output times must increase strictly through positive duration')
        if self.force_stages[-1].start_s>self.output_times_s[-1]:raise ValueError('force stage lies beyond requested solve support')
        return self


class GeneralizedResponsePoint(Contract):
    time_s: Nonnegative
    displacement_m: float
    velocity_m_s: float
    acceleration_m_s2: float
    force_n: float


class GeneralizedResponseResult(Contract):
    status: Literal['computed','needs_data']
    load_and_coordinate_basis_id: Name | None
    points: tuple[GeneralizedResponsePoint,...] = ()
    native_segment_propagations: int = 0
    issues: tuple[str,...] = ()
    force_value_at_switch: Literal['right_continuous_state_displacement_and_velocity_continuous'] = 'right_continuous_state_displacement_and_velocity_continuous'
    continuous_extrema_nonlinear_shell_or_contact_qualified: Literal[False] = False


def evaluate_generalized_response(request: GeneralizedResponseRequest) -> GeneralizedResponseResult:
    """Compose the owner's constant-force initial-value propagator at each stage.

    Stage values are explicit right-continuous zero-order holds, not inferred
    interpolation of a measured waveform. Displacement/velocity pass unchanged
    across force switches; acceleration uses the new force at the switch.
    A reviewed single-mode shell/beam reduction may supply these generalized
    SI parameters, but modal mass, pressure area and mode shape are not inferred.
    Output extrema are sampled, not certified continuous-time peak searches.
    """
    if not isinstance(request,GeneralizedResponseRequest):raise TypeError('GeneralizedResponseRequest required')
    q=GeneralizedResponseRequest.model_validate(request.model_dump(mode='python'))
    missing=['missing '+k for k in ('mass_kg','stiffness_n_m','damping_n_s_m','load_and_coordinate_basis_id') if getattr(q,k) is None]
    if any(s.force_n is None for s in q.force_stages):missing.append('one or more force stages are unresolved')
    if not q.linear_generalized_coordinate_and_held_force_basis_reviewed:missing.append('linear generalized coordinate and held-force meaning require review')
    base=dict(load_and_coordinate_basis_id=q.load_and_coordinate_basis_id)
    if missing:return GeneralizedResponseResult(status='needs_data',issues=tuple(missing),**base)
    outputs=set(q.output_times_s);events={s.start_s:s.force_n for s in q.force_stages}
    times=sorted({0.,*q.output_times_s,*events})
    x=q.initial_displacement_m;velocity=q.initial_velocity_m_s;time=0.;force=events[0.]
    points=[];calls=0
    for target in times:
        if target>time:
            model=HarmonicOscillator(q.mass_kg,q.stiffness_n_m,q.damping_n_s_m,abs(force),0.,math.pi if force<0 else 0.)
            sample=sample_response(model,(target-time,),initial_displacement_m=x,initial_velocity_m_s=velocity)[0]
            x=sample.displacement_m;velocity=sample.velocity_m_s;calls+=1
        time=target
        if target in events:force=events[target]
        if target in outputs:
            acceleration=(force-q.damping_n_s_m*velocity-q.stiffness_n_m*x)/q.mass_kg
            points.append(GeneralizedResponsePoint(time_s=target,displacement_m=x,velocity_m_s=velocity,
                acceleration_m_s2=acceleration,force_n=force))
    return GeneralizedResponseResult(status='computed',**base,points=tuple(points),native_segment_propagations=calls)


def pressure_stages_to_generalized_force(stages: tuple[tuple[float,float],...],*,generalized_pressure_area_m2: float,
                                         pressure_mode_mapping_basis_id: str,reviewed: bool) -> tuple[HeldForceStage,...]:
    """Map a supplied signed pressure difference to a reviewed generalized force.

    The effective area is a caller-provided modal/load mapping, not an inferred
    geometric shell area or a claim of a one-mode physical reduction.
    """
    if not reviewed or not pressure_mode_mapping_basis_id.strip():raise ValueError('pressure-mode mapping requires a reviewed explicit basis')
    if isinstance(generalized_pressure_area_m2,bool) or not math.isfinite(generalized_pressure_area_m2) or generalized_pressure_area_m2<=0:
        raise ValueError('a finite positive supplied generalized pressure area is required')
    return tuple(HeldForceStage(start_s=t,force_n=p*generalized_pressure_area_m2) for t,p in stages)
