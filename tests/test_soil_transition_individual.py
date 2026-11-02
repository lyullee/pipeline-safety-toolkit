from pathlib import Path
import os,math
import matintegrity
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.contracts import RunContext,EvidenceRef
from pipeline_integrity.fully_restrained_cylinder import RestrainedCylinderRequest
from pipeline_integrity.soil_transition_observer import SoilTransitionRequest,evaluate_soil_transition

CTX=RunContext(asset_id='authored-transition',scenario_id='constant-opposing-traction',clock_id='static',origin='synthetic',
    evidence=(EvidenceRef(id='reviewed-inputs',citation='Authored SI isotropic cylinder and supplied constant axial soil traction; no field fit',origin='synthetic'),))


def request(temperature=30.,pressure=3e6,**changes):
    cylinder=RestrainedCylinderRequest(asset_id=CTX.asset_id,location_id='wall',clock_id=CTX.clock_id,
        inner_radius_m=.15,outer_radius_m=.16,internal_pressure_relative_zero_exterior_pa=pressure,
        elastic_modulus_pa=205e9,poisson_ratio=.28,expansion_coefficient_per_k=12e-6,
        uniform_temperature_change_k=temperature,material_evidence_id='reviewed-inputs',
        isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed=True)
    return SoilTransitionRequest(**dict(dict(id='transition',cylinder=cylinder,supplied_constant_axial_resistance_n_m=2400.,
        resistance_evidence_id='reviewed-inputs',uniform_section_constant_opposing_resistance_far_field_transition_reviewed=True),**changes))


def test_actual_selected_immutable_owner_version():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('temperature,pressure',[(-35.,0.),(-30.,3e6),(0.,3e6),(25.,0.),(30.,3e6)])
def test_independent_axial_equilibrium_and_compatibility_event_ode(temperature,pressure):
    q=request(temperature,pressure);c=q.cylinder;r=evaluate_soil_transition(q,CTX);assert r['status']=='computed';v=r['values']
    area=math.pi*(c.outer_radius_m**2-c.inner_radius_m**2)
    free=pressure*math.pi*c.inner_radius_m**2/area
    restrained=2*c.poisson_ratio*free-c.elastic_modulus_pa*c.expansion_coefficient_per_k*temperature
    direction=math.copysign(1.,free-restrained);gradient=direction*q.supplied_constant_axial_resistance_n_m/area
    def ode(x,y):return [gradient,(y[0]-2*c.poisson_ratio*free)/c.elastic_modulus_pa+c.expansion_coefficient_per_k*temperature]
    def event(x,y):return y[0]-free
    event.terminal=True
    independent=solve_ivp(ode,(0,2*abs(free-restrained)/abs(gradient)),[restrained,0.],events=event,rtol=2e-12,atol=[1e-5,1e-12])
    assert v['transfer_length_m']==pytest.approx(independent.t_events[0][0],rel=3e-12)
    assert v['signed_terminal_displacement_m']==pytest.approx(independent.y_events[0][0,1],rel=3e-12,abs=1e-12)
    assert v['free_closed_cap_axial_stress_pa']==pytest.approx(free,rel=2e-13,abs=1e-7)
    assert v['restrained_axial_stress_pa']==pytest.approx(restrained,rel=2e-13,abs=1e-7)
    assert v['signed_distributed_soil_force_n_m']*v['transfer_length_m']+v['free_minus_restrained_material_force_n']==pytest.approx(0.,abs=1e-8)
    assert v['anchored_required_positive_end_restraint_force_n']==pytest.approx(-area*(free-restrained),abs=1e-7)


def test_zero_load_has_zero_length_movement_and_no_fictitious_soil_force():
    v=evaluate_soil_transition(request(0.,0.),CTX)['values']
    assert v['transfer_length_m']==v['signed_terminal_displacement_m']==v['signed_distributed_soil_force_n_m']==0.


def test_resistance_changes_length_and_displacement_but_not_anchor_force():
    first=evaluate_soil_transition(request(),CTX)['values']
    second=evaluate_soil_transition(request(supplied_constant_axial_resistance_n_m=4800.),CTX)['values']
    assert second['transfer_length_m']==first['transfer_length_m']/2
    assert second['signed_terminal_displacement_m']==first['signed_terminal_displacement_m']/2
    assert second['anchored_required_positive_end_restraint_force_n']==first['anchored_required_positive_end_restraint_force_n']


@pytest.mark.parametrize('changes',[dict(supplied_constant_axial_resistance_n_m=None),dict(resistance_evidence_id=None),dict(uniform_section_constant_opposing_resistance_far_field_transition_reviewed=False)])
def test_missing_soil_closure_keeps_far_field_but_not_transition(changes):
    r=evaluate_soil_transition(request(**changes),CTX)
    assert r['status']=='needs_data' and 'transfer_length_m' not in r['values']


def test_missing_soil_evidence_and_invalid_resistance_rejected():
    with pytest.raises(ValueError):evaluate_soil_transition(request(resistance_evidence_id='absent'),CTX)
    with pytest.raises(ValueError):request(supplied_constant_axial_resistance_n_m=0.)
