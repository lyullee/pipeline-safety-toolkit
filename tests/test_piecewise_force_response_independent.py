"""Independent pulse solution, forced ODE/event states and impulse/energy limits."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.piecewise_force_response import HeldForceStage,GeneralizedResponseRequest,evaluate_generalized_response,pressure_stages_to_generalized_force

def calc(stages=((0.,6.),(.2,0.)),times=(0.,.1,.2,1.),**kw):
    data=dict(mass_kg=2.,stiffness_n_m=18.,damping_n_s_m=0.,force_stages=tuple(HeldForceStage(start_s=t,force_n=f) for t,f in stages),
        output_times_s=tuple(float(t) for t in times),load_and_coordinate_basis_id='authored-generalized-mode',
        linear_generalized_coordinate_and_held_force_basis_reviewed=True)
    return evaluate_generalized_response(GeneralizedResponseRequest(**(data|kw)))

@pytest.mark.parametrize('duration',[.01,.2,.7])
def test_pressure_mapped_rectangular_pulse_matches_independent_turning_point(duration):
    wn=3.;force=6.;time=duration/2+math.pi/(2*wn)
    stages=pressure_stages_to_generalized_force(((0.,300.),(duration,0.)),generalized_pressure_area_m2=.02,
        pressure_mode_mapping_basis_id='supplied-mode-pressure-area',reviewed=True)
    result=calc(times=(0.,duration,time),force_stages=stages)
    peak=result.points[-1]
    expected=2*force/18*math.sin(wn*duration/2)
    assert peak.displacement_m==pytest.approx(expected,rel=2e-13,abs=2e-15)
    assert abs(peak.velocity_m_s)<1e-13
    assert result.continuous_extrema_nonlinear_shell_or_contact_qualified is False

@pytest.mark.parametrize('damping',[0.,4.,16.])
def test_signed_force_history_matches_independent_piecewise_ode(damping):
    stages=((0.,6.),(.15,-2.),(.4,0.));times=np.linspace(0.,.8,33)
    result=calc(stages,times,damping_n_s_m=damping)
    current=np.array([0.,0.]);references=[]
    bounds=[0.,.15,.4,.8]
    solutions=[]
    for (start,force),stop in zip(stages,bounds[1:],strict=True):
        ode=solve_ivp(lambda t,y:[y[1],(force-damping*y[1]-18*y[0])/2],(start,stop),current,
            method='DOP853',rtol=1e-11,atol=1e-13,dense_output=True)
        assert ode.success
        solutions.append(ode.sol);current=ode.y[:,-1]
    for point in result.points:
        segment=0 if point.time_s<.15 else 1 if point.time_s<.4 else 2
        expected=solutions[segment](point.time_s)
        assert point.displacement_m==pytest.approx(expected[0],rel=3e-10,abs=5e-12)
        assert point.velocity_m_s==pytest.approx(expected[1],rel=3e-10,abs=5e-12)

def test_output_sampling_partition_does_not_define_the_physical_integration_step():
    coarse=calc(times=(0.,.2,1.),damping_n_s_m=4.)
    dense=calc(times=np.linspace(0.,1.,101),damping_n_s_m=4.)
    assert dense.points[-1].displacement_m==pytest.approx(coarse.points[-1].displacement_m,rel=2e-12,abs=1e-13)
    assert dense.points[-1].velocity_m_s==pytest.approx(coarse.points[-1].velocity_m_s,rel=2e-12,abs=1e-13)

def test_fixed_impulse_short_pulses_converge_to_momentum_impulse_limit():
    impulse=.5;wn=3.;limit=impulse/(2*wn);errors=[]
    for duration in (.1,.03,.01,.003):
        t=duration/2+math.pi/(2*wn)
        result=calc(((0.,impulse/duration),(duration,0.)),(0.,duration,t))
        errors.append(abs(result.points[-1].displacement_m/limit-1))
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<4e-6

def test_unforced_tail_conserves_kinetic_plus_elastic_energy():
    result=calc(times=np.linspace(.2,2.2,81))
    energies=[p.velocity_m_s**2+9*p.displacement_m**2 for p in result.points]
    assert max(energies)-min(energies)<1e-12

def test_switch_state_is_continuous_and_acceleration_uses_right_force():
    result=calc(times=(0.,.2,.200000001))
    switch=result.points[1]
    assert switch.force_n==0
    assert switch.acceleration_m_s2==-9*switch.displacement_m
    assert switch.displacement_m==pytest.approx((6/18)*(1-math.cos(.6)),rel=1e-13)
    assert switch.velocity_m_s==pytest.approx((6/18)*3*math.sin(.6),rel=1e-13)

def test_unknown_force_does_not_become_zero():
    assert calc(stages=((0.,None),)).status=='needs_data'

def test_pressure_mapping_does_not_infer_area_or_unreviewed_modal_reduction():
    with pytest.raises(ValueError):pressure_stages_to_generalized_force(((0.,1.),),generalized_pressure_area_m2=.1,pressure_mode_mapping_basis_id='mode',reviewed=False)

def test_zero_forcing_preserves_declared_initial_condition_free_motion():
    result=calc(stages=((0.,0.),),times=(0.,.1,.7),initial_displacement_m=.001,initial_velocity_m_s=.002)
    final=result.points[-1]
    assert final.displacement_m==pytest.approx(.001*math.cos(2.1)+(.002/3)*math.sin(2.1),rel=2e-14)
