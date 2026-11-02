"""Input-contract campaign only: no successful numerical model evaluations.

Every negative mutation starts with a complete, accepted request. Assertions
identify the intended field or semantic rejection, rather than accepting any
exception from unrelated missing inputs.
"""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from pipeline_integrity.anode_lifetime import AnodeLifetimeRequest, evaluate_anode_lifetime
from pipeline_integrity.calibrated_emissions import (
    CascadeSourceRequest, FillingVaporRequest, TankLossRequest,
)
from pipeline_integrity.compact_prover import CompactProverRequest, evaluate_compact_prover
from pipeline_integrity.dc_tapped_cable import DCOhmicDriveRequest
from pipeline_integrity.electrical_circuit import (
    ElectricalCircuitRequest, ReactiveCompensationRequest, evaluate_electrical_circuit,
)
from pipeline_integrity.groundbed_fv import ConductiveEarthRequest
from pipeline_integrity.master_meter_proving import MasterMeterProvingRequest, evaluate_master_meter_proving
from pipeline_integrity.parallel_meter_runs import ParallelMeterRunsRequest, observe_parallel_meter_run_change
from pipeline_integrity.saturated_gas_fugacity import SaturatedGasFugacityRequest
from pipeline_integrity.slick_observation import SlickObservationRequest, evaluate_slick_observation
from pipeline_integrity.two_state_current import TwoStateCurrentRequest
from pipeline_integrity.vibrating_density import VibratingDensityRequest, observe_vibrating_element_density


def baseline_requests():
    budget = dict(method='protection.cathodic_budget', asset_id='pipe', location_id='loc',
        clock_id='clock', condition_id='condition', material_id='steel', environment_id='soil',
        current_meaning='protective_demand', evidence_ids=('input',),
        applicability_reviews=dict(bare_fraction_and_demand_basis=True, anode_capacity_utilization=True,
                                   minimum_current_installation_interference=True),
        parameters=dict(time_s=[0., 10.], protected_area_m2=100., bare_surface_fraction=[.1, .1],
            bare_surface_current_density_A_m2=[.02, .02], additional_current_drain_A=[0., 0.],
            anode_electrochemical_capacity_C_kg=1000., anode_utilization_fraction=.8,
            single_anode_mass_kg=10., single_anode_minimum_available_current_A=1., installed_anodes=1))
    compact = dict(meter_id='meter', proving_run_id='run', corrected_reference_volume_m3=1.,
        meter_volume_state_basis_id='state', reference_volume_correction_basis_id='correction',
        prover_sweep_elapsed_time_s=10., whole_pulse_interval_elapsed_time_s=12.,
        whole_meter_pulses_in_interval=100, elapsed_time_clock_basis_id='clock',
        volume_at_declared_meter_state_reviewed=True, clock_scale_and_interval_pairing_reviewed=True,
        representative_stationary_pulse_frequency_reviewed=True)
    density = dict(calibration=dict(instrument_id='density-meter', calibration_id='calibration',
        quadratic_kg_m3_s2=1., linear_kg_m3_s=0., constant_kg_m3=900.,
        period_support_s=(.001, .01), temperature_support_K=(280., 320.),
        pressure_support_pa_abs=(100000., 200000.), density_support_kg_m3=(800., 1000.),
        coefficient_units_and_applicability_reviewed=True), observed_instrument_id='density-meter',
        oscillation_period_s=.005, temperature_K=300., pressure_pa_abs=150000.,
        phase='single_liquid', single_phase_and_measurement_state_reviewed=True)
    master = dict(master_meter_id='master', proving_run_id='run', prover_base_volume_m3=1.,
        supplied_prover_pressure_volume_factor=1., supplied_prover_temperature_volume_factor=1.,
        prover_volume_correction_basis_id='volume-correction', prover_actual_density_kg_m3=900.,
        prover_state_basis_id='prover-state', calibration_turbine_density_kg_m3=900.,
        calibration_turbine_state_basis_id='turbine-state', nominal_turbine_pulse_factor_per_m3=100.,
        nominal_pulse_factor_basis_id='pulse-basis', prover_sweep_elapsed_time_s=10.,
        whole_pulse_interval_elapsed_time_s=12., whole_turbine_pulses_in_interval=100,
        elapsed_time_clock_basis_id='clock', reference_volume_state_and_correction_applicability_reviewed=True,
        synchronized_calibration_mass_window_and_single_phase_reviewed=True,
        clock_scale_and_representative_frequency_reviewed=True)
    return {
        'earth': (ConductiveEarthRequest, dict(x_edges_m=(-2., -1., 0., 1., 2.),
            y_edges_m=(-2., -1., 0., 1., 2.), z_depth_edges_m=(0., 1., 2., 3.),
            conductivity_S_m_by_cell=(.01,)*48,
            electrodes=({'id': 'rod', 'cells_xyz': ((1, 1, 1),)},),
            prescribed_electrode_currents_A={'rod': 1.}, coordinate_frame_id='xyz',
            material_assignment_id='layers', potential_reference_id='remote-boundary',
            heterogeneous_ohmic_mesh_and_finite_exterior_boundary_reviewed=True)),
        'saturated': (SaturatedGasFugacityRequest, dict(temperature_K=300., pressure_Pa_abs=100000.,
            dry_components=({'id': 'nitrogen', 'fraction': 1., 'molar_mass_kg_mol': .028},),
            water_molar_mass_kg_mol=.018, dry_molar_quantity=1., quantity_basis='molar_flow_mol_s',
            clock_id='clock', equilibrium_state_id='equilibrium', condensed_reference_phase='liquid_water',
            condensed_water_fugacity_Pa=3000., gas_water_fugacity_coefficient=1.,
            coefficient_at_final_equilibrium_composition_reviewed=True,
            common_state_condensed_and_gas_fugacity_reference_reviewed=True)),
        'filling': (FillingVaporRequest, dict(liquid_fill_rate_m3_s=.01, tank_capacity_m3=100.,
            displacement_vapor_density_kg_m3=2., displacement_volume_ratio=1.,
            capacity_generation_coefficient_kg_m3_s=.001, reference_capacity_m3=100.,
            latent_enthalpy_J_kg=200000., maximum_liquid_supply_kg_s=1., calibration_id='calibration',
            vapor_state_id='vapor', calibrated_fill_and_capacity_domain_reviewed=True)),
        'tank_loss': (TankLossRequest, dict(episodes=({'duration_s': 10., 'roof': 'fixed',
            'vapor_concentration_kg_m3': 2., 'liquid_density_kg_m3': 900.,
            'calibrated_geometry_environment_id': 'roof-calibration',
            'roof_mechanisms_and_factor_applicability_reviewed': True},),
            accounting_horizon_s=10., calendar_basis_id='declared-window')),
        'cascade': (CascadeSourceRequest, dict(components=({'id': 'fuel', 'liquid_volume_fraction': 1.,
            'liquid_density_kg_m3': 800., 'molar_mass_kg_mol': .08, 'pure_vapor_pressure_Pa': 10000.},),
            liquid_mass_rate_kg_s=1., entrained_dry_air_mass_rate_kg_s=1., air_molar_mass_kg_mol=.029,
            total_pressure_Pa_abs=100000., common_temperature_K=300., available_net_evaporation_heat_W=1000.,
            effective_latent_enthalpy_J_kg=200000., vapor_transfer_effectiveness=.5,
            splash_aerosol_mass_fraction=.1, calibration_id='cascade-calibration',
            common_state_ideal_equilibrium_and_heat_basis_reviewed=True,
            prescribed_interface_composition_and_vapor_selectivity_reviewed=True)),
        'anode': (AnodeLifetimeRequest, dict(budget=budget)),
        'slick': (SlickObservationRequest, dict(appearance='class-a', observed_area_m2=100.,
            calibrated_bands=({'appearance': 'class-a', 'minimum_thickness_m': 1e-6,
                               'maximum_thickness_m': 2e-6},), calibration_id='visual-calibration',
            environment_and_visual_method_reviewed=True)),
        'compact': (CompactProverRequest, compact),
        'vibrating': (VibratingDensityRequest, density),
        'master': (MasterMeterProvingRequest, master),
        'parallel': (ParallelMeterRunsRequest, dict(initial_run_count=3, remaining_run_count=2,
            initial_average_meter_differential_pa=1000., common_branch_state_and_coefficient_basis_id='state',
            identical_parallel_runs_reviewed=True, unchanged_total_flow_reviewed=True,
            frozen_quadratic_meter_differential_law_reviewed=True)),
        'two_state': (TwoStateCurrentRequest, dict(active_state_rms_current_A=10., idle_state_rms_current_A=2.,
            active_time_fraction=.5, cycle_period_s=10., cycle_clock_id='clock', constant_resistance_ohm=1.,
            two_state_rms_and_time_fraction_basis_reviewed=True)),
        'circuit': (ElectricalCircuitRequest, dict(system='dc', load_real_power_W=100., receiving_line_voltage_V=10.,
            load_power_factor=1., load_reactive_direction='none', path_resistance_ohm=1., path_reactance_ohm=0.,
            impedance_basis='complete_loop', steady_state_and_impedance_basis_reviewed=True)),
        'reactive': (ReactiveCompensationRequest, dict(load_real_power_W=100., initial_power_factor=.8,
            target_power_factor=.9, initial_direction='lagging', target_direction='lagging',
            sinusoidal_same_real_power_reviewed=True)),
        'cp_drive': (DCOhmicDriveRequest, dict(net_driving_potential_difference_V=1.,
            complete_path_resistance_ohm=1., potential_reference_id='reference', resistance_basis_id='path',
            common_reference_and_steady_complete_passive_resistive_path_reviewed=True)),
    }


BASELINES = baseline_requests()
UNITS = {
    'earth': ('electrical.earth_electrode_resistance',),
    'saturated': ('mixture.water_saturated_gas',),
    'filling': ('emissions.filling_vapor_rate',),
    'tank_loss': ('emissions.tank_standing_working_loss',),
    'cascade': ('emissions.calibrated_overfill_cascade_source',),
    'anode': ('electrochemistry.anode_charge_lifetime',),
    'slick': ('measurement.slick_appearance_thickness',),
    'compact': ('instrument.compact_prover_two_clock_factor',),
    'vibrating': ('instrument.vibrating_density_calibration',),
    'master': ('metrology.master_meter_calibration',),
    'parallel': ('metrology.parallel_identical_meter_runs',),
    'two_state': ('electrical.two_state_current_rms',),
    'circuit': ('electrical.conductor_impedance_drop', 'electrical.dc_ac_power_balance'),
    'reactive': ('electrical.power_factor_compensation',),
    'cp_drive': ('electrical.cp_current_voltage_balance',),
}


def mark(record_property, key):
    record_property('canonical_unit_ids', ','.join('proposal.'+u for u in UNITS[key]))
    record_property('verification_kind', 'input_contract_only')


@pytest.mark.parametrize('key', tuple(BASELINES))
def test_complete_input_baseline_constructs_without_running_engine(key, record_property):
    mark(record_property, key)
    model, data = BASELINES[key]
    value = model.model_validate(deepcopy(data))
    assert isinstance(value, model)


REQUIRED = [(key, field) for key, (model, _) in BASELINES.items()
            for field, definition in model.model_fields.items() if definition.is_required()]


@pytest.mark.parametrize('key,field', REQUIRED)
def test_omitted_required_input_identifies_exact_missing_field(key, field, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    model.model_validate(deepcopy(original))
    data = deepcopy(original)
    data.pop(field)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    errors = caught.value.errors()
    assert [(e['loc'], e['type']) for e in errors] == [((field,), 'missing')]


NUMERIC = [
    ('earth', ('conductivity_S_m_by_cell', 0), -1.),
    ('saturated', ('pressure_Pa_abs',), 0.),
    ('saturated', ('gas_water_fugacity_coefficient',), -1.),
    ('filling', ('latent_enthalpy_J_kg',), 0.),
    ('filling', ('liquid_fill_rate_m3_s',), -1.),
    ('tank_loss', ('episodes', 0, 'duration_s'), 0.),
    ('cascade', ('splash_aerosol_mass_fraction',), 1.1),
    ('cascade', ('components', 0, 'molar_mass_kg_mol'), 0.),
    ('anode', ('maximum_iterations',), 0),
    ('slick', ('observed_area_m2',), -1.),
    ('compact', ('whole_meter_pulses_in_interval',), 1.5),
    ('vibrating', ('oscillation_period_s',), 0.),
    ('master', ('whole_turbine_pulses_in_interval',), 1.5),
    ('parallel', ('remaining_run_count',), 0),
    ('two_state', ('active_time_fraction',), 1.1),
    ('circuit', ('path_resistance_ohm',), -1.),
    ('reactive', ('target_power_factor',), 1.1),
    ('cp_drive', ('complete_path_resistance_ohm',), 0.),
]


def replace_path(data, path, replacement):
    if not path:
        return replacement
    key, *rest = path
    if isinstance(data, tuple):
        values = list(data)
        values[key] = replace_path(values[key], rest, replacement)
        return tuple(values)
    copied = deepcopy(data)
    copied[key] = replace_path(copied[key], rest, replacement) if rest else replacement
    return copied


def assert_target_numeric_error(errors, path):
    assert path in [e['loc'] for e in errors]
    # Pydantic can also report the containing tuple's minimum length when an
    # invalid member was rejected. That is the same mutation, not a second
    # missing-input failure; require every extra error to be that ancestor.
    for error in errors:
        if error['loc'] != path:
            assert error['type'] == 'too_short'
            assert path[:len(error['loc'])] == error['loc']


@pytest.mark.parametrize('key,path,bad', NUMERIC)
def test_bad_numeric_input_rejects_target_location(key, path, bad, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    model.model_validate(deepcopy(original))
    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_path(original, path, bad))
    assert_target_numeric_error(caught.value.errors(), path)


UNIT_STRINGS = [('saturated', 'pressure_Pa_abs'), ('filling', 'latent_enthalpy_J_kg'),
                ('cascade', 'total_pressure_Pa_abs'), ('slick', 'observed_area_m2'),
                ('compact', 'corrected_reference_volume_m3'), ('vibrating', 'pressure_pa_abs'),
                ('master', 'prover_base_volume_m3'), ('circuit', 'receiving_line_voltage_V'),
                ('cp_drive', 'complete_path_resistance_ohm')]


@pytest.mark.parametrize('key,field', UNIT_STRINGS)
def test_embedded_unit_string_is_not_silently_parsed_as_si(key, field, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    data = deepcopy(original)
    data[field] = '1 bar'
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    assert [(e['loc'], e['type']) for e in caught.value.errors()] == [((field,), 'float_type')]


@pytest.mark.parametrize('key', tuple(BASELINES))
@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_input_cannot_cross_numeric_boundary(key, number, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    path = next(path for candidate, path, _ in NUMERIC if candidate == key)
    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_path(original, path, number))
    assert_target_numeric_error(caught.value.errors(), path)


SEMANTIC = [
    ('earth', ('conductivity_S_m_by_cell',), (.01,)*47, 'one conductivity per cell'),
    ('earth', ('prescribed_electrode_currents_A',), {'other': 1.}, 'every electrode'),
    ('earth', ('grounded_faces',), (), 'at least one distinct'),
    ('earth', ('x_edges_m',), (-2., -1., -1., 1., 2.), 'strictly increase'),
    ('saturated', ('dry_components', 0, 'fraction'), .5, 'fractions must sum to one'),
    ('saturated', ('water_component_id',), 'nitrogen', 'dry carrier must exclude'),
    ('saturated', ('condensed_water_fugacity_Pa',), 100000., 'strictly between zero and one'),
    ('tank_loss', ('accounting_horizon_s',), 20., 'partition the accounting horizon'),
    ('tank_loss', ('episodes', 0, 'rim_seal_mass_conductance_kg_s_Pa'), 1., 'fixed roof has no'),
    ('cascade', ('source_duration_s',), 1., 'duration, fuel mass concentration and depth'),
    ('cascade', ('components', 0, 'liquid_volume_fraction'), .5, 'volume fractions must sum to one'),
    ('two_state', ('cycle_clock_id',), None, 'clock identity'),
]


@pytest.mark.parametrize('key,path,bad,message', SEMANTIC)
def test_semantic_input_inconsistency_is_rejected_before_engine(key, path, bad, message, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    model.model_validate(deepcopy(original))
    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_path(original, path, bad))
    errors = caught.value.errors()
    semantic_errors = [e for e in errors if e['type'] == 'value_error']
    assert len(semantic_errors) == 1
    intended = semantic_errors[0]
    assert message in intended['msg']
    assert path[:len(intended['loc'])] == intended['loc']
    for error in errors:
        if error is not intended:
            assert error['type'] == 'too_short'
            assert intended['loc'][:len(error['loc'])] == error['loc']


@pytest.mark.parametrize('key,field,evaluator', [
    ('compact', 'corrected_reference_volume_m3', evaluate_compact_prover),
    ('master', 'supplied_prover_pressure_volume_factor', evaluate_master_meter_proving),
    ('parallel', 'remaining_run_count', observe_parallel_meter_run_change),
    ('vibrating', 'oscillation_period_s', observe_vibrating_element_density),
])
def test_nullable_missing_input_returns_specific_needs_data(key, field, evaluator, record_property):
    mark(record_property, key)
    model, original = BASELINES[key]
    model.model_validate(deepcopy(original))
    data = deepcopy(original)
    data.pop(field)
    result = evaluator(model.model_validate(data))
    assert result.status == 'needs_data'
    assert result.model_dump().get('density_kg_m3') is None
    assert field in ' '.join(result.issues)


def test_density_calibration_instrument_mismatch_rejected_before_polynomial(record_property):
    mark(record_property, 'vibrating')
    model, original = BASELINES['vibrating']
    request = model.model_validate({**deepcopy(original), 'observed_instrument_id': 'other-device'})
    result = observe_vibrating_element_density(request)
    assert result.status == 'out_of_scope'
    assert 'does not match' in ' '.join(result.issues)
    assert result.density_kg_m3 is None


def test_wrong_anode_model_rejected_without_material_engine(monkeypatch, record_property):
    mark(record_property, 'anode')
    model, original = BASELINES['anode']
    data = deepcopy(original)
    data['budget'].update(method='corrosion.coupon_loss', current_meaning='not_applicable',
                          applicability_reviews={})
    request = model.model_validate(data)
    def forbidden(*args, **kwargs):
        raise AssertionError('material engine must not run on wrong-model input')
    monkeypatch.setattr('pipeline_integrity.anode_lifetime.evaluate_electrochemical', forbidden)
    with pytest.raises(ValueError, match='existing cathodic charge budget'):
        evaluate_anode_lifetime(request, None)


def test_unmapped_slick_appearance_does_not_create_volume(record_property):
    mark(record_property, 'slick')
    model, original = BASELINES['slick']
    request = model.model_validate({**deepcopy(original), 'appearance': 'unmapped'})
    result = evaluate_slick_observation(request)
    assert result['status'] == 'needs_data'
    assert 'minimum_volume_m3' not in result


def test_duplicate_slick_calibration_category_rejected_before_volume(record_property):
    mark(record_property, 'slick')
    model, original = BASELINES['slick']
    data = deepcopy(original)
    data['calibrated_bands'] = (data['calibrated_bands'][0], data['calibrated_bands'][0])
    with pytest.raises(ValueError, match='appearance categories must be unique'):
        evaluate_slick_observation(model.model_validate(data))


def test_neighboring_distinct_electrodes_cannot_hide_a_metal_short(record_property):
    mark(record_property, 'earth')
    model, original = BASELINES['earth']
    data = deepcopy(original)
    data['electrodes'] += ({'id': 'other-rod', 'cells_xyz': ((2, 1, 1),)},)
    data['prescribed_electrode_currents_A']['other-rod'] = 1.
    with pytest.raises(ValidationError, match='distinct electrode groups cannot share a metal face'):
        model.model_validate(data)


def test_duplicate_dry_species_rejected_without_fugacity_calculation(record_property):
    mark(record_property, 'saturated')
    model, original = BASELINES['saturated']
    data = deepcopy(original)
    data['dry_components'] = tuple({**data['dry_components'][0], 'fraction': .5} for _ in range(2))
    with pytest.raises(ValidationError, match='component identities must be unique'):
        model.model_validate(data)


def test_dc_reactive_coordinate_rejected_before_current_owner(monkeypatch, record_property):
    mark(record_property, 'circuit')
    model, original = BASELINES['circuit']
    data = {**deepcopy(original), 'load_power_factor': .9, 'load_reactive_direction': 'lagging'}
    request = model.model_validate(data)
    def forbidden(*args, **kwargs):
        raise AssertionError('current owner must not run on incompatible DC coordinates')
    monkeypatch.setattr('fluids.pump.current_ideal', forbidden)
    with pytest.raises(ValueError, match='DC requires unity factor'):
        evaluate_electrical_circuit(request)
