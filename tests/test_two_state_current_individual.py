"""Actual piecewise waveform square integral and resistor heat references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.two_state_current import TwoStateCurrentRequest,observe_two_state_current


def run(**changes):
    q=dict(active_state_rms_current_A=50.,idle_state_rms_current_A=10.,active_time_fraction=.3,
        cycle_period_s=20.,cycle_clock_id='cycle',constant_resistance_ohm=.25,
        two_state_rms_and_time_fraction_basis_reviewed=True)
    return observe_two_state_current(TwoStateCurrentRequest(**(q|changes)))


@pytest.mark.parametrize('active,idle,duty,period',[(50.,10.,.3,20.),(80.,5.,.7,3.),(4.,9.,.4,2.),(0.,7.,.2,8.)])
def test_independent_sinusoidal_state_waveforms_and_cycle_joule_heat(active,idle,duty,period):
    split=duty*period
    def current(t):
        if t<split:return math.sqrt(2)*active*math.sin(2*math.pi*3*t/split)
        return math.sqrt(2)*idle*math.sin(2*math.pi*5*(t-split)/(period-split))
    integral=quad(lambda t:current(t)**2,0,split,epsabs=1e-9)[0]+quad(lambda t:current(t)**2,split,period,epsabs=1e-9)[0]
    r=run(active_state_rms_current_A=active,idle_state_rms_current_A=idle,active_time_fraction=duty,cycle_period_s=period)
    assert r.status=='computed'
    assert r.cycle_current_squared_integral_A2_s==pytest.approx(integral,rel=2e-13)
    assert r.effective_rms_current_A==pytest.approx(math.sqrt(integral/period),rel=2e-13)
    assert r.cycle_joule_energy_J==pytest.approx(.25*integral,rel=2e-13)
    assert r.mean_joule_power_W==pytest.approx(r.cycle_joule_energy_J/period)


@pytest.mark.parametrize('duty,expected',[(0.,10.),(1.,50.)])
def test_zero_duration_state_unknown_does_not_invent_a_current(duty,expected):
    changes={'active_state_rms_current_A':None} if duty==0 else {'idle_state_rms_current_A':None}
    r=run(active_time_fraction=duty,**changes)
    assert r.effective_rms_current_A==expected


def test_effective_rms_is_not_mean_magnitude():
    r=run()
    assert r.effective_rms_current_A>.3*50+.7*10


@pytest.mark.parametrize('scale',[.01,25.])
def test_time_and_current_scaling_have_separate_energy_meanings(scale):
    a=run();b=run(cycle_period_s=20*scale);c=run(active_state_rms_current_A=50*scale,idle_state_rms_current_A=10*scale)
    assert b.effective_rms_current_A==a.effective_rms_current_A
    assert b.cycle_joule_energy_J==pytest.approx(a.cycle_joule_energy_J*scale)
    assert c.effective_rms_current_A==pytest.approx(a.effective_rms_current_A*scale)
    assert c.mean_joule_power_W==pytest.approx(a.mean_joule_power_W*scale**2)


def test_zero_and_equal_currents_and_zero_resistance():
    assert run(active_state_rms_current_A=0,idle_state_rms_current_A=0).effective_rms_current_A==0
    assert run(active_state_rms_current_A=7,idle_state_rms_current_A=7).effective_rms_current_A==pytest.approx(7)
    assert run(constant_resistance_ohm=0).mean_joule_power_W==0


def test_no_period_or_resistance_preserves_unrequested_coordinates():
    a=run(cycle_period_s=None,cycle_clock_id=None,constant_resistance_ohm=None)
    assert a.status=='computed' and a.cycle_joule_energy_J is None and a.mean_joule_power_W is None
    assert a.cycle_current_squared_integral_A2_s is None


@pytest.mark.parametrize('change',[{'active_state_rms_current_A':None},{'idle_state_rms_current_A':None},
    {'active_time_fraction':None},{'two_state_rms_and_time_fraction_basis_reviewed':False}])
def test_unknown_positive_duration_data_and_review_are_not_zero(change):
    a=run(**change);assert a.status=='needs_data' and a.effective_rms_current_A is None


@pytest.mark.parametrize('change',[{'active_time_fraction':1.01},{'active_time_fraction':-.1},
    {'active_state_rms_current_A':-1},{'idle_state_rms_current_A':float('nan')},
    {'cycle_period_s':0},{'cycle_clock_id':None},{'constant_resistance_ohm':-1}])
def test_invalid_units_coordinates_and_clock_rejected(change):
    with pytest.raises(ValueError):run(**change)
