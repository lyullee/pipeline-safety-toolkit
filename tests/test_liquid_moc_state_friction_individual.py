"""Independent uniform acoustic-field reductions and local wall-law references."""
from dataclasses import FrozenInstanceError
import math
import numpy as np
import pytest
from scipy.integrate import quad,solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.liquid_moc_friction import LiquidMOCFrictionState,LiquidMOCQuasisteadyDarcy
from pipeline_integrity.transient import History,LiquidNode,LiquidPipe,LiquidTransient,simulate_liquid_transient

G=9.80665
CONTEXT=RunContext(asset_id='authored_pipe',scenario_id='uniform_momentum_reference',clock_id='elapsed',origin='synthetic')

def local(v=(-.22,0.,.22),d=.01):
    return LiquidMOCFrictionState('pipe',0.,d,1000.,(0.,1.,2.),(2e5,2e5,2e5),v)

def colebrook(re,relative):
    return brentq(lambda f:1/math.sqrt(f)+2*math.log10(relative/3.7+2.51/(re*math.sqrt(f))),.004,.2,xtol=1e-15)

def fixture(dt,*,v0=.02,d=.01,length=10.,wave=100.,drive=.064,duration=.4,fixed=0.):
    area=math.pi*d*d/4
    h0=40.;h1=h0-drive*length/G
    nodes=tuple(LiquidNode(id=name,elevation_m=0.,initial_head_m=h,boundary='head',history=History(time_s=(0.,),values=(h,))) for name,h in [('up',h0),('down',h1)])
    return LiquidTransient(nodes=nodes,pipes=(LiquidPipe(id='pipe',start_node='up',end_node='down',length_m=length,diameter_m=d,wave_speed_m_s=wave,darcy_friction_factor=fixed,initial_volume_flow_m3_s=v0*area),),
        duration_s=duration,requested_timestep_s=dt,density_kg_m3=1000.,ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2000.,
        maximum_wave_speed_adjustment=1e-12,initial_head_tolerance_m=1e-10,mass_balance_tolerance_m3=1e-9)

def run(spec,provider):
    return simulate_liquid_transient(spec,CONTEXT,momentum_decay_rate_provider=provider,momentum_decay_rate_evidence_id='authored_local_viscosity_law')

@pytest.mark.parametrize('nu',[1e-6,1e-5])
def test_laminar_zero_signed_and_2040_2300(nu):
    state=local()
    rates=LiquidMOCQuasisteadyDarcy(lambda s:(nu,)*3)(state)
    assert rates==pytest.approx([32*nu/.01**2]*3,rel=2e-15)

@pytest.mark.parametrize('rough',[0.,1e-4])
def test_turbulent_darcy_against_independent_implicit_root(rough):
    state=local((-1.,1.,2.),.05)
    got=LiquidMOCQuasisteadyDarcy(lambda s:(1e-6,)*3,roughness_m=rough)(state)
    want=[colebrook(abs(v)*.05/1e-6,rough/.05)*abs(v)/(.1) for v in state.velocity_m_s]
    assert got==pytest.approx(want,rel=2e-12)

@pytest.mark.parametrize('values',[(0.,)*3,(float('nan'),)*3,(1e-6,)])
def test_invalid_viscosity_is_not_a_zero_drag(values):
    with pytest.raises(ValueError):LiquidMOCQuasisteadyDarcy(lambda s:values)(local())

def test_transitional_policy_is_explicit():
    state=local((.3,)*3)
    with pytest.raises(ValueError,match='withheld'):LiquidMOCQuasisteadyDarcy(lambda s:(1e-6,)*3)(state)
    assert all(x>0 for x in LiquidMOCQuasisteadyDarcy(lambda s:(1e-6,)*3,transition_policy='use_declared_correlation')(state))

@pytest.mark.parametrize('sign',[-1.,1.])
def test_uniform_laminar_field_matches_independent_integrating_factor(sign):
    beta=3.2;growth=1.25;v0=sign*.02;end=.4
    phi=lambda t:beta*(t+growth*t*t/2)
    exact=math.exp(-phi(end))*(v0+beta*v0*quad(lambda t:math.exp(phi(t)),0,end,epsabs=1e-13)[0])
    errors=[]
    for dt in [.01,.005,.0025]:
        calls=[]
        def viscosity(state):
            calls.append(state)
            with pytest.raises(FrozenInstanceError):state.time_s=-1.
            return (1e-5*(1+growth*state.time_s),)*len(state.velocity_m_s)
        result=run(fixture(dt,v0=v0,drive=beta*v0),LiquidMOCQuasisteadyDarcy(viscosity))
        speed=np.array(result['pipes']['pipe']['start_flow_m3_s'])/(math.pi*.01**2/4)
        errors.append(abs(speed[-1]-exact))
        assert result['status']=='computed'
        assert result['maximum_pipe_volume_residual_m3']<1e-12
        np.testing.assert_allclose(result['pipes']['pipe']['start_flow_m3_s'],result['pipes']['pipe']['end_flow_m3_s'],rtol=0,atol=1e-15)
        assert len({s.time_s for s in calls})>10
        assert result['state_dependent_friction']['thermal_evolution_predicted'] is False
        # Uniform momentum balance also gives actual pressure-work minus
        # kinetic-power change as the nonnegative wall mechanical dissipation.
        times=np.array(result['time_s']);vel=speed[:-1]
        rate=beta*(1+growth*times[:-1]);acc=np.diff(speed)/np.diff(times)
        np.testing.assert_allclose(acc,beta*v0-rate*vel,atol=3e-10,rtol=2e-8)
        assert np.all(rate*vel**2>=0)
    assert errors[-1]<2e-5
    assert 1.8<errors[0]/errors[1]<2.2 and 1.8<errors[1]/errors[2]<2.2

@pytest.mark.parametrize('rough',[0.,1e-4])
def test_uniform_turbulent_field_feedback_and_time_refinement(rough):
    d=.05;nu0=1e-6;v0=1.;end=.4
    drive=colebrook(v0*d/nu0,rough/d)*v0*v0/(2*d)
    def ode(t,y):
        v=float(y[0]);nu=nu0*(1+1.5*t)
        return [drive-colebrook(abs(v)*d/nu,rough/d)*v*abs(v)/(2*d)]
    exact=solve_ivp(ode,(0,end),[v0],method='DOP853',rtol=2e-12,atol=1e-13).y[0,-1]
    errors=[]
    # Keep the original three grids: the smooth-pipe third-grid error was
    # 2.4983e-5 m/s against the unchanged 2e-5 budget. Add refinement rather
    # than treating that coarse result as qualified or relaxing the budget.
    for dt in [.01,.005,.0025,.00125]:
        model=LiquidMOCQuasisteadyDarcy(lambda s:(nu0*(1+1.5*s.time_s),)*len(s.velocity_m_s),roughness_m=rough)
        result=run(fixture(dt,v0=v0,d=d,length=20.,wave=200.,drive=drive),model)
        actual=result['pipes']['pipe']['start_flow_m3_s'][-1]/(math.pi*d*d/4)
        errors.append(abs(actual-exact))
        assert result['status']=='computed'
        assert result['maximum_pipe_volume_residual_m3']<1e-10
    assert errors[-1]<2e-5
    assert all(1.7<a/b<2.3 for a,b in zip(errors,errors[1:]))

@pytest.mark.parametrize('mode',['shape','negative','nonfinite','stiff'])
def test_invalid_rate_shape_sign_support_and_stiffness(mode):
    def provider(s):
        n=len(s.velocity_m_s)
        return [1.] if mode=='shape' else ([float('nan')]*n if mode=='nonfinite' else [-1.]*n if mode=='negative' else [1e5]*n)
    with pytest.raises(ValueError):run(fixture(.01),provider)

def test_dual_drag_and_missing_evidence_rejected():
    provider=LiquidMOCQuasisteadyDarcy(lambda s:(1e-5,)*len(s.velocity_m_s))
    with pytest.raises(ValueError,match='coexist'):run(fixture(.01,fixed=.02),provider)
    with pytest.raises(ValueError,match='evidence'):simulate_liquid_transient(fixture(.01),CONTEXT,momentum_decay_rate_provider=provider)

def test_initial_local_equilibrium_not_total_loss_only():
    with pytest.raises(ValueError,match='initial local'):
        run(fixture(.01),lambda s:tuple(3.2*(1+.2*x) for x in s.chainage_m))

def test_zero_speed_has_finite_rate_and_no_spurious_source():
    model=LiquidMOCQuasisteadyDarcy(lambda s:(1e-5,)*len(s.velocity_m_s))
    result=run(fixture(.01,v0=0.,drive=0.),model)
    assert result['status']=='computed'
    assert all(v==0 for v in result['pipes']['pipe']['start_flow_m3_s'])

def test_fixed_rate_callback_reproduces_existing_fixed_darcy_field():
    d=.05;speed=.2;factor=.03
    settings=fixture(.01,v0=speed,d=d,drive=factor*speed**2/(2*d),fixed=factor)
    old=simulate_liquid_transient(settings,CONTEXT)
    spec=settings.model_copy(update={'pipes':tuple(p.model_copy(update={'darcy_friction_factor':0.}) for p in settings.pipes)})
    new=run(spec,lambda s:tuple(factor*abs(v)/(2*d) for v in s.velocity_m_s))
    for key in old['pipes']['pipe']:
        np.testing.assert_allclose(new['pipes']['pipe'][key],old['pipes']['pipe'][key],rtol=0,atol=2e-12)
