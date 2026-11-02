"""Parabolic oxide thickness growth closed-form checks through the owner adapter."""
import math
import runpy
import pytest
from pipeline_integrity.creep_exchange import evaluate_creep

CASES,_,CTX=runpy.run_path('examples/creep_methods.py')['make_cases']()
BASE=CASES['degradation.parabolic_oxidation']

def calculate(initial,k,time):
    request=BASE.model_copy(update={'parameters':{
        'initial_oxide_thickness_m':initial,
        'parabolic_constant_m2_s':k,
        'exposure_s':time,
    }})
    return evaluate_creep(request,CTX)

@pytest.mark.parametrize('initial,k,time',[(0.0,1e-18,1e6),(1e-6,1e-18,1e6),(2e-6,4e-18,3e6)])
def test_oxide_thickness_satisfies_parabolic_growth_identity(initial,k,time):
    result=calculate(initial,k,time)
    expected=math.sqrt(initial*initial+k*time)
    assert result['status']=='computed'
    assert result['values']['oxide_thickness_m']==pytest.approx(expected,rel=2e-14)
    assert result['values']['oxide_growth_m']==pytest.approx(expected-initial,rel=2e-14,abs=1e-16)
    assert result['values']['oxide_thickness_m']**2-initial**2==pytest.approx(k*time,rel=2e-13,abs=1e-28)


def test_equal_parabolic_exposure_products_have_equal_final_thickness():
    a=calculate(1e-6,1e-18,4e6)
    b=calculate(1e-6,4e-18,1e6)
    assert a['values']['oxide_thickness_m']==pytest.approx(b['values']['oxide_thickness_m'],rel=2e-14)
    assert a['values']['oxide_growth_m']==pytest.approx(b['values']['oxide_growth_m'],rel=2e-14)
