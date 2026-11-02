"""Independent waveform power, phasor KVL and reactive budget references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.electrical_circuit import ElectricalCircuitRequest as Q,evaluate_electrical_circuit as solve,ReactiveCompensationRequest as C,evaluate_reactive_compensation as compensate

def make(**kw):return Q(**(dict(system='ac_three_phase_balanced',load_real_power_W=12000.,receiving_line_voltage_V=400.,load_power_factor=.8,load_reactive_direction='lagging',path_resistance_ohm=.2,path_reactance_ohm=.1,impedance_basis='per_phase_line',supplied_conversion_efficiency=.9,steady_state_and_impedance_basis_reviewed=True)|kw))

@pytest.mark.parametrize('system,voltage,phase,basis,factor,direction', [('dc',120.,1,'complete_loop',1.,'none'),*[(system,voltage,phase,basis,f,d) for system,voltage,phase,basis in [('ac_single_phase',240.,1,'complete_loop'),('ac_three_phase_balanced',400.,3,'per_phase_line')] for f,d in [(1.,'none'),(.8,'lagging'),(.6,'leading')]]])
def test_independent_periodic_power_and_line_heat(system,voltage,phase,basis,factor,direction):
 q=make(system=system,receiving_line_voltage_V=voltage,load_power_factor=factor,load_reactive_direction=direction,path_reactance_ohm=0. if system=='dc' else .1,impedance_basis=basis)
 r=solve(q);assert r.status=='computed';v=r.values;I=v['line_current_A'];Vr=voltage/math.sqrt(3) if phase==3 else voltage
 if system=='dc':P=Vr*I;heat=I*I*.2
 else:
  angle=math.acos(factor)*(-1 if direction=='leading' else 1)
  def instantaneous(theta):return sum(2*Vr*I*math.cos(theta-k*2*math.pi/3)*math.cos(theta-k*2*math.pi/3-angle) for k in range(phase))
  P=quad(instantaneous,0.,2*math.pi,epsabs=1e-8,epsrel=1e-12)[0]/(2*math.pi)
  heat=quad(lambda theta:sum(.2*(math.sqrt(2)*I*math.cos(theta-k*2*math.pi/3-angle))**2 for k in range(phase)),0.,2*math.pi)[0]/(2*math.pi)
 assert P==pytest.approx(12000.,rel=3e-13)
 assert v['line_joule_loss_W']==pytest.approx(heat,rel=3e-13)
 assert v['source_real_power_W']==pytest.approx(P+heat,rel=3e-13)
 assert abs(v['real_power_budget_residual_W'])<2e-9
 assert v['useful_conversion_power_W']==pytest.approx(.9*P)
 assert v['conversion_loss_W']==pytest.approx(.1*P)

@pytest.mark.parametrize('direction,sign',[('lagging',1),('leading',-1)])
def test_independent_rectangular_phasor_kvl(direction,sign):
 q=make(load_reactive_direction=direction);v=solve(q).values
 I=12000/(math.sqrt(3)*400*.8);re=I*.8;im=-sign*I*.6;Vr=400/math.sqrt(3)
 dx=.2*re-.1*im;dy=.2*im+.1*re
 assert v['phase_drop_real_V']==pytest.approx(dx,rel=2e-13)
 assert v['phase_drop_imag_V']==pytest.approx(dy,rel=2e-13)
 assert v['source_line_voltage_V']==pytest.approx(math.sqrt(3)*math.hypot(Vr+dx,dy),rel=2e-13)

@pytest.mark.parametrize('field',['load_real_power_W','receiving_line_voltage_V','load_power_factor','path_resistance_ohm','path_reactance_ohm'])
def test_missing_inputs_withheld(field):assert solve(make(**{field:None})).status=='needs_data'

def test_missing_review_is_withheld():assert solve(make(steady_state_and_impedance_basis_reviewed=False)).status=='needs_data'

@pytest.mark.parametrize('kw',[{'impedance_basis':'complete_loop'},{'system':'dc','impedance_basis':'complete_loop'},{'load_reactive_direction':'none'}])
def test_inconsistent_phase_or_reactive_definition_rejected(kw):
 with pytest.raises(ValueError):solve(make(**kw))

def test_zero_load_and_unknown_conversion_are_preserved():
 v=solve(make(load_real_power_W=0.,supplied_conversion_efficiency=None)).values
 assert v['line_current_A']==0 and v['line_joule_loss_W']==0 and 'useful_conversion_power_W' not in v

@pytest.mark.parametrize('initial,target,di,dt,expected',[(.6,1.,'lagging','lagging',4000.),(1.,.6,'lagging','lagging',-4000.),(.6,1.,'leading','lagging',-4000.),(.6,.6,'lagging','leading',8000.),(.6,.6,'leading','leading',0.)])
def test_reactive_budget_from_independent_three_four_five_triangle(initial,target,di,dt,expected):
 q=C(load_real_power_W=3000.,initial_power_factor=initial,target_power_factor=target,initial_direction=di,target_direction=dt,sinusoidal_same_real_power_reviewed=True)
 v=compensate(q).values
 assert v['required_shunt_capacitive_VAr']==pytest.approx(expected,abs=3e-10)
 assert v['initial_reactive_power_VAr']-v['required_shunt_capacitive_VAr']==pytest.approx(v['target_reactive_power_VAr'],abs=3e-10)

def test_reactive_compensation_is_extensive_and_zero_load_limit():
 def run(power):return compensate(C(load_real_power_W=power,initial_power_factor=.7,target_power_factor=.9,initial_direction='lagging',target_direction='lagging',sinusoidal_same_real_power_reviewed=True)).values
 assert run(2000.)['required_shunt_capacitive_VAr']==pytest.approx(2*run(1000.)['required_shunt_capacitive_VAr'])
 assert run(0.)['required_shunt_capacitive_VAr']==0

def test_unreviewed_reactive_target_withheld():
 q=C(load_real_power_W=3000.,initial_power_factor=.6,target_power_factor=1.,initial_direction='lagging',target_direction='lagging')
 assert compensate(q).status=='needs_data'

@pytest.mark.parametrize('system,voltage,basis,pf,direction,factor', [('dc',120.,'complete_loop',1.,'none',1.),('ac_single_phase',240.,'complete_loop',.8,'lagging',1.),('ac_three_phase_balanced',400.,'per_phase_line',.8,'lagging',math.sqrt(3))])
def test_direct_current_observation_returns_real_power(system,voltage,basis,pf,direction,factor):
 q=make(system=system,receiving_line_voltage_V=voltage,impedance_basis=basis,load_power_factor=pf,load_reactive_direction=direction,path_reactance_ohm=0. if system=='dc' else .1,load_real_power_W=None,line_current_A=25.)
 v=solve(q).values
 assert v['line_current_A']==25.
 assert v['load_real_power_W']==pytest.approx(factor*voltage*25*pf,rel=2e-13)

def test_conflicting_current_and_power_are_not_silently_overwritten():
 with pytest.raises(ValueError):solve(make(line_current_A=100.))
