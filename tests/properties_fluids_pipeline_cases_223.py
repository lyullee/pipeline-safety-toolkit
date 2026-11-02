"""Explicit real fluid observables from authored public-API fixture requests."""
import inspect,math
from pathlib import Path
from pydantic import BaseModel
from fluid_workflow_capture_223 import run_upstream,plain
from properties_pipeline_cases_223 import Observable,budget_for,save_receipt,get
from pipeline_integrity.contracts import EvidenceRef,RunContext,digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import MeasurementBasis

# Real API, selected physical output, coherent unit, exact definition.
SPECS={
 'batch.effective_longitudinal_dispersion':('batch_dispersion.evaluate_batch_interface',('mixing_length_m',),'m','caller_threshold_unbounded_step_mixing_length'),
 'batch.supplied_turbulent_dispersion_closure':('batch_dispersion.evaluate_axial_dispersion',('axial_dispersion_m2_s',),'m^2/s','supplied_turbulent_effective_axial_dispersion'),
 'blower.supplied_reduced_similarity':('blower_similarity.evaluate_blower_similarity',('values','shaft_power_W'),'W','mapped_same_geometry_blower_shaft_power'),
 'compression.real_process_and_stage_path':('real_compression_path.evaluate_real_compression_path',('values','specific_fluid_work_j_kg'),'J/kg','compression_path_specific_work_into_fluid'),
 'compressor.clearance_efficiency':('gas_clearance_cycle.evaluate_gas_clearance_intake',('ideal_clearance_volumetric_efficiency',),'1','ideal_fresh_intake_suction_volume_over_swept_volume'),
 'compressor.fixed_geometry_speed_head':('compressor_speed_head.evaluate_compressor_speed_head',('total_polytropic_specific_head_j_per_kg',),'J/kg','mapped_total_polytropic_specific_head'),
 'compressor.ideal_compression_work':('models.equipment.compression',('values','specific_work_j_kg'),'J/kg','adiabatic_compressor_specific_shaft_work'),
 'compressor.internal_recycle_inlet_enthalpy':('compressor_observations.reconstruct_gas_recycle_inlet',('mixed_inlet_state','enthalpy_j_kg'),'J/kg','recycle_mixed_inlet_enthalpy_in_declared_common_reference'),
 'compressor.labyrinth_series_cavity_leakage':('serial_gas_cavities.solve_serial_gas_cavities',('common_mass_flow_kg_s',),'kg/s','series_restriction_steady_common_mass_rate'),
 'compressor.observed_external_energy_loss_balance':('compressor_external_energy.observe_compressor_external_energy',('required_external_shaft_input_W',),'W','external_boundary_shaft_input_from_declared_energy_ports'),
 'pump.available_npsh_budget':('models.equipment.pump_npsh',('values','available_npsh_m'),'m','absolute_suction_head_minus_vapor_head_available_NPSH'),
 'pump.fixed_geometry_speed_affinity':('pump_curve_transform.scale_same_casing_pump_curve',('values','head_m',1),'m','transformed_curve_head_at_selected_corresponding_flow_point'),
 'pump.pipe_head_operating_point':('models.pump_curves.pump_system',('values','volume_flow_m3_s'),'m^3/s','steady_forward_pump_system_head_intersection_flow'),
 'pump.pressure_or_head_duty_power':('pump_pressure_duty.evaluate_pump_pressure_duty',('upstream_drive_input_power_w',),'W','drive_boundary_input_power_for_declared_pressure_duty'),
 'pump.screw_empirical_slip_and_friction':('screw_pump.evaluate_screw_pump',('net_delivery_m3_s',),'m^3/s','calibrated_screw_net_delivery_after_pressure_slip'),
 'pump.suction_acceleration_head':('liquid_acceleration_head.observe_liquid_acceleration_head',('signed_inertial_head_m',),'m','signed_inlet_minus_outlet_inertial_head_at_declared_flow_derivative'),
 'pump.thermal_pipe_operating_point':('heated_operating_point.evaluate_heated_operating_point',('mass_flow_kg_s',),'kg/s','heated_transport_pump_curve_intersection_mass_rate'),
 'thermo.cubic_gas_eos':('thermo.CoolPropProvider.resolve',('density_kg_m3',),'kg/m^3','declared_PR_cubic_EOS_same_PT_gas_density'),
 'thermo.ideal_gas_state':('gas_state_observer.observe_gas_state',('density_kg_m3',),'kg/m^3','ideal_mixture_same_PT_density_with_explicit_molar_basis'),
 'thermo.real_gas_state':('gas_state_observer.observe_gas_state',('density_kg_m3',),'kg/m^3','declared_HEOS_same_PT_single_gas_density'),
}


def produce(unit,monkeypatch,*,json_boundary=False):
    from pipeline_integrity.engineering_methods import get_engineering_method_callable
    name,path,si,quantity=SPECS[unit]
    trace=run_upstream(unit,monkeypatch)
    entry=next(t for t in trace if t['api']=='pipeline_integrity.'+name)
    fn=get_engineering_method_callable(unit,entry['api'])
    kwargs=dict(entry['kwargs'])
    request_key=next(k for k in ('request','q','p') if isinstance(kwargs.get(k),BaseModel))
    q=kwargs[request_key]
    if json_boundary:
        # Replay the actual public request with the original declared providers,
        # after the native JSON contract boundary; retain all other bound args.
        q=type(q).model_validate_json(q.model_dump_json());kwargs[request_key]=q
        result=fn(**kwargs);raw=plain(result)
    else:raw=entry['output']
    value=get(raw,path)
    assert type(value) in (float,int) and math.isfinite(value)
    qdata=plain(q)
    upstream_context=kwargs.get('context')
    nested=getattr(q,'transport',None) or q
    asset=getattr(nested,'asset_id',None) or getattr(upstream_context,'asset_id',None) or 'authored-scalar-'+unit
    clock=getattr(nested,'clock_id',None) or getattr(upstream_context,'clock_id',None) or 'authored-static-snapshot'
    dependencies={}
    for key,item in kwargs.items():
        if key==request_key or isinstance(item,(BaseModel,str,int,float,bool,type(None))):continue
        cls=type(item)
        file=inspect.getsourcefile(cls)
        dependencies[key]={'type':cls.__module__+'.'+cls.__qualname__,'source_sha256':file_digest(Path(file)) if file else None}
    context=RunContext(asset_id=asset,clock_id=clock,scenario_id='fluid_observable_'+unit,origin='simulation',
        evidence=(EvidenceRef(id='model-output',citation='Actual calculation of authored simulation request through '+entry['api'],origin='simulation',sha256=digest(raw)),
                  EvidenceRef(id='synthetic-correction',citation='Declared synthetic reading bias, covariance and screen; no field calibration',origin='synthetic')),
        metadata={'original_upstream_origin':getattr(upstream_context,'origin','authored_synthetic_fixture'),
                  'actual_execution_dependencies':dependencies,'actual_request_type':type(q).__module__+'.'+type(q).__qualname__,
                  'upstream_request_sha256':digest(qdata),'selected_output_path':list(path)})
    basis=MeasurementBasis(quantity=quantity,unit=si,definition_id=unit+':'+'.'.join(map(str,path)),evidence_id='model-output')
    return Observable(unit,entry['api'],file_digest(Path(inspect.getsourcefile(fn))),qdata,raw,float(value),basis,context)
