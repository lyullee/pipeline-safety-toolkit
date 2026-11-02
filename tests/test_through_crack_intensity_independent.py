"""Uniform-crack traction weight integral independent of the scalar K owner."""
import math,runpy
import pytest
from scipy.integrate import quad
from pipeline_integrity.through_crack_intensity import ThroughCrackIntensityRequest as Request,evaluate_through_crack_intensity as evaluate
C=runpy.run_path('examples/cyclic_fatigue.py')['make_cases']()[1]
def request(a,stress):
    return Request(asset_id=C.asset_id,clock_id=C.clock_id,location_id='plate',half_crack_length_m=a,
        opening_stress_pa=stress,geometry_factor=1.,fracture_toughness_pa_sqrt_m=80e6,
        minimum_supported_half_length_m=.0001,maximum_supported_half_length_m=1.,geometry_solution_id='infinite-plate-uniform-traction',
        evidence_id='authored',length_coordinate_linear_elastic_geometry_and_toughness_reviewed=True)

@pytest.mark.parametrize('a',[.001,.03,.5])
@pytest.mark.parametrize('stress',[20e6,200e6])
def test_weight_function_integral_and_elastic_crack_closure_energy(a,stress):
    result=evaluate(request(a,stress),C)
    assert result['status']=='computed'
    # x=a cos(theta) removes the crack-tip weight-function singularities.
    integral=quad(lambda theta:stress*a*(1+math.cos(theta)),0,math.pi,epsabs=1e-6)[0]
    expected=integral/math.sqrt(math.pi*a)
    values=result['values']
    assert values['stress_intensity_Pa_sqrt_m']==pytest.approx(expected,rel=2e-13)
    # Plate opening work per thickness = pi*sigma^2*a^2/E; two tips advance.
    modulus=200e9
    release=quad(lambda theta:2*stress**2*a/modulus*math.sin(theta)**2,0,math.pi)[0]
    assert expected**2/modulus==pytest.approx(release,rel=2e-13)
    assert values['toughness_margin_Pa_sqrt_m']==pytest.approx(80e6-expected,rel=2e-13)

def test_half_length_never_surface_penetration_gate_and_no_variable_factor_critical_claim():
    result=evaluate(request(.5,100e6).model_copy(update={'geometry_factor':2.}),C)
    assert result['status']=='computed'
    assert result['critical_length_qualification_inferred'] is False

def test_missing_geometry_is_withheld():
    assert evaluate(request(.01,100e6).model_copy(update={'geometry_factor':None}),C)['status']=='needs_data'
