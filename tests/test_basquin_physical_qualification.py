"""Basquin stress-life power law against independent dimensionless-ratio solution."""
import runpy
import pytest
from pipeline_integrity.adapters import MaterialClient

REQUEST,CTX=runpy.run_path('examples/pressure_fatigue.py')['make_case']()
CLIENT=MaterialClient()
LOCATION=REQUEST.history.location_id

def cycles(stress_range,reference_range,reference_cycles,exponent):
    result=CLIENT.evaluate('fatigue.basquin',{
        'stress_range_Pa':stress_range,
        'reference_range_Pa':reference_range,
        'reference_cycles':reference_cycles,
        'exponent':exponent,
    },CTX,location_id=LOCATION)
    assert result.status=='computed'
    return result.payload['values']['cycles_to_failure']

@pytest.mark.parametrize('sr,rr,nref,m',[
    (40e6,40e6,1000.,3.0),
    (20e6,40e6,1000.,3.0),
    (60e6,45e6,2.5e5,2.5),
    (30e6,50e6,1.2e6,4.0),
])
def test_basquin_life_matches_reference_stress_power_law(sr,rr,nref,m):
    expected=nref*(rr/sr)**m
    assert cycles(sr,rr,nref,m)==pytest.approx(expected,rel=2e-14)

def test_uniform_stress_unit_scaling_preserves_life_and_reference_identity():
    base=cycles(30e6,60e6,800.,3.2)
    scaled=cycles(300e6,600e6,800.,3.2)
    assert base==pytest.approx(scaled,rel=2e-14)
    assert cycles(60e6,60e6,800.,3.2)==pytest.approx(800.,rel=1e-14)
