"""Conditional Stern-Geary corrosion-current identity through the owner adapter."""
import math
import runpy
import pytest
from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical

CASES,CTX=runpy.run_path('examples/electrochemical_methods.py')['make_cases']()
BASE=CASES['corrosion.stern_geary']

def evaluate(a,c,rp):
    p=BASE.model_copy(update={'parameters':{
        'anodic_tafel_slope_V_decade':a,
        'cathodic_tafel_slope_V_decade':c,
        'area_normalized_resistance_ohm_m2':rp,
    }})
    return evaluate_electrochemical(p,CTX)

@pytest.mark.parametrize('a,c,rp',[(0.12,0.12,0.02),(0.08,0.18,0.035),(0.21,0.07,0.01)])
def test_stern_geary_coefficient_and_current_density_match_independent_identity(a,c,rp):
    result=evaluate(a,c,rp)
    expected=(a*c)/(math.log(10.0)*(a+c))
    assert result['status']=='computed'
    assert result['values']['stern_geary_coefficient_V']==pytest.approx(expected,rel=2e-13)
    assert result['values']['corrosion_current_density_A_m2']==pytest.approx(expected/rp,rel=2e-13)


def test_swapping_tafel_slopes_preserves_coefficient_and_resistance_scales_current():
    a,c,rp=.09,.16,.025
    x=evaluate(a,c,rp)['values']; swapped=evaluate(c,a,rp)['values']; doubled=evaluate(a,c,2*rp)['values']
    assert swapped['stern_geary_coefficient_V']==pytest.approx(x['stern_geary_coefficient_V'],rel=2e-13)
    assert swapped['corrosion_current_density_A_m2']==pytest.approx(x['corrosion_current_density_A_m2'],rel=2e-13)
    assert doubled['corrosion_current_density_A_m2']==pytest.approx(x['corrosion_current_density_A_m2']/2,rel=2e-13)

