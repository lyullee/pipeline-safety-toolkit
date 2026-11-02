"""Analytic compression and thermo-optic compensation check for anchored fiber I/Q."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fiber_interferometry import (
    FiberInterferometryRequest,
    OpticalGaugeTrace,
    OpticalPhaseAnchor,
    evaluate_fiber_interferometry,
)


def test_anchored_iq_recovers_compressive_strain_after_heating_and_cooling_phase_correction():
    strain_sensitivity = 1.0e6
    thermal_sensitivity = 0.8
    reference_phase = 0.0
    strain = tuple(
        value * 1.0e-6
        for value in (0.0, -0.25, -0.5, -0.75, -1.0, -1.25, -1.5, -1.75, -2.0,
                      -2.25, -2.5, -2.75, -3.0, -2.75, -2.5, -2.25, -2.0)
    )
    temperature = (0.0, 0.5, 1.0, 1.5, 2.0, 1.5, 1.0, 0.5, 0.0,
                   -0.5, -1.0, -1.5, -2.0, -1.5, -1.0, -0.5, 0.0)
    phase = tuple(
        reference_phase + strain_sensitivity * eps + thermal_sensitivity * delta_t
        for eps, delta_t in zip(strain, temperature, strict=True)
    )
    left_phase = 0.37
    gauge_id = "compression_gauge"
    evidence_id = "phase_strain_thermal_calibration"
    calibration_digest = digest((strain_sensitivity, thermal_sensitivity, reference_phase))
    evidence = EvidenceRef(
        id=evidence_id,
        citation="Authored synthetic phase/sensitivity calibration",
        origin="synthetic",
        sha256=calibration_digest,
    )
    gauge = OpticalGaugeTrace(
        id=gauge_id,
        fiber_id="fiber_A",
        start_distance_m=10.0,
        end_distance_m=11.0,
        left_I=tuple(math.cos(left_phase) for _ in phase),
        left_Q=tuple(math.sin(left_phase) for _ in phase),
        right_I=tuple(1.7 * math.cos(left_phase + value) for value in phase),
        right_Q=tuple(1.7 * math.sin(left_phase + value) for value in phase),
        valid=(True,) * len(phase),
        detector_amplitude_unit="V",
        minimum_valid_amplitude=0.1,
        strain_phase_sensitivity_rad=strain_sensitivity,
        phase_at_zero_strain_rad=reference_phase,
        thermal_phase_sensitivity_rad_K=thermal_sensitivity,
        temperature_change_K=temperature,
        anchors=(OpticalPhaseAnchor(
            sample_index=0,
            unwrapped_differential_phase_rad=phase[0],
            evidence_id=evidence_id,
        ),),
        evidence_ids=(evidence_id,),
    )
    request = FiberInterferometryRequest(
        id="compression_and_thermal_cycle",
        asset_id="fiber_asset",
        clock_id="elapsed_s",
        sample_time_s=tuple(0.1 * i for i in range(len(phase))),
        gauges=(gauge,),
        maximum_sample_gap_s=0.101,
        maximum_reviewed_phase_increment_rad=0.7,
        maximum_anchor_phase_residual_rad=1e-10,
        coherence_gauge_support_and_linear_phase_sensitivity_reviewed=True,
        temporal_phase_alias_bound_and_anchors_reviewed=True,
        temperature_correction_and_reference_state_reviewed=True,
    )
    context = RunContext(
        asset_id="fiber_asset",
        scenario_id="compression_and_thermal_cycle",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(evidence,),
    )

    record = evaluate_fiber_interferometry(request, context)
    assert record["status"] == "computed_diagnostic"
    rows = record["gauges"][0]["states"]
    assert len(rows) == len(strain)
    for row, expected_phase, expected_strain in zip(rows, phase, strain, strict=True):
        assert row["valid"]
        assert row["unwrapped_phase_rad"] == pytest.approx(expected_phase, abs=1e-12)
        assert row["fiber_gauge_mean_axial_strain"] == pytest.approx(expected_strain, abs=1e-18)
    assert any(row["unwrapped_phase_rad"] < -math.pi and row["wrapped_phase_rad"] > 2.9 for row in rows)
    assert rows[0]["fiber_gauge_mean_axial_strain"] == pytest.approx(0.0, abs=1e-18)
    assert all(row["fiber_gauge_mean_axial_strain"] <= 0.0 for row in rows)
