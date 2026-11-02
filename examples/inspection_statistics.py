"""Authored truth-labelled trials and SI geometry; no observed field performance is implied."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.inspection_statistics import (
    PARAMETER_UNITS,
    REVIEWS,
    InspectionStatisticsRequest,
    InspectionTrial,
    ThicknessGridLayout,
    TOFDReference,
)


def make_cases():
    context = RunContext(
        asset_id="authored_inspection_asset",
        scenario_id="authored_inspection_statistical_and_geometric_checks",
        clock_id="authored_acquisition_clock_no_service_age_inferred",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original independent trial witnesses and declared SI geometry checks",
                origin="synthetic",
                license_id="MIT",
                limitations=("Synthetic mathematical and contract checks; no field calibration.",),
            ),
        ),
    )
    pod_trials = tuple(
        InspectionTrial(
            observation_id=f"pod_observation_{group}_{i}",
            specimen_id=f"pod_specimen_{group}_{i}",
            clock_id=context.clock_id,
            truth_flawed=True,
            detected=i < hits,
            flaw_size_m=size,
            cluster_id=f"independent_pod_{group}_{i}",
        )
        for group, (size, hits) in enumerate(((0.001, 2), (0.002, 5), (0.004, 8)))
        for i in range(10)
    )
    detection_trials = tuple(
        InspectionTrial(
            observation_id=f"detection_{i}",
            specimen_id=f"detection_specimen_{i}",
            clock_id=context.clock_id,
            truth_flawed=True,
            detected=True,
            flaw_size_m=0.002,
        )
        for i in range(10)
    )
    parameters = {
        "inspection.binomial_evidence": {
            "opportunities": 10,
            "positive_responses": 10,
            "confidence_level": 0.95,
            "response_kind": "detection_at_declared_size",
            "independent_trials_declared": True,
        },
        "inspection.hit_miss_pod": {
            "specimen_ids": [t.specimen_id for t in pod_trials],
            "flaw_size_m": [t.flaw_size_m for t in pod_trials],
            "detected": [t.detected for t in pod_trials],
            "query_sizes_m": [0.004, 0.001, 0.002],
            "target_probability": 0.5,
            "confidence_level": 0.95,
            "maximum_threshold_size_m": 0.01,
            "independent_trials_declared": True,
        },
        "inspection.tofd_depth": {
            "probe_index_separation_m": 0.03,
            "longitudinal_sound_speed_m_s": 6000.0,
            "delay_from_lateral_wave_s": 1e-6,
            "wall_thickness_m": 0.02,
        },
        "geometry.thickness_grid": {
            "cell_thickness_m": [[0.012, 0.006, None], [0.008, None, 0.004]],
            "cell_size_x_m": 0.02,
            "cell_size_y_m": 0.03,
            "reference_thickness_m": 0.01,
            "reporting_threshold_m": 0.008,
        },
    }
    cases = {
        method: InspectionStatisticsRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="authored_location",
            clock_id=context.clock_id,
            specimen_set_id="authored_distinct_specimen_set_or_mapped_section",
            material_condition_id="authored_material_condition",
            procedure_id="authored_procedure",
            procedure_revision="1",
            test_conditions_id="authored_comparable_conditions",
            parameters=p,
            input_units=PARAMETER_UNITS[method],
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "authored"),
            evidence_ids=("authored",),
            **(
                {"trials": detection_trials, "declared_detection_size_m": 0.002}
                if method == "inspection.binomial_evidence"
                else {"trials": pod_trials}
                if method == "inspection.hit_miss_pod"
                else {
                    "tofd_reference": TOFDReference(
                        clock_id=context.clock_id,
                        lateral_frame_id="frame_1",
                        diffraction_frame_id="frame_1",
                        frame_sha256="a" * 64,
                        transmit_probe_id="transmit",
                        receive_probe_id="receive",
                        transmit_wedge_id="transmit_wedge",
                        receive_wedge_id="receive_wedge",
                        separation_basis_id="authored_measured_index_positions",
                        velocity_basis_id="authored_specimen_velocity",
                        wall_thickness_basis_id="authored_wall",
                        lateral_arrival_s=5e-6,
                        diffraction_arrival_s=6e-6,
                        backwall_arrival_s=10e-6,
                    )
                }
                if method == "inspection.tofd_depth"
                else {
                    "grid_layout": ThicknessGridLayout(
                        coordinate_frame_id="authored_surface_coordinates",
                        row_ids=("row_original_1", "row_original_2"),
                        column_ids=("column_original_1", "column_original_2", "column_original_3"),
                        positive_x_direction="pipe_axial_declared_positive",
                        positive_y_direction="surface_circumferential_declared_positive",
                        first_cell_center_x_m=0.0,
                        first_cell_center_y_m=0.0,
                    )
                }
            ),
        )
        for method, p in parameters.items()
    }
    return cases, context


if __name__ == "__main__":
    import json

    from pipeline_integrity.inspection_statistics import evaluate_inspection_statistics

    requests, context = make_cases()
    print(
        json.dumps(
            {
                method: evaluate_inspection_statistics(request, context)["status"]
                for method, request in requests.items()
            },
            indent=2,
            allow_nan=False,
        )
    )
