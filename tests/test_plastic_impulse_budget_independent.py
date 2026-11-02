"""Independent plastic strain-work and sheet momentum/kinetic work budget."""
import pytest
from scipy.integrate import quad
from pipeline_integrity.plastic_impulse_budget import PlasticImpulseBudgetRequest as Request,evaluate_plastic_impulse_budget as evaluate

def req(rho=7800.,flow=300e6,t=.01):
    return Request(wall_thickness_m=t,material_mass_density_kg_m3=rho,supplied_flow_stress_pa=flow,supplied_failure_work_strain=.1,
        pressure_pulse_duration_s=.002,supplied_impulse_to_peak_pressure_duration_factor=1.,material_work_strain_pulse_shape_and_unit_basis_id='authored-constant-flow-sheet',
        uniform_areal_mass_constant_flow_stress_and_strain_work_reviewed=True)

@pytest.mark.parametrize('rho',[1000.,2700.,7800.])
@pytest.mark.parametrize('flow',[10e6,100e6,300e6])
def test_strain_work_equals_kinetic_energy_from_pressure_impulse(rho,flow):
    q=req(rho,flow);r=evaluate(q)
    plastic=quad(lambda strain:flow*q.wall_thickness_m,0,q.supplied_failure_work_strain)[0]
    pulse_impulse=quad(lambda time:r['peak_pressure_coordinate_pa'],0,q.pressure_pulse_duration_s)[0]
    velocity=pulse_impulse/(rho*q.wall_thickness_m)
    kinetic=quad(lambda speed:rho*q.wall_thickness_m*speed,0,velocity)[0]
    assert kinetic==pytest.approx(plastic,rel=1e-12)
    assert r['actual_fragmentation_or_failure_strain_calibration_qualified'] is False

def test_triangular_shape_peak_preserves_impulse_budget():
    q=req().model_copy(update={'supplied_impulse_to_peak_pressure_duration_factor':.5});r=evaluate(q);T=q.pressure_pulse_duration_s
    assert quad(lambda time:r['peak_pressure_coordinate_pa']*(1-time/T),0,T)[0]==pytest.approx(r['impulse_budget_pa_s'])

def test_areal_thickness_scaling_preserves_specific_material_work_and_velocity():
    a=evaluate(req(t=.01));b=evaluate(req(t=.02))
    assert b['peak_pressure_coordinate_pa']==pytest.approx(2*a['peak_pressure_coordinate_pa'])
    assert b['impulse_budget_pa_s']/b['areal_mass_kg_m2']==pytest.approx(a['impulse_budget_pa_s']/a['areal_mass_kg_m2'])

def test_unknown_failure_work_strain_is_not_inferred():
    assert evaluate(req().model_copy(update={'supplied_failure_work_strain':None}))['status']=='needs_data'
