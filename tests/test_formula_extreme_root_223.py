"""Independent references and numeric limits for the root engineering cohort."""
from __future__ import annotations

import json
import math
import runpy
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(None)
def old(name):
    return runpy.run_path(str(ROOT / 'tests' / name))


def reference(file, name, **overrides):
    function = old(file)[name]
    parameters = {}
    for mark in getattr(function, 'pytestmark', ()):
        if mark.name == 'parametrize':
            names = mark.args[0]
            names = [n.strip() for n in names.split(',')] if isinstance(names, str) else list(names)
            row = mark.args[1][0]
            values = row.values if hasattr(row, 'values') else (row,) if len(names) == 1 else row
            parameters.update(zip(names, values))
    parameters.update(overrides)
    function(**parameters)


REFERENCES = [
    ('fluid_pipe.closed_mass_compatibility', 'formula', 'test_closed_liquid_compatibility_individual.py', 'test_affine_specific_volume_exact_thermal_pressure_reference', {'dt': 2.}),
    ('fluid_pipe.closed_mass_compatibility', 'extreme', 'test_closed_liquid_compatibility_individual.py', 'test_affine_specific_volume_exact_thermal_pressure_reference', {'dt': 0.}),
    ('leak.incompressible_small_hole', 'formula', 'test_liquid_opening_release_independent.py', 'test_supplied_coefficient_decimal_head_reference', {'cd': .5}),
    ('leak.incompressible_small_hole', 'extreme', 'test_liquid_opening_release_independent.py', 'test_extreme_nonzero_flux_is_not_silently_clipped', {}),
    ('leak.incompressible_small_hole', 'extreme', 'test_liquid_opening_release_independent.py', 'test_zero_area_is_closed_not_an_invented_jet', {}),
    ('multiphase.hilly_liquid_holdup_loss', 'formula', 'test_inclined_two_phase_segment_individual.py', 'test_independent_wallwork_plus_exact_elevation_in_distributed_limit', {'fraction': .9, 'angle': 30.}),
    ('multiphase.hilly_liquid_holdup_loss', 'extreme', 'test_inclined_two_phase_segment_individual.py', 'test_zero_length', {}),
    ('multiphase.lockhart_martinelli_loss', 'formula', 'test_horizontal_two_phase_friction_individual.py', 'test_four_regimes_independent_additive_interaction_oracle', {}),
    ('multiphase.lockhart_martinelli_loss', 'extreme', 'test_horizontal_two_phase_friction_individual.py', 'test_single_phase_endpoint_matches_physical_pipe_friction', {}),
    ('porous.constant_pressure_sharp_front', 'formula', 'test_porous_release_independent.py', 'test_sphere_radial_pressure_drop_and_mass_storage', {}),
    ('porous.constant_pressure_sharp_front', 'extreme', 'test_porous_release_independent.py', 'test_sphere_independent_time_integral', {'time': 1e-12}),
    ('porous.constant_pressure_sharp_front', 'extreme', 'test_porous_release_independent.py', 'test_sphere_independent_time_integral', {'time': 1e12}),
    ('porous.richards_unsaturated_flow', 'formula', 'test_richards_fv_independent.py', 'test_actual_nonlinear_content_one_cell_flux_not_capacity_shortcut', {}),
    ('release.offshore_advanced_inventory', 'formula', 'test_offshore_inventory_release_individual.py', 'test_component_parcels_determine_independent_reference_volume', {}),
    ('release.offshore_advanced_inventory', 'extreme', 'test_offshore_inventory_release_individual.py', 'test_equal_states_zero_inventory_release_and_immediate_shutin', {}),
    ('release.offshore_fast_ledger', 'formula', 'test_offshore_release_ledger_individual.py', 'test_same_basis_si_aggregation_matches_independent_us_volume_and_time_coordinates', {}),
    ('release.offshore_fast_ledger', 'extreme', 'test_offshore_release_ledger_individual.py', 'test_complete_factor_domain_grid_preserves_initial_plus_feed_volume_budget', {}),
    ('rheology.potapov_yield_turbulent', 'formula', 'test_turbulent_constitutive_individual.py', 'test_viscoplastic_root_matches_sixty_digit_direct_bisection', {'he': 1000.}),
    ('rheology.potapov_yield_turbulent', 'extreme', 'test_turbulent_constitutive_individual.py', 'test_zero_yield_limit_matches_smooth_newtonian_log_law', {'re': 1e200}),
    ('rheology.romanova_powerlaw_turbulent', 'formula', 'test_turbulent_constitutive_individual.py', 'test_power_law_implicit_root_matches_analytic_lambert_or_exact_n_two', {'n': 2., 're': 1e5}),
    ('rheology.romanova_powerlaw_turbulent', 'extreme', 'test_turbulent_constitutive_individual.py', 'test_power_law_implicit_root_matches_analytic_lambert_or_exact_n_two', {'n': 2., 're': 1e200}),
    ('spill.constant_feed_viscous_spread', 'formula', 'test_porous_release_independent.py', 'test_surface_similarity_value_and_zero', {}),
]


@pytest.mark.parametrize('unit,kind,file,function,parameters', REFERENCES,
    ids=[f'{u}:{k}:{f}:{i}' for i, (u,k,_,f,_) in enumerate(REFERENCES)])
def test_selected_independent_reference(unit, kind, file, function, parameters, record_property):
    record_property('canonical_units', json.dumps([unit])); record_property('test_kind', kind)
    reference(file, function, **parameters)


def annotate(record_property, unit, kind):
    record_property('canonical_units', json.dumps([unit])); record_property('test_kind', kind)


def acoustic(speed=400., length=10.):
    from pipeline_integrity.acoustic_cavity_modes import AcousticCavityModeRequest, evaluate_acoustic_cavity_modes
    from pipeline_integrity.contracts import EvidenceRef, RunContext
    q = AcousticCavityModeRequest(asset_id='pipe', clock_id='clock', length_m=length,
        sound_speed_m_s=speed, left_boundary='rigid_closed', right_boundary='pressure_release_open',
        elements=32, requested_modes=1, property_geometry_boundary_evidence_id='analytic',
        uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed=True)
    c = RunContext(asset_id='pipe', scenario_id='cavity', clock_id='clock', origin='synthetic',
        evidence=(EvidenceRef(id='analytic', citation='Quarter-wavelength standing-wave boundary solution', origin='synthetic'),))
    return evaluate_acoustic_cavity_modes(q, c)


def test_cavity_quarter_wave_reference(record_property):
    annotate(record_property, 'acoustics.uniform_pressure_standing_modes', 'formula')
    result = acoustic()
    assert result['status'] == 'computed'
    # Linear FE eigenfrequency has O(h²) error;32 elements imply <2e-4 here.
    assert result['modes'][0]['frequency_hz'] == pytest.approx(10., rel=2e-4, abs=0)


@pytest.mark.parametrize('speed', [1e-100, 1e100])
def test_cavity_extreme_wave_speed_retains_finite_scaled_modes(speed, record_property):
    annotate(record_property, 'acoustics.uniform_pressure_standing_modes', 'extreme')
    result = acoustic(speed)
    assert result['status'] == 'computed'
    mode = result['modes'][0]
    assert math.isfinite(mode['relative_eigen_residual'])
    assert mode['frequency_hz'] > 0
    assert mode['frequency_hz'] == pytest.approx(speed / 40, rel=2e-4, abs=0)


def pit(rate=.001, duration=2.):
    from pipeline_integrity.growing_pit_leak import GrowingPitLeakRequest, LeakHistoryPoint, evaluate_growing_pit_leak
    q = GrowingPitLeakRequest(geometry='spherical_intersection', density_kg_m3=1000., gravity_m_s2=2.,
        wall_thickness_m=.01, initial_pit_radius_m=.01, radial_growth_m_s=rate,
        history=(LeakHistoryPoint(time_s=0., liquid_head_m=1., discharge_coefficient=.5),
                 LeakHistoryPoint(time_s=duration, liquid_head_m=1., discharge_coefficient=.5)))
    return evaluate_growing_pit_leak(q)


def test_growing_pit_integrated_geometry_known_answer(record_property):
    annotate(record_property, 'leak.growing_tank_hole_integral', 'formula')
    result = pit()
    assert result.status == 'computed'
    # Unit flux speed; integrated sphere intersection is32π/750000 cubic metres.
    assert result.cumulative_volume_m3[-1] == pytest.approx(32 * math.pi / 750000, rel=1e-12, abs=0)
    assert result.cumulative_mass_kg[-1] == pytest.approx(1000 * result.cumulative_volume_m3[-1])


@pytest.mark.parametrize('rate', [0., 1e-200])
def test_growing_pit_extreme_rate_does_not_lose_representable_opening(rate, record_property):
    annotate(record_property, 'leak.growing_tank_hole_integral', 'extreme')
    result = pit(rate, 1.)
    assert result.status == 'computed'
    if rate == 0:
        assert result.cumulative_volume_m3[-1] == 0
    else:
        assert result.opening_areas_m2[-1] > 0
        assert result.cumulative_volume_m3[-1] > 0
        assert result.cumulative_volume_m3[-1] == pytest.approx(math.pi * .01 * rate, rel=1e-12, abs=0)


def laminar(mode, radius=.05, tau=None):
    from pipeline_integrity.slurry_transport import LaminarPressureFlowRequest, evaluate_laminar_pressure_flow
    props = runpy.run_path(str(ROOT / 'examples/slurry_transport.py'))['make_case'](reviewed=True)[0].properties
    tau = (10. if mode == 'bingham' else 1.) if tau is None else tau
    props = type(props).model_validate({**props.model_dump(), 'rheology': mode,
        'consistency_Pa_s_n': .1 if mode == 'bingham' else 1., 'flow_index': 1. if mode == 'bingham' else .5,
        'yield_stress_Pa': 5. if mode == 'bingham' else 0., 'mixture_density_kg_m3': 1000.,
        'solid_volume_fraction': 0., 'particle_diameter_m': None,
        'maximum_particle_to_pipe_diameter_ratio': None, 'supported_absolute_velocity_m_s': (0., 10.)})
    q = LaminarPressureFlowRequest(properties=props, inner_diameter_m=2*radius, length_m=200*radius,
        driving_pressure_difference_Pa=400*tau, radial_positions_fraction=(0., .5, 1.),
        laminar_homogeneous_no_slip_domain_reviewed=True)
    return evaluate_laminar_pressure_flow(q)


@pytest.mark.parametrize('mode,expected,axis', [('bingham',17*math.pi/15360,.625), ('power_law',math.pi/40000,1/60)])
def test_pressure_driven_constitutive_known_answers(mode, expected, axis, record_property):
    annotate(record_property, 'rheology.' + ('bingham_circular_laminar' if mode=='bingham' else 'power_law_circular_laminar'), 'formula')
    result = laminar(mode)
    assert result.signed_volume_flow_m3_s == pytest.approx(expected, rel=2e-12, abs=0)
    assert result.axial_velocity_m_s[0] == pytest.approx(axis, rel=2e-12, abs=0)
    assert result.axial_velocity_m_s[-1] == 0


def test_bingham_subyield_extreme_is_exact_no_flow(record_property):
    annotate(record_property, 'rheology.bingham_circular_laminar', 'extreme')
    result = laminar('bingham', tau=4.)
    assert result.signed_volume_flow_m3_s == result.dissipated_power_W == 0
    assert result.plug_radius_fraction == 1


@pytest.mark.parametrize('radius,tau', [(1e-100,1.), (1e110,1e-150)])
def test_powerlaw_extreme_geometry_has_representable_flow(radius, tau, record_property):
    annotate(record_property, 'rheology.power_law_circular_laminar', 'extreme')
    result = laminar('power_law', radius, tau)
    # n=1/2 gives Q=πR³τ²/5 with K=1;log oracle avoids transient products.
    expected = math.exp(math.log(math.pi/5)+3*math.log(radius)+2*math.log(tau))
    assert result.signed_volume_flow_m3_s > 0
    assert result.signed_volume_flow_m3_s == pytest.approx(expected, rel=1e-12, abs=0)


@pytest.mark.parametrize('scale', [1e-100,1e100])
def test_offshore_inventory_extreme_extensivity(scale, record_property):
    annotate(record_property, 'release.offshore_advanced_inventory', 'extreme')
    module = old('test_offshore_inventory_release_individual.py')
    q = module['request'](); fn = module['evaluate_offshore_inventory_release']
    base = fn(q)
    result = fn(q.model_copy(update={'pipe_volume_m3':q.pipe_volume_m3*scale,
        'pre_shutin_oil_reference_flow_m3_s':q.pre_shutin_oil_reference_flow_m3_s*scale}))
    assert result.status == 'computed' and result.total_oil_reference_release_m3 > 0
    assert result.total_oil_reference_release_m3 == pytest.approx(base.total_oil_reference_release_m3*scale, rel=1e-12, abs=0)


@pytest.mark.parametrize('scale', [1e-200,1e200])
def test_spill_extreme_feed_scaling_retains_nonzero_radius(scale, record_property):
    annotate(record_property, 'spill.constant_feed_viscous_spread', 'extreme')
    module = old('test_porous_release_independent.py')
    q = module['srf'](); fn = module['surface']; base = fn(q)
    result = fn(q.model_copy(update={'feed_m3_s':q.feed_m3_s*scale}))
    assert result.radius_m[1] > 0 and math.isfinite(result.radius_m[1])
    assert result.radius_m[1] == pytest.approx(base.radius_m[1]*scale**(3/8), rel=1e-12, abs=0)


@pytest.mark.parametrize('conductivity', [1e-200,1e200])
def test_richards_extreme_conductivity_has_no_spurious_uniform_flow(conductivity, record_property):
    annotate(record_property, 'porous.richards_unsaturated_flow', 'extreme')
    module = old('test_richards_fv_independent.py')
    mesh,_ = module['grid'](3)
    result = module['solve_richards_fv'](mesh,module['settings'](.02,.01),(.1,)*3,
                                      module['LinearHydraulicLaw'](.4,.2,conductivity))
    assert result.final_head_m == pytest.approx((.1,)*3, rel=0, abs=1e-14)
    assert abs(result.global_volume_residual_m3) < 1e-14


def gas_meter_case(drop=5000.):
    from pipeline_integrity.gas_meter import GasMeterRequest, evaluate_gas_meter
    from CoolProp.CoolProp import PropsSI
    q, context = runpy.run_path(str(ROOT / 'examples/gas_meter.py'))['make_case'](reviewed=True)
    q = GasMeterRequest.model_validate({**q.model_dump(),
        'downstream_pressure_pa_abs': q.upstream_pressure_pa_abs-drop})
    density = PropsSI('Dmass','P',q.upstream_pressure_pa_abs,'T',q.upstream_temperature_K,'HEOS::Nitrogen')
    actual_drop = q.upstream_pressure_pa_abs-q.downstream_pressure_pa_abs
    diameter = q.geometry.secondary_dimension_m
    beta = diameter/q.geometry.inner_pipe_diameter_m
    expected = q.specified_discharge_coefficient*q.specified_expansibility_factor*math.pi*diameter**2/4*math.sqrt(2*density*actual_drop/(1-beta**4))
    return q, context, expected, evaluate_gas_meter


def test_gas_meter_bernoulli_reference_and_actual_geometry_inverse(record_property):
    annotate(record_property,'meter.supplied_gas_differential_flow','formula')
    from pipeline_integrity.gas_meter_sizing import GasMeterSizingRequest, evaluate_gas_meter_sizing
    q, context, expected, evaluate = gas_meter_case()
    result = evaluate(q, context)
    assert result['status'] == 'computed', result.get('notes')
    assert result['values']['signed_mass_flow_kg_s'] == pytest.approx(expected,rel=3e-12,abs=0)
    request = GasMeterSizingRequest(template=q,target_signed_mass_flow_kg_s=expected,
        secondary_dimension_bracket_m=(.04,.06),geometry_and_calibration_valid_over_bracket_reviewed=True,
        continuous_single_geometry_branch_reviewed=True)
    inverse = evaluate_gas_meter_sizing(request,context)
    assert inverse.status == 'computed', inverse
    assert inverse.secondary_dimension_m == pytest.approx(.05,rel=1e-8,abs=0)


@pytest.mark.parametrize('drop',[0.,1e-8])
def test_gas_meter_zero_and_tiny_represented_pressure_drop(drop,record_property):
    annotate(record_property,'meter.supplied_gas_differential_flow','extreme')
    q, context, expected, evaluate = gas_meter_case(drop)
    result = evaluate(q,context)
    assert result['status'] == 'computed', result.get('notes')
    observed = result['values']['signed_mass_flow_kg_s']
    if drop:
        assert observed > 0
        assert observed == pytest.approx(expected,rel=3e-12,abs=0)
    else:
        assert observed == 0


@pytest.mark.parametrize('kind,density,volume',[('formula',1000.,10.),
    ('extreme',1e-200,1e-100),('extreme',1e100,1e100)])
def test_direct_required_injection_independent_inventory_difference(kind,density,volume,record_property):
    annotate(record_property,'fluid_pipe.closed_mass_compatibility',kind)
    from pipeline_integrity.closed_liquid_compatibility import (
        TestLiquidState,TestPipeVolumeState,TestPressureCoordinate,evaluate_required_liquid_injection)
    q = old('test_input_root_223.py')['injection_request']()
    q = q.model_copy(update={'target_pressure':TestPressureCoordinate(value_Pa=2e5,reference='absolute')})
    class Density:
        def resolve_density(self,*,pressure_Pa_abs,water_temperature_K):
            return TestLiquidState(status='computed',pressure_Pa_abs=pressure_Pa_abs,
                water_temperature_K=water_temperature_K,fluid_id=q.fluid_id,property_basis_id=q.property_basis_id,
                density_kg_m3=density,phase='liquid')
    class Volume:
        def resolve_volume(self,*,pressure_Pa_abs,external_pressure_Pa_abs,wall_temperature_K):
            return TestPipeVolumeState(status='computed',pressure_Pa_abs=pressure_Pa_abs,
                external_pressure_Pa_abs=external_pressure_Pa_abs,wall_temperature_K=wall_temperature_K,
                asset_id=q.asset_id,geometry_basis_id=q.geometry_basis_id,volume_m3=volume*(pressure_Pa_abs/1e5))
    result = evaluate_required_liquid_injection(q,density_provider=Density(),volume_provider=Volume())
    assert result.status == 'computed'
    assert result.required_added_mass_kg > 0 and result.added_volume_at_target_state_m3 > 0
    assert result.required_added_mass_kg == pytest.approx(density*volume,rel=1e-12,abs=0)
    assert result.added_volume_at_target_state_m3 == pytest.approx(volume,rel=1e-12,abs=0)
