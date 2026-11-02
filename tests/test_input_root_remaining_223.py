"""Focused valid-baseline input contracts for the remaining method families."""
import json
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from pipeline_integrity import acoustic_cavity_modes as cavity, compressor_volume_coordinate as compressor
from pipeline_integrity import elbow_moment_coordinate as elbow_moment, elbow_pressure_stress as elbow
from pipeline_integrity import horizontal_jet as jet, liquid_orifice as opening, leak_inverse_relations as inverse
from pipeline_integrity import horizontal_two_phase_friction as horizontal, inclined_two_phase_segment as inclined
from pipeline_integrity import mass_balance_section as mass, offshore_inventory_release as inventory
from pipeline_integrity import offshore_release_ledger as ledger, point_illuminance as optics
from pipeline_integrity import porous_release as porous, richards_fv as richards
from pipeline_integrity import reviewed_dynamic_correlations as dynamic, sensing_location as sensing
from pipeline_integrity import turbulent_constitutive_friction as turbulent, vapor_tube as vapor


@dataclass
class Case:
    unit: str
    request: object


def _cases():
    yield Case('acoustics.supplied_compressor_volume_correlation', compressor.CompressorSurgeVolumeRequest(
        net_displaced_volume_per_revolution_m3=.01, isentropic_exponent=1.4,
        suction_temperature_k=300., molar_mass_kg_mol=.028, temperature_reference_k=300.,
        molar_mass_reference_kg_mol=.028, stage_pressure_ratio=2.,
        supplied_suction_multiplier=1., supplied_discharge_multiplier=1.,
        coefficient_gas_state_unit_and_applicability_basis_id='reviewed-SI-basis'))
    yield Case('acoustics.uniform_pressure_standing_modes', cavity.AcousticCavityModeRequest(
        asset_id='pipe', clock_id='clock', length_m=10., sound_speed_m_s=340.,
        left_boundary='rigid_closed', right_boundary='pressure_release_open',
        property_geometry_boundary_evidence_id='review'))
    yield Case('conservation.observed_section_mass', mass.SectionMassBalanceRequest(clock_id='clock',
        window_start_s=0., window_finish_s=10., inlet_masses_kg={'inlet': 10.},
        outlet_masses_kg={'outlet': 9.}, initial_inventory_mass_kg=100., final_inventory_mass_kg=101.))
    yield Case('elbow_bending_stress_limit', elbow_moment.ElbowMomentCoordinateRequest(
        loading_basis='in_plane', moment_components_nm=(10., 0., 0.), section_modulus_m3=.001,
        wall_thickness_m=.005, bend_centerline_radius_m=.3, mean_pipe_radius_m=.05,
        supplied_yield_stress_pa=2e8, supplied_stress_index_numerator=1.,
        supplied_plastic_limit_to_first_yield_moment_ratio=1.5,
        index_loading_axes_and_material_basis_id='loading-material'))
    yield Case('elbow_pressure_stress', elbow.ElbowPressureStressRequest(pressure_difference_pa=1e5,
        outside_diameter_m=.1, wall_m=.005, centreline_bend_radius_m=.3,
        circumferential_angles_rad=(0., 1.), maximum_reviewed_wall_inside_radius_ratio=.2))
    yield Case('fluid.horizontal_jet_discharge', jet.HorizontalJetObservation(horizontal_distance_m=1.,
        vertical_drop_m=.5, gravity_m_s2=9.81, actual_normal_jet_area_m2=.01,
        tracked_to_section_mean_velocity_factor=1.))
    yield Case('leak.incompressible_small_hole', opening.LiquidOpeningReleaseRequest(opening_area_m2=.001,
        upstream_available_pressure_pa_abs=2e5, receiver_static_pressure_pa_abs=1e5,
        liquid_density_kg_m3=1000., supplied_discharge_coefficient=.6,
        liquid_state_basis_id='water-state', coefficient_geometry_basis_id='hole-calibration',
        available_pressure_head_and_stationary_opening_reviewed=True,
        noncavitating_single_liquid_incompressible_domain_reviewed=True,
        supplied_coefficient_applicability_reviewed=True))
    yield Case('leak.supplied_small_discrepancy_location', inverse.DiscrepancyLocationRequest(
        upstream_discrepancy_m3_s=.01, downstream_discrepancy_m3_s=.02, section_length_m=100.))
    common = dict(total_mass_flow_kg_s=1., gas_mass_quality=.1, liquid_density_kg_m3=800.,
        gas_density_kg_m3=2., liquid_dynamic_viscosity_Pa_s=.001,
        gas_dynamic_viscosity_Pa_s=1e-5, internal_diameter_m=.1, length_m=1.,
        phase_property_state_basis='common-P-T-composition')
    yield Case('multiphase.lockhart_martinelli_loss', horizontal.HorizontalTwoPhaseFrictionRequest(**common))
    yield Case('multiphase.hilly_liquid_holdup_loss', inclined.InclinedTwoPhaseSegmentRequest(**common,
        surface_tension_N_m=.03, inclination_deg=5., local_absolute_pressure_Pa=1e6))
    yield Case('optics.point_source_illuminance', optics.PointIlluminanceRequest(surface_point_m=(0., 0., 0.),
        outward_surface_normal=(0., 0., 1.), sources=(optics.PointLight(id='lamp', position_m=(0., 0., 2.),
            directional_luminous_intensity_cd=100., transmission_fraction=1.),)))
    yield Case('porous.constant_pressure_sharp_front', porous.SphericalPorousFrontRequest(source_radius_m=.01,
        permeability_m2=1e-12, dynamic_viscosity_pa_s=.001, porosity=.3, driving_pressure_pa=1e5,
        times_s=(0., 1.), source_center_depth_m=1.))
    yield Case('porous.richards_unsaturated_flow', richards.RichardsMesh(cell_volumes_m3=(1., 1.),
        cell_elevations_m=(0., 1.), interior_faces=(richards.InteriorDarcyFace(cell_i=0, cell_j=1,
            area_m2=1., center_distance_m=1.),), orthogonal_two_point_metrics_reviewed=True))
    yield Case('release.offshore_advanced_inventory', inventory.OffshoreInventoryRequest(pipe_volume_m3=10.,
        initial_pressure_Pa=2e5, initial_temperature_K=300., ambient_pressure_Pa=1e5,
        ambient_temperature_K=300., composition_id='mixture', property_basis_id='PVT',
        reference_volume_basis='reference-state', reference_oil_density_kg_m3=800.,
        reference_gas_density_kg_m3=1.2, reference_gas_oil_volume_ratio=10.,
        pre_shutin_oil_reference_flow_m3_s=0., time_until_shutin_s=1.))
    yield Case('release.offshore_fast_ledger', ledger.OffshoreReleaseLedgerRequest(initial_pipe_liquid_volume_m3=10.,
        maximum_release_fraction=.5, gas_oil_reduction_factor=.8, pre_shutin_feed_volume_flow_m3_s=.1,
        time_until_shutin_s=5., reference_volume_basis='reference-state', factor_source_id='reviewed-factor'))
    for unit, mode in [('rheology.potapov_yield_turbulent', 'viscoplastic'),
                       ('rheology.romanova_powerlaw_turbulent', 'power_law')]:
        yield Case(unit, turbulent.TurbulentResistanceRequest(model=mode, reynolds_coordinate=1e5,
            flow_index=.8 if mode == 'power_law' else None, hedstrom_number=100. if mode == 'viscoplastic' else None))
    yield Case('sensing.electromagnetic_cable_flight', inverse.DielectricCableRangingRequest(
        relative_permittivity=2., elapsed_round_trip_s=1e-6, cable_length_m=1000.))
    yield Case('sensing.uniform_resistive_cable_coordinate', sensing.ResistiveCableRequest(
        voltage_v=1., current_a=.1, resistance_per_length_ohm_m=.1,
        resistance_basis='complete_loop', cable_length_m=1000.))
    yield Case('spill.constant_feed_viscous_spread', porous.SurfaceViscousSpillRequest(feed_m3_s=.001,
        kinematic_viscosity_m2_s=1e-5, times_s=(0., 1.)))
    yield Case('transport.ideal_sensor_tube_parcel', vapor.VaporTubeDelayRequest(distance_to_detector_m=10.,
        air_speed_m_s=1., operation='continuous'))
    yield Case('vent.supplied_empirical_area_correlation', dynamic.DeflagrationVentCoordinateRequest(
        enclosure_internal_surface_area_m2=10., reduced_pressure_differential_pa=1000.,
        supplied_fuel_geometry_coefficient_sqrt_pa=1.,
        fuel_geometry_pressure_reference_and_coefficient_units_basis_id='reviewed-coefficient'))
    yield Case('waves.prescribed_one_dimensional_travel_time', inverse.ReflectionLocationRequest(
        sensor_to_interface_m=100., direct_arrival_s=1., reflected_arrival_s=2., wave_speed_m_s=1000.))


CASES = list(_cases())


@pytest.mark.parametrize('case', CASES, ids=[c.unit for c in CASES])
def test_additional_valid_request_construction(case, record_property):
    record_property('canonical_units', json.dumps([case.unit]))
    assert type(case.request).model_validate(case.request.model_dump()) == case.request


OMISSIONS = [(c, f) for c in CASES for f, info in type(c.request).model_fields.items() if info.is_required()]


@pytest.mark.parametrize('case,field', OMISSIONS, ids=[c.unit + ':' + f for c, f in OMISSIONS])
def test_additional_required_field_from_valid_baseline(case, field, record_property):
    record_property('canonical_units', json.dumps([case.unit]))
    data = case.request.model_dump()
    del data[field]
    with pytest.raises(ValidationError) as error:
        type(case.request).model_validate(data)
    assert any(e['loc'] == (field,) and e['type'] == 'missing' for e in error.value.errors())


NUMERIC = [(c, f) for c in CASES for f, value in c.request.model_dump().items() if isinstance(value, float)]


@pytest.mark.parametrize('case,field', NUMERIC, ids=[c.unit + ':' + f for c, f in NUMERIC])
@pytest.mark.parametrize('bad', [float('nan'), '1 unknown_unit', True])
def test_additional_bad_numeric_from_valid_baseline(case, field, bad, record_property):
    record_property('canonical_units', json.dumps([case.unit]))
    with pytest.raises(ValidationError) as error:
        type(case.request).model_validate({**case.request.model_dump(), field: bad})
    assert any(e['loc'] and e['loc'][0] == field for e in error.value.errors())


@pytest.mark.parametrize('unit,updates,reason', [
    ('conservation.observed_section_mass', {'window_finish_s': 0.}, 'positive observation window'),
    ('elbow_pressure_stress', {'wall_m': .05}, 'positive bore'),
    ('elbow_pressure_stress', {'centreline_bend_radius_m': .04}, 'bend centreline radius'),
    ('porous.constant_pressure_sharp_front', {'source_center_depth_m': .005}, 'deeper than its radius'),
    ('porous.constant_pressure_sharp_front', {'porosity': 0.}, 'porosity must be positive'),
    ('porous.richards_unsaturated_flow', {'cell_elevations_m': (0.,)}, 'aligned cell'),
    ('rheology.romanova_powerlaw_turbulent', {'friction_search_bounds': (.1, .01)}, 'interval must increase'),
    ('rheology.potapov_yield_turbulent', {'friction_search_bounds': (.1, .01)}, 'interval must increase'),
])
def test_additional_cross_field_contradiction(unit, updates, reason, record_property):
    record_property('canonical_units', json.dumps([unit]))
    q = next(c.request for c in CASES if c.unit == unit)
    with pytest.raises(ValidationError, match=reason):
        type(q).model_validate({**q.model_dump(), **updates})


def test_liquid_reverse_pressure_rejected_before_native_relation(monkeypatch, record_property):
    record_property('canonical_units', json.dumps(['leak.incompressible_small_hole']))
    import fluids.core
    monkeypatch.setattr(fluids.core, 'dP_from_K', lambda *a, **k: pytest.fail('invalid release reached native relation'))
    q = next(c.request for c in CASES if c.unit == 'leak.incompressible_small_hole')
    result = opening.evaluate_liquid_opening_release(q.model_copy(update={'receiver_static_pressure_pa_abs': 3e5}))
    assert result.status == 'out_of_scope' and any('reverse release' in issue for issue in result.issues)


def test_elbow_named_plane_rejects_other_components_before_stress(monkeypatch, record_property):
    record_property('canonical_units', json.dumps(['elbow_bending_stress_limit']))
    q = next(c.request for c in CASES if c.unit == 'elbow_bending_stress_limit')
    monkeypatch.setattr(elbow_moment.math, 'hypot', lambda *a: pytest.fail('invalid loading reached stress calculation'))
    with pytest.raises(ValueError, match='single-plane basis'):
        elbow_moment.evaluate_elbow_moment_coordinate(q.model_copy(update={'moment_components_nm': (10., 1., 0.)}))


@pytest.mark.parametrize('left,right,limit', [
    ('rigid_closed', 'rigid_closed', 4), ('rigid_closed', 'pressure_release_open', 4),
    ('pressure_release_open', 'rigid_closed', 4), ('pressure_release_open', 'pressure_release_open', 3),
])
def test_acoustic_modes_mesh_boundary_conflict_before_native_assembly(left, right, limit, monkeypatch, record_property):
    record_property('canonical_units', json.dumps(['acoustics.uniform_pressure_standing_modes']))
    q = next(c.request for c in CASES if c.unit == 'acoustics.uniform_pressure_standing_modes')
    valid = type(q).model_validate({**q.model_dump(), 'elements': 4,
        'left_boundary': left, 'right_boundary': right, 'requested_modes': limit})
    monkeypatch.setattr(cavity, 'build_native_frame', lambda *a, **k: pytest.fail('invalid modes reached native assembly'))
    with pytest.raises(ValidationError, match='degrees of freedom'):
        cavity.evaluate_acoustic_cavity_modes(valid.model_copy(update={'requested_modes': limit + 1}), None)


def test_repeated_light_identity_is_not_combined_silently(record_property):
    record_property('canonical_units', json.dumps(['optics.point_source_illuminance']))
    q = next(c.request for c in CASES if c.unit == 'optics.point_source_illuminance')
    with pytest.raises(ValidationError, match='identifiers must be unique'):
        type(q).model_validate({**q.model_dump(), 'sources': (q.sources[0], q.sources[0])})


@pytest.mark.parametrize('field,value,reason', [
    ('composition_id', 'other-mixture', 'common declared composition'),
    ('property_basis_id', 'other-PVT', 'common declared composition'),
    ('pressure_Pa', 3e5, 'requested absolute pressure'),
    ('temperature_K', 350., 'requested absolute pressure'),
])
def test_offshore_wrong_state_identity_before_inventory_arithmetic(field, value, reason, record_property):
    record_property('canonical_units', json.dumps(['release.offshore_advanced_inventory']))
    q = next(c.request for c in CASES if c.unit == 'release.offshore_advanced_inventory')
    state = inventory.OffshorePhaseState(pressure_Pa=q.initial_pressure_Pa,
        temperature_K=q.initial_temperature_K, oil_density_kg_m3=800., free_gas_density_kg_m3=2.,
        oil_volume_fraction=.9, composition_id=q.composition_id, property_basis_id=q.property_basis_id)
    bad = q.model_copy(update={'initial_state': state.model_copy(update={field: value}),
        'equilibrium_phase_states_and_common_pvt_basis_reviewed': True,
        'released_material_has_reference_gas_oil_ratio_reviewed': True,
        'single_horizontal_full_bore_segment_reviewed': True})
    with pytest.raises(ValueError, match=reason):
        inventory.evaluate_offshore_inventory_release(bad)


def test_reflection_reversed_arrivals_before_range_owner(monkeypatch, record_property):
    record_property('canonical_units', json.dumps(['waves.prescribed_one_dimensional_travel_time']))
    q = next(c.request for c in CASES if c.unit == 'waves.prescribed_one_dimensional_travel_time')
    monkeypatch.setattr(inverse, 'locate_time_of_flight', lambda *a, **k: pytest.fail('invalid arrival order reached range owner'))
    with pytest.raises(ValueError, match='must not precede'):
        inverse.locate_from_known_interface_reflection(q.model_copy(update={
            'known_constant_pressure_interface_and_uniform_speed_reviewed': True, 'reflected_arrival_s': 0.}))


def test_compressor_expansion_coordinate_rejected_without_volume_prediction(record_property):
    record_property('canonical_units', json.dumps(['acoustics.supplied_compressor_volume_correlation']))
    q = next(c.request for c in CASES if c.unit == 'acoustics.supplied_compressor_volume_correlation')
    result = compressor.evaluate_compressor_surge_volume(q.model_copy(update={
        'stage_pressure_ratio': .5, 'coefficient_reference_scales_and_gas_state_reviewed': True,
        'supplied_minimum_volume_m3': 0.}))
    assert result['status'] == 'out_of_scope' and any('expansion' in issue for issue in result['issues'])


def test_turbulent_powerlaw_support_rejected_before_root(monkeypatch, record_property):
    record_property('canonical_units', json.dumps(['rheology.romanova_powerlaw_turbulent']))
    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, 'brentq', lambda *a, **k: pytest.fail('unsupported material reached root solver'))
    q = next(c.request for c in CASES if c.unit == 'rheology.romanova_powerlaw_turbulent')
    result = turbulent.evaluate_turbulent_resistance(q.model_copy(update={
        'turbulent_regime_coordinate_and_calibration_reviewed': True, 'flow_index': .1}))
    assert result.status == 'out_of_scope' and any('index interval' in issue for issue in result.issues)
