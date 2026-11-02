# 공학 모델 카탈로그

버전0.1.0a1의 고정 범위는223개이며,339개 변형의 구현 매핑과304개 공개 API가 있다. 이 목록은 모델 탐색용이며 모든 변형의 현장 적합성 인증이 아니다. 지원 설명·실제 Python 함수·독립 물리시험 프로필을 함께 읽는다. 독립 프로필은198개, 나머지25개는 별도 기준이 필요하다.

**입출력부터 확인하려면:** [README의 223개 모델 입출력 표](../README.md#모델별-목적입력출력-223개) · [304개 API의 필수값·기본값·중첩 계약](MODULE_IO_REFERENCE_KO.md)

[README](../README.md) · [사용 설명서](USER_GUIDE_KO.md) · [API 계약](API_KO.md) · [미완료 목록](PHYSICAL_VERIFICATION_BACKLOG_KO.md)

## 1. `acoustics.supplied_compressor_volume_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Supplied-coefficient compressor surge and acoustic-terminal volume coordinates.

**공개 API**

- [`pipeline_integrity.compressor_volume_coordinate.evaluate_compressor_surge_volume`](../src/pipeline_integrity/compressor_volume_coordinate.py#L19)

## 2. `acoustics.uniform_pressure_standing_modes`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform acoustic cavity modes using existing native axial FE stiffness/mass.
- Conservative nonlinear gas/supercritical fluid in one rigid constant-area pipe.

**공개 API**

- [`pipeline_integrity.acoustic_cavity_modes.evaluate_acoustic_cavity_modes`](../src/pipeline_integrity/acoustic_cavity_modes.py#L32)
- [`pipeline_integrity.gas_acoustic_pipe.solve_gas_acoustic_pipe`](../src/pipeline_integrity/gas_acoustic_pipe.py#L371)

**검증 영역:** Uniform quiescent linear acoustic cavity with ideal closed/open ends; no mean flow or structural coupling.

**적용한 기준:** separated_eigenfunctions, eigenfrequency, mesh_convergence, length_speed_similarity.

**재현할 시험 선택자**

```text
tests/test_acoustic_cavity_modal_boundaries_independent.py::test_all_end_pairs_eigenfrequencies_shapes_and_mesh_convergence
tests/test_acoustic_cavity_modal_boundaries_independent.py::test_sound_speed_length_similarity_without_inferred_fluid_or_structural_mass
```

## 3. `anchor_group_reaction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Pure-moment tension-row and equal-share shear demand with supplied bearing line.

**공개 API**

- [`pipeline_integrity.anchor_row_demand.evaluate_anchor_row_demand`](../src/pipeline_integrity/anchor_row_demand.py#L14)

**검증 영역:** Prescribed pure-moment anchor tension row with supplied bearing line, equal stiffness and equal-share shear; no contact law or capacity inference.

**적용한 기준:** independent_complementary_energy, force_moment_constraints, zero_moment_limit.

**재현할 시험 선택자**

```text
tests/test_anchor_row_demand_independent.py::test_minimum_complementary_energy_with_independent_force_moment_constraints
tests/test_anchor_row_demand_independent.py::test_moving_reviewed_bearing_line_changes_demand_without_choosing_contact_law
tests/test_anchor_row_demand_independent.py::test_zero_moment_preserves_shear_only_demand
```

## 4. `batch.effective_longitudinal_dispersion`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Constant-coefficient batch spreading on an unbounded axial coordinate.
- Finite-volume transport of reviewed additive-volume liquid products.

**공개 API**

- [`pipeline_integrity.batch_dispersion.evaluate_axial_dispersion`](../src/pipeline_integrity/batch_dispersion.py#L38)
- [`pipeline_integrity.batch_dispersion.evaluate_batch_interface`](../src/pipeline_integrity/batch_dispersion.py#L97)
- [`pipeline_integrity.batch_transport_solver.evaluate_conservative_batch_transport`](../src/pipeline_integrity/batch_transport_solver.py#L359)

**검증 영역:** Positive supplied longitudinal dispersion and supported finite-pipe component transport; no empirical turbulent dispersion coefficient calibration.

**적용한 기준:** independent_Gaussian_convolution, diffusion_PDE_variance, Danckwerts_Robin_reference, space_time_convergence, event_mass_budget.

**재현할 시험 선택자**

```text
tests/test_batch_dispersion_individual.py::test_interface_equals_independent_gaussian_convolution
tests/test_batch_dispersion_individual.py::test_sharp_front_and_half_value_convention
tests/test_batch_dispersion_individual.py::test_interface_threshold_inverse_and_exact_time_area_similarity
tests/test_batch_dispersion_individual.py::test_heat_kernel_solves_positive_diffusion_pde_and_spreads_variance
tests/test_batch_dispersion_individual.py::test_danckwerts_continuum_oracle_satisfies_robin_boundary_and_pde
tests/test_batch_dispersion_individual.py::test_positive_dispersion_continuum_refinement_with_different_component_densities
tests/test_batch_dispersion_individual.py::test_positive_dispersion_time_refinement_and_event_mass_budget
```

## 5. `batch.supplied_turbulent_dispersion_closure`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Constant-coefficient batch spreading on an unbounded axial coordinate.
- Finite-volume transport of reviewed additive-volume liquid products.

**공개 API**

- [`pipeline_integrity.batch_dispersion.evaluate_axial_dispersion`](../src/pipeline_integrity/batch_dispersion.py#L38)
- [`pipeline_integrity.batch_dispersion.evaluate_batch_interface`](../src/pipeline_integrity/batch_dispersion.py#L97)
- [`pipeline_integrity.batch_transport_solver.evaluate_conservative_batch_transport`](../src/pipeline_integrity/batch_transport_solver.py#L359)

## 6. `beam.linear_dynamic_euler_bernoulli`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Mass-normalized elastic frame modes from the existing native frame owner.

**공개 API**

- [`pipeline_integrity.frame_modal.evaluate_frame_modes`](../src/pipeline_integrity/frame_modal.py#L53)

**검증 영역:** Small-deflection straight Euler-Bernoulli beam modes with declared mass and ideal restraints; no damping or nonlinear dynamics qualification.

**적용한 기준:** analytic_boundary_roots, mesh_convergence, elastic_mass_similarity, point_mass_compliance_limit.

**재현할 시험 선택자**

```text
tests/test_frame_modal_independent.py::test_modal_frequencies_converge_to_independent_beam_boundary_roots
tests/test_frame_modal_independent.py::test_elastic_modulus_and_density_similarity
tests/test_frame_modal_independent.py::test_native_weight_to_mass_gravity_cancels_without_changing_modal_mass
tests/test_frame_modal_independent.py::test_declared_point_mass_converges_to_independent_massless_beam_compliance_limit
```

## 7. `beam.linear_static_euler_bernoulli`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nominal closed-cap pressure plus elastic bending tensile-budget observer.
- A supplied load-to-moment coefficient and elastic section-stress coordinate.
- Traceable SI frame requests and independent static balance around the optional PyNite solver.
- Invert a supplied bending-stress ceiling through the existing frame owner.

**공개 API**

- [`pipeline_integrity.pressure_bending_budget.evaluate_pressure_bending_budget`](../src/pipeline_integrity/pressure_bending_budget.py#L33)
- [`pipeline_integrity.restraint_bending_estimate.evaluate_restraint_bending_estimate`](../src/pipeline_integrity/restraint_bending_estimate.py#L26)
- [`pipeline_integrity.structural.solve_frame`](../src/pipeline_integrity/structural.py#L873)
- [`pipeline_integrity.structural.solve_frame_signal_snapshot`](../src/pipeline_integrity/structural.py#L1184)
- [`pipeline_integrity.udl_span_sizing.evaluate_udl_span_sizing`](../src/pipeline_integrity/udl_span_sizing.py#L42)

**검증 영역:** Fixed-fixed straight prismatic beam under uniform transverse load; other frame geometries and observers remain outside this profile.

**적용한 기준:** independent_beam_boundary_solution, force_equilibrium, end_moments.

**재현할 시험 선택자**

```text
tests/test_fixed_span_uniform_load_independent.py::test_fixed_uniform_span_matches_boundary_solution_reactions_and_end_moments
```

## 8. `blower.supplied_reduced_similarity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Caller-owned centrifugal blower maps in reduced similarity coordinates.

**공개 API**

- [`pipeline_integrity.blower_similarity.evaluate_blower_similarity`](../src/pipeline_integrity/blower_similarity.py#L77)

**검증 영역:** Supplied synthetic fixed-geometry blower maps, reduced coordinates and supported speed inverse; no measured map calibration.

**적용한 기준:** independent_affine_map, similarity_invariants, power_units, independent_speed_root.

**재현할 시험 선택자**

```text
tests/test_blower_similarity_individual.py::test_bilinear_map_matches_independent_affine_surface_and_power_units
tests/test_blower_similarity_individual.py::test_mach_similarity_preserves_map_point_and_cubic_power
tests/test_blower_similarity_individual.py::test_fixed_thermal_coordinate_density_rescaling_preserves_specific_power
tests/test_blower_similarity_individual.py::test_speed_inverse_matches_independent_quadratic_root
```

## 9. `branch_intersection_limit_pressure`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Reviewed right-angle branch limit/burst pressure correlation coordinates.

**공개 API**

- [`pipeline_integrity.branch_pressure_coordinate.evaluate_branch_pressure_coordinate`](../src/pipeline_integrity/branch_pressure_coordinate.py#L21)

## 10. `buckle_propagation_arrester`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Calibrated post-buckle propagation and finite-arrester screening coordinates.

**공개 API**

- [`pipeline_integrity.buckle_arrester_coordinate.evaluate_buckle_arrester_coordinate`](../src/pipeline_integrity/buckle_arrester_coordinate.py#L22)

## 11. `buried_upheaval_buckling`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Elastic proportional-load frame eigenbuckling using native owner K/Kg assembly.

**공개 API**

- [`pipeline_integrity.buckling.evaluate_buckling`](../src/pipeline_integrity/buckling.py#L168)

**검증 영역:** Straight elastic beam with distributed linear transverse foundation represented by tributary nodal springs and ideal end supports; no nonlinear soil uplift.

**적용한 기준:** independent_Rayleigh_wavenumber_minimum, mesh_convergence, free_DOF_equilibrium.

**재현할 시험 선택자**

```text
tests/test_buried_foundation_buckling_independent.py::test_distributed_foundation_critical_force_converges_to_independent_wavenumber_minimum
```

## 12. `compression.real_process_and_stage_path`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Measured endpoint efficiency and bounded equal-ratio stage selection.
- Reversible steady-flow ideal-gas compression along a declared polytropic path.
- Reviewed static compressor maps chained through the existing equipment owner.
- Equipment budgets with declared operating conditions and vendor inputs.
- Steady gas compression with a differential efficiency and supplied cooling.

**공개 API**

- [`pipeline_integrity.compression_inverse_observers.observe_endpoint_compression_efficiency`](../src/pipeline_integrity/compression_inverse_observers.py#L42)
- [`pipeline_integrity.compression_process.evaluate_ideal_gas_compression_process`](../src/pipeline_integrity/compression_process.py#L27)
- [`pipeline_integrity.compressor_train.evaluate_compressor_train`](../src/pipeline_integrity/compressor_train.py#L684)
- [`pipeline_integrity.models.equipment.compression`](../src/pipeline_integrity/models/equipment.py#L91)
- [`pipeline_integrity.real_compression_path.evaluate_real_compression_path`](../src/pipeline_integrity/real_compression_path.py#L52)

**검증 영역:** Steady ideal/native-EOS single-phase differential-efficiency gas compression with supplied stage pressures and cooling; no compressor map calibration.

**적용한 기준:** independent_pressure_temperature_ODE, EOS_refinement, first_law_stage_ledger, enthalpy_origin_invariance.

**재현할 시험 선택자**

```text
tests/test_real_compression_path_independent.py::test_independent_ideal_temperature_entropy_and_pressure_work
tests/test_real_compression_path_independent.py::test_enthalpy_origin_shift_preserves_work_and_cooling
tests/test_real_compression_path_independent.py::test_multistage_cooling_independent_caloric_energy_ledger
tests/test_real_compression_path_independent.py::test_native_eos_independent_temperature_pressure_ode_and_refinement
tests/test_real_compression_path_independent.py::test_zero_flow_keeps_specific_path_but_has_no_power
tests/test_real_compression_path_independent.py::test_native_cooled_two_stage_path_uses_actual_intermediate_pressure
```

## 13. `compressor.clearance_efficiency`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Clearance-limited ideal-valve intake using EOS entropy and enthalpy states.

**공개 API**

- [`pipeline_integrity.gas_clearance_cycle.evaluate_gas_clearance_intake`](../src/pipeline_integrity/gas_clearance_cycle.py#L74)

**검증 영역:** Supplied gas residual chamber cycle with isentropic reexpansion and prescribed suction state; no valve dynamics or leakage calibration.

**적용한 기준:** independent_closed_expansion_open_intake_ODE, mass_energy_entropy_budget, suction_event, zero_clearance_limit.

**재현할 시험 선택자**

```text
tests/test_gas_clearance_intake_individual.py::test_ideal_constant_cp_clearance_limit_and_pressure_volume_work
tests/test_gas_clearance_intake_individual.py::test_variable_cp_against_independent_closed_expansion_and_open_intake_ODE
tests/test_gas_clearance_intake_individual.py::test_zero_clearance_requires_no_fictitious_residual_state
tests/test_gas_clearance_intake_individual.py::test_no_suction_event_is_zero_admission_not_negative_capacity
tests/test_gas_clearance_intake_individual.py::test_suction_event_threshold_from_both_sides
tests/test_gas_clearance_intake_individual.py::test_extensive_chamber_volume_similarity
tests/test_gas_clearance_intake_individual.py::test_common_enthalpy_and_entropy_reference_invariance
tests/test_gas_clearance_intake_individual.py::test_actual_EOS_mass_energy_entropy_and_full_stroke_volume_budgets
```

## 14. `compressor.fixed_geometry_speed_head`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed fixed-geometry polytropic-head/speed similarity, in J/kg.

**공개 API**

- [`pipeline_integrity.compressor_speed_head.evaluate_compressor_speed_head`](../src/pipeline_integrity/compressor_speed_head.py#L80)

**검증 영역:** Supplied fixed-geometry polytropic head map point within declared similarity support; no actual efficiency or flow prediction.

**적용한 기준:** Euler_angular_momentum, power_torque_balance, stage_energy_sum.

**재현할 시험 선택자**

```text
tests/test_compressor_speed_head_independent.py::test_independent_euler_angular_momentum_and_power_matches_head_and_speed_inverse
tests/test_compressor_speed_head_independent.py::test_stage_count_sum_and_specific_energy_unit_conversion
```

## 15. `compressor.ideal_compression_work`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reversible steady-flow ideal-gas compression along a declared polytropic path.
- Equipment budgets with declared operating conditions and vendor inputs.

**공개 API**

- [`pipeline_integrity.compression_process.evaluate_ideal_gas_compression_process`](../src/pipeline_integrity/compression_process.py#L27)
- [`pipeline_integrity.models.equipment.compression`](../src/pipeline_integrity/models/equipment.py#L91)

**검증 영역:** Reversible steady-flow ideal-gas supplied polytropic compression path; no real compressor map or efficiency inference.

**적용한 기준:** independent_pressure_volume_work, isothermal_limit, isentropic_heat_limit, first_law.

**재현할 시험 선택자**

```text
tests/test_compression_process_independent.py::test_work_matches_independent_log_pressure_volume_integral
tests/test_compression_process_independent.py::test_isothermal_work_heat_and_near_isothermal_limit
tests/test_compression_process_independent.py::test_isentropic_exponent_has_zero_heat_and_matches_enthalpy
```

## 16. `compressor.internal_recycle_inlet_enthalpy`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Observed compressor energy boundaries and gas recycle-state reconstruction.

**공개 API**

- [`pipeline_integrity.compressor_observations.reconstruct_gas_recycle_inlet`](../src/pipeline_integrity/compressor_observations.py#L44)

**검증 영역:** Steady adiabatic supplied-flow recycle mixing and passive pressure return within a reviewed gas phase.

**적용한 기준:** independent_caloric_integral, enthalpy_energy_balance, origin_invariance, flow_extensivity.

**재현할 시험 선택자**

```text
tests/test_compressor_recycle_individual.py::test_independent_integrated_caloric_mixing
tests/test_compressor_recycle_individual.py::test_common_enthalpy_reference_invariance
tests/test_compressor_recycle_individual.py::test_extensive_flow_scaling
tests/test_compressor_recycle_individual.py::test_zero_return_requires_no_return_state
tests/test_compressor_recycle_individual.py::test_actual_nitrogen_integrated_cp_energy_budget
```

## 17. `compressor.labyrinth_series_cavity_leakage`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed-area serial gas restrictions with a shared steady mass flow.

**공개 API**

- [`pipeline_integrity.serial_gas_cavities.solve_serial_gas_cavities`](../src/pipeline_integrity/serial_gas_cavities.py#L54)

**검증 영역:** Ideal-gas rest-cavity restrictions with supplied areas/discharge coefficients and common stagnation temperature; no geometric seal coefficient prediction.

**적용한 기준:** independent_isentropic_flux_root, serial_mass_continuity, choked_plateau, low_Mach_resistance_limit.

**재현할 시험 선택자**

```text
tests/test_serial_gas_cavities_individual.py::test_two_cavity_reference_from_independent_flux_root
tests/test_serial_gas_cavities_individual.py::test_multiple_rest_cavities_and_common_mass
tests/test_serial_gas_cavities_individual.py::test_area_scaling_not_duplicate_unit_models
tests/test_serial_gas_cavities_individual.py::test_choked_first_tooth_plateau_and_terminal_event
tests/test_serial_gas_cavities_individual.py::test_low_mach_series_resistance_limit_converges
tests/test_serial_gas_cavities_individual.py::test_unconverged_iteration_budget_not_accepted
```

## 18. `compressor.observed_external_energy_loss_balance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Measured steady compressor control-volume work and external mechanical loss.

**공개 API**

- [`pipeline_integrity.compressor_external_energy.observe_compressor_external_energy`](../src/pipeline_integrity/compressor_external_energy.py#L52)

**검증 영역:** Observed steady parcel enthalpy flows, external heat and mechanical loss with common energy datum.

**적용한 기준:** parcel_energy_ledger, enthalpy_origin_invariance, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_compressor_external_energy_individual.py::test_independent_parcel_heating_and_leak_export
tests/test_compressor_external_energy_individual.py::test_common_enthalpy_reference_shift
tests/test_compressor_external_energy_individual.py::test_extensive_flow_heat_loss_scaling
tests/test_compressor_external_energy_individual.py::test_heat_into_fluid_reduces_driver_work
tests/test_compressor_external_energy_individual.py::test_negative_observed_work_preserved_as_not_compressor_power_prediction
tests/test_compressor_external_energy_individual.py::test_incomplete_or_zero_throughflow_mass_boundary
```

## 19. `conservation.observed_section_mass`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Signed mass residual for one declared control volume and observation window.
- Inventory of a fixed observed pipe section using injected state/area owners.

**공개 API**

- [`pipeline_integrity.mass_balance_section.evaluate_section_mass_balance`](../src/pipeline_integrity/mass_balance_section.py#L52)
- [`pipeline_integrity.observed_pipe_storage.balance_observed_storage_window`](../src/pipeline_integrity/observed_pipe_storage.py#L315)
- [`pipeline_integrity.observed_pipe_storage.evaluate_observed_pipe_storage`](../src/pipeline_integrity/observed_pipe_storage.py#L208)

**검증 영역:** Fixed observed control-volume inventory and directional port mass totals over declared windows; no leak inference from residual.

**적용한 기준:** independent_open_mass_ledger, signed_port_budget, inventory_clock_invariance, window_partition_additivity.

**재현할 시험 선택자**

```text
tests/test_section_mass_balance_individual_local.py::test_independent_mass_ledger_preserves_zero_loss_and_negative_residual
tests/test_section_mass_balance_individual_local.py::test_multiple_ports_and_reversing_port_use_directional_mass_totals
tests/test_section_mass_balance_individual_local.py::test_closed_section_has_zero_directional_totals_and_explicit_storage_loss
tests/test_section_mass_balance_individual_local.py::test_constant_inventory_offset_and_clock_translation_leave_residual_unchanged
tests/test_section_mass_balance_individual_local.py::test_partitioned_windows_have_additive_mass_and_time_weighted_rates
tests/test_section_mass_balance_individual_local.py::test_overflow_does_not_emit_an_infinite_residual
```

## 20. `contact.prescribed_pressure_coulomb_axial`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Coulomb axial resistance integrated over a supplied cylindrical contact field.

**공개 API**

- [`pipeline_integrity.axial_contact_friction.integrate_axial_contact_friction`](../src/pipeline_integrity/axial_contact_friction.py#L61)

**검증 영역:** Caller-supplied compressive contact pressure field and Coulomb coefficient with explicit sliding/static meaning; no contact-pressure or soil-law prediction.

**적용한 기준:** independent_pressure_surface_integral, friction_dissipation, static_capacity_interval, contact_area_similarity.

**재현할 시험 선택자**

```text
tests/test_axial_contact_friction_individual.py::test_uniform_pressure_scalar_load_does_not_cancel_with_radial_normal
tests/test_axial_contact_friction_individual.py::test_independent_separable_axial_angular_pressure_integral
tests/test_axial_contact_friction_individual.py::test_partial_open_contact_has_no_invented_tensile_pressure
tests/test_axial_contact_friction_individual.py::test_sliding_drag_opposes_motion_and_work_dissipates
tests/test_axial_contact_friction_individual.py::test_static_capacity_is_interval_not_actual_signed_force
tests/test_axial_contact_friction_individual.py::test_section_contact_area_similarity
tests/test_axial_contact_friction_individual.py::test_underresolved_pressure_field_is_not_qualified_result
```

## 21. `cylindrical_local_load_attenuation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed axisymmetric semi-infinite cylindrical edge-moment coordinate.

**공개 API**

- [`pipeline_integrity.cylindrical_edge_attenuation.evaluate_cylindrical_edge_moment`](../src/pipeline_integrity/cylindrical_edge_attenuation.py#L13)

**검증 영역:** Linear decaying cylindrical shell edge-moment solution with supplied decay support and boundary moment/shear.

**적용한 기준:** independent_fourth_order_BVP, moment_equilibrium, decay_scaling, zero_load.

**재현할 시험 선택자**

```text
tests/test_cylindrical_edge_attenuation_independent.py::test_independent_boundary_solution_and_shell_moment_equilibrium
tests/test_cylindrical_edge_attenuation_independent.py::test_distance_and_decay_scaling_preserves_dimensionless_response
tests/test_cylindrical_edge_attenuation_independent.py::test_zero_moment_and_zero_edge_shear
```

## 22. `damage.circumferential_tensile_section`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed circumferential-loss axial-tension correlation coordinate.

**공개 API**

- [`pipeline_integrity.circumferential_loss_tension.evaluate_circumferential_loss_tension`](../src/pipeline_integrity/circumferential_loss_tension.py#L13)

**검증 영역:** Uniform wide-loss thin-ligament force limit and bounded supplied correlation coordinate; no calibration for isolated real defects.

**적용한 기준:** independent_annulus_force_integral, wide_loss_thin_limit, geometry_strength_scaling.

**재현할 시험 선택자**

```text
tests/test_circumferential_loss_tension_independent.py::test_wide_loss_converges_to_uniform_ligament_force_equilibrium
tests/test_circumferential_loss_tension_independent.py::test_bounds_and_zero_width_endpoint
tests/test_circumferential_loss_tension_independent.py::test_length_and_stress_units_scale_independently
```

## 23. `damage.creep_rupture_fraction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Condition-bounded creep/ageing jobs and explicit period-risk handoff.

**공개 API**

- [`pipeline_integrity.creep_exchange.evaluate_creep`](../src/pipeline_integrity/creep_exchange.py#L94)
- [`pipeline_integrity.creep_exchange.evaluate_creep_sequence`](../src/pipeline_integrity/creep_exchange.py#L259)

**검증 영역:** Supplied rupture lives and exposure blocks under the linear time-fraction rule; no rupture-life fit or interaction calibration.

**적용한 기준:** exact_exposure_life_sum, block_partition_order, exposure_scaling.

**재현할 시험 선택자**

```text
tests/test_creep_time_fraction_physical_qualification.py::test_time_fraction_equals_sum_of_exposure_to_life_ratios
tests/test_creep_time_fraction_physical_qualification.py::test_time_fraction_is_invariant_to_block_order
tests/test_creep_time_fraction_physical_qualification.py::test_time_fraction_scales_with_exposure_and_is_not_clipped_at_one
```

## 24. `damage.fatigue_cycle_fraction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Audited material-engine jobs, pressure sections and solid-temperature signal exchange.

**공개 API**

- [`pipeline_integrity.material_exchange.evaluate_material_job`](../src/pipeline_integrity/material_exchange.py#L89)
- [`pipeline_integrity.material_exchange.evaluate_pressure_section`](../src/pipeline_integrity/material_exchange.py#L184)
- [`pipeline_integrity.material_exchange.prepare_material_job`](../src/pipeline_integrity/material_exchange.py#L53)

**검증 영역:** Supplied cycle counts and corresponding lives under linear Miner accumulation; no sequence interaction or fatigue-curve calibration.

**적용한 기준:** independent_cycle_fraction_sum, block_reordering, exposure_scaling, no_damage_clipping.

**재현할 시험 선택자**

```text
tests/test_miner_damage_physical_qualification.py::test_miner_damage_matches_independent_sum_of_cycle_fractions
tests/test_miner_damage_physical_qualification.py::test_miner_damage_is_invariant_to_reordering_of_cycle_blocks
tests/test_miner_damage_physical_qualification.py::test_miner_damage_scales_linearly_with_repeated_block_counts
tests/test_miner_damage_physical_qualification.py::test_miner_damage_above_declared_limit_is_preserved_not_clipped
```

## 25. `damage.prescribed_constant_loss_time`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Time to a supplied wall-loss boundary under a constant penetration rate.
- Audited material-engine jobs, pressure sections and solid-temperature signal exchange.

**공개 API**

- [`pipeline_integrity.constant_loss_life.evaluate_constant_loss_life`](../src/pipeline_integrity/constant_loss_life.py#L16)
- [`pipeline_integrity.material_exchange.prepare_material_job`](../src/pipeline_integrity/material_exchange.py#L53)

**검증 영역:** Supplied constant wall-loss rate and declared positive-wall threshold; no corrosion-rate forecast calibration.

**적용한 기준:** independent_loss_event_ODE, zero_rate_initial_threshold, unit_clock_conversion, similarity.

**재현할 시험 선택자**

```text
tests/test_constant_wall_loss_time_individual.py::test_existing_public_job_time_and_forecast_match_independent_event_ode
tests/test_constant_wall_loss_time_individual.py::test_zero_rate_no_finite_future_crossing_and_already_reached_threshold_are_distinct
tests/test_constant_wall_loss_time_individual.py::test_exact_threshold_time_remains_positive_wall_not_failure_probability
tests/test_constant_wall_loss_time_individual.py::test_common_length_rate_scaling_preserves_time_and_scales_wall
tests/test_constant_wall_loss_time_individual.py::test_declared_customary_length_and_year_conversion_uses_seconds_not_a_hidden_clock
```

## 26. `damage.supplied_profile_pressure_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed one-dimensional loss profiles around the existing owner area-scan model.

**공개 API**

- [`pipeline_integrity.profile_capacity.evaluate_profile_capacity`](../src/pipeline_integrity/profile_capacity.py#L137)

**검증 영역:** Declared area-scan pressure-capacity law with manufactured parabolic loss and supplied amplification curve; no empirical law calibration.

**적용한 기준:** independent_continuous_window_minimum, profile_scan_convergence, uniform_loss_limit.

**재현할 시험 선택자**

```text
tests/test_profile_capacity_modern_independent.py::test_parabolic_profile_nodal_scan_converges_to_independent_continuous_window_minimum
tests/test_profile_capacity_modern_independent.py::test_existing_public_profile_api_preserves_uniform_loss_pressure_reference
```

## 27. `dent_rebound_fatigue`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Reviewed dent rebound coordinate with existing stress-life owner linkage.

**공개 API**

- [`pipeline_integrity.dent_rebound_coordinate.evaluate_dent_rebound_coordinate`](../src/pipeline_integrity/dent_rebound_coordinate.py#L19)

## 28. `dynamics.linear_sdof`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Continuous stage extrema for reviewed held base acceleration using existing propagation.
- Exact propagation of explicitly held force stages through the existing oscillator.

**공개 API**

- [`pipeline_integrity.held_acceleration_spectrum.evaluate_held_acceleration_spectrum`](../src/pipeline_integrity/held_acceleration_spectrum.py#L62)
- [`pipeline_integrity.piecewise_force_response.evaluate_generalized_response`](../src/pipeline_integrity/piecewise_force_response.py#L55)

**검증 영역:** Linear generalized oscillator driven by prescribed held force stages with declared damping; no nonlinear/contact response.

**적용한 기준:** independent_piecewise_IVP, pulse_turning_point, impulse_convergence, tail_energy_conservation, switch_continuity.

**재현할 시험 선택자**

```text
tests/test_piecewise_force_response_independent.py::test_pressure_mapped_rectangular_pulse_matches_independent_turning_point
tests/test_piecewise_force_response_independent.py::test_signed_force_history_matches_independent_piecewise_ode
tests/test_piecewise_force_response_independent.py::test_output_sampling_partition_does_not_define_the_physical_integration_step
tests/test_piecewise_force_response_independent.py::test_fixed_impulse_short_pulses_converge_to_momentum_impulse_limit
tests/test_piecewise_force_response_independent.py::test_unforced_tail_conserves_kinetic_plus_elastic_energy
tests/test_piecewise_force_response_independent.py::test_switch_state_is_continuous_and_acceleration_uses_right_force
tests/test_piecewise_force_response_independent.py::test_zero_forcing_preserves_declared_initial_condition_free_motion
```

## 29. `elastic_thermal_compression_buckling_variant`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Elastic proportional-load frame eigenbuckling using native owner K/Kg assembly.

**공개 API**

- [`pipeline_integrity.buckling.evaluate_buckling`](../src/pipeline_integrity/buckling.py#L168)

**검증 영역:** Straight elastic guided span with declared thermal-compression preload and ideal support conditions.

**적용한 기준:** independent_Euler_thermal_equilibrium, mesh_convergence, distinct_flexural_compressive_moduli.

**재현할 시험 선택자**

```text
tests/test_thermal_compression_guide_buckling_independent.py::test_guide_span_critical_temperature_converges_to_independent_euler_thermal_equilibrium
```

## 30. `elasticity.closed_pressure_cylinder`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Audited material-engine jobs, pressure sections and solid-temperature signal exchange.

**공개 API**

- [`pipeline_integrity.material_exchange.evaluate_pressure_section`](../src/pipeline_integrity/material_exchange.py#L184)

**검증 영역:** Closed-end isotropic elastic cylinder under internal pressure with zero exterior traction.

**적용한 기준:** independent_radial_boundary_value_solution, traction_conditions, end_force.

**재현할 시험 선택자**

```text
tests/test_closed_cylinder_zero_exterior_independent.py::test_closed_internal_pressure_profile_matches_boundary_problem_and_end_force
```

## 31. `elasticity.incremental_pipe_compliance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Incremental thin-wall diameter compliance under declared axial restraint.

**공개 API**

- [`pipeline_integrity.pipe_pressure_compliance.evaluate_pressure_diameter_compliance`](../src/pipeline_integrity/pipe_pressure_compliance.py#L35)

**검증 영역:** Incremental linear thin-wall membrane diameter compliance under declared axial stress or strain restraint.

**적용한 기준:** independent_membrane_Hooke_system, work_conjugacy, signed_unloading, similarity.

**재현할 시험 선택자**

```text
tests/test_pressure_diameter_individual.py::test_diameter_satisfies_independent_membrane_hooke_system_and_elastic_work
tests/test_pressure_diameter_individual.py::test_pressure_unloading_and_zero_increment_preserve_signed_linear_response
tests/test_pressure_diameter_individual.py::test_geometric_and_material_similarity
```

## 32. `elasticity.supplied_pipe_area_observation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform thin-wall thermoelastic area observations using existing owners.

**공개 API**

- [`pipeline_integrity.pipe_thermoelastic_area.evaluate_closed_end_thermoelastic_geometry`](../src/pipeline_integrity/pipe_thermoelastic_area.py#L121)
- [`pipeline_integrity.pipe_thermoelastic_area.evaluate_thermoelastic_area`](../src/pipeline_integrity/pipe_thermoelastic_area.py#L45)

**검증 영역:** Small-strain observed pipe area with supplied thermal coefficient, pressure difference and axial restraint; no evolving dynamic wall field.

**적용한 기준:** independent_three_variable_Hooke_system, thermal_integral, area_volume_distinction, first_order_geometry.

**재현할 시험 선택자**

```text
tests/test_pipe_thermoelastic_area_independent.py::test_independent_hooke_system_compatibility_signed_loads
tests/test_pipe_thermoelastic_area_independent.py::test_piecewise_coefficient_integral_and_explicit_area_basis
tests/test_pipe_thermoelastic_area_independent.py::test_first_order_area_has_only_quadratic_geometric_difference
tests/test_pipe_thermoelastic_area_independent.py::test_mechanical_and_total_support_checked_separately
tests/test_pipe_thermoelastic_area_independent.py::test_temperature_range_pressure_review_and_fixed_axial_poisson_requirements
tests/test_pipe_thermoelastic_area_independent.py::test_closed_end_independent_hooke_area_versus_volume_observables
```

## 33. `elbow_bending_stress_limit`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Reviewed elbow moment/stress-index coordinate; no local shell solver.

**공개 API**

- [`pipeline_integrity.elbow_moment_coordinate.evaluate_elbow_moment_coordinate`](../src/pipeline_integrity/elbow_moment_coordinate.py#L19)

## 34. `elbow_pressure_stress`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- An OD nominal hoop coordinate with an explicit curved-pipe correction.

**공개 API**

- [`pipeline_integrity.elbow_pressure_stress.evaluate_elbow_pressure_stress`](../src/pipeline_integrity/elbow_pressure_stress.py#L43)

**검증 영역:** Thin curved elbow nominal membrane stress with declared outside-diameter convention; no local finite-thickness peak stress prediction.

**적용한 기준:** independent_meridional_equilibrium_ODE, circumferential_force_budget, straight_thin_torus_limits, similarity.

**재현할 시험 선택자**

```text
tests/test_elbow_pressure_stress_independent.py::test_field_solves_independent_meridional_equilibrium_ode
tests/test_elbow_pressure_stress_independent.py::test_weighted_circumferential_force_budget_and_extreme_locations
tests/test_elbow_pressure_stress_independent.py::test_side_angles_match_existing_straight_od_nominal_coordinate
tests/test_elbow_pressure_stress_independent.py::test_curvature_correction_converges_to_straight_pipe
tests/test_elbow_pressure_stress_independent.py::test_hybrid_nominal_coordinate_converges_to_consistent_thin_torus_reference
tests/test_elbow_pressure_stress_independent.py::test_pressure_and_geometric_similarity
tests/test_elbow_pressure_stress_independent.py::test_zero_pressure_produces_zero_nominal_field
```

## 35. `electrical.conductor_impedance_drop`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit DC/steady sinusoidal RMS power and supplied circuit impedance budgets.

**공개 API**

- [`pipeline_integrity.electrical_circuit.evaluate_electrical_circuit`](../src/pipeline_integrity/electrical_circuit.py#L21)

**검증 영역:** Steady balanced sinusoidal AC and DC conductor impedance with supplied resistance/reactance and explicit phase/voltage convention.

**적용한 기준:** independent_periodic_power_integral, phasor_KVL, ohmic_heat.

**재현할 시험 선택자**

```text
tests/test_electrical_circuit_individual.py::test_independent_periodic_power_and_line_heat
tests/test_electrical_circuit_individual.py::test_independent_rectangular_phasor_kvl
```

## 36. `electrical.cp_current_voltage_balance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady tapped DC feed cable and explicitly supplied rectifier energy budget.

**공개 API**

- [`pipeline_integrity.dc_tapped_cable.observe_dc_collected_current`](../src/pipeline_integrity/dc_tapped_cable.py#L56)
- [`pipeline_integrity.dc_tapped_cable.observe_dc_ohmic_drive`](../src/pipeline_integrity/dc_tapped_cable.py#L26)
- [`pipeline_integrity.dc_tapped_cable.observe_dc_tapped_cable`](../src/pipeline_integrity/dc_tapped_cable.py#L134)

**검증 영역:** Passive DC cable with supplied tap currents and resistance; no cathodic protection potential claim.

**적용한 기준:** conductance_matrix, KCL, energy_budget, distributed_flux, similarity.

**재현할 시험 선택자**

```text
tests/test_dc_tapped_cable_individual.py::test_node_potentials_from_independent_conductance_matrix_not_suffix_drop_formula
tests/test_dc_tapped_cable_individual.py::test_independent_node_delivery_wire_heat_and_rectifier_conversion_energy
tests/test_dc_tapped_cable_individual.py::test_equal_taps_recover_geometric_current_sequence_and_known_drop
tests/test_dc_tapped_cable_individual.py::test_zero_resistance_and_at_bus_tap_are_exact_limits
tests/test_dc_tapped_cable_individual.py::test_length_and_resistance_density_preserve_physical_path
tests/test_dc_tapped_cable_individual.py::test_ohmic_driver_from_independent_two_resistor_Kirchhoff_node
tests/test_dc_tapped_cable_individual.py::test_segment_pickup_matches_independent_distributed_current_flux_integral
```

## 37. `electrical.dc_ac_power_balance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady tapped DC feed cable and explicitly supplied rectifier energy budget.
- Explicit DC/steady sinusoidal RMS power and supplied circuit impedance budgets.

**공개 API**

- [`pipeline_integrity.dc_tapped_cable.observe_dc_tapped_cable`](../src/pipeline_integrity/dc_tapped_cable.py#L134)
- [`pipeline_integrity.electrical_circuit.evaluate_electrical_circuit`](../src/pipeline_integrity/electrical_circuit.py#L21)

**검증 영역:** Steady declared sinusoidal AC or direct-current power at known voltage/current and explicit phase convention.

**적용한 기준:** independent_periodic_waveform_power, DC_power, zero_load.

**재현할 시험 선택자**

```text
tests/test_electrical_circuit_individual.py::test_independent_periodic_power_and_line_heat
tests/test_electrical_circuit_individual.py::test_direct_current_observation_returns_real_power
tests/test_electrical_circuit_individual.py::test_zero_load_and_unknown_conversion_are_preserved
```

## 38. `electrical.distributed_pipeline_cp`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform passive DC pipe/coating line with explicit potential and current signs.

**공개 API**

- [`pipeline_integrity.distributed_pipe_cp.evaluate_uniform_cp_line`](../src/pipeline_integrity/distributed_pipe_cp.py#L101)

**검증 영역:** One-dimensional passive distributed resistor/shunt conductor with supplied conductance and endpoint conditions; no electrochemical protection-potential calibration.

**적용한 기준:** independent_resistor_ladder_convergence, Kirchhoff_BVP, current_heat_integrals, inverse_ODE_event, energy_charge_budget.

**재현할 시험 선택자**

```text
tests/test_distributed_pipe_cp_individual.py::test_resistor_ladder_refinement_converges_to_potential_current_and_power
tests/test_distributed_pipe_cp_individual.py::test_actual_endpoint_combinations_against_independent_Kirchhoff_BVP
tests/test_distributed_pipe_cp_individual.py::test_insulated_end_target_inverse_against_independent_reverse_state_ODE
tests/test_distributed_pipe_cp_individual.py::test_local_Kirchhoff_derivatives_and_independent_integrated_current_and_heat
tests/test_distributed_pipe_cp_individual.py::test_semi_infinite_charge_and_resistor_energy_from_independent_integrals
tests/test_distributed_pipe_cp_individual.py::test_semi_infinite_target_distance_against_independent_ODE_event_and_two_arm_sum
tests/test_distributed_pipe_cp_individual.py::test_semi_infinite_current_boundary_and_sign_reversal
tests/test_distributed_pipe_cp_individual.py::test_perfect_coating_is_exact_series_resistor_with_no_ground_current
tests/test_distributed_pipe_cp_individual.py::test_zero_leakage_pure_current_boundaries_do_not_invent_a_potential
tests/test_distributed_pipe_cp_individual.py::test_leakage_to_zero_limit_and_long_line_to_semi_infinite_limit
tests/test_distributed_pipe_cp_individual.py::test_polarity_and_coordinate_length_coefficient_similarity
tests/test_distributed_pipe_cp_individual.py::test_declared_numerical_decay_support_and_semi_infinite_no_leak_are_not_extrapolated
```

## 39. `electrical.earth_electrode_resistance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform-leakage slender line electrode averages in homogeneous ohmic soil.
- Explicit geometry assignment for a reviewed approximate ground-bed model.
- Voxel-electrode conduction in a heterogeneous finite earth domain.

**공개 API**

- [`pipeline_integrity.earth_electrode.evaluate_earth_electrodes`](../src/pipeline_integrity/earth_electrode.py#L87)
- [`pipeline_integrity.groundbed_effective_envelope.evaluate_groundbed_effective_envelope`](../src/pipeline_integrity/groundbed_effective_envelope.py#L42)
- [`pipeline_integrity.groundbed_fv.evaluate_conductive_earth`](../src/pipeline_integrity/groundbed_fv.py#L91)

**검증 영역:** Analytic electrodes in homogeneous conductive half-space; no heterogeneous three-dimensional FV qualification.

**적용한 기준:** independent_green_integrals, reciprocity, current_balance, dissipation, similarity.

**재현할 시험 선택자**

```text
tests/test_earth_electrode_independent.py::test_vertical_average_self_resistance_matches_independent_line_potential_integral
tests/test_earth_electrode_independent.py::test_horizontal_buried_self_matches_independent_separation_convolution
tests/test_earth_electrode_independent.py::test_unequal_vertical_mutual_resistance_and_reciprocity_match_independent_integral
tests/test_earth_electrode_independent.py::test_two_equal_rods_common_bus_and_imposed_current_are_different_conditions
tests/test_earth_electrode_independent.py::test_three_rod_average_bus_obeys_current_balance_and_minimum_quadratic_dissipation
tests/test_earth_electrode_independent.py::test_infinite_spacing_limit_is_parallel_single_resistance_and_surface_green_mutual
tests/test_earth_electrode_independent.py::test_length_and_soil_resistivity_similarity_and_signed_current_power
```

## 40. `electrical.half_space_point_source_potential`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Unperturbed potential of surface point currents in a uniform conducting half-space.

**공개 API**

- [`pipeline_integrity.ground_halfspace.evaluate_ground_halfspace`](../src/pipeline_integrity/ground_halfspace.py#L46)

**검증 영역:** Homogeneous conductive half-space with prescribed point-source currents and explicit returns; no finite-electrode contact resistance.

**적용한 기준:** hemisphere_charge_flux, Laplace_refinement, insulating_boundary, dipole_superposition, rigid_motion.

**재현할 시험 선택자**

```text
tests/test_ground_halfspace_individual.py::test_integrated_hemispherical_charge_flux
tests/test_ground_halfspace_individual.py::test_insulating_surface_no_normal_current
tests/test_ground_halfspace_individual.py::test_potential_gradient_and_laplace_convergence
tests/test_ground_halfspace_individual.py::test_finite_return_dipole_and_polarity_superposition
tests/test_ground_halfspace_individual.py::test_ground_coordinate_translation_and_rotation
tests/test_ground_halfspace_individual.py::test_resistivity_scales_voltage_not_prescribed_current_density
tests/test_ground_halfspace_individual.py::test_active_point_singularity_not_finite_electrode_resistance
tests/test_ground_halfspace_individual.py::test_zero_source_at_observation_is_known_zero
```

## 41. `electrical.power_factor_compensation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit DC/steady sinusoidal RMS power and supplied circuit impedance budgets.

**공개 API**

- [`pipeline_integrity.electrical_circuit.evaluate_reactive_compensation`](../src/pipeline_integrity/electrical_circuit.py#L73)

**검증 영역:** Steady sinusoidal reactive-power compensation with declared lag/lead convention and supplied target power factor.

**적용한 기준:** independent_power_triangle, reactive_power_budget, extensive_scaling, zero_load_limit.

**재현할 시험 선택자**

```text
tests/test_electrical_circuit_individual.py::test_reactive_budget_from_independent_three_four_five_triangle
tests/test_electrical_circuit_individual.py::test_reactive_compensation_is_extensive_and_zero_load_limit
```

## 42. `electrical.two_state_current_rms`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-state effective current from the cycle integral of squared current.

**공개 API**

- [`pipeline_integrity.two_state_current.observe_two_state_current`](../src/pipeline_integrity/two_state_current.py#L36)

**검증 영역:** Two prescribed RMS states, duration fractions and constant resistance; no conductor temperature model.

**적용한 기준:** independent_waveform_square_integral, Joule_heat, zero_duration_limits, time_current_scaling.

**재현할 시험 선택자**

```text
tests/test_two_state_current_individual.py::test_independent_sinusoidal_state_waveforms_and_cycle_joule_heat
tests/test_two_state_current_individual.py::test_effective_rms_is_not_mean_magnitude
tests/test_two_state_current_individual.py::test_time_and_current_scaling_have_separate_energy_meanings
tests/test_two_state_current_individual.py::test_zero_and_equal_currents_and_zero_resistance
tests/test_two_state_current_individual.py::test_no_period_or_resistance_preserves_unrequested_coordinates
```

## 43. `electrochemistry.anode_charge_lifetime`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional charge-depletion observation through the material owner's budget.
- Typed electrochemical meaning and applicability around the existing material engine.

**공개 API**

- [`pipeline_integrity.anode_lifetime.evaluate_anode_lifetime`](../src/pipeline_integrity/anode_lifetime.py#L23)
- [`pipeline_integrity.electrochemical_exchange.evaluate_electrochemical`](../src/pipeline_integrity/electrochemical_exchange.py#L196)

**검증 영역:** Prescribed current-density and exposed-fraction histories with supplied usable anode charge; no protective-potential or corrosion-rate prediction.

**적용한 기준:** independent_current_quadrature, first_capacity_crossing_root, charge_capacity_ledger, no_extrapolation.

**재현할 시험 선택자**

```text
tests/test_anode_depletion_independent.py::test_charge_ledger_and_first_depletion_match_independent_current_integral
```

## 44. `emissions.calibrated_overfill_cascade_source`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional tank and cascade sources with explicit mass, state and heat bases.

**공개 API**

- [`pipeline_integrity.calibrated_emissions.evaluate_cascade_source`](../src/pipeline_integrity/calibrated_emissions.py#L151)

**검증 영역:** Prescribed pure-component ideal carrier saturation capacity, heat and available liquid supply with declared aerosol fraction; no splash/transfer-efficiency calibration.

**적용한 기준:** independent_carrier_moles, latent_heat_limit, species_feed_limit, phase_mass_budget.

**재현할 시험 선택자**

```text
tests/test_emissions_inventory_independent.py::test_cascade_independent_carrier_moles_heat_and_finite_feed_limits
```

## 45. `emissions.filling_vapor_rate`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional tank and cascade sources with explicit mass, state and heat bases.

**공개 API**

- [`pipeline_integrity.calibrated_emissions.evaluate_filling_vapor`](../src/pipeline_integrity/calibrated_emissions.py#L32)

**검증 영역:** Prescribed headspace displacement and heat/feed-limited evaporation with zero additional empirical capacity-generation term; that empirical term remains outside this profile.

**적용한 기준:** independent_headspace_inventory_ODE, latent_energy_budget, finite_liquid_supply.

**재현할 시험 선택자**

```text
tests/test_emissions_inventory_independent.py::test_filling_displaced_inventory_and_heat_limited_evaporation_ODE
```

## 46. `emissions.tank_standing_working_loss`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional tank and cascade sources with explicit mass, state and heat bases.

**공개 API**

- [`pipeline_integrity.calibrated_emissions.evaluate_tank_standing_working_loss`](../src/pipeline_integrity/calibrated_emissions.py#L87)

**검증 영역:** Manufactured constant-composition ideal-gas fixed-roof heating/venting episodes; seal, weather and wetting calibration remain outside this profile.

**적용한 기준:** independent_ideal_gas_inventory, temperature_partition_convergence, calendar_window_accounting.

**재현할 시험 선택자**

```text
tests/test_emissions_inventory_independent.py::test_tank_breathing_partition_refines_to_independent_ideal_gas_inventory_loss
```

## 47. `energy.linear_liquid_storage`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Linear volumetric-elastic compression work for a caller-specified liquid state.

**공개 API**

- [`pipeline_integrity.stored_liquid_energy.evaluate_linear_liquid_compression`](../src/pipeline_integrity/stored_liquid_energy.py#L43)

**검증 영역:** Small-strain liquid compression with supplied constant bulk modulus relative to zero pressure increment.

**적용한 기준:** independent_pressure_volume_work, work_conjugate_derivative, rigid_limit, similarity.

**재현할 시험 선택자**

```text
tests/test_stored_liquid_energy_independent.py::test_energy_matches_separately_integrated_compression_work
tests/test_stored_liquid_energy_independent.py::test_zero_reference_load_and_rigid_modulus_limit
tests/test_stored_liquid_energy_independent.py::test_pressure_volume_similarity_and_work_conjugate_derivative
```

## 48. `engineering.target.constant_stress_viscoelastic_strain`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Constant tensile-stress strain from elastic, delayed and viscous components.

**공개 API**

- [`pipeline_integrity.viscoelastic_strain.evaluate_constant_stress_strain`](../src/pipeline_integrity/viscoelastic_strain.py#L18)

**검증 영역:** Supplied linear viscoelastic element constants under held tensile stress; no material calibration.

**적용한 기준:** independent_element_ODE, initial_long_time_limits, stress_time_scaling.

**재현할 시험 선택자**

```text
tests/test_viscoelastic_strain_independent.py::test_strain_matches_independently_integrated_element_odes
tests/test_viscoelastic_strain_independent.py::test_zero_time_short_time_and_long_time_limits
tests/test_viscoelastic_strain_independent.py::test_stress_linearity_and_zero_stress
tests/test_viscoelastic_strain_independent.py::test_coordinate_order_and_common_time_viscosity_scaling
```

## 49. `engineering.target.covered_pipe_flotation_balance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local submerged bare-pipe and cover-prism vertical force balance.

**공개 API**

- [`pipeline_integrity.covered_pipe_uplift.evaluate_covered_pipe_uplift`](../src/pipeline_integrity/covered_pipe_uplift.py#L42)

**검증 영역:** Local fully submerged empty pipe and declared backfill prism weight; no soil failure mechanism.

**적용한 기준:** displaced_solid_volume, mass_force_ledger, geometric_similarity, zero_cover_limit.

**재현할 시험 선택자**

```text
tests/test_covered_pipe_uplift_independent.py::test_vertical_margin_matches_mass_and_displaced_solid_volume_ledger
tests/test_covered_pipe_uplift_independent.py::test_geometric_similarity_scales_all_forces_per_length_quadratically
tests/test_covered_pipe_uplift_independent.py::test_zero_cover_does_not_require_backfill_properties
tests/test_covered_pipe_uplift_independent.py::test_equality_of_unfactored_solid_cylinder_and_water_weight_is_preserved
```

## 50. `engineering.target.local_wind_pressure_force`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local aerodynamic load and shedding-frequency calculations in SI units.

**공개 API**

- [`pipeline_integrity.wind_screening.evaluate_wind_load`](../src/pipeline_integrity/wind_screening.py#L40)

**검증 영역:** Local force from kinetic pressure and caller-supplied aerodynamic multipliers; no coefficient calibration or structural response.

**적용한 기준:** kinetic_energy_density, projected_area_force, scaling, zero_load.

**재현할 시험 선택자**

```text
tests/test_wind_screening_independent_local_20261101.py::test_wind_pressure_matches_kinetic_energy_per_volume_and_area_load
tests/test_wind_screening_independent_local_20261101.py::test_velocity_density_area_and_reviewed_multipliers_have_distinct_scalings
tests/test_wind_screening_independent_local_20261101.py::test_zero_load_limits
```

## 51. `engineering.target.nut_factor_torque_preload`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed fabrication inputs around three material-owned calculation models.

**공개 API**

- [`pipeline_integrity.fabrication_exchange.evaluate_fabrication`](../src/pipeline_integrity/fabrication_exchange.py#L190)

**검증 영역:** Supplied nut factor derived for a manufactured helical thread/collar friction work reference; no real fastener torque calibration.

**적용한 기준:** independent_helical_contact_work, axial_force_balance.

**재현할 시험 선택자**

```text
tests/test_remaining_physical_limits_independent.py::test_nut_factor_from_independent_helical_contact_work_recovers_axial_load
```

## 52. `engineering.target.seabed_static_margin`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local static seabed force balance with caller-supplied loads per pipe length.

**공개 API**

- [`pipeline_integrity.seabed_balance.evaluate_seabed_contact_balance`](../src/pipeline_integrity/seabed_balance.py#L16)

**검증 영역:** Local stationary per-length force ledger and supplied Coulomb resistance while contact remains; no dynamic seabed response.

**적용한 기준:** signed_force_ledger, contact_loss_boundary, sliding_threshold, load_similarity.

**재현할 시험 선택자**

```text
tests/test_seabed_balance_independent.py::test_independent_vertical_and_horizontal_force_ledgers
tests/test_seabed_balance_independent.py::test_axis_reversal_preserves_resistance_and_opposing_forces_cancel
tests/test_seabed_balance_independent.py::test_analytic_lift_threshold_brackets_sliding_boundary
tests/test_seabed_balance_independent.py::test_contact_loss_withholds_coulomb_capacity_and_preserves_signed_normal
tests/test_seabed_balance_independent.py::test_common_force_scale_preserves_normalized_margin
```

## 53. `engineering.target.soil_prism_overburden`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local backfill-prism weight and water-head load at a declared plane.

**공개 API**

- [`pipeline_integrity.soil_prism_load.evaluate_soil_prism_load`](../src/pipeline_integrity/soil_prism_load.py#L38)

**검증 영역:** Declared dry/submerged soil-prism weights and hydrostatic pore pressure at one plane; no soil arching law.

**적용한 기준:** independent_layer_weight, pressure_line_force, width_similarity, zero_cover.

**재현할 시험 선택자**

```text
tests/test_soil_prism_load_independent.py::test_dry_prism_pressure_equals_declared_unit_length_weight_per_area
tests/test_soil_prism_load_independent.py::test_partly_wet_prism_matches_independent_layer_weight_ledger
tests/test_soil_prism_load_independent.py::test_width_scales_line_load_without_changing_pressure
tests/test_soil_prism_load_independent.py::test_water_level_above_ground_is_not_silently_extrapolated
```

## 54. `engineering.target.stationary_section_hydrodynamic_load`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Instantaneous fluid loads per metre of a stationary circular cylinder.

**공개 API**

- [`pipeline_integrity.cylinder_hydrodynamics.evaluate_stationary_cylinder_load`](../src/pipeline_integrity/cylinder_hydrodynamics.py#L59)

**검증 영역:** Instantaneous stationary cylinder loads with supplied drag/lift/inertia coefficients; no coefficient calibration or moving-body dynamics.

**적용한 기준:** whole_segment_force, displaced_mass_impulse, signed_axis_conventions, geometric_similarity.

**재현할 시험 선택자**

```text
tests/test_cylinder_hydrodynamics_individual_local.py::test_section_load_equals_independent_whole_segment_pressure_area_normalization
tests/test_cylinder_hydrodynamics_individual_local.py::test_velocity_reversal_changes_drag_but_lift_uses_its_own_signed_axis
tests/test_cylinder_hydrodynamics_individual_local.py::test_total_and_added_conventions_match_and_keep_displaced_fluid_component
tests/test_cylinder_hydrodynamics_individual_local.py::test_zero_added_mass_retains_displaced_fluid_acceleration_load
tests/test_cylinder_hydrodynamics_individual_local.py::test_diameter_and_velocity_scaling_distinguish_area_from_projected_width
tests/test_cylinder_hydrodynamics_individual_local.py::test_acceleration_impulse_matches_displaced_mass_times_velocity_change
tests/test_cylinder_hydrodynamics_individual_local.py::test_signed_inertia_can_cancel_drag_without_adding_magnitudes
```

## 55. `engineering.target.strouhal_frequency_screen`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local aerodynamic load and shedding-frequency calculations in SI units.

**공개 API**

- [`pipeline_integrity.wind_screening.evaluate_vortex_frequency`](../src/pipeline_integrity/wind_screening.py#L79)

**검증 영역:** Caller-supplied Strouhal coordinate and harmonic frequency comparison; no vortex-lock-in response or Strouhal calibration.

**적용한 기준:** dimensionless_frequency, harmonic_band_comparison, zero_flow_limit.

**재현할 시험 선택자**

```text
tests/test_wind_screening_independent_local_20261101.py::test_shedding_frequencies_recover_dimensionless_strouhal_and_harmonic
tests/test_wind_screening_independent_local_20261101.py::test_caller_band_is_closed_and_compares_each_harmonic_separately
tests/test_wind_screening_independent_local_20261101.py::test_still_flow_has_no_shedding_frequency
```

## 56. `engineering.target.supplied_gasket_required_preload`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two local bolt-demand components from supplied effective gasket data.

**공개 API**

- [`pipeline_integrity.gasket_bolt_demand.evaluate_gasket_bolt_demand`](../src/pipeline_integrity/gasket_bolt_demand.py#L30)

**검증 영역:** Supplied gasket factors and effective widths for seating and operating preload demand; no leak-tightness or code compliance claim.

**적용한 기준:** independent_pressure_surface_strip_integrals, governing_case, area_similarity.

**재현할 시험 선택자**

```text
tests/test_gasket_bolt_demand_independent.py::test_three_load_components_match_separate_surface_and_strip_integrals
tests/test_gasket_bolt_demand_independent.py::test_governing_case_is_explicit_including_zero_and_equal_limits
tests/test_gasket_bolt_demand_independent.py::test_pressure_scaling_does_not_change_assembly_seating_demand
tests/test_gasket_bolt_demand_independent.py::test_common_geometry_scale_changes_all_loads_with_area
tests/test_gasket_bolt_demand_independent.py::test_zero_operating_factor_removes_seal_term_without_removing_pressure_end_force
```

## 57. `fabrication.supplied_neutral_axis_curvature`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Circular-bend kinematics about an explicitly located neutral surface.

**공개 API**

- [`pipeline_integrity.neutral_surface_bending.evaluate_neutral_surface_bending`](../src/pipeline_integrity/neutral_surface_bending.py#L33)

**검증 영역:** Circular plane-section kinematics about an explicitly located neutral surface; no plastic neutral-axis prediction or springback.

**적용한 기준:** independent_face_arc_length_ratio, neutral_radius_geometry.

**재현할 시험 선택자**

```text
tests/test_mechanics_223_gap_implementations.py::test_neutral_arc_ratio_and_face_radius
```

## 58. `fatigue.supplied_stress_life`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Calibrated stress-life curve coordinates through the existing Basquin owner.

**공개 API**

- [`pipeline_integrity.stress_life_coordinate.evaluate_stress_life_coordinate`](../src/pipeline_integrity/stress_life_coordinate.py#L45)

**검증 영역:** Caller-supplied constant-amplitude stress-life curve coordinates and cycle convention; no measured fatigue calibration.

**적용한 기준:** independent_coefficient_form, amplitude_range_cycle_basis, stress_unit_invariance.

**재현할 시험 선택자**

```text
tests/test_stress_life_coordinate_independent.py::test_effective_component_amplitude_matches_supplied_coefficient_form
tests/test_stress_life_coordinate_independent.py::test_maximum_stress_curve_matches_independent_cycles_times_stress_power_form
tests/test_stress_life_coordinate_independent.py::test_amplitude_and_full_range_change_coordinates_without_doubling_cycle_count
tests/test_stress_life_coordinate_independent.py::test_coherent_stress_unit_scale_leaves_cycle_life_unchanged
```

## 59. `fatigue_mean_stress_correction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Fixed-life strain capacity by inversion of existing mean-sensitive owners.
- Audited material-engine jobs, pressure sections and solid-temperature signal exchange.
- Reviewed elastic tensile-mean fatigue coordinates through one existing owner.
- Machine-precision endpoint reconciliation around an unchanged SWT owner.
- A supplied fixed-life Goodman envelope intersected with an elastic peak limit.

**공개 API**

- [`pipeline_integrity.fixed_life_mean_strain.evaluate_fixed_life_mean_strain`](../src/pipeline_integrity/fixed_life_mean_strain.py#L31)
- [`pipeline_integrity.material_exchange.evaluate_material_job`](../src/pipeline_integrity/material_exchange.py#L89)
- [`pipeline_integrity.material_exchange.prepare_material_job`](../src/pipeline_integrity/material_exchange.py#L53)
- [`pipeline_integrity.mean_stress_coordinate.evaluate_mean_stress_coordinate`](../src/pipeline_integrity/mean_stress_coordinate.py#L40)
- [`pipeline_integrity.swt_endpoint_compatibility.evaluate_swt_cycle_job`](../src/pipeline_integrity/swt_endpoint_compatibility.py#L46)
- [`pipeline_integrity.yield_limited_mean_stress.evaluate_yield_limited_mean_stress`](../src/pipeline_integrity/yield_limited_mean_stress.py#L27)

**검증 영역:** Supplied Morrow/SWT strain-life laws inside reviewed cycle support; no new measured mean-stress calibration.

**적용한 기준:** independent_high_precision_quadratic_cubic, zero_mean_limits, stress_modulus_similarity.

**재현할 시험 선택자**

```text
tests/test_morrow_swt_strain_life_independent.py::test_morrow_inverse_matches_independent_high_precision_quadratic
tests/test_morrow_swt_strain_life_independent.py::test_morrow_mean_effect_continuously_weakens_as_plastic_term_dominates
tests/test_morrow_swt_strain_life_independent.py::test_morrow_zero_mean_reduces_to_existing_fully_reversed_strain_life
tests/test_morrow_swt_strain_life_independent.py::test_swt_inverse_matches_independent_high_precision_monotone_cubic
tests/test_morrow_swt_strain_life_independent.py::test_swt_zero_mean_consistent_stress_strain_anchor_recovers_baseline_cycles
tests/test_morrow_swt_strain_life_independent.py::test_swt_common_stress_modulus_scaling_preserves_life_and_scales_parameter
```

## 60. `flange_external_load_bolt_stress`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Rigid joint faces, explicit elastic bolt/member sharing and retained contact.

**공개 API**

- [`pipeline_integrity.elastic_bolt_group.evaluate_elastic_bolt_group`](../src/pipeline_integrity/elastic_bolt_group.py#L28)

**검증 영역:** Rigid joint faces with explicitly supplied elastic bolt/member sharing before contact separation.

**적용한 기준:** angular_resultant_solution, virtual_work, equilibrium, signed_load_reversal.

**재현할 시험 선택자**

```text
tests/test_elastic_bolt_group_independent.py::test_circular_bolt_group_matches_independent_angular_resultant_solution
tests/test_elastic_bolt_group_independent.py::test_irregular_heterogeneous_joint_virtual_work_and_equilibrium
tests/test_elastic_bolt_group_independent.py::test_contact_separation_is_not_continued_as_linear_safe_state
tests/test_elastic_bolt_group_independent.py::test_load_reversal_preserves_incremental_energy_and_reverses_increment
```

## 61. `fluid.horizontal_jet_discharge`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Horizontal ballistic liquid-jet observation without empirical discharge factors.

**공개 API**

- [`pipeline_integrity.horizontal_jet.invert_horizontal_jet`](../src/pipeline_integrity/horizontal_jet.py#L14)

**검증 영역:** Horizontal ballistic parcel with constant gravity and separately supplied actual jet-area/mean-velocity mapping; no empirical discharge factor.

**적용한 기준:** independent_Newton_trajectory_IVP, observed_area_flux, gravity_geometry_similarity.

**재현할 시험 선택자**

```text
tests/test_horizontal_jet_individual.py::test_independent_time_integrated_trajectory_inverse
tests/test_horizontal_jet_individual.py::test_actual_jet_area_and_tracked_mean_mapping_are_separate
tests/test_horizontal_jet_individual.py::test_degenerate_observation_is_not_fitted
tests/test_horizontal_jet_individual.py::test_gravity_and_horizontal_geometric_similarity
```

## 62. `fluid_pipe.closed_mass_compatibility`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Quasistatic uniform liquid/pipe mass compatibility with explicit property providers.

**공개 API**

- [`pipeline_integrity.closed_liquid_compatibility.evaluate_closed_liquid_compatibility`](../src/pipeline_integrity/closed_liquid_compatibility.py#L231)
- [`pipeline_integrity.closed_liquid_compatibility.evaluate_required_liquid_injection`](../src/pipeline_integrity/closed_liquid_compatibility.py#L321)

**검증 영역:** Quasistatic closed liquid mass balance with reviewed pressure-volume and density providers, including compressed liquid water; supplied constitutive applicability remains external.

**적용한 기준:** independent_thermal_pressure_root, quadratic_mass_compatibility, signed_added_mass, extensive_scaling, actual_water_mass_budget.

**재현할 시험 선택자**

```text
tests/test_closed_liquid_compatibility_individual.py::test_affine_specific_volume_exact_thermal_pressure_reference
tests/test_closed_liquid_compatibility_individual.py::test_signed_mass_change_and_distinct_wall_water_temperatures
tests/test_closed_liquid_compatibility_individual.py::test_affine_density_volume_product_has_independent_quadratic_root
tests/test_closed_liquid_compatibility_individual.py::test_volume_and_added_mass_extensive_scaling
tests/test_closed_liquid_compatibility_individual.py::test_actual_compressed_water_eos_mass_budget_with_distinct_temperatures
```

## 63. `fracture.elastic_energy_release`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Mode-I linear-elastic intensity to energy-release coordinate.

**공개 API**

- [`pipeline_integrity.elastic_fracture_energy.evaluate_elastic_fracture_energy`](../src/pipeline_integrity/elastic_fracture_energy.py#L13)

**검증 영역:** Isotropic linear-elastic mode-I near-tip energy release for declared plane stress/strain, without plasticity or measured fracture resistance.

**적용한 기준:** independent_J_contour_integral, contour_radius_invariance, zero_intensity_limit.

**재현할 시험 선택자**

```text
tests/test_elastic_fracture_energy_independent.py::test_j_contour_integral_is_radius_independent_and_matches_energy_coordinate
tests/test_elastic_fracture_energy_independent.py::test_zero_intensity_zero_energy
```

## 64. `fracture.growth_threshold_critical_modifiers`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Threshold and critical-intensity modifiers of a supplied baseline growth rate.

**공개 API**

- [`pipeline_integrity.crack_growth_modifiers.evaluate_crack_growth_modifiers`](../src/pipeline_integrity/crack_growth_modifiers.py#L31)

**검증 영역:** Supplied threshold and critical-intensity modifiers of a declared baseline crack-growth rate; no calibration or failure-time prediction.

**적용한 기준:** independent_log_rate_reference, threshold_arrest, critical_domain_boundary, intensity_similarity.

**재현할 시험 선택자**

```text
tests/test_crack_growth_modifiers_independent.py::test_rate_matches_separate_log_coordinate_reference
tests/test_crack_growth_modifiers_independent.py::test_arrest_branch_and_exact_threshold_return_zero
tests/test_crack_growth_modifiers_independent.py::test_critical_boundary_never_returns_divergent_growth
tests/test_crack_growth_modifiers_independent.py::test_common_intensity_coordinate_scaling_preserves_modifiers
tests/test_crack_growth_modifiers_independent.py::test_increasing_intensity_near_critical_accelerates_without_crossing_boundary
```

## 65. `fracture.local_energy_arrest`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local elastic driving-energy and supplied impact-energy resistance screen.

**공개 API**

- [`pipeline_integrity.local_crack_arrest_energy.evaluate_local_crack_arrest_energy`](../src/pipeline_integrity/local_crack_arrest_energy.py#L15)

**검증 영역:** Declared uniaxial elastic energy release length and caller-supplied resistance transfer; no empirical arrest-factor selection.

**적용한 기준:** independent_loading_work, release_length_energy, strict_comparison_boundary.

**재현할 시험 선택자**

```text
tests/test_local_crack_arrest_energy_independent.py::test_independent_loading_work_times_release_length
tests/test_local_crack_arrest_energy_independent.py::test_resistance_input_is_a_separate_energy_area_transfer
tests/test_local_crack_arrest_energy_independent.py::test_equality_is_not_a_strictly_lower_driving_energy
```

## 66. `fracture.mode_i_coordinate`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Evidence-qualified use of existing Mode-I, growth and declared-boundary models.
- Proof-survival inversion of a reviewed surface-flaw failure-pressure curve.
- Length-based through-crack coordinates through the existing Mode-I owner.

**공개 API**

- [`pipeline_integrity.fracture_exchange.assess_crack_growth`](../src/pipeline_integrity/fracture_exchange.py#L273)
- [`pipeline_integrity.fracture_exchange.assess_failure_points`](../src/pipeline_integrity/fracture_exchange.py#L405)
- [`pipeline_integrity.fracture_exchange.assess_mode_i`](../src/pipeline_integrity/fracture_exchange.py#L189)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L79)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L112)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L105)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L143)
- [`pipeline_integrity.surface_flaw_proof.evaluate_surface_flaw_proof`](../src/pipeline_integrity/surface_flaw_proof.py#L63)
- [`pipeline_integrity.through_crack_intensity.evaluate_through_crack_intensity`](../src/pipeline_integrity/through_crack_intensity.py#L20)

**검증 영역:** Infinite elastic plate uniform crack-opening traction with geometry factor one and supplied toughness; no surface flaw geometry prediction.

**적용한 기준:** independent_crack_weight_integral, elastic_closure_energy, toughness_margin.

**재현할 시험 선택자**

```text
tests/test_through_crack_intensity_independent.py::test_weight_function_integral_and_elastic_crack_closure_energy
tests/test_through_crack_intensity_independent.py::test_half_length_never_surface_penetration_gate_and_no_variable_factor_critical_claim
```

## 67. `fracture.paris_growth`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Evidence-qualified use of existing Mode-I, growth and declared-boundary models.
- A scalar Region-II rate observation using the shared growth calibration.
- Proof-survival inversion of a reviewed surface-flaw failure-pressure curve.

**공개 API**

- [`pipeline_integrity.fracture_exchange.assess_crack_growth`](../src/pipeline_integrity/fracture_exchange.py#L273)
- [`pipeline_integrity.fracture_exchange.assess_failure_points`](../src/pipeline_integrity/fracture_exchange.py#L405)
- [`pipeline_integrity.fracture_exchange.assess_mode_i`](../src/pipeline_integrity/fracture_exchange.py#L189)
- [`pipeline_integrity.paris_region_rate.evaluate_paris_region_rate`](../src/pipeline_integrity/paris_region_rate.py#L40)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L79)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L112)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L105)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L143)
- [`pipeline_integrity.surface_flaw_proof.evaluate_surface_flaw_proof`](../src/pipeline_integrity/surface_flaw_proof.py#L63)

**검증 영역:** Synthetic constant-geometry Paris growth with ordered stress blocks and prescribed depth event; no material calibration or other fracture failure mechanisms.

**적용한 기준:** independent_transformed_depth_solution, ordered_block_history, terminal_depth_event.

**재현할 시험 선택자**

```text
tests/test_variable_paris_analytic_hangang.py::test_two_ordered_stress_blocks_match_exact_transformed_depth
tests/test_variable_paris_analytic_hangang.py::test_depth_limit_event_matches_exact_cycles_to_terminal_depth
```

## 68. `fracture.surface_flaw_proof_survival`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Evidence-qualified use of existing Mode-I, growth and declared-boundary models.
- Proof-survival inversion of a reviewed surface-flaw failure-pressure curve.
- Irreversible tearing under a reviewed pressure-history driving-force map.

**공개 API**

- [`pipeline_integrity.fracture_exchange.assess_crack_growth`](../src/pipeline_integrity/fracture_exchange.py#L273)
- [`pipeline_integrity.fracture_exchange.assess_failure_points`](../src/pipeline_integrity/fracture_exchange.py#L405)
- [`pipeline_integrity.fracture_exchange.assess_mode_i`](../src/pipeline_integrity/fracture_exchange.py#L189)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L79)
- [`pipeline_integrity.surface_flaw_proof.bind_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L112)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_growth`](../src/pipeline_integrity/surface_flaw_proof.py#L105)
- [`pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_tearing`](../src/pipeline_integrity/surface_flaw_proof.py#L143)
- [`pipeline_integrity.surface_flaw_proof.evaluate_surface_flaw_proof`](../src/pipeline_integrity/surface_flaw_proof.py#L63)
- [`pipeline_integrity.time_dependent_tearing.evaluate_pressure_history_tearing`](../src/pipeline_integrity/time_dependent_tearing.py#L83)

**검증 영역:** Supplied monotone manufactured constant-geometry elastic failure-pressure curve and supported proof/operating depth bounds; no proof-induced plasticity, actual flaw calibration or growth variant qualification.

**적용한 기준:** independent_LEFM_energy_bound, curve_mesh_convergence, proof_operating_order.

**재현할 시험 선택자**

```text
tests/test_surface_flaw_proof_lefm_independent.py::test_proof_depth_bounds_converge_to_independent_elastic_crack_energy_limit
```

## 69. `fragmentation.supplied_empirical_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Ideal areal-mass impulse versus constant-flow plastic-work coordinate.

**공개 API**

- [`pipeline_integrity.plastic_impulse_budget.evaluate_plastic_impulse_budget`](../src/pipeline_integrity/plastic_impulse_budget.py#L15)

**검증 영역:** Ideal constant-flow plastic work versus supplied sheet areal-mass impulse shape; no actual fragmentation or failure strain calibration.

**적용한 기준:** independent_strain_work, pressure_impulse_integral, momentum_kinetic_budget, thickness_similarity.

**재현할 시험 선택자**

```text
tests/test_plastic_impulse_budget_independent.py::test_strain_work_equals_kinetic_energy_from_pressure_impulse
tests/test_plastic_impulse_budget_independent.py::test_triangular_shape_peak_preserves_impulse_budget
tests/test_plastic_impulse_budget_independent.py::test_areal_thickness_scaling_preserves_specific_material_work_and_velocity
```

## 70. `framework.evolving_thermoelastic_pipe_field`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Evolving ideal-gas/elastic-area field through the existing conservative FV.
- Conservative ideal-gas field coupled to a thermally expanding elastic wall.

**공개 API**

- [`pipeline_integrity.elastic_area_gas_field.solve_elastic_area_gas_field`](../src/pipeline_integrity/elastic_area_gas_field.py#L147)
- [`pipeline_integrity.thermoelastic_pipe_field.solve_thermoelastic_pipe_field`](../src/pipeline_integrity/thermoelastic_pipe_field.py#L181)

**검증 영역:** Ideal-gas conservative field with pressure-compliant elastic area, constant exterior pressure and prescribed wall temperature; thermal-expansion wall variant remains outside this profile.

**적용한 기준:** independent_physical_isentrope, space_time_convergence, fluid_wall_energy_partition, entropy_budget, boundary_conservation.

**재현할 시험 선택자**

```text
tests/test_elastic_area_gas_field_independent.py::test_actual_ideal_PVT_area_and_energy_restore
tests/test_elastic_area_gas_field_independent.py::test_physical_work_and_momentum_flux_identities
tests/test_elastic_area_gas_field_independent.py::test_effective_wave_from_independent_physical_isentrope
tests/test_elastic_area_gas_field_independent.py::test_effective_line_cv_by_fixed_mass_energy_temperature_derivative
tests/test_elastic_area_gas_field_independent.py::test_C_zero_actual_rigid_FV_equivalence
tests/test_elastic_area_gas_field_independent.py::test_homogeneous_heat_pressure_ODE_time_convergence_and_physical_energy_partition
tests/test_elastic_area_gas_field_independent.py::test_periodic_smallwave_independent_characteristic_phase_and_grid_convergence
tests/test_elastic_area_gas_field_independent.py::test_periodic_energy_reference_shift_and_component_mass_energy_balances
tests/test_elastic_area_gas_field_independent.py::test_supported_boundary_physical_restore_and_actual_conservative_ledgers
tests/test_elastic_area_gas_field_independent.py::test_adiabatic_periodic_physical_entropy_budget
```

## 71. `free_surface.circular_saint_venant`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nonlinear wet free-surface Saint-Venant flow in one rigid circular pipe.

**공개 API**

- [`pipeline_integrity.gravity_free_surface_pipe.solve_circular_free_surface_pipe`](../src/pipeline_integrity/gravity_free_surface_pipe.py#L250)

**검증 영역:** Circular one-dimensional free-surface Saint-Venant field with supported gravity/friction and boundary stages; no mixed pressurized or dry-front extension.

**적용한 기준:** independent_geometry_quadrature, Riemann_shock_rarefaction, spatial_convergence, volume_momentum_budget, gravity_work, boundary_event.

**재현할 시험 선택자**

```text
tests/test_circular_free_surface_independent.py::test_circular_geometry_matches_independent_positive_cross_section_quadratures
tests/test_circular_free_surface_independent.py::test_area_inverse_preserves_the_supplied_area_and_recovers_depth
tests/test_circular_free_surface_independent.py::test_flat_resting_lake_is_stationary_under_consistent_boundary_states
tests/test_circular_free_surface_independent.py::test_uniform_chezy_flow_balances_tangential_gravity_and_friction
tests/test_circular_free_surface_independent.py::test_periodic_nonlinear_motion_conserves_volume_and_momentum_without_claiming_exact_energy
tests/test_circular_free_surface_independent.py::test_small_free_surface_wave_has_geometric_wave_speed_and_spatial_convergence
tests/test_circular_free_surface_independent.py::test_nonlinear_circular_shock_and_rarefaction_match_independent_riemann_solution_with_refinement
tests/test_circular_free_surface_independent.py::test_uniform_tangential_gravity_has_exact_acceleration_and_work_budget
tests/test_circular_free_surface_independent.py::test_uniform_chezy_friction_has_signed_analytic_decay_and_mechanical_dissipation
tests/test_circular_free_surface_independent.py::test_closure_event_and_optin_snapshots_preserve_pre_event_state_and_mass_budget
tests/test_circular_free_surface_independent.py::test_strong_closure_that_cannot_remain_free_surface_terminates_without_a_fake_computed_result
tests/test_circular_free_surface_independent.py::test_scalar_and_per_cell_chezy_closures_agree_for_the_same_supplied_coefficient
tests/test_circular_free_surface_independent.py::test_observation_times_are_not_reordered_clipped_or_merged
```

## 72. `free_surface.circular_steady_depth_observers`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady wet circular-section observations on the existing geometry/Chezy law.

**공개 API**

- [`pipeline_integrity.steady_circular_depth.evaluate_steady_circular_profile`](../src/pipeline_integrity/steady_circular_depth.py#L189)
- [`pipeline_integrity.steady_circular_depth.solve_circular_critical_depth`](../src/pipeline_integrity/steady_circular_depth.py#L162)
- [`pipeline_integrity.steady_circular_depth.solve_circular_normal_depth`](../src/pipeline_integrity/steady_circular_depth.py#L109)
- [`pipeline_integrity.steady_circular_depth.solve_circular_normal_depth_branches`](../src/pipeline_integrity/steady_circular_depth.py#L113)

**검증 영역:** Supported smooth steady circular free-surface branches with supplied Chezy closure and explicit critical-depth margin.

**적용한 기준:** independent_section_force, energy_minimum, spatial_energy_quadrature, critical_event, static_depth_boundary.

**재현할 시험 선택자**

```text
tests/test_steady_circular_depth_individual.py::test_normal_depth_recovers_independent_section_force_balance
tests/test_steady_circular_depth_individual.py::test_selected_brackets_keep_both_valid_near_full_normal_depth_roots
tests/test_steady_circular_depth_individual.py::test_critical_depth_minimizes_independent_specific_energy
tests/test_steady_circular_depth_individual.py::test_lossless_steady_profiles_match_independent_energy_level_roots
tests/test_steady_circular_depth_individual.py::test_resisted_profile_matches_independent_spatial_quadrature
tests/test_steady_circular_depth_individual.py::test_uniform_chezy_state_remains_exactly_steady_with_signed_energy_work
tests/test_steady_circular_depth_individual.py::test_critical_margin_event_stops_before_singularity_at_independent_quadrature_station
tests/test_steady_circular_depth_individual.py::test_static_tilted_surface_stops_at_declared_upper_depth_without_clipping
```

## 73. `free_surface.circular_uniform_friction_balance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform prismatic free-surface flow with a supplied dimensional Chezy factor.

**공개 API**

- [`pipeline_integrity.gravity_uniform_flow.evaluate_uniform_chezy_flow`](../src/pipeline_integrity/gravity_uniform_flow.py#L26)

**검증 영역:** Uniform open-channel flow with caller-supplied dimensional Chezy factor and known wetted geometry; no roughness calibration.

**적용한 기준:** gravity_resistance_balance, half_full_circle_limit, geometric_similarity.

**재현할 시험 선택자**

```text
tests/test_uniform_chezy_individual.py::test_uniform_gravity_and_resistance_terms_balance_with_signed_flow
tests/test_uniform_chezy_individual.py::test_half_full_circle_has_half_radius_hydraulic_radius
tests/test_uniform_chezy_individual.py::test_geometric_similarity_scales_velocity_and_flow
tests/test_uniform_chezy_individual.py::test_dry_or_zero_perimeter_geometry_is_outside_this_wet_flow_relation
```

## 74. `gas.capacity_limited_regulator_boundary`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady pressure-control feasibility with finite reviewed gas valve capacity.
- Finite gas-valve capacity by inverting the existing native Kv sizing leaf.

**공개 API**

- [`pipeline_integrity.gas_regulator_boundary.solve_gas_regulator_boundary`](../src/pipeline_integrity/gas_regulator_boundary.py#L90)
- [`pipeline_integrity.gas_regulator_cv_capacity.NativeCvGasCapacityProvider.__call__`](../src/pipeline_integrity/gas_regulator_cv_capacity.py#L54)

**검증 영역:** Reviewed steady supplied gas branch laws and bounded valve capacity with pressure/temperature domains; no controller transient dynamics or device coefficient calibration.

**적용한 기준:** independent_mass_equilibrium, capacity_inverse, choked_plateau, saturated_pressure_root_refinement.

**재현할 시험 선택자**

```text
tests/test_gas_regulator_boundary_independent.py::test_two_pressure_squared_resistances_independent_common_mass_equilibrium
tests/test_gas_regulator_boundary_independent.py::test_zero_external_flow_closes_capacity_without_inventing_a_stem_curve
tests/test_gas_regulator_boundary_independent.py::test_native_maximum_kv_inverse_against_dimensional_gas_expansion_capacity
tests/test_gas_regulator_boundary_independent.py::test_native_choked_capacity_plateau_and_no_guessed_normal_density
tests/test_gas_regulator_boundary_independent.py::test_actual_native_kv_capacity_with_both_control_sides_and_saturation
tests/test_gas_regulator_boundary_independent.py::test_saturated_pressure_root_refinement_retains_original_coarse_tolerance
tests/test_gas_regulator_boundary_independent.py::test_actual_existing_coolprop_factory_and_explicit_normal_mass_basis
```

## 75. `gas.conditional_legacy_blowdown_estimator`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Quasi-steady ideal-gas restriction coupled to a pipe acoustic boundary.

**공개 API**

- [`pipeline_integrity.gas_restricted_vent.solve_ideal_restricted_pipe_blowdown`](../src/pipeline_integrity/gas_restricted_vent.py#L136)

**검증 영역:** Reviewed finite ideal-gas pipe with supported nozzle face and declared finite horizon/pressure endpoint; no legacy empirical emptying-time correlation.

**적용한 기준:** independent_nozzle_capacity, characteristic_boundary_matching, adiabatic_inventory_limit, opening_event, energy_origin_invariance.

**재현할 시험 선택자**

```text
tests/test_restricted_gas_vent_independent.py::test_existing_relief_shape_normalizes_to_independent_SI_nozzle_capacity
tests/test_restricted_gas_vent_independent.py::test_pipe_face_independently_matches_acoustic_invariant_and_nozzle
tests/test_restricted_gas_vent_independent.py::test_finite_pipe_pressure_endpoint_against_independent_adiabatic_inventory_limit
tests/test_restricted_gas_vent_independent.py::test_opening_event_and_explicit_finite_horizon_are_not_time_to_empty
tests/test_restricted_gas_vent_independent.py::test_negative_energy_reference_keeps_mass_pressure_endpoint_and_balances
```

## 76. `gas.conservative_rigid_pipe_1d`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conservative nonlinear gas/supercritical fluid in one rigid constant-area pipe.
- Reviewed same-fluid ideal-reservoir open edge with bounded reverse inflow.
- Quasi-steady ideal-gas restriction coupled to a pipe acoustic boundary.
- Calorically perfect gas normal incident shock reflected from a rigid wall.

**공개 API**

- [`pipeline_integrity.gas_acoustic_pipe.evaluate_pressure_outflow_face`](../src/pipeline_integrity/gas_acoustic_pipe.py#L280)
- [`pipeline_integrity.gas_acoustic_pipe.solve_gas_acoustic_pipe`](../src/pipeline_integrity/gas_acoustic_pipe.py#L371)
- [`pipeline_integrity.gas_reservoir_boundary.evaluate_ideal_same_fluid_open_face`](../src/pipeline_integrity/gas_reservoir_boundary.py#L17)
- [`pipeline_integrity.gas_restricted_vent.solve_ideal_restricted_pipe_blowdown`](../src/pipeline_integrity/gas_restricted_vent.py#L136)
- [`pipeline_integrity.normal_shock_wall_reflection.evaluate_normal_shock_wall_reflection`](../src/pipeline_integrity/normal_shock_wall_reflection.py#L17)

**검증 영역:** Ideal-gas one-dimensional Euler flow in a rigid constant-area pipe with declared sources and supported boundaries.

**적용한 기준:** independent_riemann_and_acoustic_solution, mass_momentum_energy_conservation, spatial_convergence, source_work_and_heat, boundary_events.

**재현할 시험 선택자**

```text
tests/test_gas_acoustic_pipe_independent.py::test_uniform_nonlinear_state_is_preserved_under_consistent_ghost_boundaries
tests/test_gas_acoustic_pipe_independent.py::test_nonuniform_periodic_mass_momentum_and_total_energy_are_conserved
tests/test_gas_acoustic_pipe_independent.py::test_advected_entropy_contact_has_spatial_convergence_and_constant_pressure_velocity
tests/test_gas_acoustic_pipe_independent.py::test_small_right_running_acoustic_mode_has_the_independent_sound_speed
tests/test_gas_acoustic_pipe_independent.py::test_nonlinear_sod_shock_contact_rarefaction_matches_independent_exact_riemann_solution
tests/test_gas_acoustic_pipe_independent.py::test_stationary_adiabatic_wall_friction_preserves_total_energy_and_heats_internal_energy
tests/test_gas_acoustic_pipe_independent.py::test_uniform_wall_heating_matches_exact_constant_density_exponential_energy_balance
tests/test_gas_acoustic_pipe_independent.py::test_uniform_gravity_acceleration_changes_total_energy_by_force_work_only
tests/test_gas_acoustic_pipe_independent.py::test_negative_energy_reference_preserves_identical_thermodynamic_evolution
tests/test_gas_acoustic_pipe_independent.py::test_boundary_closure_event_is_hit_exactly_and_reflective_wall_has_zero_mass_energy_flux
tests/test_gas_acoustic_pipe_independent.py::test_boundary_event_at_final_time_is_recorded_without_an_extra_step
tests/test_gas_acoustic_pipe_independent.py::test_snapshot_at_boundary_event_is_continuous_state_before_new_ghost_evolution
```

## 77. `gas.dilute_binary_molecular_diffusivity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reviewed dilute binary-gas kinetic diffusivity and molar-frame Fick flux.

**공개 API**

- [`pipeline_integrity.binary_gas_diffusion.evaluate_binary_gas_diffusivity`](../src/pipeline_integrity/binary_gas_diffusion.py#L30)
- [`pipeline_integrity.binary_gas_diffusion.evaluate_binary_molar_fick`](../src/pipeline_integrity/binary_gas_diffusion.py#L66)

**검증 영역:** Dilute ideal binary gas molecular transport with supplied Lennard-Jones inputs and collision integral; no collision parameter calibration.

**적용한 기준:** independent_Maxwell_speed_quadrature, Fick_face_mass_budget, temperature_pressure_scaling.

**재현할 시험 선택자**

```text
tests/test_binary_gas_diffusion_independent.py::test_relative_maxwell_speed_quadrature
tests/test_binary_gas_diffusion_independent.py::test_species_interchange
tests/test_binary_gas_diffusion_independent.py::test_pressure_and_temperature_conditioned_on_same_supplied_omega
tests/test_binary_gas_diffusion_independent.py::test_collision_diameter_scaling
tests/test_binary_gas_diffusion_independent.py::test_omega_scaling
tests/test_binary_gas_diffusion_independent.py::test_reduced_temperature_geometric_mixing
tests/test_binary_gas_diffusion_independent.py::test_independent_legacy_unit_conversion_rounded_coefficient
tests/test_binary_gas_diffusion_independent.py::test_fick_relative_face_transfer_and_binary_mole_budget
```

## 78. `gas.empirical_capacity_alternatives`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- A selected diameter root around the existing fixed-flow gas path.
- Serial gas-path pressure/total-energy coupling using owner friction closure.
- Typed near-ambient Spitzglass-low flow using the existing external kernel.
- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.gas_path_sizing.evaluate_gas_path_diameter`](../src/pipeline_integrity/gas_path_sizing.py#L52)
- [`pipeline_integrity.gas_potential_network.evaluate_gas_potential_path`](../src/pipeline_integrity/gas_potential_network.py#L135)
- [`pipeline_integrity.low_pressure_gas.evaluate_low_pressure_spitzglass`](../src/pipeline_integrity/low_pressure_gas.py#L28)
- [`pipeline_integrity.models.hydraulics.gas_correlation`](../src/pipeline_integrity/models/hydraulics.py#L209)

**검증 영역:** Selected supported gas-path diameter inverse using the conservative momentum/energy alternative; empirical Panhandle/Spitzglass branches remain outside this profile.

**적용한 기준:** independent_continuous_thermal_momentum_ODE, geometry_inverse, mesh_convergence.

**재현할 시험 선택자**

```text
tests/test_gas_path_diameter_individual.py::test_diameter_for_independent_continuum_target_converges_to_known_geometry
tests/test_gas_path_diameter_individual.py::test_selected_root_recovers_the_forward_discrete_solution_and_endpoint_root
```

## 79. `gas.empirical_panhandle_a_flat`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.models.hydraulics.gas_correlation`](../src/pipeline_integrity/models/hydraulics.py#L209)

## 80. `gas.empirical_panhandle_b_flat`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.models.hydraulics.gas_correlation`](../src/pipeline_integrity/models/hydraulics.py#L209)

## 81. `gas.empirical_weymouth_flat`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.models.hydraulics.gas_correlation`](../src/pipeline_integrity/models/hydraulics.py#L209)

## 82. `gas.ideal_reversible_expansion_work`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Pressure-test loads, signed gas inventories and source-preserving laboratory histories.

**공개 API**

- [`pipeline_integrity.pressure_tests.evaluate_pressure_test`](../src/pipeline_integrity/pressure_tests.py#L86)

**검증 영역:** Closed ideal-gas reversible polytropic expansion with supplied constant exponent; not irreversible discharge energy.

**적용한 기준:** independent_boundary_work_integral, pressure_volume_budget, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_gas_expansion_energy_physical_qualification.py::test_polytropic_final_volume_and_boundary_work_match_closed_form
tests/test_gas_expansion_energy_physical_qualification.py::test_pressure_scaling_changes_work_but_preserves_volume_ratio
tests/test_gas_expansion_energy_physical_qualification.py::test_initial_volume_scaling_is_linear_in_final_volume_and_work
tests/test_gas_expansion_energy_physical_qualification.py::test_work_matches_integrated_polytropic_boundary_expression
```

## 83. `gas.isentropic_nozzle`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Quasi-steady ideal-gas restriction coupled to a pipe acoustic boundary.
- Ideal nozzle pressure inversion and explicitly referenced gas opening flow.
- Curated library calls with declared operating and coefficient conventions.

**공개 API**

- [`pipeline_integrity.gas_restricted_vent.ideal_vent_capacity`](../src/pipeline_integrity/gas_restricted_vent.py#L33)
- [`pipeline_integrity.ideal_gas_opening_observer.invert_sonic_throat_pressure`](../src/pipeline_integrity/ideal_gas_opening_observer.py#L129)
- [`pipeline_integrity.ideal_gas_opening_observer.observe_ideal_opening_flow`](../src/pipeline_integrity/ideal_gas_opening_observer.py#L157)
- [`pipeline_integrity.models.library_models.relief_gas`](../src/pipeline_integrity/models/library_models.py#L177)

**검증 영역:** Steady ideal-gas isentropic nozzle sizing with caller-supplied discharge factors and declared library coefficient precision.

**적용한 기준:** independent_enthalpy_flux_integral, critical_pressure_event, choked_plateau, subcritical_monotonicity.

**재현할 시험 선택자**

```text
tests/test_gas_relief_nozzle_individual_reference.py::test_existing_relief_area_matches_independent_ideal_nozzle_mass_flux
tests/test_gas_relief_nozzle_individual_reference.py::test_k_dependent_critical_event_is_checked_from_each_side
tests/test_gas_relief_nozzle_individual_reference.py::test_choked_plateau_and_subcritical_backpressure_monotonicity
```

## 84. `gas.low_pressure_spitzglass`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Typed near-ambient Spitzglass-low flow using the existing external kernel.

**공개 API**

- [`pipeline_integrity.low_pressure_gas.evaluate_low_pressure_spitzglass`](../src/pipeline_integrity/low_pressure_gas.py#L28)

## 85. `gas.panhandle_network_equivalence`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Series/parallel reduction of explicitly frozen pipe power-law coefficients.

**공개 API**

- [`pipeline_integrity.pipe_resistance_reduction.reduce_pipe_resistances`](../src/pipeline_integrity/pipe_resistance_reduction.py#L64)

**검증 영역:** Frozen caller-reviewed fractional-exponent resistance coordinate reductions, including series common flow and parallel common potential; no Panhandle empirical capacity calibration.

**적용한 기준:** independent_decimal_constitutive_sums, noninteger_continuity, branch_potential_equality.

**재현할 시험 선택자**

```text
tests/test_frozen_fractional_resistance_independent.py::test_series_common_flow_loss_with_fractional_geometry
tests/test_frozen_fractional_resistance_independent.py::test_parallel_noninteger_continuity_and_each_branch_potential
```

## 86. `gas.prescribed_junction_mass_enthalpy_boundary`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady, completely mixed gas plenum with a signed external mass boundary.

**공개 API**

- [`pipeline_integrity.prescribed_gas_junction.solve_prescribed_gas_junction`](../src/pipeline_integrity/prescribed_gas_junction.py#L114)

**검증 영역:** Prescribed-flow steady gas junction with compatible property and enthalpy bases; no unresolved transient node storage.

**적용한 기준:** independent_caloric_resistor_balance, component_mass_ledger, coupled_thermal_resistance, branch_reversal, EOS_energy_integrals.

**재현할 시험 선택자**

```text
tests/test_prescribed_gas_junction_independent.py::test_signed_external_flow_analytic_resistor_and_caloric_balance
tests/test_prescribed_gas_junction_independent.py::test_two_species_mass_inventory_and_caloric_energy
tests/test_prescribed_gas_junction_independent.py::test_coupled_pressure_temperature_against_eliminated_thermal_resistance
tests/test_prescribed_gas_junction_independent.py::test_enthalpy_reference_shift_leaves_pressure_temperature_unchanged
tests/test_prescribed_gas_junction_independent.py::test_solver_resolution_and_branch_reversal_event
tests/test_prescribed_gas_junction_independent.py::test_zero_flow_branch_does_not_impose_incoming_temperature
tests/test_prescribed_gas_junction_independent.py::test_zero_throughflow_has_no_identified_steady_temperature
tests/test_prescribed_gas_junction_independent.py::test_native_nitrogen_against_independent_cp_volume_derivative_energy_integrals
```

## 87. `gas.receiver_start_inventory_size`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit wet/dry reference-volume conversion by conservation of dry moles.
- Rigid gas receiver sizing from reviewed endpoint density inventories.
- Observed signed gas inventory loss from explicit endpoint P/T states.
- Reference liquid volume from a reviewed volumetric engineering-strain curve.
- Thermal and inventory budgets with explicit boundary states.
- State-consistent usable tank inventory assembled from existing owners.

**공개 API**

- [`pipeline_integrity.gas_basis.evaluate_gas_basis`](../src/pipeline_integrity/gas_basis.py#L212)
- [`pipeline_integrity.gas_receiver_sizing.size_gas_receiver`](../src/pipeline_integrity/gas_receiver_sizing.py#L49)
- [`pipeline_integrity.isolated_gas_inventory_loss.observe_isolated_gas_inventory_loss`](../src/pipeline_integrity/isolated_gas_inventory_loss.py#L54)
- [`pipeline_integrity.liquid_reference_volume.evaluate_liquid_reference_volume`](../src/pipeline_integrity/liquid_reference_volume.py#L26)
- [`pipeline_integrity.models.thermal.inventory`](../src/pipeline_integrity/models/thermal.py#L20)
- [`pipeline_integrity.models.thermal.segmented_inventory`](../src/pipeline_integrity/models/thermal.py#L60)
- [`pipeline_integrity.tank_usable_inventory.evaluate_tank_usable_inventory`](../src/pipeline_integrity/tank_usable_inventory.py#L69)

**검증 영역:** Rigid uniform gas receiver endpoint mass capacity under supplied reference demand and endpoint temperatures; no transient heat path or pressure rating selection.

**적용한 기준:** ideal_inventory_reference, endpoint_mass_budget, reference_basis_invariance, actual_air_EOS_budget.

**재현할 시험 선택자**

```text
tests/test_gas_receiver_sizing_individual.py::test_independent_isothermal_pressure_ratio_sizing
tests/test_gas_receiver_sizing_individual.py::test_declared_different_endpoint_temperatures
tests/test_gas_receiver_sizing_individual.py::test_demand_extensivity
tests/test_gas_receiver_sizing_individual.py::test_gas_reference_volume_basis_change_preserves_required_mass_and_vessel
tests/test_gas_receiver_sizing_individual.py::test_ideal_gas_constant_cancels_only_with_same_composition_reference
tests/test_gas_receiver_sizing_individual.py::test_zero_demand
tests/test_gas_receiver_sizing_individual.py::test_actual_air_eos_endpoint_inventory_budget
```

## 88. `gas.squared_pressure_mean`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Absolute pressure integral mean for a declared linear-squared-pressure profile.

**공개 API**

- [`pipeline_integrity.gas_profile_average.evaluate_squared_pressure_profile_mean`](../src/pipeline_integrity/gas_profile_average.py#L21)

**검증 영역:** Spatial mean of a declared linear squared-absolute-pressure profile.

**적용한 기준:** independent_profile_quadrature, symmetry, homogeneity, uniform_limit.

**재현할 시험 선택자**

```text
tests/test_squared_pressure_profile_mean_individual.py::test_mean_matches_independent_linear_squared_pressure_profile_quadrature
tests/test_squared_pressure_profile_mean_individual.py::test_profile_mean_is_symmetric_and_homogeneous_in_absolute_pressure
tests/test_squared_pressure_profile_mean_individual.py::test_uniform_profile_returns_exact_endpoint_across_wide_finite_scales
```

## 89. `gas.steady_pipe_total_energy_path`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- A selected diameter root around the existing fixed-flow gas path.
- Serial gas-path pressure/total-energy coupling using owner friction closure.

**공개 API**

- [`pipeline_integrity.gas_path_sizing.evaluate_gas_path_diameter`](../src/pipeline_integrity/gas_path_sizing.py#L52)
- [`pipeline_integrity.gas_potential_network.evaluate_gas_potential_path`](../src/pipeline_integrity/gas_potential_network.py#L135)

**검증 영역:** Single-phase steady gas path with signed elevation, acceleration, prescribed heat and supported friction; no empirical network-capacity law qualification.

**적용한 기준:** independent_continuous_momentum_energy_ODE, mesh_refinement, signed_height, low_Mach_reduction.

**재현할 시험 선택자**

```text
tests/test_elevated_general_gas_independent.py::test_signed_height_momentum_energy_against_continuous_reference_with_refinement
tests/test_elevated_general_gas_independent.py::test_exponential_source_reduction_matches_independent_low_mach_ode
```

## 90. `ground_movement_pipe_response`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed nodal ground movement around the existing linear PyNite frame owner.

**공개 API**

- [`pipeline_integrity.soil_pipe.evaluate_soil_pipe`](../src/pipeline_integrity/soil_pipe.py#L208)

**검증 영역:** Linear straight beam on distributed elastic springs under prescribed ground shape; no nonlinear soil constitutive prediction.

**적용한 기준:** independent_fourth_order_foundation_solution, displacement_mesh_convergence, reaction_mesh_convergence.

**재현할 시험 선택자**

```text
tests/test_prescribed_ground_profile_independent.py::test_prescribed_ground_profile_converges_in_displacement_and_support_reaction
```

## 91. `hydraulics.circular_pipe_friction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.models.hydraulics.liquid_head_loss`](../src/pipeline_integrity/models/hydraulics.py#L114)
- [`pipeline_integrity.models.hydraulics.liquid_pressure_loss`](../src/pipeline_integrity/models/hydraulics.py#L139)

**검증 영역:** Laminar Stokes pressure-work reference and declared Darcy/Colebrook arithmetic. The separate measured turbulent-rig acceptance miss remains open.

**적용한 기준:** stokes_pressure_work, independent_colebrook_root, minor_loss_scaling, geometric_similarity.

**재현할 시험 선택자**

```text
tests/test_liquid_pressure_loss_physical_qualification.py::test_catalog_laminar_pressure_work_matches_stokes_darcy_balance
tests/test_hydraulics_liquid_head_loss_physical_qualification.py::test_catalog_head_loss_matches_independent_darcy_and_colebrook
tests/test_hydraulics_liquid_head_loss_physical_qualification.py::test_laminar_darcy_head_is_linear_in_flow_and_minor_loss_is_quadratic
tests/test_hydraulics_liquid_head_loss_physical_qualification.py::test_coherent_geometric_and_viscosity_scaling_preserves_head_loss
```

## 92. `hydraulics.concentric_annulus_loss`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady concentric-annulus loss with explicitly distinct constitutive choices.

**공개 API**

- [`pipeline_integrity.annular_flow.evaluate_concentric_annulus_loss`](../src/pipeline_integrity/annular_flow.py#L156)

**검증 영역:** Steady Newtonian laminar concentric annulus with no-slip walls; supplied Darcy alternative is separate from laminar derivation.

**적용한 기준:** independent_radial_Poisson_BVP, wall_work_dissipation, thin_slit_convergence, circular_limit, high_precision_thin_gap.

**재현할 시험 선택자**

```text
tests/test_concentric_annulus_independent.py::test_actual_annular_flow_and_wall_shears_match_independent_radial_poisson_bvp
tests/test_concentric_annulus_independent.py::test_thin_gap_converges_to_independent_plane_slit_pressure_and_parabola
tests/test_concentric_annulus_independent.py::test_vanishing_inner_rod_approaches_circular_poiseuille_limit_without_replacing_annular_area
tests/test_concentric_annulus_independent.py::test_thin_conductance_and_next_float_inside_wall_match_ninety_six_digit_reference
tests/test_concentric_annulus_independent.py::test_supplied_darcy_uses_actual_mean_velocity_and_native_pressure_loss
tests/test_concentric_annulus_independent.py::test_zero_flow_or_length_preserves_zero_pressure_work
tests/test_concentric_annulus_independent.py::test_sample_order_immutability_and_out_of_range_arithmetic_remain_explicit
```

## 93. `hydraulics.hazen_williams_water`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Constant-density hydraulic grade along an explicitly surveyed series path.

**공개 API**

- [`pipeline_integrity.hydraulic_grade.evaluate_hazen_williams`](../src/pipeline_integrity/hydraulic_grade.py#L23)

## 94. `hydraulics.hydraulic_grade_profile`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Constant-density hydraulic grade along an explicitly surveyed series path.
- Steady pump curves and common-flow head balances with explicit branch support.

**공개 API**

- [`pipeline_integrity.hydraulic_grade.evaluate_hydraulic_grade`](../src/pipeline_integrity/hydraulic_grade.py#L96)
- [`pipeline_integrity.models.pump_curves.solve_pump_station_path`](../src/pipeline_integrity/models/pump_curves.py#L219)

**검증 영역:** Steady prescribed pump stations and common-flow pipe head balances with supported curves.

**적용한 기준:** independent_station_assembly_root, serial_head_budget, datum_invariance, station_pressure_order, partition_invariance.

**재현할 시험 선택자**

```text
tests/test_pump_station_path_individual.py::test_nonidentical_series_parallel_path_matches_exact_root_and_station_pressures
tests/test_pump_station_path_individual.py::test_elevation_datum_does_not_change_pressure_or_flow
tests/test_pump_station_path_individual.py::test_density_pressure_similarity_preserves_common_flow_and_scales_power
tests/test_pump_station_path_individual.py::test_station_and_pipe_order_preserve_flow_but_change_intermediate_pressure
tests/test_pump_station_path_individual.py::test_serial_pipe_partition_and_branch_permutation_do_not_change_root
tests/test_pump_station_path_individual.py::test_caller_pressure_thresholds_are_visible_without_claiming_cavitation_acceptance
```

## 95. `hydraulics.nonisothermal_viscous_pipe`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Steady liquid heating/viscosity/loss coupling through existing model owners.
- Single-liquid coolant flow from a declared heat duty and enthalpy rise.

**공개 API**

- [`pipeline_integrity.heated_transport.evaluate_heated_transport`](../src/pipeline_integrity/heated_transport.py#L369)
- [`pipeline_integrity.liquid_heat_duty.size_liquid_heat_duty_flow`](../src/pipeline_integrity/liquid_heat_duty.py#L43)

**검증 영역:** Single-phase constant-density liquid march with supplied temperature-dependent viscosity and reviewed thermal boundary; no phase change or unknown rheology.

**적용한 기준:** independent_logarithmic_pressure_solution, coupled_temperature_pressure_ODE, mesh_convergence, gravity_internal_energy_balance.

**재현할 시험 선택자**

```text
tests/test_heated_variable_viscosity_individual.py::test_variable_laminar_adiabatic_friction_has_independent_logarithmic_solution
tests/test_heated_variable_viscosity_individual.py::test_temperature_viscosity_pressure_coupling_converges_to_independent_ode
tests/test_heated_variable_viscosity_individual.py::test_elevation_changes_pressure_without_spurious_gravity_heating
tests/test_heated_variable_viscosity_individual.py::test_viscosity_uses_reviewed_kelvin_difference_and_real_pressure_work_enthalpy
```

## 96. `hydraulics.relief_system_total_resistance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-terminal series/parallel liquid paths built from existing loss primitives.
- Total-resistance capacity from an explicitly supplied expansion curve.
- Calibrated net-expansion relief capacity and a distinct reviewed Fanno branch.

**공개 API**

- [`pipeline_integrity.liquid_path_system.evaluate_liquid_path_system`](../src/pipeline_integrity/liquid_path_system.py#L163)
- [`pipeline_integrity.relief_expansion_curve.observe_curve_driven_relief_capacity`](../src/pipeline_integrity/relief_expansion_curve.py#L62)
- [`pipeline_integrity.relief_line_capacity.evaluate_calibrated_relief_line`](../src/pipeline_integrity/relief_line_capacity.py#L70)
- [`pipeline_integrity.relief_line_capacity.evaluate_fanno_relief_line`](../src/pipeline_integrity/relief_line_capacity.py#L182)

**검증 영역:** Reviewed adiabatic constant-area ideal-gas Fanno relief branch with supplied friction and area; empirical expansion-curve calibration is outside this profile.

**적용한 기준:** independent_Fanno_quadrature, PT_velocity_momentum_energy_ODE, sonic_event, friction_zero_limit, midpoint_refinement.

**재현할 시험 선택자**

```text
tests/test_relief_line_capacity_individual.py::test_Fanno_integral_against_independent_positive_differential_quadrature
tests/test_relief_line_capacity_individual.py::test_subcritical_capacity_against_independent_P_T_velocity_momentum_energy_ODE
tests/test_relief_line_capacity_individual.py::test_choked_profile_conserves_mass_energy_and_loses_stagnation_pressure
tests/test_relief_line_capacity_individual.py::test_sonic_event_is_continuous_and_lower_back_pressure_does_not_increase_capacity
tests/test_relief_line_capacity_individual.py::test_zero_distributed_friction_reuses_existing_ideal_nozzle_limit
tests/test_relief_line_capacity_individual.py::test_friction_to_zero_and_low_Mach_asymptotic_limits
tests/test_relief_line_capacity_individual.py::test_pressure_area_similarity_and_profile_sampling_do_not_add_new_losses
tests/test_relief_line_capacity_individual.py::test_independent_explicit_midpoint_refinement_converges_to_endpoint
```

## 97. `hydraulics.series_local_resistance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-terminal series/parallel liquid paths built from existing loss primitives.
- Pointwise series reduction with explicit local-velocity and water-Cv bases.

**공개 API**

- [`pipeline_integrity.liquid_path_system.evaluate_liquid_path_system`](../src/pipeline_integrity/liquid_path_system.py#L163)
- [`pipeline_integrity.liquid_resistance_coordinates.reduce_liquid_resistance_coordinates`](../src/pipeline_integrity/liquid_resistance_coordinates.py#L69)

**검증 영역:** Known local-loss/Cv components reduced using explicit reference velocities, areas and fluid states.

**적용한 기준:** independent_pressure_work, reference_velocity_ODE, unit_definition, coordinate_invariance.

**재현할 시험 선택자**

```text
tests/test_liquid_resistance_coordinates_independent.py::test_distinct_reference_velocities_pressure_ode_and_energy
tests/test_liquid_resistance_coordinates_independent.py::test_water_cv_definition_series_pressure_and_reference_reexpression
tests/test_liquid_resistance_coordinates_independent.py::test_mixed_cv_k_and_actual_basis_roundtrip_without_assumed_water_density
tests/test_liquid_resistance_coordinates_independent.py::test_diameter_reference_transform_preserves_work_and_single_component_limit
tests/test_liquid_resistance_coordinates_independent.py::test_same_bore_sum_and_reordering_are_coordinate_invariant
tests/test_liquid_resistance_coordinates_independent.py::test_review_flow_direction_and_actual_state_guards
```

## 98. `hydraulics.series_parallel_network`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-terminal series/parallel liquid paths built from existing loss primitives.

**공개 API**

- [`pipeline_integrity.liquid_path_system.evaluate_liquid_path_system`](../src/pipeline_integrity/liquid_path_system.py#L163)

**검증 영역:** Two-terminal supported parallel liquid paths with actual diameters and declared common boundary type.

**적용한 기준:** independent_laminar_fields, branch_pressure_flow_conservation, turbulent_friction_roots.

**재현할 시험 선택자**

```text
tests/test_liquid_path_system_independent.py::test_parallel_pressure_boundary_matches_independent_laminar_column_fields
tests/test_liquid_path_system_independent.py::test_parallel_total_flow_inverse_uses_common_pressure_and_conserves_flow
tests/test_liquid_path_system_independent.py::test_parallel_static_pressure_inverse_uses_common_endpoint_area_not_branch_area
tests/test_liquid_path_system_independent.py::test_native_turbulent_colebrook_parallel_paths_match_independent_friction_roots
tests/test_liquid_path_system_independent.py::test_parallel_pointwise_equivalence_matches_independent_conductance
```

## 99. `hydraulics.supplied_drag_reduction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional resistance-change observations under explicit flow/head/power bounds.

**공개 API**

- [`pipeline_integrity.drag_reduction_observer.evaluate_drag_reduction`](../src/pipeline_integrity/drag_reduction_observer.py#L63)

**검증 영역:** Caller-supplied same-flow drag reduction and explicitly reviewed projection law; no additive chemistry or extrapolation calibration.

**적용한 기준:** independent_distributed_friction_equilibrium, pressure_work, signed_same_flow_measurement, fixed_head_power_distinction.

**재현할 시험 선택자**

```text
tests/test_drag_reduction_independent.py::test_independent_distributed_friction_equilibrium_and_work
tests/test_drag_reduction_independent.py::test_measured_same_flow_drop_defines_signed_reduction_without_chemistry
tests/test_drag_reduction_independent.py::test_fixed_head_and_power_limits_are_different_and_pump_limits_do_not_clip
tests/test_drag_reduction_independent.py::test_same_flow_measurement_does_not_prove_extrapolation
```

## 100. `hydrostatics.coated_pipe_effective_weight`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Exact annular coating extension of the existing sealed bare-pipe ledger.

**공개 API**

- [`pipeline_integrity.coated_pipe_weight.evaluate_coated_pipe_weight`](../src/pipeline_integrity/coated_pipe_weight.py#L38)
- [`pipeline_integrity.coated_pipe_weight.size_dense_coating_for_submerged_weight`](../src/pipeline_integrity/coated_pipe_weight.py#L67)

**검증 영역:** Fully submerged empty sealed pipe with uniform coating; no flooding or soil contact.

**적용한 기준:** surface_traction, material_volume, similarity, inverse_ballast.

**재현할 시험 선택자**

```text
tests/test_coated_pipe_weight_individual.py::test_integrated_surface_traction_and_material_volume
tests/test_coated_pipe_weight_individual.py::test_cross_section_similarity
tests/test_coated_pipe_weight_individual.py::test_inverse_independent_annular_ballast_load
tests/test_coated_pipe_weight_individual.py::test_already_met_target_does_not_add_coating
tests/test_coated_pipe_weight_individual.py::test_thick_coating_uses_outer_displacement_not_thin_annulus
```

## 101. `hydrostatics.density_gravity_column`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-pressure density observation for one reviewed uniform static column.
- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.hydrostatic_density_observer.observe_hydrostatic_density`](../src/pipeline_integrity/hydrostatic_density_observer.py#L71)
- [`pipeline_integrity.models.hydraulics.hydrostatic`](../src/pipeline_integrity/models/hydraulics.py#L173)

**검증 영역:** Uniform static liquid density inferred from two vertically separated pressure observations with a common datum.

**적용한 기준:** independent_pressure_ode, weight_traction_equilibrium, gauge_invariance, gravity_scaling.

**재현할 시험 선택자**

```text
tests/test_hydrostatic_density_observer_independent.py::test_two_observations_from_independent_pressure_ode_recover_density
tests/test_hydrostatic_density_observer_independent.py::test_independent_pressure_tractions_balance_column_weight
tests/test_hydrostatic_density_observer_independent.py::test_common_gauge_offset_preserves_density_without_inventing_absolute_datum
tests/test_hydrostatic_density_observer_independent.py::test_vertical_datum_translation_and_gravity_pressure_scaling_preserve_density
```

## 102. `impact.elastic_energy_bound`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Energy-equivalent linear-target deflection bound using the existing oscillator.

**공개 API**

- [`pipeline_integrity.elastic_drop_energy_bound.evaluate_elastic_drop_energy_bound`](../src/pipeline_integrity/elastic_drop_energy_bound.py#L33)

**검증 영역:** Loss-free linear equivalent stiffness drop-energy upper envelope with supplied impact mass and reviewed deflection range; no contact stress history.

**적용한 기준:** independent_energy_root, event_ODE, energy_conservation, zero_gap_limit, mass_clock_scaling.

**재현할 시험 선택자**

```text
tests/test_elastic_drop_energy_bound_independent.py::test_peak_matches_independent_initial_final_energy_root
tests/test_elastic_drop_energy_bound_independent.py::test_event_ode_and_constant_energy_agree_with_equivalent_oscillator_clock
tests/test_elastic_drop_energy_bound_independent.py::test_zero_gap_unpreloaded_sudden_weight_has_two_times_static_reaction
tests/test_elastic_drop_energy_bound_independent.py::test_equivalent_mass_changes_the_clock_but_not_the_energy_bound
```

## 103. `impact_to_fracture_toughness_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Unit-explicit supplied empirical notch and impact-toughness coordinates.

**공개 API**

- [`pipeline_integrity.fracture_property_coordinates.evaluate_normalized_impact_toughness_coordinate`](../src/pipeline_integrity/fracture_property_coordinates.py#L59)

## 104. `instrument.compact_prover_two_clock_factor`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-interval pulse interpolation at an explicitly supplied volume state.

**공개 API**

- [`pipeline_integrity.compact_prover.evaluate_compact_prover`](../src/pipeline_integrity/compact_prover.py#L50)

**검증 영역:** Supplied swept piston volume and pulse timing windows with explicit volume correction; no traceable instrument calibration claim.

**적용한 기준:** independent_piston_pulse_ODE, high_precision_clock_ratio, clock_volume_scaling.

**재현할 시험 선택자**

```text
tests/test_compact_prover_independent.py::test_independent_piston_volume_and_pulse_phase_ode
tests/test_compact_prover_independent.py::test_high_precision_clock_ratio_and_units
tests/test_compact_prover_independent.py::test_equal_clock_scaling_and_volume_scaling
tests/test_compact_prover_independent.py::test_nominal_factor_is_optional_and_orientation_is_explicit
```

## 105. `instrument.vibrating_density_calibration`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Evaluate an explicitly supplied, instrument-specific period calibration.

**공개 API**

- [`pipeline_integrity.vibrating_density.observe_vibrating_element_density`](../src/pipeline_integrity/vibrating_density.py#L61)

**검증 영역:** Caller-supplied dimensioned period-density calibration checked against a synthetic loaded oscillator; no sensor accuracy claim.

**적용한 기준:** independent_spring_mass_ODE, oscillator_energy, high_precision_polynomial, coefficient_unit_conversion.

**재현할 시험 선택자**

```text
tests/test_vibrating_density_independent.py::test_independent_loaded_spring_mass_ode_period_and_energy
tests/test_vibrating_density_independent.py::test_dimensioned_polynomial_against_high_precision
tests/test_vibrating_density_independent.py::test_millisecond_coefficient_conversion_is_explicit_si_data
```

## 106. `inventory.prescribed_rainfall_drainage`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed rainfall and drain-capacity depth inventory with a dry boundary.

**공개 API**

- [`pipeline_integrity.rainfall_inventory.evaluate_rainfall_inventory`](../src/pipeline_integrity/rainfall_inventory.py#L32)

**검증 영역:** Fixed plan area prescribed rain and drainage with empty-inventory constraint; no rainfall forecast.

**적용한 기준:** switched_ode, mass_budget, dry_event, partition_invariance.

**재현할 시험 선택자**

```text
tests/test_rainfall_inventory_independent.py::test_switched_ode_with_dry_event
tests/test_rainfall_inventory_independent.py::test_volume_mass_pressure_and_actual_drain_scaling
tests/test_rainfall_inventory_independent.py::test_known_interior_dry_event_and_capacity_is_not_outflow
tests/test_rainfall_inventory_independent.py::test_dry_roof_rain_passes_through_without_storage
tests/test_rainfall_inventory_independent.py::test_interval_partition_invariance_and_dry_wet_events
```

## 107. `leak.growing_tank_hole_integral`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed through-wall opening growth and outward liquid inventory loss.

**공개 API**

- [`pipeline_integrity.growing_pit_leak.evaluate_growing_pit_leak`](../src/pipeline_integrity/growing_pit_leak.py#L67)

**검증 영역:** Initially penetrating spherical pit, prescribed constant head and supplied discharge coefficient; no evolving tank-level or coefficient calibration claim.

**적용한 기준:** independent_geometry_time_integral, mass_volume_budget, time_growth_similarity.

**재현할 시험 선택자**

```text
tests/test_physical_similarity_223.py::test_growing_opening_time_similarity_and_exact_volume
```

## 108. `leak.incompressible_small_hole`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional incompressible release through an actual stationary opening.

**공개 API**

- [`pipeline_integrity.liquid_orifice.evaluate_liquid_opening_release`](../src/pipeline_integrity/liquid_orifice.py#L56)

**검증 영역:** Steady incompressible declared opening driven by positive pressure head with supplied discharge coefficient; no coefficient calibration.

**적용한 기준:** independent_parcel_acceleration_ODE, pressure_power, geometry_density_similarity.

**재현할 시험 선택자**

```text
tests/test_liquid_opening_release_independent.py::test_ideal_parcel_pressure_ode_and_power
tests/test_liquid_opening_release_independent.py::test_supplied_coefficient_decimal_head_reference
tests/test_liquid_opening_release_independent.py::test_area_pressure_density_and_coefficient_similarity
tests/test_liquid_opening_release_independent.py::test_zero_area_is_closed_not_an_invented_jet
tests/test_liquid_opening_release_independent.py::test_zero_head_and_steady_duration_contract
```

## 109. `leak.supplied_small_discrepancy_location`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local leak-position reductions and acoustic/cable input transformations.

**공개 API**

- [`pipeline_integrity.leak_inverse_relations.locate_small_leak_from_discrepancies`](../src/pipeline_integrity/leak_inverse_relations.py#L32)

**검증 영역:** Small observed discrepancy position in a prescribed linear hydraulic-resistance split; no leak diagnosis or general network inverse.

**적용한 기준:** independent_branch_pressure_balance, small_leak_position.

**재현할 시험 선택자**

```text
tests/test_leak_inverse_relations_independent.py::test_discrepancy_from_independent_linear_hydraulic_resistances
```

## 110. `liquid.declared_saturated_mixed_circular_flow`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conservative wet/filled circular flow with explicit saturation-pressure closure.

**공개 API**

- [`pipeline_integrity.saturated_mixed_pipe.solve_saturated_mixed_pipe`](../src/pipeline_integrity/saturated_mixed_pipe.py#L83)

**검증 영역:** Reviewed one-dimensional barotropic saturated mixed circular pipe with supported filled/partial states and declared vapor model; no general two-fluid phase kinetics.

**적용한 기준:** independent_nonlinear_Riemann_reference, mesh_convergence, acoustic_travel, phase_boundary_event, mass_observation_clock.

**재현할 시험 선택자**

```text
tests/test_saturated_mixed_pipe_independent.py::test_uniform_wet_or_filled_signed_translation
tests/test_saturated_mixed_pipe_independent.py::test_resting_surface_crosses_crown_over_bed_without_numerical_motion
tests/test_saturated_mixed_pipe_independent.py::test_filled_negative_gauge_is_distinct_from_massless_vapor_surface
tests/test_saturated_mixed_pipe_independent.py::test_nonlinear_filled_to_partial_riemann_reference_and_mesh_convergence
tests/test_saturated_mixed_pipe_independent.py::test_boundary_switch_creates_moving_partial_region_with_exact_observation_clock
tests/test_saturated_mixed_pipe_independent.py::test_diverging_filled_columns_create_finite_vapor_region_with_rarefaction_reference
tests/test_saturated_mixed_pipe_independent.py::test_pressurized_acoustic_travel_and_spatial_refinement
```

## 111. `liquid.fixed_inertance_column_dynamics`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two incident liquid acoustic states and a separate constant-head return model.
- Fixed-inertance liquid column against one closed polytropic gas pocket.

**공개 API**

- [`pipeline_integrity.liquid_column_collision.evaluate_constant_head_column_return`](../src/pipeline_integrity/liquid_column_collision.py#L145)
- [`pipeline_integrity.trapped_gas_column.evaluate_trapped_gas_column`](../src/pipeline_integrity/trapped_gas_column.py#L63)

**검증 영역:** Constant imposed head reversal and return of a fixed-inertance liquid column; trapped polytropic gas pocket variant remains outside this profile.

**적용한 기준:** independent_event_ODE, reversal_return_time, pressure_work.

**재현할 시험 선택자**

```text
tests/test_liquid_column_collision_independent.py::test_constant_head_reversal_return_and_work_against_independent_event_ode
```

## 112. `liquid.homogeneous_mixture_compliance_celerity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Homogeneous frozen-phase liquid/gas acoustics with elastic-wall storage.

**공개 API**

- [`pipeline_integrity.mixture_wave_compliance.evaluate_mixture_wave_speed`](../src/pipeline_integrity/mixture_wave_compliance.py#L46)

**검증 영역:** Homogeneous frozen-phase liquid/gas acoustic tangent with supplied gas polytropic law and elastic wall storage; no phase-slip or mass transfer.

**적용한 기준:** independent_phase_volume_derivative, polytropic_acoustic_limit, single_liquid_limit, similarity.

**재현할 시험 선택자**

```text
tests/test_mixture_wave_compliance_individual.py::test_phase_volume_work_tangent_matches_combined_wave_compliance
tests/test_mixture_wave_compliance_individual.py::test_all_gas_rigid_wall_limit_is_polytropic_acoustic_speed
tests/test_mixture_wave_compliance_individual.py::test_zero_gas_exactly_reuses_the_existing_single_liquid_owner_without_gas_data
tests/test_mixture_wave_compliance_individual.py::test_pressure_density_and_modulus_scale_preserves_acoustic_speed
tests/test_mixture_wave_compliance_individual.py::test_geometric_scale_and_rigid_wall_bounds
tests/test_mixture_wave_compliance_individual.py::test_density_and_compressibility_competition_has_an_interior_speed_minimum
tests/test_mixture_wave_compliance_individual.py::test_input_support_and_applicability_reviews_are_required
```

## 113. `liquid.linear_acoustic_characteristics`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local leak-position reductions and acoustic/cable input transformations.
- Two incident liquid acoustic states and a separate constant-head return model.

**공개 API**

- [`pipeline_integrity.leak_inverse_relations.combine_directional_acoustic_signal`](../src/pipeline_integrity/leak_inverse_relations.py#L135)
- [`pipeline_integrity.liquid_column_collision.evaluate_liquid_column_collision`](../src/pipeline_integrity/liquid_column_collision.py#L62)

**검증 영역:** Two incident linear acoustic states in supported liquid media and their junction star state; no phase change.

**적용한 기준:** independent_acoustic_Riemann_solution, unequal_impedance_energy, pressure_datum_invariance.

**재현할 시험 선택자**

```text
tests/test_liquid_column_collision_independent.py::test_equal_medium_signed_approach_has_half_relative_joukowsky_increment
tests/test_liquid_column_collision_independent.py::test_unequal_impedance_two_state_riemann_solution_and_independent_energy
tests/test_liquid_column_collision_independent.py::test_stationary_liquid_is_not_a_rigid_wall_and_coherent_force_units_match_SI
tests/test_liquid_column_collision_independent.py::test_reference_datum_change_does_not_change_physical_interface_state
tests/test_liquid_column_collision_independent.py::test_two_actual_moc_incident_waves_meet_at_junction_with_the_same_star_state
```

## 114. `liquid.linear_acoustic_pipe_field`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Current-state quasi-steady wall resistance for the liquid acoustic owner.
- Explicit inertia-coordinate adapter for uniform liquid kinetic correction.
- Liquid acoustic characteristics on pipe graphs with explicit head/withdrawal boundaries.
- Reuse native actuator kinematics with independently supplied liquid conductance data.

**공개 API**

- [`pipeline_integrity.liquid_moc_friction.LiquidMOCQuasisteadyDarcy.__call__`](../src/pipeline_integrity/liquid_moc_friction.py#L57)
- [`pipeline_integrity.liquid_virtual_mass.simulate_virtual_mass_liquid`](../src/pipeline_integrity/liquid_virtual_mass.py#L54)
- [`pipeline_integrity.transient.simulate_liquid_transient`](../src/pipeline_integrity/transient.py#L175)
- [`pipeline_integrity.valve_motion.simulate_valve_motion`](../src/pipeline_integrity/valve_motion.py#L99)

**검증 영역:** Frictionless single-phase linear liquid MOC, ideal reservoir/closed boundaries and lossless interfaces; other model variants remain outside this profile.

**적용한 기준:** joukowsky_pressure, arrival_time, junction_continuity, impedance_reflection_transmission, wave_superposition.

**재현할 시험 선택자**

```text
tests/test_liquid_moc_physical_qualification.py::test_closed_end_first_arrival_matches_joukowsky_head_and_distance_over_speed
tests/test_liquid_moc_physical_qualification.py::test_pressure_increment_matches_density_gravity_times_joukowsky_head
tests/test_moc_impedance_reflection_hangang.py::test_moc_interface_matches_independent_pressure_reflection_and_transmission
tests/test_moc_simultaneous_wave_superposition_hangang.py::test_simultaneous_head_waves_superpose_by_characteristic_admittance
```

## 115. `liquid.linear_elastic_compliance_celerity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Geometry, steady hydraulic budgets and limited transient screening.

**공개 API**

- [`pipeline_integrity.models.hydraulics.wave_speed`](../src/pipeline_integrity/models/hydraulics.py#L275)

**검증 영역:** Single liquid with linear bulk response and thin elastic pipe under zero axial strain.

**적용한 기준:** independent_Hooke_constraint_system, stored_line_mass_pressure_derivative, linear_wave_speed.

**재현할 시험 선택자**

```text
tests/test_single_liquid_celerity_independent.py::test_celerity_matches_independent_pressure_derivative_of_stored_line_mass
```

## 116. `liquid.prescribed_growing_inventory`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed exponential filling in a constant-area, constant-density pipe.

**공개 API**

- [`pipeline_integrity.prescribed_pipe_fill.evaluate_prescribed_pipe_fill`](../src/pipeline_integrity/prescribed_pipe_fill.py#L35)
- [`pipeline_integrity.prescribed_pipe_fill.time_to_prescribed_fill_position`](../src/pipeline_integrity/prescribed_pipe_fill.py#L83)

**검증 영역:** Prescribed flow growing pipe inventory before arrival; no coupled pipe-network dynamics.

**적용한 기준:** independent_relaxation_ode, arrival_root, momentum_energy, similarity, startup_limit.

**재현할 시험 선택자**

```text
tests/test_prescribed_pipe_fill_individual.py::test_front_mass_momentum_and_energy_match_independent_relaxation_ode
tests/test_prescribed_pipe_fill_individual.py::test_arrival_root_matches_independent_ode_front_crossing
tests/test_prescribed_pipe_fill_individual.py::test_tiny_startup_inventory_keeps_quadratic_volume_instead_of_cancelling_to_zero
tests/test_prescribed_pipe_fill_individual.py::test_density_and_same_velocity_area_flow_scaling_preserve_front
tests/test_prescribed_pipe_fill_individual.py::test_initial_rest_and_prestart_state_do_not_manufacture_a_nonzero_elbow_force
```

## 117. `liquid.supplied_exponential_viscosity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Supplied exponential liquid-viscosity law on a declared temperature interval.

**공개 API**

- [`pipeline_integrity.liquid_viscosity.evaluate_exponential_viscosity`](../src/pipeline_integrity/liquid_viscosity.py#L84)
- [`pipeline_integrity.liquid_viscosity.fit_exponential_viscosity`](../src/pipeline_integrity/liquid_viscosity.py#L45)

**검증 영역:** Supplied exponential viscosity temperature law and bounded two-point fit, with a separate constant-density heated-pipe test provider; no material viscosity calibration.

**적용한 기준:** independent_temperature_ODE, unit_origin_invariance, logarithmic_fit_limits, coupled_continuum_reference.

**재현할 시험 선택자**

```text
tests/test_liquid_viscosity_individual.py::test_constitutive_law_matches_independently_integrated_temperature_ode
tests/test_liquid_viscosity_individual.py::test_two_point_law_is_order_invariant_and_has_geometric_midpoint
tests/test_liquid_viscosity_individual.py::test_kelvin_origin_shift_preserves_temperature_difference_and_curve
tests/test_liquid_viscosity_individual.py::test_dynamic_conversion_requires_real_density_and_preserves_kinematic_basis
tests/test_liquid_viscosity_individual.py::test_mismatched_density_does_not_manufacture_dynamic_viscosity
tests/test_liquid_viscosity_individual.py::test_same_temperature_is_underdetermined_or_conflicting_not_a_zero_slope_fit
tests/test_liquid_viscosity_individual.py::test_constant_and_extreme_calibration_ratios_use_positive_log_coordinates
tests/test_liquid_viscosity_individual.py::test_fitted_law_plugs_into_existing_heated_pipe_against_independent_continuum
```

## 118. `local_pipe_indentation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Separate calibrated force and energy coordinates for local pipe indentation.

**공개 API**

- [`pipeline_integrity.local_indentation_coordinate.evaluate_local_indentation_coordinate`](../src/pipeline_integrity/local_indentation_coordinate.py#L18)

**검증 영역:** Manufactured shallow parabolic punch on a rigid-perfectly-plastic strip for the supplied indentation coordinate laws; actual pipe contact and residual dent are not qualified.

**적용한 기준:** independent_contact_traction_integral, plastic_work_integral, work_force_derivative, zero_limit.

**재현할 시험 선택자**

```text
tests/test_local_indentation_independent.py::test_force_inverse_against_integrated_plastic_contact_traction_and_displacement_work
tests/test_local_indentation_independent.py::test_energy_inverse_and_force_against_independent_plastic_displacement_integral
tests/test_local_indentation_independent.py::test_work_derivative_matches_force_under_symmetric_refinement
tests/test_local_indentation_independent.py::test_stationary_zero_limit_has_no_inferred_residual_dent
```

## 119. `measurement.slick_appearance_thickness`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Volume interval from a supplied appearance-to-thickness calibration.

**공개 API**

- [`pipeline_integrity.slick_observation.evaluate_slick_observation`](../src/pipeline_integrity/slick_observation.py#L26)

**검증 영역:** Volume interval propagation for a caller-supplied appearance/thickness band; no optical appearance calibration.

**적용한 기준:** independent_variable_thickness_surface_integral, volume_bound_enclosure.

**재현할 시험 선택자**

```text
tests/test_remaining_physical_limits_independent.py::test_slick_volume_bounds_enclose_independent_variable_thickness_surface_integral
```

## 120. `mechanical.prescribed_pressure_work_actuation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Ideal pressure-actuation and supplied uniform-shear load observers in SI.

**공개 API**

- [`pipeline_integrity.ideal_actuation_observers.evaluate_ideal_motor`](../src/pipeline_integrity/ideal_actuation_observers.py#L73)
- [`pipeline_integrity.ideal_actuation_observers.evaluate_ideal_piston`](../src/pipeline_integrity/ideal_actuation_observers.py#L48)
- [`pipeline_integrity.ideal_actuation_observers.evaluate_uniform_shear_cut`](../src/pipeline_integrity/ideal_actuation_observers.py#L92)

**검증 영역:** Ideal piston and motor with prescribed chamber pressure and displacement; no friction calibration.

**적용한 기준:** surface_force, pressure_volume_work, rotational_energy.

**재현할 시험 선택자**

```text
tests/test_ideal_actuation_observers_independent.py::test_piston_surface_integral_and_pressure_volume_work
tests/test_ideal_actuation_observers_independent.py::test_incompressible_chamber_volume_change
tests/test_ideal_actuation_observers_independent.py::test_common_absolute_pressure_datum_and_force_projection
tests/test_ideal_actuation_observers_independent.py::test_motor_rotational_work_integral_and_flow_energy
```

## 121. `mechanical.supplied_uniform_shear_cut_load`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Ideal pressure-actuation and supplied uniform-shear load observers in SI.

**공개 API**

- [`pipeline_integrity.ideal_actuation_observers.evaluate_ideal_motor`](../src/pipeline_integrity/ideal_actuation_observers.py#L73)
- [`pipeline_integrity.ideal_actuation_observers.evaluate_ideal_piston`](../src/pipeline_integrity/ideal_actuation_observers.py#L48)
- [`pipeline_integrity.ideal_actuation_observers.evaluate_uniform_shear_cut`](../src/pipeline_integrity/ideal_actuation_observers.py#L92)

**검증 영역:** Supplied uniform shear strength integrated over declared cutting area; no fracture prediction.

**적용한 기준:** independent_area_integral.

**재현할 시험 선택자**

```text
tests/test_ideal_actuation_observers_independent.py::test_uniform_shear_area_integral
```

## 122. `mechanics.key_shear_bearing`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nominal tangential force and average key shear/bearing on supplied engagement.

**공개 API**

- [`pipeline_integrity.key_loads.evaluate_key_loads`](../src/pipeline_integrity/key_loads.py#L23)

**검증 영역:** Nominal key shear and bearing on declared contact areas and supplied torque shares; no local stress concentration.

**적용한 기준:** section_force_moment, independent_contact_areas, torque_partition, geometric_similarity.

**재현할 시험 선택자**

```text
tests/test_key_loads_individual.py::test_contact_integrals_reconstruct_force_and_moment
tests/test_key_loads_individual.py::test_actual_bearing_height_is_independent_of_shear_width
tests/test_key_loads_individual.py::test_unequal_reviewed_key_shares_and_different_contact_areas
tests/test_key_loads_individual.py::test_geometric_cube_torque_similarity
tests/test_key_loads_individual.py::test_load_shares_are_not_automatically_normalized
```

## 123. `mechanics.relief_jet_reaction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed open-port pressure/momentum contributions and optional steady CV force.
- Frozen prescribed liquid-slug port states at an elbow, in a common SI frame.

**공개 API**

- [`pipeline_integrity.port_momentum_load.evaluate_port_momentum_load`](../src/pipeline_integrity/port_momentum_load.py#L29)
- [`pipeline_integrity.slug_elbow_load.evaluate_slug_elbow_load`](../src/pipeline_integrity/slug_elbow_load.py#L31)

**검증 영역:** Known steady flow ports and explicit fluid body force in a complete control volume; no internal transient pressure model.

**적용한 기준:** independent_parcel_impulse, pressure_thrust, momentum_balance, frame_rotation, pressure_datum.

**재현할 시험 선택자**

```text
tests/test_port_momentum_load_individual.py::test_outlet_pressure_plus_momentum_thrust
tests/test_port_momentum_load_individual.py::test_opposed_outlets_cancel_only_their_known_contributions
tests/test_port_momentum_load_individual.py::test_complete_elbow_force_from_independent_parcel_impulse
tests/test_port_momentum_load_individual.py::test_explicit_fluid_body_force_is_transferred_to_hardware
tests/test_port_momentum_load_individual.py::test_uniform_absolute_pressure_reference_shift_does_not_change_thrust
tests/test_port_momentum_load_individual.py::test_orthogonal_frame_rotation_rotates_the_load
tests/test_port_momentum_load_individual.py::test_unbalanced_whole_cv_is_not_a_steady_hardware_force
```

## 124. `meter.supplied_gas_differential_flow`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Gas differential-pressure meters and explicit real-gas reference-volume states.
- Bracketed meter geometry inversion through the existing gas-meter owner.
- Liquid cone differential meter using the native actual-cone geometry leaf.

**공개 API**

- [`pipeline_integrity.gas_meter.evaluate_gas_meter`](../src/pipeline_integrity/gas_meter.py#L653)
- [`pipeline_integrity.gas_meter_sizing.evaluate_gas_meter_sizing`](../src/pipeline_integrity/gas_meter_sizing.py#L53)
- [`pipeline_integrity.liquid_cone_meter.evaluate_liquid_cone_meter`](../src/pipeline_integrity/liquid_cone_meter.py#L52)

**검증 영역:** Supplied coefficient/expansibility annular cone differential gas meter with reviewed actual operating geometry; no coefficient calibration or other meter geometries.

**적용한 기준:** independent_annular_pressure_energy_capacity, reference_mass_budget, area_similarity, thermal_geometry.

**재현할 시험 선택자**

```text
tests/test_cone_meter_individual.py::test_independent_annular_capacity_and_reference_mass
tests/test_cone_meter_individual.py::test_similar_geometry_scales_area
tests/test_cone_meter_individual.py::test_operating_dimensions_reuse_reviewed_thermal_curve
tests/test_cone_meter_individual.py::test_native_cone_solution_against_separately_bracketed_coefficient_relation
```

## 125. `metrology.master_meter_calibration`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional mass-factor transfer from a supplied corrected prover volume.

**공개 API**

- [`pipeline_integrity.master_meter_proving.evaluate_master_meter_proving`](../src/pipeline_integrity/master_meter_proving.py#L70)

**검증 영역:** Declared proving volume/density/counter observations and factor transfer; no new physical instrument calibration.

**적용한 기준:** independent_geometry_mass_counter_ODE, pulse_partition.

**재현할 시험 선택자**

```text
tests/test_master_meter_proving_independent.py::test_geometry_volume_and_independent_mass_counter_odes
tests/test_master_meter_proving_independent.py::test_calibration_pulse_count_split_and_no_transfer_request
```

## 126. `metrology.parallel_identical_meter_runs`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional differential observation for identical quadratic meter runs.

**공개 API**

- [`pipeline_integrity.parallel_meter_runs.observe_parallel_meter_run_change`](../src/pipeline_integrity/parallel_meter_runs.py#L45)

**검증 영역:** Identical quadratic meter branches under frozen total flow and density; no measured calibration.

**적용한 기준:** independent_Bernoulli_branch_root, flow_continuity, count_change, unit_scaling.

**재현할 시험 선택자**

```text
tests/test_parallel_meter_runs_independent.py::test_independent_frozen_bernoulli_branch_root_and_continuity
tests/test_parallel_meter_runs_independent.py::test_withdrawal_order_and_unit_scaling
tests/test_parallel_meter_runs_independent.py::test_zero_head_identity_and_explicit_measurement_range
tests/test_parallel_meter_runs_independent.py::test_unequal_or_changing_state_not_silently_frozen
```

## 127. `metrology.ultrasonic_transit`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Paired travel-time inversion for uniform axial advection and stationary endpoints.

**공개 API**

- [`pipeline_integrity.paired_transit.invert_paired_transit`](../src/pipeline_integrity/paired_transit.py#L31)

**검증 영역:** Uniform moving isotropic medium with fixed ray geometry, explicit timing delays and supplied profile factor.

**적용한 기준:** independent_vector_ray_geometry, flow_sound_inverse, clock_delay_removal.

**재현할 시험 선택자**

```text
tests/test_paired_transit_individual.py::test_independent_vector_ray_geometry_recovers_flow_and_medium_sound
tests/test_paired_transit_individual.py::test_oblique_half_sum_is_not_relabelled_true_sound_speed
tests/test_paired_transit_individual.py::test_asymmetric_fixed_delays_are_removed_in_same_clock
```

## 128. `metrology.ultrasonic_wall_thickness`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Source-preserving NDE waveform reading and reviewed pulse-echo exchange with the owned material engine.

**공개 API**

- [`pipeline_integrity.nde.evaluate_echo_thickness`](../src/pipeline_integrity/nde.py#L211)

**검증 영역:** Known solid echo path and supplied wave speed, fixed reference reflector and observation angle; no defect detectability inference.

**적용한 기준:** independent_ray_flight, normal_depth_projection, absolute_clock_shift.

**재현할 시험 선택자**

```text
tests/test_solid_pulse_echo_reference_independent.py::test_native_solid_echo_recovers_known_depth_from_independent_ray_flight
tests/test_solid_pulse_echo_reference_independent.py::test_equal_absolute_timestamp_shift_preserves_measured_solid_echo_interval
```

## 129. `mixture.closed_volume_replace_to_target`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Single withdrawal/refill inventory under an explicitly additive-volume basis.

**공개 API**

- [`pipeline_integrity.component_replacement.solve_component_replacement`](../src/pipeline_integrity/component_replacement.py#L24)

**검증 영역:** Single uniform withdrawal and refill with additive component volumes; no continuous mixing kinetics.

**적용한 기준:** independent_decimal_inventory, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_component_replacement_individual.py::test_inverse_from_independent_decimal_component_inventory
tests/test_component_replacement_individual.py::test_extensive_inventory_scale
```

## 130. `mixture.common_volume_specific_gravity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Measured bulk mass partition and separately declared liquid-equivalent volumes.
- Common-basis volume-weighted liquid reference-property blend relations.
- Additive-volume two-liquid density conversion with explicit identifiability.

**공개 API**

- [`pipeline_integrity.component_mass_reference.observe_component_mass_reference`](../src/pipeline_integrity/component_mass_reference.py#L99)
- [`pipeline_integrity.liquid_blending.evaluate_liquid_blend`](../src/pipeline_integrity/liquid_blending.py#L42)
- [`pipeline_integrity.liquid_mixture.evaluate_two_liquid_mixture`](../src/pipeline_integrity/liquid_mixture.py#L29)

**검증 영역:** Additive liquid component volumes at common conditions and reference density; no excess-volume or viscosity prediction.

**적용한 기준:** component_mass_budget, unit_invariance, permutation.

**재현할 시험 선택자**

```text
tests/test_liquid_blending_individual.py::test_specific_gravity_matches_independent_component_mass_budget
tests/test_liquid_blending_individual.py::test_volume_unit_scaling_and_component_permutation_do_not_change_specific_gravity
```

## 131. `mixture.gas_calorific_value`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Supplied combustion-energy properties on explicit gas reference volumes.

**공개 API**

- [`pipeline_integrity.gas_combustion_reference.observe_gas_combustion_reference`](../src/pipeline_integrity/gas_combustion_reference.py#L70)

**검증 영역:** Supplied complete-combustion component reaction energies with declared reference volume and water basis; no heating-value fit.

**적용한 기준:** atom_balanced_reaction, energy_mass_budget, water_latent_energy, reference_volume_extensivity.

**재현할 시험 선택자**

```text
tests/test_gas_combustion_reference_individual.py::test_independent_complete_reaction_product_energy_and_dry_mass
tests/test_gas_combustion_reference_individual.py::test_wet_dry_energy_is_invariant_but_mass_changes_only_by_water
tests/test_gas_combustion_reference_individual.py::test_Z_correction_is_molar_density_not_a_new_heating_fit
tests/test_gas_combustion_reference_individual.py::test_existing_native_gas_reference_backend_reaches_independent_dilute_energy_density_limit
tests/test_gas_combustion_reference_individual.py::test_gross_minus_net_equals_independent_stoichiometric_product_water_latent_energy
tests/test_gas_combustion_reference_individual.py::test_quantity_extensivity_retains_energy_density_and_J_versus_W
tests/test_gas_combustion_reference_individual.py::test_all_inert_zero_is_explicit_not_unknown_combustion_data
```

## 132. `mixture.gas_molar_mass_heat_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Neutral composition and supplied ideal-gas caloric properties, without viscosity inputs.

**공개 API**

- [`pipeline_integrity.gas_caloric_mixture.evaluate_gas_caloric_mixture`](../src/pipeline_integrity/gas_caloric_mixture.py#L54)
- [`pipeline_integrity.gas_caloric_mixture.gas_caloric_mixture_from_amounts`](../src/pipeline_integrity/gas_caloric_mixture.py#L126)

**검증 영역:** Ideal-gas caloric mixture using supplied component molar masses and common-temperature heat capacities.

**적용한 기준:** independent_weighed_moles, parcel_enthalpy_integral, expansion_work_Cp_Cv, mass_fraction_conversion, single_species_limit.

**재현할 시험 선택자**

```text
tests/test_gas_caloric_mixture_individual.py::test_weighed_component_mass_and_parcel_heat_ledgers
tests/test_gas_caloric_mixture_individual.py::test_mass_fraction_basis_reconstructs_independently_counted_moles
tests/test_gas_caloric_mixture_individual.py::test_molar_mass_only_needs_no_viscosity_or_heat_capacity
tests/test_gas_caloric_mixture_individual.py::test_single_component_limit
tests/test_gas_caloric_mixture_individual.py::test_different_component_temperature_not_averaged
tests/test_gas_caloric_mixture_individual.py::test_nonphysical_ideal_gas_cv_not_clipped
tests/test_gas_caloric_mixture_individual.py::test_explicit_absolute_quantity_adapter_reuses_same_composition
tests/test_gas_caloric_mixture_individual.py::test_zero_absolute_quantity_has_no_composition
```

## 133. `mixture.glycol_freezing_target`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Concentration inversion of an explicitly selected aqueous-glycol curve.

**공개 API**

- [`pipeline_integrity.glycol_freezing_target.evaluate_glycol_freezing_target`](../src/pipeline_integrity/glycol_freezing_target.py#L68)

**검증 영역:** Supplied monotone glycol freezing branch with independent ideal solvent-equilibrium test provider and weighed stock recipe; no empirical phase-curve calibration.

**적용한 기준:** independent_chemical_potential, mole_inventory, weighed_mass_volume_recipe.

**재현할 시험 선택자**

```text
tests/test_glycol_freezing_target_independent.py::test_independent_solvent_chemical_potential_and_species_mole_inventory
tests/test_glycol_freezing_target_independent.py::test_charged_volume_recipe_from_independently_weighed_stocks_not_mixture_additivity
```

## 134. `mixture.inverse_root_sus`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Common-basis volume-weighted liquid reference-property blend relations.

**공개 API**

- [`pipeline_integrity.liquid_blending.evaluate_liquid_blend`](../src/pipeline_integrity/liquid_blending.py#L42)

## 135. `mixture.kinematic_viscosity_index_blend`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Mass-weighted double-log viscosity coordinate for reviewed liquid recipes.

**공개 API**

- [`pipeline_integrity.kinematic_viscosity_blend.evaluate_kinematic_viscosity_blend`](../src/pipeline_integrity/kinematic_viscosity_blend.py#L66)

## 136. `mixture.oil_water_pseudoliquid_viscosity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit volume-average liquid viscosity estimate with applicability retained.

**공개 API**

- [`pipeline_integrity.oil_water_viscosity_estimate.estimate_oil_water_viscosity`](../src/pipeline_integrity/oil_water_viscosity_estimate.py#L37)

**검증 영역:** Explicit parallel-stripe Newtonian Couette averaging approximation; no emulsion rheology prediction.

**적용한 기준:** independent_Couette_force_work_dissipation, phase_relabel, volume_scaling, identical_phase_limit.

**재현할 시험 선택자**

```text
tests/test_oil_water_viscosity_estimate_independent.py::test_independent_parallel_stripe_couette_force_work_and_dissipation
tests/test_oil_water_viscosity_estimate_independent.py::test_volume_scale_phase_relabel_and_identical_viscosity_limits
tests/test_oil_water_viscosity_estimate_independent.py::test_convex_dynamic_not_density_or_kinematic_mix
tests/test_oil_water_viscosity_estimate_independent.py::test_absent_component_property_not_inferred
tests/test_oil_water_viscosity_estimate_independent.py::test_zero_volume_and_no_shear_observation
```

## 137. `mixture.pseudocritical_gas`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit pseudo-critical approximations, not mixture critical-point/EOS solves.

**공개 API**

- [`pipeline_integrity.gas_pseudocritical.estimate_gas_pseudocritical`](../src/pipeline_integrity/gas_pseudocritical.py#L60)

**검증 영역:** Manufactured fixed-composition van-der-Waals pseudofluid family with common excluded volume and arithmetic pair attractions; not a general real-mixture critical law.

**적용한 기준:** independent_Helmholtz_critical_root, pressure_work_derivative, tolerance_refinement, composition_extensivity.

**재현할 시험 선택자**

```text
tests/test_kay_pseudocritical_helmholtz_independent.py::test_independent_pair_helmholtz_mechanical_critical_root_and_tolerance_refinement
tests/test_kay_pseudocritical_helmholtz_independent.py::test_independent_reference_pressure_is_helmholtz_derivative_and_reversible_work
tests/test_kay_pseudocritical_helmholtz_independent.py::test_noncommon_excluded_volume_is_not_a_general_true_critical_prediction
tests/test_kay_pseudocritical_helmholtz_independent.py::test_species_amount_scaling_and_identical_component_splitting_invariance
```

## 138. `mixture.sample_cylinder_pressure_purge`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Ideal fixed-temperature fill/mix/vent provenance ledger for sample cylinders.

**공개 API**

- [`pipeline_integrity.sample_pressure_purge.evaluate_sample_pressure_purge`](../src/pipeline_integrity/sample_pressure_purge.py#L59)

**검증 영역:** Ideal-gas common-temperature well-mixed pressure fill/vent stages with known species molar masses.

**적용한 기준:** independent_species_inventory_ODE, mole_mass_conservation, pressure_product_reference, target_event, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_sample_pressure_purge_individual.py::test_stage_process_from_independent_species_mass_ODE
tests/test_sample_pressure_purge_individual.py::test_independent_decimal_pressure_product_and_retained_final_fill_target
tests/test_sample_pressure_purge_individual.py::test_venting_alone_changes_inventory_not_composition
tests/test_sample_pressure_purge_individual.py::test_cylinder_volume_extensivity_preserves_fraction_and_first_cycle
tests/test_sample_pressure_purge_individual.py::test_zero_target_remains_unattainable_despite_fraction_underflow
tests/test_sample_pressure_purge_individual.py::test_no_stages_and_initial_target_event
tests/test_sample_pressure_purge_individual.py::test_no_pressure_change_and_unrequested_target
tests/test_sample_pressure_purge_individual.py::test_same_chemistry_molar_mass_does_not_erase_original_provenance
```

## 139. `mixture.water_saturated_gas`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Water-equilibrium observation from supplied same-state fugacity closures.
- Explicit liquid-dew/ice-frost observations and ideal dry-carrier water ledgers.

**공개 API**

- [`pipeline_integrity.saturated_gas_fugacity.observe_saturated_gas_fugacity`](../src/pipeline_integrity/saturated_gas_fugacity.py#L51)
- [`pipeline_integrity.water_vapor_observation.moisture_gas_basis_state`](../src/pipeline_integrity/water_vapor_observation.py#L229)
- [`pipeline_integrity.water_vapor_observation.observe_water_vapor`](../src/pipeline_integrity/water_vapor_observation.py#L125)

**검증 영역:** Ideal dilute water/carrier wet-dry mass/mole and reference-volume ledgers at declared saturation basis; fugacity-based nonideal branch remains outside this profile.

**적용한 기준:** independent_decimal_mass_moles, dry_carrier_extensivity, water_free_zero_limit, reference_volume_ledger.

**재현할 시험 선택자**

```text
tests/test_water_vapor_observation_individual.py::test_wet_dry_mass_and_mole_ledgers_against_independent_decimal_conservation
tests/test_water_vapor_observation_individual.py::test_dry_carrier_extensivity_preserves_humidity_and_clock
tests/test_water_vapor_observation_individual.py::test_zero_dry_quantity_has_zero_amounts_without_fake_zero_molar_mass
tests/test_water_vapor_observation_individual.py::test_exact_zero_water_has_no_finite_dewpoint_and_no_added_water
tests/test_water_vapor_observation_individual.py::test_computed_water_fraction_reuses_existing_reference_volume_owner_and_mass_ledger
```

## 140. `moisture.dewpoint_relative_humidity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit liquid-dew/ice-frost observations and ideal dry-carrier water ledgers.

**공개 API**

- [`pipeline_integrity.water_vapor_observation.observe_water_saturation`](../src/pipeline_integrity/water_vapor_observation.py#L26)
- [`pipeline_integrity.water_vapor_observation.observe_water_vapor`](../src/pipeline_integrity/water_vapor_observation.py#L125)

**검증 영역:** Reviewed water/ice equilibrium saturation and ideal dilute-carrier humidity coordinates; no nonideal multicomponent phase-envelope calibration.

**적용한 기준:** independent_Clausius_Clapeyron, latent_heat_integral, phase_seam, RH_partial_pressure, condensation_coordinate.

**재현할 시험 선택자**

```text
tests/test_water_vapor_observation_individual.py::test_liquid_saturation_slope_matches_independent_clausius_clapeyron
tests/test_water_vapor_observation_individual.py::test_ice_sublimation_slope_matches_ice_vapor_latent_heat
tests/test_water_vapor_observation_individual.py::test_liquid_pressure_change_from_integrated_latent_heat_not_inverse_roundtrip
tests/test_water_vapor_observation_individual.py::test_triple_reference_and_named_phase_seam_not_a_single_unlabelled_curve
tests/test_water_vapor_observation_individual.py::test_partial_pressure_RH_mole_fraction_and_condensation_coordinates
tests/test_water_vapor_observation_individual.py::test_RH_liquid_reference_at_warm_gas_and_separate_cold_frost_point
tests/test_water_vapor_observation_individual.py::test_saturated_state_returns_phase_reference_and_same_condensation_point
```

## 141. `multiphase.hilly_liquid_holdup_loss`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Local inclined two-phase segment using a declared empirical holdup model.

**공개 API**

- [`pipeline_integrity.inclined_two_phase_segment.evaluate_inclined_two_phase_segment`](../src/pipeline_integrity/inclined_two_phase_segment.py#L76)

## 142. `multiphase.lockhart_martinelli_loss`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Smooth horizontal two-phase friction via an explicit named correlation backend.

**공개 API**

- [`pipeline_integrity.horizontal_two_phase_friction.evaluate_horizontal_two_phase_friction`](../src/pipeline_integrity/horizontal_two_phase_friction.py#L36)

## 143. `notch_threshold_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Unit-explicit supplied empirical notch and impact-toughness coordinates.

**공개 API**

- [`pipeline_integrity.fracture_property_coordinates.evaluate_impact_toughness_coordinate`](../src/pipeline_integrity/fracture_property_coordinates.py#L74)
- [`pipeline_integrity.fracture_property_coordinates.evaluate_normalized_impact_toughness_coordinate`](../src/pipeline_integrity/fracture_property_coordinates.py#L59)
- [`pipeline_integrity.fracture_property_coordinates.evaluate_notch_threshold_coordinate`](../src/pipeline_integrity/fracture_property_coordinates.py#L16)

## 144. `optics.point_source_illuminance`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Directional point-light contributions at one explicitly oriented surface point.

**공개 API**

- [`pipeline_integrity.point_illuminance.evaluate_point_illuminance`](../src/pipeline_integrity/point_illuminance.py#L22)

**검증 영역:** Far-field point-source intensity on a planar differential receiver with supplied transmission.

**적용한 기준:** independent_solid_angle_flux, oblique_geometry, inverse_square_similarity, source_superposition.

**재현할 시험 선택자**

```text
tests/test_point_illuminance_individual.py::test_normal_and_oblique_three_four_five_geometry
tests/test_point_illuminance_individual.py::test_distance_square_and_intensity_scaling
tests/test_point_illuminance_individual.py::test_disk_received_flux_against_independent_exact_solid_angle
tests/test_point_illuminance_individual.py::test_multiple_sources_transmission_and_back_face
tests/test_point_illuminance_individual.py::test_rigid_translation_and_normal_scale_preserve_observation
```

## 145. `pendulum_swing_response`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Small-angle suspended-body frequency and supplied spectral displacement.

**공개 API**

- [`pipeline_integrity.pendulum_spectral_coordinate.evaluate_pendulum_spectral_coordinate`](../src/pipeline_integrity/pendulum_spectral_coordinate.py#L13)

**검증 영역:** Small-angle pendulum spectral coordinate with supplied spectral ordinate and displacement multiplier; no nonlinear swinging or spectrum calibration.

**적용한 기준:** independent_gravity_energy_period_quadrature, small_angle_limit, pseudo_acceleration_identity, gravity_work.

**재현할 시험 선택자**

```text
tests/test_pendulum_spectral_coordinate_independent.py::test_frequency_is_small_angle_limit_of_gravity_energy_period
tests/test_pendulum_spectral_coordinate_independent.py::test_spectral_pseudo_acceleration_and_gravity_work
tests/test_pendulum_spectral_coordinate_independent.py::test_zero_spectrum_preserves_natural_frequency
```

## 146. `pipe.hose_mises_friction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Two-terminal series/parallel liquid paths built from existing loss primitives.

**공개 API**

- [`pipeline_integrity.liquid_path_system.evaluate_liquid_path_system`](../src/pipeline_integrity/liquid_path_system.py#L163)

**검증 영역:** Reviewed actual-diameter series liquid path using supported Darcy/local-loss owners; no separate legacy empirical hose-law calibration.

**적용한 기준:** independent_radial_force, series_energy_budget, pointwise_reference_length.

**재현할 시험 선택자**

```text
tests/test_liquid_path_system_independent.py::test_series_actual_diameters_and_local_velocity_basis_from_radial_force
tests/test_liquid_path_system_independent.py::test_static_pressure_keeps_independently_integrated_endpoint_kinetic_energy
tests/test_liquid_path_system_independent.py::test_series_pointwise_reference_length_includes_actual_local_losses
```

## 147. `pipe.karman_radial_profile`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Supplied-parameter Karman mean-profile approximation in a circular pipe.

**공개 API**

- [`pipeline_integrity.karman_radial_profile.evaluate_karman_radial_profile`](../src/pipeline_integrity/karman_radial_profile.py#L58)

**검증 영역:** Supplied wall-law constants and monotone inverse-gradient pipe profile with explicit support; no turbulent wall-law empirical fit.

**적용한 기준:** independent_inverse_gradient_ODE, area_moment_integrals, constitutive_stress_balance, no_slip, asymptotic_limits.

**재현할 시험 선택자**

```text
tests/test_karman_radial_profile_individual.py::test_profile_and_three_area_moments_match_independent_inverse_gradient_ode
tests/test_karman_radial_profile_individual.py::test_sign_and_geometric_scaling_preserve_dimensionless_shape_and_distinct_moments
tests/test_karman_radial_profile_individual.py::test_large_offset_arithmetic_has_the_triangular_profile_limit_not_a_fake_parabola
tests/test_karman_radial_profile_individual.py::test_next_float_inside_wall_retains_small_positive_velocity_and_axis_limit_is_explicit
tests/test_karman_radial_profile_individual.py::test_profile_finite_differences_and_stress_constitutive_balance_agree_away_from_axis
tests/test_karman_radial_profile_individual.py::test_supplied_wall_gradient_condition_and_no_slip_hold
```

## 148. `pipe.prescribed_pressure_floor_route`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed-gradient piezometric routing with an equilibrium pressure floor.

**공개 API**

- [`pipeline_integrity.saturation_head_route.evaluate_saturation_head_route`](../src/pipeline_integrity/saturation_head_route.py#L65)

**검증 영역:** Prescribed circular-pipe route pressure-floor observer under declared head boundary and optional separate depth capacity checks; no vapor transport dynamics.

**적용한 기준:** independent_global_head_optimization, transfer_events, vertical_datum_invariance, survey_partition_invariance, capacity_budget.

**재현할 시험 선택자**

```text
tests/test_saturation_head_route_individual.py::test_exact_transfer_intersections_and_original_boundary_are_retained
tests/test_saturation_head_route_individual.py::test_piecewise_route_matches_independent_global_head_optimization
tests/test_saturation_head_route_individual.py::test_two_separated_gravity_candidates_retain_a_filled_middle_reach
tests/test_saturation_head_route_individual.py::test_vertical_datum_shift_does_not_change_pressure_or_transfer_positions
tests/test_saturation_head_route_individual.py::test_linear_survey_partition_does_not_change_the_route_or_events
tests/test_saturation_head_route_individual.py::test_sufficient_outlet_pressure_keeps_every_section_filled
tests/test_saturation_head_route_individual.py::test_flat_transformed_obstacle_is_marginal_not_an_invented_vapor_fraction
tests/test_saturation_head_route_individual.py::test_optional_partial_flow_depth_checks_mass_capacity_and_velocity_head_budget
tests/test_saturation_head_route_individual.py::test_pressure_density_scaling_preserves_head_and_scales_absolute_pressures
tests/test_saturation_head_route_individual.py::test_optional_velocity_head_limit_is_enforced_instead_of_silently_claiming_exact_routing
```

## 149. `pipe.state_dependent_quasisteady_friction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Quasisteady single-phase Darcy drag from current local thermodynamic states.
- Current-state quasi-steady wall resistance for the liquid acoustic owner.
- Liquid acoustic characteristics on pipe graphs with explicit head/withdrawal boundaries.

**공개 API**

- [`pipeline_integrity.gas_pipe_friction.StateDependentDarcyDecay`](../src/pipeline_integrity/gas_pipe_friction.py#L14)
- [`pipeline_integrity.liquid_moc_friction.LiquidMOCQuasisteadyDarcy`](../src/pipeline_integrity/liquid_moc_friction.py#L45)
- [`pipeline_integrity.liquid_moc_friction.LiquidMOCQuasisteadyDarcy.__call__`](../src/pipeline_integrity/liquid_moc_friction.py#L57)
- [`pipeline_integrity.transient.simulate_liquid_transient`](../src/pipeline_integrity/transient.py#L175)

**검증 영역:** Linear liquid acoustic field with prescribed spatially varying laminar-viscosity drag and reviewed characteristic interpolation; no general turbulent transient friction memory.

**적용한 기준:** independent_modal_field_solution, characteristic_feet, spatial_refinement, friction_work_budget.

**재현할 시험 선택자**

```text
tests/test_liquid_moc_spatial_drag_wave_individual.py::test_spatial_viscosity_characteristic_feet_wave_and_work_budget
```

## 150. `pipe.supplied_wall_friction_modifier`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Implicit empirical turbulent resistance laws, with explicit domain selection.

**공개 API**

- [`pipeline_integrity.turbulent_constitutive_friction.evaluate_turbulent_resistance`](../src/pipeline_integrity/turbulent_constitutive_friction.py#L59)

## 151. `pipeline.buried_aboveground_transition`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Constant supplied axial soil resistance between restrained and free pipe.

**공개 API**

- [`pipeline_integrity.soil_transition_observer.evaluate_soil_transition`](../src/pipeline_integrity/soil_transition_observer.py#L20)

**검증 영역:** Straight linear elastic axial transition with declared constant soil resistance and observed far-field loading; no nonlinear soil law inference.

**적용한 기준:** independent_axial_equilibrium_ODE, compatibility_event, anchor_force_balance, zero_load_limit.

**재현할 시험 선택자**

```text
tests/test_soil_transition_individual.py::test_independent_axial_equilibrium_and_compatibility_event_ode
tests/test_soil_transition_individual.py::test_zero_load_has_zero_length_movement_and_no_fictitious_soil_force
tests/test_soil_transition_individual.py::test_resistance_changes_length_and_displacement_but_not_anchor_force
```

## 152. `pipeline.lowering_elastic_compatibility`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Additional uniaxial elastic stress for a prescribed material-line length.

**공개 API**

- [`pipeline_integrity.lowering_axial_compatibility.evaluate_lowering_axial_compatibility`](../src/pipeline_integrity/lowering_axial_compatibility.py#L36)

**검증 영역:** Prescribed lowering profile with pure linear elastic material-length compatibility and declared initial axial stress; no unknown slip/feed mechanics.

**적용한 기준:** independent_curve_arc_length, axial_bar_shooting, elastic_work, material_length_compatibility.

**재현할 시험 선택자**

```text
tests/test_lowering_axial_compatibility_individual.py::test_circular_profile_length_uses_independent_parametric_curve_integral
tests/test_lowering_axial_compatibility_individual.py::test_independent_axial_bar_shooting_equilibrium_work_and_compatibility
tests/test_lowering_axial_compatibility_individual.py::test_authored_smooth_four_arc_profile_preserves_initial_horizontal_length
tests/test_lowering_axial_compatibility_individual.py::test_initial_convex_material_length_is_not_replaced_with_chord
tests/test_lowering_axial_compatibility_individual.py::test_existing_stress_is_separate_from_additional_elastic_work
```

## 153. `plasticity.hardening_membrane_pressure_turn`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Pressure limit point of a supplied power-hardening membrane idealization.

**공개 API**

- [`pipeline_integrity.hardening_membrane_limit.evaluate_hardening_membrane_limit`](../src/pipeline_integrity/hardening_membrane_limit.py#L39)

**검증 영역:** Supplied isotropic power-hardening thin membrane with declared strain transformation; no fracture or material fit.

**적용한 기준:** independent_pressure_curve_optimizer, virtual_work, incompressible_material_volume, similarity.

**재현할 시험 선택자**

```text
tests/test_hardening_membrane_limit_independent.py::test_pressure_turning_point_matches_independent_curve_optimizer
tests/test_hardening_membrane_limit_independent.py::test_work_conjugate_transformation_and_constant_length_material_volume
tests/test_hardening_membrane_limit_independent.py::test_supplied_legacy_transformation_matches_distinct_closed_expression
tests/test_hardening_membrane_limit_independent.py::test_material_and_geometry_similarity
tests/test_hardening_membrane_limit_independent.py::test_thick_geometry_is_not_silently_treated_as_a_membrane
tests/test_hardening_membrane_limit_independent.py::test_supplied_transformation_requires_an_explicit_basis
```

## 154. `plasticity.perfect_plastic_cylinder`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Associated-J2 full-wall plastic pressure limit in fixed annular geometry.

**공개 API**

- [`pipeline_integrity.perfect_plastic_cylinder.evaluate_perfect_plastic_cylinder`](../src/pipeline_integrity/perfect_plastic_cylinder.py#L44)

**검증 영역:** Closed-cap isotropic incompressible perfectly plastic cylinder under zero exterior reference pressure; no hardening or fracture.

**적용한 기준:** independent_radial_IVP, J2_yield_every_radius, cap_thrust, collapse_virtual_work, thin_limit.

**재현할 시험 선택자**

```text
tests/test_perfect_plastic_cylinder_independent.py::test_independent_radial_equilibrium_initial_value_solution_and_boundaries
tests/test_perfect_plastic_cylinder_independent.py::test_every_point_satisfies_von_mises_yield_and_closed_cap_axial_force
tests/test_perfect_plastic_cylinder_independent.py::test_admissible_incompressible_collapse_velocity_dissipation_matches_pressure_power
tests/test_perfect_plastic_cylinder_independent.py::test_decreasing_wall_ratios_converge_to_closed_end_thin_j2_limit
tests/test_perfect_plastic_cylinder_independent.py::test_full_wall_limit_is_at_least_elastic_lame_first_yield
tests/test_perfect_plastic_cylinder_independent.py::test_pressure_and_geometry_similarity
tests/test_perfect_plastic_cylinder_independent.py::test_other_axial_constraints_are_not_assigned_the_closed_cap_solution
tests/test_perfect_plastic_cylinder_independent.py::test_zero_exterior_reference_and_limitations_are_explicit
```

## 155. `plate.clamped_circular_elastic_pressure`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Linear clamped circular plate under uniform differential pressure.

**공개 API**

- [`pipeline_integrity.circular_plate.evaluate_clamped_circular_plate`](../src/pipeline_integrity/circular_plate.py#L17)
- [`pipeline_integrity.circular_plate.invert_clamped_plate_stress_ceiling`](../src/pipeline_integrity/circular_plate.py#L55)

**검증 영역:** Small-deflection isotropic thin circular plate with clamped edge and uniform pressure; declared material support only.

**적용한 기준:** independent_virtual_work, surface_curvature, geometric_elastic_similarity, inverse_consistency.

**재현할 시험 선택자**

```text
tests/test_circular_plate_individual.py::test_independent_virtual_work_and_surface_curvature
tests/test_circular_plate_individual.py::test_length_similarity_and_elastic_modulus
tests/test_circular_plate_individual.py::test_supplied_nominal_ceiling_inverse_then_response
```

## 156. `porous.constant_pressure_sharp_front`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Bounded constant-feed pool and pressure-driven spherical porous-front models.

**공개 API**

- [`pipeline_integrity.porous_release.evaluate_spherical_porous_front`](../src/pipeline_integrity/porous_release.py#L112)

**검증 영역:** Homogeneous spherical Darcy sharp front before breakthrough; no post-breakthrough flow-field qualification.

**적용한 기준:** darcy_integral, pore_volume_budget, independent_ode, time_refinement, breakthrough_event, dimensional_similarity.

**재현할 시험 선택자**

```text
tests/test_porous_release_independent.py::test_sphere_radial_pressure_drop_and_mass_storage
tests/test_porous_release_independent.py::test_sphere_independent_time_integral
tests/test_porous_release_independent.py::test_sphere_mass_rate_by_refined_center_difference
tests/test_porous_release_independent.py::test_sphere_independent_differential_integration
tests/test_porous_release_independent.py::test_sphere_breakthrough_event_and_geometry
tests/test_porous_release_independent.py::test_sphere_long_time_limit_and_scaling
tests/test_physical_similarity_223.py::test_porous_darcy_permeability_time_similarity_and_storage
```

## 157. `porous.richards_unsaturated_flow`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Mixed-form single-liquid Darcy/Richards flow on orthogonal finite volumes.

**공개 API**

- [`pipeline_integrity.richards_fv.solve_richards_fv`](../src/pipeline_integrity/richards_fv.py#L149)

**검증 영역:** Orthogonal FV meshes with authored supported linear and nonlinear hydraulic closures; no field soil calibration.

**적용한 기준:** analytic_diffusion, mass_conservation, space_and_time_convergence, gravity_equilibrium, boundary_sources.

**재현할 시험 선택자**

```text
tests/test_richards_fv_independent.py::test_uniform_preservation_generic_dimension
tests/test_richards_fv_independent.py::test_diffusion_exact_neumann_mode_spatial_refinement
tests/test_richards_fv_independent.py::test_backward_euler_time_refinement_independent_discrete_mode
tests/test_richards_fv_independent.py::test_nonuniform_3d_diffusion_against_analytic_discrete_eigenmode
tests/test_richards_fv_independent.py::test_hydrostatic_gravity_no_flow
tests/test_richards_fv_independent.py::test_actual_nonlinear_content_one_cell_flux_not_capacity_shortcut
tests/test_richards_fv_independent.py::test_closed_nonlinear_multicell_mass_conservation_and_time_variation
tests/test_richards_fv_independent.py::test_heterogeneous_series_darcy_resistance_steady_limit
tests/test_richards_fv_independent.py::test_signed_cell_source_and_prescribed_boundary_ledger
```

## 158. `pressure.nominal_membrane`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit diameter-basis nominal pressure-stress relations and algebraic inverses.
- Nominal closed-cap pressure plus elastic bending tensile-budget observer.

**공개 API**

- [`pipeline_integrity.nominal_pipe_pressure.nominal_hoop_stress_Pa`](../src/pipeline_integrity/nominal_pipe_pressure.py#L40)
- [`pipeline_integrity.nominal_pipe_pressure.nominal_wall_thickness_m`](../src/pipeline_integrity/nominal_pipe_pressure.py#L57)
- [`pipeline_integrity.pressure_bending_budget.evaluate_pressure_bending_budget`](../src/pipeline_integrity/pressure_bending_budget.py#L33)

**검증 영역:** Caller-declared outside-diameter nominal hoop convention and its asymptotic thin-wall limit; not exact finite-thickness stress or code acceptance.

**적용한 기준:** independent_lame_thin_limit, asymptotic_error_order, unit_invariance.

**재현할 시험 선택자**

```text
tests/test_physical_similarity_223.py::test_nominal_outside_diameter_hoop_stress_converges_to_lame_thin_limit
tests/test_nominal_pipe_pressure_independent_local.py::test_authored_nominal_reference_points
tests/test_nominal_pipe_pressure_independent_local.py::test_exact_customary_unit_normalization_preserves_nominal_stress
```

## 159. `properties.acoustic_bulk_density`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional density observation from independent isentropic modulus and fluid sound speed.

**공개 API**

- [`pipeline_integrity.acoustic_density.observe_acoustic_density`](../src/pipeline_integrity/acoustic_density.py#L26)

**검증 영역:** Same-state isentropic bulk modulus and true uniform-medium wave speed density inverse; no dispersive or pipe-wall effective-speed interpretation.

**적용한 기준:** independent_mass_momentum_IVP, isentropic_EOS_derivative, oblique_ray_reference, similarity.

**재현할 시험 선택자**

```text
tests/test_acoustic_density_individual.py::test_linear_mass_momentum_mode_period_inverts_known_density
tests/test_acoustic_density_individual.py::test_actual_single_phase_EOS_isentropic_derivative
tests/test_acoustic_density_individual.py::test_true_medium_speed_from_oblique_transit_not_effective_beam_speed
tests/test_acoustic_density_individual.py::test_common_modulus_and_speed_scaling
tests/test_acoustic_density_individual.py::test_different_states_not_relabelled
```

## 160. `properties.black_oil_phase_partition`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Reference-component black-oil partition with supplied reviewed PVT closure.

**공개 API**

- [`pipeline_integrity.black_oil_phase_observer.observe_black_oil_partition`](../src/pipeline_integrity/black_oil_phase_observer.py#L56)

**검증 영역:** Supplied black-oil equilibrium closures tested with manufactured Henry chemistry and additive phase volumes; no reservoir PVT calibration.

**적용한 기준:** chemical_potential_equilibrium, component_mass_budget, phase_volume, bubblepoint_event, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_black_oil_phase_observer_independent.py::test_independent_henry_chemical_potential_component_mass_and_phase_volume
tests/test_black_oil_phase_observer_independent.py::test_reference_gas_volume_relabel_preserves_physical_inventory_and_phase_state
tests/test_black_oil_phase_observer_independent.py::test_inventory_scale_preserves_phase_densities_and_volume_fractions
tests/test_black_oil_phase_observer_independent.py::test_bubblepoint_threshold_both_sides_and_boundary
tests/test_black_oil_phase_observer_independent.py::test_no_gas_and_zero_solubility_boundaries
tests/test_black_oil_phase_observer_independent.py::test_wrong_saturated_formation_factor_in_undersaturated_oil_not_silently_accepted
tests/test_black_oil_phase_observer_independent.py::test_gas_only_inventory_outside_oil_host_model
```

## 161. `properties.crude_oil_bulk_modulus`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local isothermal/acoustic moduli of an explicitly identified liquid EOS.

**공개 API**

- [`pipeline_integrity.identified_liquid_bulk_modulus.observe_identified_liquid_bulk_modulus`](../src/pipeline_integrity/identified_liquid_bulk_modulus.py#L46)

**검증 영역:** Single-phase liquid local isothermal/isentropic derivatives from a supplied property provider; no universal crude correlation.

**적용한 기준:** independent_Tait_constitutive_law, heat_capacity_identity, native_EOS_derivative_reference.

**재현할 시험 선택자**

```text
tests/test_identified_liquid_bulk_modulus_independent.py::test_independent_tait_pressure_volume_law_and_zero_expansion_caloric_identity
tests/test_identified_liquid_bulk_modulus_independent.py::test_native_hydrocarbon_distinct_isothermal_and_isentropic_derivative_references
```

## 162. `properties.gas_heating_value_gravity_fit`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Identifiable two-fuel/inert gravity observation, not a universal gas fit.

**공개 API**

- [`pipeline_integrity.gravity_constrained_calorific.observe_gravity_constrained_calorific`](../src/pipeline_integrity/gravity_constrained_calorific.py#L52)

**검증 영역:** Identifiable declared two-fuel/inert composition class with supplied pure reaction data and ideal reference density; no universal gravity heating-value fit.

**적용한 기준:** independent_molecular_density_inverse, Hess_reaction_energy, reference_water_mass_budget.

**재현할 시험 선택자**

```text
tests/test_gravity_constrained_calorific_independent.py::test_independent_molecular_density_inverse_and_hess_total_reaction_energy
tests/test_gravity_constrained_calorific_independent.py::test_reference_pressure_temperature_and_water_dilution_preserve_dry_molar_energy
tests/test_gravity_constrained_calorific_independent.py::test_equal_molar_masses_have_no_unique_inverse_even_with_different_heats
tests/test_gravity_constrained_calorific_independent.py::test_infeasible_gravity_not_clipped_into_binary_convex_domain
```

## 163. `properties.liquid_water_eos_response`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Single-phase water EOS response observations with explicit absolute states.

**공개 API**

- [`pipeline_integrity.liquid_water_response.observe_liquid_water_response`](../src/pipeline_integrity/liquid_water_response.py#L48)

**검증 영역:** Supported single-phase liquid-water HEOS response, density derivatives and fixed-mass state paths; no new EOS calibration.

**적용한 기준:** independent_five_point_derivatives, caloric_identity, density_anomaly, thermodynamic_path_integrals, reference_invariance.

**재현할 시험 선택자**

```text
tests/test_liquid_water_response_individual.py::test_beta_and_kappa_from_independent_five_point_density_derivatives
tests/test_liquid_water_response_individual.py::test_water_anomaly_has_negative_zero_positive_signed_beta
tests/test_liquid_water_response_individual.py::test_fixed_mass_ratio_and_isobaric_beta_path_integral
tests/test_liquid_water_response_individual.py::test_pressure_path_integral_and_two_leg_state_path
tests/test_liquid_water_response_individual.py::test_reference_state_changes_ratio_without_changing_local_response
tests/test_liquid_water_response_individual.py::test_extensive_mass_scaling_and_absent_reference
tests/test_liquid_water_response_individual.py::test_enthalpy_entropy_reference_shift_does_not_change_response
```

## 164. `properties.suspension_viscosity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Effective viscosity of a reviewed Newtonian hard-sphere suspension.

**공개 API**

- [`pipeline_integrity.suspension_viscosity.evaluate_suspension_viscosity`](../src/pipeline_integrity/suspension_viscosity.py#L37)

**검증 영역:** Reviewed hard-sphere low-inertia Newtonian-carrier effective-medium closure and independent dilute-sphere traction limit; no general slurry calibration.

**적용한 기준:** independent_stresslet_ODE, Einstein_surface_traction, dilute_limit, packing_limit.

**재현할 시험 선택자**

```text
tests/test_suspension_viscosity_independent.py::test_independent_incremental_stresslet_effective_medium_ODE
tests/test_suspension_viscosity_independent.py::test_sphere_surface_traction_recovers_independent_Einstein_dilute_slope
tests/test_suspension_viscosity_independent.py::test_zero_solids_and_monotone_packing_approach
```

## 165. `properties.water_resistivity_solids_fit`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional ionic-current observation from a reviewed water analysis.

**공개 API**

- [`pipeline_integrity.ionic_water_conductivity.observe_ionic_water_conductivity`](../src/pipeline_integrity/ionic_water_conductivity.py#L62)

**검증 영역:** Caller-supplied electroneutral ionic analysis and same-state transport mobilities in dilute linear-response water; no universal solids-resistivity fit.

**적용한 기준:** independent_particle_force_drag_current, Joule_dissipation, detailed_balance_random_walk, mass_charge_budget.

**재현할 시험 선택자**

```text
tests/test_ionic_water_conductivity_independent.py::test_independent_single_particle_force_drag_charge_current_and_joule_budget
tests/test_ionic_water_conductivity_independent.py::test_independent_detailed_balance_random_walk_small_field_reference
tests/test_ionic_water_conductivity_independent.py::test_mass_concentration_scaling_reference_unit_and_no_universal_TDS_mapping
tests/test_ionic_water_conductivity_independent.py::test_neutral_solute_does_not_create_carrier_current
tests/test_ionic_water_conductivity_independent.py::test_zero_dissolved_inventory_background_and_no_infinity_serialization
tests/test_ionic_water_conductivity_independent.py::test_state_shift_is_not_silently_corrected
```

## 166. `pump.available_npsh_budget`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Equipment budgets with declared operating conditions and vendor inputs.
- Temperature-dependent available suction-head search with injected properties.
- Bracketed suction diameter observation through existing Darcy/NPSH budgets.

**공개 API**

- [`pipeline_integrity.models.equipment.pump_npsh`](../src/pipeline_integrity/models/equipment.py#L64)
- [`pipeline_integrity.npsh_temperature_search.search_minimum_available_npsh`](../src/pipeline_integrity/npsh_temperature_search.py#L90)
- [`pipeline_integrity.suction_diameter_inverse.evaluate_suction_diameter`](../src/pipeline_integrity/suction_diameter_inverse.py#L39)

**검증 영역:** Stationary available suction head and supplied requirement margin; no cavitation performance claim.

**적용한 기준:** absolute_pressure_head, energy_budget, boundary_sensitivity.

**재현할 시험 선택자**

```text
tests/test_npsh_individual_absolute_head_local.py::test_constructed_absolute_head_energy_budgets
tests/test_npsh_individual_absolute_head_local.py::test_same_head_budget_is_invariant_to_density_and_gravity_basis
tests/test_npsh_individual_absolute_head_local.py::test_customary_pressure_head_conversion_with_explicit_rounding_interval
tests/test_npsh_individual_absolute_head_local.py::test_declared_vendor_requirement_changes_margin_only
```

## 167. `pump.fixed_geometry_speed_affinity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Equipment budgets with declared operating conditions and vendor inputs.
- Declared same-casing pump-curve approximation under speed/impeller changes.

**공개 API**

- [`pipeline_integrity.models.equipment.pump_affinity`](../src/pipeline_integrity/models/equipment.py#L40)
- [`pipeline_integrity.pump_curve_transform.scale_same_casing_pump_curve`](../src/pipeline_integrity/pump_curve_transform.py#L24)

**검증 영역:** Same-casing supplied pump curve coordinate transformations; no empirical efficiency prediction.

**적용한 기준:** coordinate_substitution, composition, diameter_scaling.

**재현할 시험 선택자**

```text
tests/test_pump_curve_transform_independent.py::test_transformed_quadratic_curve_matches_independent_coordinate_substitution
tests/test_pump_curve_transform_independent.py::test_same_casing_diameter_flow_is_linear_and_not_geometric_volume_scaling
tests/test_pump_curve_transform_independent.py::test_successive_transforms_equal_composed_transform_on_arbitrary_reference_curve
```

## 168. `pump.liquid_clearance_volumetric_efficiency`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Endpoint-density mass balance of a reviewed reciprocating liquid chamber.

**공개 API**

- [`pipeline_integrity.liquid_chamber_cycle.evaluate_liquid_chamber_cycle`](../src/pipeline_integrity/liquid_chamber_cycle.py#L56)

**검증 영역:** Single-phase liquid chamber delivery from supplied endpoint densities and slip with a reviewed stroke; no slip calibration.

**적용한 기준:** independent_closed_mass_pressure_volume_ODE, cycle_mass_transfer, incompressible_limit, reference_pressure_scaling.

**재현할 시험 선택자**

```text
tests/test_liquid_chamber_cycle_independent.py::test_closed_mass_pressure_volume_ode_and_full_piston_cycle
tests/test_liquid_chamber_cycle_independent.py::test_incompressible_limit_and_slip_basis
tests/test_liquid_chamber_cycle_independent.py::test_zero_clearance_removes_liquid_compression_delivery_penalty
tests/test_liquid_chamber_cycle_independent.py::test_extensive_scaling_and_common_pressure_datum_do_not_change_efficiency
tests/test_liquid_chamber_cycle_independent.py::test_small_density_increment_continuous_liquid_limit
tests/test_liquid_chamber_cycle_independent.py::test_impossible_stroke_density_reversal_and_excessive_slip_not_clipped
tests/test_liquid_chamber_cycle_independent.py::test_zero_delivery_boundary_is_preserved_without_artificial_positive_flow
tests/test_liquid_chamber_cycle_independent.py::test_existing_coolprop_liquid_states_endpoint_mass_reference
```

## 169. `pump.pipe_head_operating_point`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Selected installed liquid-valve lift inverse and local static sensitivity.
- Steady pump curves and common-flow head balances with explicit branch support.

**공개 API**

- [`pipeline_integrity.installed_valve_observer.evaluate_installed_valve`](../src/pipeline_integrity/installed_valve_observer.py#L50)
- [`pipeline_integrity.models.pump_curves.pump_system`](../src/pipeline_integrity/models/pump_curves.py#L74)
- [`pipeline_integrity.models.pump_curves.solve_pump_station_path`](../src/pipeline_integrity/models/pump_curves.py#L219)

**검증 영역:** Supported steady pump branch coupled to laminar or declared Colebrook pipe loss and local resistance; no pump map calibration.

**적용한 기준:** independent_laminar_quadratic_root, independent_turbulent_friction_root, series_partition_invariance.

**재현할 시험 선택자**

```text
tests/test_pump_pipe_operating_individual.py::test_laminar_pipe_linear_pump_and_quadratic_minor_loss_exact_root
tests/test_pump_pipe_operating_individual.py::test_turbulent_pipe_root_matches_independent_inverse_sqrt_friction_solution
tests/test_pump_pipe_operating_individual.py::test_serial_pipe_partition_invariance_preserves_minor_loss_and_common_flow
```

## 170. `pump.pressure_or_head_duty_power`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Pressure/volume-rate adapter to the existing two-efficiency pump energy ledger.

**공개 API**

- [`pipeline_integrity.pump_pressure_duty.evaluate_pump_pressure_duty`](../src/pipeline_integrity/pump_pressure_duty.py#L31)

**검증 영역:** Prescribed pressure-volume duty and two supplied efficiency stages; no pump performance calibration.

**적용한 기준:** pressure_work, energy_budget, zero_duty.

**재현할 시험 선택자**

```text
tests/test_pump_pressure_duty_individual.py::test_pressure_volume_power_matches_independent_us_units_and_source_rounded_factor
tests/test_pump_pressure_duty_individual.py::test_energy_losses_close_the_declared_two_efficiency_stage_budget
tests/test_pump_pressure_duty_individual.py::test_pressure_and_flow_similarity_does_not_require_an_inferred_fluid_density
tests/test_pump_pressure_duty_individual.py::test_zero_duty_preserves_zero_power
```

## 171. `pump.screw_empirical_slip_and_friction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Positive-displacement screw delivery with reviewed leakage and drag closures.

**공개 API**

- [`pipeline_integrity.screw_pump.evaluate_screw_pump`](../src/pipeline_integrity/screw_pump.py#L55)

**검증 영역:** Manufactured Newtonian planar leakage gap and annular Couette drag mapped to supplied screw-pump closure coefficients; no actual screw geometry/vendor calibration.

**적용한 기준:** independent_planar_Stokes_BVP, annular_azimuthal_Stokes_BVP, pressure_work_dissipation, stopped_reverse_leak.

**재현할 시험 선택자**

```text
tests/test_remaining_physical_limits_independent.py::test_screw_supplied_gap_closures_against_independent_stokes_fields
```

## 172. `pump.suction_acceleration_head`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Observed rigid-column inertia from actual common flow acceleration.

**공개 API**

- [`pipeline_integrity.liquid_acceleration_head.observe_liquid_acceleration_head`](../src/pipeline_integrity/liquid_acceleration_head.py#L44)

**검증 영역:** Prescribed incompressible piston-driven uniform inertance with explicit acceleration history; no friction or valve inertia.

**적용한 기준:** independent_Euler_force, kinetic_work_integral, cycle_zero_work, signed_deceleration.

**재현할 시험 선택자**

```text
tests/test_liquid_acceleration_head_independent.py::test_piston_motion_distributed_euler_pressure_force_and_work
tests/test_liquid_acceleration_head_independent.py::test_integrated_pressure_work_equals_kinetic_change_and_period_zero_work
tests/test_liquid_acceleration_head_independent.py::test_inertial_head_not_friction_and_deceleration_returns_energy
tests/test_liquid_acceleration_head_independent.py::test_zero_rate_and_zero_length_do_not_invent_dissipation
tests/test_liquid_acceleration_head_independent.py::test_pressure_observer_does_not_require_flow_for_optional_kinetic_output
```

## 173. `pump.thermal_pipe_operating_point`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Pressure-boundary operating points around the existing heated-liquid march.

**공개 API**

- [`pipeline_integrity.heated_operating_point.evaluate_heated_operating_point`](../src/pipeline_integrity/heated_operating_point.py#L69)

**검증 영역:** Pressure-boundary operating-point solver in the synthetic constant-property laminar limit; temperature-dependent liquid heating remains outside this profile.

**적용한 기준:** independent_Poiseuille_root.

**재현할 시험 선택자**

```text
tests/test_heated_operating_point_laminar_analytic_hangang.py::test_pressure_root_matches_closed_form_laminar_flow_rate
```

## 174. `release.offshore_advanced_inventory`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Equilibrium inventory release using caller-reviewed phase states and reference composition.

**공개 API**

- [`pipeline_integrity.offshore_inventory_release.evaluate_offshore_inventory_release`](../src/pipeline_integrity/offshore_inventory_release.py#L75)

**검증 영역:** Initial/final offshore component inventory from caller-supplied phase factors plus declared feed; no time-resolved discharge.

**적용한 기준:** independent_component_parcels, mass_budget, reference_basis_invariance, extensive_scaling.

**재현할 시험 선택자**

```text
tests/test_offshore_inventory_release_individual.py::test_component_parcels_determine_independent_reference_volume
tests/test_offshore_inventory_release_individual.py::test_equal_states_zero_inventory_release_and_immediate_shutin
tests/test_offshore_inventory_release_individual.py::test_extensive_volume_scaling
tests/test_offshore_inventory_release_individual.py::test_reference_volume_reexpression_preserves_masses
```

## 175. `release.offshore_fast_ledger`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional offshore inventory/feed release ledger with caller-supplied factors.

**공개 API**

- [`pipeline_integrity.offshore_release_ledger.evaluate_offshore_release_ledger`](../src/pipeline_integrity/offshore_release_ledger.py#L33)

**검증 영역:** Conditional initial-volume and pre-shutin-feed ledger with supplied release/reduction factors; no empirical factor calibration.

**적용한 기준:** independent_volume_time_units, initial_plus_feed_budget, immediate_shutin_limit.

**재현할 시험 선택자**

```text
tests/test_offshore_release_ledger_individual.py::test_same_basis_si_aggregation_matches_independent_us_volume_and_time_coordinates
tests/test_offshore_release_ledger_individual.py::test_complete_factor_domain_grid_preserves_initial_plus_feed_volume_budget
tests/test_offshore_release_ledger_individual.py::test_immediate_shutin_has_no_feed_release
```

## 176. `rheology.bingham_circular_laminar`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional homogeneous slurry transport using existing numerical/hydraulic owners.

**공개 API**

- [`pipeline_integrity.slurry_transport.evaluate_laminar_pressure_flow`](../src/pipeline_integrity/slurry_transport.py#L343)
- [`pipeline_integrity.slurry_transport.evaluate_slurry_transport`](../src/pipeline_integrity/slurry_transport.py#L602)

**검증 영역:** Homogeneous fully developed laminar Bingham flow in a circular no-slip pipe with supplied viscosity and yield stress.

**적용한 기준:** independent_yielded_annulus_integral, plug_radius, signed_pressure_flow, dissipative_work.

**재현할 시험 선택자**

```text
tests/test_laminar_rheology_individual_methods.py::test_bingham_inverse_matches_closed_form_and_plug_radius
tests/test_laminar_rheology_individual_methods.py::test_stationary_limit_preserves_yield_pressure_nonuniqueness
tests/test_laminar_radial_profile_individual.py::test_bingham_profile_has_constant_core_continuous_annulus_and_no_slip
```

## 177. `rheology.potapov_yield_turbulent`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Implicit empirical turbulent resistance laws, with explicit domain selection.

**공개 API**

- [`pipeline_integrity.turbulent_constitutive_friction.evaluate_turbulent_resistance`](../src/pipeline_integrity/turbulent_constitutive_friction.py#L59)

## 178. `rheology.power_law_circular_laminar`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional homogeneous slurry transport using existing numerical/hydraulic owners.

**공개 API**

- [`pipeline_integrity.slurry_transport.evaluate_laminar_pressure_flow`](../src/pipeline_integrity/slurry_transport.py#L343)
- [`pipeline_integrity.slurry_transport.evaluate_slurry_transport`](../src/pipeline_integrity/slurry_transport.py#L602)

**검증 영역:** Homogeneous fully developed laminar power-law flow in a circular no-slip pipe with supplied positive constitutive parameters.

**적용한 기준:** independent_velocity_integral, pressure_work, newtonian_limit, signed_flow.

**재현할 시험 선택자**

```text
tests/test_laminar_rheology_individual_methods.py::test_power_law_inverse_matches_integrated_velocity_solution
tests/test_laminar_radial_profile_individual.py::test_power_law_profile_matches_direct_integrated_velocity_law
tests/test_laminar_radial_profile_individual.py::test_newtonian_laminar_limit_is_the_hagen_poiseuille_parabola
```

## 179. `rheology.romanova_powerlaw_turbulent`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Implicit empirical turbulent resistance laws, with explicit domain selection.

**공개 API**

- [`pipeline_integrity.turbulent_constitutive_friction.evaluate_turbulent_resistance`](../src/pipeline_integrity/turbulent_constitutive_friction.py#L59)

## 180. `rigging.prescribed_guy_equilibrium`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Prescribed coplanar guy-load projection and conditional load-share observation.

**공개 API**

- [`pipeline_integrity.guy_projection.evaluate_guy_projection`](../src/pipeline_integrity/guy_projection.py#L122)

**검증 영역:** Single declared coplanar guy and applied resultant with reaction couple; no inferred support capacity.

**적용한 기준:** independent_force_system, virtual_work, rigid_motion, similarity.

**재현할 시험 선택자**

```text
tests/test_guy_projection_individual.py::test_actual_vector_projection_matches_independent_two_equation_force_system
tests/test_guy_projection_individual.py::test_horizontal_virtual_work_converges_from_actual_length_changes
tests/test_guy_projection_individual.py::test_required_couple_and_rotation_virtual_work_do_not_assume_pin_capacity
tests/test_guy_projection_individual.py::test_rigid_rotation_and_translation_preserve_scalar_and_transform_reactions
tests/test_guy_projection_individual.py::test_length_and_force_similarity_have_distinct_moment_dimensions
```

## 181. `rigging.pulley_mechanical_advantage`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Quasistatic reeving force balance with caller supplied per-sheave efficiency.

**공개 API**

- [`pipeline_integrity.reeved_hoist.evaluate_reeved_hoist`](../src/pipeline_integrity/reeved_hoist.py#L25)

**검증 영역:** Quasistatic massless reeved rope with supplied sheave efficiencies; no component capacity claim.

**적용한 기준:** leg_equilibrium, virtual_work, perfect_sheave_limit, load_scaling.

**재현할 시험 선택자**

```text
tests/test_reeved_hoist_individual.py::test_independent_leg_recursion_and_virtual_work
tests/test_reeved_hoist_individual.py::test_perfect_sheaves_exact_limit
tests/test_reeved_hoist_individual.py::test_zero_load_and_missing_displacement_are_distinct
tests/test_reeved_hoist_individual.py::test_force_scaling
```

## 182. `saddle_local_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Caller-calibrated saddle reaction-capacity screening coordinate.

**공개 API**

- [`pipeline_integrity.saddle_capacity_coordinate.evaluate_saddle_capacity`](../src/pipeline_integrity/saddle_capacity_coordinate.py#L15)

## 183. `sensing.electromagnetic_cable_flight`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local leak-position reductions and acoustic/cable input transformations.
- Individual cable-position calculations with explicit path and timing bases.

**공개 API**

- [`pipeline_integrity.leak_inverse_relations.locate_dielectric_cable`](../src/pipeline_integrity/leak_inverse_relations.py#L103)
- [`pipeline_integrity.sensing_location.locate_time_of_flight`](../src/pipeline_integrity/sensing_location.py#L89)

**검증 영역:** Nonmagnetic nondispersive dielectric cable round-trip flight with supplied permittivity and latency.

**적용한 기준:** independent_dielectric_wave_path.

**재현할 시험 선택자**

```text
tests/test_leak_inverse_relations_independent.py::test_dielectric_wave_time_and_existing_range_latency
```

## 184. `sensing.uniform_resistive_cable_coordinate`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Individual cable-position calculations with explicit path and timing bases.

**공개 API**

- [`pipeline_integrity.sensing_location.locate_resistive_cable`](../src/pipeline_integrity/sensing_location.py#L66)

**검증 영역:** Uniform two-wire resistive cable with supplied fixed series resistance and current.

**적용한 기준:** independent_two_wire_network.

**재현할 시험 선택자**

```text
tests/test_sensing_location_individual_local.py::test_resistance_location_inverts_independent_two_wire_network
```

## 185. `shaft.material_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nominal circular-shaft elastic torsion with a supplied shear-stress ceiling.

**공개 API**

- [`pipeline_integrity.shaft_torsion_observer.evaluate_shaft_torsion`](../src/pipeline_integrity/shaft_torsion_observer.py#L42)

**검증 영역:** Circular elastic torsion demand and inverse capacity relative to a caller-supplied nominal shear ceiling; no material strength prediction.

**적용한 기준:** independent_twist_traction_energy, allowable_torque_inverse, rotation_work, zero_speed_limit.

**재현할 시험 선택자**

```text
tests/test_shaft_torsion_individual.py::test_independent_twist_field_section_traction_and_energy_integrals
tests/test_shaft_torsion_individual.py::test_allowable_inverse_is_independently_recovered_from_traction
tests/test_shaft_torsion_individual.py::test_rotation_work_and_energy_gradient_have_distinct_factor_two
tests/test_shaft_torsion_individual.py::test_speed_sign_and_zero_speed_do_not_change_torque_capacity
```

## 186. `shell.elastic_ring_pressure_instability`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nonlinear pressure/ovality equilibrium of an axially uniform elastic ring.

**공개 API**

- [`pipeline_integrity.elastic_shell_ring.evaluate_elastic_shell_ring`](../src/pipeline_integrity/elastic_shell_ring.py#L153)

**검증 영역:** Thin homogeneous elastic circular ring under uniform external pressure with declared axial strip condition; no general shell collapse.

**적용한 기준:** independent_modal_determinant, instability_event, thin_limit, quadrature_refinement, energy_closure.

**재현할 시험 선택자**

```text
tests/test_elastic_ring_critical_pressure_independent_local.py::test_actual_instability_event_matches_independent_extensible_modal_reference
tests/test_elastic_ring_critical_pressure_independent_local.py::test_thickness_refinement_approaches_classical_long_cylinder_limit
tests/test_elastic_ring_critical_pressure_independent_local.py::test_angular_quadrature_refinement_retains_critical_pressure_and_energy_closure
tests/test_elastic_ring_critical_pressure_independent_local.py::test_independent_reference_has_expected_strip_width_independence
```

## 187. `shell.linear_radial_pressure_dynamics`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Selected thin-cylinder modal energy/mass projections through existing owners.
- Uniform radial shell pulse using derived modal properties and existing propagator.
- Exact propagation of explicitly held force stages through the existing oscillator.

**공개 API**

- [`pipeline_integrity.cylindrical_modal_projection.evaluate_cylindrical_modal_projection`](../src/pipeline_integrity/cylindrical_modal_projection.py#L23)
- [`pipeline_integrity.cylindrical_radial_pressure_pulse.evaluate_cylindrical_radial_pressure_pulse`](../src/pipeline_integrity/cylindrical_radial_pressure_pulse.py#L21)
- [`pipeline_integrity.piecewise_force_response.evaluate_generalized_response`](../src/pipeline_integrity/piecewise_force_response.py#L55)

**검증 영역:** Uniform thin isotropic shell radial pressure pulse under declared plane-stress or plane-strain idealization; no contact or nonlinear shell qualification.

**적용한 기준:** independent_membrane_oscillator, pulse_peak, pressure_work_energy.

**재현할 시험 선택자**

```text
tests/test_cylindrical_radial_pressure_pulse_independent.py::test_uniform_pressure_pulse_radial_peak_hoop_coordinate_and_work_balance
```

## 188. `shell.local_normal_membrane_equilibrium`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Local principal-curvature membrane normal-force balance in coherent SI.

**공개 API**

- [`pipeline_integrity.principal_membrane_equilibrium.evaluate_principal_membrane`](../src/pipeline_integrity/principal_membrane_equilibrium.py#L34)

**검증 영역:** Local membrane patch normal equilibrium with supplied principal curvatures and sufficient independent constitutive constraints.

**적용한 기준:** independent_patch_traction_integrals, spherical_cylindrical_limits, anisotropic_mesh_limit, normal_reversal.

**재현할 시험 선택자**

```text
tests/test_principal_membrane_equilibrium_independent.py::test_sphere_cap_pressure_and_boundary_traction
tests/test_principal_membrane_equilibrium_independent.py::test_cylinder_pressure_patch_and_hoop_traction
tests/test_principal_membrane_equilibrium_independent.py::test_infinitesimal_anisotropic_surface_edge_traction_convergence
tests/test_principal_membrane_equilibrium_independent.py::test_normal_reversal_preserves_physical_tension
tests/test_principal_membrane_equilibrium_independent.py::test_thickness_changes_stress_coordinate_not_resultant
tests/test_principal_membrane_equilibrium_independent.py::test_equal_resultant_requires_extra_constraint_evidence
tests/test_principal_membrane_equilibrium_independent.py::test_zero_coefficient_no_arbitrary_unique_solution
tests/test_principal_membrane_equilibrium_independent.py::test_observed_imbalance_is_exposed_not_forced_to_pass
```

## 189. `shell.short_stiff_pipe_supplied_membrane_yield_reference`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Caller-conditioned shell pressure references, distinct from collapse prediction.

**공개 API**

- [`pipeline_integrity.shell_pressure_references.evaluate_intermediate_shell_reference`](../src/pipeline_integrity/shell_pressure_references.py#L81)
- [`pipeline_integrity.shell_pressure_references.evaluate_oval_shell_interaction`](../src/pipeline_integrity/shell_pressure_references.py#L108)
- [`pipeline_integrity.shell_pressure_references.evaluate_short_pipe_hoop_reference`](../src/pipeline_integrity/shell_pressure_references.py#L67)

**검증 영역:** Caller-supplied nominal compressive hoop limit converted through a declared thin membrane half-section force balance; no shell collapse prediction.

**적용한 기준:** independent_half_section_force, geometry_strength_similarity.

**재현할 시험 선택자**

```text
tests/test_shell_pressure_references_independent.py::test_short_pipe_reference_closes_nominal_half_section_hoop_force
tests/test_shell_pressure_references_independent.py::test_short_reference_is_invariant_to_common_geometry_scale_and_linear_in_limit
```

## 190. `shell.supplied_intermediate_collapse_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Caller-conditioned shell pressure references, distinct from collapse prediction.

**공개 API**

- [`pipeline_integrity.shell_pressure_references.evaluate_intermediate_shell_reference`](../src/pipeline_integrity/shell_pressure_references.py#L81)
- [`pipeline_integrity.shell_pressure_references.evaluate_oval_shell_interaction`](../src/pipeline_integrity/shell_pressure_references.py#L108)
- [`pipeline_integrity.shell_pressure_references.evaluate_short_pipe_hoop_reference`](../src/pipeline_integrity/shell_pressure_references.py#L67)

## 191. `shell.supplied_oval_pressure_interaction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Compressive hoop limit from a reviewed normalized hoop/axial yield ellipse.
- Caller-conditioned shell pressure references, distinct from collapse prediction.

**공개 API**

- [`pipeline_integrity.longitudinal_hoop_yield.evaluate_longitudinal_hoop_yield`](../src/pipeline_integrity/longitudinal_hoop_yield.py#L29)
- [`pipeline_integrity.shell_pressure_references.evaluate_intermediate_shell_reference`](../src/pipeline_integrity/shell_pressure_references.py#L81)
- [`pipeline_integrity.shell_pressure_references.evaluate_oval_shell_interaction`](../src/pipeline_integrity/shell_pressure_references.py#L108)
- [`pipeline_integrity.shell_pressure_references.evaluate_short_pipe_hoop_reference`](../src/pipeline_integrity/shell_pressure_references.py#L67)

**검증 영역:** Supplied normalized hoop/axial strength ellipse and nominal pressure coordinate; other oval-collapse correlations are outside this profile.

**적용한 기준:** independent_upper_ellipse_root, J2_principal_invariant, pressure_sign_convention.

**재현할 시험 선택자**

```text
tests/test_longitudinal_hoop_yield_independent.py::test_upper_ellipse_root_and_principal_stress_yield_surface
tests/test_longitudinal_hoop_yield_independent.py::test_od_nominal_pressure_reference_preserves_tensile_pressure_sign_separation
```

## 192. `sleeve_end_fillet_stress`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Thin circumferential sleeve-joint nominal transfer with supplied peak factors.

**공개 API**

- [`pipeline_integrity.sleeve_fillet_nominal_stress.evaluate_sleeve_fillet_nominal_stress`](../src/pipeline_integrity/sleeve_fillet_nominal_stress.py#L16)

**검증 영역:** Nominal thin sleeve fillet section with supplied stress factors and retained effective width; no local weld toe peak prediction.

**적용한 기준:** independent_shear_invariants, circumferential_force, finite_annulus_thin_limit.

**재현할 시험 선택자**

```text
tests/test_sleeve_fillet_nominal_stress_independent.py::test_unit_factors_match_independent_uniform_shear_invariants_and_circumferential_force
tests/test_sleeve_fillet_nominal_stress_independent.py::test_clearance_loss_preserves_force_and_supports_explicit_factor_provider
tests/test_sleeve_fillet_nominal_stress_independent.py::test_nominal_area_approaches_exact_finite_annulus_in_thin_limit
tests/test_sleeve_fillet_nominal_stress_independent.py::test_clearance_cannot_remove_all_effective_width
tests/test_sleeve_fillet_nominal_stress_independent.py::test_declared_thin_geometry_support_is_retained
```

## 193. `smooth_specimen_strain_life`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Explicit smooth-specimen coordinates through the existing fatigue owner.

**공개 API**

- [`pipeline_integrity.strain_life_coordinate.evaluate_strain_life_coordinate`](../src/pipeline_integrity/strain_life_coordinate.py#L24)

**검증 영역:** Supplied fully reversed two-term strain-life law with unequal exponents inside its reviewed cycle interval; no material fit.

**적용한 기준:** independent_quadratic_inverse, strain_range_amplitude, reversal_cycle_consistency.

**재현할 시험 선택자**

```text
tests/test_strain_life_coordinate_independent.py::test_quadratic_inverse_and_coordinate_consistency
```

## 194. `soil.surface_load_and_pipe_contact`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Observe a reviewed circular elastic strip on an isotropic linear foundation.
- Vertical stress transfer in a homogeneous elastic half-space.

**공개 API**

- [`pipeline_integrity.supported_ring_observer.evaluate_supported_ring`](../src/pipeline_integrity/supported_ring_observer.py#L41)
- [`pipeline_integrity.surface_point_load_transfer.evaluate_surface_point_load_transfer`](../src/pipeline_integrity/surface_point_load_transfer.py#L12)

**검증 영역:** Homogeneous isotropic elastic half-space point-load transfer and its prescribed circular footprint integral; pipe-ring contact variant remains outside this profile.

**적용한 기준:** independent_displacement_constitutive_stress, integrated_force_conservation, circular_footprint_limit, similarity.

**재현할 시험 선택자**

```text
tests/test_surface_point_load_transfer_independent.py::test_independent_boussinesq_displacement_constitutive_stress
tests/test_surface_point_load_transfer_independent.py::test_horizontal_plane_integrated_stress_transmits_full_vertical_force
tests/test_surface_point_load_transfer_independent.py::test_integrated_uniform_circular_footprint_matches_independent_axis_solution
tests/test_surface_point_load_transfer_independent.py::test_force_length_similarity_and_zero_load
```

## 195. `spill.constant_feed_viscous_spread`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Bounded constant-feed pool and pressure-driven spherical porous-front models.

**공개 API**

- [`pipeline_integrity.porous_release.evaluate_surface_viscous_spill`](../src/pipeline_integrity/porous_release.py#L35)

**검증 영역:** Declared constant-feed gravity/viscous similarity law; dimensional consistency and input-volume budget only. Empirical spreading coefficient is not independently calibrated.

**적용한 기준:** dimensional_similarity, supplied_volume_budget, zero_time_limit.

**재현할 시험 선택자**

```text
tests/test_porous_release_independent.py::test_surface_similarity_value_and_zero
tests/test_porous_release_independent.py::test_surface_feed_dimensional_scaling
tests/test_physical_similarity_223.py::test_viscous_spill_feed_viscosity_similarity_and_supplied_inventory
```

## 196. `strain_rate_strength_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Explicitly calibrated dynamic-strength and vent-area scalar coordinates.

**공개 API**

- [`pipeline_integrity.reviewed_dynamic_correlations.evaluate_dynamic_strength_coordinate`](../src/pipeline_integrity/reviewed_dynamic_correlations.py#L19)

## 197. `support_force_movement`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- A linear mass, viscous damper and spring under a prescribed cosine force.

**공개 API**

- [`pipeline_integrity.harmonic_response.frequency_response`](../src/pipeline_integrity/harmonic_response.py#L59)

**검증 영역:** Linear single-degree harmonic response with declared positive mass/stiffness/damping; no support capacity or nonlinear contact.

**적용한 기준:** independent_steady_IVP, resonance_limit, critical_decay, energy_conservation, static_high_frequency_limits.

**재현할 시험 선택자**

```text
tests/test_harmonic_response_independent_local.py::test_frequency_response_matches_separately_integrated_steady_oscillator
tests/test_harmonic_response_independent_local.py::test_static_resonance_and_high_frequency_limits_and_phase_quadrant
tests/test_harmonic_response_independent_local.py::test_si_parameter_scaling_and_output_clock_do_not_change_trajectory
```

## 198. `thermal.free_linear_expansion`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Thermal and inventory budgets with explicit boundary states.
- Temperature-dependent tangent expansion curves connected to the material owner.

**공개 API**

- [`pipeline_integrity.models.thermal.expansion`](../src/pipeline_integrity/models/thermal.py#L130)
- [`pipeline_integrity.thermal_expansion.evaluate_curve_thermal_frame`](../src/pipeline_integrity/thermal_expansion.py#L79)
- [`pipeline_integrity.thermal_expansion.integrate_thermal_expansion`](../src/pipeline_integrity/thermal_expansion.py#L33)

**검증 영역:** Reference-length thermal expansion with supplied alpha and signed temperature histories; no restrained stress or logarithmic strain claim.

**적용한 기준:** independent_temperature_history_ode, reference_strain_integral.

**재현할 시험 선택자**

```text
tests/test_free_expansion_path_individual.py::test_nonlinear_temperature_history_ode
tests/test_free_expansion_path_individual.py::test_reference_strain_integral_not_multiplicative_reanchoring
```

## 199. `thermal.gas_regulator_joule_thomson`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Shared checked gas-state observations and adiabatic pressure-reduction states.

**공개 API**

- [`pipeline_integrity.gas_thermal_observation.observe_gas_pressure_reduction`](../src/pipeline_integrity/gas_thermal_observation.py#L61)

**검증 영역:** Steady single-phase isenthalpic throttling using declared gas EOS; no kinetic, elevation or heat/work terms.

**적용한 기준:** independent_isenthalpic_differential_path, ideal_caloric_limit, zero_pressure_change.

**재현할 시험 선택자**

```text
tests/test_gas_pressure_reduction_individual.py::test_ideal_pressure_independent_caloric_enthalpy_has_no_temperature_change
tests/test_gas_pressure_reduction_individual.py::test_actual_EOS_matches_independent_isenthalpic_differential_path
tests/test_gas_pressure_reduction_individual.py::test_zero_pressure_change_has_zero_drop_without_a_cooling_floor
```

## 200. `thermal.ground_heat_diffusion`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- P1 soil heat exchange assembled by scikit-fem on reviewed polygon cavities.

**공개 API**

- [`pipeline_integrity.soil_fem.evaluate_soil_fem`](../src/pipeline_integrity/soil_fem.py#L981)

**검증 영역:** P1 two-dimensional soil heat diffusion on supported rectangular/polygon cavity geometries with supplied conductivity and capacity.

**적용한 기준:** independent_separated_heat_mode, spatial_convergence, time_convergence, heat_storage_flux_budget, constant_temperature_limit.

**재현할 시험 선택자**

```text
tests/test_ground_heat_equation_individual.py::test_2d_heat_equation_analytic_mode_spatial_convergence
tests/test_ground_heat_equation_individual.py::test_transient_heat_time_refinement_fixed_mesh
tests/test_ground_heat_equation_individual.py::test_steady_linear_2d_heat_flux_and_constant_temperature_preservation
tests/test_ground_heat_equation_individual.py::test_signed_heat_flux_integrated_storage_and_zero_flux_boundary_ledger
```

## 201. `thermal.steady_radial_conduction`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Fixed-surface radial temperature outputs through the existing layer engine.

**공개 API**

- [`pipeline_integrity.radial_temperature_profile.evaluate_radial_temperature_profile`](../src/pipeline_integrity/radial_temperature_profile.py#L56)

**검증 영역:** Steady one-dimensional cylindrical conduction with supplied constant layer conductivities and fixed surface temperatures.

**적용한 기준:** radial_equilibrium_solution, heat_budget, layer_subdivision_invariance, signed_heat_reversal, isothermal_limit.

**재현할 시험 선택자**

```text
tests/test_radial_temperature_profile_independent.py::test_profile_and_heat_match_independent_radial_equilibrium
tests/test_radial_temperature_profile_independent.py::test_interface_subdivision_changes_sampling_without_changing_heat
tests/test_radial_temperature_profile_independent.py::test_temperature_reversal_reverses_heat_without_clipping
tests/test_radial_temperature_profile_independent.py::test_uniform_surface_temperature_has_no_conduction_rate
```

## 202. `thermo.cubic_gas_eos`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Composition-aware gas state observations and fixed-mass endpoint coordinates.
- Open thermodynamic provider with explicit state pairs and phase boundaries.

**공개 API**

- [`pipeline_integrity.gas_state_observer.observe_gas_state`](../src/pipeline_integrity/gas_state_observer.py#L72)
- [`pipeline_integrity.thermo.CoolPropProvider.resolve`](../src/pipeline_integrity/thermo.py#L121)

**검증 영역:** Pure-fluid PR/SRK gas states above critical temperature; independent cubic volume roots. PR nitrogen PT auto-density failure is retained and the supported T,Dmass pair is separately checked; no measured EOS calibration.

**적용한 기준:** independent_primitive_pressure_volume_root, phase_domain, independent_state_pair_pressure_recovery, native_PT_failure_not_silently_accepted.

**재현할 시험 선택자**

```text
tests/test_cubic_eos_independent.py::test_pure_supercritical_temperature_state_matches_independent_cubic_volume_root
```

## 203. `thermo.ideal_gas_state`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Composition-aware gas state observations and fixed-mass endpoint coordinates.

**공개 API**

- [`pipeline_integrity.gas_state_observer.observe_gas_state`](../src/pipeline_integrity/gas_state_observer.py#L72)

**검증 영역:** Ideal-gas composition density and closed-mass endpoint coordinate transformations; no nonideal EOS.

**적용한 기준:** mole_mass_basis, Boyle_Charles_law, closed_mass_inventory, extensivity.

**재현할 시험 선택자**

```text
tests/test_gas_state_observer_independent.py::test_ideal_forward_density_uses_mole_or_mass_basis_without_g_per_mol_confusion
tests/test_gas_state_observer_independent.py::test_closed_mass_endpoint_pressure_matches_boyle_charles_and_exact_molar_count
tests/test_gas_state_observer_independent.py::test_closed_mass_inverse_volume_and_amount_extensivity
```

## 204. `thermo.real_gas_state`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Composition-aware gas state observations and fixed-mass endpoint coordinates.
- Open thermodynamic provider with explicit state pairs and phase boundaries.

**공개 API**

- [`pipeline_integrity.gas_state_observer.observe_gas_state`](../src/pipeline_integrity/gas_state_observer.py#L72)
- [`pipeline_integrity.thermo.CoolPropProvider.resolve`](../src/pipeline_integrity/thermo.py#L121)

**검증 영역:** Reviewed HEOS single-phase gas states and fixed-mass endpoint transforms; thermodynamic consistency and dilute limits, not EOS calibration.

**적용한 기준:** independent_ideal_dilute_limit, heat_capacity_derivative_identity, closed_mass_budget, composition_basis.

**재현할 시험 선택자**

```text
tests/test_gas_state_observer_independent.py::test_real_dilute_limit_matches_independent_molar_ideal_law
tests/test_gas_state_observer_independent.py::test_real_density_derivatives_obey_independent_heat_capacity_identity
tests/test_gas_state_observer_independent.py::test_real_pure_closed_mass_volume_inverse_exposes_pressure_without_energy_path_claim
tests/test_gas_state_observer_independent.py::test_mixture_molar_mass_and_dilute_density_do_not_use_unweighted_species_average
tests/test_gas_state_observer_independent.py::test_mixture_closed_volume_inverse_requires_reviewed_bracket_then_preserves_mass
tests/test_gas_state_observer_independent.py::test_heos_mass_fraction_coordinate_reuses_existing_composition_ledger
```

## 205. `thermoelasticity.radial_cylinder`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Small-strain axisymmetric thermoelastic cylinder with declared axial boundary.

**공개 API**

- [`pipeline_integrity.cylindrical_thermal_stress.evaluate_cylindrical_thermal_stress`](../src/pipeline_integrity/cylindrical_thermal_stress.py#L28)

**검증 영역:** Small-strain isotropic axisymmetric thermoelastic cylinder with prescribed temperature field and axial restraint or force.

**적용한 기준:** independent_displacement_and_stress_BVP, traction_equilibrium, axial_force_integral, temperature_partition_convergence, free_expansion_limit.

**재현할 시험 선택자**

```text
tests/test_cylindrical_thermal_stress_independent.py::test_uniform_temperature_lame_pressure_and_axial_boundary_limit
tests/test_cylindrical_thermal_stress_independent.py::test_uniform_free_expansion_has_no_stress
tests/test_cylindrical_thermal_stress_independent.py::test_quadratic_thermal_particular_solution
tests/test_cylindrical_thermal_stress_independent.py::test_log_temperature_input_partition_refines_to_analytic_field
tests/test_cylindrical_thermal_stress_independent.py::test_radial_equilibrium_traction_and_axial_integral
tests/test_cylindrical_thermal_stress_independent.py::test_independent_stress_primary_bvp_matches_temperature_moment_solution
```

## 206. `thermoelasticity.restrained_cylinder`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Uniform-temperature zero-axial-strain cylinder through existing material owners.

**공개 API**

- [`pipeline_integrity.fully_restrained_cylinder.evaluate_fully_restrained_cylinder`](../src/pipeline_integrity/fully_restrained_cylinder.py#L22)

**검증 영역:** Uniform-temperature linear isotropic cylinder with zero axial strain and zero exterior pressure; no plasticity or creep.

**적용한 기준:** independent_displacement_BVP, axial_compatibility, end_force, thin_membrane_limit.

**재현할 시험 선택자**

```text
tests/test_fully_restrained_cylinder_independent.py::test_displacement_boundary_solution_axial_compatibility_and_end_force
tests/test_fully_restrained_cylinder_independent.py::test_exact_annulus_pressure_component_converges_to_od_membrane_source_coordinate
```

## 207. `torsion.annular_elastic_section`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Nominal circular-shaft elastic torsion with a supplied shear-stress ceiling.

**공개 API**

- [`pipeline_integrity.shaft_torsion_observer.evaluate_shaft_torsion`](../src/pipeline_integrity/shaft_torsion_observer.py#L42)

**검증 영역:** Saint-Venant linear elastic torsion of supported hollow circular shafts with declared section and stiffness.

**적용한 기준:** independent_section_traction_integral, twist_compliance, work_energy, allowable_inverse.

**재현할 시험 선택자**

```text
tests/test_shaft_torsion_individual.py::test_independent_twist_field_section_traction_and_energy_integrals
tests/test_shaft_torsion_individual.py::test_allowable_inverse_is_independently_recovered_from_traction
tests/test_shaft_torsion_individual.py::test_rotation_work_and_energy_gradient_have_distinct_factor_two
tests/test_shaft_torsion_individual.py::test_speed_sign_and_zero_speed_do_not_change_torque_capacity
```

## 208. `transport.ideal_sensor_tube_parcel`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Ideal passive-parcel transit in a sensing tube at declared constant air speed.

**공개 API**

- [`pipeline_integrity.vapor_tube.evaluate_vapor_tube_delay`](../src/pipeline_integrity/vapor_tube.py#L66)
- [`pipeline_integrity.vapor_tube.locate_vapor_tube_parcel`](../src/pipeline_integrity/vapor_tube.py#L103)

**검증 영역:** Passive nondispersing parcel at declared constant tube airflow with explicit purge timing and detector latency.

**적용한 기준:** independent_constant_velocity_motion, purge_wait_once, one_way_clock.

**재현할 시험 선택자**

```text
tests/test_vapor_tube_individual_local.py::test_continuous_advection_matches_independent_constant_velocity_motion
tests/test_vapor_tube_individual_local.py::test_single_purge_uses_supplied_residual_wait_once
tests/test_vapor_tube_individual_local.py::test_parcel_location_has_one_way_airflow_start_clock_and_latency
```

## 209. `uniaxial_constitutive_coordinate`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Normalized monotonic elastic-plus-power coordinates through the material owner.

**공개 API**

- [`pipeline_integrity.normalized_constitutive_coordinate.evaluate_normalized_constitutive_coordinate`](../src/pipeline_integrity/normalized_constitutive_coordinate.py#L42)

**검증 영역:** Supplied normalized monotonic power-strain coordinate law in its reviewed calibration interval; no fitted material parameters.

**적용한 기준:** independent_inverse_root, conjugate_tangent_work_integral, stress_unit_scaling.

**재현할 시험 선택자**

```text
tests/test_normalized_constitutive_coordinate_independent.py::test_native_coordinate_recovers_independently_inverted_normalized_curve
tests/test_normalized_constitutive_coordinate_independent.py::test_monotonic_work_coordinate_matches_independent_tangent_integral
tests/test_normalized_constitutive_coordinate_independent.py::test_zero_stress_and_consistent_stress_unit_scaling
tests/test_normalized_constitutive_coordinate_independent.py::test_curve_support_is_not_extrapolated
```

## 210. `vacuum.pumpdown_time`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Isothermal lumped gas pumpdown with series conductance and supplied gas load.

**공개 API**

- [`pipeline_integrity.vacuum_pumpdown.evaluate_vacuum_pumpdown`](../src/pipeline_integrity/vacuum_pumpdown.py#L47)

**검증 영역:** Isothermal fixed-volume ideal gas with supplied effective pumping/line conductance and constant loading.

**적용한 기준:** independent_throughput_ODE, integrated_gas_mass, target_floor_event, clock_scaling.

**재현할 시험 선택자**

```text
tests/test_vacuum_pumpdown_individual.py::test_series_resistance_and_one_time_constant_target
tests/test_vacuum_pumpdown_individual.py::test_independent_throughput_ode_and_integrated_mass_budget
tests/test_vacuum_pumpdown_individual.py::test_zero_pumping_or_closed_line_no_target_and_linear_gas_loading
tests/test_vacuum_pumpdown_individual.py::test_target_semantics_and_asymptotic_floor
tests/test_vacuum_pumpdown_individual.py::test_equal_initial_and_asymptotic_pressure_is_stationary
tests/test_vacuum_pumpdown_individual.py::test_gas_load_can_raise_pressure_despite_running_pump
tests/test_vacuum_pumpdown_individual.py::test_volume_speed_and_gas_load_common_scaling
tests/test_vacuum_pumpdown_individual.py::test_clock_origin_translation_preserved
tests/test_vacuum_pumpdown_individual.py::test_short_time_positive_pump_inventory_with_loading
tests/test_vacuum_pumpdown_individual.py::test_small_positive_long_time_pressure_not_cancelled_to_zero
```

## 211. `vacuum.water_evaporation_time`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional isothermal free-water depletion on a pure-water vacuum plateau.

**공개 API**

- [`pipeline_integrity.vacuum_water_evaporation.evaluate_vacuum_water_evaporation`](../src/pipeline_integrity/vacuum_water_evaporation.py#L74)

**검증 영역:** Declared rigid-volume liquid/vapor saturation plateau with supplied heat and pumping capacities until depletion; no later transient phase path.

**적용한 기준:** independent_mass_volume_system, ODE_depletion_event, open_energy_budget, latent_heat_limit, enthalpy_origin_invariance.

**재현할 시험 선택자**

```text
tests/test_vacuum_water_evaporation_individual.py::test_independent_mass_volume_linear_system_and_ode_depletion_event
tests/test_vacuum_water_evaporation_individual.py::test_finite_net_heat_throttles_pump_without_inventing_thermal_transport
tests/test_vacuum_water_evaporation_individual.py::test_exact_open_mass_and_energy_budgets_with_finite_vapor_storage
tests/test_vacuum_water_evaporation_individual.py::test_event_stop_does_not_extend_constant_pressure_plateau_after_depletion
tests/test_vacuum_water_evaporation_individual.py::test_existing_series_conductance_and_zero_isolation
tests/test_vacuum_water_evaporation_individual.py::test_source_removal_only_time_is_recovered_in_dilute_vapor_limit
tests/test_vacuum_water_evaporation_individual.py::test_geometry_scaling_clock_and_zero_initial_inventory
tests/test_vacuum_water_evaporation_individual.py::test_ice_critical_and_nonvacuum_boundaries
tests/test_vacuum_water_evaporation_individual.py::test_native_water_plateau_independent_rigid_volume_energy_and_mass
tests/test_vacuum_water_evaporation_individual.py::test_common_enthalpy_reference_shift_preserves_time_heat_and_open_energy_balance
```

## 212. `valve.cavitation_margin`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Separate liquid choking and declared cavitation-inception pressure criteria.

**공개 API**

- [`pipeline_integrity.liquid_valve_pressure_limits.evaluate_liquid_valve_pressure_limits`](../src/pipeline_integrity/liquid_valve_pressure_limits.py#L50)

**검증 영역:** Declared nominal recovery proxy and separate choking pressure comparison; not onset of actual cavitation damage.

**적용한 기준:** independent_pressure_budgets, separate_inception_choking_criteria, zero_drop_limit.

**재현할 시험 선택자**

```text
tests/test_liquid_valve_pressure_limits_independent.py::test_nominal_inception_proxy_and_separate_choke_budget
tests/test_liquid_valve_pressure_limits_independent.py::test_zero_pressure_drop_keeps_the_index_undefined_and_both_finite_limit_comparisons_false
```

## 213. `valve.gas_modern_terminal_ratio`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Forward equal-bore turbulent gas capacity through the existing sizing kernel.
- Explicit gas reference-flow normalization and conventional valve coefficients.
- Curated library calls with declared operating and coefficient conventions.

**공개 API**

- [`pipeline_integrity.gas_valve_forward_capacity.evaluate_gas_valve_forward_capacity`](../src/pipeline_integrity/gas_valve_forward_capacity.py#L37)
- [`pipeline_integrity.gas_valve_reference.normalize_gas_reference_flow`](../src/pipeline_integrity/gas_valve_reference.py#L44)
- [`pipeline_integrity.models.library_models.gas_control_valve`](../src/pipeline_integrity/models/library_models.py#L130)

**검증 영역:** Declared equal-bore turbulent gas valve capacity law with supplied terminal/recovery factors; no actual device coefficient calibration.

**적용한 기준:** independent_engineering_unit_capacity, reference_mass_volume_conversion, choked_backpressure_plateau.

**재현할 시험 선택자**

```text
tests/test_gas_valve_forward_capacity_independent.py::test_forward_mass_and_normal_flow_match_separate_engineering_unit_expression
tests/test_gas_valve_forward_capacity_independent.py::test_conventional_cv_input_recovers_the_same_physical_capacity_after_exact_unit_conversion
tests/test_gas_valve_forward_capacity_independent.py::test_forward_choked_mass_capacity_is_a_backpressure_plateau
tests/test_gas_valve_forward_capacity_independent.py::test_zero_declared_coefficient_has_zero_model_flow_without_inventing_a_choking_state
```

## 214. `valve.liquid_capacity`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Forward liquid flow from a supplied conventional valve coefficient.
- Separate liquid choking and declared cavitation-inception pressure criteria.
- Curated library calls with declared operating and coefficient conventions.

**공개 API**

- [`pipeline_integrity.liquid_valve_forward_capacity.evaluate_liquid_valve_forward_capacity`](../src/pipeline_integrity/liquid_valve_forward_capacity.py#L35)
- [`pipeline_integrity.liquid_valve_pressure_limits.evaluate_liquid_valve_pressure_limits`](../src/pipeline_integrity/liquid_valve_pressure_limits.py#L50)
- [`pipeline_integrity.models.library_models.liquid_control_valve`](../src/pipeline_integrity/models/library_models.py#L78)

**검증 영역:** Supplied conventional liquid valve coefficient with explicit equal-bore recovery pressure budget; no measured device calibration.

**적용한 기준:** available_pressure_budget, recovery_boundary_continuity, choked_backpressure_plateau, flow_factor_scaling.

**재현할 시험 선택자**

```text
tests/test_liquid_valve_recovery_limit_individual.py::test_equal_bore_capacity_matches_explicit_available_pressure_budget
tests/test_liquid_valve_recovery_limit_individual.py::test_recovery_boundary_from_either_side_is_continuous_with_changed_choke_flag
tests/test_liquid_valve_recovery_limit_individual.py::test_decreasing_downstream_pressure_below_choke_does_not_raise_capacity
tests/test_liquid_valve_recovery_limit_individual.py::test_choked_coefficient_scales_with_requested_flow_and_inverse_recovery_factor
```

## 215. `valve.steam_superheat_sizing`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conditional single-phase steam nozzle capacity using the actual water EOS.

**공개 API**

- [`pipeline_integrity.steam_nozzle.evaluate_steam_nozzle`](../src/pipeline_integrity/steam_nozzle.py#L62)

**검증 영역:** Single-phase superheated steam ideal isentropic effective-area nozzle; no actual valve discharge coefficient or wet-steam claim.

**적용한 기준:** independent_PT_first_law_ODE, flux_maximum_sonic_event, variable_cp_limit, enthalpy_entropy_origin_invariance, area_extensivity.

**재현할 시험 선택자**

```text
tests/test_steam_nozzle_individual.py::test_constant_cp_limit_against_analytic_nozzle_flux
tests/test_steam_nozzle_individual.py::test_actual_water_against_independent_PT_thermodynamic_ODE_and_flux_maximum
tests/test_steam_nozzle_individual.py::test_actual_water_unchoked_PT_path_energy_integral
tests/test_steam_nozzle_individual.py::test_actual_industrial_water_backend_selected_gas_case
tests/test_steam_nozzle_individual.py::test_variable_cp_ideal_fixture_against_energy_ODE
tests/test_steam_nozzle_individual.py::test_effective_area_extensivity_and_mass_flow_area_inverse
tests/test_steam_nozzle_individual.py::test_choked_capacity_does_not_need_an_unreviewed_downstream_expansion
tests/test_steam_nozzle_individual.py::test_enthalpy_entropy_reference_shift_invariance
tests/test_steam_nozzle_individual.py::test_small_pressure_drive_converges_to_incompressible_kinetic_limit
tests/test_steam_nozzle_individual.py::test_zero_drive_and_zero_requested_mass_are_explicit
```

## 216. `valve.supplied_liquid_pressure_limits`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Separate liquid choking and declared cavitation-inception pressure criteria.

**공개 API**

- [`pipeline_integrity.liquid_valve_pressure_limits.evaluate_liquid_valve_pressure_limits`](../src/pipeline_integrity/liquid_valve_pressure_limits.py#L50)

**검증 영역:** Caller-supplied cavitation index basis and declared pressure-state limits; no inferred vapor fraction or measured inception threshold.

**적용한 기준:** supplied_index_basis, pressure_condition_interpretation.

**재현할 시험 선택자**

```text
tests/test_liquid_valve_pressure_limits_independent.py::test_supplied_index_has_an_explicit_different_basis_from_the_recovery_factor
tests/test_liquid_valve_pressure_limits_independent.py::test_below_vapour_outlet_is_only_a_pressure_condition_not_an_inferred_phase_fraction
```

## 217. `vent.supplied_empirical_area_correlation`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Explicitly calibrated dynamic-strength and vent-area scalar coordinates.

**공개 API**

- [`pipeline_integrity.reviewed_dynamic_correlations.evaluate_deflagration_vent_coordinate`](../src/pipeline_integrity/reviewed_dynamic_correlations.py#L49)

## 218. `vortex_lock_in_response`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 미완료 — 독립 기준 또는 비교자료 필요.

- Supplied-calibration lock-in amplitude and bending-stress coordinates.

**공개 API**

- [`pipeline_integrity.vortex_lockin_coordinate.evaluate_vortex_lockin_coordinate`](../src/pipeline_integrity/vortex_lockin_coordinate.py#L23)

## 219. `waves.cylindrical_scalar_wave`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Regular axisymmetric scalar-wave modes through SciPy Bessel functions.

**공개 API**

- [`pipeline_integrity.cylindrical_scalar_wave_mode.evaluate_cylindrical_scalar_wave_mode`](../src/pipeline_integrity/cylindrical_scalar_wave_mode.py#L17)

**검증 영역:** Regular axisymmetric scalar wave with outer zero-displacement boundary and supplied speed; no elastic solid vector modes.

**적용한 기준:** independent_annular_FV_eigenvalue_convergence, wave_energy_integral, boundary_conditions.

**재현할 시험 선택자**

```text
tests/test_cylindrical_scalar_wave_mode_independent.py::test_independent_annular_finite_volume_eigenvalue_convergence
tests/test_cylindrical_scalar_wave_mode_independent.py::test_independent_scalar_wave_energy_integral_and_boundaries
```

## 220. `waves.prescribed_one_dimensional_travel_time`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Paired waveform delay estimates and bounded, conditional one-path pipe locations.
- Local leak-position reductions and acoustic/cable input transformations.

**공개 API**

- [`pipeline_integrity.acoustics.locate_on_span`](../src/pipeline_integrity/acoustics.py#L313)
- [`pipeline_integrity.leak_inverse_relations.locate_from_known_interface_reflection`](../src/pipeline_integrity/leak_inverse_relations.py#L75)

**검증 영역:** Known constant-pressure reflection interface and uniform one-dimensional wave speed; no unknown multipath inverse.

**적용한 기준:** independent_direct_reflected_path_geometry.

**재현할 시험 선택자**

```text
tests/test_leak_inverse_relations_independent.py::test_reflection_independent_direct_and_reflected_paths
```

## 221. `weld_group_section_stress`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Elastic equal-stiffness planar weld-line force/moment distribution.

**공개 API**

- [`pipeline_integrity.weld_line_group.evaluate_weld_line_group`](../src/pipeline_integrity/weld_line_group.py#L18)

**검증 영역:** Linear nominal weld-line group stresses from declared throat geometry and complete force/moment resultants; no weld strength or fatigue acceptance.

**적용한 기준:** independent_six_resultant_line_integral, rectangular_line_section_modulus.

**재현할 시험 선택자**

```text
tests/test_weld_line_group_independent.py::test_independent_line_integral_recovers_all_six_resultants
tests/test_weld_line_group_independent.py::test_rectangular_line_section_modulus_and_orthogonal_resultant
```

## 222. `welding.supplied_burnthrough_limit`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- Conservative radial pipe-wall heating with an inner gas film boundary.

**공개 API**

- [`pipeline_integrity.weld_wall_thermal_limit.evaluate_weld_wall_thermal_limit`](../src/pipeline_integrity/weld_wall_thermal_limit.py#L39)

**검증 영역:** Conservative constant-property radial wall heating with prescribed outer flux and inner film; no melting or actual weld burn-through acceptance.

**적용한 기준:** independent_steady_log_conduction, film_heat_balance, lumped_energy_ODE_limit, time_refinement.

**재현할 시험 선택자**

```text
tests/test_remaining_physical_limits_independent.py::test_weld_wall_steady_log_profile_and_gas_heat_balance
tests/test_remaining_physical_limits_independent.py::test_weld_wall_high_conductivity_time_refinement_matches_lumped_energy_ode
```

## 223. `yield.pressurized_isotropic_cylinder`

**구현:** 연결 경로 확인 완료. **독립 프로필:** 명시된 제한 영역 통과.

- First elastic von-Mises threshold via the existing closed-cylinder solver.
- Uniform-temperature zero-axial-strain cylinder through existing material owners.
- Compressive hoop limit from a reviewed normalized hoop/axial yield ellipse.
- Pressure-only first elastic J2 yield with prescribed zero axial strain.

**공개 API**

- [`pipeline_integrity.elastic_first_yield.evaluate_elastic_cylinder_first_yield`](../src/pipeline_integrity/elastic_first_yield.py#L60)
- [`pipeline_integrity.fully_restrained_cylinder.evaluate_fully_restrained_cylinder`](../src/pipeline_integrity/fully_restrained_cylinder.py#L22)
- [`pipeline_integrity.longitudinal_hoop_yield.evaluate_longitudinal_hoop_yield`](../src/pipeline_integrity/longitudinal_hoop_yield.py#L29)
- [`pipeline_integrity.restrained_first_yield.evaluate_restrained_first_yield`](../src/pipeline_integrity/restrained_first_yield.py#L26)

**검증 영역:** Closed-end isotropic elastic cylinder first J2 yield under supported pressure loading and supplied yield strength; no plastic collapse.

**적용한 기준:** independent_radial_BVP, first_yield_inverse, hydrostatic_shift_invariance, thin_limit.

**재현할 시험 선택자**

```text
tests/test_elastic_first_yield_independent.py::test_first_yield_matches_radial_boundary_problem_and_occurs_at_bore
tests/test_elastic_first_yield_independent.py::test_common_hydrostatic_pressure_does_not_change_closed_end_first_yield
tests/test_elastic_first_yield_independent.py::test_yield_strength_scales_threshold_without_changing_critical_radius
tests/test_elastic_first_yield_independent.py::test_full_geometry_solution_converges_to_thin_wall_first_yield_limit
tests/test_elastic_first_yield_independent.py::test_unbracketed_threshold_is_not_reported_as_a_failed_material_or_as_zero_pressure
```

