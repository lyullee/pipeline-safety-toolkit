"""Constructor and invalid-input checks for the remaining mechanical targets.

No successful model, independent numerical reference or regression is executed.
Fixtures are authored values or source-checked constructor-only historical inputs.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT=Path(__file__).resolve().parents[1]
DOCUMENT=json.loads((Path(__file__).parent/'data/mechanics_input_baselines_223.json').read_text(encoding='utf-8'))
CASES=DOCUMENT['cases']
BY_UNIT={c['canonical_unit']:c for c in CASES}


def kind(case):
    module,name=case['contract'].rsplit('.',1)
    return getattr(importlib.import_module(module),name)


def stamp(case,record_property):
    record_property('canonical_units',json.dumps([case['canonical_unit']]))
    record_property('request_contract',case['contract'])


@pytest.mark.parametrize('case',CASES,ids=[c['canonical_unit'] for c in CASES])
def test_complete_constructor_input_baseline(case,record_property):
    stamp(case,record_property)
    q=kind(case).model_validate_json(case['payload_json'])
    assert q.model_dump()
    if case['canonical_unit']=='engineering.target.nut_factor_torque_preload':
        assert q.method=='fabrication.bolt_preload'
    if case['canonical_unit']=='torsion.annular_elastic_section':
        assert 0<q.inside_diameter_m<q.outside_diameter_m


@pytest.mark.parametrize('case',CASES,ids=[c['canonical_unit'] for c in CASES])
def test_specific_invalid_field_rejected_at_its_own_location(case,record_property):
    stamp(case,record_property)
    data=json.loads(case['payload_json'])
    field=case['invalid_field']
    data[field]=case['invalid_value']
    with pytest.raises(ValidationError) as caught:
        kind(case).model_validate_json(json.dumps(data))
    assert any(e['loc']==(field,) for e in caught.value.errors()),caught.value.errors()


NUMERIC_CASES=[c for c in CASES if c['unit_string_value'] is not None]


@pytest.mark.parametrize('case',NUMERIC_CASES,ids=[c['canonical_unit'] for c in NUMERIC_CASES])
def test_physical_quantity_does_not_accept_a_unit_string(case,record_property):
    stamp(case,record_property)
    data=json.loads(case['payload_json'])
    field=case['invalid_field']
    data[field]=case['unit_string_value']
    with pytest.raises(ValidationError) as caught:
        kind(case).model_validate_json(json.dumps(data))
    assert any(e['loc']==(field,) and e['type'] in ('float_type','float_parsing','int_type','int_parsing')
               for e in caught.value.errors()),caught.value.errors()


@pytest.mark.parametrize('case',CASES,ids=[c['canonical_unit'] for c in CASES])
def test_unknown_unit_system_key_is_not_silently_accepted(case,record_property):
    stamp(case,record_property)
    data=json.loads(case['payload_json'])|{'units':'imperial'}
    with pytest.raises(ValidationError) as caught:
        kind(case).model_validate_json(json.dumps(data))
    assert any(e['loc']==('units',) and e['type']=='extra_forbidden' for e in caught.value.errors())


RELATIONS=[
 ('beam.linear_dynamic_euler_bernoulli',{'density_by_material_kg_m3':{}},'one explicit density'),
 ('buried_upheaval_buckling',{'source_port_id':'same','target_port_id':'same'},'buckling ports must be distinct'),
 ('elastic_thermal_compression_buckling_variant',{'source_port_id':'same','target_port_id':'same'},'buckling ports must be distinct'),
 ('contact.prescribed_pressure_coulomb_axial',{'mode':'static_bound','coefficient_basis':'kinetic'},'static and kinetic friction coefficients'),
 ('elasticity.closed_pressure_cylinder',{'evaluation_radii_m':[.1,.1]},'section radii strictly span'),
 ('engineering.target.covered_pipe_flotation_balance',{'inner_diameter_m':.3},'inner diameter must be smaller'),
 ('engineering.target.soil_prism_overburden',{'burial_depth_m':1.,'water_head_m':2.},'water level above the ground'),
 ('engineering.target.strouhal_frequency_screen',{'natural_frequency_Hz':1.,'relative_nearness_band':None},'must be supplied together'),
 ('fracture.mode_i_coordinate',{'minimum_supported_depth_m':.1,'maximum_supported_depth_m':.01},'geometry-factor support is reversed'),
 ('ground_movement_pipe_response',{'source_port_id':'same','target_port_id':'same'},'distinct ports'),
 ('mechanical.prescribed_pressure_work_actuation',{'positive_extension_axis':[2.,0.,0.]},'explicit unit directions'),
 ('plasticity.perfect_plastic_cylinder',{'outside_radius_m':.001},'positive annular wall'),
 ('rigging.prescribed_guy_equilibrium',{'horizontal_axis_in_load_plane':[2.,0.,0.]},'explicit orthonormal'),
 ('shell.short_stiff_pipe_supplied_membrane_yield_reference',{'outside_diameter_m':.1,'wall_thickness_m':.06},'positive internal diameter'),
 ('shell.supplied_intermediate_collapse_correlation',{'outside_diameter_m':.1,'wall_thickness_m':.06},'positive internal diameter'),
 ('torsion.annular_elastic_section',{'length_m':1.,'shear_modulus_pa':None},'length and shear modulus'),
]


@pytest.mark.parametrize('unit,updates,reason',RELATIONS,ids=[u for u,_,_ in RELATIONS])
def test_declared_cross_field_rule_has_specific_rejection(unit,updates,reason,record_property):
    case=BY_UNIT[unit]
    stamp(case,record_property)
    data=json.loads(case['payload_json'])|updates
    with pytest.raises(ValidationError) as caught:
        kind(case).model_validate_json(json.dumps(data))
    assert any(e['loc']==() and reason in e['msg'] for e in caught.value.errors()),caught.value.errors()


NATIVE_CASES=[
 ('damage.fatigue_cycle_fraction','MinerDamage',{'cycle_counts':[10.,20.],'cycles_to_failure':[100.,200.],
     'declared_damage_limit':1.},'cycles_to_failure','counts and fatigue lives must align'),
 ('damage.creep_rupture_fraction','RobinsonDamage',{'exposure_s':[10.,20.],'rupture_life_s':[100.,200.]},
     'rupture_life_s','aligned creep intervals and rupture lives required'),
]


def owner_kind(name):
    source=Path(os.environ.get('PIPELINE_MATERIAL_SRC',str(ROOT.parent.parent/'재료_부식 평가 툴 프로젝트/matintegrity/src')))
    if source.is_dir() and str(source) not in sys.path:sys.path.insert(0,str(source))
    return getattr(importlib.import_module('matintegrity.models.fatigue'),name)


@pytest.mark.parametrize('unit,name,data,field,reason',NATIVE_CASES,ids=[c[0] for c in NATIVE_CASES])
def test_native_scalar_owner_valid_input_without_owner_evaluation(unit,name,data,field,reason,record_property):
    record_property('canonical_units',json.dumps([unit]))
    cls=owner_kind(name)
    q=cls.model_validate(data)
    assert q.model_dump()==data


@pytest.mark.parametrize('unit,name,data,field,reason',NATIVE_CASES,ids=[c[0] for c in NATIVE_CASES])
def test_native_owner_array_alignment_rejected_at_constructor(unit,name,data,field,reason,record_property):
    record_property('canonical_units',json.dumps([unit]))
    with pytest.raises(ValidationError) as caught:
        owner_kind(name).model_validate(data|{field:[100.]})
    assert any(e['loc']==() and reason in e['msg'] for e in caught.value.errors())


@pytest.mark.parametrize('unit,name,data,field,reason',NATIVE_CASES,ids=[c[0] for c in NATIVE_CASES])
@pytest.mark.parametrize('bad',[0.,-1.,'100 cycles'],ids=['zero-life','negative-life','unit-string'])
def test_native_owner_life_input_has_specific_member_error(unit,name,data,field,reason,bad,record_property):
    record_property('canonical_units',json.dumps([unit]))
    with pytest.raises(ValidationError) as caught:
        owner_kind(name).model_validate(data|{field:[bad,200.]})
    assert any(e['loc']==(field,0) for e in caught.value.errors())


@pytest.mark.parametrize('field,bad,reason',[('mass_kg',-1.,'mass and stiffness must be positive'),
    ('damping_N_s_m',-1.,'must be nonnegative'),('mass_kg','1 kg','mass_kg must be a finite real number')])
def test_support_oscillator_constructor_inputs_only(field,bad,reason,record_property):
    from pipeline_integrity.harmonic_response import HarmonicOscillator
    record_property('canonical_units',json.dumps(['support_force_movement']))
    values=dict(mass_kg=10.,stiffness_N_m=1000.,damping_N_s_m=1.,force_amplitude_N=20.,angular_frequency_rad_s=2.)
    assert HarmonicOscillator(**values).mass_kg==10.
    with pytest.raises(ValueError,match=reason):
        HarmonicOscillator(**(values|{field:bad}))
