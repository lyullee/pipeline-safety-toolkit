"""Actual nonlinear mechanics solves against energy and limiting references.

These synthetic verification fixtures do not establish physical calibration.
Execution is assigned to Nurion; optional native packages must be present.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.elastic_shell_ring import (
    ElasticShellRingRequest,
    elastic_shell_ring_backend_metadata,
    evaluate_elastic_shell_ring,
)
from pipeline_integrity.dynamic_fracture import (
    CrackGeometryEnergy,
    DynamicCrackResistance,
    DynamicFractureRequest,
    bind_dynamic_fracture_context,
    dynamic_fracture_metadata,
    evaluate_dynamic_fracture,
)
from pipeline_integrity.thermo import StateRequest


def context(scenario):
    return RunContext(asset_id="unit", scenario_id=scenario, clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored mechanics verification fixture",
            origin="synthetic", sha256="f" * 64),))


def ring_case(quadrature_points):
    return ElasticShellRingRequest(
        id="circular_ring", asset_id="unit", clock_id="seconds", cross_section_id="ring",
        coordinate_frame_id="xy", mean_reference_centerline_radius_m=1.0, wall_thickness_m=0.01,
        axial_strip_width_m=0.2, young_modulus_Pa=2e11, poisson_ratio=0.3,
        axial_constraint="plane_stress_ring", external_minus_internal_pressure_Pa=(0.0, 1000.0, 2000.0, 5000.0),
        maximum_cartesian_harmonic=3, quadrature_points=quadrature_points,
        minimum_pressure_step_Pa=0.001, pressure_event_tolerance_Pa=0.001,
        native_backend_pins=elastic_shell_ring_backend_metadata(),
        geometry_evidence_id="review", material_evidence_id="review", pressure_evidence_id="review",
        scope_review_evidence_id="review", reference_ovality_is_stress_free_geometry_reviewed=True,
        homogeneous_linear_elastic_thin_unshearable_ring_reviewed=True,
        axial_constraint_and_omitted_finite_length_modes_reviewed=True,
        conservative_uniform_differential_pressure_reviewed=True,
    )


def test_actual_ring_continuation_matches_exact_uniform_membrane_equilibrium_on_three_grids():
    endpoints = []
    for points in (64, 128, 256):
        request = ring_case(points)
        record = evaluate_elastic_shell_ring(request, context("circular_ring"))
        assert record["status"] == "computed_diagnostic", record.get("failure")
        assert record["stability_event"] is None
        assert record["states"][-1]["pressure_Pa"] == 5000.0
        for row in record["states"]:
            # Minimize pi*EA*(r-R)^2/R + p*b*pi*r^2 independently.
            alpha = row["pressure_Pa"] / (request.young_modulus_Pa * request.wall_thickness_m)
            radius = 1.0 / (1.0 + alpha)
            strain = radius - 1.0
            exact_area = math.pi * radius**2
            exact_energy = math.pi * request.young_modulus_Pa * request.wall_thickness_m * request.axial_strip_width_m * strain**2
            assert row["enclosed_area_m2"] == pytest.approx(exact_area, abs=2e-10)
            assert row["maximum_axial_strain"] == pytest.approx(abs(strain), abs=3e-11)
            assert row["axial_energy_J"] == pytest.approx(exact_energy, rel=5e-5, abs=1e-10)
            assert row["bending_energy_J"] == pytest.approx(0.0, abs=1e-12)
            assert row["force_residual_N"] <= row["force_tolerance_N"]
            assert row["positive_tangent"] is True
        endpoints.append(record["states"][-1]["enclosed_area_m2"])
    # The circle is exactly represented in every Fourier space. Angular refinement
    # must preserve its analytic equilibrium rather than invent truncation error.
    assert max(endpoints) - min(endpoints) < 2e-10


def fracture_case(*, heated_restart=False):
    resistance = 1.0 if heated_restart else 2.0
    request = DynamicFractureRequest(
        id="fracture", asset_id="unit", event_id="fracture", clock_id="seconds", location_id="location",
        flaw_id="flaw", coordinate_frame_id="path", crack_path_id="crack",
        pipe_outer_diameter_m=0.1, pipe_wall_thickness_m=0.005,
        duration_s=0.5, fluid="Hydrogen", property_basis_id="HEOS-common-reference",
        initial_fluid_state=StateRequest(fluid="Hydrogen", inputs={"P": 1e6, "T": 300.0}),
        external_fluid_state=StateRequest(fluid="Hydrogen", inputs={"P": 1e5, "T": 300.0}),
        initial_wall_temperature_K=400.0 if heated_restart else 300.0,
        wall_heat_capacity_J_K=1e5, fluid_wall_UA_W_K=1000.0 if heated_restart else 0.0,
        ambient_wall_UA_W_K=0.0, ambient_wall_temperature_K=300.0,
        geometry=CrackGeometryEnergy(id="geometry", advance_m=(0.0, 1.0), fluid_volume_m3=(0.01, 0.010001),
            new_crack_area_m2=(0.0, 1.0), opening_area_m2=(0.0, 1e-14),
            recoverable_elastic_energy_J=(0.0, 0.0), new_crack_area_basis_id="two-surfaces", evidence_id="review"),
        resistance=DynamicCrackResistance(id="resistance", speed_m_s=(0.0, 10.0),
            material_temperature_K=(250.0, 500.0), resistance_J_m2=((resistance, resistance), (resistance, resistance)),
            material_condition_id="reviewed", material_orientation_id="path", evidence_id="review",
            definition="dynamic_energy_per_new_crack_area_excluding_explicit_front_inertia"),
        effective_front_inertia_kg=1.0, initial_advance_m=0.0, initial_speed_m_s=0.0 if heated_restart else 0.2,
        discharge_coefficient=0.8, throat_pressure_samples=12,
        pressure_support_Pa_abs=(1e3, 1e7), fluid_temperature_support_K=(50.0, 1000.0),
        minimum_inventory_kg=1e-6, maximum_step_s=0.01,
        restart_force_tolerance_N=1e-7, stop_speed_projection_tolerance_m_s=1e-7,
        maximum_velocity_projection_energy_J=1e-12,
        absolute_mass_balance_tolerance_kg=1e-9, absolute_energy_balance_tolerance_J=1e-3,
        evidence_ids=("review",), coupling_review_evidence_id="review",
        variable_volume_pressure_work_and_elastic_potential_reviewed=True,
        dynamic_resistance_and_explicit_inertia_do_not_double_count_energy_reviewed=True,
        lumped_wall_temperature_represents_resistance_temperature_reviewed=True,
        irreversible_fracture_energy_not_recycled_to_lumped_wall_reviewed=True,
        homogeneous_equilibrium_restriction_and_pure_fluid_external_boundary_reviewed=True,
        owner_pins=dynamic_fracture_metadata(),
    )
    return request, bind_dynamic_fracture_context(request, context("fracture"))


def assert_closed_energy_and_irreversible_history(record, request):
    assert record["status"] == "computed_diagnostic", record.get("issues")
    rows = record["history"]
    assert rows[-1]["elapsed_time_s"] == pytest.approx(request.duration_s)
    initial = rows[0]
    initial_stored = initial["fluid_internal_energy_J"] + request.wall_heat_capacity_J_K * initial["wall_temperature_K"] + initial["front_kinetic_energy_J"]
    for row in rows:
        # Reassemble conserved energy directly from the retained physical terms.
        stored = (row["fluid_internal_energy_J"] + request.wall_heat_capacity_J_K * row["wall_temperature_K"]
            + row["front_kinetic_energy_J"] + row["fracture_dissipation_J"]
            + row["cumulative_external_displacement_work_J"])
        residual = (stored - initial_stored + row["cumulative_enthalpy_out_J"]
            - row["cumulative_ambient_heat_J"] - row["numerical_velocity_projection_energy_J"])
        assert abs(residual) < 0.002
        assert row["mass_kg"] + row["cumulative_mass_out_kg"] == pytest.approx(initial["mass_kg"], abs=1e-9)
        assert row["speed_m_s"] >= 0.0
        assert row["fracture_dissipation_J"] == pytest.approx(
            row["dynamic_resistance_J_m2"] * row["advance_m"], abs=1e-5)
    assert all(b["advance_m"] >= a["advance_m"] - 1e-10 for a, b in zip(rows, rows[1:]))
    return rows


def test_native_hem_fracture_stops_at_the_independent_constant_force_limit_and_conserves_energy():
    request, boundary = fracture_case()
    record = evaluate_dynamic_fracture(request, boundary)
    rows = assert_closed_energy_and_irreversible_history(record, request)
    events = record["events"]
    assert len(events) == 1 and events[0]["from_mode"] == "moving" and events[0]["to_mode"] == "stuck"
    # The volume slope is 1e-6 m2, the new-area slope is 1 m, and resistance is
    # 2 J/m2. Near-constant source pressure predicts F=-1.1 N, not a KIC speed law.
    stop_time = 0.2 / 1.1
    stop_distance = 0.2**2 / (2 * 1.1)
    assert events[0]["time_s"] == pytest.approx(stop_time, abs=1e-4)
    assert events[0]["advance_m"] == pytest.approx(stop_distance, abs=1e-5)
    assert rows[-1]["advance_m"] == pytest.approx(events[0]["advance_m"], abs=1e-10)
    assert rows[-1]["speed_m_s"] == 0.0
    assert rows[-1]["cumulative_net_pressure_crack_work_J"] > 0.0


def test_native_hem_wall_heating_restarts_an_initially_stuck_crack_with_explicit_energy_source():
    request, boundary = fracture_case(heated_restart=True)
    record = evaluate_dynamic_fracture(request, boundary)
    rows = assert_closed_energy_and_irreversible_history(record, request)
    assert rows[0]["mode"] == "stuck"
    restarts = [event for event in record["events"] if event["from_mode"] == "stuck" and event["to_mode"] == "moving"]
    assert len(restarts) == 1
    assert 0.0 < restarts[0]["time_s"] < request.duration_s
    assert restarts[0]["advance_m"] == pytest.approx(0.0, abs=1e-10)
    assert rows[-1]["advance_m"] > 1e-4
    assert rows[-1]["wall_temperature_K"] < rows[0]["wall_temperature_K"]
    assert rows[-1]["fluid_state"]["temperature_K"] > rows[0]["fluid_state"]["temperature_K"]
    assert rows[-1]["cumulative_ambient_heat_J"] == pytest.approx(0.0, abs=1e-8)
