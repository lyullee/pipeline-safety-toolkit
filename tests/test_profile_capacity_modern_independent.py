"""Measured-profile area solver against a continuous analytic-loss window oracle."""
from copy import deepcopy
import runpy
import numpy as np
import pytest
from scipy.optimize import minimize_scalar
from pipeline_integrity.profile_capacity import evaluate_profile_capacity

MAKE=runpy.run_path('examples/profile_capacity.py')['make_case']

def evaluate_profile(position,thickness,knots,factors):
    q,c=MAKE();p=deepcopy(q.parameters)
    p.update(axial_position_m=list(map(float,position)),measured_remaining_thickness_m=list(map(float,thickness)))
    p['bulging_table'].update(window_length_m=list(map(float,knots)),factor=list(map(float,factors)),
        validity_description='Authored smooth bulging provider sampled on a declared length support')
    q=q.model_copy(update={'parameters':p,'source_point_ids':tuple(f'point-{i}' for i in range(len(position)))})
    result=evaluate_profile_capacity(q,c)
    assert result['status']=='computed',result['issues']
    return result

@pytest.mark.parametrize('depth_fraction',[.2,.45,.65])
def test_parabolic_profile_nodal_scan_converges_to_independent_continuous_window_minimum(depth_fraction):
    knots=np.linspace(0.,1.,1001);factors=1+3*knots**2
    def interpolate(length):
        index=min(int(length*1000),999);fraction=(length-knots[index])/(knots[index+1]-knots[index])
        return factors[index]+fraction*(factors[index+1]-factors[index])
    # For each length the symmetric window maximizes the integral of the
    # concave parabolic loss. Integrating it analytically leaves one free length.
    def reduction(length):
        area_fraction=depth_fraction*(1-length**2/3)
        return (1-area_fraction)/(1-area_fraction/interpolate(length))
    optimum=minimize_scalar(reduction,bounds=(1e-8,1.),method='bounded',options={'xatol':1e-12})
    expected=min(optimum.fun,reduction(1.));errors=[]
    for cells in (8,16,32,64):
        x=np.linspace(0.,1.,cells+1);loss=depth_fraction*(1-4*(x-.5)**2)
        result=evaluate_profile(x,.01*(1-loss),knots,factors)
        ratio=result['values']['capacity_reduction_factor']
        assert ratio>=expected-2e-12
        errors.append(ratio/expected-1)
        assert result['values']['pressure_basis']=='internal_minus_external'
        assert result['values']['scanned_nodal_windows']==cells*(cells+1)//2
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<3e-4

def test_existing_public_profile_api_preserves_uniform_loss_pressure_reference():
    result=evaluate_profile([0.,.5,1.],[.008,.008,.008],[0.,1.],[2.,2.])
    assert result['values']['capacity_reduction_factor']==pytest.approx(8/9,rel=2e-14)
    assert result['values']['declared_model_pressure_difference_capacity_Pa']==pytest.approx(10e6*8/9)
    assert result['field_or_standard_qualification'] is False
