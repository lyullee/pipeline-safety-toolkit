"""Continuous stage extrema for reviewed held base acceleration using existing propagation."""
import math
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative
from .harmonic_response import HarmonicOscillator,sample_response

class BaseAccelerationStage(Contract):
    start_s: Nonnegative
    acceleration_m_s2: float | None

class HeldAccelerationSpectrumRequest(Contract):
    natural_frequencies_hz: tuple[Positive,...] = Field(min_length=1,max_length=100)
    damping_ratios: tuple[Nonnegative,...] = Field(min_length=1,max_length=20)
    base_acceleration_stages: tuple[BaseAccelerationStage,...] = Field(min_length=1,max_length=10000)
    duration_s: Positive
    initial_relative_displacement_m: float = 0.
    initial_relative_velocity_m_s: float = 0.
    maximum_event_candidates_per_oscillator: int = Field(default=100000,ge=10,le=1000000)
    acceleration_clock_interpolation_and_relative_coordinate_basis_id: Name
    known_zero_order_hold_and_linear_relative_coordinate_reviewed: bool = False

    @model_validator(mode='after')
    def history(self):
        if self.base_acceleration_stages[0].start_s!=0:raise ValueError('base acceleration starts at zero')
        if any(b.start_s<=a.start_s for a,b in zip(self.base_acceleration_stages,self.base_acceleration_stages[1:])):
            raise ValueError('stage clocks must increase without sorting')
        if self.base_acceleration_stages[-1].start_s>=self.duration_s:raise ValueError('last stage must have positive supported duration')
        if len(set(self.natural_frequencies_hz))!=len(self.natural_frequencies_hz) or len(set(self.damping_ratios))!=len(self.damping_ratios):
            raise ValueError('duplicate spectral coordinates require explicit resolution')
        return self

def _homogeneous_linear_event_times(row,state,omega,zeta,duration,budget):
    """Enumerate zero events of a linear observable, never propagate a state.

    Constant-force equilibrium removes forcing. Any derivative observable has
    two exponential or sinusoidal coefficients; its initial value/derivative
    determine zero times. All state values are evaluated by the existing owner.
    """
    gamma=2*zeta*omega;y,v=state
    r0=row[0]*y+row[1]*v
    rdot=row[0]*v+row[1]*(-omega**2*y-gamma*v)
    if r0==0 and rdot==0:return ()
    if zeta<1:
        decay=zeta*omega;wd=omega*math.sqrt((1-zeta)*(1+zeta))
        sine=(rdot+decay*r0)/wd
        phase=math.atan2(-r0,sine)
        first=math.ceil(-phase/math.pi);last=math.floor((wd*duration-phase)/math.pi)
        if last-first+1>budget:raise ValueError('continuous-extrema event budget exceeded')
        return tuple(t for n in range(first,last+1) if 0<(t:=(phase+n*math.pi)/wd)<duration)
    if zeta==1:
        slope=rdot+omega*r0
        if slope==0:return ()
        time=-r0/slope
    else:
        root=math.sqrt(zeta-1)*math.sqrt(zeta+1)
        slow=-omega/(zeta+root);fast=-omega*(zeta+root)
        first=(rdot-fast*r0)/(slow-fast);second=r0-first
        if first==0 or second==0 or math.copysign(1,first)==math.copysign(1,second):return ()
        time=(math.log(abs(second))-math.log(abs(first)))/(slow-fast)
    return (time,) if 0<time<duration else ()

def evaluate_held_acceleration_spectrum(request: HeldAccelerationSpectrumRequest):
    """Resolve Sd/Sv and both relative and absolute Sa over a declared interval.

    Base acceleration is a known right-held signal, not inferred interpolation
    of measurements. Relative motion obeys x''+2*zeta*wn*x'+wn²*x=-ag.
    Stage boundaries include both one-sided relative accelerations. Absolute
    acceleration is x''+ag; pseudo spectra wn*Sd and wn²*Sd are separate fields.
    A caller must include any desired free tail in the declared duration.
    """
    q=HeldAccelerationSpectrumRequest.model_validate(request.model_dump())
    if not q.known_zero_order_hold_and_linear_relative_coordinate_reviewed or any(s.acceleration_m_s2 is None for s in q.base_acceleration_stages):
        return dict(status='needs_data')
    rows=[]
    for frequency in q.natural_frequencies_hz:
        omega=2*math.pi*frequency
        for zeta in q.damping_ratios:
            gamma=2*zeta*omega;x=q.initial_relative_displacement_m;v=q.initial_relative_velocity_m_s
            peaks={name:dict(value=0.,time_s=0.,signed_value=0.) for name in ('relative_displacement_m','relative_velocity_m_s','relative_acceleration_m_s2','absolute_acceleration_m_s2')}
            candidates=0
            for index,stage in enumerate(q.base_acceleration_stages):
                stop=q.base_acceleration_stages[index+1].start_s if index+1<len(q.base_acceleration_stages) else q.duration_s
                duration=stop-stage.start_s;ag=stage.acceleration_m_s2;force=-ag
                equilibrium=force/omega**2;state=(x-equilibrium,v)
                times={0.,duration}
                for row in ((0.,1.),(-omega**2,-gamma),(gamma*omega**2,gamma**2-omega**2)):
                    times.update(_homogeneous_linear_event_times(row,state,omega,zeta,duration,q.maximum_event_candidates_per_oscillator-candidates))
                candidates+=len(times)
                if candidates>q.maximum_event_candidates_per_oscillator:raise ValueError('continuous-extrema event budget exceeded')
                model=HarmonicOscillator(1.,omega**2,gamma,abs(force),0.,math.pi if force<0 else 0.)
                samples=sample_response(model,tuple(sorted(times)),initial_displacement_m=x,initial_velocity_m_s=v)
                for sample in samples:
                    values=dict(relative_displacement_m=sample.displacement_m,relative_velocity_m_s=sample.velocity_m_s,
                        relative_acceleration_m_s2=sample.acceleration_m_s2,absolute_acceleration_m_s2=sample.acceleration_m_s2+ag)
                    for name,value in values.items():
                        if abs(value)>peaks[name]['value']:
                            peaks[name]=dict(value=abs(value),time_s=stage.start_s+sample.time_s,signed_value=value)
                x=samples[-1].displacement_m;v=samples[-1].velocity_m_s
            sd=peaks['relative_displacement_m']['value']
            rows.append(dict(natural_frequency_hz=frequency,damping_ratio=zeta,peaks=peaks,pseudo_velocity_m_s=omega*sd,
                pseudo_acceleration_m_s2=omega**2*sd,event_and_boundary_candidates_evaluated=candidates,
                final_relative_displacement_m=x,final_relative_velocity_m_s=v))
    return dict(status='computed',spectral_points=tuple(rows),
        acceleration_clock_interpolation_and_relative_coordinate_basis_id=q.acceleration_clock_interpolation_and_relative_coordinate_basis_id,
        extrema_scope='All analytic derivative-zero events and both boundary sides for the declared held signal and finite time interval',
        measured_interpolation_future_tail_nonlinear_or_multimode_response_inferred=False)
