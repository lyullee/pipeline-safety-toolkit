"""Independent source-ODE and constitutive checks for instantaneous pipe drag."""
import math
from dataclasses import FrozenInstanceError
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.gas_acoustic_pipe import (
    GasPipeLocalState, GasAcousticPipeSettings, GasPipeBoundary, GasPipeBoundaryStage,
    IdealGasInternalEnergyEOS, gas_conservative_state, solve_gas_acoustic_pipe,
)
from pipeline_integrity.gas_pipe_friction import StateDependentDarcyDecay

EOS=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=1.,gamma=1.4)

def local(v=(0.,1.),rho=(1.,1.),temp=(1.,1.),d=1.):
    return GasPipeLocalState(0.,d,tuple(rho),tuple(v),tuple(r*t for r,t in zip(rho,temp)),tuple(temp))

def integrate(provider,v=.5,t=.2,dt=.002,temperature=1.,**kwargs):
    n=4
    u=gas_conservative_state(np.ones(n),np.full(n,v),np.full(n,temperature/(EOS.gamma-1)))
    b=GasPipeBoundary(kind='periodic')
    return solve_gas_acoustic_pipe(u,GasAcousticPipeSettings(length_m=100.,inner_diameter_m=1.,duration_s=t,
        maximum_step_s=dt,**kwargs),eos=EOS,boundary_stages=(GasPipeBoundaryStage(start_time_s=0.,left=b,right=b),),
        momentum_decay_rate_provider=provider)

def ledger(r):
    assert max(abs(r.ledger.residual))<2e-11
    assert r.ledger.integrated_distributed_sources[0]==0.
    assert r.ledger.integrated_distributed_sources[2]==0.
    assert r.ledger.final_mass_momentum_energy[2]==pytest.approx(r.ledger.initial_mass_momentum_energy[2],abs=1e-10)

@pytest.mark.parametrize('velocity',[-.5,0.,.5])
def test_laminar_exponential_signed_decay_and_internal_heating(velocity):
    mu=.02; rate=32*mu; end=.2
    p=StateDependentDarcyDecay(lambda s:(mu,)*len(s.velocity_m_s))
    r=integrate(p,v=velocity,t=end,dt=.0005)
    exact=velocity*math.exp(-rate*end)
    assert r.velocity_m_s==pytest.approx(np.full(4,exact),abs=3e-9)
    expected=1.+(EOS.gamma-1)*.5*(velocity**2-exact**2)
    assert r.temperature_K==pytest.approx(np.full(4,expected),abs=2e-9)
    assert r.momentum_decay_identity['total_energy_removed_by_drag'] is False
    ledger(r)

def test_viscosity_uses_current_temperature_not_initial_value_and_second_order_time_convergence():
    v0=2.;total=1./(EOS.gamma-1)+.5*v0*v0
    mu0=.01
    def mu(s):return tuple(mu0*t**1.5 for t in s.temperature_K)
    def ode(t,y):
        temp=(EOS.gamma-1)*(total-.5*y[0]**2)
        return [-32*mu0*temp**1.5*y[0]]
    exact=solve_ivp(ode,(0.,.4),[v0],rtol=2e-13,atol=2e-14).y[0,-1]
    errors=[]
    for dt in (.02,.01,.005):
        r=integrate(StateDependentDarcyDecay(mu),v=v0,t=.4,dt=dt)
        errors.append(abs(r.velocity_m_s[0]-exact));ledger(r)
    assert errors[1]<.3*errors[0]
    assert errors[2]<.3*errors[1]
    assert errors[-1]<2e-6

@pytest.mark.parametrize('velocity',[-8.,8.])
def test_turbulent_feedback_against_independent_colebrook_root_and_ode(velocity):
    mu=1e-5;rough=.0004
    def f(re):
        # Independent root in reciprocal-square-root friction coordinate.
        z=brentq(lambda z:z+2*math.log10(rough/3.7+2.51*z/re),1.,30.,xtol=1e-13)
        return 1/z**2
    initial_rate=f(abs(velocity)/mu)*abs(velocity)/2
    provider=StateDependentDarcyDecay(lambda s:(mu,)*len(s.velocity_m_s),roughness_m=rough)
    assert provider(local(v=(velocity,),rho=(1.,),temp=(1.,)))[0]==pytest.approx(initial_rate,rel=2e-12)
    ref=solve_ivp(lambda t,y:[-f(abs(y[0])/mu)*y[0]*abs(y[0])/2],(0.,.3),[velocity],rtol=2e-13,atol=2e-14).y[0,-1]
    r=integrate(provider,v=velocity,t=.3,dt=.0005)
    assert r.velocity_m_s==pytest.approx(np.full(4,ref),rel=2e-9)
    ledger(r)

def test_callback_receives_immutable_stage_states_and_actual_stage_times():
    seen=[]
    def mu(s):
        seen.append(s)
        with pytest.raises(FrozenInstanceError):s.time_s=1.
        with pytest.raises(TypeError):s.temperature_K[0]=3.
        return (.01,)*len(s.velocity_m_s)
    r=integrate(StateDependentDarcyDecay(mu),dt=.05,t=.1)
    assert any(s.time_s==.05 and s.velocity_m_s[0]!=.5 for s in seen)
    assert max(s.time_s for s in seen)==pytest.approx(.1)
    assert len(seen)>=3*r.accepted_steps

@pytest.mark.parametrize('re',[2300.,3000.,3999.])
def test_transition_is_withheld_unless_explicitly_reviewed(re):
    s=local(v=(1.,),rho=(1.,),temp=(1.,))
    p=StateDependentDarcyDecay(lambda s:(1/re,))
    with pytest.raises(ValueError,match='transitional'):p(s)
    assert StateDependentDarcyDecay(lambda s:(1/re,),transition_policy='use_declared_correlation')(s)[0]>0

@pytest.mark.parametrize('bad',[(-1.,)*4,(float('nan'),)*4,(1.,),(float('inf'),)*4])
def test_solver_rejects_invalid_rates_before_advancing(bad):
    with pytest.raises(ValueError,match='aligned finite nonnegative'):integrate(lambda s:bad)

def test_fixed_and_state_dependent_drag_cannot_be_counted_twice():
    with pytest.raises(ValueError,match='mutually exclusive'):integrate(lambda s:(1.,)*4,darcy_friction_factor=.02)

@pytest.mark.parametrize('mu',[0.,-1.,float('nan')])
def test_invalid_viscosity_is_not_invented(mu):
    with pytest.raises(ValueError,match='positive finite'):StateDependentDarcyDecay(lambda s:(mu,mu))(local())

def test_per_cell_density_diameter_and_roughness_basis():
    p=StateDependentDarcyDecay(lambda s:(.01,.02),roughness_m=(0.,.0001))
    assert p(local(v=(0.,-.1),rho=(1.,4.),d=.5))==pytest.approx((32*.01/.5**2,32*.005/.5**2))

def test_stage_source_growth_retries_and_preserves_total_energy():
    def rate(s):return (1.+200*s.time_s,)*len(s.velocity_m_s)
    r=integrate(rate,t=.1,dt=.1,source_fraction_per_step=.2)
    assert r.rejected_steps>0
    # Integral rate = t+100*t^2.
    assert r.velocity_m_s[0]==pytest.approx(.5*math.exp(-1.1),rel=.02)
    ledger(r)

@pytest.mark.parametrize('temperature',[0.,float('nan')])
def test_provider_rejects_invalid_absolute_thermodynamic_input(temperature):
    with pytest.raises(ValueError,match='absolute pressure and temperature'):
        StateDependentDarcyDecay(lambda s:(.01,))(local(v=(0.,),rho=(1.,),temp=(temperature,)))

def test_per_cell_roughness_is_copied_before_caller_mutation():
    values=[.0001,.0002]
    p=StateDependentDarcyDecay(lambda s:(.01,.01),roughness_m=values)
    values[0]=1.
    assert p.roughness_m==(.0001,.0002)
