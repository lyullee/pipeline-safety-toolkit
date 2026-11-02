"""Executed formula references and extreme numeric cases for 73 fluid methods.

Reused cases contain independently authored quadrature, ODE, SI/customary-unit
or closed-form references. They are invoked individually, not as full suites.
Empirical law arithmetic is distinct from calibration or field qualification.
"""
from __future__ import annotations
from functools import lru_cache
import importlib.util
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

TESTS=Path(__file__).resolve().parent
REUSED_REFERENCE_FILES=set()
sys.path.insert(0,str(TESTS))


@lru_cache(None)
def source(name):
    path=TESTS/name
    REUSED_REFERENCE_FILES.add(str(path))
    key='_focused_reference_'+path.stem
    spec=importlib.util.spec_from_file_location(key,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[key]=module
    spec.loader.exec_module(module)
    return module


def ref(file,name,*args,**kwargs):
    return lambda:getattr(source(file),name)(*args,**kwargs)


CASES=[]
def pair(unit,formula,extreme):
    CASES.extend(((unit,'formula',formula),(unit,'extreme',extreme)))

pair('batch.effective_longitudinal_dispersion',
    ref('test_batch_dispersion_individual.py','test_interface_equals_independent_gaussian_convolution',.7,.03),
    ref('test_batch_dispersion_individual.py','test_sharp_front_and_half_value_convention',0.,1.))
pair('batch.supplied_turbulent_dispersion_closure',
    ref('test_batch_dispersion_individual.py','test_turbulent_dispersion_independent_wall_shear_coordinate',.02),
    ref('test_batch_dispersion_individual.py','test_paired_width_average_and_identical_branch_limit'))
pair('blower.supplied_reduced_similarity',
    ref('test_blower_similarity_individual.py','test_bilinear_map_matches_independent_affine_surface_and_power_units',110.),
    ref('test_blower_similarity_individual.py','test_mach_similarity_preserves_map_point_and_cubic_power',3.))
pair('compression.real_process_and_stage_path',
    ref('test_real_compression_path_independent.py','test_independent_ideal_temperature_entropy_and_pressure_work',4.,.8),
    ref('test_real_compression_path_independent.py','test_zero_flow_keeps_specific_path_but_has_no_power'))
pair('compressor.clearance_efficiency',
    ref('test_gas_clearance_intake_individual.py','test_ideal_constant_cp_clearance_limit_and_pressure_volume_work',1.4,6.,.1),
    ref('test_gas_clearance_intake_individual.py','test_zero_clearance_requires_no_fictitious_residual_state'))
pair('compressor.fixed_geometry_speed_head',
    ref('test_compressor_speed_head_independent.py','test_independent_euler_angular_momentum_and_power_matches_head_and_speed_inverse',1.14,'gas'),
    ref('test_compressor_speed_head_independent.py','test_stage_count_sum_and_specific_energy_unit_conversion',12))
pair('compressor.ideal_compression_work',
    ref('test_compression_ideal_gas_physical_qualification.py','test_compression_matches_constant_cp_ideal_gas_closed_form',5.,.82,.2),
    ref('test_compression_ideal_gas_physical_qualification.py','test_compression_matches_constant_cp_ideal_gas_closed_form',2.,1.,0.))
pair('compressor.internal_recycle_inlet_enthalpy',
    ref('test_compressor_recycle_individual.py','test_independent_integrated_caloric_mixing',.2,1.7),
    ref('test_compressor_recycle_individual.py','test_zero_return_requires_no_return_state'))
pair('compressor.labyrinth_series_cavity_leakage',
    ref('test_serial_gas_cavities_individual.py','test_two_cavity_reference_from_independent_flux_root',1.4,.3),
    ref('test_serial_gas_cavities_individual.py','test_choked_first_tooth_plateau_and_terminal_event'))
pair('compressor.observed_external_energy_loss_balance',
    ref('test_compressor_external_energy_individual.py','test_independent_parcel_heating_and_leak_export',17.),
    ref('test_compressor_external_energy_individual.py','test_common_enthalpy_reference_shift',8e8))
pair('free_surface.circular_saint_venant',
    ref('test_circular_free_surface_independent.py','test_uniform_chezy_flow_balances_tangential_gravity_and_friction',.02),
    lambda:_uniform_extreme_sources('chezy'))
pair('free_surface.circular_uniform_friction_balance',
    ref('test_uniform_chezy_individual.py','test_uniform_gravity_and_resistance_terms_balance_with_signed_flow',.002),
    ref('test_uniform_chezy_individual.py','test_uniform_gravity_and_resistance_terms_balance_with_signed_flow',0.))
pair('gas.capacity_limited_regulator_boundary',
    ref('test_gas_regulator_boundary_independent.py','test_two_pressure_squared_resistances_independent_common_mass_equilibrium','downstream',4e5,2.3),
    ref('test_gas_regulator_boundary_independent.py','test_zero_external_flow_closes_capacity_without_inventing_a_stem_curve'))
pair('gas.conditional_legacy_blowdown_estimator',
    ref('test_restricted_gas_vent_independent.py','test_finite_pipe_pressure_endpoint_against_independent_adiabatic_inventory_limit',4e5,2.3e5),
    ref('test_restricted_gas_vent_independent.py','test_opening_event_and_explicit_finite_horizon_are_not_time_to_empty'))
pair('gas.conservative_rigid_pipe_1d',
    lambda:_uniform_extreme_sources('gas_heat'),
    ref('test_gas_acoustic_pipe_independent.py','test_negative_energy_reference_preserves_identical_thermodynamic_evolution'))
pair('gas.dilute_binary_molecular_diffusivity',
    ref('test_binary_gas_diffusion_independent.py','test_relative_maxwell_speed_quadrature',360.,160000.,.022,.041),
    ref('test_binary_gas_diffusion_independent.py','test_relative_maxwell_speed_quadrature',800.,300000.,.06,.06))
pair('gas.ideal_reversible_expansion_work',
    ref('test_gas_expansion_energy_physical_qualification.py','test_polytropic_final_volume_and_boundary_work_match_closed_form',2e5,1e5,.1,1.4),
    ref('test_gas_expansion_energy_physical_qualification.py','test_polytropic_final_volume_and_boundary_work_match_closed_form',7e5,7e5,.08,1.4))
pair('gas.isentropic_nozzle',
    ref('test_restricted_gas_vent_independent.py','test_existing_relief_shape_normalizes_to_independent_SI_nozzle_capacity',1.4,.7),
    ref('test_restricted_gas_vent_independent.py','test_existing_relief_shape_normalizes_to_independent_SI_nozzle_capacity',1.67,.01))
pair('gas.low_pressure_spitzglass',
    ref('test_low_pressure_spitzglass_individual.py','test_reference_corrected_low_formula_matches_separate_us_geometry_coordinates',5.,500.,5.),
    ref('test_low_pressure_spitzglass_individual.py','test_pressure_drop_and_length_similarities_preserve_declared_reference_state'))
pair('gas.panhandle_network_equivalence',
    ref('test_pipe_resistance_reduction_independent.py','test_series_equivalent_reconstructs_independent_potential_loss',[(3.,.1,2.),(5.,.15,2.)]),
    ref('test_pipe_resistance_reduction_independent.py','test_parallel_fraction_and_equivalent_geometry_match_independent_nonlinear_continuity_root',2.,5.,-.7))
pair('gas.prescribed_junction_mass_enthalpy_boundary',
    ref('test_prescribed_gas_junction_independent.py','test_signed_external_flow_analytic_resistor_and_caloric_balance',.2),
    ref('test_prescribed_gas_junction_independent.py','test_solver_resolution_and_branch_reversal_event',1e-12))
pair('gas.receiver_start_inventory_size',
    ref('test_gas_receiver_sizing_individual.py','test_independent_isothermal_pressure_ratio_sizing',1e6,4e5,300.,.02),
    ref('test_gas_receiver_sizing_individual.py','test_zero_demand','demand_count'))
pair('gas.squared_pressure_mean',
    ref('test_squared_pressure_profile_mean_individual.py','test_mean_matches_independent_linear_squared_pressure_profile_quadrature',2e5,1e5),
    ref('test_squared_pressure_profile_mean_individual.py','test_uniform_profile_returns_exact_endpoint_across_wide_finite_scales'))
pair('gas.steady_pipe_total_energy_path',
    ref('test_gas_potential_analytic_validation.py','test_gas_potential_path_matches_laminar_reference_and_converges_with_cell_refinement'),
    ref('test_gas_potential_turbulent_colebrook_hangang.py','test_turbulent_gas_pressure_flow_matches_independent_colebrook_and_energy',12.,.0001,60.))
pair('hydraulics.circular_pipe_friction',
    ref('test_liquid_pressure_loss_physical_qualification.py','test_catalog_laminar_pressure_work_matches_stokes_darcy_balance',900.,.1,.0001,.1,10.,0.,0.,9.80665),
    ref('test_hydraulics_liquid_head_loss_physical_qualification.py','test_laminar_darcy_head_is_linear_in_flow_and_minor_loss_is_quadratic'))
pair('hydraulics.concentric_annulus_loss',
    ref('test_concentric_annulus_independent.py','test_actual_annular_flow_and_wall_shears_match_independent_radial_poisson_bvp',.03,100.),
    ref('test_concentric_annulus_independent.py','test_thin_gap_converges_to_independent_plane_slit_pressure_and_parabola',1.))
pair('hydraulics.nonisothermal_viscous_pipe',
    ref('test_heated_incompressible_source_individual.py','test_adiabatic_pressure_work_increases_internal_energy_not_total_enthalpy',100.,4),
    ref('test_heated_variable_viscosity_individual.py','test_elevation_changes_pressure_without_spurious_gravity_heating',-20.))
pair('hydraulics.relief_system_total_resistance',
    ref('test_relief_line_capacity_individual.py','test_subcritical_capacity_against_independent_P_T_velocity_momentum_energy_ODE',1.4,.1,.5),
    ref('test_relief_line_capacity_individual.py','test_zero_distributed_friction_reuses_existing_ideal_nozzle_limit'))
pair('hydraulics.series_local_resistance',
    ref('test_liquid_path_system_independent.py','test_series_actual_diameters_and_local_velocity_basis_from_radial_force',.00013),
    ref('test_liquid_path_system_independent.py','test_series_actual_diameters_and_local_velocity_basis_from_radial_force',0.))
pair('hydraulics.series_parallel_network',
    ref('test_liquid_path_system_independent.py','test_parallel_pressure_boundary_matches_independent_laminar_column_fields',5000.),
    ref('test_liquid_path_system_independent.py','test_parallel_pressure_boundary_matches_independent_laminar_column_fields',-5000.))
pair('hydraulics.supplied_drag_reduction',
    ref('test_drag_reduction_independent.py','test_independent_distributed_friction_equilibrium_and_work',1.83,.31,'fixed_friction_head'),
    ref('test_drag_reduction_independent.py','test_independent_distributed_friction_equilibrium_and_work',2.,-.2,'fixed_hydraulic_friction_power'))
pair('hydrostatics.coated_pipe_effective_weight',
    ref('test_coated_pipe_weight_individual.py','test_integrated_surface_traction_and_material_volume',.6,.56,.08,10000.),
    ref('test_coated_pipe_weight_individual.py','test_thick_coating_uses_outer_displacement_not_thin_annulus'))
pair('hydrostatics.density_gravity_column',
    ref('test_hydrostatic_density_observer_independent.py','test_two_observations_from_independent_pressure_ode_recover_density',900.,9.80665,0.,10.),
    lambda:_observed_roundoff_reference('hydrostatic'))
pair('liquid.declared_saturated_mixed_circular_flow',
    ref('test_saturated_mixed_pipe_independent.py','test_uniform_wet_or_filled_signed_translation',.8,.4),
    ref('test_saturated_mixed_pipe_independent.py','test_filled_negative_gauge_is_distinct_from_massless_vapor_surface'))
pair('liquid.fixed_inertance_column_dynamics',
    ref('test_trapped_gas_column_individual.py','test_nonlinear_turning_volume_time_and_full_return_match_independent_work_quadrature',1.4),
    ref('test_trapped_gas_column_individual.py','test_reverse_initial_motion_reaches_upper_volume_support_at_work_quadrature_time'))
pair('liquid.homogeneous_mixture_compliance_celerity',
    ref('test_mixture_wave_compliance_individual.py','test_phase_volume_work_tangent_matches_combined_wave_compliance',.01,1.4),
    ref('test_mixture_wave_compliance_individual.py','test_zero_gas_exactly_reuses_the_existing_single_liquid_owner_without_gas_data'))
pair('liquid.linear_acoustic_characteristics',
    lambda:_observed_roundoff_reference('acoustic'),
    lambda:_observed_roundoff_reference('acoustic_extreme'))
pair('liquid.linear_acoustic_pipe_field',
    ref('test_liquid_moc_physical_qualification.py','test_closed_end_first_arrival_matches_joukowsky_head_and_distance_over_speed',1000.,.01),
    ref('test_liquid_moc_physical_qualification.py','test_closed_end_first_arrival_matches_joukowsky_head_and_distance_over_speed',500.,.02))
pair('liquid.prescribed_growing_inventory',
    ref('test_prescribed_pipe_fill_individual.py','test_front_mass_momentum_and_energy_match_independent_relaxation_ode',2.,0.),
    ref('test_prescribed_pipe_fill_individual.py','test_tiny_startup_inventory_keeps_quadratic_volume_instead_of_cancelling_to_zero'))
pair('liquid.supplied_exponential_viscosity',
    ref('test_liquid_viscosity_individual.py','test_constitutive_law_matches_independently_integrated_temperature_ode',.02),
    ref('test_liquid_viscosity_individual.py','test_constant_and_extreme_calibration_ratios_use_positive_log_coordinates'))
pair('pipe.hose_mises_friction',
    ref('test_liquid_path_system_independent.py','test_series_actual_diameters_and_local_velocity_basis_from_radial_force',-.00013),
    ref('test_liquid_path_system_independent.py','test_series_actual_diameters_and_local_velocity_basis_from_radial_force',0.))
pair('pipe.karman_radial_profile',
    ref('test_karman_radial_profile_individual.py','test_profile_and_three_area_moments_match_independent_inverse_gradient_ode',5.,.00001),
    ref('test_karman_radial_profile_individual.py','test_large_offset_arithmetic_has_the_triangular_profile_limit_not_a_fake_parabola'))
pair('pipe.prescribed_pressure_floor_route',
    ref('test_saturation_head_route_individual.py','test_piecewise_route_matches_independent_global_head_optimization',17),
    ref('test_saturation_head_route_individual.py','test_flat_transformed_obstacle_is_marginal_not_an_invented_vapor_fraction'))
pair('pipe.state_dependent_quasisteady_friction',
    ref('test_gas_state_dependent_friction_independent.py','test_laminar_exponential_signed_decay_and_internal_heating',.5),
    ref('test_gas_state_dependent_friction_independent.py','test_laminar_exponential_signed_decay_and_internal_heating',-.5))
pair('pipe.supplied_wall_friction_modifier',
    ref('test_turbulent_constitutive_individual.py','test_full_smooth_wall_relation_matches_analytic_lambert_solution',25.,.4),
    ref('test_turbulent_constitutive_individual.py','test_high_reynolds_limit_matches_independent_fully_rough_expression',.01))
pair('pump.available_npsh_budget',
    ref('test_npsh_individual_absolute_head_local.py','test_constructed_absolute_head_energy_budgets',(12.,1.,2.,3.,4.),(10.,6.)),
    ref('test_npsh_individual_absolute_head_local.py','test_same_head_budget_is_invariant_to_density_and_gravity_basis'))
pair('pump.fixed_geometry_speed_affinity',
    ref('test_pump_curve_transform_independent.py','test_transformed_quadratic_curve_matches_independent_coordinate_substitution',1.2,.9),
    ref('test_pump_curve_transform_independent.py','test_positive_transform_underflow_is_not_reported_as_a_stopped_pump'))
pair('pump.pipe_head_operating_point',
    ref('test_pump_curves.py','test_analytic_linear_curve_quadratic_system_and_power_budget'),
    ref('test_pump_curves.py','test_identical_parallel_series_scaling_and_extrapolation_rejection'))
pair('pump.pressure_or_head_duty_power',
    ref('test_pump_pressure_duty_individual.py','test_pressure_volume_power_matches_independent_us_units_and_source_rounded_factor',150.,1000.),
    ref('test_pump_pressure_duty_individual.py','test_zero_duty_preserves_zero_power','volume_flow_m3_s'))
pair('pump.suction_acceleration_head',
    ref('test_liquid_acceleration_head_independent.py','test_piston_motion_distributed_euler_pressure_force_and_work',.7),
    ref('test_liquid_acceleration_head_independent.py','test_zero_rate_and_zero_length_do_not_invent_dissipation'))
pair('pump.thermal_pipe_operating_point',
    ref('test_heated_pump_operating_individual.py','test_constant_viscosity_adiabatic_pump_root_has_exact_closed_flow_and_energy_budget'),
    ref('test_heated_pump_operating_individual.py','test_exponential_adiabatic_pump_root_converges_to_independent_logarithmic_pressure_law'))
pair('thermal.free_linear_expansion',
    ref('test_free_expansion_path_individual.py','test_nonlinear_temperature_history_ode',10.,.000012,10.,50.),
    ref('test_free_expansion_path_individual.py','test_reference_strain_integral_not_multiplicative_reanchoring',-1))
pair('thermal.gas_regulator_joule_thomson',
    ref('test_gas_pressure_reduction_individual.py','test_ideal_pressure_independent_caloric_enthalpy_has_no_temperature_change',1.7,1e8),
    ref('test_gas_pressure_reduction_individual.py','test_zero_pressure_change_has_zero_drop_without_a_cooling_floor'))
pair('thermal.ground_heat_diffusion',
    ref('test_soil_fem.py','test_skew_linear_physical_flux_and_native_reactions',.3),
    ref('test_soil_fem.py','test_total_Dirichlet_and_mixed_Robin_storage_sources',True))
pair('thermal.steady_radial_conduction',
    ref('test_radial_temperature_profile_independent.py','test_profile_and_heat_match_independent_radial_equilibrium',.03,.06,.2,1.2,500.,300.),
    ref('test_radial_temperature_profile_independent.py','test_uniform_surface_temperature_has_no_conduction_rate'))
pair('thermo.ideal_gas_state',
    ref('test_gas_state_observer_independent.py','test_ideal_forward_density_uses_mole_or_mass_basis_without_g_per_mol_confusion','mole',.035),
    ref('test_gas_state_observer_independent.py','test_closed_mass_endpoint_pressure_matches_boyle_charles_and_exact_molar_count',600.,2.))
pair('thermo.real_gas_state',
    ref('test_gas_state_observer_independent.py','test_real_density_derivatives_obey_independent_heat_capacity_identity','Methane',2e6,350.),
    ref('test_gas_state_observer_independent.py','test_real_dilute_limit_matches_independent_molar_ideal_law','Nitrogen'))
pair('valve.cavitation_margin',
    ref('test_liquid_valve_pressure_limits_independent.py','test_nominal_inception_proxy_and_separate_choke_budget',.8,.9),
    ref('test_liquid_valve_pressure_limits_independent.py','test_zero_pressure_drop_keeps_the_index_undefined_and_both_finite_limit_comparisons_false'))
pair('valve.gas_modern_terminal_ratio',
    ref('test_gas_valve_forward_capacity_independent.py','test_forward_mass_and_normal_flow_match_separate_engineering_unit_expression',1.4,.5),
    ref('test_gas_valve_forward_capacity_independent.py','test_forward_choked_mass_capacity_is_a_backpressure_plateau'))
pair('valve.steam_superheat_sizing',
    ref('test_steam_nozzle_individual.py','test_constant_cp_limit_against_analytic_nozzle_flux',1.3,.7),
    ref('test_steam_nozzle_individual.py','test_zero_drive_and_zero_requested_mass_are_explicit'))


def _named_gas(method,extreme):
    m=source('test_flat_gas_named_correlations_individual.py')
    # Independent kPa/km/mm/day convention, obtained by exact SI conversion of
    # the named Panhandle-A coefficient, rather than a second backend call.
    m.POWERS['Panhandle_A']=(1.0788,.8539,.5394,2.6182,.0045965)
    if extreme:
        m.test_declared_pressure_reference_and_efficiency_scaling(method)
    else:
        m.test_named_flat_formula_matches_separate_kpa_km_mm_day_coordinates(method,{})


for _unit,_method in (('gas.empirical_capacity_alternatives','Weymouth'),
        ('gas.empirical_panhandle_a_flat','Panhandle_A'),('gas.empirical_panhandle_b_flat','Panhandle_B'),
        ('gas.empirical_weymouth_flat','Weymouth')):
    pair(_unit,lambda method=_method:_named_gas(method,False),lambda method=_method:_named_gas(method,True))


def _thermoelastic_reference(extreme):
    from pipeline_integrity.thermoelastic_pipe_field import solve_thermoelastic_pipe_field
    factory=source('test_thermoelastic_pipe_field.py')
    if extreme:
        q=factory.request(heat_capacity_ratio=1.01,fluid_wall_conductance_W_m_K=0.,
            initial_wall_temperature_K=(350.,)*5,end_time_s=.0002)
        r=solve_thermoelastic_pipe_field(q)
        assert r.final_pressure_pa_abs==pytest.approx(q.initial_pressure_pa_abs,rel=2e-10)
        assert r.final_fluid_temperature_K==pytest.approx(q.initial_fluid_temperature_K,rel=2e-10)
        assert r.final_wall_temperature_K==pytest.approx(q.initial_wall_temperature_K,rel=2e-10)
        assert abs(r.energy_balance_residual_J)<1e-6
        return
    q=factory.request(fluid_wall_conductance_W_m_K=100.,initial_wall_temperature_K=(320.,)*5,
        end_time_s=.01,maximum_step_s=.00005)
    w=q.wall;a=w.natural_area_temperature_derivative_m2_K;c=w.area_compliance_m2_pa
    p0,tf0,tw0=200000.,300.,320.
    area0=w.reference_area_m2+a*(tw0-w.reference_temperature_K)+c*(p0-w.exterior_pressure_pa_abs)
    mass=p0*area0/(q.gas_constant_J_kg_K*tf0)
    cv=q.gas_constant_J_kg_K/(q.heat_capacity_ratio-1)
    def primitive_first_law(_,y):
        p,tf,tw=y
        area=w.reference_area_m2+a*(tw-w.reference_temperature_K)+c*(p-w.exterior_pressure_pa_abs)
        heat=q.fluid_wall_conductance_W_m_K*(tw-tf)
        # Independent uniform-cell balances: fixed mass, fluid first law with
        # p*dA work, and wall entropy heat. No production recovery/flux routine.
        matrix=np.array(((area+p*c,-p*area/tf,p*a),
                         (p*c,mass*cv,p*a),
                         (tw*a,0.,w.heat_capacity_per_length_J_m_K)))
        return np.linalg.solve(matrix,np.array((0.,heat,-heat)))
    reference=solve_ivp(primitive_first_law,(0.,q.end_time_s),(p0,tf0,tw0),method='DOP853',rtol=2e-12,atol=1e-10)
    assert reference.success
    r=solve_thermoelastic_pipe_field(q)
    p,tf,tw=reference.y[:,-1]
    assert r.final_pressure_pa_abs==pytest.approx((p,)*5,rel=2e-7)
    assert r.final_fluid_temperature_K==pytest.approx((tf,)*5,rel=2e-7)
    assert r.final_wall_temperature_K==pytest.approx((tw,)*5,rel=2e-9)
    expected_area=w.reference_area_m2+a*(tw-w.reference_temperature_K)+c*(p-w.exterior_pressure_pa_abs)
    assert r.final_area_m2==pytest.approx((expected_area,)*5,rel=2e-9)
    assert abs(r.energy_balance_residual_J)<1e-6
    assert abs(r.mass_balance_residual_kg)<1e-12


pair('framework.evolving_thermoelastic_pipe_field',lambda:_thermoelastic_reference(False),lambda:_thermoelastic_reference(True))


def _all_depth_branches(extreme):
    from pipeline_integrity.steady_circular_depth import CircularDepthRootRequest,solve_circular_normal_depth_branches
    m=source('test_steady_circular_depth_individual.py')
    coefficient=40.;angle=.002
    flow=1.02*coefficient*math.pi/4*math.sqrt(.25*math.tan(angle))
    q=m.flow(volume_flow_m3_s=flow,signed_downhill_angle_rad=angle,friction_model='chezy',chezy_coefficient_m_half_per_s=coefficient)
    lo=.5 if not extreme else .001
    r=solve_circular_normal_depth_branches(CircularDepthRootRequest(flow=q,depth_bracket_m=(lo,.9999999)))
    assert r.status=='computed'
    roots=r.values['normal_depth_roots']
    assert len(roots)==2
    def force_balance(h):
        area,perimeter,_,_=m.geometry(h)
        return area**3/perimeter-flow**2/(coefficient**2*math.tan(angle))
    expected=(brentq(force_balance,.5,.95),brentq(force_balance,.95,.9999999))
    assert [p['depth_m'] for p in roots]==pytest.approx(expected,abs=1e-9)
    if extreme:
        peak=r.values['maximum_discharge_depth_m']
        area,perimeter,_,_=m.geometry(peak)
        tangent=coefficient*area*math.sqrt(area/perimeter*math.tan(angle))
        q=q.model_copy(update={'volume_flow_m3_s':tangent})
        result=solve_circular_normal_depth_branches(CircularDepthRootRequest(flow=q,depth_bracket_m=(.001,.9999999)))
        assert result.status=='computed' and result.values['root_count']==1
        assert result.values['normal_depth_roots'][0]['depth_m']==pytest.approx(peak,abs=1e-9)
        near=q.model_copy(update={'volume_flow_m3_s':tangent*(1-1e-10)})
        separate=solve_circular_normal_depth_branches(CircularDepthRootRequest(flow=near,depth_bracket_m=(.001,.9999999)))
        assert separate.status=='computed' and separate.values['root_count']==2
        depths=[row['depth_m'] for row in separate.values['normal_depth_roots']]
        assert depths[0]<peak<depths[1]
        for depth in depths:
            area,perimeter,_,_=m.geometry(depth)
            assert coefficient*area*math.sqrt(area/perimeter*math.tan(angle))==pytest.approx(near.volume_flow_m3_s,rel=3e-12)


pair('free_surface.circular_steady_depth_observers',lambda:_all_depth_branches(False),lambda:_all_depth_branches(True))


def _hazen_reference(extreme):
    from pipeline_integrity.hydraulic_grade import HazenWilliamsRequest,evaluate_hazen_williams
    if extreme:
        for flow,length in ((0.,100.),(.001,0.),(1e-8,100.)):
            q=HazenWilliamsRequest(volume_flow_m3_s=flow,diameter_m=.15,length_m=length,
                coefficient_C=120.,coefficient_calibration_id='selected-water-law',water_and_coefficient_domain_reviewed=True)
            r=evaluate_hazen_williams(q)
            assert math.isfinite(r['head_loss_m'])
            if flow==0 or length==0:assert r['head_loss_m']==0.
            else:assert r['head_loss_m']>0.
        tiny=q.model_copy(update={'volume_flow_m3_s':1e-200,'length_m':100.})
        with pytest.raises(ValueError,match='nonzero head loss is below finite'):
            evaluate_hazen_williams(tiny)
        return
    import wntr
    from tempfile import TemporaryDirectory
    from pipeline_integrity.epanet import solve_epanet
    model=wntr.network.WaterNetworkModel()
    model.add_reservoir('up',base_head=100.)
    model.add_junction('down',base_demand=.001,elevation=0.)
    model.add_pipe('pipe','up','down',length=100.,diameter=.15,roughness=120.)
    model.options.hydraulic.headloss='H-W'
    model.options.time.duration=0
    model.options.hydraulic.accuracy=1e-9
    with TemporaryDirectory(prefix='hazen-reference-') as folder:
        result=wntr.sim.EpanetSimulator(model).run_sim(file_prefix=str(Path(folder)/'case'))
    independent=100.-float(result.node['head'].loc[0,'down'])
    q=HazenWilliamsRequest(volume_flow_m3_s=.001,diameter_m=.15,length_m=100.,coefficient_C=120.,
        coefficient_calibration_id='selected-water-law',water_and_coefficient_domain_reviewed=True)
    actual=evaluate_hazen_williams(q)['head_loss_m']
    # EPANET and this declared scalar convention round the SI prefactor and
    # diameter exponent differently; the discrepancy is bounded explicitly.
    assert actual==pytest.approx(independent,rel=.002,abs=2e-5)


pair('hydraulics.hazen_williams_water',lambda:_hazen_reference(False),lambda:_hazen_reference(True))


def _grade_reference(extreme):
    from pipeline_integrity.hydraulic_grade import HydraulicGradeRequest,evaluate_hydraulic_grade
    rho=900.;mu=.9;flow=0. if extreme else 1e-6;diameter=.1;area=math.pi*diameter**2/4
    q=HydraulicGradeRequest(stations=(dict(id='in',elevation_m=0.,flow_area_m2=area),
        dict(id='out',elevation_m=10.,flow_area_m2=area)),segments=(dict(length_m=100.,diameter_m=diameter),),
        volume_flow_m3_s=flow,inlet_pressure_pa_abs=200000.,density_kg_m3=rho,
        kinematic_viscosity_m2_s=mu/rho,constant_density_single_phase_and_loss_domains_reviewed=True)
    r=evaluate_hydraulic_grade(q)
    # Integrating the independent laminar radial Stokes solution gives Q/G.
    gradient=128*mu*flow/(math.pi*diameter**4)
    expected=q.inlet_pressure_pa_abs-rho*q.gravity_m_s2*10-gradient*100
    assert r['stations'][-1]['pressure_pa_abs']==pytest.approx(expected,abs=1e-8)
    assert abs(r['head_balance_residual_m'])<1e-12


pair('hydraulics.hydraulic_grade_profile',lambda:_grade_reference(False),lambda:_grade_reference(True))


def _wave_reference(extreme):
    from pipeline_integrity.models.hydraulics import WaveSpeed,wave_speed
    p=WaveSpeed(density_kg_m3=1000.,fluid_bulk_modulus_pa=2e9,pipe_elastic_modulus_pa=2e11,
        inner_diameter_m=.1,wall_thickness_m=.005,restraint_coefficient=0. if extreme else 1.)
    # Fluid compression volume plus thin-wall circumferential equilibrium
    # compliance determine independent stored-volume derivative dV/dp.
    volume=math.pi*p.inner_diameter_m**2/4
    pressure=1.
    hoop_stress=pressure*p.inner_diameter_m/(2*p.wall_thickness_m)
    area_strain=0. if extreme else 2*hoop_stress/p.pipe_elastic_modulus_pa
    delta_volume=volume*(pressure/p.fluid_bulk_modulus_pa+area_strain)
    expected=math.sqrt(volume/(p.density_kg_m3*delta_volume))
    r=wave_speed(p)
    assert r.status=='computed'
    assert r.values['wave_speed_m_s']==pytest.approx(expected,rel=2e-14)


pair('liquid.linear_elastic_compliance_celerity',lambda:_wave_reference(False),lambda:_wave_reference(True))
pair('pump.screw_empirical_slip_and_friction',
    ref('test_screw_pump.py','test_supplied_rectangular_clearance_matches_poiseuille_and_power'),
    ref('test_screw_pump.py','test_stopped_pump_preserves_reverse_leak_and_undefined_efficiency'))


def _cubic_reference(extreme):
    from CoolProp.CoolProp import PropsSI
    from pipeline_integrity.thermo import StateRequest,CoolPropProvider
    p,t=(100.,1500.) if extreme else (2e6,350.)
    state=CoolPropProvider(use_cache=False).resolve(StateRequest(fluid='Methane',backend='PR',inputs={'P':p,'T':t}))
    tc=PropsSI('Tcrit','Methane');pc=PropsSI('pcrit','Methane');omega=PropsSI('acentric','Methane')
    # Triple critical root conditions independently determine PR A and B.
    def critical(b):
        z=(1-b)/3;a=3*z*z+3*b*b+2*b
        return a*b-b*b-b**3-z**3
    b0=brentq(critical,.01,.2,xtol=1e-15)
    z0=(1-b0)/3;a0=3*z0*z0+3*b0*b0+2*b0
    kappa=.37464+1.54226*omega-.26992*omega*omega
    A=a0*(p/pc)*(tc/t)**2*(1+kappa*(1-math.sqrt(t/tc)))**2
    B=b0*(p/pc)*(tc/t)
    roots=np.roots((1.,-(1-B),A-3*B*B-2*B,-(A*B-B*B-B**3)))
    z=max(float(value.real) for value in roots if abs(value.imag)<1e-9 and value.real>B)
    expected=p*PropsSI('molar_mass','Methane')/(z*8.31446261815324*t)
    assert state.density_kg_m3==pytest.approx(expected,rel=2e-6)
    assert math.isfinite(state.enthalpy_j_kg)


pair('thermo.cubic_gas_eos',lambda:_cubic_reference(False),lambda:_cubic_reference(True))


def _liquid_forward(extreme):
    from pipeline_integrity.models.library_models import LiquidControlValve
    from pipeline_integrity.liquid_valve_forward_capacity import LiquidValveForwardCapacityRequest,evaluate_liquid_valve_forward_capacity
    p=LiquidControlValve(density_kg_m3=850.,vapour_pressure_pa_abs=10000.,critical_pressure_pa_abs=4e6,
        viscosity_pa_s=.0002,inlet_pressure_pa_abs=1e6,outlet_pressure_pa_abs=8e5,volume_flow_m3_s=.01,
        inlet_diameter_m=.1,outlet_diameter_m=.1,valve_diameter_m=.1,pressure_recovery_factor=.7,valve_style_factor=.8)
    q=LiquidValveForwardCapacityRequest(sizing_reference_case=p,coefficient_scale='Kv',supplied_coefficient=100.,
        volume_flow_bracket_m3_s=(.001,.2),increasing_single_liquid_sizing_branch_reviewed=True)
    if not extreme:
        r=evaluate_liquid_valve_forward_capacity(q)
        expected=100./3600*math.sqrt(((p.inlet_pressure_pa_abs-p.outlet_pressure_pa_abs)/1e5)/(p.density_kg_m3/999.10329075702327))
        assert r['volume_flow_m3_s']==pytest.approx(expected,rel=2e-10)
        assert r['mass_flow_kg_s']==pytest.approx(expected*p.density_kg_m3,rel=2e-10)
        return
    zero=evaluate_liquid_valve_forward_capacity(q.model_copy(update={'supplied_coefficient':0.}))
    assert zero['volume_flow_m3_s']==zero['mass_flow_kg_s']==0.
    source('test_liquid_valve_viscosity_independent.py').test_native_low_re_sizing_is_reached_and_brackets_independent_capacity_root(20.,1000.)
    r=[]
    for outlet in (200000.,50000.):
        state=p.model_copy(update={'outlet_pressure_pa_abs':outlet,'pressure_recovery_factor':.5})
        r.append(evaluate_liquid_valve_forward_capacity(q.model_copy(update={'sizing_reference_case':state})))
    assert r[0]['diagnostics']['choked'] and r[1]['diagnostics']['choked']
    assert r[0]['volume_flow_m3_s']==pytest.approx(r[1]['volume_flow_m3_s'],rel=1e-12)


pair('valve.liquid_capacity',lambda:_liquid_forward(False),lambda:_liquid_forward(True))
pair('valve.supplied_liquid_pressure_limits',
    ref('test_liquid_valve_pressure_limits_independent.py','test_nominal_inception_proxy_and_separate_choke_budget',.85,1.1),
    ref('test_liquid_valve_pressure_limits_independent.py','test_zero_pressure_drop_keeps_the_index_undefined_and_both_finite_limit_comparisons_false'))

assert len({u for u,k,f in CASES})==73 and len(CASES)==146


def _uniform_extreme_sources(kind):
    if kind=='chezy':
        m=source('test_circular_free_surface_independent.py')
        speed=-.7;time=.2;coefficient=5.
        radius=float(m.circular_section_from_depth(.3,1.).hydraulic_radius_m)
        expected=speed/(1+m.G*abs(speed)*time/(coefficient**2*radius))
        state=m.free_surface_area_discharge(np.full(4,.3),speed,1.)
        errors=[]
        for step in (.0005,.000125):
            result=m.solve(state,t=time,friction_model='chezy',chezy_coefficient_m_half_per_s=coefficient,maximum_step_s=step)
            errors.append(float(np.max(abs(result.velocity_m_s-expected))))
            m.assert_ledger(result)
        # Preserve the original difficult speed and original analytic accuracy
        # target; refine the explicit second-order source step rather than
        # pretending that one fixed dt guarantees the same error at all speeds.
        assert errors[1]<abs(expected)*3e-8
        assert errors[1]<errors[0]/8
    else:
        m=source('test_gas_acoustic_pipe_independent.py')
        ambient=340.;time=.4
        expected=ambient+(1-ambient)*math.exp(-4*.3/m.EOS.cv_J_kg_K*time)
        result=m.solve(m.state(np.ones(4),0.,np.ones(4)),t=time,wall_heat_transfer_W_m2_K=.3,
            ambient_temperature_K=ambient,maximum_step_s=.000125)
        assert result.temperature_K==pytest.approx(np.full(4,expected),rel=2e-9)
        m.assert_ledger(result)


def _observed_roundoff_reference(kind):
    from decimal import Decimal,localcontext
    if kind=='hydrostatic':
        m=source('test_hydrostatic_density_observer_independent.py');q=m.request()
        first=m.point(q.first.pressure_pa+1e8,q.first.upward_elevation_m,'common_gauge')
        second=m.point(q.second.pressure_pa+1e8,q.second.upward_elevation_m,'common_gauge')
        result=m.observe(m.request(first,second))
        assert result.status=='computed'
        with localcontext() as context:
            context.prec=65
            exact=(Decimal.from_float(first.pressure_pa)-Decimal.from_float(second.pressure_pa))/(
                Decimal.from_float(q.gravity_m_s2)*(Decimal.from_float(second.upward_elevation_m)-Decimal.from_float(first.upward_elevation_m)))
        assert result.density_kg_m3==pytest.approx(float(exact),rel=2e-15)
        representability=result.pressure_input_half_ulp_sum_pa/(q.gravity_m_s2*abs(second.upward_elevation_m-first.upward_elevation_m))
        assert abs(result.density_kg_m3-900.)<=representability+4*math.ulp(900.)
        assert not result.absolute_pressure_or_phase_margin_inferred
        return
    from pipeline_integrity.leak_inverse_relations import DirectionalAcousticSignalRequest,combine_directional_acoustic_signal
    if kind=='acoustic':
        fields=dict(pressure_change_pa=80.,volume_flow_change_m3_s=.2*120/(700*900),
            density_kg_m3=700.,wave_speed_m_s=900.,flow_area_m2=.2,direction=1)
    else:
        fields=dict(pressure_change_pa=0.,volume_flow_change_m3_s=1e-200,
            density_kg_m3=1e200,wave_speed_m_s=1e200,flow_area_m2=1e200,direction=-1)
    q=DirectionalAcousticSignalRequest(**fields,linear_single_phase_acoustic_impedance_reviewed=True)
    r=combine_directional_acoustic_signal(q)
    with localcontext() as context:
        context.prec=70
        D=Decimal.from_float
        expected=D(q.pressure_change_pa)+q.direction*D(q.density_kg_m3)*D(q.wave_speed_m_s)*D(q.volume_flow_change_m3_s)/D(q.flow_area_m2)
    target=float(expected)
    assert r.status=='computed'
    assert abs(r.combined_pressure_signal_pa-target)<=4*math.ulp(target)
    assert math.isfinite(r.combined_pressure_signal_pa)
    if kind=='acoustic_extreme':
        inverse=q.model_copy(update={'density_kg_m3':1e-200,'wave_speed_m_s':1e-200,
            'volume_flow_change_m3_s':1e200,'flow_area_m2':1e-200})
        restored=combine_directional_acoustic_signal(inverse)
        assert restored.combined_pressure_signal_pa==pytest.approx(-1.,rel=1e-15)
        for updates,message in (({'density_kg_m3':1e300,'wave_speed_m_s':1e300,
                                 'volume_flow_change_m3_s':1.,'flow_area_m2':1.},'exceeds finite'),
                                ({'density_kg_m3':1e-300,'wave_speed_m_s':1e-300,
                                  'volume_flow_change_m3_s':1.,'flow_area_m2':1.},'below finite')):
            with pytest.raises(ValueError,match=message):
                combine_directional_acoustic_signal(q.model_copy(update=updates))


@pytest.mark.parametrize('unit,kind,run',CASES,ids=[unit+'-'+kind for unit,kind,_ in CASES])
def test_formula_or_extreme_reference(unit,kind,run,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind',kind)
    run()
