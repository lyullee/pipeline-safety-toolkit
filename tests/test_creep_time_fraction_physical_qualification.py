"""Creep time-fraction linear damage against exact block sums."""
import runpy
import pytest
from pipeline_integrity.creep_exchange import evaluate_creep

CASES,_,CTX=runpy.run_path('examples/creep_methods.py')['make_cases']()
BASE=CASES['creep.time_fraction']

def compute(exposures,lives):
    request=BASE.model_copy(update={'parameters':{'exposure_s':list(exposures),'rupture_life_s':list(lives)}})
    return evaluate_creep(request,CTX)

def test_time_fraction_equals_sum_of_exposure_to_life_ratios():
    exposures=(2e5,5e5,7e5); lives=(1e6,2.5e6,7e6)
    result=compute(exposures,lives)
    expected=sum(t/l for t,l in zip(exposures,lives))
    assert result['status']=='computed'
    assert result['values']['time_fraction_damage']==pytest.approx(expected,rel=2e-14)
    assert result['probability_semantics']=={}

def test_time_fraction_is_invariant_to_block_order():
    a=compute((2e5,5e5,7e5),(1e6,2.5e6,7e6))
    b=compute((7e5,2e5,5e5),(7e6,1e6,2.5e6))
    assert b['values']['time_fraction_damage']==pytest.approx(a['values']['time_fraction_damage'],rel=2e-14)

def test_time_fraction_scales_with_exposure_and_is_not_clipped_at_one():
    base=compute((2e5,5e5),(1e6,2.5e6))
    scaled=compute((6e5,1.5e6),(1e6,2.5e6))
    assert base['values']['time_fraction_damage']==pytest.approx(.4,rel=2e-14)
    assert scaled['values']['time_fraction_damage']==pytest.approx(1.2,rel=2e-14)
    assert scaled['probability_semantics']=={}
