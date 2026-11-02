"""Authored simultaneous trajectory, not a pipeline material or dynamic solver calibration."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fracture_arrest import (
    REVIEWS,
    ArrestPoint,
    ArrestTrajectory,
    FractureArrestRequest,
    boundary_metadata,
    trajectory_binding,
    trajectory_payload_sha256,
)


def make_case(*, reviewed=False):
    trajectory = ArrestTrajectory(
        status="computed",
        origin="synthetic",
        asset_id="pipe",
        clock_id="event_clock",
        event_id="event",
        coordinate_frame_id="pipe_local",
        crack_path_id="axial_path",
        material_condition_id="authored_condition",
        material_orientation_id="authored_longitudinal",
        provider_id="authored_table",
        provider_version="1",
        provider_source_sha256=digest("authored simultaneous dynamic trajectory"),
        provider_configuration_sha256=digest("authored geometry and dynamic energy basis"),
        decompression_source_sha256=digest("authored pressure history; not a gas solver"),
        resistance_definition="dynamic_energy_per_new_crack_area",
        new_crack_area_basis_id="authored_projected_single_crack_area",
        pressure_envelope_definition_id="authored_dynamic_pressure_envelope",
        points=tuple(
            ArrestPoint(
                id=f"point_{i}",
                time_s=float(i),
                crack_path_position_m=position,
                local_pressure_pa_abs=pressure,
                temperature_k=290.0,
                propagation_velocity_m_s=velocity,
                driving_energy_release_j_m2=energy,
                dynamic_resistance_j_m2=1000.0,
                arrest_pressure_envelope_pa_abs=2e6,
                valid=True,
            )
            for i, (position, pressure, velocity, energy) in enumerate(
                (
                    (0.0, 3e6, 20.0, 1500.0),
                    (1.0, 1.9e6, 0.0, 900.0),
                    (1.0, 1.8e6, 0.0, 800.0),
                    (1.0, 1.7e6, 0.0, 700.0),
                )
            )
        ),
        native_receipt={"origin": "authored synthetic table; no physical solver executed"},
        evaluation_sha256="0" * 64,
    )
    q = FractureArrestRequest(
        id="authored_arrest",
        asset_id="pipe",
        location_id="pipe_section",
        clock_id="event_clock",
        material_condition_id="authored_condition",
        flaw_id="flaw",
        evidence_ids=("authored",),
        event_id="event",
        trajectory_origin="synthetic",
        coordinate_frame_id="pipe_local",
        crack_path_id="axial_path",
        new_crack_area_basis_id=trajectory.new_crack_area_basis_id,
        pressure_envelope_definition_id=trajectory.pressure_envelope_definition_id,
        material_orientation_id="authored_longitudinal",
        gas_composition_id="authored_gas",
        eos_basis_id="authored_basis",
        pipe_diameter_m=0.5,
        wall_thickness_m=0.01,
        path_position_limits_m=(0.0, 10.0),
        propagation_direction="increasing_path",
        time_limits_s=(0.0, 3.0),
        supported_temperature_k=(280.0, 300.0),
        source_mode="reviewed_trajectory",
        provider_id=trajectory.provider_id,
        provider_version=trajectory.provider_version,
        provider_source_sha256=trajectory.provider_source_sha256,
        provider_configuration_sha256=trajectory.provider_configuration_sha256,
        decompression_source_sha256=trajectory.decompression_source_sha256,
        expected_trajectory_payload_sha256=trajectory_payload_sha256(trajectory),
        stationary_velocity_tolerance_m_s=0.001,
        stationary_position_tolerance_m=0.001,
        minimum_sampled_window_s=2.0,
        maximum_sample_gap_s=1.0,
        reviews={k: reviewed for k in REVIEWS},
        review_evidence_ids={k: "authored" for k in REVIEWS},
    )
    c = RunContext(
        asset_id="pipe",
        scenario_id="authored_dynamic_trajectory",
        clock_id="event_clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored", citation="Original illustrative simultaneous trajectory", origin="synthetic"
            ),
        ),
        metadata={"fracture_arrest_boundary": boundary_metadata(q)},
    )
    trajectory = trajectory.model_copy(update={"evaluation_sha256": trajectory_binding(q, c, trajectory)})
    return q, c, trajectory
