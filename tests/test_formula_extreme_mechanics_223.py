"""Executed independent references and numeric boundary cases for mechanics."""
import inspect
import importlib
import json
import math
import os
import runpy
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
OWNER=Path(os.environ.get('PIPELINE_MATERIAL_SRC',str(ROOT.parent.parent/'재료_부식 평가 툴 프로젝트/matintegrity/src')))
if OWNER.is_dir() and str(OWNER) not in sys.path:sys.path.insert(0,str(OWNER))
MODULES={}
CASES=[]


def add(unit,file,formula,extreme=None):
    CASES.append((unit,'formula','test_'+file+'.py',formula,{}))
    if extreme:CASES.append((unit,'extreme','test_'+file+'.py',extreme,{}))


add('anchor_group_reaction','anchor_row_demand_independent',
    'test_minimum_complementary_energy_with_independent_force_moment_constraints','test_zero_moment_preserves_shear_only_demand')
add('beam.linear_dynamic_euler_bernoulli','frame_modal_independent',
    'test_modal_frequencies_converge_to_independent_beam_boundary_roots',
    'test_declared_point_mass_converges_to_independent_massless_beam_compliance_limit')
add('beam.linear_static_euler_bernoulli','simple_udl_complete_outputs_individual',
    'test_simple_span_complete_resultants_deflection_and_elastic_work')
add('buried_upheaval_buckling','buried_foundation_buckling_independent',
    'test_distributed_foundation_critical_force_converges_to_independent_wavenumber_minimum')
add('contact.prescribed_pressure_coulomb_axial','axial_contact_friction_individual',
    'test_independent_separable_axial_angular_pressure_integral','test_zero_coefficient_is_known_zero_not_missing')
add('cylindrical_local_load_attenuation','cylindrical_edge_attenuation_independent',
    'test_independent_boundary_solution_and_shell_moment_equilibrium','test_zero_moment_and_zero_edge_shear')
add('damage.circumferential_tensile_section','circumferential_loss_tension_independent',
    'test_wide_loss_converges_to_uniform_ligament_force_equilibrium','test_bounds_and_zero_width_endpoint')
add('damage.creep_rupture_fraction','creep_time_fraction_physical_qualification',
    'test_time_fraction_equals_sum_of_exposure_to_life_ratios','test_time_fraction_scales_with_exposure_and_is_not_clipped_at_one')
add('damage.supplied_profile_pressure_capacity','profile_capacity_modern_independent',
    'test_existing_public_profile_api_preserves_uniform_loss_pressure_reference')
add('dynamics.linear_sdof','piecewise_force_response_independent',
    'test_signed_force_history_matches_independent_piecewise_ode','test_zero_forcing_preserves_declared_initial_condition_free_motion')
add('elastic_thermal_compression_buckling_variant','thermal_compression_guide_buckling_independent',
    'test_guide_span_critical_temperature_converges_to_independent_euler_thermal_equilibrium')
add('elasticity.closed_pressure_cylinder','closed_cylinder_zero_exterior_independent',
    'test_closed_internal_pressure_profile_matches_boundary_problem_and_end_force')
add('elasticity.incremental_pipe_compliance','pressure_diameter_individual',
    'test_diameter_satisfies_independent_membrane_hooke_system_and_elastic_work',
    'test_pressure_unloading_and_zero_increment_preserve_signed_linear_response')
add('elasticity.supplied_pipe_area_observation','pipe_thermoelastic_area_independent',
    'test_independent_hooke_system_compatibility_signed_loads','test_first_order_area_has_only_quadratic_geometric_difference')
add('engineering.target.constant_stress_viscoelastic_strain','viscoelastic_strain_independent',
    'test_strain_matches_independently_integrated_element_odes','test_zero_time_short_time_and_long_time_limits')
add('engineering.target.covered_pipe_flotation_balance','covered_pipe_uplift_independent',
    'test_vertical_margin_matches_mass_and_displaced_solid_volume_ledger','test_zero_cover_does_not_require_backfill_properties')
add('engineering.target.local_wind_pressure_force','wind_screening_independent_local_20261101',
    'test_wind_pressure_matches_kinetic_energy_per_volume_and_area_load','test_zero_load_limits')
add('engineering.target.nut_factor_torque_preload','bolt_preload_physical_qualification',
    'test_torque_preload_and_nominal_stress_match_force_balance')
add('engineering.target.seabed_static_margin','seabed_balance_independent',
    'test_independent_vertical_and_horizontal_force_ledgers','test_contact_loss_withholds_coulomb_capacity_and_preserves_signed_normal')
add('engineering.target.soil_prism_overburden','soil_prism_load_independent',
    'test_partly_wet_prism_matches_independent_layer_weight_ledger','test_zero_cover_has_zero_weight_and_optional_width_remains_unknown')
add('engineering.target.stationary_section_hydrodynamic_load','cylinder_hydrodynamics_individual_local',
    'test_section_load_equals_independent_whole_segment_pressure_area_normalization','test_zero_added_mass_retains_displaced_fluid_acceleration_load')
add('engineering.target.strouhal_frequency_screen','wind_screening_independent_local_20261101',
    'test_shedding_frequencies_recover_dimensionless_strouhal_and_harmonic','test_still_flow_has_no_shedding_frequency')
add('engineering.target.supplied_gasket_required_preload','gasket_bolt_demand_independent',
    'test_three_load_components_match_separate_surface_and_strip_integrals','test_governing_case_is_explicit_including_zero_and_equal_limits')
add('fabrication.supplied_neutral_axis_curvature','mechanics_223_gap_implementations',
    'test_neutral_arc_ratio_and_face_radius')
add('fatigue.supplied_stress_life','stress_life_coordinate_independent',
    'test_effective_component_amplitude_matches_supplied_coefficient_form')
add('fatigue_mean_stress_correction','mean_stress_coordinate_independent',
    'test_equivalent_coordinate_intersects_independent_failure_envelope','test_zero_amplitude_is_retained_without_inventing_a_life')
add('flange_external_load_bolt_stress','elastic_bolt_group_independent',
    'test_irregular_heterogeneous_joint_virtual_work_and_equilibrium','test_contact_separation_is_not_continued_as_linear_safe_state')
add('fracture.elastic_energy_release','elastic_fracture_energy_independent',
    'test_j_contour_integral_is_radius_independent_and_matches_energy_coordinate','test_zero_intensity_zero_energy')
add('fracture.growth_threshold_critical_modifiers','crack_growth_modifiers_independent',
    'test_rate_matches_separate_log_coordinate_reference','test_critical_boundary_never_returns_divergent_growth')
add('fracture.local_energy_arrest','local_crack_arrest_energy_independent',
    'test_independent_loading_work_times_release_length','test_equality_is_not_a_strictly_lower_driving_energy')
add('fracture.mode_i_coordinate','through_crack_intensity_independent',
    'test_weight_function_integral_and_elastic_crack_closure_energy','test_half_length_never_surface_penetration_gate_and_no_variable_factor_critical_claim')
add('fracture.paris_growth','paris_region_rate_individual',
    'test_scalar_rate_against_independent_seventy_five_digit_arithmetic','test_overflow_and_underflow_are_not_false_finite_or_zero_physics')
add('fragmentation.supplied_empirical_capacity','plastic_impulse_budget_independent',
    'test_strain_work_equals_kinetic_energy_from_pressure_impulse')
add('ground_movement_pipe_response','soil_pipe_buoyancy_spring_closed_form_hangang',
    'test_buoyancy_adjusted_uniform_load_and_tip_spring_match_closed_form')
add('impact.elastic_energy_bound','elastic_drop_energy_bound_independent',
    'test_peak_matches_independent_initial_final_energy_root','test_zero_gap_unpreloaded_sudden_weight_has_two_times_static_reaction')
add('local_pipe_indentation','local_indentation_independent',
    'test_force_inverse_against_integrated_plastic_contact_traction_and_displacement_work','test_stationary_zero_limit_has_no_inferred_residual_dent')
add('mechanical.prescribed_pressure_work_actuation','ideal_actuation_observers_independent',
    'test_piston_surface_integral_and_pressure_volume_work','test_motor_rotational_work_integral_and_flow_energy')
add('mechanical.supplied_uniform_shear_cut_load','ideal_actuation_observers_independent',
    'test_uniform_shear_area_integral')
add('mechanics.key_shear_bearing','key_loads_individual',
    'test_contact_integrals_reconstruct_force_and_moment')
add('mechanics.relief_jet_reaction','port_momentum_load_individual',
    'test_complete_elbow_force_from_independent_parcel_impulse','test_opposed_outlets_cancel_only_their_known_contributions')
add('pendulum_swing_response','pendulum_spectral_coordinate_independent',
    'test_frequency_is_small_angle_limit_of_gravity_energy_period','test_zero_spectrum_preserves_natural_frequency')
add('pipeline.buried_aboveground_transition','soil_transition_individual',
    'test_independent_axial_equilibrium_and_compatibility_event_ode','test_zero_load_has_zero_length_movement_and_no_fictitious_soil_force')
add('pipeline.lowering_elastic_compatibility','lowering_axial_compatibility_individual',
    'test_independent_axial_bar_shooting_equilibrium_work_and_compatibility','test_large_extension_outside_supplied_linear_support_is_withheld')
add('plasticity.hardening_membrane_pressure_turn','hardening_membrane_limit_independent',
    'test_pressure_turning_point_matches_independent_curve_optimizer','test_unsupported_strain_is_not_extrapolated_to_a_pressure_limit')
add('plasticity.perfect_plastic_cylinder','perfect_plastic_cylinder_independent',
    'test_independent_radial_equilibrium_initial_value_solution_and_boundaries','test_decreasing_wall_ratios_converge_to_closed_end_thin_j2_limit')
add('plate.clamped_circular_elastic_pressure','circular_plate_individual',
    'test_independent_virtual_work_and_surface_curvature','test_zero_pressure_inverse_is_only_algebraic_zero')
add('pressure.nominal_membrane','nominal_pipe_pressure_independent_local',
    'test_authored_nominal_reference_points')
add('rigging.prescribed_guy_equilibrium','guy_projection_individual',
    'test_actual_vector_projection_matches_independent_two_equation_force_system','test_vertical_guy_singular_balance_is_not_zero_tension')
add('rigging.pulley_mechanical_advantage','reeved_hoist_individual',
    'test_independent_leg_recursion_and_virtual_work','test_zero_load_and_missing_displacement_are_distinct')
add('shaft.material_capacity','shaft_torsion_individual',
    'test_independent_twist_field_section_traction_and_energy_integrals','test_speed_sign_and_zero_speed_do_not_change_torque_capacity')
add('shell.elastic_ring_pressure_instability','elastic_ring_critical_pressure_independent_local',
    'test_actual_instability_event_matches_independent_extensible_modal_reference','test_thickness_refinement_approaches_classical_long_cylinder_limit')
add('shell.linear_radial_pressure_dynamics','cylindrical_modal_projection_independent',
    'test_ring_energy_projection_matches_independent_membrane_and_inextensional_fields','test_thick_wall_not_silently_extended')
add('shell.local_normal_membrane_equilibrium','principal_membrane_equilibrium_independent',
    'test_infinitesimal_anisotropic_surface_edge_traction_convergence','test_zero_coefficient_no_arbitrary_unique_solution')
add('shell.short_stiff_pipe_supplied_membrane_yield_reference','shell_pressure_references_independent',
    'test_short_pipe_reference_closes_nominal_half_section_hoop_force')
add('shell.supplied_intermediate_collapse_correlation','shell_pressure_references_independent',
    'test_intermediate_correlation_matches_separate_customary_unit_coordinate_expression')
add('shell.supplied_oval_pressure_interaction','shell_pressure_references_independent',
    'test_oval_lower_root_matches_an_independent_bracketed_polynomial_root','test_zero_ovality_selects_the_smaller_reference_including_the_double_root')
add('sleeve_end_fillet_stress','sleeve_fillet_nominal_stress_independent',
    'test_unit_factors_match_independent_uniform_shear_invariants_and_circumferential_force','test_nominal_area_approaches_exact_finite_annulus_in_thin_limit')
add('smooth_specimen_strain_life','strain_life_coordinate_independent',
    'test_quadratic_inverse_and_coordinate_consistency','test_no_outside_calibration_extrapolation')
add('soil.surface_load_and_pipe_contact','surface_point_load_transfer_independent',
    'test_independent_boussinesq_displacement_constitutive_stress','test_force_length_similarity_and_zero_load')
add('support_force_movement','harmonic_response_independent_local',
    'test_initial_value_matrix_response_matches_independent_adaptive_ivp','test_undamped_resonance_transient_has_secular_growth_but_no_periodic_solution')
add('thermoelasticity.radial_cylinder','cylindrical_thermal_stress_independent',
    'test_independent_stress_primary_bvp_matches_temperature_moment_solution','test_uniform_free_expansion_has_no_stress')
add('thermoelasticity.restrained_cylinder','fully_restrained_cylinder_independent',
    'test_displacement_boundary_solution_axial_compatibility_and_end_force','test_exact_annulus_pressure_component_converges_to_od_membrane_source_coordinate')
add('torsion.annular_elastic_section','shaft_torsion_individual',
    'test_independent_twist_field_section_traction_and_energy_integrals','test_speed_sign_and_zero_speed_do_not_change_torque_capacity')
add('uniaxial_constitutive_coordinate','normalized_constitutive_coordinate_independent',
    'test_native_coordinate_recovers_independently_inverted_normalized_curve','test_zero_stress_and_consistent_stress_unit_scaling')
add('waves.cylindrical_scalar_wave','cylindrical_scalar_wave_mode_independent',
    'test_independent_annular_finite_volume_eigenvalue_convergence')
add('weld_group_section_stress','weld_line_group_independent',
    'test_independent_line_integral_recovers_all_six_resultants')
add('welding.supplied_burnthrough_limit','mechanics_223_gap_implementations',
    'test_weld_wall_adiabatic_storage_matches_applied_heat','test_weld_wall_uniform_thermal_equilibrium')
add('yield.pressurized_isotropic_cylinder','elastic_first_yield_independent',
    'test_first_yield_matches_radial_boundary_problem_and_occurs_at_bore')

for unit,file,function,overrides in [
 ('beam.linear_static_euler_bernoulli','simple_udl_complete_outputs_individual','test_simple_span_complete_resultants_deflection_and_elastic_work',{'load':0.}),
 ('buried_upheaval_buckling','buckling','test_tension_is_not_flipped_into_positive_compression_buckling',{}),
 ('elastic_thermal_compression_buckling_variant','buckling','test_tension_is_not_flipped_into_positive_compression_buckling',{}),
 ('ground_movement_pipe_response','soil_pipe_buoyancy_spring_closed_form_hangang','test_buoyancy_adjusted_uniform_load_and_tip_spring_match_closed_form',
  {'pipe_mass':0.,'contents_mass':0.,'displaced_area':0.,'immersed_fraction':0.}),
 ('mechanics.key_shear_bearing','key_loads_individual','test_contact_integrals_reconstruct_force_and_moment',{'torque':0.}),
 ('pressure.nominal_membrane','nominal_pipe_pressure_independent_local','test_inverse_reference_points_and_zero_pressure',{'pressure':0.,'stress':100e6,'expected':0.}),
 ('yield.pressurized_isotropic_cylinder','elastic_first_yield_independent','test_full_geometry_solution_converges_to_thin_wall_first_yield_limit',{}),
 ('weld_group_section_stress','weld_line_group_independent','test_independent_line_integral_recovers_all_six_resultants',{'force':(0.,0.,0.),'moment':(0.,0.,0.)}),
]:CASES.append((unit,'extreme','test_'+file+'.py',function,overrides))

CASES=[(u,k,f,fn,({'dp':0.,'speed':0.} if u=='mechanical.prescribed_pressure_work_actuation' and k=='extreme' else o))
       for u,k,f,fn,o in CASES]


def namespace(file):
    if file not in MODULES:MODULES[file]=runpy.run_path(str(ROOT/'tests'/file))
    return MODULES[file]


def arguments(fn,ns,extreme=False):
    kwargs={}
    for mark in getattr(fn,'pytestmark',[]):
        if mark.name!='parametrize':continue
        names=mark.args[0]
        names=[n.strip() for n in names.split(',')] if isinstance(names,str) else list(names)
        row=mark.args[1][-1 if extreme else 0]
        if hasattr(row,'values') and not callable(row.values):row=row.values
        values=(row,) if len(names)==1 else tuple(row)
        kwargs.update(zip(names,values))
    for name,p in inspect.signature(fn).parameters.items():
        if name in kwargs or p.default is not inspect.Parameter.empty:continue
        fixture=ns.get(name)
        if fixture is None:raise AssertionError('unresolved reference fixture '+name)
        factory=getattr(fixture,'__wrapped__',fixture)
        kwargs[name]=factory(**arguments(factory,ns,extreme))
    return kwargs


@pytest.mark.parametrize('unit,kind,file,function,overrides',CASES,ids=[u+'-'+k for u,k,*_ in CASES])
def test_selected_independent_reference_and_extreme(unit,kind,file,function,overrides,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind',kind)
    record_property('reference_test',file+'::'+function)
    ns=namespace(file)
    assert function in ns,'reference function must exist in the pinned source'
    fn=ns[function]
    fn(**(arguments(fn,ns,kind=='extreme')|overrides))


INPUT_CASES=json.loads((ROOT/'tests/data/mechanics_input_baselines_223.json').read_text(encoding='utf-8'))['cases']


def changed(q,**updates):
    return type(q).model_validate_json(json.dumps(q.model_dump(mode='json')|updates))


def reviewed(q):
    return changed(q,**{key:True for key in type(q).model_fields if key.endswith('reviewed')})


def authored(unit):
    case=next(c for c in INPUT_CASES if c['canonical_unit']==unit)
    module,name=case['contract'].rsplit('.',1)
    cls=getattr(importlib.import_module(module),name)
    return reviewed(cls.model_validate_json(case['payload_json']))


def new_request(name):
    return reviewed(namespace('test_input_mechanics_223.py')['baseline'](name))


def finite_result(result):
    assert result['status'] in ('computed','out_of_scope'),result
    if result['status']=='out_of_scope':return
    def walk(x):
        if isinstance(x,dict):
            for item in x.values():yield from walk(item)
        elif isinstance(x,(tuple,list)):
            for item in x:yield from walk(item)
        elif type(x) in (float,int):yield x
    assert all(math.isfinite(x) for x in walk(result)),result


CUSTOM_FORMULA=[
 'branch_intersection_limit_pressure','buckle_propagation_arrester','damage.fatigue_cycle_fraction',
 'damage.prescribed_constant_loss_time','dent_rebound_fatigue','fracture.surface_flaw_proof_survival',
 'impact_to_fracture_toughness_correlation','notch_threshold_correlation','saddle_local_capacity',
 'strain_rate_strength_correlation','vortex_lock_in_response',
]
CUSTOM_EXTREME=[
 'branch_intersection_limit_pressure','buckle_propagation_arrester','damage.fatigue_cycle_fraction',
 'damage.prescribed_constant_loss_time','damage.supplied_profile_pressure_capacity','dent_rebound_fatigue',
 'elasticity.closed_pressure_cylinder','engineering.target.nut_factor_torque_preload',
 'fabrication.supplied_neutral_axis_curvature','fatigue.supplied_stress_life','fracture.surface_flaw_proof_survival',
 'fragmentation.supplied_empirical_capacity','impact_to_fracture_toughness_correlation',
 'mechanical.supplied_uniform_shear_cut_load','notch_threshold_correlation','saddle_local_capacity',
 'shell.short_stiff_pipe_supplied_membrane_yield_reference','shell.supplied_intermediate_collapse_correlation',
 'strain_rate_strength_correlation','vortex_lock_in_response','waves.cylindrical_scalar_wave',
]
CUSTOM=[(u,'formula') for u in CUSTOM_FORMULA]+[(u,'extreme') for u in CUSTOM_EXTREME]


@pytest.mark.parametrize('unit,kind',CUSTOM,ids=[u+'-'+k for u,k in CUSTOM])
def test_authored_missing_reference_and_extreme(unit,kind,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind',kind)
    if unit=='branch_intersection_limit_pressure':
        from pipeline_integrity.branch_pressure_coordinate import evaluate_branch_pressure_coordinate as evaluate
        q=changed(authored(unit),supplied_numerator_coefficients=(0.,0.,0.,1.),
                  supplied_denominator_coefficients=(0.,0.,0.,1.))
        if kind=='formula':
            r=evaluate(q)
            force=q.supplied_header_yield_stress_pa*2*q.header_wall_thickness_m
            assert r['status']=='computed'
            assert r['limit_pressure_coordinate_pa']*q.header_outside_diameter_m==pytest.approx(force,rel=2e-14)
        else:
            r=evaluate(changed(q,supplied_burst_multiplier=1e308));finite_result(r)
            assert r['burst_status']=='out_of_scope' and 'burst_pressure_coordinate_pa' not in r
    elif unit=='buckle_propagation_arrester':
        from scipy.integrate import solve_ivp
        from pipeline_integrity.buckle_arrester_coordinate import evaluate_buckle_arrester_coordinate as evaluate
        q=authored(unit);r=evaluate(q);assert r['status']=='computed'
        if kind=='formula':
            p0=r['pipe_propagation_pressure_coordinate_pa'];pinf=r['long_arrester_propagation_pressure_coordinate_pa']
            k=q.supplied_length_transfer_coefficient*q.arrester_wall_thickness_m/q.arrester_outside_diameter_m**2
            reference=solve_ivp(lambda x,y:[k*(pinf-y[0])],(0,q.arrester_length_m),[p0],rtol=1e-11,atol=1e-6)
            assert reference.success
            assert r['finite_length_arrest_pressure_coordinate_pa']==pytest.approx(reference.y[0,-1],rel=2e-10)
        else:
            limit=evaluate(changed(q,arrester_length_m=1e308))
            assert limit['finite_length_arrest_pressure_coordinate_pa']==r['long_arrester_propagation_pressure_coordinate_pa']
    elif unit=='damage.fatigue_cycle_fraction':
        from matintegrity.models.fatigue import MinerDamage,miner_damage
        counts,lives=([10.,20.],[100.,100.]) if kind=='formula' else ([0.,1e10],[100.,1.])
        r=miner_damage(MinerDamage(cycle_counts=counts,cycles_to_failure=lives,declared_damage_limit=1.))
        assert r.values['damage']==pytest.approx(.3 if kind=='formula' else 1e10,rel=2e-14)
        assert r.values['damage_margin']==pytest.approx(.7 if kind=='formula' else 1.-1e10)
    elif unit=='damage.prescribed_constant_loss_time':
        from pipeline_integrity.constant_loss_life import evaluate_constant_loss_life as evaluate
        q=new_request('loss')
        if kind=='formula':
            q=changed(q,current_remaining_wall_m=.01,supplied_critical_remaining_wall_m=.004,supplied_penetration_rate_m_s=2e-10)
            r=evaluate(q);assert r['status']=='computed'
            assert r['time_to_boundary_s']==pytest.approx(30e6,rel=2e-14)
        else:
            assert evaluate(changed(q,supplied_penetration_rate_m_s=0.))['time_to_boundary_s'] is None
            assert evaluate(changed(q,supplied_critical_remaining_wall_m=.02))['time_to_boundary_s']==0.
    elif unit=='dent_rebound_fatigue':
        from scipy.optimize import brentq
        from pipeline_integrity.dent_rebound_coordinate import evaluate_dent_rebound_coordinate as evaluate
        q=authored(unit)
        if kind=='formula':
            r=evaluate(q);assert r['status']=='computed'
            reference=brentq(lambda original: original*q.supplied_rebound_log_coefficient*math.log(q.supplied_flow_stress_pa/q.operating_hoop_stress_pa)-q.observed_pressurized_dent_depth_m,
                             q.observed_pressurized_dent_depth_m,1.,xtol=1e-14)
            assert r['original_dent_depth_coordinate_m']==pytest.approx(reference,rel=2e-12)
        else:
            r=evaluate(changed(q,observed_pressurized_dent_depth_m=0.));assert r['status']=='computed'
            assert r['original_dent_depth_coordinate_m']==0.
    elif unit=='fracture.surface_flaw_proof_survival':
        from pipeline_integrity.surface_flaw_proof import evaluate_surface_flaw_proof
        from pipeline_integrity.time_dependent_tearing import evaluate_pressure_history_tearing
        proof=evaluate_surface_flaw_proof(new_request('proof'))
        assert proof['proof_limited_initial_depth_m']==pytest.approx(.0022,abs=1e-15)
        assert proof['operating_failure_depth_m']==pytest.approx(.0064,abs=1e-15)
        q=changed(new_request('tearing'),time_s=(0.,1.,2.,3.,4.),
            differential_pressure_pa=(10e6,2e6,2e6,10e6,10e6),
            supplied_driving_j_per_m2=((200.,200.,200.),)*3,supplied_resistance_j_per_m2=(100.,)*3,
            supplied_projected_crack_area_m2=(.00001,.00004,.00009),supplied_rate_coefficient_m_s=1e-4,
            closing_peak_pressure_fraction=.4,reopening_peak_pressure_fraction=.8)
        if kind=='formula':
            r=evaluate_pressure_history_tearing(q);assert r['status']=='completed_history'
            assert [x['time_s'] for x in r['closure_switches']]==pytest.approx([.75,2.75],abs=1e-12)
            assert r['final_depth_m']==pytest.approx(.0022,abs=2e-11)
            assert r['final_state']['new_projected_crack_area_m2']==pytest.approx(2e-6,abs=2e-13)
            assert abs(r['driving_resistance_overstress_work_residual_j'])<1e-12
        else:
            r=evaluate_pressure_history_tearing(changed(q,initial_depth_m=q.rupture_depth_m))
            assert r['status']=='rupture_depth_reached' and r['completed_time_s']==0.
            assert r['final_state']['supplied_driving_work_j']==0.
    elif unit=='impact_to_fracture_toughness_correlation':
        from pipeline_integrity.fracture_property_coordinates import evaluate_normalized_impact_toughness_coordinate as evaluate
        q=authored(unit)
        if kind=='formula':
            q=changed(q,impact_energy_over_yield_strength_m3=5e-7,
                supplied_energy_strength_coefficient_per_m2=1e6,supplied_squared_toughness_over_strength_offset_m=.25)
            r=evaluate(q);assert r['status']=='computed'
            assert r['estimated_toughness_over_strength_sqrt_m']==pytest.approx(.5,rel=2e-14)
        else:
            r=evaluate(changed(q,impact_energy_over_yield_strength_m3=0.))
            assert r['status']=='out_of_scope' and 'estimated_toughness_over_strength_sqrt_m' not in r
    elif unit=='notch_threshold_correlation':
        from pipeline_integrity.fracture_property_coordinates import evaluate_notch_threshold_coordinate as evaluate
        q=authored(unit)
        if kind=='formula':
            q=changed(q,notch_root_radius_m=4*q.reference_notch_radius_m,yield_stress_pa=9*q.reference_yield_stress_pa,
                      supplied_radius_strength_exponent=.5)
            r=evaluate(q);assert r['status']=='computed'
            assert r['threshold_intensity_range_coordinate_pa_sqrt_m']==pytest.approx(6*q.supplied_reference_threshold_pa_sqrt_m)
        else:
            r=evaluate(changed(q,notch_root_radius_m=1e308,reference_notch_radius_m=1e-308));finite_result(r)
            assert r['status']=='out_of_scope' and 'threshold_intensity_range_coordinate_pa_sqrt_m' not in r
    elif unit=='saddle_local_capacity':
        from scipy.integrate import quad
        from pipeline_integrity.saddle_capacity_coordinate import evaluate_saddle_capacity as evaluate
        q=authored(unit)
        if kind=='formula':
            r=evaluate(q);assert r['status']=='computed'
            integral=quad(lambda radius:1/radius,q.wall_thickness_m,q.pipe_reference_radius_m,epsabs=1e-11)[0]
            reference=2*q.supplied_yield_stress_pa*q.wall_thickness_m**2/(q.supplied_contact_geometry_coefficient*integral)
            assert r['nominal_saddle_reaction_capacity_coordinate_n']==pytest.approx(reference,rel=2e-12)
        else:
            r=evaluate(changed(q,wall_thickness_m=1e-200));finite_result(r)
            assert r['status']=='out_of_scope' and 'nominal_saddle_reaction_capacity_coordinate_n' not in r
    elif unit=='strain_rate_strength_correlation':
        from scipy.integrate import quad
        from pipeline_integrity.reviewed_dynamic_correlations import evaluate_dynamic_strength_coordinate as evaluate
        q=authored(unit)
        if kind=='formula':
            q=changed(q,strain_rate_magnitude_per_s=1.,maximum_reviewed_rate_per_s=100.)
            r=evaluate(q);assert r['status']=='computed'
            logratio=quad(lambda rate:1/rate,q.reference_strain_rate_per_s,q.strain_rate_magnitude_per_s)[0]/math.log(10.)
            assert r['dynamic_to_reference_strength_ratio']==pytest.approx(q.supplied_ratio_intercept+q.supplied_ratio_multiplier*logratio,rel=2e-12)
        else:
            r=evaluate(changed(q,law='power_rate_ratio',strain_rate_magnitude_per_s=0.,minimum_reviewed_rate_per_s=0.,supplied_power_exponent=.5))
            assert r['status']=='computed' and r['dynamic_to_reference_strength_ratio']==q.supplied_ratio_intercept
    elif unit=='vortex_lock_in_response':
        import numpy as np
        from pipeline_integrity.vortex_lockin_coordinate import evaluate_vortex_lockin_coordinate as evaluate
        q=authored(unit)
        if kind=='formula':
            r=evaluate(q);assert r['status']=='computed'
            a=math.sqrt(q.supplied_amplitude_denominator_offset)
            b=2*math.pi*q.strouhal_number**2*r['mass_damping_index']
            reference=np.linalg.solve([[a,-b],[b,a]],[q.obstruction_outside_diameter_m*q.supplied_amplitude_numerator,0.])
            assert r['lockin_displacement_amplitude_coordinate_m']==pytest.approx(math.hypot(*reference),rel=2e-14)
        else:
            r=evaluate(changed(q,damping_ratio=0.));assert r['status']=='computed'
            assert r['lockin_displacement_amplitude_coordinate_m']==pytest.approx(q.obstruction_outside_diameter_m*q.supplied_amplitude_numerator/math.sqrt(q.supplied_amplitude_denominator_offset))
            overflow=evaluate(changed(q,elastic_modulus_pa=1e308,bending_second_moment_m4=1e308));finite_result(overflow)
            assert overflow['status']=='out_of_scope' and 'bending_stress_coordinate_pa' not in overflow
    elif unit=='damage.supplied_profile_pressure_capacity':
        ns=namespace('test_profile_capacity_modern_independent.py')
        r=ns['evaluate_profile']([0.,.5,1.],[.01,.01,.01],[0.,1.],[2.,2.])
        assert r['values']['capacity_reduction_factor']==1.
    elif unit=='elasticity.closed_pressure_cylinder':
        from pipeline_integrity.contracts import EvidenceRef,Pressure,RunContext
        from pipeline_integrity.material_exchange import evaluate_pressure_section
        q=authored(unit);zero=Pressure(value=1e5,unit='Pa',reference='absolute')
        q=changed(q,internal_pressure=zero.model_dump(mode='json'),external_pressure=zero.model_dump(mode='json'))
        c=RunContext(asset_id=q.asset_id,scenario_id='zero-pressure',clock_id=q.clock_id,origin='synthetic',
            evidence=tuple(EvidenceRef(id=e,citation='Authored zero traction input',origin='synthetic') for e in {q.geometry_evidence_id,q.pressure_evidence_id}))
        r=evaluate_pressure_section(q,c);assert r['status']=='computed'
        for record in r['material_records']:
            values=record['native']['payload']['values']
            assert [values[k] for k in ('radial_stress_Pa','hoop_stress_Pa','axial_stress_Pa')]==pytest.approx([-1e5]*3,abs=1e-8)
    elif unit=='engineering.target.nut_factor_torque_preload':
        r=namespace('test_bolt_preload_physical_qualification.py')['solve'](0.,.02,.2,2e-4)
        assert r['preload_N']==r['nominal_tensile_stress_Pa']==0.
    elif unit=='fabrication.supplied_neutral_axis_curvature':
        from pipeline_integrity.neutral_surface_bending import evaluate_neutral_surface_bending
        q=changed(new_request('neutral'),neutral_radius_m=1e300)
        r=evaluate_neutral_surface_bending(q);assert r['status']=='computed'
        assert r['values']['signed_inner_face_engineering_strain']<0.
        assert 0.<r['values']['signed_outer_face_engineering_strain']<1e-300
        assert r['values']['signed_inner_face_engineering_strain']*q.neutral_radius_m==pytest.approx(-q.neutral_distance_from_inner_face_m,rel=2e-14,abs=0.)
        assert r['values']['signed_outer_face_engineering_strain']*q.neutral_radius_m==pytest.approx(q.thickness_m-q.neutral_distance_from_inner_face_m,rel=2e-14,abs=0.)
    elif unit=='fatigue.supplied_stress_life':
        ns=namespace('test_stress_life_coordinate_independent.py')
        q,c=ns['make'](query_stress_coordinate_pa=1e-30)
        r=ns['evaluate'](q,c);assert r.status=='computed'
        assert r.curve_life_cycles==pytest.approx(1e195,rel=2e-12,abs=0.)
    elif unit=='fragmentation.supplied_empirical_capacity':
        ns=namespace('test_plastic_impulse_budget_independent.py');q=ns['req']()
        q=changed(q,supplied_failure_work_strain=1e-100)
        r=ns['evaluate'](q);assert r['status']=='computed'
        reference=math.sqrt(2*q.supplied_flow_stress_pa*1e-100/q.material_mass_density_kg_m3)
        assert r['impulse_budget_pa_s']/(q.material_mass_density_kg_m3*q.wall_thickness_m)==pytest.approx(reference,rel=2e-14,abs=0.)
    elif unit=='mechanical.supplied_uniform_shear_cut_load':
        from pipeline_integrity.ideal_actuation_observers import evaluate_uniform_shear_cut
        q=changed(authored(unit),uniform_sheet_thickness_m=1e-100)
        r=evaluate_uniform_shear_cut(q);assert r['status']=='computed'
        assert r['values']['nominal_uniform_shear_force_n']>0.
        assert r['values']['nominal_uniform_shear_force_n']==pytest.approx(q.actual_cut_perimeter_m*q.supplied_uniform_shear_strength_pa*1e-100,rel=2e-14,abs=0.)
    elif unit=='shell.short_stiff_pipe_supplied_membrane_yield_reference':
        from pipeline_integrity.shell_pressure_references import evaluate_short_pipe_hoop_reference
        q=changed(authored(unit),supplied_compressive_hoop_limit_pa=1e-100)
        r=evaluate_short_pipe_hoop_reference(q);assert r.status=='computed'
        assert r.external_over_internal_reference_pressure_pa>0.
        assert r.external_over_internal_reference_pressure_pa==pytest.approx(2e-100*q.wall_thickness_m/q.outside_diameter_m,rel=2e-14,abs=0.)
    elif unit=='shell.supplied_intermediate_collapse_correlation':
        from pipeline_integrity.shell_pressure_references import evaluate_intermediate_shell_reference
        q=authored(unit);baseline=evaluate_intermediate_shell_reference(q)
        r=evaluate_intermediate_shell_reference(changed(q,supplied_correlation_coefficient=q.supplied_correlation_coefficient*1e-100))
        assert r.status=='computed' and r.external_over_internal_reference_pressure_pa>0.
        assert r.external_over_internal_reference_pressure_pa==pytest.approx(baseline.external_over_internal_reference_pressure_pa*1e-100,rel=2e-14,abs=0.)
    elif unit=='waves.cylindrical_scalar_wave':
        ns=namespace('test_cylindrical_scalar_wave_mode_independent.py')
        q=changed(ns['req'](),initial_axis_velocity_m_s=0.)
        r=ns['evaluate'](q);assert r['status']=='computed'
        assert all(row['scalar_displacement_m']==row['scalar_velocity_m_s']==0. for row in r['rows'])
    else:
        raise AssertionError('missing numerical reference implementation '+unit)


def test_weld_wall_cell_field_reaches_independent_radial_steady_boundary_solution(record_property):
    from scipy.integrate import quad
    from pipeline_integrity.weld_wall_thermal_limit import evaluate_weld_wall_thermal_limit
    record_property('canonical_units',json.dumps(['welding.supplied_burnthrough_limit']))
    record_property('test_kind','formula')
    q=changed(new_request('weld'),radial_cells=8,maximum_step_s=20.,stages=[dict(duration_s=40000.,
        effective_outer_heat_flux_w_m2=1000.,gas_temperature_k=300.,gas_film_w_m2_k=100.)])
    r=evaluate_weld_wall_thermal_limit(q);assert r['status']=='computed'
    outer=q.inside_radius_m+q.wall_thickness_m
    power_per_length=2*math.pi*outer*1000.
    inner_surface=300.+power_per_length/(2*math.pi*q.inside_radius_m*100.)
    reference=[inner_surface+quad(lambda radius:power_per_length/(2*math.pi*q.conductivity_w_m_k*radius),
                                  q.inside_radius_m,point,epsabs=1e-12)[0] for point in r['radii_m']]
    assert r['final_cell_temperature_k']==pytest.approx(reference,abs=3e-7,rel=0.)
