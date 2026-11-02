"""Valid request baselines and input mutations for the remaining 63 fluid units.

Only request construction/validation runs. Existing fixture factories are
extracted with their constructor-only helpers; numerical API imports become
sentinels. No valid model solve or thermodynamic provider is invoked.
"""
from __future__ import annotations
import ast
from copy import deepcopy
import importlib
import json
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

ROOT=Path(__file__).resolve().parents[1]
REUSED_FACTORY_FILES=set()
INPUT_ASSERTIONS={}


def _no_numeric(*args,**kwargs):
    pytest.fail('input-only fixture attempted a numerical model call')


def _factory(filename,function,*args,helpers=(),**kwargs):
    path=ROOT/'tests'/filename
    REUSED_FACTORY_FILES.add(str(path))
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    keep=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]
    for node in tree.body:
        if isinstance(node,ast.Import):
            aliases=[a for a in node.names if a.name in ('math','numpy','json')]
            if aliases:keep.append(ast.Import(names=aliases))
        elif isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('pipeline_integrity'):
            keep.append(node)
        elif isinstance(node,ast.Assign):
            try:ast.literal_eval(node.value)
            except (ValueError,TypeError):continue
            keep.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name in (function,*helpers):
            node.decorator_list=[];keep.append(node)
    namespace={'__file__':str(path)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=keep,type_ignores=[])),str(path),'exec'),namespace)
    for name,value in list(namespace.items()):
        if callable(value) and not isinstance(value,type) and name.startswith(
                ('evaluate_','solve_','observe_','simulate_','reconstruct_','verify_','fit_','reduce_')):
            namespace[name]=_no_numeric
    result=namespace[function](*args,**kwargs)
    return result[0] if isinstance(result,tuple) else result


def _new(name,**data):
    module,attribute=name.rsplit('.',1)
    return getattr(importlib.import_module('pipeline_integrity.'+module),attribute)(**data)


def _example(name):
    path=ROOT/'examples'/name
    REUSED_FACTORY_FILES.add(str(path))
    if name=='soil_fem.py':
        REUSED_FACTORY_FILES.add(str(ROOT/'examples/soil_cross_section.py'))
    # Both reviewed examples guard their numeric entrypoint under __main__.
    # reviewed=False avoids optional owner/backend access in the heated example.
    module=runpy.run_path(str(path),run_name='input_fixture_only')
    return module['make_case'](reviewed=False)[0]


def _gas_correlation(method):
    return _new('models.hydraulics.GasCorrelation',method=method,relative_density_to_air=.65,
        mean_temperature_k=300.,mean_compressibility=1.,length_m=1000.,diameter_m=.1,
        inlet_pressure_pa_abs=500000.,outlet_pressure_pa_abs=300000.,
        standard_temperature_k=288.15,standard_pressure_pa_abs=101325.,empirical_efficiency=.9)


def _gas_path():
    return _new('gas_potential_network.GasPotentialPathRequest',id='path',asset_id='gas',clock_id='steady',
        fluid='Methane',property_basis_id='EOS',diameter_m=.1,inlet_pressure_Pa_abs=500000.,
        inlet_temperature_K=300.,outlet_pressure_Pa_abs=490000.,segments=(dict(id='s1',length_m=10.,
            end_minus_start_elevation_m=0.,roughness_m=0.,evidence_id='review'),),
        pressure_support_Pa_abs=(100000.,1000000.),temperature_support_K=(250.,400.),
        positive_mass_flow_support_kg_s=(.001,1.),initial_mass_flow_kg_s=.1,
        initial_node_pressures_Pa_abs=(490000.,),initial_node_temperatures_K=(300.,),
        expected_owner_pipe_sha256='a'*64,absolute_momentum_tolerance_Pa=.01,
        absolute_total_energy_tolerance_W=.01,evidence_ids=('review',))


def _transient():
    history=dict(time_s=(0.,),values=(30.,))
    return _new('transient.LiquidTransient',nodes=(dict(id='a',elevation_m=0.,initial_head_m=30.,
        boundary='head',history=history),dict(id='b',elevation_m=0.,initial_head_m=30.,boundary='head',history=history)),
        pipes=(dict(id='pipe',start_node='a',end_node='b',length_m=100.,diameter_m=.1,
            wave_speed_m_s=1000.,darcy_friction_factor=.02,initial_volume_flow_m3_s=0.),),
        duration_s=1.,requested_timestep_s=.01,density_kg_m3=1000.,ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2000.)


def _pump_thermal():
    q=_example('heated_transport.py')
    return _new('heated_operating_point.HeatedPumpOperatingPointRequest',id='pump',transport=q,
        downstream_pressure_pa_abs=900000.,mass_flow_bracket_kg_s=(.5,1.5),
        mapping_review_evidence_id='curve',diagnostic_model_and_selected_bracket_reviewed=True,
        pump_curve=dict(volume_flow_m3_s=(.0001,.001,.01),head_m=(100.,80.,10.),source_id='curve'),
        upstream_pressure_pa_abs=1000000.,curve_reference_density_kg_m3=1000.,constant_density_curve_mapping_reviewed=True)


CASES=[]
def add(unit,factory):
    CASES.append((unit,factory))

add('batch.effective_longitudinal_dispersion',lambda:_new('batch_dispersion.BatchInterfaceRequest',
    axial_positions_m=(0.,1.,2.),elapsed_time_s=1.,axial_dispersion_m2_s=.1,mean_velocity_m_s=1.))
add('batch.supplied_turbulent_dispersion_closure',lambda:_new('batch_dispersion.AxialDispersionRequest',
    method='turbulent_taylor',mean_velocity_m_s=1.,internal_diameter_m=.1,darcy_friction_factor=.02,
    reynolds_number=50000.,turbulent_calibration_and_similar_properties_reviewed=True))
add('blower.supplied_reduced_similarity',lambda:_factory('test_blower_similarity_individual.py','case'))
add('compression.real_process_and_stage_path',lambda:_factory('test_real_compression_path_independent.py','request',helpers=('stage','cooler')))
add('compressor.clearance_efficiency',lambda:_factory('test_gas_clearance_intake_individual.py','request'))
add('compressor.fixed_geometry_speed_head',lambda:_new('compressor_speed_head.CompressorSpeedHeadRequest',
    stages=(dict(stage_id='s1',nominal_polytropic_specific_head_j_per_kg=10000.,nominal_head_basis='polytropic_specific_head',
        impeller_diameter_m=.5,reference_inlet_sound_speed_m_s=500.,target_inlet_sound_speed_m_s=500.,
        reference_inlet_phase='gas',target_inlet_phase='gas',reviewed_tip_mach_support=(.05,.9),
        map_and_inlet_state_basis_id='map',fixed_geometry_reduced_flow_reynolds_and_head_efficiency_support_reviewed=True),),
    mode='speed_to_head',reference_shaft_speed_rpm=3000.,target_shaft_speed_rpm=3300.))
add('compressor.ideal_compression_work',lambda:_new('compression_process.IdealGasCompressionProcess',
    inlet_pressure_pa_abs=100000.,outlet_pressure_pa_abs=200000.,inlet_temperature_k=300.,
    specific_gas_constant_j_kg_k=287.,polytropic_exponent=1.4,mass_flow_kg_s=1.,constant_cp_j_kg_k=1005.))
add('compressor.internal_recycle_inlet_enthalpy',lambda:_factory('test_compressor_recycle_individual.py','request'))
add('compressor.labyrinth_series_cavity_leakage',lambda:_factory('test_serial_gas_cavities_individual.py','request',helpers=('tooth',)))
add('compressor.observed_external_energy_loss_balance',lambda:_factory('test_compressor_external_energy_individual.py','request',helpers=('port',)))
add('free_surface.circular_saint_venant',lambda:_new('gravity_free_surface_pipe.CircularFreeSurfaceSettings',length_m=10.,inner_diameter_m=.5,duration_s=1.))
add('free_surface.circular_uniform_friction_balance',lambda:_new('gravity_uniform_flow.UniformChezyRequest',
    wetted_area_m2=1.,wetted_perimeter_m=3.,signed_downhill_bed_slope=.002,chezy_coefficient_m_half_per_s=30.))
add('gas.conditional_legacy_blowdown_estimator',lambda:_new('gas_restricted_vent.RestrictedVentBlowdownSpec',
    geometric_vent_area_m2=.0001,discharge_coefficient=.8,ambient_pressure_pa_abs=100000.,
    target_maximum_pipe_pressure_pa_abs=150000.,quasisteady_ideal_small_vent_and_one_way_support_reviewed=True))
add('gas.conservative_rigid_pipe_1d',lambda:_new('gas_acoustic_pipe.GasAcousticPipeSettings',length_m=10.,inner_diameter_m=.1,duration_s=1.))
add('gas.dilute_binary_molecular_diffusivity',lambda:_factory('test_binary_gas_diffusion_independent.py','request'))
for unit,method in (('gas.empirical_capacity_alternatives','Weymouth'),('gas.empirical_panhandle_a_flat','Panhandle_A'),
                    ('gas.empirical_panhandle_b_flat','Panhandle_B'),('gas.empirical_weymouth_flat','Weymouth')):
    add(unit,lambda method=method:_gas_correlation(method))
add('gas.ideal_reversible_expansion_work',lambda:_new('pressure_tests.GasExpansionInputParameters',
    initial_absolute_pressure_Pa=200000.,final_absolute_pressure_Pa=100000.,initial_volume_m3=.1,heat_capacity_ratio=1.4))
add('gas.isentropic_nozzle',lambda:_new('models.library_models.ReliefGas',mass_flow_kg_s=1.,temperature_k=300.,
    compressibility=1.,molar_mass_kg_mol=.028,heat_capacity_ratio=1.4,relieving_pressure_pa_abs=500000.,
    back_pressure_pa_abs=100000.,discharge_coefficient=.8,back_pressure_factor=1.,combination_factor=1.))
add('gas.low_pressure_spitzglass',lambda:_new('low_pressure_gas.LowPressureSpitzglassRequest',relative_density_to_air=.65,
    mean_temperature_k=300.,mean_compressibility=1.,length_m=150.,diameter_m=.1,inlet_pressure_pa_abs=102325.,
    outlet_pressure_pa_abs=101325.,standard_temperature_k=288.15,standard_pressure_pa_abs=101325.,
    empirical_efficiency=1.,ambient_pressure_pa_abs=101325.,low_pressure_uniform_state_domain_reviewed=True))
add('gas.panhandle_network_equivalence',lambda:_factory('test_pipe_resistance_reduction_independent.py','request',
    'series',((3.,.1,2.),(5.,.15,2.))))
add('gas.receiver_start_inventory_size',lambda:_factory('test_gas_receiver_sizing_individual.py','make'))
add('gas.squared_pressure_mean',lambda:_new('gas_profile_average.SquaredPressureProfileMeanRequest',
    first_pressure_pa_abs=200000.,second_pressure_pa_abs=100000.,squared_pressure_linear_in_distance_reviewed=True))
add('gas.steady_pipe_total_energy_path',_gas_path)
add('hydraulics.circular_pipe_friction',lambda:_new('models.hydraulics.LiquidPressureLoss',diameter_m=.1,length_m=10.,
    roughness_m=0.,volume_flow_m3_s=.001,density_kg_m3=1000.,viscosity_pa_s=.001))
add('hydraulics.concentric_annulus_loss',lambda:_new('annular_flow.ConcentricAnnulusLossRequest',
    outer_flow_radius_m=.1,inner_body_radius_m=.05,length_m=10.,volume_flow_m3_s=.001,density_kg_m3=1000.,
    closure='newtonian_laminar',dynamic_viscosity_Pa_s=.1,maximum_reviewed_laminar_reynolds=2000.,
    radial_positions_m=(.05,.075,.1),fully_developed_no_slip_laminar_domain_reviewed=True))
add('hydraulics.nonisothermal_viscous_pipe',lambda:_example('heated_transport.py'))
add('hydraulics.relief_system_total_resistance',lambda:_new('relief_line_capacity.FannoReliefLineRequest',
    reservoir_stagnation_pressure_Pa_abs=500000.,reservoir_stagnation_temperature_K=300.,back_pressure_Pa_abs=100000.,
    flow_area_m2=.01,integrated_darcy_friction=.5,specific_gas_constant_J_kg_K=287.,heat_capacity_ratio=1.4,
    friction_basis_id='distributed-darcy'))
for unit in ('hydraulics.series_local_resistance','hydraulics.series_parallel_network','pipe.hose_mises_friction'):
    add(unit,lambda:_factory('test_liquid_path_system_independent.py','request',helpers=('paths','loss')))
add('hydraulics.supplied_drag_reduction',lambda:_factory('test_drag_reduction_independent.py','request'))
add('hydrostatics.coated_pipe_effective_weight',lambda:_factory('test_coated_pipe_weight_individual.py','request'))
add('hydrostatics.density_gravity_column',lambda:_new('models.hydraulics.Hydrostatic',
    inlet_pressure_pa_abs=200000.,density_kg_m3=1000.,outlet_minus_inlet_elevation_m=1.))
add('liquid.declared_saturated_mixed_circular_flow',lambda:_factory('test_saturated_mixed_pipe_independent.py','settings'))
add('liquid.fixed_inertance_column_dynamics',lambda:_factory('test_trapped_gas_column_individual.py','case'))
add('liquid.homogeneous_mixture_compliance_celerity',lambda:_factory('test_mixture_wave_compliance_individual.py','case'))
add('liquid.linear_acoustic_characteristics',lambda:_factory('test_liquid_column_collision_independent.py','collision'))
add('liquid.linear_acoustic_pipe_field',_transient)
add('liquid.linear_elastic_compliance_celerity',lambda:_new('models.hydraulics.WaveSpeed',density_kg_m3=1000.,
    fluid_bulk_modulus_pa=2e9,pipe_elastic_modulus_pa=2e11,inner_diameter_m=.1,wall_thickness_m=.005,restraint_coefficient=1.))
add('liquid.prescribed_growing_inventory',lambda:_factory('test_prescribed_pipe_fill_individual.py','case'))
add('liquid.supplied_exponential_viscosity',lambda:_new('liquid_viscosity.ExponentialViscosityLaw',
    material_basis_id='oil',reference_temperature_K=300.,reference_kinematic_viscosity_m2_s=.00002,
    temperature_coefficient_per_K=.02,temperature_support_K=(270.,360.)))
add('pipe.karman_radial_profile',lambda:_factory('test_karman_radial_profile_individual.py','case'))
add('pipe.prescribed_pressure_floor_route',lambda:_factory('test_saturation_head_route_individual.py','case'))
add('pipe.state_dependent_quasisteady_friction',_transient)
add('pipe.supplied_wall_friction_modifier',lambda:_new('turbulent_constitutive_friction.TurbulentResistanceRequest',
    model='wall_interaction',reynolds_coordinate=50000.,relative_roughness=.001,wall_interaction_parameter=25.,
    turbulent_regime_coordinate_and_calibration_reviewed=True))
add('pump.available_npsh_budget',lambda:_new('models.equipment.PumpNPSH',surface_pressure_pa_abs=100000.,
    vapour_pressure_pa_abs=2000.,density_kg_m3=1000.,surface_above_pump_m=1.,suction_head_loss_m=.2,vendor_required_npsh_m=1.))
add('pump.fixed_geometry_speed_affinity',lambda:_factory('test_pump_curve_transform_independent.py','request'))
add('pump.pipe_head_operating_point',lambda:_new('models.pump_curves.PumpSystem',
    reference_curve=dict(volume_flow_m3_s=(0.,.001,.01),head_m=(100.,80.,10.),source_id='curve'),
    static_head_m=10.,resistance_head_s2_m5=1000000.,parallel_pumps=1,series_pumps=1,
    density_kg_m3=1000.,pump_efficiency=.8,motor_efficiency=.9))
add('pump.pressure_or_head_duty_power',lambda:_new('pump_pressure_duty.PumpPressureDutyRequest',volume_flow_m3_s=.01,
    differential_pressure_pa=500000.,pump_efficiency=.8,upstream_drive_efficiency=.9,
    upstream_drive_boundary='electrical_input',incompressible_liquid_duty_and_efficiency_basis_reviewed=True))
add('pump.suction_acceleration_head',lambda:_factory('test_liquid_acceleration_head_independent.py','request',.001))
add('pump.thermal_pipe_operating_point',_pump_thermal)
add('thermal.free_linear_expansion',lambda:_new('models.thermal.Expansion',reference_length_m=10.,
    linear_expansion_per_k=.000012,reference_temperature_k=300.,operating_temperature_k=350.))
add('thermal.gas_regulator_joule_thomson',lambda:_new('gas_thermal_observation.GasPressureReductionRequest',
    fluid='Nitrogen',inlet_pressure_Pa_abs=500000.,outlet_pressure_Pa_abs=200000.,inlet_temperature_K=300.,property_basis_id='EOS'))
add('thermal.ground_heat_diffusion',lambda:_example('soil_fem.py'))
add('thermal.steady_radial_conduction',lambda:_factory('test_radial_temperature_profile_independent.py','make'))
add('thermo.cubic_gas_eos',lambda:_new('thermo.StateRequest',fluid='Methane',backend='PR',inputs={'P':200000.,'T':300.}))
add('thermo.ideal_gas_state',lambda:_factory('test_gas_state_observer_independent.py','ideal'))
add('thermo.real_gas_state',lambda:_factory('test_gas_state_observer_independent.py','real'))
add('valve.gas_modern_terminal_ratio',lambda:_new('models.library_models.GasControlValve',temperature_k=300.,
    molar_mass_kg_mol=.028,viscosity_pa_s=.00001,heat_capacity_ratio=1.4,compressibility=1.,
    inlet_pressure_pa_abs=500000.,outlet_pressure_pa_abs=200000.,standard_volume_flow_m3_s=.01,
    reference_temperature_k=273.15,reference_pressure_pa_abs=101325.,inlet_diameter_m=.1,
    outlet_diameter_m=.1,valve_diameter_m=.1,pressure_recovery_factor=.8,valve_style_factor=1.,terminal_pressure_ratio_factor=.7))
add('valve.steam_superheat_sizing',lambda:_factory('test_steam_nozzle_individual.py','request'))
CANONICAL_UNITS=tuple(unit for unit,_ in CASES)
assert len(CASES)==63 and len(set(CANONICAL_UNITS))==63


@pytest.fixture(params=CASES,ids=[unit for unit,_ in CASES])
def request_case(request,record_property):
    unit,factory=request.param
    record_property('canonical_units',json.dumps([unit]))
    baseline=factory()
    assert hasattr(type(baseline),'model_fields')
    return unit,baseline


def _assert_error(model,values,field,types):
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==(field,) and e['type'] in types for e in exc.value.errors()),exc.value.errors()


def test_valid_mode_specific_baseline(request_case):
    unit,q=request_case
    assert type(q).model_validate(q.model_dump()).model_dump()==q.model_dump()
    INPUT_ASSERTIONS.setdefault(unit,{})['baseline']=1


def test_required_field_omissions(request_case):
    unit,q=request_case
    names=[n for n,f in type(q).model_fields.items() if f.is_required()]
    for name in names:
        values=q.model_dump();values.pop(name)
        _assert_error(type(q),values,name,{'missing'})
    INPUT_ASSERTIONS.setdefault(unit,{})['required_fields']=len(names)
    if not names:
        INPUT_ASSERTIONS[unit]['required_fields_na']='All fields explicitly permit unknown/default values; populated baseline mode is tested.'


def test_numeric_bounds(request_case):
    unit,q=request_case
    count=0
    for name,field in type(q).model_fields.items():
        limits=[m.gt for m in field.metadata if getattr(m,'gt',None) is not None]
        inclusive=[m.ge for m in field.metadata if getattr(m,'ge',None) is not None]
        if not limits and not inclusive:continue
        value=max(limits) if limits else max(inclusive)-1
        if field.annotation is int:value=int(value)
        values=q.model_dump();values[name]=value
        _assert_error(type(q),values,name,{'greater_than','greater_than_equal'})
        count+=1
    INPUT_ASSERTIONS.setdefault(unit,{})['numeric_bound_fields']=count
    if not count:
        INPUT_ASSERTIONS[unit]['numeric_bounds_na']='No direct scalar lower-bound field; nested numeric input and exact mode/schema tests apply.'


def test_unknown_unit_alias(request_case):
    unit,q=request_case
    values=q.model_dump();values['pressure_kPa']=200.
    _assert_error(type(q),values,'pressure_kPa',{'extra_forbidden'})
    INPUT_ASSERTIONS.setdefault(unit,{})['unknown_unit_alias']=1


def test_nonfinite_and_unit_text_numeric_field(request_case):
    unit,q=request_case
    values=q.model_dump()
    name=next((n for n,v in values.items() if isinstance(v,float)),None)
    if name is None:
        # Nested quantity models (e.g. StateRequest) still have numeric fields;
        # preserve the explicit container so the error belongs to that field.
        name=next(n for n,v in values.items() if isinstance(v,dict) and any(isinstance(x,float) for x in v.values()))
        child=next(k for k,v in values[name].items() if isinstance(v,float))
    else:child=None
    for bad in ('200 kPa',float('nan'),float('inf'),float('-inf')):
        changed=deepcopy(values)
        if child is None:changed[name]=bad
        else:changed[name][child]=bad
        with pytest.raises(ValidationError) as exc:
            type(q).model_validate(changed)
        wanted=(name,) if child is None else (name,child)
        assert any(e['loc']==wanted and e['type'] in ('float_type','finite_number') for e in exc.value.errors()),exc.value.errors()
    INPUT_ASSERTIONS.setdefault(unit,{})['malformed_nonfinite_values']=4


# Each relation starts from the selected valid baseline and changes only the
# named coordinate. Error locations/messages establish the intended rejection.
RELATIONS={
 'batch.effective_longitudinal_dispersion':(('lower_volume_fraction',.995,(),'thresholds must be strictly increasing'),),
 'blower.supplied_reduced_similarity':((('performance_map','pressure_ratios'),lambda d:d['performance_map']['pressure_ratios'][:-1],('performance_map',),'tables must follow speed rows'),),
 'compression.real_process_and_stage_path':(('stages',lambda d:d['stages']*2,(),'stage identifiers must be distinct'),),
 'compressor.clearance_efficiency':(('residual_discharge_pressure_Pa_abs',100000.,(),'must not be below suction'),),
 'compressor.fixed_geometry_speed_head':(('required_total_polytropic_specific_head_j_per_kg',10000.,(),'must not independently prescribe'),),
 'compressor.ideal_compression_work':(('outlet_pressure_pa_abs',50000.,(),'strictly higher absolute outlet'),('constant_cp_j_kg_k',100.,(),'cp greater than R')),
 'compressor.labyrinth_series_cavity_leakage':(('restrictions',lambda d:(d['restrictions'][0],)*2,(),'restriction IDs must be unique'),),
 'compressor.observed_external_energy_loss_balance':(('ports',lambda d:(d['ports'][0],)*2,(),'port IDs must be unique'),),
 'free_surface.circular_saint_venant':(('friction_model','chezy',(),'requires a supplied dimensional coefficient'),),
 'gas.conditional_legacy_blowdown_estimator':(('target_maximum_pipe_pressure_pa_abs',90000.,(),'target must exceed ambient'),),
 'gas.conservative_rigid_pipe_1d':(('observation_times_s',(.2,.1),(),'observation times must increase'),),
 'gas.isentropic_nozzle':(('back_pressure_pa_abs',600000.,(),'relieving pressure must exceed back pressure'),),
 'gas.panhandle_network_equivalence':(('reference_diameter_m',None,(),'series reduction requires only a reference diameter'),),
 'gas.receiver_start_inventory_size':(('low_pressure_Pa_abs',2000000.,(),'receiver pressure must decrease'),),
 'gas.steady_pipe_total_energy_path':(('initial_node_pressures_Pa_abs',(400000.,450000.),(),'arrays must identify every downstream'),),
 'hydraulics.circular_pipe_friction':(('roughness_m',.02,(),'relative roughness exceeds'),),
 'hydraulics.concentric_annulus_loss':(('inner_body_radius_m',.11,(),'inner body radius must be strictly below'),('radial_positions_m',(.05,.15),(),'sample radii must lie between')),
 'hydraulics.nonisothermal_viscous_pipe':(('outlet_port_id',lambda d:d['inlet_port_id'],(),'inlet and outlet ports must differ'),),
 'hydraulics.relief_system_total_resistance':(('samples_at_friction_fraction',(0.,.75,.5,1.),(),'ordered distinct friction fractions'),),
 'hydraulics.supplied_drag_reduction':(('observed_treated_friction_drop_pa',100000.,(),'choose observed treated drop or supplied reduction'),),
 'hydrostatics.coated_pipe_effective_weight':(('inner_diameter_m',.7,(),'inner diameter must be smaller'),),
 'liquid.declared_saturated_mixed_circular_flow':((('numerics','signed_downhill_angle_rad'),.01,(),None),),
 'liquid.fixed_inertance_column_dynamics':(('gas_volume_support_m3',(.04,.08),(),'initial pocket volume must be strictly inside'),),
 'liquid.homogeneous_mixture_compliance_celerity':(('gas_volume_fraction',1.1,('gas_volume_fraction',),None),),
 'liquid.linear_acoustic_characteristics':((('left','density_kg_m3'),0.,('left','density_kg_m3'),None),),
 'liquid.linear_acoustic_pipe_field':(('nodes',lambda d:(d['nodes'][0],)*2,(),'node and pipe IDs must be unique'),),
 'liquid.supplied_exponential_viscosity':(('temperature_support_K',(350.,360.),(),'support must contain the reference'),),
 'pipe.karman_radial_profile':(('radial_positions_m',(.01,.2),(),'observations must lie within'),),
 'pipe.prescribed_pressure_floor_route':(('query_stations_m',(4000.,),(),'query stations must strictly increase within'),),
 'pipe.state_dependent_quasisteady_friction':(('vapour_pressure_pa_abs',200000.,(),'requires sub-boiling ambient conditions'),),
 'pipe.supplied_wall_friction_modifier':(('friction_search_bounds',(.1,.01),(),'friction search interval must increase'),),
 'pump.fixed_geometry_speed_affinity':(('reference_head_m',(80.,75.),(),'each reference flow requires one head'),),
 'pump.pipe_head_operating_point':((('reference_curve','head_m'),(100.,110.,10.),('reference_curve',),'requires a strictly decreasing pump branch'),),
 'pump.pressure_or_head_duty_power':(('pump_efficiency',1.1,('pump_efficiency',),None),),
 'pump.suction_acceleration_head':((('segments',0,'actual_flow_area_m2'),0.,('segments',0,'actual_flow_area_m2'),None),),
 'pump.thermal_pipe_operating_point':(('mass_flow_bracket_kg_s',(20.,30.),(),'bracket exceeds the supplied pump curve'),),
 'thermal.gas_regulator_joule_thomson':(('outlet_pressure_Pa_abs',600000.,(),'passive pressure reduction cannot raise pressure'),),
 'thermal.ground_heat_diffusion':(('initial_node_temperature_K',lambda d:d['initial_node_temperature_K'][:-1],(),'identify every original mesh vertex'),),
 'thermal.steady_radial_conduction':(('evaluation_radii_m',(.06,.03),(),'evaluation radii must strictly increase'),),
 'thermo.cubic_gas_eos':(('inputs',{'P':200000.},(),'supported pair of independent SI state'),),
 'thermo.ideal_gas_state':(('mass_kg',1.,(),'supply exactly the declared'),(('components',0,'fraction'),.5,(),'declared fractions must sum to one')),
 'thermo.real_gas_state':(('volume_m3',1.,(),'supply exactly the declared'),),
 'valve.gas_modern_terminal_ratio':(('outlet_pressure_pa_abs',600000.,(),'positive pressure drop'),),
 'valve.steam_superheat_sizing':(('back_pressure_Pa_abs',2000000.,(),'back pressure no greater than total inlet'),),
}
for _unit in ('gas.empirical_capacity_alternatives','gas.empirical_panhandle_a_flat','gas.empirical_panhandle_b_flat',
              'gas.empirical_weymouth_flat','gas.low_pressure_spitzglass'):
    RELATIONS[_unit]=(('inlet_pressure_pa_abs',100000.,(),'requires upstream pressure above downstream'),)
for _unit in ('hydraulics.series_local_resistance','hydraulics.series_parallel_network','pipe.hose_mises_friction'):
    RELATIONS[_unit]=(('branch_flow_brackets_m3_s',((-.001,.001),),(),'one flow bracket per path'),)


def _replaced(value,path,new):
    if not path:return new
    key,*tail=path
    if isinstance(value,tuple):
        result=list(value);result[key]=_replaced(result[key],tail,new);return tuple(result)
    result=dict(value);result[key]=_replaced(result[key],tail,new);return result


def test_model_specific_relations_or_explicit_scalar_na(request_case,record_property):
    unit,q=request_case
    if unit not in RELATIONS:
        assert not type(q).__pydantic_decorators__.model_validators
        record_property('relational_constraints','N/A: selected scalar contract has no relational validator; exact scalar/mode/schema mutations tested.')
        INPUT_ASSERTIONS.setdefault(unit,{})['relational_na']='No relational validator on the selected scalar contract.'
        return
    relations=RELATIONS[unit]
    for path,bad,loc,message in relations:
        path=(path,) if isinstance(path,str) else path
        values=q.model_dump()
        values=_replaced(values,path,bad(values) if callable(bad) else bad)
        # A varying bed and a uniform inclination are mutually exclusive.
        if unit=='liquid.declared_saturated_mixed_circular_flow':
            values['bed_elevations_m']=(0.,0.,0.)
            message='bed reconstruction uses a horizontal small-slope reference'
        with pytest.raises(ValidationError) as exc:
            type(q).model_validate(values)
        assert any(e['loc']==loc and (message is None or message in e['msg']) for e in exc.value.errors()),exc.value.errors()
    INPUT_ASSERTIONS.setdefault(unit,{})['specific_relational_mutations']=len(relations)


@pytest.mark.parametrize('key,value,error',(
    ('heat_capacity_ratio',1.,'greater_than'),
    ('initial_absolute_pressure_Pa','200 kPa','float_type'),
    ('final_absolute_pressure_Pa',-1.,'greater_than'),
    ('initial_volume_m3',0.,'greater_than'),
))
def test_expansion_parameter_rejection_before_owner_dispatch(key,value,error,record_property):
    record_property('canonical_units',json.dumps(['gas.ideal_reversible_expansion_work']))
    from pipeline_integrity.pressure_tests import PressureTestRequest
    parameters=dict(initial_absolute_pressure_Pa=200000.,final_absolute_pressure_Pa=100000.,
        initial_volume_m3=.1,heat_capacity_ratio=1.4)
    parameters[key]=value
    with pytest.raises(ValidationError) as exc:
        PressureTestRequest(method='inspection.gas_expansion_energy',asset_id='vessel',location_id='gas',
            clock_id='clock',parameters=parameters,evidence_ids=('basis',))
    assert any(e['loc']==('parameters',key) and e['type']==error for e in exc.value.errors())


def test_expansion_unknown_inputs_preserved_without_owner_dispatch(record_property):
    record_property('canonical_units',json.dumps(['gas.ideal_reversible_expansion_work']))
    from pipeline_integrity.pressure_tests import PressureTestRequest
    q=PressureTestRequest(method='inspection.gas_expansion_energy',asset_id='vessel',location_id='gas',
        clock_id='clock',parameters={'initial_absolute_pressure_Pa':None},evidence_ids=('basis',))
    assert q.parameters=={'initial_absolute_pressure_Pa':None}


def test_isothermal_process_exponent_stays_a_valid_input(record_property):
    record_property('canonical_units',json.dumps(['compressor.ideal_compression_work']))
    q=dict(CASES)['compressor.ideal_compression_work']()
    restored=type(q).model_validate(q.model_copy(update={'polytropic_exponent':1.}).model_dump())
    assert restored.polytropic_exponent==1.
