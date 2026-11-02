"""Steady arc-energy balance through the public welding exchange API."""
import runpy
import pytest
from pipeline_integrity.welding_exchange import evaluate_welding

CASES,_,_,_,_,CTX=runpy.run_path('examples/welding_methods.py')['make_cases']()
BASE=CASES['welding.arc_energy']

def calculate(voltage,current,speed,efficiency):
    request=BASE.model_copy(update={'parameters':{'voltage_V':voltage,'current_A':current,
        'travel_speed_m_s':speed,'efficiency':efficiency}})
    return evaluate_welding(request,CTX)

@pytest.mark.parametrize('voltage,current,speed,efficiency',[
    (20.0,100.0,0.005,0.8),
    (28.0,240.0,0.004,0.72),
    (12.0,65.0,0.0013,1.0),
])
def test_net_arc_heat_per_length_matches_electrical_energy_balance(voltage,current,speed,efficiency):
    result=calculate(voltage,current,speed,efficiency)
    gross=voltage*current/speed
    net=efficiency*gross
    assert result['status']=='computed'
    assert result['values']['arc_energy_J_m']==pytest.approx(gross,rel=2e-14)
    assert result['values']['net_heat_input_J_m']==pytest.approx(net,rel=2e-14)


def test_net_heat_input_scales_linearly_with_efficiency_and_inversely_with_speed():
    base=calculate(20.,100.,.005,.8)['values']['net_heat_input_J_m']
    doubled_eff=calculate(20.,100.,.005,.4)['values']['net_heat_input_J_m']
    doubled_speed=calculate(20.,100.,.010,.8)['values']['net_heat_input_J_m']
    assert doubled_eff==pytest.approx(.5*base,rel=2e-14)
    assert doubled_speed==pytest.approx(.5*base,rel=2e-14)
