"""Independent gas-throughput ODE, series resistance and gas-mass budgets."""
import math
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.vacuum_pumpdown import VacuumPumpdownRequest as Request,evaluate_vacuum_pumpdown as solve


def make(**updates):
    q=Request(volume_m3=2.,initial_pressure_Pa_abs=1e5,pump_speed_m3_s=.4,line_conductances_m3_s=(.4,),
        pump_ultimate_pressure_Pa_abs=20.,constant_gas_load_Pa_m3_s=4.,gas_temperature_K=300.,
        specific_gas_constant_J_kg_K=287.05,elapsed_times_s=(0.,1.,10.,100.),target_pressure_Pa_abs=100.,
        clock_id='vacuum-clock',pump_start_time_s=50.,
        isothermal_fixed_composition_and_constant_linear_throughput_reviewed=True)
    return q.model_copy(update=updates)


@pytest.mark.parametrize('conductances',[(),(.4,),(.4,.2)])
def test_series_resistance_and_one_time_constant_target(conductances):
    inverse=1/.4+sum(1/c for c in conductances);speed=1/inverse;tau=2/speed
    q=make(line_conductances_m3_s=conductances,constant_gas_load_Pa_m3_s=0.,
        target_pressure_Pa_abs=20.+(1e5-20.)/math.e,elapsed_times_s=(0.,tau,2*tau,5*tau))
    r=solve(q)
    assert r.effective_speed_m3_s==pytest.approx(speed)
    assert r.time_to_target_s==pytest.approx(tau)
    for i,row in enumerate(r.points):
        n=(0,1,2,5)[i]
        assert row.pressure_Pa_abs==pytest.approx(20.+(1e5-20.)/math.e**n)


@pytest.mark.parametrize('load',[0.,4.,40000.])
def test_independent_throughput_ode_and_integrated_mass_budget(load):
    q=make(constant_gas_load_Pa_m3_s=load);r=solve(q);speed=.2
    def rhs(t,y):
        # Separate pump-inlet pressure from line throughput continuity.
        inlet=(.4*20.+.4*y[0])/(.4+.4)
        line_throughput=.4*(y[0]-inlet)
        return [(load-line_throughput)/2.]
    numerical=solve_ivp(rhs,(0.,100.),[1e5],method='DOP853',t_eval=q.elapsed_times_s,rtol=2e-12,atol=1e-8)
    assert numerical.success
    for row,value in zip(r.points,numerical.y[0]):
        assert row.pressure_Pa_abs==pytest.approx(value,rel=2e-10,abs=1e-6)
        equilibrium=20.+load/speed
        extracted=quad(lambda t:speed*((equilibrium+(1e5-equilibrium)*math.exp(-speed*t/2.))-20.),0.,row.elapsed_time_s,epsabs=1e-8)[0]
        assert row.cumulative_removed_mass_kg==pytest.approx(extracted/(287.05*300),abs=1e-10)
        assert row.gas_mass_kg==pytest.approx(1e5*2/(287.05*300)+row.cumulative_added_mass_kg-row.cumulative_removed_mass_kg)
        assert abs(row.mass_budget_residual_kg)<1e-12


@pytest.mark.parametrize('pump,conductances',[(0.,()),(.4,(0.,)),(.4,(.4,0.))])
def test_zero_pumping_or_closed_line_no_target_and_linear_gas_loading(pump,conductances):
    r=solve(make(pump_speed_m3_s=pump,line_conductances_m3_s=conductances))
    assert r.effective_speed_m3_s==0. and r.target_status=='unreachable'
    for row in r.points:
        assert row.pressure_Pa_abs==pytest.approx(1e5+4*row.elapsed_time_s/2)
        assert row.cumulative_removed_mass_kg==0.


@pytest.mark.parametrize('target,status',[(None,'not_requested'),(1e5,'at_start'),(2e5,'at_start'),(40.,'unreachable'),(20.,'unreachable'),(0.,'unreachable')])
def test_target_semantics_and_asymptotic_floor(target,status):
    r=solve(make(target_pressure_Pa_abs=target))
    assert r.target_status==status
    assert r.time_to_target_s==0. if status=='at_start' else r.time_to_target_s is None


def test_equal_initial_and_asymptotic_pressure_is_stationary():
    r=solve(make(initial_pressure_Pa_abs=40.))
    assert all(p.pressure_Pa_abs==pytest.approx(40.) for p in r.points)
    assert r.target_status=='at_start'


def test_gas_load_can_raise_pressure_despite_running_pump():
    r=solve(make(constant_gas_load_Pa_m3_s=40000.,target_pressure_Pa_abs=5e4))
    assert r.points[-1].pressure_Pa_abs>1e5 and r.target_status=='unreachable'


def test_volume_speed_and_gas_load_common_scaling():
    a=solve(make());b=solve(make(volume_m3=6.,pump_speed_m3_s=1.2,line_conductances_m3_s=(1.2,),constant_gas_load_Pa_m3_s=12.))
    assert b.time_to_target_s==pytest.approx(a.time_to_target_s)
    for x,y in zip(a.points,b.points):
        assert y.pressure_Pa_abs==pytest.approx(x.pressure_Pa_abs)
        assert y.gas_mass_kg==pytest.approx(3*x.gas_mass_kg)


def test_clock_origin_translation_preserved():
    a=solve(make());b=solve(make(pump_start_time_s=250.))
    assert a.clock_id==b.clock_id
    for x,y in zip(a.points,b.points):
        assert x.pressure_Pa_abs==y.pressure_Pa_abs and y.time_s==x.time_s+200.


def test_short_time_positive_pump_inventory_with_loading():
    t=1e-8;r=solve(make(initial_pressure_Pa_abs=20.,elapsed_times_s=(t,)))
    expected=(4./(287.05*300))*t*t/(2*10.)
    assert r.points[0].cumulative_removed_mass_kg==pytest.approx(expected,rel=1e-7,abs=0.)


def test_small_positive_long_time_pressure_not_cancelled_to_zero():
    r=solve(make(pump_ultimate_pressure_Pa_abs=0.,constant_gas_load_Pa_m3_s=0.,elapsed_times_s=(500.,)))
    assert r.points[0].pressure_Pa_abs==pytest.approx(1e5*math.exp(-50.),rel=1e-13,abs=0.)


@pytest.mark.parametrize('field,value',[('pump_speed_m3_s',None),('line_conductances_m3_s',None),
    ('line_conductances_m3_s',(.4,None)),('pump_ultimate_pressure_Pa_abs',None),
    ('constant_gas_load_Pa_m3_s',None),('isothermal_fixed_composition_and_constant_linear_throughput_reviewed',False)])
def test_unknown_parameters_and_review_withheld(field,value):
    r=solve(make(**{field:value}))
    assert r.status=='needs_data' and not r.points


@pytest.mark.parametrize('field,value',[('volume_m3',0.),('line_conductances_m3_s',(-.1,)),
    ('elapsed_times_s',(-1.,)),('specific_gas_constant_J_kg_K',math.inf),
    ('pump_ultimate_pressure_Pa_abs',2e5)])
def test_invalid_domains(field,value):
    with pytest.raises(ValueError):solve(make(**{field:value}))
