"""Exact computed fluid observables enter a separate synthetic uncertainty budget."""
import dataclasses,inspect,json,math,os,sys
from pathlib import Path
from pydantic import BaseModel
from fluid_workflow_capture_223 import run_upstream,plain
from properties_pipeline_cases_223 import Observable,budget_for,save_receipt,get
from pipeline_integrity.contracts import EvidenceRef,RunContext,digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import MeasurementBasis

if os.environ.get('PIPELINE_FORMULA_LH2DT_SRC'):
    sys.path.insert(0,os.environ['PIPELINE_FORMULA_LH2DT_SRC'])


def _clone_public_argument(value):
    """Replay JSON contracts and numeric buffers without inventing provider state."""
    if isinstance(value,BaseModel):return type(value).model_validate_json(value.model_dump_json())
    if dataclasses.is_dataclass(value):
        return type(value)(**{f.name:_clone_public_argument(getattr(value,f.name)) for f in dataclasses.fields(value) if f.init})
    if isinstance(value,dict):return {k:_clone_public_argument(v) for k,v in value.items()}
    if isinstance(value,tuple):return tuple(_clone_public_argument(v) for v in value)
    if isinstance(value,list):return [_clone_public_argument(v) for v in value]
    if hasattr(value,'shape') and hasattr(value,'tolist'):
        import numpy as np
        return np.asarray(json.loads(json.dumps(value.tolist())),dtype=value.dtype)
    if isinstance(value,(str,int,float,bool,type(None))):return json.loads(json.dumps(value,allow_nan=False))
    return value


def _argument_record(value):
    if isinstance(value,(BaseModel,dict,tuple,list,str,int,float,bool,type(None))) or dataclasses.is_dataclass(value) or hasattr(value,'tolist'):
        return plain(value)
    cls=type(value)
    try:path=inspect.getsourcefile(cls)
    except TypeError:path=None
    return {'declared_provider_type':cls.__module__+'.'+cls.__qualname__,
            'provider_source_sha256':file_digest(Path(path)) if path else None}


def _direct_trace(unit):
    """Avoid tracing every FV iteration when the complete public request is known."""
    from fluid_workflow_capture_223 import CASES,NUMERIC
    source=NUMERIC['source']
    CASES[unit]()  # Existing independent assertions execute unchanged, without profiling.
    if unit=='thermal.ground_heat_diffusion':
        module=source('test_soil_fem.py');q,c=module.MAKE(shear=.3,reviewed=True)
        fn=module.evaluate_soil_fem;kwargs={'request':q,'context':c}
    elif unit=='framework.evolving_thermoelastic_pipe_field':
        from pipeline_integrity.thermoelastic_pipe_field import solve_thermoelastic_pipe_field as fn
        module=source('test_thermoelastic_pipe_field.py')
        q=module.request(fluid_wall_conductance_W_m_K=100.,initial_wall_temperature_K=(320.,)*5,end_time_s=.01,maximum_step_s=.00005)
        kwargs={'request':q}
    elif unit=='gas.conservative_rigid_pipe_1d':
        import numpy as np
        from pipeline_integrity.gas_acoustic_pipe import solve_gas_acoustic_pipe as fn
        module=source('test_gas_acoustic_pipe_independent.py')
        kwargs={'initial_conservative':module.state(np.ones(4),0.,np.ones(4)),
            'settings':module.Settings(length_m=1.,inner_diameter_m=1.,duration_s=.4,wall_heat_transfer_W_m2_K=.3,
                ambient_temperature_K=340.,maximum_step_s=.000125),
            'eos':module.EOS,'boundary_stages':module.periodic()}
    else:
        # The fixture exposes the complete finite-endpoint vent construction.
        # Capture only the one top-level solve call; never profile its FV inner loop.
        import numpy as np
        from pipeline_integrity.gas_restricted_vent import solve_ideal_restricted_pipe_blowdown as fn,RestrictedVentBlowdownSpec
        from pipeline_integrity.gas_acoustic_pipe import GasAcousticPipeSettings,IdealGasInternalEnergyEOS,gas_conservative_state
        module=source('test_restricted_gas_vent_independent.py')
        p0=4e5;target=2.3e5;fraction=.02;area=math.pi*.1**2/4;vent_area=area*fraction;cd=.8
        exact=module.lumped_time(p0,target,1e5,vent_area,cd)
        eos=IdealGasInternalEnergyEOS()
        settings=GasAcousticPipeSettings(length_m=1.,inner_diameter_m=.1,duration_s=2*exact,
            maximum_step_s=.01,pressure_stop_tolerance_pa=.02,observation_times_s=())
        vent=RestrictedVentBlowdownSpec(geometric_vent_area_m2=vent_area,discharge_coefficient=cd,ambient_pressure_pa_abs=1e5,
            target_maximum_pipe_pressure_pa_abs=target,quasisteady_ideal_small_vent_and_one_way_support_reviewed=True)
        kwargs={'initial_conservative':gas_conservative_state(np.full(4,p0/(287.05*310.)),0.,eos.internal_energy_from_temperature(310.)),
            'settings':settings,'eos':eos,'spec':vent,'opening_time_s':0.}
        # Signature names come from the public solver, not assumed aliases.
        names=tuple(inspect.signature(fn).parameters)
        kwargs[names[0]]=kwargs.pop('initial_conservative')
        kwargs[names[1]]=kwargs.pop('settings')
        kwargs[names[2]]=kwargs.pop('eos')
        kwargs[names[3]]=kwargs.pop('spec')
    result=fn(**kwargs)
    return [{'api':fn.__module__+'.'+fn.__qualname__,'args':(),'kwargs':kwargs,'result':result,'output':plain(result),'successful':True}]

# Exact field, SI unit, pressure datum, and meaning. Predictions remain conditional
# simulation quantities; downstream correction/covariance is authored, not calibrated.
SPECS={
'framework.evolving_thermoelastic_pipe_field':(('final_pressure_pa_abs',0),'Pa','absolute','final_cell_zero_thermoelastic_gas_pressure'),
'gas.capacity_limited_regulator_boundary':(('controlled_pressure_pa_abs',),'Pa','absolute','regulated_node_pressure_at_capacity_equilibrium'),
'gas.conditional_legacy_blowdown_estimator':(('elapsed_stop_time_s',),'s',None,'elapsed_time_to_declared_finite_pressure_endpoint'),
'gas.conservative_rigid_pipe_1d':(('pressure_pa_abs',0),'Pa','absolute','final_cell_zero_rigid_pipe_gas_pressure'),
'gas.dilute_binary_molecular_diffusivity':(('values','binary_molecular_diffusivity_m2_s'),'m^2/s',None,'dilute_binary_relative_collision_diffusivity'),
'gas.empirical_capacity_alternatives':(('values','standard_volume_flow_m3_s'),'m^3/s',None,'empirical_gas_flow_at_request_declared_reference_PT'),
'gas.empirical_panhandle_a_flat':(('values','standard_volume_flow_m3_s'),'m^3/s',None,'panhandle_A_flow_at_request_declared_reference_PT'),
'gas.empirical_panhandle_b_flat':(('values','standard_volume_flow_m3_s'),'m^3/s',None,'panhandle_B_flow_at_request_declared_reference_PT'),
'gas.empirical_weymouth_flat':(('values','standard_volume_flow_m3_s'),'m^3/s',None,'weymouth_flow_at_request_declared_reference_PT'),
'gas.ideal_reversible_expansion_work':(('values','ideal_expansion_work_J'),'J',None,'conditional_reversible_polytropic_boundary_work'),
'gas.isentropic_nozzle':((0,),'kg/s',None,'ideal_isentropic_nozzle_mass_capacity'),
'gas.low_pressure_spitzglass':(('standard_volume_flow_m3_s',),'m^3/s',None,'spitzglass_flow_at_request_declared_reference_PT'),
'gas.panhandle_network_equivalence':(('equivalent_length_m',),'m',None,'frozen_series_pressure_squared_law_equivalent_length'),
'gas.prescribed_junction_mass_enthalpy_boundary':(('state','pressure_Pa_abs'),'Pa','absolute','common_mixed_junction_equilibrium_pressure'),
'gas.receiver_start_inventory_size':(('required_receiver_volume_m3',),'m^3',None,'required_rigid_receiver_inventory_volume'),
'gas.squared_pressure_mean':(('mean_pressure_pa_abs',),'Pa','absolute','distance_mean_of_linear_squared_pressure_profile'),
'gas.steady_pipe_total_energy_path':(('mass_flow_kg_s',),'kg/s',None,'steady_total_energy_pipe_mass_rate'),
'hydraulics.circular_pipe_friction':(('values','dissipative_pressure_loss_pa'),'Pa','differential','signed_friction_and_local_dissipative_pressure_loss'),
'hydraulics.concentric_annulus_loss':(('signed_friction_pressure_drop_Pa',),'Pa','differential','signed_annular_viscous_pressure_drop'),
'hydraulics.hazen_williams_water':(('head_loss_m',),'m',None,'conditional_hazen_williams_water_dissipative_head'),
'hydraulics.hydraulic_grade_profile':(('stations',-1,'pressure_pa_abs'),'Pa','absolute','last_requested_station_static_pressure'),
'hydraulics.nonisothermal_viscous_pipe':(('values','outlet','temperature_k'),'K',None,'outlet_temperature_from_pressure_work_energy_path'),
'hydraulics.relief_system_total_resistance':(('mass_flow_kg_s',),'kg/s',None,'finite_friction_relief_path_mass_capacity'),
'hydraulics.series_local_resistance':(('common_inlet_minus_outlet_pressure_pa',),'Pa','differential','series_path_required_inlet_minus_outlet_pressure'),
'hydraulics.series_parallel_network':(('total_volume_flow_m3_s',),'m^3/s',None,'parallel_network_signed_total_volume_flow'),
'hydraulics.supplied_drag_reduction':(('projected_flow_magnitude_m3_s',),'m^3/s',None,'conditional_calibrated_fixed_head_projected_flow'),
'thermal.free_linear_expansion':(('values','operating_length_m'),'m',None,'free_length_integrating_reference_thermal_strain'),
'thermal.gas_regulator_joule_thomson':(('outlet_state','temperature_k'),'K',None,'isenthalpic_pressure_reduction_outlet_temperature'),
'thermal.ground_heat_diffusion':(('native','rows',-1,'boundary_physical_gradient_power_into_soil_W','ground'),'W',None,'computed_ground_boundary_gradient_heat_into_soil'),
'thermal.steady_radial_conduction':(('heat_rate_outward_w',),'W',None,'steady_cylindrical_outward_conduction_power'),
'valve.cavitation_margin':(('incipient_pressure_drop_limit_pa',),'Pa','differential','declared_cavitation_inception_pressure_drop_limit'),
'valve.gas_modern_terminal_ratio':(('mass_flow_kg_s',),'kg/s',None,'forward_valve_mass_capacity_with_declared_terminal_ratio'),
'valve.liquid_capacity':(('volume_flow_m3_s',),'m^3/s',None,'forward_liquid_valve_capacity_at_declared_liquid_state'),
'valve.steam_superheat_sizing':(('ideal_mass_flux_kg_m2_s',),'kg/m^2/s',None,'single_phase_superheated_isentropic_ideal_mass_flux'),
'valve.supplied_liquid_pressure_limits':(('choked_pressure_drop_limit_pa',),'Pa','differential','declared_liquid_valve_choked_pressure_drop_limit'),
}


def produce(unit,monkeypatch,*,json_boundary=False):
    from pipeline_integrity.engineering_methods import get_engineering_method_callable
    path,si,pressure,meaning=SPECS[unit]
    direct={'framework.evolving_thermoelastic_pipe_field','gas.conservative_rigid_pipe_1d','thermal.ground_heat_diffusion','gas.conditional_legacy_blowdown_estimator'}
    trace=_direct_trace(unit) if unit in direct else run_upstream(unit,monkeypatch)
    selected=[]
    for e in trace:
        try:v=get(e['output'],path)
        except (KeyError,IndexError,TypeError):continue
        if type(v) in (int,float) and math.isfinite(v):selected.append(e)
    assert selected,'Expected actual output absent: '+unit+' '+repr(path)
    e=selected[-1];kwargs=dict(e['kwargs']);qkeys=[k for k,v in kwargs.items() if isinstance(v,BaseModel) and type(v).__name__!='RunContext']
    qkey=next((k for k in ('request','q','p') if k in qkeys),qkeys[0] if qkeys else None)
    q=kwargs.get(qkey)
    if json_boundary:
        kwargs={k:_clone_public_argument(v) for k,v in kwargs.items()}
        q=kwargs.get(qkey)
        fn=get_engineering_method_callable(unit,e['api'])
        raw=plain(fn(**kwargs))
    else:raw=e['output']
    if isinstance(raw,dict) and 'status' in raw:
        assert raw['status'] in ('computed','computed_diagnostic','ok','completed_history','rupture_depth_reached'), raw.get('status')
    value=get(raw,path);assert math.isfinite(value)
    qdata=plain(q) if q is not None else {k:_argument_record(v) for k,v in kwargs.items()}
    if not isinstance(qdata,dict):qdata={'parameters':qdata}
    # Multiargument FV states/EOS/boundaries and scalar-kernel EOS parameters
    # are part of the reproducible request, not omitted as untyped extras.
    extras={k:_argument_record(v) for k,v in kwargs.items() if k!=qkey}
    if extras:qdata['public_execution_arguments']=extras
    # Preserve exact selected snapshot; a steady model has no invented time.
    times=raw.get('times_s',raw.get('step_times_s')) if isinstance(raw,dict) else None
    if times:qdata['time_s']=times[-1]
    if unit=='thermal.ground_heat_diffusion':qdata['time_s']=raw['native']['rows'][-1]['time_s']+qdata['clock_offset_s']
    if isinstance(qdata.get('time_s'),list):qdata['time_s']=qdata['time_s'][-1]+qdata.get('clock_offset_s',0.)
    upstream=kwargs.get('context')
    asset=getattr(q,'asset_id',None) or getattr(upstream,'asset_id',None) or 'authored-model-'+unit
    clock=getattr(q,'clock_id',None) or getattr(upstream,'clock_id',None) or 'authored-steady-snapshot'
    context=RunContext(asset_id=asset,clock_id=clock,scenario_id='fluid_handoff_'+unit,origin='simulation',
        evidence=(EvidenceRef(id='model-output',citation='Actual public model calculation for authored conditional simulation',origin='simulation',sha256=digest(raw)),
            EvidenceRef(id='synthetic-correction',citation='Authored correction covariance and acceptance rule; no calibration accuracy inference',origin='synthetic')),
        metadata={'source_api':e['api'],'selected_output_path':list(path),'upstream_request_sha256':digest(qdata),
            'upstream_result_sha256':digest(raw),'original_asset_id':getattr(q,'asset_id',None),
            'original_clock_id':getattr(q,'clock_id',None),'reference_basis':'Complete source request retained; no standard-to-actual gas volume conversion inferred'})
    basis=MeasurementBasis(quantity=meaning,unit=si,definition_id=unit+':'+'.'.join(map(str,path))+':'+digest(qdata),evidence_id='model-output',pressure_reference=pressure)
    fn=get_engineering_method_callable(unit,e['api'])
    return Observable(unit,e['api'],file_digest(Path(inspect.getsourcefile(fn))),qdata,raw,float(value),basis,context)
