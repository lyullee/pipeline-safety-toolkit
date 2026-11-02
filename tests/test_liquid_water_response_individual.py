"""Density derivatives, caloric identity and volume paths independent of beta output."""
import math

import pytest
from CoolProp.CoolProp import PropsSI
from scipy.integrate import quad
from scipy.optimize import minimize_scalar

from pipeline_integrity.liquid_water_response import (
    LiquidWaterPT, LiquidWaterResponseRequest, observe_liquid_water_response)


def run(p=1e5, t=300., ref=None, mass=None):
    return observe_liquid_water_response(LiquidWaterResponseRequest(
        state=LiquidWaterPT(pressure_Pa_abs=p,temperature_K=t),
        reference_state=LiquidWaterPT(pressure_Pa_abs=ref[0],temperature_K=ref[1]) if ref else None,
        fixed_mass_kg=mass))


def rho(p,t):
    return PropsSI('Dmass','P',p,'T',t,'HEOS::Water')


@pytest.mark.parametrize('p,t',[(1e5,274.),(1e5,277.),(1e5,278.),(1e5,300.),
    (2e7,275.),(2e7,320.),(2e7,500.),(1e8,350.)])
def test_beta_and_kappa_from_independent_five_point_density_derivatives(p,t):
    a=run(p,t);assert a.status=='computed'
    dt=.01;dp=max(10.,p*1e-5)
    dT=(rho(p,t-2*dt)-8*rho(p,t-dt)+8*rho(p,t+dt)-rho(p,t+2*dt))/(12*dt)
    dP=(rho(p-2*dp,t)-8*rho(p-dp,t)+8*rho(p+dp,t)-rho(p+2*dp,t))/(12*dp)
    assert a.isobaric_volumetric_expansion_K_inv==pytest.approx(-dT/rho(p,t),rel=3e-6,abs=3e-11)
    assert a.isothermal_compressibility_Pa_inv==pytest.approx(dP/rho(p,t),rel=3e-5)


@pytest.mark.parametrize('p,t',[(1e5,274.),(1e5,300.),(2e7,320.),(2e7,500.)])
def test_caloric_identity_cp_minus_cv_from_PT_density_slopes(p,t):
    a=run(p,t);h=.015;dp=200.
    dT=(rho(p,t+h)-rho(p,t-h))/(2*h)
    dP=(rho(p+dp,t)-rho(p-dp,t))/(2*dp)
    # cp-cv=T*beta^2/(rho*kappa_T), derived entirely from PT density slopes.
    expect=t*dT*dT/(rho(p,t)**2*dP)
    assert a.cp_J_kg_K-a.cv_J_kg_K==pytest.approx(expect,rel=3e-5,abs=1e-5)


def test_water_anomaly_has_negative_zero_positive_signed_beta():
    peak=minimize_scalar(lambda t:-rho(1e5,t),bounds=(275.,280.),method='bounded',
                         options={'xatol':1e-9}).x
    assert 276.<peak<278.
    assert run(t=274.).isobaric_volumetric_expansion_K_inv<0
    assert abs(run(t=peak).isobaric_volumetric_expansion_K_inv)<2e-10
    assert run(t=280.).isobaric_volumetric_expansion_K_inv>0


@pytest.mark.parametrize('p,t0,t1',[(1e5,274.,280.),(1e5,280.,340.),(2e7,280.,500.)])
def test_fixed_mass_ratio_and_isobaric_beta_path_integral(p,t0,t1):
    a=run(p,t1,(p,t0),mass=15.25)
    integral=quad(lambda t:run(p,t).isobaric_volumetric_expansion_K_inv,t0,t1,
                  epsabs=1e-11,epsrel=1e-9)[0]
    assert a.specific_volume_over_reference==pytest.approx(math.exp(integral),rel=2e-10)
    assert a.volume_m3*a.density_kg_m3==pytest.approx(15.25,rel=1e-14)
    assert a.reference_volume_m3*a.reference_density_kg_m3==pytest.approx(15.25,rel=1e-14)
    assert a.volume_m3/a.reference_volume_m3==pytest.approx(rho(p,t0)/rho(p,t1),rel=1e-14)


def test_pressure_path_integral_and_two_leg_state_path():
    p0,p1,t0,t1=1e5,2e7,290.,350.
    thermal=quad(lambda t:run(p0,t).isobaric_volumetric_expansion_K_inv,t0,t1,epsabs=1e-11)[0]
    pressure=quad(lambda p:run(p,t1).isothermal_compressibility_Pa_inv,p0,p1,epsabs=1e-11)[0]
    actual=run(p1,t1,(p0,t0)).specific_volume_over_reference
    assert actual==pytest.approx(math.exp(thermal-pressure),rel=2e-9)


def test_reference_state_changes_ratio_without_changing_local_response():
    a=run(ref=(1e5,280.));b=run(ref=(2e7,330.));c=run(ref=(1e5,300.))
    assert a.isobaric_volumetric_expansion_K_inv==b.isobaric_volumetric_expansion_K_inv
    assert a.isothermal_compressibility_Pa_inv==b.isothermal_compressibility_Pa_inv
    assert a.specific_volume_over_reference!=b.specific_volume_over_reference
    assert c.specific_volume_over_reference==1


@pytest.mark.parametrize('mass',[0.,1.,1250.])
def test_extensive_mass_scaling_and_absent_reference(mass):
    a=run(mass=mass)
    assert a.volume_m3==pytest.approx(mass*a.specific_volume_m3_kg)
    assert a.reference_volume_m3 is None and a.specific_volume_over_reference is None


@pytest.mark.parametrize('p,t',[(1e5,250.),(1e5,273.15),(1e5,647.096),(1e5,700.),
    (1e5,400.),(100.,300.),(1e10,300.)])
def test_ice_supercritical_vapor_or_unsupported_pressure_is_not_liquid_response(p,t):
    a=run(p,t)
    assert a.status=='out_of_scope'
    assert a.density_kg_m3 is None and a.isobaric_volumetric_expansion_K_inv is None


def test_invalid_reference_not_silently_replaced_by_current_state():
    a=run(ref=(1e5,500.))
    assert a.status=='out_of_scope' and a.specific_volume_over_reference is None


@pytest.mark.parametrize('bad',[{'pressure_Pa_abs':0,'temperature_K':300},
    {'pressure_Pa_abs':1e5,'temperature_K':-10},
    {'pressure_Pa_abs':1e5,'temperature_K':float('nan')},
    {'pressure_Pa_abs':1e5,'temperature_K':300,'pressure_reference':'gauge'},
    {'pressure_Pa_abs':1e5,'temperature_K':300,'fluid':'mixture'}])
def test_absolute_finite_coordinates_and_unknown_fields_rejected(bad):
    with pytest.raises(ValueError):LiquidWaterPT(**bad)


def test_native_unknown_or_nonfinite_property_not_reported_computed(monkeypatch):
    import CoolProp.CoolProp as cp
    original=cp.PropsSI
    def unknown(key,*args):
        if key=='isobaric_expansion_coefficient':return float('nan')
        return original(key,*args)
    monkeypatch.setattr(cp,'PropsSI',unknown)
    a=run()
    assert a.status=='out_of_scope' and a.isobaric_volumetric_expansion_K_inv is None


def test_enthalpy_entropy_reference_shift_does_not_change_response():
    import CoolProp.CoolProp as cp
    a=run(ref=(2e7,320.),mass=2.)
    original_h=cp.PropsSI('Hmolar','T',300.,'P',1e5,'HEOS::Water')
    original_s=cp.PropsSI('Smolar','T',300.,'P',1e5,'HEOS::Water')
    molar_density=cp.PropsSI('Dmolar','T',300.,'P',1e5,'HEOS::Water')
    try:
        cp.set_reference_state('Water',300.,molar_density,original_h+12345.,original_s+31.)
        b=run(ref=(2e7,320.),mass=2.)
        for field in ['density_kg_m3','specific_volume_over_reference',
            'isobaric_volumetric_expansion_K_inv','isothermal_compressibility_Pa_inv','cp_J_kg_K','cv_J_kg_K']:
            assert getattr(a,field)==pytest.approx(getattr(b,field),rel=1e-12)
    finally:
        cp.set_reference_state('Water',300.,molar_density,original_h,original_s)
