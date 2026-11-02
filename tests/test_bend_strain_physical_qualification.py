"""Bend outer-fiber geometric strain check through the actual fabrication adapter."""
import runpy
import pytest
from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.fabrication_exchange import evaluate_fabrication

REQUESTS,CTX=runpy.run_path('examples/fabrication_methods.py')['make_cases']()
BASE=REQUESTS['fabrication.bend_strain']

def run(diameter,radius):
    request=BASE.model_copy(update={'parameters':{
        'outside_diameter_m':diameter,
        'centerline_bend_radius_m':radius,
    }})
    result=evaluate_fabrication(request,CTX,client=MaterialClient())
    assert result.status=='computed'
    return result.values['outer_fiber_strain']

@pytest.mark.parametrize('diameter,radius',[(0.01,0.25),(0.2,2.0),(0.5,1.5)])
def test_outer_fiber_strain_matches_bending_geometry_ratio(diameter,radius):
    assert run(diameter,radius)==pytest.approx(diameter/(2*radius),rel=2e-14)

def test_outer_fiber_strain_is_ratio_invariant_under_geometric_scale():
    assert run(0.1,1.0)==pytest.approx(run(0.4,4.0),rel=2e-14)

def test_radius_at_or_inside_half_diameter_is_withheld():
    request=BASE.model_copy(update={'parameters':{
        'outside_diameter_m':0.2,'centerline_bend_radius_m':0.1,
    }})
    result=evaluate_fabrication(request,CTX,client=MaterialClient())
    assert result.status=='out_of_scope'
    assert not result.values
