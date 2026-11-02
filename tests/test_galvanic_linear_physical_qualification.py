"""Linear galvanic coupling current and charge-balance identities through owner API."""
import runpy
import pytest
from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical
CASES,CTX=runpy.run_path('examples/electrochemical_methods.py')['make_cases']()
BASE=CASES['corrosion.galvanic_linear']

def solve(**changes):
    p=BASE.model_copy(update={'parameters':BASE.parameters|changes})
    return evaluate_electrochemical(p,CTX)

def test_linear_galvanic_current_and_voltage_drop_close():
    p=BASE.parameters; r=solve(); v=r['values']
    ra=p['anodic_polarization_resistance_ohm_m2']/p['anodic_area_m2']
    rc=p['cathodic_polarization_resistance_ohm_m2']/p['cathodic_area_m2']
    delta=p['cathodic_free_potential_V']-p['anodic_free_potential_V']
    expected=delta/(ra+rc+p['electrolyte_resistance_ohm'])
    assert r['status']=='computed'
    assert v['coupling_current_A']==pytest.approx(expected,rel=2e-14)
    assert v['anodic_current_density_A_m2']*p['anodic_area_m2']==pytest.approx(expected,rel=2e-14)
    assert v['cathodic_current_density_A_m2']*p['cathodic_area_m2']==pytest.approx(expected,rel=2e-14)
    assert v['anodic_overpotential_V']+v['cathodic_overpotential_magnitude_V']+expected*p['electrolyte_resistance_ohm']==pytest.approx(delta,rel=2e-14)

def test_potential_difference_scales_current_linearly_within_linear_domain():
    base=solve()['values']['coupling_current_A']
    doubled=solve(cathodic_free_potential_V=-0.68)['values']['coupling_current_A']
    assert doubled==pytest.approx(2*base,rel=2e-14)

def test_simultaneous_electrode_area_scaling_preserves_current_density_without_electrolyte_resistance():
    p=BASE.parameters|{'electrolyte_resistance_ohm':0.0}
    a=evaluate_electrochemical(BASE.model_copy(update={'parameters':p}),CTX)['values']
    b=evaluate_electrochemical(BASE.model_copy(update={'parameters':p|{'anodic_area_m2':0.2,'cathodic_area_m2':0.4}}),CTX)['values']
    assert b['coupling_current_A']==pytest.approx(2*a['coupling_current_A'],rel=2e-14)
    assert b['anodic_current_density_A_m2']==pytest.approx(a['anodic_current_density_A_m2'],rel=2e-14)
    assert b['cathodic_current_density_A_m2']==pytest.approx(a['cathodic_current_density_A_m2'],rel=2e-14)

def test_reversed_declared_electrode_potential_order_is_withheld():
    r=solve(anodic_free_potential_V=-0.68,cathodic_free_potential_V=-0.70)
    assert r['status']=='out_of_scope'
    assert not r['values']

