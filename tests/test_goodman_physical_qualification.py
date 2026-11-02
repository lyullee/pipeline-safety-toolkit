"""Goodman mean-stress correction and strength boundary through owner exchange."""
import runpy
import pytest
from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue

REQUESTS,CTX=runpy.run_path('examples/cyclic_fatigue.py')['make_cases']()
BASE=REQUESTS['fatigue.goodman']

def evaluate(alternating,mean,ultimate):
    request=BASE.model_copy(update={'parameters':{
        'alternating_stress_Pa':alternating,
        'mean_stress_Pa':mean,
        'ultimate_tensile_strength_Pa':ultimate,
    }})
    return evaluate_cyclic_fatigue(request,CTX)

@pytest.mark.parametrize('alternating,mean,ultimate',[
    (120e6,0.,800e6),(120e6,200e6,800e6),(120e6,400e6,800e6),
    (60e6,400e6,800e6),(120e6,200e6,400e6),
])
def test_goodman_corrected_amplitude_matches_closed_form(alternating,mean,ultimate):
    record=evaluate(alternating,mean,ultimate)
    expected=alternating/(1.-mean/ultimate)
    assert record['status']=='computed'
    assert record['values']['equivalent_fully_reversed_amplitude_Pa']==pytest.approx(expected,rel=2e-12,abs=1e-6)
    assert record['failure_probability'] is None
    assert record['material_or_code_acceptance'] is False

def test_zero_mean_and_zero_alternating_boundaries():
    zero_mean=evaluate(80e6,0.,400e6)
    zero_alternating=evaluate(0.,100e6,400e6)
    assert zero_mean['values']['equivalent_fully_reversed_amplitude_Pa']==pytest.approx(80e6)
    assert zero_alternating['values']['equivalent_fully_reversed_amplitude_Pa']==pytest.approx(0.)

def test_mean_stress_at_or_above_ultimate_strength_is_withheld():
    for mean in (400e6,450e6):
        record=evaluate(80e6,mean,400e6)
        assert record['status']=='out_of_scope'
        assert not record['values']
