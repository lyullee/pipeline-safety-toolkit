"""Cathodic charge/current budget integration against exact quadratic demand."""
import runpy
import pytest
from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical

CASES,CTX=runpy.run_path('examples/electrochemical_methods.py')['make_cases']()
BASE=CASES['protection.cathodic_budget']

def evaluate(overrides):
    req=BASE.model_copy(update={'parameters':BASE.parameters|overrides})
    return evaluate_electrochemical(req,CTX)

def test_linear_bare_fraction_and_current_density_product_has_exact_charge_and_peak():
    out=evaluate({'time_s':[0.,4.], 'protected_area_m2':10.,
        'bare_surface_fraction':[0.,1.], 'bare_surface_current_density_A_m2':[1.,0.],
        'additional_current_drain_A':[0.,0.], 'anode_electrochemical_capacity_C_kg':100000.,
        'anode_utilization_fraction':.8,'single_anode_mass_kg':.1,
        'single_anode_minimum_available_current_A':1.,'installed_anodes':3})
    assert out['status']=='computed'
    v=out['values']
    assert v['required_charge_C']==pytest.approx(20./3.,rel=2e-14)
    assert v['mean_current_A']==pytest.approx(5./3.,rel=2e-14)
    assert v['peak_current_A']==pytest.approx(2.5,rel=2e-14)
    assert v['peak_time_s']==pytest.approx(2.0,abs=1e-13)
    assert v['minimum_anodes_by_charge']==1
    assert v['minimum_anodes_by_peak_current']==3
    assert v['minimum_anodes_for_declared_budget']==3

def test_constant_full_surface_current_and_additional_drain_close_charge():
    duration=5.
    out=evaluate({'time_s':[0.,duration], 'protected_area_m2':10.,
        'bare_surface_fraction':[1.,1.], 'bare_surface_current_density_A_m2':[2.,2.],
        'additional_current_drain_A':[.5,.5]})
    v=out['values']
    assert v['peak_current_A']==pytest.approx(20.5,rel=2e-14)
    assert v['mean_current_A']==pytest.approx(20.5,rel=2e-14)
    assert v['required_charge_C']==pytest.approx(20.5*duration,rel=2e-14)

def test_protected_area_scales_integrated_charge_and_peak_current():
    params={'time_s':[0.,4.], 'protected_area_m2':10.,
        'bare_surface_fraction':[0.,1.], 'bare_surface_current_density_A_m2':[1.,0.],
        'additional_current_drain_A':[0.,0.]}
    a=evaluate(params)['values']
    b=evaluate(params|{'protected_area_m2':30.})['values']
    assert b['required_charge_C']==pytest.approx(3*a['required_charge_C'],rel=2e-14)
    assert b['peak_current_A']==pytest.approx(3*a['peak_current_A'],rel=2e-14)
