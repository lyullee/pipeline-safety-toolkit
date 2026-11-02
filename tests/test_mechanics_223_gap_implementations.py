"""Independent boundary/energy checks prepared for later batch execution."""
import math

import pytest

from pipeline_integrity.constant_loss_life import ConstantLossLifeRequest, evaluate_constant_loss_life
from pipeline_integrity.neutral_surface_bending import NeutralSurfaceBendingRequest, evaluate_neutral_surface_bending
from pipeline_integrity.surface_flaw_proof import SurfaceFlawProofRequest, evaluate_surface_flaw_proof
from pipeline_integrity.shaft_torsion_observer import ShaftTorsionRequest, evaluate_shaft_torsion
from pipeline_integrity.weld_wall_thermal_limit import WeldHeatStage, WeldWallThermalLimitRequest, evaluate_weld_wall_thermal_limit


def test_neutral_arc_ratio_and_face_radius():
    q = NeutralSurfaceBendingRequest(basis_id="geometry", neutral_radius_m=2,
        thickness_m=.02, neutral_distance_from_inner_face_m=.006,
        reference_arc_length_m=math.pi,
        plane_sections_and_circular_neutral_surface_reviewed=True)
    v = evaluate_neutral_surface_bending(q)['values']
    angle = math.pi / 2
    assert v['signed_bend_angle_rad'] == pytest.approx(angle)
    for face in ('inner', 'outer'):
        radius = v['inside_face_radius_m' if face == 'inner' else 'outside_face_radius_m']
        observed_arc = radius * angle
        assert v[f'signed_{face}_face_engineering_strain'] == pytest.approx(observed_arc / math.pi - 1)


def test_constant_loss_zero_rate_and_already_reached():
    q = ConstantLossLifeRequest(basis_id="wall", current_remaining_wall_m=.01,
        supplied_critical_remaining_wall_m=.005, supplied_penetration_rate_m_s=0,
        constant_loss_and_critical_boundary_reviewed=True)
    assert evaluate_constant_loss_life(q)['time_to_boundary_s'] is None
    assert evaluate_constant_loss_life(q.model_copy(update={'supplied_critical_remaining_wall_m': .011}))['time_to_boundary_s'] == 0


def test_proof_plateau_uses_deepest_surviving_knot_without_extrapolation():
    q = SurfaceFlawProofRequest(basis_id="curve", flaw_family_id="flaw", material_condition_id="material",
        wall_thickness_m=.02, depth_m=(.001, .003, .005, .008),
        failure_pressure_pa=(20e6, 15e6, 15e6, 10e6),
        proof_pressure_pa=15e6, maximum_operating_pressure_pa=10e6,
        monotonic_elastic_proof_and_same_flaw_family_reviewed=True,
        failure_pressure_curve_applicability_reviewed=True)
    result = evaluate_surface_flaw_proof(q)
    assert result['proof_limited_initial_depth_m'] == .005
    assert result['operating_failure_depth_m'] == .008
    assert result['operating_boundary_status'] == 'supported_boundary'


def _wall(**updates):
    values = dict(basis_id="thermal", inside_radius_m=.2, wall_thickness_m=.01,
        conductivity_w_m_k=40, density_kg_m3=7800, specific_heat_j_kg_k=500,
        initial_temperature_k=300, supplied_inner_surface_ceiling_k=1200,
        stages=(WeldHeatStage(duration_s=4, effective_outer_heat_flux_w_m2=10000,
                              gas_temperature_k=300, gas_film_w_m2_k=0),),
        radial_cells=8, maximum_step_s=.2,
        radial_constant_property_heat_flux_and_film_basis_reviewed=True)
    return WeldWallThermalLimitRequest(**(values | updates))


def test_weld_wall_adiabatic_storage_matches_applied_heat():
    q = _wall()
    r = evaluate_weld_wall_thermal_limit(q)
    expected = 2 * math.pi * (.2 + .01) * 10000 * 4
    assert r['stored_heat_j_per_m'] == pytest.approx(expected, rel=1e-10)
    assert r['gas_removed_heat_j_per_m'] == 0
    assert abs(r['energy_residual_j_per_m']) < 1e-5


def test_weld_wall_uniform_thermal_equilibrium():
    q = _wall(stages=(WeldHeatStage(duration_s=4, effective_outer_heat_flux_w_m2=0,
                                    gas_temperature_k=300, gas_film_w_m2_k=100),))
    r = evaluate_weld_wall_thermal_limit(q)
    assert r['final_cell_temperature_k'] == pytest.approx([300] * q.radial_cells)
    assert r['peak_sampled_inner_surface_temperature_k'] == pytest.approx(300)


def test_combined_shaft_bending_reduces_torsion_capacity_by_equivalent_stress():
    radius = .05
    allowable = 100e6
    second_moment = math.pi * radius ** 4 / 4
    q = ShaftTorsionRequest(id="shaft", outside_diameter_m=2*radius,
        signed_torque_nm=0, signed_angular_velocity_rad_s=20,
        signed_bending_moment_nm=.6*allowable*second_moment/radius,
        homogeneous_circular_saint_venant_torsion_reviewed=True,
        combined_bending_and_torsion_reviewed=True,
        supplied_combined_equivalent_stress_ceiling_pa=allowable,
        combined_ceiling_evidence_id="material")
    v = evaluate_shaft_torsion(q)['values']
    assert v['combined_equivalent_stress_pa'] == pytest.approx(.6*allowable)
    assert v['combined_maximum_absolute_torque_nm'] == pytest.approx(.8*allowable/math.sqrt(3)*2*second_moment/radius)
