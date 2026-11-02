"""Unequal-exponent life roots reduced independently to a quadratic."""
import math,runpy
import pytest
from pipeline_integrity.strain_life_coordinate import StrainLifeCoordinateRequest as Request,evaluate_strain_life_coordinate as evaluate
CONTEXT=runpy.run_path('examples/cyclic_fatigue.py')['make_cases']()[1]

def request(coordinate,value):
    return Request(asset_id=CONTEXT.asset_id,clock_id=CONTEXT.clock_id,location_id='specimen',coordinate=coordinate,value=value,
        elastic_modulus_pa=200e9,fatigue_strength_coefficient_pa=1e9,fatigue_strength_exponent=-.5,
        fatigue_ductility_coefficient=.12,fatigue_ductility_exponent=-1.,minimum_valid_cycles=1.,maximum_valid_cycles=1e9,
        calibration_basis_id='authored-two-term',calibration_evidence_id='authored',fully_reversed_same_measure_modern_calibration_reviewed=True)

@pytest.mark.parametrize('amplitude',[.0002,.001,.01])
@pytest.mark.parametrize('coordinate',['total_strain_amplitude','total_strain_range','elastic_pseudo_stress_amplitude'])
def test_quadratic_inverse_and_coordinate_consistency(amplitude,coordinate):
    value=amplitude*({'total_strain_amplitude':1,'total_strain_range':2,'elastic_pseudo_stress_amplitude':200e9}[coordinate])
    result=evaluate(request(coordinate,value),CONTEXT)
    # z=(2N)^(-1/2), epsilon=A*z+B*z*z; cancellation-safe positive root.
    a=.005;b=.12;z=2*amplitude/(a+math.sqrt(a*a+4*b*amplitude))
    expected=.5/z**2
    assert result['status']=='computed'
    assert result['values']['cycles_to_failure']==pytest.approx(expected,rel=2e-11)
    assert result['values']['reversals_to_failure']==pytest.approx(2*expected,rel=2e-11)

def test_missing_value_and_review_are_withheld():
    q=request('total_strain_amplitude',None)
    assert evaluate(q,CONTEXT)['status']=='needs_data'
    q=request('total_strain_amplitude',.001).model_copy(update={'fully_reversed_same_measure_modern_calibration_reviewed':False})
    assert evaluate(q,CONTEXT)['status']=='needs_data'

def test_no_outside_calibration_extrapolation():
    q=request('total_strain_amplitude',.001).model_copy(update={'minimum_valid_cycles':1e6})
    assert evaluate(q,CONTEXT)['status']=='out_of_scope'
