"""Archard wear-volume and mean-depth closure through the public owner bridge."""
import runpy
import pytest
from pipeline_integrity.corrosion_wear import evaluate_corrosion_wear

REQUESTS,CTX=runpy.run_path('examples/corrosion_wear.py')['make_cases']()
BASE=REQUESTS['degradation.archard_wear']

def calculate(**overrides):
    p=BASE.parameters | overrides
    return evaluate_corrosion_wear(BASE.model_copy(update={'parameters':p}),CTX)

@pytest.mark.parametrize('changes',[
    {},
    {'normal_force_N':200.0,'sliding_distance_m':30.0},
    {'hardness_Pa':2e9},
])
def test_archard_volume_and_mean_depth_match_closed_form(changes):
    result=calculate(**changes)
    p=BASE.parameters|changes
    expected_volume=(p['dimensionless_wear_coefficient']*p['normal_force_N']*p['sliding_distance_m']/p['hardness_Pa'])
    expected_depth=expected_volume/p['distributed_area_m2']
    assert result['status']=='computed'
    assert result['values']['wear_volume_m3']==pytest.approx(expected_volume,rel=2e-14)
    assert result['values']['mean_wear_depth_m']==pytest.approx(expected_depth,rel=2e-14)

def test_archard_scaling_and_zero_load_boundary():
    base=calculate()
    sixfold=calculate(normal_force_N=200.0,sliding_distance_m=30.0)
    assert sixfold['values']['wear_volume_m3']==pytest.approx(6*base['values']['wear_volume_m3'],rel=2e-14)
    assert sixfold['values']['mean_wear_depth_m']==pytest.approx(6*base['values']['mean_wear_depth_m'],rel=2e-14)
    zero=calculate(normal_force_N=0.0)
    assert zero['values']['wear_volume_m3']==0.0
    assert zero['values']['mean_wear_depth_m']==0.0

