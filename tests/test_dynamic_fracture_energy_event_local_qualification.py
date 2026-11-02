"""Local closed-form and conservation checks for a reduced inertial crack stop."""

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.dynamic_fracture import (
    CrackGeometryEnergy,
    DynamicCrackResistance,
    DynamicFractureRequest,
    bind_dynamic_fracture_context,
    dynamic_fracture_metadata,
    evaluate_dynamic_fracture,
)
from pipeline_integrity.thermo import StateRequest


def _case(maximum_step_s):
    evidence_ids = ("geometry", "resistance", "coupling")
    evidence = tuple(EvidenceRef(
        id=identity, citation="Authored synthetic reduced-fracture reference", origin="synthetic",
        sha256=str(i + 1) * 64,
    ) for i, identity in enumerate(evidence_ids))
    request = DynamicFractureRequest(
        id="inertial_crack_stop",
        asset_id="synthetic_pipe",
        event_id="pressure_release",
        clock_id="elapsed_seconds",
        location_id="section_1",
        flaw_id="axial_flaw",
        coordinate_frame_id="pipe_axis",
        crack_path_id="axial_path",
        pipe_outer_diameter_m=0.1,
        pipe_wall_thickness_m=0.005,
        duration_s=0.02,
        fluid="Hydrogen",
        property_basis_id="CoolProp_HEOS_Hydrogen",
        initial_fluid_state=StateRequest(fluid="Hydrogen", inputs={"P": 5.0e6, "T": 300.0}),
        external_fluid_state=StateRequest(fluid="Hydrogen", inputs={"P": 1.0e6, "T": 300.0}),
        initial_wall_temperature_K=300.0,
        wall_heat_capacity_J_K=1000.0,
        fluid_wall_UA_W_K=0.0,
        ambient_wall_UA_W_K=0.0,
        ambient_wall_temperature_K=300.0,
        geometry=CrackGeometryEnergy(
            id="linear_geometry",
            advance_m=(0.0, 0.2),
            fluid_volume_m3=(0.01, 0.01002),
            new_crack_area_m2=(0.0, 0.002),
            opening_area_m2=(0.0, 1.0e-6),
            recoverable_elastic_energy_J=(500.0, 400.0),
            new_crack_area_basis_id="projected_crack_area",
            evidence_id="geometry",
        ),
        resistance=DynamicCrackResistance(
            id="constant_dynamic_resistance",
            speed_m_s=(0.0, 20.0, 40.0),
            material_temperature_K=(280.0, 320.0),
            resistance_J_m2=((200000.0, 200000.0),) * 3,
            material_condition_id="synthetic_material",
            material_orientation_id="longitudinal",
            evidence_id="resistance",
            definition="dynamic_energy_per_new_crack_area_excluding_explicit_front_inertia",
        ),
        effective_front_inertia_kg=1.0,
        initial_advance_m=0.01,
        initial_speed_m_s=10.0,
        discharge_coefficient=0.7,
        throat_pressure_samples=12,
        pressure_support_Pa_abs=(1.0e3, 6.0e6),
        fluid_temperature_support_K=(50.0, 350.0),
        minimum_inventory_kg=1.0e-4,
        maximum_step_s=maximum_step_s,
        relative_ODE_tolerance=1.0e-8,
        absolute_energy_ODE_tolerance_J=1.0e-6,
        absolute_speed_ODE_tolerance_m_s=1.0e-8,
        restart_force_tolerance_N=1.0,
        stop_speed_projection_tolerance_m_s=1.0e-6,
        maximum_velocity_projection_energy_J=0.1,
        absolute_mass_balance_tolerance_kg=1.0e-9,
        absolute_energy_balance_tolerance_J=1.0e-3,
        relative_balance_tolerance=1.0e-6,
        maximum_RHS_calls=100000,
        maximum_native_state_conversions=1000000,
        maximum_points=20000,
        maximum_mode_events=20,
        evidence_ids=evidence_ids,
        coupling_review_evidence_id="coupling",
        variable_volume_pressure_work_and_elastic_potential_reviewed=True,
        dynamic_resistance_and_explicit_inertia_do_not_double_count_energy_reviewed=True,
        lumped_wall_temperature_represents_resistance_temperature_reviewed=True,
        irreversible_fracture_energy_not_recycled_to_lumped_wall_reviewed=True,
        homogeneous_equilibrium_restriction_and_pure_fluid_external_boundary_reviewed=True,
        owner_pins=dynamic_fracture_metadata(),
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id=request.event_id,
        clock_id=request.clock_id,
        origin="simulation",
        evidence=evidence,
    )
    return request, bind_dynamic_fracture_context(request, context)


@pytest.mark.parametrize("maximum_step_s", (0.002, 0.001))
def test_dynamic_fracture_stops_at_zero_speed_and_conserves_mass_energy(maximum_step_s):
    request, context = _case(maximum_step_s)
    record = evaluate_dynamic_fracture(request, context)
    assert record["status"] == "computed_diagnostic", record.get("failure")
    assert len(record["events"]) == 1
    event = record["events"][0]
    assert (event["from_mode"], event["to_mode"]) == ("moving", "stuck")
    assert record["history"][-1]["speed_m_s"] == 0.0
    assert 0.0 < event["time_s"] < request.duration_s

    history = record["history"]
    initial = history[0]
    initial_mechanical = 500.0 - 500.0 * request.initial_advance_m + 0.5 * request.effective_front_inertia_kg * request.initial_speed_m_s**2
    for row in history:
        advance = row["advance_m"]
        elastic = 500.0 - 500.0 * advance
        volume = 0.01 + 1.0e-4 * advance
        crack_energy = elastic + 0.5 * request.effective_front_inertia_kg * row["speed_m_s"]**2
        independent_mechanical_residual = (
            crack_energy + row["fracture_dissipation_J"] - initial_mechanical
            - row["cumulative_net_pressure_crack_work_J"]
        )
        assert abs(independent_mechanical_residual) < 0.02
        independent_mass_residual = row["mass_kg"] + row["cumulative_mass_out_kg"] - initial["mass_kg"]
        assert abs(independent_mass_residual) < 1.0e-8

        total_energy = (
            row["fluid_internal_energy_J"] + request.wall_heat_capacity_J_K * row["wall_temperature_K"]
            + crack_energy + row["fracture_dissipation_J"]
            + 1.0e6 * (volume - (0.01 + 1.0e-4 * request.initial_advance_m))
        )
        initial_total = (
            initial["fluid_internal_energy_J"] + request.wall_heat_capacity_J_K * initial["wall_temperature_K"]
            + initial_mechanical
        )
        independent_total_energy_residual = (
            total_energy - initial_total + row["cumulative_enthalpy_out_J"]
            - row["cumulative_ambient_heat_J"] - row["numerical_velocity_projection_energy_J"]
        )
        assert abs(independent_total_energy_residual) < 0.03


def test_crack_stop_event_converges_under_timestep_refinement():
    coarse_request, coarse_context = _case(0.002)
    fine_request, fine_context = _case(0.001)
    coarse = evaluate_dynamic_fracture(coarse_request, coarse_context)
    fine = evaluate_dynamic_fracture(fine_request, fine_context)
    assert coarse["status"] == fine["status"] == "computed_diagnostic"
    assert len(coarse["events"]) == len(fine["events"]) == 1
    assert abs(coarse["events"][0]["time_s"] - fine["events"][0]["time_s"]) < 2.0e-5
    assert abs(coarse["events"][0]["advance_m"] - fine["events"][0]["advance_m"]) < 2.0e-4
