"""Remaining 26 property-related units: input validation, never solver success."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from pipeline_integrity.acoustic_density import AcousticDensityRequest, observe_acoustic_density
from pipeline_integrity.black_oil_phase_observer import BlackOilPartitionRequest
from pipeline_integrity.component_replacement import ComponentReplacementRequest, solve_component_replacement
from pipeline_integrity.distributed_pipe_cp import UniformCPLineRequest
from pipeline_integrity.gas_basis import REVIEWS as GAS_BASIS_REVIEWS
from pipeline_integrity.gas_caloric_mixture import GasCaloricMixtureRequest
from pipeline_integrity.gas_combustion_reference import GasCombustionReferenceRequest
from pipeline_integrity.gas_pseudocritical import PseudocriticalRequest
from pipeline_integrity.glycol_freezing_target import GlycolFreezingTargetRequest
from pipeline_integrity.gravity_constrained_calorific import GravityCalorificRequest, observe_gravity_constrained_calorific
from pipeline_integrity.ground_halfspace import GroundHalfspaceRequest, evaluate_ground_halfspace
from pipeline_integrity.identified_liquid_bulk_modulus import LiquidBulkModulusRequest
from pipeline_integrity.ionic_water_conductivity import IonicWaterRequest, observe_ionic_water_conductivity
from pipeline_integrity.kinematic_viscosity_blend import KinematicViscosityBlendRequest, evaluate_kinematic_viscosity_blend
from pipeline_integrity.liquid_blending import LiquidBlendRequest
from pipeline_integrity.liquid_chamber_cycle import LiquidChamberCycleRequest
from pipeline_integrity.liquid_water_response import LiquidWaterResponseRequest
from pipeline_integrity.nde import EchoThicknessRequest
from pipeline_integrity.oil_water_viscosity_estimate import OilWaterViscosityRequest
from pipeline_integrity.paired_transit import PairedTransitRequest, invert_paired_transit
from pipeline_integrity.rainfall_inventory import RainfallInventoryRequest
from pipeline_integrity.sample_pressure_purge import SamplePressurePurgeRequest, evaluate_sample_pressure_purge
from pipeline_integrity.suspension_viscosity import SuspensionViscosityRequest, evaluate_suspension_viscosity
from pipeline_integrity.vacuum_pumpdown import VacuumPumpdownRequest
from pipeline_integrity.vacuum_water_evaporation import VacuumWaterEvaporationRequest, evaluate_vacuum_water_evaporation
from pipeline_integrity.water_vapor_observation import WaterVaporObservationRequest

from test_input_properties_223 import assert_target_numeric_error, replace_path


def baselines():
    state = dict(id='source', pressure_pa_abs=100000., temperature_K=300.,
        water_vapor_mole_fraction=0., volume_basis='dry_gas', compressibility_Z_common_R=1.,
        state_evidence_id='input', water_fraction_evidence_id='input', gas_phase_evidence_id='input',
        compressibility_evidence_id='input')
    gas_basis = dict(id='conversion', asset_id='pipe', clock_id='clock', event_id='event', time_s=0.,
        quantity_kind='volume', source_value=1., dry_composition_id='dry-composition',
        dry_components=({'fluid': 'Methane', 'mole_fraction': 1.},), composition_evidence_id='input',
        source=state, target={**state, 'id': 'target'}, Z_mode='supplied',
        reviews=dict.fromkeys(GAS_BASIS_REVIEWS, True),
        review_evidence_ids=dict.fromkeys(GAS_BASIS_REVIEWS, 'input'), evidence_ids=('input',))
    datum = lambda name, mass, heat: dict(fluid=name, molar_mass_kg_mol=mass,
        released_combustion_energy_J_mol=heat, combustion_reference_id='calibration')
    return {
        'electrical.distributed_pipeline_cp': (UniformCPLineRequest, dict(mode='finite',
            longitudinal_resistance_ohm_m=.1, transverse_conductance_S_m=.01, length_m=1.,
            left={'potential_displacement_V': 1.}, right={'potential_displacement_V': 0.},
            sample_positions_m=(0., 1.), asset_id='pipe', coordinate_frame_id='x',
            potential_reference_id='potential-reference', coefficient_basis_id='coating',
            uniform_passive_ohmic_pipe_and_shunt_reference_reviewed=True), ('longitudinal_resistance_ohm_m',)),
        'electrical.half_space_point_source_potential': (GroundHalfspaceRequest,
            dict(uniform_resistivity_ohm_m=100., coordinate_frame_id='xyz',
                sources=({'id': 'source', 'horizontal_position_m': (0., 0.), 'signed_current_into_ground_A': 1.},),
                observations=({'id': 'observation', 'position_xyz_depth_m': (1., 0., 1.)},),
                homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed=True,
                net_current_return_at_infinity_reviewed=True), ('uniform_resistivity_ohm_m',)),
        'inventory.prescribed_rainfall_drainage': (RainfallInventoryRequest,
            dict(id='rain', time_s=(0., 1., 2.), rain_depth_rate_m_s=(.001, .002),
                drain_capacity_depth_rate_m_s=(.001, .001), footprint_area_m2=100.,
                initial_water_depth_m=.001, water_density_kg_m3=1000., gravity_m_s2=9.81,
                observation_times_s=(0., 1., 2.), uniform_footprint_prescribed_rates_and_dry_boundary_reviewed=True),
            ('footprint_area_m2',)),
        'metrology.ultrasonic_transit': (PairedTransitRequest,
            dict(clock_id='clock', path_length_m=1., signed_axial_projection_m=.5,
                forward_observed_time_s=.001, reverse_observed_time_s=.0011,
                forward_fixed_delay_s=0., reverse_fixed_delay_s=0.,
                uniform_axial_flow_isotropic_medium_and_stationary_paths_reviewed=True), ('path_length_m',)),
        'metrology.ultrasonic_wall_thickness': (EchoThicknessRequest,
            dict(waveform_digest='waveform', reference_index=2, return_index=20,
                sound_speed_m_s=5000., time_zero_offset_s=0., incidence_angle_rad=0.,
                source_evidence_id='source', echo_review_evidence_id='review', calibration_evidence_id='calibration'),
            ('sound_speed_m_s',)),
        'mixture.closed_volume_replace_to_target': (ComponentReplacementRequest,
            dict(system_volume_m3=1., initial_component_volume_fraction=.1, feed_component_volume_fraction=.9,
                target_component_volume_fraction=.5, additive_volumes_common_state_and_uniform_withdrawal_reviewed=True),
            ('system_volume_m3',)),
        'mixture.common_volume_specific_gravity': (LiquidBlendRequest,
            dict(method='specific_gravity', components=({'id': 'liquid', 'volume_m3': 1., 'property_value': .8},),
                reference_basis='common-water-PT', common_property_basis_and_additive_volumes_reviewed=True),
            ('components', 0, 'property_value')),
        'mixture.gas_calorific_value': (GasCombustionReferenceRequest,
            dict(gas_basis=gas_basis, component_data=(datum('Methane', .016, 1000.),),
                calorific_basis='gross_liquid_product_water', combustion_reference_id='calibration',
                combustion_reference_temperature_K=298.15, combustion_reference_pressure_Pa_abs=100000.,
                vapor_water_molar_mass_kg_mol=.018, supplied_component_combustion_data_additivity_and_reference_reviewed=True),
            ('combustion_reference_temperature_K',)),
        'mixture.gas_molar_mass_heat_capacity': (GasCaloricMixtureRequest,
            dict(mode='ideal_gas_caloric', fraction_basis='mole',
                components=({'id': 'gas', 'fraction': 1., 'molar_mass_kg_mol': .03,
                    'ideal_gas_molar_cp_J_mol_K': 30., 'heat_capacity_temperature_K': 300.},),
                property_basis_id='properties', temperature_K=300.,
                nonreacting_component_composition_ledger_reviewed=True,
                ideal_gas_molar_heat_capacity_additivity_reviewed=True), ('temperature_K',)),
        'mixture.glycol_freezing_target': (GlycolFreezingTargetRequest,
            dict(glycol_species='ethylene_glycol', target_freezing_temperature_K=260., mass_fraction_bracket=(0., .6),
                requested_concentration_basis='mass', reviewed_curve_and_single_decreasing_branch=True,
                curve_evidence_id='calibrated-curve'), ('target_freezing_temperature_K',)),
        'mixture.inverse_root_sus': (LiquidBlendRequest,
            dict(method='inverse_root_SUS_efflux_time', components=({'id': 'liquid', 'volume_m3': 1., 'property_value': 100.},),
                reference_basis='same-temperature-SUS', common_property_basis_and_additive_volumes_reviewed=True),
            ('components', 0, 'property_value')),
        'mixture.kinematic_viscosity_index_blend': (KinematicViscosityBlendRequest,
            dict(components=({'id': 'liquid', 'fraction': 1., 'temperature_K': 300., 'pressure_Pa_abs': 100000.,
                    'kinematic_viscosity_m2_s': 1e-5, 'property_origin': 'calibration'},),
                fraction_basis='mass', temperature_K=300., pressure_Pa_abs=100000., state_id='PT',
                homogeneous_newtonian_liquid_reviewed=True, measured_blend_applicability_evidence='calibration'),
            ('components', 0, 'kinematic_viscosity_m2_s')),
        'mixture.oil_water_pseudoliquid_viscosity': (OilWaterViscosityRequest,
            dict(oil_volume_m3=.7, water_volume_m3=.3, oil_dynamic_viscosity_Pa_s=.02,
                water_dynamic_viscosity_Pa_s=.001, temperature_K=300., pressure_Pa_abs=100000.,
                component_properties_at_same_state_reviewed=True, volume_average_approximation_selected=True,
                rheology_evidence_id='calibration', requested_shear_rate_s_inv=1.), ('oil_dynamic_viscosity_Pa_s',)),
        'mixture.pseudocritical_gas': (PseudocriticalRequest,
            dict(method='kay_mole_average', components=({'id': 'gas', 'mole_fraction': 1.,
                'critical_temperature_K': 200., 'critical_pressure_Pa_abs': 4000000.},),
                approximation_and_component_basis_reviewed=True), ('components', 0, 'critical_pressure_Pa_abs')),
        'mixture.sample_cylinder_pressure_purge': (SamplePressurePurgeRequest,
            dict(cylinder_volume_m3=.002, common_temperature_K=300., initial_pressure_Pa_abs=100000.,
                original_gas_molar_mass_kg_mol=.028, feed_gas_molar_mass_kg_mol=.044,
                stages=({'fill_pressure_Pa_abs': 1000000., 'vent_pressure_after_Pa_abs': 100000.},),
                clock_id='clock', property_basis_id='properties',
                ideal_fixed_temperature_volume_perfect_mixing_nonselective_vent_and_original_free_feed_reviewed=True),
            ('cylinder_volume_m3',)),
        'moisture.dewpoint_relative_humidity': (WaterVaporObservationRequest,
            dict(gas_temperature_K=300., total_pressure_Pa_abs=100000., humidity_reference_phase='liquid_water',
                condensation_reference_phase='liquid_water', relative_humidity_fraction=.5,
                condensation_temperature_bracket_K=(273.16, 320.), ideal_partial_pressure_equilibrium_applicability_reviewed=True,
                ideal_mixture_pressure_support_Pa_abs=(1000., 200000.), property_basis_id='water-property'), ('gas_temperature_K',)),
        'properties.acoustic_bulk_density': (AcousticDensityRequest,
            dict(isentropic_bulk_modulus_Pa=1e6, acoustic_speed_m_s=1000., modulus_basis='isentropic',
                speed_basis='unbounded_fluid', bulk_state_id='state', speed_state_id='state',
                same_state_composition_single_phase_nonrelaxing_acoustics_reviewed=True), ('isentropic_bulk_modulus_Pa',)),
        'properties.black_oil_phase_partition': (BlackOilPartitionRequest,
            dict(pressure_Pa_abs=200000., temperature_K=300., stock_oil_volume_m3=1., total_gas_reference_volume_m3=2.,
                oil_reference_density_kg_m3=800., gas_reference_density_kg_m3=1.,
                oil_reference_temperature_K=300., oil_reference_pressure_Pa_abs=100000.,
                gas_reference_temperature_K=300., gas_reference_pressure_Pa_abs=100000., reference_basis_id='stock-states',
                material_PVT_id='calibration', saturated_gas_ratio_ref_m3_per_stock_oil_m3=1.,
                oil_formation_volume_factor=1.1, oil_factor_at_dissolved_gas_ratio=1., gas_formation_volume_factor=.5,
                reviewed_nonvolatile_oil_single_gas_equilibrium_closure=True,
                formation_factors_and_saturation_at_requested_PT_and_reference_basis_reviewed=True), ('oil_formation_volume_factor',)),
        'properties.crude_oil_bulk_modulus': (LiquidBulkModulusRequest,
            dict(fluid_model='n-Decane', pressure_Pa_abs=2000000., temperature_K=300., derivative_basis='both',
                material_assignment_id='material', modeled_material_kind='identified_hydrocarbon_surrogate',
                identified_material_and_local_liquid_PT_domain_reviewed=True), ('pressure_Pa_abs',)),
        'properties.gas_heating_value_gravity_fit': (GravityCalorificRequest,
            dict(gas_specific_gravity_to_air=.8, dry_nitrogen_mole_fraction=.1, air_molar_mass_kg_mol=.02897,
                nitrogen_data=datum('Nitrogen', .028, 0.), fuel_data=(datum('Methane', .016, 1000.), datum('Ethane', .03, 2000.)),
                gravity_measurement_basis='dry_gas', gravity_measurement_water_mole_fraction=0.,
                reference_water_mole_fraction=0., water_molar_mass_kg_mol=.018, reference_temperature_K=300.,
                reference_pressure_Pa_abs=100000., combustion_reference_id='calibration', calorific_basis='gross_liquid_product_water',
                two_known_fuels_and_nitrogen_are_complete_dry_composition_reviewed=True,
                same_PT_ideal_gas_gravity_and_single_gas_reference_reviewed=True,
                supplied_combustion_reference_and_water_basis_reviewed=True), ('air_molar_mass_kg_mol',)),
        'properties.suspension_viscosity': (SuspensionViscosityRequest,
            dict(id='suspension', temperature_K=300., carrier_dynamic_viscosity_Pa_s=.001,
                dry_solid_volume_fraction=.1, maximum_packing_volume_fraction=.63, slurry_density_kg_m3=1200.,
                hard_sphere_aqueous_newtonian_low_inertia_regime_reviewed=True,
                carrier_property_and_packing_evidence_id='properties'), ('maximum_packing_volume_fraction',)),
        'properties.water_resistivity_solids_fit': (IonicWaterRequest,
            dict(dissolved_solids_kg_m3=1., species=tuple(dict(species_id=name, dissolved_mass_fraction=.5,
                molar_mass_kg_mol=.03, charge_number=charge, electrical_mobility_m2_V_s=1e-8)
                for name, charge in (('positive-ion', 1), ('negative-ion', -1))),
                temperature_K=300., pressure_Pa_abs=100000., coefficient_temperature_K=300.,
                coefficient_pressure_Pa_abs=100000., analysis_id='analysis', transport_coefficient_id='transport',
                ionic_analysis_and_transport_domain_reviewed=True, background_conductivity_S_m=0.), ('temperature_K',)),
        'properties.liquid_water_eos_response': (LiquidWaterResponseRequest,
            dict(state={'pressure_Pa_abs': 100000., 'temperature_K': 300.},
                reference_state={'pressure_Pa_abs': 100000., 'temperature_K': 290.}, fixed_mass_kg=1.), ('state', 'pressure_Pa_abs')),
        'pump.liquid_clearance_volumetric_efficiency': (LiquidChamberCycleRequest,
            dict(suction={'pressure_pa_abs': 200000., 'temperature_K': 300., 'density_kg_m3': 800., 'phase': 'liquid', 'property_basis_id': 'material'},
                discharge={'pressure_pa_abs': 2000000., 'temperature_K': 300., 'density_kg_m3': 810., 'phase': 'liquid', 'property_basis_id': 'material'},
                end_discharge_dead_volume_m3=.0001, swept_volume_m3=.001, slip_fraction_of_swept_suction_volume=.01,
                single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed=True,
                common_fluid_and_slip_reference_reviewed=True), ('swept_volume_m3',)),
        'vacuum.pumpdown_time': (VacuumPumpdownRequest,
            dict(volume_m3=1., initial_pressure_Pa_abs=100000., pump_speed_m3_s=.01, line_conductances_m3_s=(.1,),
                pump_ultimate_pressure_Pa_abs=10., constant_gas_load_Pa_m3_s=0., gas_temperature_K=300.,
                specific_gas_constant_J_kg_K=287., elapsed_times_s=(0., 10.), target_pressure_Pa_abs=100.,
                clock_id='clock', pump_start_time_s=0., isothermal_fixed_composition_and_constant_linear_throughput_reviewed=True),
            ('volume_m3',)),
        'vacuum.water_evaporation_time': (VacuumWaterEvaporationRequest,
            dict(internal_volume_m3=1., initial_free_water_mass_kg=1., plateau_temperature_K=300.,
                external_pressure_Pa_abs=100000., pump_speed_m3_s=.01, line_conductances_m3_s=(),
                thermal_boundary='prescribed_isothermal_reservoir',
                pure_water_uniform_saturation_and_constant_coefficients_reviewed=True,
                prescribed_thermal_boundary_and_pump_control_reviewed=True, elapsed_times_s=(0., 10.),
                clock_id='clock', start_time_s=0.), ('internal_volume_m3',)),
    }


BASELINES = baselines()


def mark(record_property, unit):
    record_property('canonical_unit_ids', 'proposal.'+unit)
    record_property('canonical_units', json.dumps(['proposal.'+unit]))
    record_property('verification_kind', 'input_contract_only')


@pytest.mark.parametrize('unit', tuple(BASELINES))
def test_complete_remaining_input_baseline_without_engine(unit, record_property):
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    request = model.model_validate(deepcopy(original))
    assert isinstance(request, model)


REQUIRED = [(unit, field) for unit, (model, _, _) in BASELINES.items()
            for field, definition in model.model_fields.items() if definition.is_required()]


@pytest.mark.parametrize('unit,field', REQUIRED)
def test_required_field_missing_identifies_exact_location(unit, field, record_property):
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    model.model_validate(deepcopy(original))
    invalid = deepcopy(original)
    invalid.pop(field)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(invalid)
    assert [(e['loc'], e['type']) for e in caught.value.errors()] == [((field,), 'missing')]


@pytest.mark.parametrize('unit', tuple(BASELINES))
@pytest.mark.parametrize('bad', [0., -1., float('nan'), float('inf'), -float('inf'), '1 bar'])
def test_invalid_governing_quantity_rejected_at_target_input(unit, bad, record_property):
    mark(record_property, unit)
    model, original, path = BASELINES[unit]
    model.model_validate(deepcopy(original))
    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_path(original, path, bad))
    assert_target_numeric_error(caught.value.errors(), path)


SEMANTIC = [
    ('electrical.distributed_pipeline_cp', ('left', 'inward_current_A'), 1., 'one endpoint potential or inward current'),
    ('electrical.distributed_pipeline_cp', ('sample_positions_m',), (0., 2.), 'inside the declared line'),
    ('inventory.prescribed_rainfall_drainage', ('rain_depth_rate_m_s',), (.001,), 'one rain and capacity rate'),
    ('inventory.prescribed_rainfall_drainage', ('time_s',), (0., 1., 1.), 'strictly increasing'),
    ('inventory.prescribed_rainfall_drainage', ('observation_times_s',), (3.,), 'observation outside'),
    ('metrology.ultrasonic_wall_thickness', ('return_index',), 1, 'return pick must follow'),
    ('mixture.gas_molar_mass_heat_capacity', ('components', 0, 'fraction'), .5, 'fractions must sum to one'),
    ('mixture.gas_calorific_value', ('component_data', 0, 'fluid'), 'Ethane', 'every dry-gas component'),
    ('mixture.gas_calorific_value', ('component_data', 0, 'combustion_reference_id'), 'other-reference', 'share the declared reference'),
    ('mixture.kinematic_viscosity_index_blend', ('components', 0, 'fraction'), .5, 'recipe fractions must sum to one'),
    ('mixture.pseudocritical_gas', ('relative_gas_gravity_air',), .8, 'inputs must not be mixed'),
    ('mixture.pseudocritical_gas', ('components', 0, 'mole_fraction'), .5, 'mole fractions must sum to one'),
    ('mixture.glycol_freezing_target', ('mass_fraction_bracket',), (.6, .1), 'ordered mass fractions'),
    ('moisture.dewpoint_relative_humidity', ('water_mole_fraction',), .1, 'exactly one independent moisture coordinate'),
    ('moisture.dewpoint_relative_humidity', ('condensation_temperature_bracket_K',), (320., 273.16), 'ordered positive support'),
    ('pump.liquid_clearance_volumetric_efficiency', ('discharge', 'pressure_pa_abs'), 100000., 'must not be below suction'),
]


@pytest.mark.parametrize('unit,path,bad,message', SEMANTIC)
def test_remaining_input_relationships_reject_intended_inconsistency(unit, path, bad, message, record_property):
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    model.model_validate(deepcopy(original))
    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_path(original, path, bad))
    errors = caught.value.errors()
    semantic = [e for e in errors if e['type'] == 'value_error']
    assert len(semantic) == 1
    assert message in semantic[0]['msg']
    for error in errors:
        if error is not semantic[0]:
            assert error['type'] == 'too_short'
            assert semantic[0]['loc'][:len(error['loc'])] == error['loc']


API_REJECTIONS = [
    ('metrology.ultrasonic_transit', ('signed_axial_projection_m',), 2., invert_paired_transit, 'projection exceeds'),
    ('properties.acoustic_bulk_density', ('speed_state_id',), 'other-fluid-state', observe_acoustic_density, 'state identities differ'),
    ('mixture.kinematic_viscosity_index_blend', ('components', 0, 'temperature_K'), 310.,
     evaluate_kinematic_viscosity_blend, 'share the requested T'),
    ('properties.gas_heating_value_gravity_fit', ('fuel_data', 0, 'combustion_reference_id'), 'other-reference',
     observe_gravity_constrained_calorific, 'Combustion references differ'),
    ('mixture.sample_cylinder_pressure_purge', ('stages', 0, 'fill_pressure_Pa_abs'), 50000.,
     evaluate_sample_pressure_purge, 'fill pressure cannot lower'),
]


@pytest.mark.parametrize('unit,path,bad,evaluator,message', API_REJECTIONS)
def test_semantic_api_rejection_precedes_physical_output(unit, path, bad, evaluator, message, record_property):
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    model.model_validate(deepcopy(original))
    request = model.model_validate(replace_path(original, path, bad))
    with pytest.raises(ValueError, match=message):
        evaluator(request)


def test_unreachable_replacement_target_returns_no_inventory(record_property):
    unit = 'mixture.closed_volume_replace_to_target'
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    request = model.model_validate({**deepcopy(original), 'target_component_volume_fraction': 1.})
    result = solve_component_replacement(request)
    assert result.status == 'out_of_scope'
    assert 'outside the reachable' in ' '.join(result.issues)
    assert result.replacement_volume_m3 is None


def test_ionic_coefficient_state_mismatch_precedes_transport_sum(record_property):
    unit = 'properties.water_resistivity_solids_fit'
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    request = model.model_validate({**deepcopy(original), 'coefficient_temperature_K': 310.})
    result = observe_ionic_water_conductivity(request)
    assert result.status == 'out_of_scope'
    assert 'temperature or pressure correction' in ' '.join(result.issues)
    assert result.conductivity_S_m is None
    assert not result.species


def test_duplicate_ground_source_identity_cannot_reach_field_loop(record_property):
    unit = 'electrical.half_space_point_source_potential'
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    data = deepcopy(original)
    data['sources'] += (deepcopy(data['sources'][0]),)
    with pytest.raises(ValueError, match='identities must be unique'):
        evaluate_ground_halfspace(model.model_validate(data))


def test_suspension_packing_boundary_returns_no_viscosity(record_property):
    unit = 'properties.suspension_viscosity'
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    request = model.model_validate({**deepcopy(original), 'dry_solid_volume_fraction': .63})
    result = evaluate_suspension_viscosity(request)
    assert result.status == 'out_of_scope'
    assert 'packing boundary' in ' '.join(result.issues)
    assert result.effective_dynamic_viscosity_Pa_s is None


def test_incompatible_vacuum_thermal_coordinates_precede_property_provider(monkeypatch, record_property):
    unit = 'vacuum.water_evaporation_time'
    mark(record_property, unit)
    model, original, _ = BASELINES[unit]
    request = model.model_validate({**deepcopy(original), 'available_net_evaporation_heat_W': 100.})
    def forbidden(*args, **kwargs):
        raise AssertionError('property provider must not run for conflicting thermal coordinates')
    monkeypatch.setattr('pipeline_integrity.vacuum_water_evaporation.CoolPropProvider', forbidden)
    with pytest.raises(ValueError, match='finite heat capacity requires'):
        evaluate_vacuum_water_evaporation(request)
