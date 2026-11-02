"""Reviewed physical observables for the 18 delegated fluid methods."""
from fluid_workflow_capture_223 import *
OBSERVABLES={
'free_surface.circular_saint_venant':('depth_m[0]','m','local_free_surface_depth_at_final_time'),
'free_surface.circular_uniform_friction_balance':('mean_velocity_m_s','m/s','uniform_cross_section_mean_liquid_velocity'),
'hydrostatics.coated_pipe_effective_weight':('downward_submerged_weight_N_m','N/m','downward_minus_buoyant_pipe_weight_per_length'),
'hydrostatics.density_gravity_column':('density_kg_m3','kg/m**3','density_observed_from_signed_hydrostatic_column'),
'liquid.declared_saturated_mixed_circular_flow':('core.area_discharge[0][1]','m**3/s','signed_final_section_volume_flow'),
'liquid.fixed_inertance_column_dynamics':('rows[1].gas_volume_m3','m**3','gas_volume_at_declared_first_turning_time'),
'liquid.homogeneous_mixture_compliance_celerity':('wave_speed_m_s','m/s','homogeneous_frozen_mixture_wall_compliance_wave_speed'),
'liquid.linear_acoustic_characteristics':('combined_pressure_signal_pa','Pa','signed_directional_acoustic_pressure_difference'),
'liquid.linear_acoustic_pipe_field':('vapour_pressure_margin_pa','Pa','minimum_computed_absolute_pressure_minus_vapour_threshold'),
'liquid.prescribed_growing_inventory':('values.front_position_m','m','liquid_front_displacement_from_declared_origin'),
'liquid.supplied_exponential_viscosity':('kinematic_viscosity_m2_s','m**2/s','kinematic_viscosity_at_declared_temperature_and_material'),
'pipe.hose_mises_friction':('common_inlet_minus_outlet_total_pressure_pa','Pa','signed_inlet_minus_outlet_total_pressure_drop'),
'pipe.karman_radial_profile':('values.centerline_velocity_m_s','m/s','axial_centerline_velocity_of_declared_radial_profile'),
'pipe.prescribed_pressure_floor_route':('values.rows[0].pressure_margin_over_saturation_Pa','Pa','absolute_pressure_minus_supplied_saturation_floor'),
'pipe.state_dependent_quasisteady_friction':('[0]','1/s','instantaneous_positive_velocity_decay_rate_at_local_state'),
'pipe.supplied_wall_friction_modifier':('darcy_friction_factor','1','conditional_darcy_not_fanning_friction_factor'),
'free_surface.circular_steady_depth_observers':('values.normal_depth_roots[0].depth_m','m','selected_first_normal_depth_root_not_unique_branch'),
'liquid.linear_elastic_compliance_celerity':('values.wave_speed_m_s','m/s','single_liquid_elastic_wall_linear_acoustic_celerity'),
}
UNITS=sorted(OBSERVABLES)
