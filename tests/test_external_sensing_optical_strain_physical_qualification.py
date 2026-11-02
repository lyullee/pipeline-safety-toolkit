"""Independent bounded oracle for calibrated coherent optical strain sensing."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fiber_interferometry import (
    FiberInterferometryRequest,
    OpticalGaugeTrace,
    OpticalPhaseAnchor,
    evaluate_fiber_interferometry,
)


def make_case(strains, temperatures, *, scale=1.0, gap_after=None, amplitude=(1.0,)):
    times = tuple(float(i) for i in range(len(strains)))
    sensitivity = 1.0e6 * scale
    thermal_sensitivity = -0.35
    zero_phase = 0.17
    truth_phase = tuple(
        zero_phase + sensitivity * eps + thermal_sensitivity * temp
        for eps, temp in zip(strains, temperatures, strict=True)
    )
    left_phase = -0.61
    amp = tuple(amplitude[i % len(amplitude)] for i in range(len(times)))
    gap_id = "optical_calibration"
    q = FiberInterferometryRequest(
        id="fiber_strain_closed_form",
        asset_id="test_fiber",
        clock_id="sample_seconds",
        sample_time_s=times,
        gauges=(OpticalGaugeTrace(
            id="gauge_1",
            fiber_id="fiber_1",
            start_distance_m=5.0,
            end_distance_m=6.0,
            left_I=tuple(math.cos(left_phase) for _ in times),
            left_Q=tuple(math.sin(left_phase) for _ in times),
            right_I=tuple(a * math.cos(left_phase + p) for a, p in zip(amp, truth_phase, strict=True)),
            right_Q=tuple(a * math.sin(left_phase + p) for a, p in zip(amp, truth_phase, strict=True)),
            valid=tuple(True for _ in times),
            detector_amplitude_unit="V",
            minimum_valid_amplitude=0.2,
            strain_phase_sensitivity_rad=sensitivity,
            phase_at_zero_strain_rad=zero_phase,
            thermal_phase_sensitivity_rad_K=thermal_sensitivity,
            temperature_change_K=tuple(temperatures),
            anchors=(OpticalPhaseAnchor(
                sample_index=0,
                unwrapped_differential_phase_rad=truth_phase[0],
                evidence_id=gap_id,
            ),),
            evidence_ids=(gap_id,),
        ),),
        maximum_sample_gap_s=1.1 if gap_after is None else gap_after,
        maximum_reviewed_phase_increment_rad=2.5,
        maximum_anchor_phase_residual_rad=1e-10,
        coherence_gauge_support_and_linear_phase_sensitivity_reviewed=True,
        temporal_phase_alias_bound_and_anchors_reviewed=True,
        temperature_correction_and_reference_state_reviewed=True,
    )
    evidence = EvidenceRef(
        id=gap_id,
        citation="Authored synthetic optical phase-to-strain calibration fixture",
        origin="synthetic",
        sha256=digest((sensitivity, thermal_sensitivity, zero_phase, strains, temperatures)),
    )
    context = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.id,
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(evidence,),
    )
    return q, context, truth_phase


def test_phase_to_strain_matches_independent_inverse_calibration_across_sign_and_thermal_cycle():
    strain = (0.0, 2.0e-6, 4.0e-6, 2.0e-6, 0.0, -2.0e-6, -4.0e-6)
    temperature = (0.0, 1.0, 2.0, 1.0, 0.0, -1.0, -2.0)
    q, context, phase = make_case(strain, temperature, amplitude=(0.8, 1.4, 2.0))
    result = evaluate_fiber_interferometry(q, context)
    assert result["status"] == "computed_diagnostic"
    states = result["gauges"][0]["states"]
    for i, row in enumerate(states):
        oracle_strain = (phase[i] - 0.17 - (-0.35) * temperature[i]) / 1.0e6
        assert row["valid"]
        assert row["fiber_gauge_mean_axial_strain"] == pytest.approx(oracle_strain, abs=2e-18)
        assert row["fiber_gauge_mean_axial_strain"] == pytest.approx(strain[i], abs=2e-18)


def test_joint_phase_sensitivity_and_strain_scaling_preserves_recovered_strain():
    strain = (0.0, 1.0e-6, 2.0e-6, 3.0e-6)
    temp = (0.0, 0.5, 1.0, 1.5)
    base_q, base_c, _ = make_case(strain, temp, scale=1.0)
    scaled_strain = tuple(value / 4.0 for value in strain)
    scaled_q, scaled_c, _ = make_case(scaled_strain, temp, scale=4.0)
    base = evaluate_fiber_interferometry(base_q, base_c)["gauges"][0]["states"]
    scaled = evaluate_fiber_interferometry(scaled_q, scaled_c)["gauges"][0]["states"]
    assert [r["fiber_gauge_mean_axial_strain"] for r in scaled] == pytest.approx(
        [r["fiber_gauge_mean_axial_strain"] / 4.0 for r in base], abs=2e-18
    )


def test_phase_branch_is_not_interpolated_across_a_clock_gap_without_new_anchor():
    strain = (0.0, 0.2e-6, 0.4e-6, 0.6e-6)
    temp = (0.0, 0.0, 0.0, 0.0)
    q, context, _ = make_case(strain, temp)
    q = q.model_copy(update={"sample_time_s": (0.0, 1.0, 4.0, 5.0), "maximum_sample_gap_s": 1.1})
    result = evaluate_fiber_interferometry(q, context)
    states = result["gauges"][0]["states"]
    assert states[0]["valid"] and states[1]["valid"]
    assert states[2]["valid"] is False
    assert states[3]["valid"] is False
    assert states[3]["reason"] == "phase_branch_unknown_without_anchor"
