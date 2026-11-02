"""Time-path integration of the declared reference-length thermal strain law."""
import math
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.models.thermal import Expansion,expansion


@pytest.mark.parametrize('length,alpha,duration,delta',[(3.,12e-6,17.,200.),(900.,17e-6,630.,-110.),(.02,0.,.3,25.)])
def test_nonlinear_temperature_history_ode(length,alpha,duration,delta):
    # A smooth nonlinear temperature ramp drives the reference-length strain law.
    # It is not the logarithmic/current-length expansion convention.
    tref=400.
    derivative=lambda t:delta/duration*(1.-math.cos(2*math.pi*t/duration))
    sol=solve_ivp(lambda t,y:[length*alpha*derivative(t)],(0,duration),[length],
        method='DOP853',rtol=2e-12,atol=max(1e-14,length*1e-13))
    assert sol.success
    r=expansion(Expansion(reference_length_m=length,linear_expansion_per_k=alpha,
        reference_temperature_k=tref,operating_temperature_k=tref+delta))
    assert r.values['operating_length_m']==pytest.approx(sol.y[0,-1],rel=3e-12,abs=2e-13)


@pytest.mark.parametrize('direction',[-1,1])
def test_reference_strain_integral_not_multiplicative_reanchoring(direction):
    length=123.;alpha=22e-6;tref=400.;top=tref+direction*99.
    normalized_change=quad(lambda t:alpha,tref,top)[0]
    r=expansion(Expansion(reference_length_m=length,linear_expansion_per_k=alpha,
        reference_temperature_k=tref,operating_temperature_k=top))
    assert (r.values['operating_length_m']/length-1.)==pytest.approx(normalized_change,rel=1e-12)
