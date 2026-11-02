"""Thirteen scalar/observation purposes: independent references and valid limits."""
from functools import wraps
import importlib.util
import json
import math
from pathlib import Path
import sys

import pytest

from pipeline_integrity.leak_inverse_relations import (
    DiscrepancyLocationRequest, ReflectionLocationRequest, DielectricCableRangingRequest,
    locate_small_leak_from_discrepancies, locate_from_known_interface_reflection, locate_dielectric_cable,
)
from pipeline_integrity.sensing_location import ResistiveCableRequest, locate_resistive_cable


def member(unit, kind):
    def decorate(function):
        function._canonical_units=['proposal.'+unit]
        function._test_kind=kind
        return function
    return decorate


@pytest.fixture(autouse=True)
def annotate_executed_case(request):
    units=getattr(request.function,'_canonical_units',None)
    if units:
        request.node.user_properties.append(('canonical_units',json.dumps(units)))
        request.node.user_properties.append(('test_kind',request.function._test_kind))


SELECTIONS=[
 ('compressor_volume_coordinate_independent','acoustics.supplied_compressor_volume_correlation',
  ['quarter_power_coordinate_with_independent_eighty_digit_and_bounds','terminal_exponential_plus_linear_against_eighty_digit_reference'],[]),
 ('section_mass_balance_individual_local','conservation.observed_section_mass',
  ['independent_mass_ledger_preserves_zero_loss_and_negative_residual'],['closed_section_has_zero_directional_totals_and_explicit_storage_loss']),
 ('observed_pipe_storage_independent','conservation.observed_section_mass',
  ['gauss2_observed_linear_state_product_and_exact_time_window_changes'],[]),
 ('pressure_bending_budget_independent','elbow_bending_stress_limit',
  ['actual_fibre_arc_strain_and_annular_resultants','inverse_ceiling_exact_on_both_curvature_signs'],['zero_pressure_pure_bending_and_tensile_only_meaning']),
 ('elbow_pressure_stress_independent','elbow_pressure_stress',
  ['field_solves_independent_meridional_equilibrium_ode','weighted_circumferential_force_budget_and_extreme_locations'],['zero_pressure_produces_zero_nominal_field']),
 ('stored_liquid_energy_independent','energy.linear_liquid_storage',
  ['energy_matches_separately_integrated_compression_work'],['zero_reference_load_and_rigid_modulus_limit']),
 ('horizontal_jet_individual','fluid.horizontal_jet_discharge',
  ['independent_time_integrated_trajectory_inverse'],['gravity_and_horizontal_geometric_similarity','degenerate_observation_is_not_fitted']),
 ('leak_inverse_relations_independent','leak.supplied_small_discrepancy_location',
  ['discrepancy_from_independent_linear_hydraulic_resistances'],[]),
 ('point_illuminance_individual','optics.point_source_illuminance',
  ['disk_received_flux_against_independent_exact_solid_angle','normal_and_oblique_three_four_five_geometry'],['multiple_sources_transmission_and_back_face']),
 ('leak_inverse_relations_independent','sensing.electromagnetic_cable_flight',
  ['dielectric_wave_time_and_existing_range_latency'],[]),
 ('sensing_location_individual_local','sensing.uniform_resistive_cable_coordinate',
  ['resistance_location_inverts_independent_two_wire_network'],[]),
 ('vapor_tube_individual_local','transport.ideal_sensor_tube_parcel',
  ['continuous_advection_matches_independent_constant_velocity_motion','single_purge_uses_supplied_residual_wait_once'],[]),
 ('vent_coordinate_independent','vent.supplied_empirical_area_correlation',
  ['independent_dimensioned_reference_in_us_and_si_coordinates'],['pressure_area_coefficient_scaling_and_dimensionless_identity']),
 ('leak_inverse_relations_independent','waves.prescribed_one_dimensional_travel_time',
  ['reflection_independent_direct_and_reflected_paths'],[]),
]

_modules={}
for module_name,unit,formula_names,extreme_names in SELECTIONS:
    if module_name not in _modules:
        path=Path(__file__).with_name('test_'+module_name+'.py')
        spec=importlib.util.spec_from_file_location('_root_misc_reference_'+module_name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        _modules[module_name]=module
    for kind,names in (('formula',formula_names),('extreme',extreme_names)):
        for name in names:
            original=getattr(_modules[module_name],'test_'+name)
            def make_wrapper(function):
                @wraps(function)
                def wrapped(*args,**kwargs):
                    return function(*args,**kwargs)
                return wrapped
            globals()['test_'+kind+'_'+unit.replace('.','_')+'_'+name]=member(unit,kind)(make_wrapper(original))


@member('acoustics.supplied_compressor_volume_correlation','extreme')
@pytest.mark.parametrize('scale',[1e-100,1e100])
def test_compressor_volume_extreme_displacement_scaling(scale):
    m=_modules['compressor_volume_coordinate_independent']
    base=m.surge(m.request(supplied_minimum_volume_m3=0.))
    r=m.surge(m.request(supplied_minimum_volume_m3=0.,net_displaced_volume_per_revolution_m3=.0173*scale))
    assert r['status']=='computed'
    for field in ('suction_surge_volume_coordinate_m3','discharge_surge_volume_coordinate_m3'):
        assert r[field]>0
        assert r[field]==pytest.approx(base[field]*scale,rel=4e-14,abs=0.)


@member('conservation.observed_section_mass','extreme')
@pytest.mark.parametrize('scale',[1e-100,1e100])
def test_observed_integrated_mass_inventory_extensive_extremes(scale):
    m=_modules['section_mass_balance_individual_local']
    r=m.evaluate_section_mass_balance(m.case(inlet_masses_kg={'in':40.*scale},outlet_masses_kg={'out':20.*scale},
        initial_inventory_mass_kg=100.*scale,final_inventory_mass_kg=113.*scale))
    assert r.status=='computed'
    assert r.mass_residual_kg>0
    assert r.mass_residual_kg==pytest.approx(7.*scale,rel=5e-14,abs=0.)


@member('elbow_bending_stress_limit','extreme')
@pytest.mark.parametrize('scale',[1e-50,1e50])
def test_elastic_bend_extreme_geometric_similarity_preserves_stress(scale):
    m=_modules['pressure_bending_budget_independent']
    base=m.evaluate(m.request())['values']
    r=m.evaluate(m.request(actual_outside_diameter_m=.3*scale,actual_wall_thickness_m=.009*scale,
        signed_prescribed_curvature_per_m=.001/scale))
    assert r['status']=='computed'
    v=r['values']
    assert v['positive_coordinate_fibre_longitudinal_stress_pa']==pytest.approx(base['positive_coordinate_fibre_longitudinal_stress_pa'],rel=3e-14)
    assert v['minimum_absolute_bend_radius_m']>0
    assert v['minimum_absolute_bend_radius_m']==pytest.approx(base['minimum_absolute_bend_radius_m']*scale,rel=3e-14,abs=0.)


@member('elbow_pressure_stress','extreme')
@pytest.mark.parametrize('pressure',[1e-100,1e100])
def test_elbow_nominal_membrane_extreme_pressure_scale(pressure):
    m=_modules['elbow_pressure_stress_independent']
    base=m.calc()
    r=m.calc(pressure_difference_pa=pressure)
    assert r.status=='computed'
    for actual,reference in zip(r.nominal_hoop_stress_pa,base.nominal_hoop_stress_pa):
        assert actual>0
        assert actual==pytest.approx(reference*(pressure/2e6),rel=3e-14,abs=0.)


@member('energy.linear_liquid_storage','extreme')
@pytest.mark.parametrize('volume',[1e-100,1e100])
def test_linear_stored_work_extreme_volume_coordinates(volume):
    m=_modules['stored_liquid_energy_independent']
    r=m.evaluate_linear_liquid_compression(m.LinearLiquidCompression(1e6,volume,2e9))
    assert r.volume_reduction_m3>0 and r.stored_elastic_energy_j>0
    assert r.volume_reduction_m3==pytest.approx(volume*.0005,rel=3e-14,abs=0.)
    assert r.stored_elastic_energy_j==pytest.approx(volume*250.,rel=3e-14,abs=0.)


@member('fluid.horizontal_jet_discharge','extreme')
@pytest.mark.parametrize('scale',[1e-100,1e100])
def test_jet_extreme_length_scale_preserves_ballistic_similarity(scale):
    m=_modules['horizontal_jet_individual']
    r=m.solve(m.make(x=.6*scale,y=.2*scale))
    assert r.status=='computed'
    expected=.6/math.sqrt(2*.2/9.81)*math.sqrt(scale)
    assert r.values['tracked_horizontal_velocity_m_s']>0
    assert r.values['tracked_horizontal_velocity_m_s']==pytest.approx(expected,rel=4e-14,abs=0.)


@member('leak.supplied_small_discrepancy_location','extreme')
@pytest.mark.parametrize('scale',[1e-100,1e100])
def test_discrepancy_ratio_large_and_small_absolute_flows(scale):
    r=locate_small_leak_from_discrepancies(DiscrepancyLocationRequest(
        upstream_discrepancy_m3_s=3.*scale,downstream_discrepancy_m3_s=scale,section_length_m=100.,
        small_leak_fully_propagated_linear_resistance_reviewed=True))
    assert r.status=='computed'
    assert r.distance_from_upstream_m==pytest.approx(25.)
    assert r.inferred_loss_m3_s>0
    assert r.inferred_loss_m3_s==pytest.approx(4.*scale,rel=2e-14,abs=0.)


@member('leak.supplied_small_discrepancy_location','extreme')
def test_discrepancy_distance_retains_representable_rescaled_tiny_fraction():
    r=locate_small_leak_from_discrepancies(DiscrepancyLocationRequest(
        upstream_discrepancy_m3_s=1e308,downstream_discrepancy_m3_s=1e-308,section_length_m=1e308,
        small_leak_fully_propagated_linear_resistance_reviewed=True))
    assert r.status=='computed'
    assert r.distance_from_upstream_m>0
    assert r.distance_from_upstream_m==pytest.approx(1e-308,rel=1e-12,abs=0.)
    assert r.fraction_underflowed
    assert r.fraction_from_upstream is None


@member('leak.supplied_small_discrepancy_location','extreme')
def test_combined_loss_beyond_output_representation_is_explicitly_withheld():
    q=DiscrepancyLocationRequest(upstream_discrepancy_m3_s=1e308,downstream_discrepancy_m3_s=1e308,
        section_length_m=100.,small_leak_fully_propagated_linear_resistance_reviewed=True)
    with pytest.raises(ValueError,match='combined loss is outside finite'):
        locate_small_leak_from_discrepancies(q)


@member('optics.point_source_illuminance','extreme')
@pytest.mark.parametrize('distance',[1e-100,1e100])
def test_point_illuminance_extreme_separation(distance):
    m=_modules['point_illuminance_individual']
    q=m.make(sources=(m.S(id='light',position_m=(0.,0.,distance),directional_luminous_intensity_cd=1.,transmission_fraction=1.),))
    r=m.solve(q)
    assert r.status=='computed'
    assert r.values['total_illuminance_lux']>0
    assert r.values['total_illuminance_lux']==pytest.approx((1/distance)/distance,rel=3e-14,abs=0.)


@member('sensing.electromagnetic_cable_flight','extreme')
@pytest.mark.parametrize('permittivity,length',[(1.,0.),(1.,1e-100),(1e100,100.)])
def test_dielectric_cable_zero_tiny_position_and_large_permittivity(permittivity,length):
    elapsed=2*length*math.sqrt(permittivity)/299792458.
    r=locate_dielectric_cable(DielectricCableRangingRequest(relative_permittivity=permittivity,
        elapsed_round_trip_s=elapsed,nonmagnetic_nondispersive_dielectric_reviewed=True))
    assert r.status=='computed'
    if length>0:assert r.position_m>0
    assert r.position_m==pytest.approx(length,rel=3e-14,abs=0.)


@member('sensing.uniform_resistive_cable_coordinate','extreme')
@pytest.mark.parametrize('coefficient,length',[(.01,0.),(1e-100,100.),(1e100,100.)])
def test_resistive_coordinate_zero_and_extreme_loop_resistance(coefficient,length):
    r=locate_resistive_cable(ResistiveCableRequest(voltage_v=2*coefficient*length,current_a=1.,
        resistance_per_length_ohm_m=coefficient,resistance_basis='single_conductor'))
    assert r.status=='computed'
    if length>0:assert r.position_m>0
    assert r.position_m==pytest.approx(length,rel=3e-14,abs=0.)


@member('transport.ideal_sensor_tube_parcel','extreme')
@pytest.mark.parametrize('distance',[0.,1e-100,1e100])
def test_ideal_tube_zero_and_extreme_valid_parcel_delays(distance):
    m=_modules['vapor_tube_individual_local']
    r=m.evaluate_vapor_tube_delay(m.delay(distance_to_detector_m=distance,air_speed_m_s=1.))
    assert r.status=='computed'
    if distance>0:assert r.parcel_arrival_delay_s>0
    assert r.parcel_arrival_delay_s==pytest.approx(distance,rel=3e-14,abs=0.)


@member('vent.supplied_empirical_area_correlation','extreme')
@pytest.mark.parametrize('area',[1e-100,1e100])
def test_vent_finite_extreme_conditional_area_coordinate(area):
    m=_modules['vent_coordinate_independent']
    r=m.evaluate(m.request(enclosure_internal_surface_area_m2=area,reduced_pressure_differential_pa=4.,
                           supplied_fuel_geometry_coefficient_sqrt_pa=2.))
    assert r['status']=='computed'
    assert r['vent_area_coordinate_m2']>0
    assert r['vent_area_coordinate_m2']==pytest.approx(area,rel=3e-14,abs=0.)


@member('waves.prescribed_one_dimensional_travel_time','extreme')
@pytest.mark.parametrize('speed',[1e-100,1e100])
def test_reflected_arrival_extreme_prescribed_speed_and_delay_reciprocity(speed):
    r=locate_from_known_interface_reflection(ReflectionLocationRequest(sensor_to_interface_m=1000.,
        direct_arrival_s=0.,reflected_arrival_s=250./speed,wave_speed_m_s=speed,
        known_constant_pressure_interface_and_uniform_speed_reviewed=True))
    assert r.status=='computed'
    assert r.interface_to_source_m==pytest.approx(125.,rel=3e-14)
    assert r.distance_upstream_from_sensor_m==pytest.approx(875.,rel=3e-14)
