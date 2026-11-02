"""Independent ideal nozzle, characteristic matching and finite-inventory clocks."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.gas_acoustic_pipe import IdealGasInternalEnergyEOS,gas_conservative_state,GasAcousticPipeSettings,GasPipeBoundary
from pipeline_integrity.gas_restricted_vent import ideal_vent_capacity,evaluate_restricted_ideal_vent_face,RestrictedVentBlowdownSpec,solve_ideal_restricted_pipe_blowdown


def flux(p,t,pb,k=1.4,R=287.05):
    r=pb/p;critical=(2/(k+1))**(k/(k-1))
    if r>=1:return 0.
    factor=k*(2/(k+1))**((k+1)/(k-1)) if r<=critical else 2*k/(k-1)*(r**(2/k)-r**((k+1)/k))
    return p/math.sqrt(R*t)*math.sqrt(factor)


@pytest.mark.parametrize('gamma',[1.2,1.4,1.67])
@pytest.mark.parametrize('ratio',[.1,.7,.95])
def test_existing_relief_shape_normalizes_to_independent_SI_nozzle_capacity(gamma,ratio):
    eos=IdealGasInternalEnergyEOS(gamma=gamma);area=1.7e-4;cd=.73
    m,regime,raw,correction=ideal_vent_capacity(stagnation_pressure_pa_abs=4e5,stagnation_temperature_K=320.,ambient_pressure_pa_abs=4e5*ratio,vent_area_m2=area,discharge_coefficient=cd,eos=eos)
    assert m==pytest.approx(area*cd*flux(4e5,320.,4e5*ratio,gamma),rel=3e-13)
    assert abs(correction-1)<.001
    assert m==raw*correction
    critical=(2/(gamma+1))**(gamma/(gamma-1))
    rates=[ideal_vent_capacity(stagnation_pressure_pa_abs=4e5,stagnation_temperature_K=320.,ambient_pressure_pa_abs=4e5*critical*(1+d),vent_area_m2=area,discharge_coefficient=cd,eos=eos)[0] for d in (-1e-7,1e-7)]
    assert abs(rates[1]/rates[0]-1)<1e-12


@pytest.mark.parametrize('normal',[-1,1])
@pytest.mark.parametrize('ambient',[1e5,3.5e5])
def test_pipe_face_independently_matches_acoustic_invariant_and_nozzle(normal,ambient):
    eos=IdealGasInternalEnergyEOS();rho=4e5/(287.05*310.);velocity=normal*2.
    state=gas_conservative_state(rho,velocity,eos.internal_energy_from_temperature(310.))
    r=evaluate_restricted_ideal_vent_face(state,eos,pipe_area_m2=.01,vent_area_m2=.0004,discharge_coefficient=.8,ambient_pressure_pa_abs=ambient,outward_normal=normal)
    f=r.pipe_face;ai=math.sqrt(1.4*287.05*310.)
    assert f.outward_velocity_m_s+2*f.sound_speed_m_s/.4==pytest.approx(2.+2*ai/.4,rel=3e-15)
    assert f.pressure_pa_abs/f.density_kg_m3**1.4==pytest.approx(4e5/rho**1.4,rel=3e-15)
    independent=.0004*.8*flux(r.stagnation_pressure_pa_abs,r.stagnation_temperature_K,ambient)
    assert r.mass_flow_kg_s==pytest.approx(independent,rel=2e-9)
    assert r.mass_flow_kg_s==pytest.approx(.01*f.density_kg_m3*f.outward_velocity_m_s,rel=2e-15)
    assert f.outward_mach_number<.1


def lumped_time(p0,target,pb,area,cd,length=1.,diameter=.1,T0=310.):
    k=1.4;R=287.05;volume=math.pi*diameter**2/4*length;mass=p0*volume/(R*T0)
    xf=(target/p0)**(1/k);critical=(2/(k+1))**(k/(k-1));xc=(pb/(p0*critical))**(1/k)
    def integrand(x):return mass/(area*cd*flux(p0*x**k,T0*x**(k-1),pb))
    return quad(integrand,xf,1.,points=[xc] if xf<xc<1. else None,epsabs=1e-12,epsrel=3e-11)[0]


def run(p0=4e5,target=2.3e5,area_fraction=.02,duration=None,opening=0.,reference=0.,observation_times=()):
    eos=IdealGasInternalEnergyEOS(internal_energy_reference_J_kg=reference)
    area=math.pi*.1**2/4;vent_area=area*area_fraction;cd=.8;pb=1e5
    exact=lumped_time(p0,target,pb,vent_area,cd)
    q=GasAcousticPipeSettings(length_m=1.,inner_diameter_m=.1,duration_s=duration or (2*exact+opening),
        maximum_step_s=.01,pressure_stop_tolerance_pa=.02,observation_times_s=observation_times)
    vent=RestrictedVentBlowdownSpec(geometric_vent_area_m2=vent_area,discharge_coefficient=cd,ambient_pressure_pa_abs=pb,
        target_maximum_pipe_pressure_pa_abs=target,quasisteady_ideal_small_vent_and_one_way_support_reviewed=True)
    state=gas_conservative_state(np.full(4,p0/(287.05*310.)),0.,eos.internal_energy_from_temperature(310.))
    return solve_ideal_restricted_pipe_blowdown(state,q,eos,vent,opening_time_s=opening),exact


def budgets(r):
    l=r.core.ledger
    scale=np.maximum(1.,abs(l.initial_mass_momentum_energy)+abs(l.final_mass_momentum_energy)+abs(l.integrated_boundary_inflow)+abs(l.integrated_distributed_sources))
    assert np.max(abs(l.residual)/scale)<5e-13
    assert r.discharged_mass_kg==pytest.approx(-l.integrated_boundary_inflow[0],abs=2e-14)
    assert r.remaining_mass_kg>0


@pytest.mark.parametrize('p0,target',[(4e5,2.3e5),(2.5e5,1.15e5)])
def test_finite_pipe_pressure_endpoint_against_independent_adiabatic_inventory_limit(p0,target):
    errors=[]
    for fraction in (.04,.02):
        r,exact=run(p0,target,fraction)
        assert r.target_reached
        assert target-.02<=np.max(r.core.pressure_pa_abs)<=target
        assert r.elapsed_stop_time_s<r.core.termination['requested_finish_time_s']
        errors.append(abs(r.elapsed_stop_time_s/exact-1))
        budgets(r)
        assert r.final_vent_face.throat_regime==('choked' if target==2.3e5 else 'subcritical')
    assert errors[1]<.8*errors[0]
    assert errors[1]<.012


def test_opening_event_and_explicit_finite_horizon_are_not_time_to_empty():
    r,exact=run(opening=.003,observation_times=(0.,.003))
    assert r.core.boundary_event_times_s==(.003,)
    assert tuple(s.time_s for s in r.core.observations)==(0.,.003)
    assert r.elapsed_stop_time_s==pytest.approx(exact+.003,rel=.012)
    np.testing.assert_allclose(r.core.observations[0].conservative,r.core.observations[1].conservative,rtol=0,atol=1e-12)
    budgets(r)
    short,_=run(duration=.001)
    assert not short.target_reached
    assert short.core.termination['reason']=='duration_limit'
    assert short.elapsed_stop_time_s==.001
    budgets(short)


def test_negative_energy_reference_keeps_mass_pressure_endpoint_and_balances():
    ordinary,_=run();shifted,_=run(reference=-1e6)
    assert shifted.elapsed_stop_time_s==pytest.approx(ordinary.elapsed_stop_time_s,rel=2e-11)
    np.testing.assert_allclose(shifted.core.pressure_pa_abs,ordinary.core.pressure_pa_abs,rtol=3e-13)
    assert shifted.remaining_mass_kg==pytest.approx(ordinary.remaining_mass_kg,rel=3e-13)
    budgets(shifted)


@pytest.mark.parametrize('failure',['area','mach','review','endpoint','unknown','unsupported_eos'])
def test_unsupported_nozzle_or_stop_semantics_are_rejected(failure):
    eos=IdealGasInternalEnergyEOS();state=gas_conservative_state(4.,0.,eos.internal_energy_from_temperature(310.))
    with pytest.raises(ValueError):
        if failure=='area':evaluate_restricted_ideal_vent_face(state,eos,pipe_area_m2=.01,vent_area_m2=.003,discharge_coefficient=.8,ambient_pressure_pa_abs=1e5)
        elif failure=='mach':evaluate_restricted_ideal_vent_face(gas_conservative_state(4.,200.,eos.internal_energy_from_temperature(310.)),eos,pipe_area_m2=.01,vent_area_m2=.0001,discharge_coefficient=.8,ambient_pressure_pa_abs=1e5)
        elif failure=='review':GasPipeBoundary(kind='restricted_ideal_vent',ambient_pressure_pa_abs=1e5,vent_area_m2=.0001,vent_discharge_coefficient=.8)
        elif failure=='endpoint':RestrictedVentBlowdownSpec(geometric_vent_area_m2=.0001,discharge_coefficient=.8,ambient_pressure_pa_abs=1e5,target_maximum_pipe_pressure_pa_abs=1e5)
        elif failure=='unknown':GasPipeBoundary.model_validate(dict(kind='pressure_outflow',ambient_pressure_pa_abs=1e5,invented_terminal_time=30.))
        else:ideal_vent_capacity(stagnation_pressure_pa_abs=4e5,stagnation_temperature_K=300.,ambient_pressure_pa_abs=1e5,vent_area_m2=.0001,discharge_coefficient=.8,eos=object())
