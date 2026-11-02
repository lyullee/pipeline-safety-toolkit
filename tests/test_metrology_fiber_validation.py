"""Independent GLS, sampled OU covariance and coherent optical phase references.

All error laws and optical calibration values are authored inputs. These tests
exercise mathematical recovery and missing-data handling, not field transfer.
"""

import math

import pytest

from pipeline_integrity.constrained_metrology import (
    ConstrainedGLSRequest,
    ExactParameterConstraint,
    GLSParameter,
    evaluate_constrained_gls,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.fiber_interferometry import (
    FiberInterferometryRequest,
    OpticalGaugeTrace,
    OpticalPhaseAnchor,
    evaluate_fiber_interferometry,
)
from pipeline_integrity.metrology import MeasurementBasis, MeasurementInput, StandardCovariance
from pipeline_integrity.temporal_metrology import (
    SharedTemporalMode,
    TemporalWeightedRequest,
    evaluate_temporal_weighted_budget,
)


def _context():
    return RunContext(
        asset_id="metrology", scenario_id="analytic_reference", clock_id="seconds", origin="synthetic",
        evidence=(EvidenceRef(id="review", citation="Authored independent mathematical references",
                              origin="synthetic", sha256="e" * 64),),
    )


def _basis():
    return MeasurementBasis(quantity="length", unit="m", definition_id="length", evidence_id="review")


def _gls(y, covariance, design, *, constraints=(), rank=2):
    observations = tuple(MeasurementInput(id=f"observation_{i}", estimate=value, basis=_basis(),
                                          evidence_id="review", valid=True) for i, value in enumerate(y))
    parameters = tuple(GLSParameter(id=f"parameter_{i}", basis=_basis(), scale=1.0,
                                    evidence_id="review") for i in range(len(design[0])))
    return ConstrainedGLSRequest(
        id="linear_reference", asset_id="metrology", clock_id="seconds", event_id="event", time_s=0.0,
        observations=observations, parameters=parameters,
        design_matrix=design, design_coefficient_units=("dimensionless",) * len(parameters),
        observation_scales=(1.0,) * len(y),
        covariance=StandardCovariance(input_ids=tuple(o.id for o in observations), values=covariance,
                                      evidence_id="review", definition_reviewed=True),
        declared_covariance_rank=rank, exact_constraints=constraints,
        exact_linear_model_and_absolute_covariance_reviewed=True,
        singular_rank_and_exact_directions_reviewed=True,
        model_evidence_id="review", evidence_ids=("review",),
    )


def test_correlated_gls_common_mean_matches_two_observation_closed_form():
    # Covariance inverse gives mean weights (8/11,3/11), variance 35/11.
    record = evaluate_constrained_gls(_gls(
        (1.0, 3.0), ((4.0, 1.0), (1.0, 9.0)), ((1.0,), (1.0,)),
    ), _context())
    assert record["status"] == "computed", record["issues"]
    assert record["parameter_estimates"] == pytest.approx([17 / 11], abs=1e-12)
    assert record["parameter_covariance"]["values"][0][0] == pytest.approx(35 / 11, abs=1e-12)
    assert record["stochastic_sum_squares"] == pytest.approx(4 / 11, abs=1e-12)
    assert record["covariance_jitter_added"] is False
    assert record["residual_variance_rescaling_applied"] is False


def test_gls_exact_parameter_sum_matches_lagrange_multiplier_solution():
    constraint = ExactParameterConstraint(
        id="known_sum", coefficients=(1.0, 1.0), coefficient_units=("dimensionless", "dimensionless"),
        target=5.0, target_basis=_basis(), residual_scale=1.0, evidence_id="review",
    )
    record = evaluate_constrained_gls(_gls(
        (2.0, 4.0), ((1.0, 0.0), (0.0, 4.0)), ((1.0, 0.0), (0.0, 1.0)),
        constraints=(constraint,),
    ), _context())
    assert record["status"] == "computed", record["issues"]
    assert record["parameter_estimates"] == pytest.approx([1.8, 3.2], abs=1e-12)
    covariance = record["parameter_covariance"]["values"]
    assert covariance[0] == pytest.approx([0.8, -0.8], abs=1e-12)
    assert covariance[1] == pytest.approx([-0.8, 0.8], abs=1e-12)
    assert sum(record["parameter_estimates"]) == pytest.approx(5.0, abs=1e-12)
    assert sum(sum(row) for row in covariance) == pytest.approx(0.0, abs=1e-12)
    assert record["stochastic_sum_squares"] == pytest.approx(0.2, abs=1e-12)


def test_singular_gls_zero_variance_observation_remains_an_exact_equality():
    request = _gls((2.0, 5.0), ((0.0, 0.0), (0.0, 4.0)), ((1.0,), (1.0,)), rank=1)
    record = evaluate_constrained_gls(request, _context())
    assert record["status"] == "computed", record["issues"]
    assert record["parameter_estimates"] == pytest.approx([2.0], abs=1e-12)
    assert record["parameter_covariance"]["values"][0][0] == 0.0
    assert record["stochastic_sum_squares"] == pytest.approx(9 / 4, abs=1e-12)
    assert record["normalized_exact_equalities"]["covariance_null_direction_count"] == 1
    incorrect = evaluate_constrained_gls(request.model_copy(update={"declared_covariance_rank": 2}), _context())
    assert incorrect["status"] == "out_of_scope"
    assert "rank" in " ".join(incorrect["issues"])


def _temporal(groups=("one",) * 4):
    return TemporalWeightedRequest(
        id="sample_mean", asset_id="metrology", clock_id="seconds",
        times_s=(0.0, 1.0, 2.0, 3.0), estimates=(1.0, 3.0, 5.0, 7.0), weights=(0.25,) * 4,
        weight_unit="dimensionless", input_basis=_basis(), output_basis=_basis(),
        white_standard_uncertainties=(0.5,) * 4, temporal_standard_uncertainties=(2.0,) * 4,
        correlation_time_s=2.0, independent_reset_groups=groups,
        shared_modes=(SharedTemporalMode(id="shared_bias", standard_effect_by_sample=(1.0,) * 4,
                                         evidence_id="review"),),
        supplied_error_components_independent_reviewed=True, exact_linear_measurand_reviewed=True,
        evidence_ids=("review",),
    )


def test_sampled_ou_mean_variance_matches_finite_geometric_covariance_sum():
    record = evaluate_temporal_weighted_budget(_temporal(), _context())
    phi = math.exp(-0.5)
    # This is a mean of four point samples, not a continuous time integral.
    temporal = 4 / 16 * (4 + 2 * sum((4 - k) * phi**k for k in range(1, 4)))
    assert record["status"] == "computed"
    assert record["estimate"] == 4.0
    assert record["variance_components"]["white"] == pytest.approx(0.25 / 4, abs=1e-14)
    assert record["variance_components"]["temporal"] == pytest.approx(temporal, abs=1e-12)
    assert record["variance_components"]["shared_modes"]["shared_bias"] == 1.0
    assert record["variance"] == pytest.approx(temporal + 0.25 / 4 + 1, abs=1e-12)


def test_ou_reset_groups_break_temporal_covariance_but_preserve_supplied_shared_bias():
    request = _temporal(("first", "first", "second", "second"))
    record = evaluate_temporal_weighted_budget(request, _context())
    temporal = 4 / 16 * (4 + 4 * math.exp(-0.5))
    assert record["variance_components"]["temporal"] == pytest.approx(temporal, abs=1e-12)
    assert record["variance_components"]["shared_modes"]["shared_bias"] == 1.0
    signed = request.model_copy(update={"weights": (1.0, -1.0, 2.0, -2.0)})
    result = evaluate_temporal_weighted_budget(signed, _context())
    expected = math.fsum(
        signed.weights[i] * signed.weights[j] * 4 * math.exp(-abs(i - j) / 2)
        for i in range(4) for j in range(4)
        if signed.independent_reset_groups[i] == signed.independent_reset_groups[j]
    )
    assert result["variance_components"]["temporal"] == pytest.approx(expected, abs=1e-12)
    assert result["variance_components"]["shared_modes"]["shared_bias"] == 0.0
    with pytest.raises(ValueError, match="reviews"):
        evaluate_temporal_weighted_budget(request.model_copy(update={"exact_linear_measurand_reviewed": False}), _context())


def _fiber():
    reference_phase = 6 * math.pi + 0.2
    strains = tuple(i * 1e-6 for i in range(6))
    temperatures = tuple(i * 0.2 for i in range(6))
    phase = tuple(reference_phase + 1e6 * strain + 0.25 * temp
                  for strain, temp in zip(strains, temperatures, strict=True))
    common = (-2.0, 0.1, 1.0, 0.5, 2.0, -1.0)
    gauge = OpticalGaugeTrace(
        id="gauge", fiber_id="fiber", start_distance_m=0.0, end_distance_m=1.0,
        left_I=tuple(math.cos(v) for v in common), left_Q=tuple(math.sin(v) for v in common),
        right_I=tuple(2 * math.cos(v + p) for v, p in zip(common, phase, strict=True)),
        right_Q=tuple(2 * math.sin(v + p) for v, p in zip(common, phase, strict=True)),
        valid=(True,) * 6, detector_amplitude_unit="V", minimum_valid_amplitude=0.1,
        strain_phase_sensitivity_rad=1e6, phase_at_zero_strain_rad=reference_phase,
        thermal_phase_sensitivity_rad_K=0.25, temperature_change_K=temperatures,
        anchors=(OpticalPhaseAnchor(sample_index=0, unwrapped_differential_phase_rad=phase[0], evidence_id="review"),),
        evidence_ids=("review",),
    )
    request = FiberInterferometryRequest(
        id="coherent", asset_id="metrology", clock_id="seconds",
        sample_time_s=tuple(float(i) for i in range(6)), gauges=(gauge,), maximum_sample_gap_s=1.1,
        maximum_reviewed_phase_increment_rad=1.2, maximum_anchor_phase_residual_rad=1e-10,
        coherence_gauge_support_and_linear_phase_sensitivity_reviewed=True,
        temporal_phase_alias_bound_and_anchors_reviewed=True,
        temperature_correction_and_reference_state_reviewed=True,
    )
    return request, strains, phase


def test_coherent_iq_recovers_anchored_phase_across_wrap_and_corrects_thermal_strain():
    request, strains, phases = _fiber()
    record = evaluate_fiber_interferometry(request, _context())
    assert record["status"] == "computed_diagnostic"
    rows = record["gauges"][0]["states"]
    assert len(rows) == 6 and all(row["valid"] for row in rows)
    for row, strain, phase in zip(rows, strains, phases, strict=True):
        assert row["unwrapped_phase_rad"] == pytest.approx(phase, abs=1e-12)
        assert row["wrapped_phase_rad"] == pytest.approx(math.remainder(phase, 2 * math.pi), abs=1e-12)
        assert row["fiber_gauge_mean_axial_strain"] == pytest.approx(strain, abs=1e-18)
    assert record["host_pipe_strain_transfer_inferred"] is False


def test_optical_fading_breaks_branch_until_new_anchor_without_interpolating_strain():
    request, strains, phases = _fiber()
    gauge = request.gauges[0]
    left_i, left_q = list(gauge.left_I), list(gauge.left_Q)
    left_i[2] = left_q[2] = 0.0
    anchor = OpticalPhaseAnchor(sample_index=4, unwrapped_differential_phase_rad=phases[4], evidence_id="review")
    changed = gauge.model_copy(update={"left_I": tuple(left_i), "left_Q": tuple(left_q),
                                       "anchors": (*gauge.anchors, anchor)})
    record = evaluate_fiber_interferometry(request.model_copy(update={"gauges": (changed,)}), _context())
    rows = record["gauges"][0]["states"]
    assert [row["valid"] for row in rows] == [True, True, False, False, True, True]
    assert rows[2]["reason"] == "fading"
    assert rows[3]["reason"] == "phase_branch_unknown_without_anchor"
    assert rows[2]["fiber_gauge_mean_axial_strain"] is rows[3]["fiber_gauge_mean_axial_strain"] is None
    assert rows[4]["fiber_gauge_mean_axial_strain"] == pytest.approx(strains[4], abs=1e-18)
    missing_anchors = gauge.model_copy(update={"anchors": ()})
    unknown = evaluate_fiber_interferometry(request.model_copy(update={"gauges": (missing_anchors,)}), _context())
    assert unknown["status"] == "needs_data"
    assert all(not row["valid"] for row in unknown["gauges"][0]["states"])
