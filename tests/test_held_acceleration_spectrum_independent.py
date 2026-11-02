"""Pulse closed forms and independent adaptive ODE event maxima."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.held_acceleration_spectrum import BaseAccelerationStage as Stage,HeldAccelerationSpectrumRequest as Request,evaluate_held_acceleration_spectrum as evaluate

def request(stages,duration,frequency=1.,damping=0.):
    return Request(natural_frequencies_hz=(frequency,),damping_ratios=(damping,),base_acceleration_stages=tuple(Stage(start_s=t,acceleration_m_s2=a) for t,a in stages),
        duration_s=duration,acceleration_clock_interpolation_and_relative_coordinate_basis_id='authored-held-ground-acceleration',known_zero_order_hold_and_linear_relative_coordinate_reviewed=True)

@pytest.mark.parametrize('frequency',[.5,2.])
@pytest.mark.parametrize('fraction',[.01,.1,.4])
def test_rectangular_base_acceleration_spectrum_matches_independent_impulse_state_rotation(frequency,fraction):
    omega=2*math.pi*frequency;tau=fraction/frequency;ag=2.
    result=evaluate(request(((0.,ag),(tau,0.)),3/frequency,frequency))['spectral_points'][0]
    sine=math.sin(omega*tau/2);sd=2*ag/omega**2*sine;sv=2*ag/omega*sine;sa=2*ag*sine
    assert result['peaks']['relative_displacement_m']['value']==pytest.approx(sd,rel=2e-11)
    assert result['peaks']['relative_velocity_m_s']['value']==pytest.approx(sv,rel=2e-11)
    assert result['peaks']['absolute_acceleration_m_s2']['value']==pytest.approx(sa,rel=2e-11)
    assert result['peaks']['relative_acceleration_m_s2']['value']==pytest.approx(max(ag,sa),rel=2e-11)
    assert result['pseudo_acceleration_m_s2']==pytest.approx(sa,rel=2e-11)

@pytest.mark.parametrize('zeta',[.05,1.,2.])
def test_damped_signed_stage_spectrum_matches_independent_dop853_event_roots(zeta):
    stages=((0.,2.),(.13,-.8),(.41,0.));duration=3.;frequency=1.3;omega=2*math.pi*frequency;gamma=2*zeta*omega
    result=evaluate(request(stages,duration,frequency,zeta))['spectral_points'][0]
    values={name:[] for name in ('relative_displacement_m','relative_velocity_m_s','relative_acceleration_m_s2','absolute_acceleration_m_s2')}
    state=np.zeros(2)
    for i,(start,ag) in enumerate(stages):
        stop=stages[i+1][0] if i+1<len(stages) else duration
        def acc(y):return -omega**2*y[0]-gamma*y[1]-ag
        def velocity_event(t,y):return y[1]
        def acceleration_event(t,y):return acc(y)
        def jerk_event(t,y):return -omega**2*y[1]-gamma*acc(y)
        sol=solve_ivp(lambda t,y:[y[1],acc(y)],(start,stop),state,method='DOP853',rtol=2e-12,atol=2e-14,
            max_step=1/(frequency*60),events=(velocity_event,acceleration_event,jerk_event),dense_output=True)
        assert sol.success
        times=[start,stop,*[v for roots in sol.t_events for v in roots]]
        for time in times:
            x,v=sol.sol(time);a=acc((x,v))
            for name,value in zip(values,(x,v,a,a+ag)):values[name].append(abs(value))
        state=sol.y[:,-1]
    for name,references in values.items():assert result['peaks'][name]['value']==pytest.approx(max(references),rel=3e-9,abs=1e-11)

def test_held_triangular_signal_refinement_converges_to_independent_duhamel_integral():
    omega=2*math.pi;duration=.8;finish=1.3;errors=[]
    def acceleration(t):return 2*t/duration if t<duration/2 else 2*(1-t/duration)
    reference=-quad(lambda t:acceleration(t)*math.sin(omega*(finish-t))/omega,0,duration,points=[duration/2],epsabs=1e-13)[0]
    # Left-held input is first-order in the waveform discretization; retain
    # the absolute error requirement and refine the supplied signal itself.
    for cells in (256,512,1024):
        h=duration/cells;stages=tuple((i*h,acceleration(i*h)) for i in range(cells))+((duration,0.),)
        row=evaluate(request(stages,finish))['spectral_points'][0]
        errors.append(abs(row['final_relative_displacement_m']-reference))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<1e-4

def test_unknown_signal_is_not_zero_filled():
    q=request(((0.,None),),1.)
    assert evaluate(q)['status']=='needs_data'
