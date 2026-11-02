"""Reservoir energy/entropy, interior wave and actual boundary-flux references."""
import math
import numpy as np
import pytest
from pipeline_integrity.gas_acoustic_pipe import (
    IdealGasInternalEnergyEOS, gas_conservative_state, GasAcousticPipeSettings,
    GasPipeBoundary, GasPipeBoundaryStage, solve_gas_acoustic_pipe,
    evaluate_pressure_outflow_face,
)
from pipeline_integrity.gas_reservoir_boundary import evaluate_ideal_same_fluid_open_face as face

EOS=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=1.,gamma=1.4)

def state(velocity=0.,pressure=1.,rho=1.,eos=EOS):
    return gas_conservative_state(rho,velocity,pressure/(rho*(eos.gamma-1))+eos.internal_energy_reference_J_kg)

def evaluate(u,p=1.,t=1.,**kw):return face(u,EOS,p,t,same_fluid_isentropic_reservoir_reviewed=True,**kw)

@pytest.mark.parametrize('pressure',[.95,1.1])
@pytest.mark.parametrize('normal',[-1,1])
def test_manufactured_subsonic_inflow_matches_independent_hugoniot_or_rarefaction(pressure,normal):
    gamma=1.4;cp=3.5;target=-.1;temperature=.9
    if pressure>1:
        # Hugoniot density and shock speed give the independent velocity jump.
        b=(gamma-1)/(gamma+1);rstar=(pressure+b)/(b*pressure+1)
        delta=math.sqrt((pressure-1)*(1-1/rstar))
    else:delta=2*math.sqrt(gamma)/(gamma-1)*(pressure**((gamma-1)/(2*gamma))-1)
    vi=target+delta
    t0=temperature+target**2/(2*cp)
    p0=pressure*(t0/temperature)**(gamma/(gamma-1))
    r=evaluate(state(normal*vi),p0,t0,outward_normal=normal)
    assert r.outward_velocity_m_s==pytest.approx(target,abs=2e-12)
    assert r.pressure_pa_abs==pytest.approx(pressure,abs=2e-12)
    assert r.temperature_K==pytest.approx(temperature,abs=2e-12)
    assert r.pressure_pa_abs/r.density_kg_m3**gamma==pytest.approx(p0/(p0/t0)**gamma,rel=2e-12)
    assert cp*r.temperature_K+.5*r.outward_velocity_m_s**2==pytest.approx(cp*t0,rel=2e-13)

@pytest.mark.parametrize('p0',[.1,.95])
def test_expansion_and_sonic_outflow_reuse_existing_owner(p0):
    u=state(.1)
    r=evaluate(u,p0,2.)
    old=evaluate_pressure_outflow_face(u,EOS,p0)
    assert r==old

def test_compression_outflow_obeys_rankine_hugoniot_and_entropy_increase():
    pi=1.;rho=1.;vi=.5;p0=1.1
    r=evaluate(state(vi),p0,1.)
    dv=vi-r.outward_velocity_m_s
    shock_speed=vi-(p0-pi)/(rho*dv)
    # Both sides of the traveling shock carry the same mass/momentum/energy flux.
    wi=vi-shock_speed;wb=r.outward_velocity_m_s-shock_speed
    assert rho*wi==pytest.approx(r.density_kg_m3*wb,rel=2e-12)
    assert pi+rho*wi**2==pytest.approx(p0+r.density_kg_m3*wb**2,rel=2e-12)
    assert 3.5+.5*wi**2==pytest.approx(3.5*r.temperature_K+.5*wb**2,rel=2e-12)
    assert p0/r.density_kg_m3**1.4>pi

def test_equal_pressure_rest_and_signed_inflow_transition():
    rest=evaluate(state(),1.,3.)
    assert rest.outward_velocity_m_s==0
    assert rest.temperature_K==1. # Incoming reservoir T must not overwrite resting interior.
    assert evaluate(state(),1.001,1.).outward_velocity_m_s<0
    assert evaluate(state(),.999,1.).outward_velocity_m_s>0

@pytest.mark.parametrize('velocity',[-1e-10,1e-10])
def test_finite_signed_velocity_is_resolved_arbitrarily_close_to_reversal(velocity):
    r=evaluate(state(velocity),1.,1.)
    assert r.outward_velocity_m_s==pytest.approx(velocity,abs=2e-14)

def test_inflow_branch_residual_is_strictly_monotone_without_extra_squared_roots():
    p0=1.01;t0=1.1;gamma=1.4;cp=3.5
    velocities=np.linspace(-math.sqrt(2*(gamma-1)*cp*t0/(gamma+1)),0.,2001)
    pressures=p0*(1-velocities**2/(2*cp*t0))**(gamma/(gamma-1))
    b=(gamma-1)/(gamma+1)
    curve=np.where(pressures>1.,(pressures-1)*np.sqrt(2/(gamma+1)/(pressures+b)),
        2*math.sqrt(gamma)/(gamma-1)*(pressures**((gamma-1)/(2*gamma))-1))
    residual=velocities+curve
    assert np.all(np.diff(residual)>0)
    assert residual[0]<0<residual[-1]

@pytest.mark.parametrize('reference',[-100.,100.])
def test_reservoir_stagnation_energy_respects_declared_internal_reference(reference):
    eos=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=1.,gamma=1.4,internal_energy_reference_J_kg=reference)
    r=face(state(eos=eos),eos,1.01,1.1,same_fluid_isentropic_reservoir_reviewed=True)
    h0=r.internal_energy_J_kg+r.pressure_pa_abs/r.density_kg_m3+.5*r.outward_velocity_m_s**2
    assert h0==pytest.approx(reference+3.5*1.1,rel=1e-12)

def test_supersonic_and_reservoir_choked_inflow_are_not_silently_imposed():
    with pytest.raises(ValueError,match='supersonic interior inflow'):evaluate(state(-2.))
    with pytest.raises(ValueError,match='reservoir-choked inflow'):evaluate(state(pressure=.01,rho=.01),1.,1.)

@pytest.mark.parametrize('p,t',[(0.,1.),(1.,0.),(float('nan'),1.)])
def test_invalid_reservoir_state_is_rejected(p,t):
    with pytest.raises(ValueError,match='positive finite'):evaluate(state(),p,t)

def test_assumption_review_and_temperature_are_required():
    with pytest.raises(ValueError,match='review'):face(state(),EOS,1.,1.)
    with pytest.raises(ValueError):GasPipeBoundary(kind='ideal_same_fluid_open',ambient_pressure_pa_abs=1.)

@pytest.mark.parametrize('normal',[-1,1])
def test_actual_fv_inflow_mass_and_total_energy_budgets(normal):
    n=20;u=np.tile(state(),(n,1));opened=GasPipeBoundary(kind='ideal_same_fluid_open',ambient_pressure_pa_abs=1.01,
        reservoir_stagnation_temperature_K=1.1,same_fluid_isentropic_reservoir_reviewed=True)
    closed=GasPipeBoundary(kind='reflective')
    stage=GasPipeBoundaryStage(start_time_s=0.,left=opened if normal==-1 else closed,right=opened if normal==1 else closed)
    r=solve_gas_acoustic_pipe(u,GasAcousticPipeSettings(length_m=1.,inner_diameter_m=1.,duration_s=.1),eos=EOS,boundary_stages=(stage,))
    assert max(abs(r.ledger.residual))<1e-12
    dm=r.ledger.integrated_boundary_inflow[0];de=r.ledger.integrated_boundary_inflow[2]
    assert dm>0
    assert de/dm==pytest.approx(3.5*1.1,rel=2e-12)
    assert r.ledger.final_mass_momentum_energy[0]>r.ledger.initial_mass_momentum_energy[0]

def test_actual_boundary_event_switches_outflow_to_inflow_with_separate_snapshot_budgets():
    def opened(p):return GasPipeBoundary(kind='ideal_same_fluid_open',ambient_pressure_pa_abs=p,
        reservoir_stagnation_temperature_K=1.,same_fluid_isentropic_reservoir_reviewed=True)
    c=GasPipeBoundary(kind='reflective')
    r=solve_gas_acoustic_pipe(np.tile(state(),(24,1)),GasAcousticPipeSettings(length_m=1.,inner_diameter_m=1.,duration_s=.1,
        observation_times_s=(0.,.05,.1)),eos=EOS,boundary_stages=(GasPipeBoundaryStage(start_time_s=0.,left=c,right=opened(.99)),GasPipeBoundaryStage(start_time_s=.05,left=c,right=opened(1.01))))
    assert r.boundary_event_times_s==(.05,)
    area=math.pi/4;dx=1/24
    masses=[area*dx*np.sum(s.conservative[:,0]) for s in r.observations]
    assert masses[1]<masses[0]
    assert masses[2]>masses[1]
    assert max(abs(r.ledger.residual))<1e-12
