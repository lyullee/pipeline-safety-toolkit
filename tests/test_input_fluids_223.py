"""Input contracts only: no valid solver, property or physical calculation runs."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from pipeline_integrity.thermoelastic_pipe_field import (
    ThermoelasticWall, ThermoelasticPipeFieldRequest, solve_thermoelastic_pipe_field,
)
from pipeline_integrity.screw_pump import ScrewPumpRequest, evaluate_screw_pump
from pipeline_integrity.hydraulic_grade import (
    GradeStation, HydraulicGradeRequest, HazenWilliamsRequest,
    evaluate_hydraulic_grade, evaluate_hazen_williams,
)
from pipeline_integrity.liquid_valve_forward_capacity import LiquidValveForwardCapacityRequest
from pipeline_integrity.models.library_models import LiquidControlValve
from pipeline_integrity.steady_circular_depth import SteadyCircularFlow, CircularDepthRootRequest
from pipeline_integrity.gas_regulator_boundary import GasRegulatorBoundaryRequest, solve_gas_regulator_boundary
from pipeline_integrity.prescribed_gas_junction import (
    JunctionFeed, PrescribedGasJunctionRequest, solve_prescribed_gas_junction,
)
from pipeline_integrity.liquid_valve_pressure_limits import LiquidValvePressureLimitsRequest

CANONICAL_UNITS=(
    'framework.evolving_thermoelastic_pipe_field','pump.screw_empirical_slip_and_friction',
    'hydraulics.hazen_williams_water','hydraulics.hydraulic_grade_profile',
    'valve.liquid_capacity','valve.cavitation_margin','valve.supplied_liquid_pressure_limits',
    'free_surface.circular_steady_depth_observers','gas.capacity_limited_regulator_boundary',
    'gas.prescribed_junction_mass_enthalpy_boundary',
)


@pytest.fixture(autouse=True)
def _canonical_input_membership(request,record_property):
    model=getattr(request.node,'callspec',None)
    model=None if model is None else model.params.get('model')
    memberships={
        'ThermoelasticWall':['framework.evolving_thermoelastic_pipe_field'],
        'ThermoelasticPipeFieldRequest':['framework.evolving_thermoelastic_pipe_field'],
        'ScrewPumpRequest':['pump.screw_empirical_slip_and_friction'],
        'HazenWilliamsRequest':['hydraulics.hazen_williams_water'],
        'GradeStation':['hydraulics.hydraulic_grade_profile'],
        'HydraulicGradeRequest':['hydraulics.hydraulic_grade_profile'],
        'LiquidControlValve':['valve.liquid_capacity'],
        'LiquidValveForwardCapacityRequest':['valve.liquid_capacity'],
        'SteadyCircularFlow':['free_surface.circular_steady_depth_observers'],
        'CircularDepthRootRequest':['free_surface.circular_steady_depth_observers'],
        'GasRegulatorBoundaryRequest':['gas.capacity_limited_regulator_boundary'],
        'JunctionFeed':['gas.prescribed_junction_mass_enthalpy_boundary'],
        'PrescribedGasJunctionRequest':['gas.prescribed_junction_mass_enthalpy_boundary'],
        'LiquidValvePressureLimitsRequest':['valve.cavitation_margin','valve.supplied_liquid_pressure_limits'],
    }
    if model is not None:units=memberships[model.__name__]
    elif 'regulator' in request.node.name:units=['gas.capacity_limited_regulator_boundary']
    elif 'junction' in request.node.name:units=['gas.prescribed_junction_mass_enthalpy_boundary']
    else:pytest.fail('input case has no explicit canonical model membership')
    record_property('canonical_units',json.dumps(units))
WALL=dict(reference_area_m2=.01,reference_temperature_K=300.,area_compliance_m2_pa=1e-10,
    natural_area_temperature_derivative_m2_K=2e-7,heat_capacity_per_length_J_m_K=2000.,
    exterior_pressure_pa_abs=100000.,minimum_pressure_pa_abs=110000.,maximum_pressure_pa_abs=1000000.,
    minimum_temperature_K=250.,maximum_temperature_K=400.,maximum_reference_area_fractional_change=.05,
    constitutive_evidence_id='wall-calibration')
THERMAL=dict(id='field',length_m=10.,gas_constant_J_kg_K=287.,heat_capacity_ratio=1.4,wall=WALL,
    fluid_wall_conductance_W_m_K=5.,initial_pressure_pa_abs=(200000.,)*3,
    initial_fluid_temperature_K=(300.,)*3,initial_wall_temperature_K=(300.,)*3,
    initial_velocity_m_s=(0.,)*3,end_time_s=.001,maximum_step_s=.0001,cfl=.2,
    fixed_axial_length_quasistatic_wall_and_ideal_gas_reviewed=True)
SCREW=dict(displacement_m3_rev=.002,speed_rev_s=5.,pressure_rise_pa=200000.,dynamic_viscosity_pa_s=.1,
    leakage_conductance_m3=1e-10,viscous_torque_geometry_m3=.02,calibration_id='clearance',
    operating_regime_id='newtonian',newtonian_linear_leakage_and_viscous_drag_reviewed=True)
HAZEN=dict(volume_flow_m3_s=.001,diameter_m=.1,length_m=10.,coefficient_C=120.,
    coefficient_calibration_id='water-C',water_and_coefficient_domain_reviewed=True)
STATION=dict(id='in',elevation_m=0.,flow_area_m2=.01)
GRADE=dict(stations=(STATION,dict(id='out',elevation_m=1.,flow_area_m2=.01)),
    segments=(dict(length_m=10.,diameter_m=.1),),volume_flow_m3_s=.001,
    inlet_pressure_pa_abs=200000.,density_kg_m3=1000.,kinematic_viscosity_m2_s=1e-6,
    constant_density_single_phase_and_loss_domains_reviewed=True)
VALVE=dict(density_kg_m3=1000.,vapour_pressure_pa_abs=2000.,critical_pressure_pa_abs=22000000.,
    viscosity_pa_s=.001,inlet_pressure_pa_abs=300000.,outlet_pressure_pa_abs=200000.,
    volume_flow_m3_s=.001,inlet_diameter_m=.1,outlet_diameter_m=.1,valve_diameter_m=.1,
    pressure_recovery_factor=.8,valve_style_factor=1.)
FORWARD=dict(sizing_reference_case=VALVE,coefficient_scale='Kv',supplied_coefficient=10.,
    volume_flow_bracket_m3_s=(.0001,.01),increasing_single_liquid_sizing_branch_reviewed=True)
FLOW=dict(inner_diameter_m=1.,volume_flow_m3_s=.1,signed_downhill_angle_rad=.01,
    friction_model='chezy',chezy_coefficient_m_half_per_s=30.,hydrostatic_prismatic_wet_section_reviewed=True)
ROOT=dict(flow=FLOW,depth_bracket_m=(.01,.99))
REGULATOR=dict(controlled_side='downstream',fixed_other_side_pressure_pa_abs=500000.,
    target_controlled_pressure_pa_abs=300000.,saturated_controlled_pressure_bounds_pa_abs=(150000.,300000.))
FEED=dict(enthalpy_J_kg=100000.,mass_fractions={'gas':1.},enthalpy_reference_id='h-ref',property_basis_id='gas-basis')
JUNCTION=dict(id='node',asset_id='pipe',clock_id='clock',time_s=0.,species=('gas',),
    branches=(dict(id='branch',closure_id='law',incoming=FEED),),external_mass_flow_into_node_kg_s=-.1,
    pressure_support_Pa_abs=(100000.,500000.),temperature_support_K=(250.,400.),
    initial_pressure_Pa_abs=200000.,initial_temperature_K=300.,initial_mass_fractions={'gas':1.},
    enthalpy_reference_id='h-ref',property_basis_id='gas-basis',closure_revision_id='rev1',
    mass_scale_kg_s=1.,energy_scale_W=1000.,evidence_ids=('source',))
LIMITS=dict(inception_criterion='nominal_recovery_proxy',inlet_pressure_pa_abs=300000.,
    outlet_pressure_pa_abs=200000.,vapour_pressure_pa_abs=2000.,critical_pressure_pa_abs=22000000.,
    pressure_recovery_factor=.8)
BASES=((ThermoelasticWall,WALL),(ThermoelasticPipeFieldRequest,THERMAL),(ScrewPumpRequest,SCREW),
    (HazenWilliamsRequest,HAZEN),(GradeStation,STATION),(HydraulicGradeRequest,GRADE),
    (LiquidControlValve,VALVE),(LiquidValveForwardCapacityRequest,FORWARD),
    (SteadyCircularFlow,FLOW),(CircularDepthRootRequest,ROOT),(GasRegulatorBoundaryRequest,REGULATOR),
    (JunctionFeed,FEED),(PrescribedGasJunctionRequest,JUNCTION),(LiquidValvePressureLimitsRequest,LIMITS))


@pytest.mark.parametrize('model,data',BASES,ids=lambda value:getattr(value,'__name__','baseline'))
def test_valid_input_baseline_only(model,data):
    assert model.model_validate(deepcopy(data))


MISSING=[(model,data,name) for model,data in BASES for name,field in model.model_fields.items() if field.is_required()]


@pytest.mark.parametrize('model,data,name',MISSING,ids=lambda value:getattr(value,'__name__',str(value) if isinstance(value,str) else 'baseline'))
def test_required_input_omission_has_exact_location(model,data,name):
    values=deepcopy(data);values.pop(name)
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==(name,) and e['type']=='missing' for e in exc.value.errors())


BOUNDS=[]
for _model,_data in BASES:
    for _name,_field in _model.model_fields.items():
        for _limit in _field.metadata:
            if getattr(_limit,'gt',None) is not None:
                BOUNDS.append((_model,_data,_name,float(_limit.gt)));break
            if getattr(_limit,'ge',None) is not None:
                BOUNDS.append((_model,_data,_name,float(_limit.ge)-1));break


@pytest.mark.parametrize('model,data,name,value',BOUNDS,ids=lambda value:getattr(value,'__name__',str(value) if isinstance(value,(str,float)) else 'baseline'))
def test_numeric_lower_bound_has_exact_location(model,data,name,value):
    values=deepcopy(data);values[name]=int(value) if model.model_fields[name].annotation is int else value
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==(name,) and e['type'] in ('greater_than','greater_than_equal') for e in exc.value.errors())


@pytest.mark.parametrize('model,data',BASES,ids=lambda value:getattr(value,'__name__','baseline'))
def test_unknown_unit_alias_rejected(model,data):
    values=deepcopy(data);values['pressure_kPa']=200.
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==('pressure_kPa',) and e['type']=='extra_forbidden' for e in exc.value.errors())


NUMERIC=[(model,data,next((n for n,v in data.items() if isinstance(v,float)),None)) for model,data in BASES]


@pytest.mark.parametrize('bad',('1 bar',float('nan'),float('inf'),float('-inf')))
@pytest.mark.parametrize('model,data,name',[(m,d,n) for m,d,n in NUMERIC if n],ids=lambda value:getattr(value,'__name__',value if isinstance(value,str) else 'baseline'))
def test_malformed_or_nonfinite_numeric_field(model,data,name,bad):
    values=deepcopy(data);values[name]=bad
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==(name,) and e['type'] in ('float_type','finite_number') for e in exc.value.errors())


STRUCTURAL=(
    (ThermoelasticWall,WALL,{'minimum_pressure_pa_abs':1000001.},'tensile absolute-pressure bracket'),
    (ThermoelasticWall,WALL,{'minimum_temperature_K':500.},'increasing temperature'),
    (ThermoelasticWall,WALL,{'heat_capacity_per_length_J_m_K':.01},'constant-area heat capacity'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'initial_velocity_m_s':(0.,)*4},'initial cell arrays must align'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'external_bath_conductance_W_m_K':5.},'absolute bath temperature'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'initial_pressure_pa_abs':(100000.,)*3},'outside wall pressure support'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'initial_fluid_temperature_K':(200.,)*3},'outside wall temperature support'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'initial_wall_temperature_K':(450.,)*3},'outside wall temperature support'),
    (ThermoelasticPipeFieldRequest,THERMAL,{'wall':dict(WALL,maximum_reference_area_fractional_change=.0001)},'initial area lies outside'),
    (ScrewPumpRequest,SCREW,{'effective_shear_radius_m':.02},'supplied together'),
    (HydraulicGradeRequest,GRADE,{'segments':(dict(length_m=10.,diameter_m=.1),)*2},'one segment'),
    (HydraulicGradeRequest,GRADE,{'stations':(STATION,STATION)},'station IDs must be unique'),
    (HydraulicGradeRequest,GRADE,{'segments':(dict(length_m=10.,diameter_m=.1,law='hazen_williams'),)},'Hazen segment requires'),
    (GradeStation,STATION,{'minimum_allowed_pressure_pa_abs':300000.,'maximum_allowed_pressure_pa_abs':200000.},'minimum pressure criterion'),
    (LiquidValveForwardCapacityRequest,FORWARD,{'volume_flow_bracket_m3_s':(.01,.001)},'increasing flow bracket'),
    (LiquidControlValve,VALVE,{'outlet_pressure_pa_abs':400000.},'positive pressure drop'),
    (LiquidControlValve,VALVE,{'vapour_pressure_pa_abs':400000.},'above the supplied vapour pressure'),
    (LiquidControlValve,VALVE,{'critical_pressure_pa_abs':1000.},'below the liquid critical pressure'),
    (SteadyCircularFlow,FLOW,{'chezy_coefficient_m_half_per_s':None},'Chezy friction needs'),
    (CircularDepthRootRequest,ROOT,{'depth_bracket_m':(.1,1.)},'strictly increase inside'),
    (GasRegulatorBoundaryRequest,REGULATOR,{'saturated_controlled_pressure_bounds_pa_abs':(150000.,250000.)},'support ends at target'),
    (GasRegulatorBoundaryRequest,REGULATOR,{'fixed_other_side_pressure_pa_abs':250000.},'target must be below supply'),
    (JunctionFeed,FEED,{'mass_fractions':{'gas':.5}},'sum to one'),
    (PrescribedGasJunctionRequest,JUNCTION,{'species':('gas','gas')},'identities must be unique'),
    (PrescribedGasJunctionRequest,JUNCTION,{'external_mass_flow_into_node_kg_s':.1},'positive injection needs'),
    (PrescribedGasJunctionRequest,JUNCTION,{'initial_pressure_Pa_abs':600000.},'strictly inside ordered support'),
    (PrescribedGasJunctionRequest,JUNCTION,{'initial_mass_fractions':{'other':1.}},'initial composition identities differ'),
    (LiquidValvePressureLimitsRequest,LIMITS,{'outlet_pressure_pa_abs':400000.},'pressure drop must be nonnegative'),
    (LiquidValvePressureLimitsRequest,LIMITS,{'vapour_pressure_pa_abs':300000.},'liquid inlet must be above'),
)


@pytest.mark.parametrize('model,data,updates,message',STRUCTURAL,ids=lambda value:getattr(value,'__name__',value if isinstance(value,str) else 'baseline'))
def test_inconsistent_input_specific_contract(model,data,updates,message):
    values=deepcopy(data);values.update(updates)
    with pytest.raises(ValidationError) as exc:
        model.model_validate(values)
    assert any(e['loc']==() and message in e['msg'] for e in exc.value.errors())


@pytest.mark.parametrize('model,data,function,review_field,message',(
    (ThermoelasticPipeFieldRequest,THERMAL,solve_thermoelastic_pipe_field,'fixed_axial_length_quasistatic_wall_and_ideal_gas_reviewed','reviewed fixed-length'),
    (ScrewPumpRequest,SCREW,evaluate_screw_pump,'newtonian_linear_leakage_and_viscous_drag_reviewed','reviewed Newtonian'),
    (HazenWilliamsRequest,HAZEN,evaluate_hazen_williams,'water_and_coefficient_domain_reviewed','reviewed water'),
    (HydraulicGradeRequest,GRADE,evaluate_hydraulic_grade,'constant_density_single_phase_and_loss_domains_reviewed','reviewed constant-density'),
))
def test_api_rejects_missing_review_before_computation(model,data,function,review_field,message):
    values=deepcopy(data);values[review_field]=False
    with pytest.raises(ValueError,match=message):
        function(model.model_validate(values))


def _must_not_call(*args,**kwargs):
    pytest.fail('input-only test reached a numeric/provider path')


def test_regulator_revalidates_forged_pressure_before_provider():
    request=GasRegulatorBoundaryRequest.model_validate(REGULATOR).model_copy(
        update={'target_controlled_pressure_pa_abs':-1.})
    with pytest.raises(ValidationError) as exc:
        solve_gas_regulator_boundary(request,capacity_provider=_must_not_call,external_flow_provider=_must_not_call)
    assert exc.value.errors()[0]['loc']==('target_controlled_pressure_pa_abs',)


def test_regulator_unknown_basis_never_calls_provider():
    result=solve_gas_regulator_boundary(GasRegulatorBoundaryRequest.model_validate(REGULATOR),
        capacity_provider=_must_not_call,external_flow_provider=_must_not_call)
    assert result.state=='needs_data'
    assert result.evaluated_pressure_state_count==0


def test_regulator_wrong_request_type_rejected_before_provider():
    with pytest.raises(TypeError,match='GasRegulatorBoundaryRequest required'):
        solve_gas_regulator_boundary(deepcopy(REGULATOR),capacity_provider=_must_not_call,
                                    external_flow_provider=_must_not_call)


def test_junction_missing_review_before_closure():
    with pytest.raises(ValueError,match='require explicit review'):
        solve_prescribed_gas_junction(PrescribedGasJunctionRequest.model_validate(JUNCTION),closure=_must_not_call)
