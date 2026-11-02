"""Physical prediction outputs enter a distinct, unit-aware estimation model.

The second observation and its absolute covariance are authored synthetic inputs,
not inferred calibration or field evidence. Each handoff keeps the exact scalar
observable (including stress sign, pressure reference and whole-cycle meaning).
"""
import hashlib
import json
import math
import re

import pytest

from mechanics_workflow_cases_223 import ROOT,UNITS,encoded,plain,run_upstream,save_trace

# unit -> exact computed output path, coherent unit, observable definition.
OBSERVABLES={
 'anchor_group_reaction':('tension_per_tension_row_anchor_n','N','tension_in_one_anchor_of_declared_row'),
 'beam.linear_dynamic_euler_bernoulli':('modes[0].frequency_hz','1/s','first_requested_linear_frame_mode_frequency'),
 'beam.linear_static_euler_bernoulli':('combinations.service.nodes.anchor.reaction_force_n[1]','N','anchor_global_Y_support_reaction'),
 'branch_intersection_limit_pressure':('limit_pressure_coordinate_pa','Pa','internal_minus_external_limit_pressure_coordinate'),
 'buckle_propagation_arrester':('finite_length_arrest_pressure_coordinate_pa','Pa','external_minus_internal_arrester_pressure_coordinate'),
 'buried_upheaval_buckling':('solution.critical_positive_load_factor','1','critical_multiplier_of_declared_reference_load_pattern'),
 'contact.prescribed_pressure_coulomb_axial':('signed_axial_drag_force_N','N','signed_axial_sliding_drag_force'),
 'cylindrical_local_load_attenuation':('moment_per_circumference_n[0]','N','edge_bending_moment_per_circumferential_length'),
 'damage.circumferential_tensile_section':('nominal_axial_failure_stress_coordinate_pa','Pa','nominal_axial_failure_stress_coordinate'),
 'damage.creep_rupture_fraction':('values.time_fraction_damage','1','deterministic_creep_time_fraction_not_probability'),
 'damage.fatigue_cycle_fraction':('native.payload.values.damage','1','deterministic_whole_cycle_usage_not_probability'),
 'damage.prescribed_constant_loss_time':('time_to_boundary_s','s','elapsed_time_to_supplied_wall_boundary'),
 'damage.supplied_profile_pressure_capacity':('values.declared_model_pressure_difference_capacity_Pa','Pa','internal_minus_external_profile_pressure_coordinate'),
 'dent_rebound_fatigue':('original_dent_depth_coordinate_m','m','original_dent_depth_coordinate_not_penetrating_wall_loss'),
 'dynamics.linear_sdof':('points[1].displacement_m','m','signed_generalized_displacement_at_selected_time'),
 'elastic_thermal_compression_buckling_variant':('solution.critical_positive_load_factor','1','critical_multiplier_of_supplied_thermal_reference_pattern'),
 'elasticity.closed_pressure_cylinder':('material_records[0].native.payload.values.hoop_stress_Pa','Pa','signed_total_bore_hoop_stress_in_declared_traction_datum'),
 'elasticity.incremental_pipe_compliance':('diameter_increment_m','m','increment_of_internal_diameter'),
 'elasticity.supplied_pipe_area_observation':('linearized_area_m2','m**2','first_order_area_about_declared_reference'),
 'engineering.target.constant_stress_viscoelastic_strain':('values.total_strain[1]','1','total_tensile_engineering_strain_at_declared_time'),
 'engineering.target.covered_pipe_flotation_balance':('vertical_margin_n_m','N/m','downward_minus_factored_upward_force_per_pipe_length'),
 'engineering.target.local_wind_pressure_force':('wind_force_N','N','projected_wind_drag_force'),
 'engineering.target.nut_factor_torque_preload':('values.preload_N','N','conditional_bolt_preload_from_applied_torque'),
 'engineering.target.seabed_static_margin':('values.lateral_margin_n_m','N/m','supplied_static_lateral_resistance_margin'),
 'engineering.target.soil_prism_overburden':('total_vertical_pressure_pa','Pa','compressive_vertical_prism_load_at_declared_plane'),
 'engineering.target.stationary_section_hydrodynamic_load':('total_horizontal_n_m','N/m','signed_global_horizontal_hydrodynamic_line_force'),
 'engineering.target.strouhal_frequency_screen':('lift_frequency_Hz','1/s','crossflow_lift_shedding_frequency'),
 'engineering.target.supplied_gasket_required_preload':('governing_demand_n','N','governing_required_bolt_preload_coordinate'),
 'fabrication.supplied_neutral_axis_curvature':('values.signed_outer_face_engineering_strain','1','outer_face_strain_relative_to_neutral_arc'),
 'fatigue.supplied_stress_life':('curve_life_cycles','1','whole_cycles_to_supplied_curve_endpoint_not_reversals'),
 'fatigue_mean_stress_correction':('equivalent_fully_reversed_amplitude_pa','Pa','equivalent_fully_reversed_stress_amplitude'),
 'flange_external_load_bolt_stress':('bolts[0].normal_stress_pa','Pa','signed_normal_stress_in_selected_bolt'),
 'fracture.elastic_energy_release':('elastic_energy_release_j_m2','J/m**2','elastic_mode_I_energy_release_per_projected_crack_area'),
 'fracture.growth_threshold_critical_modifiers':('modified_rate_m_cycle','m','depth_increment_per_whole_cycle_with_count_dimensionless'),
 'fracture.local_energy_arrest':('driving_energy_coordinate_j_m2','J/m**2','local_available_driving_energy_per_declared_crack_area'),
 'fracture.mode_i_coordinate':('values.stress_intensity_Pa_sqrt_m','Pa*m**0.5','opening_mode_I_intensity_for_half_crack_length'),
 'fracture.paris_growth':('growth_rate_m_per_whole_cycle','m','region_II_depth_increment_per_whole_cycle'),
 'fracture.surface_flaw_proof_survival':('final_depth_m','m','final_irreversible_surface_flaw_depth'),
 'fragmentation.supplied_empirical_capacity':('peak_pressure_coordinate_pa','Pa','peak_pressure_for_supplied_impulse_work_coordinate'),
 'ground_movement_pipe_response':('solution.soil_response[0].axes[0].pipe_displacement_m','m','selected_pipe_node_global_Y_displacement'),
 'impact.elastic_energy_bound':('peak_spring_reaction_n','N','peak_linear_target_spring_reaction_coordinate'),
 'impact_to_fracture_toughness_correlation':('estimated_toughness_over_strength_sqrt_m','m**0.5','yield_normalized_empirical_toughness_coordinate'),
 'local_pipe_indentation':('indentation_coordinate_m','m','selected_loading_indentation_not_residual_dent'),
 'mechanical.prescribed_pressure_work_actuation':('values.net_extension_force_n','N','net_piston_force_along_declared_extension_axis'),
 'mechanical.supplied_uniform_shear_cut_load':('values.nominal_uniform_shear_force_n','N','uniform_simultaneous_shear_cut_force'),
 'mechanics.key_shear_bearing':('values.key_results[0].nominal_signed_shear_stress_Pa','Pa','signed_nominal_shear_stress_on_selected_key_plane'),
 'mechanics.relief_jet_reaction':('values.fluid_force_on_hardware_N[0]','N','fluid_force_on_hardware_global_X'),
 'notch_threshold_correlation':('threshold_intensity_range_coordinate_pa_sqrt_m','Pa*m**0.5','threshold_intensity_range_not_maximum_intensity'),
 'pendulum_swing_response':('natural_frequency_hz','1/s','small_angle_gravity_pendulum_frequency'),
 'pipeline.buried_aboveground_transition':('values.transfer_length_m','m','conditional_constant_resistance_axial_transfer_length'),
 'pipeline.lowering_elastic_compatibility':('values.signed_additional_engineering_strain','1','additional_axial_strain_against_initial_material_arc'),
 'plasticity.hardening_membrane_pressure_turn':('pressure_limit_pa','Pa','membrane_pressure_turning_point_coordinate'),
 'plasticity.perfect_plastic_cylinder':('full_wall_plastic_pressure_difference_pa','Pa','full_wall_J2_internal_minus_external_pressure'),
 'plate.clamped_circular_elastic_pressure':('values.center_displacement_along_load_m','m','clamped_plate_central_displacement_along_load'),
 'pressure.nominal_membrane':('','Pa','nominal_outside_diameter_hoop_stress_coordinate'),
 'rigging.prescribed_guy_equilibrium':('coplanar_load_induced_pull_n','N','load_induced_coplanar_guy_pull'),
 'rigging.pulley_mechanical_advantage':('required_lead_pull_N','N','required_lead_rope_pull_at_declared_motion'),
 'saddle_local_capacity':('nominal_saddle_reaction_capacity_coordinate_n','N','conditional_local_saddle_reaction_coordinate'),
 'shaft.material_capacity':('values.signed_surface_shear_stress_pa','Pa','signed_outer_surface_Saint_Venant_shear_stress'),
 'shell.elastic_ring_pressure_instability':('stability_event.pressure_bracket_Pa[0]','Pa','lower_stable_external_pressure_bracket_not_observation'),
 'shell.linear_radial_pressure_dynamics':('modes[0].frequency_hz','1/s','uniform_radial_breathing_frequency'),
 'shell.local_normal_membrane_equilibrium':('values.resultant_1_n_m','N/m','signed_principal_membrane_resultant_1'),
 'shell.short_stiff_pipe_supplied_membrane_yield_reference':('external_over_internal_reference_pressure_pa','Pa','external_minus_internal_short_pipe_reference'),
 'shell.supplied_intermediate_collapse_correlation':('external_over_internal_reference_pressure_pa','Pa','external_minus_internal_intermediate_shell_reference'),
 'shell.supplied_oval_pressure_interaction':('external_over_internal_reference_pressure_pa','Pa','external_minus_internal_oval_shell_reference'),
 'sleeve_end_fillet_stress':('signed_nominal_shear_coordinate_pa','Pa','signed_nominal_sleeve_end_joint_shear'),
 'smooth_specimen_strain_life':('values.cycles_to_failure','1','whole_cycles_to_declared_smooth_specimen_endpoint'),
 'soil.surface_load_and_pipe_contact':('vertical_compressive_continuum_stress_pa','Pa','vertical_compressive_halfspace_stress_at_point'),
 'strain_rate_strength_correlation':('dynamic_strength_coordinate_pa','Pa','dynamic_strength_coordinate_under_declared_strength_kind'),
 'support_force_movement':('natural_frequency_Hz','1/s','linear_support_oscillator_natural_frequency'),
 'thermoelasticity.radial_cylinder':('rows[0].hoop_stress_tension_positive_pa','Pa','selected_radius_tension_positive_hoop_stress'),
 'thermoelasticity.restrained_cylinder':('axial_stress_tension_positive_pa','Pa','zero_axial_strain_axial_stress_tension_positive'),
 'torsion.annular_elastic_section':('values.signed_surface_shear_stress_pa','Pa','annular_section_outer_surface_signed_shear_stress'),
 'uniaxial_constitutive_coordinate':('total_strain','1','monotonic_uniaxial_strain_in_declared_measure'),
 'vortex_lock_in_response':('lockin_displacement_amplitude_coordinate_m','m','conditional_lockin_displacement_amplitude'),
 'waves.cylindrical_scalar_wave':('rows[0].scalar_velocity_m_s','m/s','selected_scalar_wave_velocity_not_fluid_pressure'),
 'weld_group_section_stress':('maximum_nominal_resultant_stress_pa','Pa','maximum_nominal_weld_line_resultant_stress'),
 'welding.supplied_burnthrough_limit':('peak_sampled_inner_surface_temperature_k','K','peak_sampled_inner_wall_surface_temperature'),
 'yield.pressurized_isotropic_cylinder':('predicted_first_yield_excess_pressure_pa','Pa','elastic_first_yield_internal_minus_external_pressure'),
}
assert set(OBSERVABLES)==set(UNITS)


def at(value,path):
    if not path:return value
    for name,index in re.findall(r'([^.\[\]]+)|\[(\d+)\]',path):
        value=value[int(index)] if index else value[name]
    return value


def upstream_output(unit,trace):
    path,_,_=OBSERVABLES[unit]
    for entry in reversed(trace):
        try:value=at(entry['output'],path)
        except (KeyError,IndexError,TypeError):continue
        if type(value) in (int,float) and math.isfinite(value):return entry,float(value)
    raise AssertionError('actual expected physical output absent: '+unit+'::'+path)


def identity(unit,entry):
    source=entry['args'][0] if entry['args'] else None
    context=next((q for q in (*entry['args'],*entry['kwargs'].values()) if type(q).__name__=='RunContext'),None)
    frame=getattr(source,'frame',None)
    asset=getattr(source,'asset_id',None) or getattr(frame,'asset_id',None) or getattr(context,'asset_id',None) or 'synthetic-scalar-'+unit
    clock=getattr(source,'clock_id',None) or getattr(frame,'clock_id',None) or getattr(context,'clock_id',None) or 'authored-static-clock'
    source_frame=getattr(source,'coordinate_frame_id',None) or getattr(source,'frame_id',None)
    material=getattr(source,'material_condition_id',None)
    if material is None:
        materials=getattr(source,'materials',None) or getattr(frame,'materials',None)
        if materials:material=[m.id for m in materials]
    return asset,clock,source_frame,material


def physical_handoff(unit,entry,value):
    """Compute a unit-aware GLS fusion; preserve bound-only ring pressure separately."""
    from pipeline_integrity.contracts import EvidenceRef,RunContext,digest
    path,unit_token,definition=OBSERVABLES[unit]
    asset,clock,source_frame,material=identity(unit,entry)
    evidence_id='real-upstream-result'
    source_request=plain(entry['args'][0]) if entry['args'] else {}
    source_request_digest=digest(source_request)
    metadata=dict(source_api=entry['api'],source_result_digest=digest(entry['output']),source_output_path=path,
        source_request_digest=source_request_digest,
        source_observable_definition=definition,source_frame_id=source_frame,
        source_material_condition_id=material,missing_material_identity_inferred=False,
        quantity_is_failure_probability=False,observation_covariance_origin='authored_synthetic_absolute_covariance')
    context=RunContext(asset_id=asset,clock_id=clock,scenario_id='actual-output-to-next-input',origin='synthetic',
        evidence=(EvidenceRef(id=evidence_id,citation='Actual upstream calculation and authored downstream test basis',
                              origin='synthetic',sha256=digest(entry['output'])),),metadata=metadata)
    if unit=='shell.elastic_ring_pressure_instability':
        # A deterministic numerical bracket endpoint is a load condition, not a
        # noisy observation. Preserve external-minus-internal sign when applying
        # it to the actual two-traction cylinder adapter.
        from pipeline_integrity.contracts import Pressure
        from pipeline_integrity.material_exchange import PressureSection,evaluate_pressure_section
        ring=entry['args'][0]
        radius=ring.mean_reference_centerline_radius_m
        inner=radius-ring.wall_thickness_m/2
        outer=radius+ring.wall_thickness_m/2
        q=PressureSection(asset_id=asset,location_id=ring.cross_section_id,clock_id=clock,time_s=0.,inner_radius_m=inner,
            outer_radius_m=outer,evaluation_radii_m=(inner,radius,outer),
            internal_pressure=Pressure(value=1e5,unit='Pa',reference='absolute'),
            external_pressure=Pressure(value=1e5+value,unit='Pa',reference='absolute'),pressure_loaded_closed_ends=False,
            geometry_evidence_id=evidence_id,pressure_evidence_id=evidence_id)
        r=evaluate_pressure_section(q,context);assert r['status']=='computed'
        assert q.external_pressure.value-q.internal_pressure.value==pytest.approx(value,abs=1e-10)
        assert r['boundary_traction_residuals']['outer_radial_traction_pa']==pytest.approx(0.,abs=1e-7)
        assert q.outer_radius_m-q.inner_radius_m==pytest.approx(ring.wall_thickness_m,rel=2e-12,abs=0.)
        return dict(route='external_pressure_bracket_to_actual_annular_traction',context=context.model_dump(mode='json'),
                    downstream_request=q.model_dump(mode='json'),downstream_result=r)
    from pipeline_integrity.constrained_metrology import ConstrainedGLSRequest,GLSParameter,evaluate_constrained_gls
    from pipeline_integrity.metrology import MeasurementBasis,MeasurementInput,StandardCovariance
    # Each quantity has its own exact definition; equal SI dimensions alone do
    # not permit mixing dent/penetration, amplitudes/stresses, or cycles/reversals.
    basis=MeasurementBasis(quantity=definition,unit=unit_token,
        definition_id='exact-observable:'+unit+':'+path+':'+source_request_digest,evidence_id=evidence_id,
        pressure_reference=('absolute' if unit=='elasticity.closed_pressure_cylinder' else 'differential')
            if unit_token=='Pa' else None)
    scale=max(abs(value),1e-6)
    sigma=.02*scale
    second=value+sigma
    observations=(MeasurementInput(id='computed-model-observation',estimate=value,basis=basis,evidence_id=evidence_id,valid=True),
                  MeasurementInput(id='authored-independent-observation',estimate=second,basis=basis,evidence_id=evidence_id,valid=True))
    request=ConstrainedGLSRequest(id='fusion:'+unit,asset_id=asset,clock_id=clock,event_id='same-physical-snapshot',time_s=0.,
        observations=observations,parameters=(GLSParameter(id='same-observable',basis=basis,scale=scale,evidence_id=evidence_id),),
        design_matrix=((1.,),(1.,)),design_coefficient_units=('1',),observation_scales=(scale,scale),
        covariance=StandardCovariance(input_ids=tuple(o.id for o in observations),
            values=((sigma*sigma,0.),(0.,4*sigma*sigma)),evidence_id=evidence_id,definition_reviewed=True),
        declared_covariance_rank=2,exact_linear_model_and_absolute_covariance_reviewed=True,
        singular_rank_and_exact_directions_reviewed=True,model_evidence_id=evidence_id,evidence_ids=(evidence_id,))
    result=evaluate_constrained_gls(request,context)
    assert result['status']=='computed',result['issues']
    assert request.observations[0].estimate==value
    assert request.observations[0].basis==request.parameters[0].basis
    assert result['request']['asset_id']==asset and result['context']['clock_id']==clock
    assert result['context']['metadata']['source_frame_id']==source_frame
    assert result['context']['metadata']['source_material_condition_id']==material
    reference=value+sigma/5
    assert result['parameter_estimates'][0]==pytest.approx(reference,rel=5e-13,abs=1e-14*scale)
    assert result['parameter_covariance']['values'][0][0]==pytest.approx(.8*sigma*sigma,rel=5e-13,abs=0.)
    assert result['probability_or_coverage_inferred'] is False
    return dict(route='physical_prediction_to_exact_observable_GLS',source_unit=unit_token,
                downstream_request=request.model_dump(mode='json'),downstream_result=result)


@pytest.mark.parametrize('unit',UNITS,ids=UNITS)
def test_actual_computed_output_enters_distinct_downstream_calculation(unit,monkeypatch,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind','integration')
    trace=run_upstream(unit,monkeypatch)
    entry,value=upstream_output(unit,trace)
    handoff=physical_handoff(unit,entry,value)
    assert handoff['downstream_request']
    record_property('handoff_route',handoff['route'])
    record_property('source_output_path',OBSERVABLES[unit][0])
    folder=ROOT/'artifacts/mechanics-workflow-traces-223/integration'
    folder.mkdir(parents=True,exist_ok=True)
    body=dict(canonical_unit=unit,source_api=entry['api'],source_result_digest=hashlib.sha256(encoded(entry['output']).encode()).hexdigest(),
              source_output_value=value,**handoff)
    (folder/(unit+'.json')).write_text(json.dumps(plain(body),ensure_ascii=False,indent=2),encoding='utf-8')
