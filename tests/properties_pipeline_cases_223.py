"""Explicit observable handoffs for supported model-to-budget API workflows.

All upstream values are real calculation outputs. The downstream uncertainty
and correction are declared synthetic fixtures, never estimated field accuracy.
"""
from dataclasses import asdict, dataclass, is_dataclass
import importlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import sys

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest, json_value
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import (
    CoverageDeclaration, MeasurementBasis, MeasurementBudgetRequest, MeasurementDecisionRule,
    MeasurementInput, MeasurementLinearization, MeasurementOutput, StandardCovariance,
)

from test_input_properties_223 import BASELINES as FIRST
from test_input_properties_remaining_223 import BASELINES as REMAINING

if os.environ.get('PIPELINE_FORMULA_MATERIAL_SRC'):
    sys.path.insert(0,os.environ['PIPELINE_FORMULA_MATERIAL_SRC'])


def api(name):
    module,symbol=name.rsplit('.',1)
    return getattr(importlib.import_module('pipeline_integrity.'+module),symbol)


def dump(value):
    if hasattr(value,'model_dump'):
        return value.model_dump(mode='json')
    if is_dataclass(value):
        return json_value(asdict(value))
    return json_value(value)


def get(value,path):
    for key in path:
        value=value[key]
    return value


# canonical unit -> API, request fixture, output path, physical quantity, coherentSI unit, pressure reference
SPECS={
 'electrical.conductor_impedance_drop':('electrical_circuit.evaluate_electrical_circuit','circuit',('values','source_line_voltage_V'),'source_line_voltage','V',None),
 'electrical.cp_current_voltage_balance':('dc_tapped_cable.observe_dc_ohmic_drive','cp_drive',('signed_current_A',),'complete_path_signed_DC_current','A',None),
 'electrical.dc_ac_power_balance':('electrical_circuit.evaluate_electrical_circuit','circuit',('values','source_real_power_W'),'source_active_power','W',None),
 'electrical.distributed_pipeline_cp':('distributed_pipe_cp.evaluate_uniform_cp_line',None,('left_potential_displacement_V',),'left_potential_displacement_about_zero_drain_reference','V',None),
 'electrical.earth_electrode_resistance':('groundbed_fv.evaluate_conductive_earth','earth',('electrode_potentials_V','rod'),'electrode_potential_relative_grounded_exterior','V',None),
 'electrical.half_space_point_source_potential':('ground_halfspace.evaluate_ground_halfspace',None,('fields',0,'potential_V_relative_infinity'),'earth_potential_relative_infinity','V',None),
 'electrical.power_factor_compensation':('electrical_circuit.evaluate_reactive_compensation','reactive',('values','required_shunt_capacitive_VAr'),'signed_shunt_reactive_power','V*A',None),
 'electrical.two_state_current_rms':('two_state_current.observe_two_state_current','two_state',('effective_rms_current_A',),'duration_weighted_RMS_current','A',None),
 'electrochemistry.anode_charge_lifetime':('anode_lifetime.evaluate_anode_lifetime','anode',('consumed_charge_C',),'consumed_usable_anode_charge','C',None),
 'emissions.calibrated_overfill_cascade_source':('calibrated_emissions.evaluate_cascade_source','cascade',('vapor_mass_rate_kg_s',),'conditional_cascade_vapor_mass_rate','kg/s',None),
 'emissions.filling_vapor_rate':('calibrated_emissions.evaluate_filling_vapor','filling',('total_vent_vapor_rate_kg_s',),'combined_displacement_and_generation_vapor_mass_rate','kg/s',None),
 'emissions.tank_standing_working_loss':('calibrated_emissions.evaluate_tank_standing_working_loss','tank_loss',('total_loss_kg',),'declared_accounting_window_vapor_mass_loss','kg',None),
 'instrument.compact_prover_two_clock_factor':('compact_prover.evaluate_compact_prover','compact',('observed_pulse_factor_per_m3',),'observed_meter_pulse_factor','1/m^3',None),
 'instrument.vibrating_density_calibration':('vibrating_density.observe_vibrating_element_density','vibrating',('density_kg_m3',),'instrument_calibrated_density','kg/m^3',None),
 'inventory.prescribed_rainfall_drainage':('rainfall_inventory.evaluate_rainfall_inventory',None,('values','final','mass_kg'),'final_prescribed_rainwater_inventory','kg',None),
 'measurement.slick_appearance_thickness':('slick_observation.evaluate_slick_observation','slick',('maximum_volume_m3',),'conditional_visual_band_upper_volume','m^3',None),
 'metrology.master_meter_calibration':('master_meter_proving.evaluate_master_meter_proving','master',('master_true_over_indicated_mass_factor',),'master_true_to_indicated_mass_ratio','1',None),
 'metrology.parallel_identical_meter_runs':('parallel_meter_runs.observe_parallel_meter_run_change','parallel',('remaining_average_meter_differential_pa',),'parallel_run_meter_differential_pressure','Pa','differential'),
 'metrology.ultrasonic_transit':('paired_transit.invert_paired_transit',None,('uniform_medium_sound_speed_m_s',),'uniform_medium_sound_speed','m/s',None),
 'metrology.ultrasonic_wall_thickness':('nde.evaluate_echo_thickness',None,('thickness_m',),'solid_pulse_echo_normal_wall_thickness','m',None),
 'mixture.closed_volume_replace_to_target':('component_replacement.solve_component_replacement',None,('replacement_volume_m3',),'single_withdrawal_equal_refill_volume','m^3',None),
 'mixture.common_volume_specific_gravity':('liquid_blending.evaluate_liquid_blend',None,('blended_specific_gravity',),'common_reference_volume_blended_specific_gravity','1',None),
 'mixture.gas_calorific_value':('gas_combustion_reference.observe_gas_combustion_reference',None,('dry_molar_combustion_energy_J_mol',),'dry_molar_combustion_energy_at_declared_HHV_reference','J/mol',None),
 'mixture.gas_molar_mass_heat_capacity':('gas_caloric_mixture.evaluate_gas_caloric_mixture',None,('ideal_gas_molar_cp_J_mol_K',),'supplied_ideal_mixture_molar_constant_pressure_heat_capacity','J/mol/K',None),
 'mixture.glycol_freezing_target':('glycol_freezing_target.evaluate_glycol_freezing_target',None,('required_glycol_mass_fraction',),'required_glycol_mass_fraction_on_selected_freezing_branch','1',None),
 'mixture.inverse_root_sus':('liquid_blending.evaluate_liquid_blend',None,('blended_SUS_efflux_time_s',),'empirical_blended_SUS_efflux_time','s',None),
 'mixture.kinematic_viscosity_index_blend':('kinematic_viscosity_blend.evaluate_kinematic_viscosity_blend',None,('blended_kinematic_viscosity_m2_s',),'conditional_mass_weighted_kinematic_viscosity','m^2/s',None),
 'mixture.oil_water_pseudoliquid_viscosity':('oil_water_viscosity_estimate.estimate_oil_water_viscosity',None,('estimated_dynamic_viscosity_Pa_s',),'conditional_parallel_shear_dynamic_viscosity','Pa*s',None),
 'mixture.pseudocritical_gas':('gas_pseudocritical.estimate_gas_pseudocritical',None,('uncorrected_pseudo_temperature_K',),'Kay_pseudocritical_temperature_not_actual_critical_point','K',None),
 'mixture.sample_cylinder_pressure_purge':('sample_pressure_purge.evaluate_sample_pressure_purge',None,('points',-1,'original_gas_mole_fraction'),'remaining_original_gas_mole_fraction_after_reviewed_purge','1',None),
 'mixture.water_saturated_gas':('saturated_gas_fugacity.observe_saturated_gas_fugacity','saturated',('water_mole_fraction',),'water_mole_fraction_from_reviewed_fugacity_equality','1',None),
 'moisture.dewpoint_relative_humidity':('water_vapor_observation.observe_water_vapor',None,('condensation_point_temperature_K',),'equilibrium_water_condensation_point_temperature','K',None),
 'properties.acoustic_bulk_density':('acoustic_density.observe_acoustic_density',None,('density_kg_m3',),'same_state_unbounded_acoustic_density','kg/m^3',None),
 'properties.black_oil_phase_partition':('black_oil_phase_observer.observe_black_oil_partition',None,('oil_phase_mass_kg',),'oil_phase_mass_including_dissolved_reference_gas','kg',None),
 'properties.crude_oil_bulk_modulus':('identified_liquid_bulk_modulus.observe_identified_liquid_bulk_modulus',None,('isothermal_bulk_modulus_Pa',),'isothermal_tangent_bulk_modulus_about_declared_material_state','Pa','differential'),
 'properties.gas_heating_value_gravity_fit':('gravity_constrained_calorific.observe_gravity_constrained_calorific',None,('dry_molar_combustion_energy_J_mol',),'binary_fuel_class_dry_molar_combustion_energy','J/mol',None),
 'properties.suspension_viscosity':('suspension_viscosity.evaluate_suspension_viscosity',None,('effective_dynamic_viscosity_Pa_s',),'reviewed_hard_sphere_suspension_dynamic_viscosity','Pa*s',None),
 'properties.water_resistivity_solids_fit':('ionic_water_conductivity.observe_ionic_water_conductivity',None,('conductivity_S_m',),'state_matched_bulk_ionic_conductivity','S/m',None),
 'properties.liquid_water_eos_response':('liquid_water_response.observe_liquid_water_response',None,('specific_volume_m3_kg',),'same_state_single_liquid_water_specific_volume','m^3/kg',None),
 'pump.liquid_clearance_volumetric_efficiency':('liquid_chamber_cycle.evaluate_liquid_chamber_cycle',None,('net_transferred_mass_kg_per_cycle',),'net_liquid_mass_delivered_per_reviewed_chamber_cycle','kg',None),
 'vacuum.pumpdown_time':('vacuum_pumpdown.evaluate_vacuum_pumpdown',None,('points',-1,'pressure_Pa_abs'),'absolute_chamber_pressure_at_declared_elapsed_time','Pa','absolute'),
 'vacuum.water_evaporation_time':('vacuum_water_evaporation.evaluate_vacuum_water_evaporation',None,('time_to_free_water_depletion_s',),'conditional_free_liquid_depletion_elapsed_time','s',None),
 'acoustics.supplied_compressor_volume_correlation':('compressor_volume_coordinate.evaluate_compressor_surge_volume','misc',('suction_surge_volume_coordinate_m3',),'supplied_correlation_suction_surge_volume_coordinate','m^3',None),
 'conservation.observed_section_mass':('mass_balance_section.evaluate_section_mass_balance','misc',('mass_residual_kg',),'signed_integrated_control_volume_mass_residual','kg',None),
 'elbow_bending_stress_limit':('pressure_bending_budget.evaluate_pressure_bending_budget','misc',('values','maximum_longitudinal_tensile_stress_pa'),'nominal_maximum_longitudinal_tensile_stress_relative_unloaded_state','Pa','differential'),
 'elbow_pressure_stress':('elbow_pressure_stress.evaluate_elbow_pressure_stress','misc',('intrados_nominal_hoop_pa',),'intrados_OD_nominal_hoop_stress_due_to_pressure_difference','Pa','differential'),
 'energy.linear_liquid_storage':('stored_liquid_energy.evaluate_linear_liquid_compression','misc',('stored_elastic_energy_j',),'linear_liquid_compression_elastic_work_relative_reference_pressure','J',None),
 'fluid.horizontal_jet_discharge':('horizontal_jet.invert_horizontal_jet','misc',('values','tracked_horizontal_velocity_m_s'),'tracked_ballistic_parcel_horizontal_speed','m/s',None),
 'leak.supplied_small_discrepancy_location':('leak_inverse_relations.locate_small_leak_from_discrepancies','misc',('distance_from_upstream_m',),'small_settled_discrepancy_distance_from_upstream','m',None),
 'optics.point_source_illuminance':('point_illuminance.evaluate_point_illuminance','misc',('values','total_illuminance_lux'),'point_source_front_face_photopic_illuminance','lux',None),
 'sensing.electromagnetic_cable_flight':('leak_inverse_relations.locate_dielectric_cable','misc',('position_m',),'nonmagnetic_dielectric_round_trip_cable_position','m',None),
 'sensing.uniform_resistive_cable_coordinate':('sensing_location.locate_resistive_cable','misc',('position_m',),'uniform_two_conductor_DC_short_position','m',None),
 'transport.ideal_sensor_tube_parcel':('vapor_tube.evaluate_vapor_tube_delay','misc',('parcel_arrival_delay_s',),'ideal_passive_tube_parcel_arrival_delay','s',None),
 'vent.supplied_empirical_area_correlation':('reviewed_dynamic_correlations.evaluate_deflagration_vent_coordinate','misc',('vent_area_coordinate_m2',),'supplied_empirical_pressure_reference_vent_area_coordinate','m^2',None),
 'waves.prescribed_one_dimensional_travel_time':('leak_inverse_relations.locate_from_known_interface_reflection','misc',('interface_to_source_m',),'uniform_speed_interface_to_source_reflection_distance','m',None),
}

_helpers={}
from root_pipeline_cases_223 import ROOT_SPECS
SPECS.update({unit:(name,'root',path,quantity,si,reference)
    for unit,(name,path,quantity,si,reference) in ROOT_SPECS.items()})
def helper(name):
    if name not in _helpers:
        path=Path(__file__).with_name('test_'+name+'.py')
        spec=importlib.util.spec_from_file_location('_pipeline_helper_'+name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        _helpers[name]=module
    return _helpers[name]


def owner_client():
    from matintegrity.contracts import Evidence,RunContext as OwnerContext
    from matintegrity.models.inspection import specifications as inspection_specs
    from matintegrity.models.protection import specifications as protection_specs
    from matintegrity.registry import ModelRegistry
    from pipeline_integrity.adapters import MaterialClient
    registry=ModelRegistry()
    for spec in (*inspection_specs(),*protection_specs()):
        if spec.model_id in {'inspection.ultrasonic_thickness','protection.cathodic_budget'}:
            registry.register(spec)
    return MaterialClient(evaluator=registry.run,context_factory=OwnerContext,evidence_factory=Evidence,
                          registry_factory=lambda:registry)


def context_for(request,unit):
    nested=getattr(request,'gas_basis',None) or getattr(request,'budget',None) or request
    return RunContext(asset_id=getattr(nested,'asset_id','pipe'),clock_id=getattr(nested,'clock_id','clock'),
        scenario_id='observable_pipeline_'+unit,origin='simulation',
        evidence=(EvidenceRef(id='input',citation='Authored simulation inputs and state/calibration declarations',
            origin='synthetic',sha256=digest(dump(request))),))


def misc_request(unit):
    if unit=='acoustics.supplied_compressor_volume_correlation':
        return helper('compressor_volume_coordinate_independent').request()
    if unit=='conservation.observed_section_mass':
        return helper('section_mass_balance_individual_local').case(final_inventory_mass_kg=113.)
    if unit=='elbow_bending_stress_limit':
        return helper('pressure_bending_budget_independent').request()
    if unit=='elbow_pressure_stress':
        cls=api('elbow_pressure_stress.ElbowPressureStressRequest')
        return cls(pressure_difference_pa=2e6,outside_diameter_m=.2,wall_m=.002,centreline_bend_radius_m=.3,
            circumferential_angles_rad=(-math.pi/2,0.,math.pi/2),maximum_reviewed_wall_inside_radius_ratio=.1,
            uniform_wall_thin_curved_membrane_basis_reviewed=True)
    if unit=='energy.linear_liquid_storage':
        return api('stored_liquid_energy.LinearLiquidCompression')(1e6,.2,2e9)
    if unit=='fluid.horizontal_jet_discharge':
        return helper('horizontal_jet_individual').make()
    if unit=='leak.supplied_small_discrepancy_location':
        return api('leak_inverse_relations.DiscrepancyLocationRequest')(upstream_discrepancy_m3_s=.003,
            downstream_discrepancy_m3_s=.001,section_length_m=100.,small_leak_fully_propagated_linear_resistance_reviewed=True)
    if unit=='optics.point_source_illuminance':
        return helper('point_illuminance_individual').make()
    if unit=='sensing.electromagnetic_cable_flight':
        return api('leak_inverse_relations.DielectricCableRangingRequest')(relative_permittivity=4.,
            elapsed_round_trip_s=4*100/299792458.,nonmagnetic_nondispersive_dielectric_reviewed=True)
    if unit=='sensing.uniform_resistive_cable_coordinate':
        return api('sensing_location.ResistiveCableRequest')(voltage_v=2.,current_a=1.,
            resistance_per_length_ohm_m=.01,resistance_basis='single_conductor')
    if unit=='transport.ideal_sensor_tube_parcel':
        return helper('vapor_tube_individual_local').delay()
    if unit=='vent.supplied_empirical_area_correlation':
        return helper('vent_coordinate_independent').request()
    if unit=='waves.prescribed_one_dimensional_travel_time':
        return api('leak_inverse_relations.ReflectionLocationRequest')(sensor_to_interface_m=1000.,
            direct_arrival_s=0.,reflected_arrival_s=.5,wave_speed_m_s=1000.,
            known_constant_pressure_interface_and_uniform_speed_reviewed=True)
    raise KeyError(unit)


@dataclass(frozen=True)
class Observable:
    unit_id: str
    source_api: str
    source_sha256: str
    request: dict
    result: dict
    value: float
    basis: MeasurementBasis
    context: RunContext


def produce(unit,tmp_path,*,json_boundary=False):
    if unit in ROOT_SPECS:
        from root_pipeline_cases_223 import produce_root
        return produce_root(unit,tmp_path,json_boundary=json_boundary)
    source_name,key,path,quantity,si_unit,pressure_reference=SPECS[unit]
    function=api(source_name)
    if unit=='metrology.ultrasonic_wall_thickness':
        trace,q,c=helper('ultrasonic_thickness_physical_qualification').make_inputs(tmp_path)
        c=c.model_copy(update={'origin':'simulation'})
        if json_boundary:q=type(q).model_validate_json(q.model_dump_json())
        result=function(trace,q,c,materials=owner_client())
    else:
        if key=='misc':
            q=misc_request(unit)
        elif key:
            cls,data=FIRST[key]
            q=cls.model_validate(data)
        else:
            cls,data,_=REMAINING[unit]
            q=cls.model_validate(data)
        if unit=='emissions.tank_standing_working_loss':
            episode=q.episodes[0].model_copy(update={'breathing_exchange_m3':1.,'working_displaced_gas_m3':2.})
            q=q.model_copy(update={'episodes':(episode,)})
        if json_boundary:
            q=type(q).model_validate_json(q.model_dump_json()) if hasattr(q,'model_dump_json') else type(q)(**json.loads(json.dumps(asdict(q))))
        c=context_for(q,unit)
        kwargs={}
        if 'context' in inspect.signature(function).parameters:kwargs['context']=c
        if unit=='electrochemistry.anode_charge_lifetime':kwargs['client']=owner_client()
        result=function(q,**kwargs)
    raw=dump(result)
    status=raw.get('status',raw.get('state','computed'))
    if status not in {'computed','exchange_checked'}:
        raise AssertionError(f'{unit}: actual upstream API did not compute: {raw}')
    value=get(raw,path)
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
        raise AssertionError(f'{unit}: declared output {path} is not a finite observable')
    source_path=Path(inspect.getsourcefile(function))
    qdata=dump(q)
    evidence=EvidenceRef(id='model-output',citation='Actual simulation output of '+unit+' via '+source_name,
        origin='simulation',sha256=digest(raw))
    additions=(evidence,EvidenceRef(id='synthetic-correction',citation='Declared synthetic additive bias/covariance and screening rule; no field accuracy inference',origin='synthetic'))
    c=c.model_copy(update={'evidence':c.evidence+additions})
    basis=MeasurementBasis(quantity=quantity,unit=si_unit,definition_id=unit+':'+'.'.join(map(str,path)),
        evidence_id='model-output',pressure_reference=pressure_reference)
    return Observable(unit,'pipeline_integrity.'+source_name,file_digest(source_path),qdata,raw,float(value),basis,c)


def budget_for(observation,*,end_to_end=False):
    x=observation.value
    scale=max(abs(x),1e-12)
    bias=.01*scale
    reading=x+bias if end_to_end else x
    correction=-bias if end_to_end else bias
    inputs=(MeasurementInput(id='reading',estimate=reading,basis=observation.basis,evidence_id='model-output',valid=True),
            MeasurementInput(id='additive-correction',estimate=correction,basis=observation.basis,
                evidence_id='synthetic-correction',valid=True))
    estimate=math.fsum((reading,correction))
    covariance=((.0004*scale*scale,.0001*scale*scale),(.0001*scale*scale,.0001*scale*scale))
    context=observation.context
    snapshot_time=observation.request.get('time_s',observation.request.get('gas_basis',{}).get('time_s'))
    if isinstance(snapshot_time,(tuple,list)):
        # Rainfall observable is the explicitly selected final inventory, not the full time series.
        if observation.unit_id!='inventory.prescribed_rainfall_drainage':raise ValueError('time series needs an explicit observable snapshot mapping')
        snapshot_time=snapshot_time[-1]
    linear=MeasurementLinearization(model_id='declared_additive_simulation_observable_correction',model_version='1',
        provider_source_sha256=observation.source_sha256,
        configuration_sha256=digest({'upstream_model':observation.unit_id,'upstream_api':observation.source_api,
            'upstream_request':observation.request,'upstream_result_sha256':digest(observation.result),
            'meaning':observation.basis.model_dump(mode='json'),'mode':'synthetic_reading_correction' if end_to_end else 'observable_handoff'}),
        inputs_sha256=digest(inputs),outputs=(MeasurementOutput(id='corrected-observable',estimate=estimate,basis=observation.basis),),
        jacobian=((1.,1.),),evidence_id='synthetic-correction',local_approximation_reviewed=True)
    rule=MeasurementDecisionRule(output_id='corrected-observable',lower_limit=x-.1*scale,
        upper_limit=x+.1*scale,comparison='expanded_interval',evidence_id='synthetic-correction',reviewed=True)
    request=MeasurementBudgetRequest(id='observable-budget-'+observation.unit_id,asset_id=context.asset_id,
        clock_id=context.clock_id,event_id=observation.request.get('event_id',observation.request.get('gas_basis',{}).get('event_id','caller-bound-model-snapshot')),
        time_s=snapshot_time,
        inputs=inputs,linearization=linear,
        covariance=StandardCovariance(input_ids=('reading','additive-correction'),values=covariance,
            evidence_id='synthetic-correction',definition_reviewed=True),
        coverage=CoverageDeclaration(factor=2.,evidence_id='synthetic-correction',reviewed=True),
        decision_rules=(rule,))
    return request,context,scale


def save_receipt(stage,unit,observation,record):
    root=Path(__file__).resolve().parents[1]/'artifacts/hpc/local-results'/('properties-'+stage+'-223')
    root.mkdir(parents=True,exist_ok=True)
    path=root/(unit.replace('.','_')+'.json')
    data=dict(canonical_unit='proposal.'+unit,source_api=observation.source_api,source_sha256=observation.source_sha256,
        source_request=observation.request,source_result=observation.result,source_origin='simulation',
        actual_observable=observation.value,observable_basis=observation.basis.model_dump(mode='json'),downstream=record,
        scope='supported simulation observable API handoff/correction/uncertainty decision; no fullplant physical qualification')
    path.write_text(json.dumps(json_value(data),ensure_ascii=False,indent=2),encoding='utf-8')
    return path
