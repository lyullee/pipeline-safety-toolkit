"""Input-only tests: constructors and pre-computation rejection, no solvers."""
import copy
import json

import pytest
from pydantic import ValidationError

from pipeline_integrity import surface_flaw_proof
from pipeline_integrity.constant_loss_life import ConstantLossLifeRequest, evaluate_constant_loss_life
from pipeline_integrity.neutral_surface_bending import NeutralSurfaceBendingRequest, evaluate_neutral_surface_bending
from pipeline_integrity.shaft_torsion_observer import ShaftTorsionRequest, evaluate_shaft_torsion
from pipeline_integrity.surface_flaw_proof import SurfaceFlawProofRequest, evaluate_surface_flaw_proof
from pipeline_integrity.time_dependent_tearing import PressureHistoryTearingRequest, evaluate_pressure_history_tearing
from pipeline_integrity.weld_wall_thermal_limit import WeldHeatStage, WeldWallThermalLimitRequest, evaluate_weld_wall_thermal_limit


BASELINES = {
    'neutral': (NeutralSurfaceBendingRequest, evaluate_neutral_surface_bending, dict(
        basis_id='geometry', neutral_radius_m=2., thickness_m=.02,
        neutral_distance_from_inner_face_m=.006, reference_arc_length_m=1.)),
    'loss': (ConstantLossLifeRequest, evaluate_constant_loss_life, dict(
        basis_id='wall', current_remaining_wall_m=.01,
        supplied_critical_remaining_wall_m=.005, supplied_penetration_rate_m_s=1e-10)),
    'shaft': (ShaftTorsionRequest, evaluate_shaft_torsion, dict(
        id='shaft', outside_diameter_m=.1, signed_torque_nm=50., signed_angular_velocity_rad_s=2.)),
    'proof': (SurfaceFlawProofRequest, evaluate_surface_flaw_proof, dict(
        basis_id='curve', flaw_family_id='flaw', material_condition_id='material',
        wall_thickness_m=.02, depth_m=(.001,.004,.008),
        failure_pressure_pa=(20e6,15e6,10e6), proof_pressure_pa=18e6,
        maximum_operating_pressure_pa=12e6)),
    'tearing': (PressureHistoryTearingRequest, evaluate_pressure_history_tearing, dict(
        basis_id='tearing', material_condition_id='material', flaw_family_id='flaw',
        calibration_evidence_id='calibration', time_s=(0.,1.,2.),
        differential_pressure_pa=(10e6,5e6,10e6), depth_grid_m=(.001,.004,.009),
        pressure_grid_pa=(0.,10e6,20e6),
        supplied_driving_j_per_m2=((0.,100.,200.),(0.,120.,240.),(0.,150.,300.)),
        supplied_resistance_j_per_m2=(100.,120.,150.),
        supplied_projected_crack_area_m2=(.0001,.0004,.0009),
        initial_depth_m=.002, rupture_depth_m=.008, wall_thickness_m=.02,
        initial_peak_pressure_pa=10e6, initially_open=True,
        closing_peak_pressure_fraction=.2, reopening_peak_pressure_fraction=.7,
        supplied_rate_coefficient_m_s=1e-6, supplied_rate_exponent=2.,
        maximum_reviewed_driving_to_resistance_ratio=5., temperature_k=300.,
        minimum_calibrated_temperature_k=280., maximum_calibrated_temperature_k=350.,
        maximum_step_s=.1)),
    'weld': (WeldWallThermalLimitRequest, evaluate_weld_wall_thermal_limit, dict(
        basis_id='thermal', inside_radius_m=.2, wall_thickness_m=.01,
        conductivity_w_m_k=40., density_kg_m3=7800., specific_heat_j_kg_k=500.,
        initial_temperature_k=300., supplied_inner_surface_ceiling_k=1200.,
        stages=(WeldHeatStage(duration_s=1.,effective_outer_heat_flux_w_m2=1000.,
                              gas_temperature_k=300.,gas_film_w_m2_k=20.),), maximum_step_s=.1)),
}

UNIT_MEMBERSHIPS = {
    'neutral':'fabrication.supplied_neutral_axis_curvature',
    'loss':'damage.prescribed_constant_loss_time',
    'shaft':'shaft.material_capacity',
    'proof':'fracture.surface_flaw_proof_survival',
    'tearing':'fracture.surface_flaw_proof_survival',
    'weld':'welding.supplied_burnthrough_limit',
}


@pytest.fixture(autouse=True)
def canonical_input_membership(request,record_property):
    params=getattr(getattr(request.node,'callspec',None),'params',{})
    name=params.get('name','proof')
    record_property('canonical_units',json.dumps([UNIT_MEMBERSHIPS[name]]))


def baseline(name):
    cls, _, data = BASELINES[name]
    return cls.model_validate(copy.deepcopy(data))


@pytest.mark.parametrize('name', tuple(BASELINES))
def test_input_valid_complete_baseline_constructs_and_review_gate_withholds(name):
    q = baseline(name)
    assert q.model_dump()
    assert BASELINES[name][1](q)['status'] == 'needs_data'


REQUIRED = [(name, field) for name,(cls,_,_) in BASELINES.items()
            for field,info in cls.model_fields.items() if info.is_required()]


@pytest.mark.parametrize('name,field', REQUIRED, ids=[f'{n}-missing-{f}' for n,f in REQUIRED])
def test_input_each_required_field_missing_is_identified(name, field):
    data = baseline(name).model_dump()
    del data[field]
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][0].model_validate(data)
    assert any(e['loc'] == (field,) and e['type'] == 'missing' for e in caught.value.errors())


LEAVES = [('neutral','neutral_radius_m'),('loss','current_remaining_wall_m'),
          ('shaft','outside_diameter_m'),('proof','proof_pressure_pa'),
          ('tearing','maximum_step_s'),('weld','conductivity_w_m_k')]


@pytest.mark.parametrize('name,field',LEAVES)
@pytest.mark.parametrize('bad', [0.,-1.,float('nan'),float('inf'),'12 mm',None],
                         ids=['zero','negative','nan','infinity','unit-string','null'])
def test_input_invalid_leaf_identifies_requested_field(name, field, bad):
    data = baseline(name).model_dump()
    data[field] = bad
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][0].model_validate(data)
    assert any(e['loc'] == (field,) for e in caught.value.errors())


@pytest.mark.parametrize('name,field',LEAVES)
def test_input_api_revalidates_untrusted_model_copy_before_review_gate(name, field):
    q = baseline(name).model_copy(update={field: -1.})
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][1](q)
    assert any(e['loc'] == (field,) for e in caught.value.errors())


@pytest.mark.parametrize('name',tuple(BASELINES))
def test_input_unrecognized_units_key_is_not_accepted(name):
    data = baseline(name).model_dump() | {'units': 'imperial'}
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][0].model_validate(data)
    assert any(e['loc'] == ('units',) and e['type'] == 'extra_forbidden' for e in caught.value.errors())


CROSS = [
    ('neutral', {'neutral_distance_from_inner_face_m': .03}, 'neutral surface must lie inside'),
    ('neutral', {'neutral_radius_m': .003}, 'inside-face radius must be positive'),
    ('neutral', {'signed_curvature_direction': 0}, 'curvature direction must be'),
    ('shaft', {'inside_diameter_m': .11}, 'positive circular section required'),
    ('shaft', {'length_m': 1.}, 'length and shear modulus'),
    ('proof', {'failure_pressure_pa': (20e6,15e6)}, 'each flaw depth requires'),
    ('proof', {'depth_m': (.001,.001,.008)}, 'flaw depths must strictly increase'),
    ('proof', {'failure_pressure_pa': (10e6,15e6,20e6)}, 'failure pressure must not increase'),
    ('proof', {'maximum_operating_pressure_pa': 19e6}, 'operating pressure exceeds'),
    ('proof', {'depth_m': (.001,.004,.02)}, 'below through-wall depth'),
    ('tearing', {'time_s': (0.,0.,2.)}, 'time_s must strictly increase'),
    ('tearing', {'differential_pressure_pa': (10e6,5e6)}, 'one differential pressure'),
    ('tearing', {'depth_grid_m': (.001,.001,.009)}, 'depth_grid_m must strictly increase'),
    ('tearing', {'pressure_grid_pa': (0.,0.,20e6)}, 'pressure_grid_pa must strictly increase'),
    ('tearing', {'differential_pressure_pa': (10e6,5e6,21e6)}, 'pressure history exceeds'),
    ('tearing', {'supplied_driving_j_per_m2': ((0.,100.),)}, 'driving map is depth-by-pressure'),
    ('tearing', {'supplied_resistance_j_per_m2': (100.,120.)}, 'driving map is depth-by-pressure'),
    ('tearing', {'supplied_projected_crack_area_m2': (.0001,.0001,.0009)}, 'projected crack area must strictly increase'),
    ('tearing', {'initial_peak_pressure_pa': 9e6}, 'peak-pressure memory cannot precede'),
    ('tearing', {'closing_peak_pressure_fraction': .8}, 'closing threshold must be below'),
    ('tearing', {'initially_open': False}, 'initially closed state contradicts'),
    ('tearing', {'initial_peak_pressure_pa': 100e6}, 'initially open state contradicts'),
    ('tearing', {'temperature_k': 360.}, 'temperature lies outside'),
    ('tearing', {'initial_depth_m': .0085}, 'initial, rupture and table depths'),
]


@pytest.mark.parametrize('name,updates,message',CROSS,ids=[f'{n}-{next(iter(u))}' for n,u,_ in CROSS])
def test_input_cross_field_consistency_reports_exact_reason(name, updates, message):
    data = baseline(name).model_dump() | updates
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][0].model_validate(data)
    errors = caught.value.errors()
    assert any(e['loc'] == () and message in e['msg'] for e in errors)


@pytest.mark.parametrize('field,bad', [('duration_s',0.),('gas_temperature_k',-1.),
                                    ('gas_film_w_m2_k',-1.),('effective_outer_heat_flux_w_m2',float('nan'))])
def test_input_weld_nested_stage_error_preserves_member_location(field,bad):
    data = baseline('weld').model_dump()
    data['stages'] = ({**data['stages'][0],field:bad},)
    with pytest.raises(ValidationError) as caught:
        WeldWallThermalLimitRequest.model_validate(data)
    assert any(e['loc'] == ('stages',0,field) for e in caught.value.errors())


@pytest.mark.parametrize('name,field', [('neutral','plane_sections_and_circular_neutral_surface_reviewed'),
    ('loss','constant_loss_and_critical_boundary_reviewed'),
    ('shaft','homogeneous_circular_saint_venant_torsion_reviewed'),
    ('proof','failure_pressure_curve_applicability_reviewed'),
    ('tearing','pressure_map_resistance_kinetics_closure_and_area_basis_reviewed'),
    ('weld','radial_constant_property_heat_flux_and_film_basis_reviewed')])
def test_input_review_flags_require_actual_boolean(name,field):
    with pytest.raises(ValidationError) as caught:
        BASELINES[name][0].model_validate(baseline(name).model_dump() | {field:'yes'})
    assert any(e['loc'] == (field,) and e['type'] == 'bool_type' for e in caught.value.errors())


@pytest.mark.parametrize('field,bad', [('flaw_family_id','other-flaw'),
                                    ('material_condition_id','other-material'),('wall_thickness_m',.03)])
def test_input_proof_tearing_identity_mismatch_rejected_before_observation(monkeypatch,field,bad):
    proof = baseline('proof').model_copy(update={'tearing_curve_compatibility_reviewed':True})
    tearing = baseline('tearing').model_copy(update={field:bad})
    def unexpected_computation(*args,**kwargs):
        raise AssertionError('input identity rejection must precede observation or external solver')
    monkeypatch.setattr(surface_flaw_proof,'evaluate_surface_flaw_proof',unexpected_computation)
    with pytest.raises(ValueError,match='proof and tearing flaw/material/wall identities differ'):
        surface_flaw_proof.bind_proof_limited_tearing(proof,tearing)
