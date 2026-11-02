"""Gravity-energy period quadrature and independent pseudo-acceleration identity."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.pendulum_spectral_coordinate import PendulumSpectralRequest as Request,evaluate_pendulum_spectral_coordinate as evaluate
def request(length=2.,gravity=9.81,acceleration=.1,multiplier=1.3):
    return Request(suspension_length_m=length,gravity_m_s2=gravity,spectral_acceleration_at_natural_frequency_m_s2=acceleration,
        supplied_spectral_displacement_multiplier=multiplier,maximum_reviewed_small_angle_rad=.1,
        spectrum_ordinate_frequency_damping_and_small_angle_reviewed=True)

@pytest.mark.parametrize('length,gravity',[(.5,9.81),(5.,9.81),(2.,3.7)])
def test_frequency_is_small_angle_limit_of_gravity_energy_period(length,gravity):
    r=evaluate(request(length,gravity));periods=[]
    # Energy integral after sin(theta/2)=sin(theta0/2)*sin(phi).
    for amplitude in (.2,.1,.05):
        period=4*math.sqrt(length/gravity)*quad(lambda phi:1/math.sqrt(1-math.sin(amplitude/2)**2*math.sin(phi)**2),0,math.pi/2)[0]
        periods.append(abs(period*r['natural_frequency_hz']-1))
    assert periods[2]<periods[1]<periods[0]
    assert periods[-1]<.00016

@pytest.mark.parametrize('length,multiplier',[(.5,.8),(2.,1.),(5.,1.3),(3.,2.)])
def test_spectral_pseudo_acceleration_and_gravity_work(length,multiplier):
    r=evaluate(request(length=length,multiplier=multiplier))
    assert r['status']=='computed'
    x=r['linear_spectral_displacement_m'];omega=r['natural_angular_frequency_rad_s']
    assert omega**2*x==pytest.approx(multiplier*.1,rel=2e-14)
    # Unit mass: work by linear gravity restoring force equals oscillator energy.
    assert quad(lambda s:9.81*s/length,0,x)[0]==pytest.approx(.5*omega**2*x*x,rel=2e-14)

def test_unknown_spectrum_not_zero_and_review_required():
    assert evaluate(request().model_copy(update={'spectral_acceleration_at_natural_frequency_m_s2':None}))['status']=='needs_data'
    assert evaluate(request().model_copy(update={'spectrum_ordinate_frequency_damping_and_small_angle_reviewed':False}))['status']=='needs_data'

def test_excessive_linear_angle_withheld():
    assert evaluate(request(acceleration=5.))['status']=='out_of_scope'

def test_zero_spectrum_preserves_natural_frequency():
    r=evaluate(request(acceleration=0.))
    assert r['status']=='computed' and r['linear_spectral_displacement_m']==0
