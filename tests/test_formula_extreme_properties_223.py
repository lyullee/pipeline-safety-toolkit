"""Executed independent formula references and valid extreme limits for42 units.

Selected existing authored reference tests are reused without changing their
assertions. New models receive explicit analytic cases below. Model membership
and formula/extreme kinds are attached to each actually executed JUnit case.
"""
from __future__ import annotations

from functools import wraps
import importlib.util
import json
import math
import os
from pathlib import Path
import sys

import pytest

from pipeline_integrity.anode_lifetime import AnodeLifetimeRequest, evaluate_anode_lifetime
from pipeline_integrity.calibrated_emissions import (
    CascadeSourceRequest, FillingVaporRequest, TankLossRequest,
    evaluate_cascade_source, evaluate_filling_vapor, evaluate_tank_standing_working_loss,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.groundbed_fv import ConductiveEarthRequest, evaluate_conductive_earth
from pipeline_integrity.saturated_gas_fugacity import SaturatedGasFugacityRequest, observe_saturated_gas_fugacity
from pipeline_integrity.slick_observation import SlickObservationRequest, evaluate_slick_observation

from test_input_properties_223 import BASELINES as INPUT_BASELINES

OWNER_SOURCE = os.environ.get('PIPELINE_FORMULA_MATERIAL_SRC')
if OWNER_SOURCE:
    if not Path(OWNER_SOURCE).is_dir():
        raise RuntimeError('PIPELINE_FORMULA_MATERIAL_SRC must identify the actual owner source directory')
    sys.path.insert(0, OWNER_SOURCE)


def member(units, kind):
    def decorate(function):
        function._canonical_units = ['proposal.'+u for u in units]
        function._test_kind = kind
        return function
    return decorate


@pytest.fixture(autouse=True)
def annotate_executed_case(request):
    units = getattr(request.function, '_canonical_units', None)
    if units:
        request.node.user_properties.append(('canonical_units', json.dumps(units)))
        request.node.user_properties.append(('test_kind', request.function._test_kind))


@pytest.fixture
def material_client():
    # Use the actual owner's registry and laws, selecting only laws required by
    # this campaign. No stand-in evaluator or copied material formula is used.
    from matintegrity.contracts import Evidence, RunContext as OwnerContext
    from matintegrity.models.inspection import specifications as inspection_specs
    from matintegrity.models.protection import specifications as protection_specs
    from matintegrity.registry import ModelRegistry
    from pipeline_integrity.adapters import MaterialClient
    registry = ModelRegistry()
    wanted = {'inspection.ultrasonic_thickness', 'protection.cathodic_budget'}
    for spec in (*inspection_specs(), *protection_specs()):
        if spec.model_id in wanted:
            registry.register(spec)
    return MaterialClient(evaluator=registry.run, context_factory=OwnerContext,
                          evidence_factory=Evidence, registry_factory=lambda: registry)


@pytest.fixture(autouse=True)
def actual_owner_for_selected_reference(request, monkeypatch):
    units = getattr(request.function, '_canonical_units', ())
    if 'proposal.metrology.ultrasonic_wall_thickness' in units:
        client = request.getfixturevalue('material_client')
        monkeypatch.setattr('pipeline_integrity.nde.MaterialClient', lambda: client)


# module, canonical units, independent formula functions, valid extreme functions
SELECTIONS = [
 ('electrical_circuit_individual', ['electrical.conductor_impedance_drop','electrical.dc_ac_power_balance'],
  ['independent_periodic_power_and_line_heat','independent_rectangular_phasor_kvl'], ['zero_load_and_unknown_conversion_are_preserved']),
 ('electrical_circuit_individual', ['electrical.power_factor_compensation'],
  ['reactive_budget_from_independent_three_four_five_triangle'], ['reactive_compensation_is_extensive_and_zero_load_limit']),
 ('dc_tapped_cable_individual', ['electrical.cp_current_voltage_balance'],
  ['ohmic_driver_from_independent_two_resistor_Kirchhoff_node','segment_pickup_matches_independent_distributed_current_flux_integral'],
  ['zero_resistance_and_at_bus_tap_are_exact_limits']),
 ('distributed_pipe_cp_individual', ['electrical.distributed_pipeline_cp'],
  ['resistor_ladder_refinement_converges_to_potential_current_and_power','local_Kirchhoff_derivatives_and_independent_integrated_current_and_heat'],
  ['perfect_coating_is_exact_series_resistor_with_no_ground_current','leakage_to_zero_limit_and_long_line_to_semi_infinite_limit']),
 ('two_state_current_individual', ['electrical.two_state_current_rms'],
  ['independent_sinusoidal_state_waveforms_and_cycle_joule_heat'], ['zero_and_equal_currents_and_zero_resistance']),
 ('ground_halfspace_individual', ['electrical.half_space_point_source_potential'],
  ['integrated_hemispherical_charge_flux'], ['zero_source_at_observation_is_known_zero']),
 ('earth_electrode_independent', ['electrical.earth_electrode_resistance'],
  ['vertical_average_self_resistance_matches_independent_line_potential_integral','unequal_vertical_mutual_resistance_and_reciprocity_match_independent_integral'],
  ['infinite_spacing_limit_is_parallel_single_resistance_and_surface_green_mutual']),
 ('compact_prover_independent', ['instrument.compact_prover_two_clock_factor'],
  ['independent_piston_volume_and_pulse_phase_ode','high_precision_clock_ratio_and_units'], ['equal_clock_scaling_and_volume_scaling','finite_support_and_request_replay']),
 ('vibrating_density_independent', ['instrument.vibrating_density_calibration'],
  ['independent_loaded_spring_mass_ode_period_and_energy','dimensioned_polynomial_against_high_precision'], ['nonpositive_and_overflow_polynomial_is_withheld']),
 ('master_meter_proving_independent', ['metrology.master_meter_calibration'],
  ['geometry_volume_and_independent_mass_counter_odes'], ['finite_support_and_replay']),
 ('parallel_meter_runs_independent', ['metrology.parallel_identical_meter_runs'],
  ['independent_frozen_bernoulli_branch_root_and_continuity'], ['zero_head_identity_and_explicit_measurement_range']),
 ('rainfall_inventory_independent', ['inventory.prescribed_rainfall_drainage'],
  ['switched_ode_with_dry_event'], ['dry_roof_rain_passes_through_without_storage']),
 ('paired_transit_individual', ['metrology.ultrasonic_transit'],
  ['independent_vector_ray_geometry_recovers_flow_and_medium_sound'], ['perpendicular_geometry_is_not_identifiable']),
 ('solid_pulse_echo_reference_independent', ['metrology.ultrasonic_wall_thickness'],
  ['native_solid_echo_recovers_known_depth_from_independent_ray_flight'], ['equal_absolute_timestamp_shift_preserves_measured_solid_echo_interval']),
 ('component_replacement_individual', ['mixture.closed_volume_replace_to_target'],
  ['inverse_from_independent_decimal_component_inventory'], ['extensive_inventory_scale']),
 ('liquid_blending_individual', ['mixture.common_volume_specific_gravity'],
  ['specific_gravity_matches_independent_component_mass_budget'], ['volume_unit_scaling_and_component_permutation_do_not_change_specific_gravity']),
 ('liquid_blending_individual', ['mixture.inverse_root_sus'],
  ['inverse_root_coordinate_has_known_blend_values'], ['empirical_efflux_relation_scales_with_property_and_stays_in_component_interval']),
 ('gas_combustion_reference_individual', ['mixture.gas_calorific_value'],
  ['independent_complete_reaction_product_energy_and_dry_mass'], ['all_inert_zero_is_explicit_not_unknown_combustion_data']),
 ('gas_caloric_mixture_individual', ['mixture.gas_molar_mass_heat_capacity'],
  ['weighed_component_mass_and_parcel_heat_ledgers'], ['single_component_limit']),
 ('glycol_freezing_target_independent', ['mixture.glycol_freezing_target'],
  ['independent_solvent_chemical_potential_and_species_mole_inventory'], ['missing_volume_basis_preserves_known_mass_and_lower_bound_requires_no_density']),
 ('kinematic_viscosity_blend_individual', ['mixture.kinematic_viscosity_index_blend'],
  ['high_precision_coordinate_inversion'], ['pure_and_identical_components']),
 ('oil_water_viscosity_estimate_independent', ['mixture.oil_water_pseudoliquid_viscosity'],
  ['independent_parallel_stripe_couette_force_work_and_dissipation'], ['volume_scale_phase_relabel_and_identical_viscosity_limits']),
 ('gas_pseudocritical_independent', ['mixture.pseudocritical_gas'],
  ['kay_authored_mole_weighted_component_reference'], ['kay_pure_limit_and_permutation']),
 ('sample_pressure_purge_individual', ['mixture.sample_cylinder_pressure_purge'],
  ['stage_process_from_independent_species_mass_ODE','independent_decimal_pressure_product_and_retained_final_fill_target'], ['zero_target_remains_unattainable_despite_fraction_underflow']),
 ('water_vapor_observation_individual', ['moisture.dewpoint_relative_humidity'],
  ['liquid_saturation_slope_matches_independent_clausius_clapeyron'], ['exact_zero_water_has_no_finite_dewpoint_and_no_added_water']),
 ('water_vapor_observation_individual', ['mixture.water_saturated_gas'],
  ['wet_dry_mass_and_mole_ledgers_against_independent_decimal_conservation'], ['zero_dry_quantity_has_zero_amounts_without_fake_zero_molar_mass']),
 ('acoustic_density_individual', ['properties.acoustic_bulk_density'],
  ['linear_mass_momentum_mode_period_inverts_known_density'], ['common_modulus_and_speed_scaling']),
 ('black_oil_phase_observer_independent', ['properties.black_oil_phase_partition'],
  ['independent_henry_chemical_potential_component_mass_and_phase_volume'], ['no_gas_and_zero_solubility_boundaries']),
 ('identified_liquid_bulk_modulus_independent', ['properties.crude_oil_bulk_modulus'],
  ['independent_tait_pressure_volume_law_and_zero_expansion_caloric_identity'], []),
 ('gravity_constrained_calorific_independent', ['properties.gas_heating_value_gravity_fit'],
  ['independent_molecular_density_inverse_and_hess_total_reaction_energy'], ['pure_inert_zero_heat_and_inconsistent_inert_gravity']),
 ('suspension_viscosity_independent', ['properties.suspension_viscosity'],
  ['independent_incremental_stresslet_effective_medium_ODE'], ['zero_solids_and_monotone_packing_approach']),
 ('ionic_water_conductivity_independent', ['properties.water_resistivity_solids_fit'],
  ['independent_single_particle_force_drag_charge_current_and_joule_budget'], ['zero_dissolved_inventory_background_and_no_infinity_serialization']),
 ('liquid_water_response_individual', ['properties.liquid_water_eos_response'],
  ['beta_and_kappa_from_independent_five_point_density_derivatives'], ['water_anomaly_has_negative_zero_positive_signed_beta','extensive_mass_scaling_and_absent_reference']),
 ('liquid_chamber_cycle_independent', ['pump.liquid_clearance_volumetric_efficiency'],
  ['closed_mass_pressure_volume_ode_and_full_piston_cycle'], ['incompressible_limit_and_slip_basis','zero_delivery_boundary_is_preserved_without_artificial_positive_flow']),
 ('vacuum_pumpdown_individual', ['vacuum.pumpdown_time'],
  ['independent_throughput_ode_and_integrated_mass_budget'], ['zero_pumping_or_closed_line_no_target_and_linear_gas_loading','small_positive_long_time_pressure_not_cancelled_to_zero']),
 ('vacuum_water_evaporation_individual', ['vacuum.water_evaporation_time'],
  ['independent_mass_volume_linear_system_and_ode_depletion_event','native_water_plateau_independent_rigid_volume_energy_and_mass'], ['geometry_scaling_clock_and_zero_initial_inventory','existing_series_conductance_and_zero_isolation']),
]

_modules = {}
for module_name, units, formula_names, extreme_names in SELECTIONS:
    if module_name not in _modules:
        path = Path(__file__).with_name('test_'+module_name+'.py')
        spec = importlib.util.spec_from_file_location('_formula_reference_'+module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _modules[module_name] = module
    for kind, names in (('formula', formula_names), ('extreme', extreme_names)):
        for name in names:
            original = getattr(_modules[module_name], 'test_'+name)
            def make_wrapper(function):
                @wraps(function)
                def wrapped(*args, **kwargs):
                    return function(*args, **kwargs)
                return wrapped
            wrapped = member(units, kind)(make_wrapper(original))
            globals()['test_'+kind+'_'+units[0].replace('.', '_')+'_'+name] = wrapped


def source_request(key, **changes):
    model, data = INPUT_BASELINES[key]
    return model.model_validate({**data, **changes})


@member(['electrical.earth_electrode_resistance'], 'formula')
@pytest.mark.parametrize('sigma_left,sigma_right', [(2., 3.), (.2, 20.)])
def test_heterogeneous_earth_fv_matches_two_parallel_continuous_slab_paths(sigma_left, sigma_right):
    q = ConductiveEarthRequest(x_edges_m=(-1.5,-.5,.5,1.5), y_edges_m=(0.,1.), z_depth_edges_m=(0.,1.),
        conductivity_S_m_by_cell=(sigma_left,1.,sigma_right),
        electrodes=({'id':'metal','cells_xyz':((1,0,0),)},), grounded_faces=('x_min','x_max'),
        prescribed_electrode_currents_A={'metal':1.}, coordinate_frame_id='xyz',
        material_assignment_id='two-slab-conductors', potential_reference_id='outer-planes',
        heterogeneous_ohmic_mesh_and_finite_exterior_boundary_reviewed=True)
    r = evaluate_conductive_earth(q)
    # Both soil paths have unit length and cross section; J=sigma*V/length.
    voltage = 1/(sigma_left+sigma_right)
    assert r['status']=='computed'
    assert r['electrode_potentials_V']['metal']==pytest.approx(voltage,rel=2e-14)
    assert r['electrode_resistance_matrix_ohm'][0][0]==pytest.approx(voltage,rel=2e-14)
    assert r['conductive_dissipation_W']==pytest.approx(voltage,rel=2e-14)
    assert r['outward_ground_current_by_face_A']['x_min']==pytest.approx(sigma_left*voltage)
    assert r['outward_ground_current_by_face_A']['x_max']==pytest.approx(sigma_right*voltage)


@member(['electrical.earth_electrode_resistance'], 'formula')
def test_fv_two_electrode_mutual_resistance_matches_three_physical_series_resistors():
    q = ConductiveEarthRequest(x_edges_m=(0.,1.,2.,3.,4.,5.),y_edges_m=(0.,1.),z_depth_edges_m=(0.,1.),
        conductivity_S_m_by_cell=(1.,)*5,
        electrodes=({'id':'a','cells_xyz':((1,0,0),)},{'id':'b','cells_xyz':((3,0,0),)}),
        grounded_faces=('x_min','x_max'), prescribed_electrode_currents_A={'a':1.,'b':0.},
        coordinate_frame_id='xyz',material_assignment_id='unit-soil-paths',potential_reference_id='outer-planes',
        heterogeneous_ohmic_mesh_and_finite_exterior_boundary_reviewed=True)
    r=evaluate_conductive_earth(q)
    assert r['status']=='computed'
    assert r['electrode_potentials_V']==pytest.approx({'a':2/3,'b':1/3},rel=3e-14)
    for actual, expected in zip(r['electrode_resistance_matrix_ohm'],((2/3,1/3),(1/3,2/3))):
        assert actual==pytest.approx(expected,rel=3e-14)


@member(['electrical.earth_electrode_resistance'], 'extreme')
@pytest.mark.parametrize('sigma,current', [(1e-100,1.),(1e100,1.),(1.,0.)])
def test_fv_extreme_conductivity_and_zero_source_are_finite(sigma,current):
    q=ConductiveEarthRequest(x_edges_m=(-1.5,-.5,.5,1.5),y_edges_m=(0.,1.),z_depth_edges_m=(0.,1.),
        conductivity_S_m_by_cell=(sigma,1.,sigma),electrodes=({'id':'metal','cells_xyz':((1,0,0),)},),
        grounded_faces=('x_min','x_max'),prescribed_electrode_currents_A={'metal':current},coordinate_frame_id='xyz',
        material_assignment_id='uniform-soil',potential_reference_id='outer-planes',
        heterogeneous_ohmic_mesh_and_finite_exterior_boundary_reviewed=True)
    r=evaluate_conductive_earth(q)
    assert r['status']=='computed'
    if current>0:assert r['electrode_potentials_V']['metal']>0
    assert r['electrode_potentials_V']['metal']==pytest.approx(current/(2*sigma),rel=3e-14,abs=0.)
    assert r['electrode_input_power_W']==pytest.approx(current*current/(2*sigma),rel=3e-14,abs=0.)


@member(['mixture.water_saturated_gas'], 'formula')
def test_fugacity_equality_and_independent_dry_species_mass_ledger():
    q=source_request('saturated', pressure_Pa_abs=1e6,condensed_water_fugacity_Pa=2e4,
                     gas_water_fugacity_coefficient=2.,dry_molar_quantity=1000.)
    r=observe_saturated_gas_fugacity(q)
    assert r['status']=='computed'
    assert r['water_mole_fraction']==pytest.approx(.01,rel=2e-14)
    assert r['water_molar_quantity']==pytest.approx(1000/99,rel=2e-14)
    assert r['wet_mass_quantity']==pytest.approx(28.+(1000/99)*.018,rel=3e-14)
    assert abs(r['mass_balance_residual'])<1e-12


@member(['mixture.water_saturated_gas'], 'extreme')
@pytest.mark.parametrize('quantity,water', [(1e-100,.01),(1e100,.01),(1.,1e-12),(1.,1-1e-10)])
def test_fugacity_extensive_and_saturation_limit_inputs_remain_finite(quantity,water):
    q=source_request('saturated',dry_molar_quantity=quantity,condensed_water_fugacity_Pa=100000.*water)
    r=observe_saturated_gas_fugacity(q)
    assert r['status']=='computed'
    actual_y=q.condensed_water_fugacity_Pa/q.pressure_Pa_abs
    assert r['water_molar_quantity']>0 and r['wet_molar_quantity']>0
    assert r['wet_molar_quantity']==pytest.approx(quantity/(1-actual_y),rel=3e-14,abs=0.)
    assert math.isfinite(r['wet_mass_quantity'])


@member(['emissions.filling_vapor_rate'], 'formula')
def test_fill_displacement_capacity_and_latent_heat_have_separate_si_sources():
    r=evaluate_filling_vapor(source_request('filling',additional_thermal_power_W=20000.))
    assert r['displacement_vapor_rate_kg_s']==pytest.approx(.02)
    assert r['generated_vapor_rate_kg_s']==pytest.approx(.2)
    assert r['total_vent_vapor_rate_kg_s']==pytest.approx(.22)


@member(['emissions.filling_vapor_rate'], 'extreme')
@pytest.mark.parametrize('scale', [0.,1e-100,1e100])
def test_fill_zero_and_large_small_rate_scales(scale):
    r=evaluate_filling_vapor(source_request('filling',liquid_fill_rate_m3_s=.01*scale,
        capacity_generation_coefficient_kg_m3_s=.001*scale,
        additional_thermal_power_W=20000.*scale,maximum_liquid_supply_kg_s=1.*scale))
    if scale>0:assert r['total_vent_vapor_rate_kg_s']>0
    assert r['total_vent_vapor_rate_kg_s']==pytest.approx(.22*scale,rel=3e-14,abs=0.)


def loss_request(roof='fixed',scale=1.):
    episode=dict(duration_s=10.,roof=roof,vapor_concentration_kg_m3=2.*scale,
        breathing_exchange_m3=3.,vapor_partial_pressure_difference_Pa=10.,
        rim_seal_mass_conductance_kg_s_Pa=0. if roof=='fixed' else .02*scale,
        fittings_mass_conductance_kg_s_Pa=.01*scale,working_displaced_gas_m3=4.,
        wetted_area_per_cycle_m2=5.,retained_liquid_film_m=.001,liquid_density_kg_m3=1000.*scale,
        wetting_evaporation_fraction=.5,turnover_cycles=2.,calibrated_geometry_environment_id='reviewed-data',
        roof_mechanisms_and_factor_applicability_reviewed=True)
    return TankLossRequest(episodes=(episode,),accounting_horizon_s=10.,calendar_basis_id='interval')


@member(['emissions.tank_standing_working_loss'], 'formula')
@pytest.mark.parametrize('roof', ['fixed','external_floating','internal_floating'])
def test_distinct_roof_mass_transfer_and_wetting_inventories(roof):
    r=evaluate_tank_standing_working_loss(loss_request(roof))
    assert r['standing_loss_kg']==pytest.approx(7. if roof=='fixed' else 9.)
    assert r['working_loss_kg']==pytest.approx(13.)


@member(['emissions.tank_standing_working_loss'], 'extreme')
@pytest.mark.parametrize('scale', [1e-100,1e100])
def test_roof_large_and_small_conditional_mass_scales(scale):
    r=evaluate_tank_standing_working_loss(loss_request(scale=scale))
    assert r['total_loss_kg']>0
    assert r['total_loss_kg']==pytest.approx(20.*scale,rel=3e-14,abs=0.)


@member(['emissions.calibrated_overfill_cascade_source'], 'formula')
def test_cascade_species_air_capacity_heat_and_cloud_mass_coordinates():
    q=source_request('cascade',air_molar_mass_kg_mol=.02,available_net_evaporation_heat_W=100000.,
        source_duration_s=9.,diluted_fuel_concentration_kg_m3=.02,cloud_depth_m=1.)
    r=evaluate_cascade_source(q)
    assert r['saturated_fuel_air_mass_ratio']==pytest.approx(4/9)
    assert r['vapor_mass_rate_kg_s']==pytest.approx(2/9)
    assert r['aerosol_mass_rate_kg_s']==pytest.approx(.1)
    assert r['residual_liquid_mass_rate_kg_s']==pytest.approx(61/90)
    assert r['conditional_cloud_volume_m3']==pytest.approx(100.)
    assert r['conditional_cloud_radius_m']==pytest.approx(10/math.sqrt(math.pi))
    assert abs(r['mass_balance_residual_kg_s'])<1e-15


@member(['emissions.calibrated_overfill_cascade_source'], 'extreme')
@pytest.mark.parametrize('change', [dict(entrained_dry_air_mass_rate_kg_s=0.),
    dict(available_net_evaporation_heat_W=0.),dict(liquid_mass_rate_kg_s=0.),
    dict(vapor_transfer_effectiveness=0.),dict(splash_aerosol_mass_fraction=1.)])
def test_cascade_actual_zero_limit_capacities_preserve_all_phase_inventory(change):
    q=source_request('cascade',**change)
    r=evaluate_cascade_source(q)
    assert r['vapor_mass_rate_kg_s']==0.
    assert r['residual_liquid_mass_rate_kg_s']>=0
    assert abs(r['mass_balance_residual_kg_s'])<1e-15
    assert abs(r['heat_capacity_budget_residual_W'])<1e-10


@member(['measurement.slick_appearance_thickness'], 'formula')
def test_visual_band_volume_is_an_independent_area_times_depth_inventory():
    r=evaluate_slick_observation(source_request('slick'))
    assert r['status']=='computed'
    assert r['minimum_volume_m3']==pytest.approx(.0001)
    assert r['maximum_volume_m3']==pytest.approx(.0002)


@member(['measurement.slick_appearance_thickness'], 'extreme')
@pytest.mark.parametrize('area', [0.,1e-100,1e100])
def test_slick_zero_and_extreme_area_inventory(area):
    r=evaluate_slick_observation(source_request('slick',observed_area_m2=area))
    assert r['status']=='computed'
    if area>0:assert r['minimum_volume_m3']>0 and r['maximum_volume_m3']>0
    assert r['minimum_volume_m3']==pytest.approx(area*1e-6,rel=2e-14,abs=0.)
    assert r['maximum_volume_m3']==pytest.approx(area*2e-6,rel=2e-14,abs=0.)


def anode_inputs(mass=.001,zero=False):
    q=source_request('anode')
    p=dict(q.budget.parameters)
    p['single_anode_mass_kg']=mass
    if zero:
        p['bare_surface_fraction']=[0.,0.]
    request=AnodeLifetimeRequest(budget=q.budget.model_copy(update={'parameters':p}),time_absolute_tolerance_s=1e-8)
    context=RunContext(asset_id='pipe',scenario_id='formula-case',clock_id='clock',origin='synthetic',
        evidence=(EvidenceRef(id='input',citation='Authored constant charge demand and specified usable capacity',origin='synthetic'),))
    return request,context


@member(['electrochemistry.anode_charge_lifetime'], 'formula')
def test_anode_actual_owner_charge_depletion_matches_constant_current_time(material_client):
    q,c=anode_inputs()
    r=evaluate_anode_lifetime(q,c,client=material_client)
    assert r['status']=='computed'
    assert r['depletion_time_bracket_s'][0]<=4.<=r['depletion_time_bracket_s'][1]
    assert r['depletion_clock_time_s']==pytest.approx(4.,abs=1e-8)
    assert r['charge_deficit_C']==pytest.approx(1.2)
    assert r['demand_charge_C']==pytest.approx(2.)
    assert r['consumed_charge_C']==pytest.approx(.8)
    assert r['consumed_charge_C']+r['charge_deficit_C']==pytest.approx(r['demand_charge_C'])
    assert r['consumed_usable_anode_mass_kg']==pytest.approx(.0008)


@member(['electrochemistry.anode_charge_lifetime'], 'extreme')
@pytest.mark.parametrize('mass,zero', [(1e-100,False),(1e100,False),(.001,True)])
def test_anode_extreme_capacity_and_zero_demand(material_client,mass,zero):
    q,c=anode_inputs(mass,zero)
    r=evaluate_anode_lifetime(q,c,client=material_client)
    assert r['status']=='computed'
    assert math.isfinite(r['remaining_usable_charge_C'])
    assert math.isfinite(r['charge_deficit_C'])
    if not zero:assert r['consumed_charge_C']>0
    if zero or mass>1:
        assert r['depletion']=='not_reached_in_supplied_history'
    else:
        exact=mass*1000*.8/.2
        assert r['depletion_time_bracket_s'][0]<=exact<=r['depletion_time_bracket_s'][1]


@member(['properties.crude_oil_bulk_modulus'], 'extreme')
@pytest.mark.parametrize('pressure', [1e5,1e8])
def test_liquid_modulus_actual_tait_provider_at_reference_and_high_pressure(pressure):
    module=_modules['identified_liquid_bulk_modulus_independent']
    provider=module.TaitProvider()
    r=module.observe(module.request(pressure_Pa_abs=pressure),property_provider=provider)
    assert r.status=='computed'
    assert r.isothermal_bulk_modulus_Pa==pytest.approx(provider.n*(provider.B+pressure-provider.p0),rel=2e-7)
