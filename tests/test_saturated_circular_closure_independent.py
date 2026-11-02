"""Independent hydrostatic density ODE and pressure/area differential identities."""
import math
import numpy as np
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.saturated_circular_closure import SaturatedCircularClosure
from pipeline_integrity.gravity_free_surface_pipe import circular_section_from_depth


def closure(c=80.):return SaturatedCircularClosure(.5,1000.,2000.,c,9.80665)


@pytest.mark.parametrize('head',[.02,.2,.49,.5,.8])
def test_column_mass_and_pressure_against_independent_vertical_density_ode(head):
    c=closure();top=min(head,.5);initial_density=1000.*math.exp(9.80665*(head-top)/80.**2)
    ode=solve_ivp(lambda y,rho:[-rho[0]*9.80665/80.**2],(top,0.),[initial_density],dense_output=True,rtol=2e-13,atol=1e-9)
    width=lambda y:2*math.sqrt(max(0.,y*(.5-y)))
    area=quad(lambda y:ode.sol(y)[0]/1000.*width(y),0,top,epsabs=2e-12,epsrel=2e-12)[0]
    pressure=quad(lambda y:80.**2*(ode.sol(y)[0]/1000.-1)*width(y),0,top,epsabs=2e-11,epsrel=1e-10)[0]
    state=c.at_head(np.array([head]))
    assert state.area_m2[0]==pytest.approx(area,rel=2e-11)
    assert state.pressure_potential_m4_s2[0]==pytest.approx(pressure,rel=3e-8,abs=3e-11)
    recovered=c.from_area(state.area_m2)
    assert recovered.depth_m[0]==pytest.approx(head,rel=3e-10,abs=2e-12)
    assert np.array_equal(recovered.area_m2,state.area_m2)


@pytest.mark.parametrize('head',[.05,.25,.45,.7])
def test_independent_pressure_area_and_head_finite_difference_identities(head):
    c=closure();step=1e-6
    a,b=c.at_head(np.array([head-step])),c.at_head(np.array([head+step]))
    state=c.at_head(np.array([head]))
    dadh=(b.area_m2[0]-a.area_m2[0])/(2*step)
    dpdh=(b.pressure_potential_m4_s2[0]-a.pressure_potential_m4_s2[0])/(2*step)
    dpda=(b.pressure_potential_m4_s2[0]-a.pressure_potential_m4_s2[0])/(b.area_m2[0]-a.area_m2[0])
    assert dpdh==pytest.approx(9.80665*state.area_m2[0],rel=2e-7)
    assert dadh==pytest.approx(state.storage_derivative_width_m[0],rel=2e-7)
    assert dpda==pytest.approx(state.wave_speed_m_s[0]**2,rel=2e-7)


def test_full_matching_derivative_is_finite_and_continuous():
    c=closure();h=np.array([np.nextafter(.5,0.),.5,np.nextafter(.5,1.)]);s=c.at_head(h)
    assert s.wave_speed_m_s==pytest.approx([80.,80.,80.],rel=6e-5)
    assert np.all(s.wave_speed_m_s<=80.*(1+2e-15))
    assert s.pressure_potential_m4_s2==pytest.approx([c.full_pressure_potential]*3,rel=5e-12)
    assert s.area_m2==pytest.approx([c.full_effective_area]*3,rel=2e-15)


def test_incompressible_sound_speed_limit_reuses_original_circular_hydrostatics():
    c=closure(50000.);h=np.array([.03,.2,.42]);a=c.at_head(h);old=circular_section_from_depth(h,.5)
    assert a.area_m2==pytest.approx(old.area_m2,rel=2e-9)
    assert a.hydrostatic_integral_m3==pytest.approx(old.hydrostatic_integral_m3,rel=2e-9)
    assert a.first_height_moment_m3==pytest.approx(old.first_height_moment_m3,rel=2e-9)
    assert a.wave_speed_m_s==pytest.approx(np.sqrt(9.80665*old.area_m2/old.top_width_m),rel=2e-9)


def test_full_liquid_negative_gauge_is_distinct_from_saturated_free_surface():
    c=closure(1000.);head=.5+math.log1p((50000.-2000.)/(1000.*1000.**2))/c.kappa
    s=c.at_head(np.array([head,.4]));assert not s.free_surface[0] and s.free_surface[1]
    assert s.crown_pressure_pa_abs[0]==pytest.approx(50000.,rel=3e-15)
    assert 2000.<s.crown_pressure_pa_abs[0]<101325.
    assert s.physical_depth_m[0]==.5 and s.physical_depth_m[1]==.4
    assert s.crown_pressure_pa_abs[1]==2000.


def test_barotropic_mechanical_potential_derivative_not_a_latent_energy_claim():
    c=closure();h=.3;step=1e-6
    a,b=c.at_head(np.array([h-step])),c.at_head(np.array([h+step]))
    derivative=9.80665*(b.first_height_moment_m3-a.first_height_moment_m3)/(b.area_m2-a.area_m2)
    assert derivative[0]==pytest.approx(9.80665*h,rel=1e-8)


def test_dry_or_large_density_departure_withheld_without_area_clipping():
    c=closure()
    with pytest.raises(ValueError):c.from_area(np.array([0.]))
    with pytest.raises(ValueError):c.at_head(np.array([c.maximum_head*2]))
