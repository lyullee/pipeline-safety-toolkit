"""Closed-form coherent sum for finite, fixed linear acoustic transfer paths."""

import math

import pytest

from pipeline_integrity.acoustic_multipath import (
    AcousticMultipathRequest,
    AcousticTransferPath,
    AcousticTransferSensor,
    evaluate_acoustic_multipath,
    verify_acoustic_multipath_record,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.inverse import OperatingPoint
from pipeline_integrity.inverse import (
    FitParameter,
    ObservedChannel,
    OperatingPointFitRequest,
    OperatingPointObservations,
    OperatingPointResidualMetric,
    ResidualRow,
    ResidualScale,
)


def _case():
    evidence = tuple(
        EvidenceRef(
            id=identity,
            citation="Authored synthetic transfer-path review",
            origin="synthetic",
            sha256=digest(("multipath-review", identity)),
        )
        for identity in (
            "excitation",
            "model-review",
            "sensor-calibration",
            "path-a-review",
            "path-b-review",
            "frequency-review",
        )
    )
    context = RunContext(
        asset_id="synthetic_acoustic_asset",
        scenario_id="two-path-coherent-sum",
        clock_id="synthetic_frequency_axis",
        origin="synthetic",
        evidence=evidence,
    )
    frequency_hash = next(item.sha256 for item in evidence if item.id == "frequency-review")
    points = tuple(
        OperatingPoint(
            id=f"frequency_{frequency:g}_Hz",
            state={"frequency_Hz": frequency},
            state_units={"frequency_Hz": "Hz"},
            group_id="fixed_source_excitation",
            evidence_ids=("frequency-review",),
            source_digests={"frequency-data": frequency_hash},
        )
        for frequency in (0.0, 250.0)
    )
    request = AcousticMultipathRequest(
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        points=points,
        sensors=(
            AcousticTransferSensor(
                id="sensor_a",
                real_channel_id="sensor_a_real",
                imaginary_channel_id="sensor_a_imag",
                calibration_evidence_id="sensor-calibration",
            ),
        ),
        paths=(
            AcousticTransferPath(
                id="direct_path",
                sensor_id="sensor_a",
                amplitude_gain=2.0,
                delay_s=0.001,
                phase_rad=0.0,
                evidence_id="path-a-review",
            ),
            AcousticTransferPath(
                id="reflected_path",
                sensor_id="sensor_a",
                amplitude_gain=1.0,
                delay_s=0.0,
                phase_rad=math.pi / 2.0,
                evidence_id="path-b-review",
            ),
        ),
        source_excitation_evidence_id="excitation",
        model_review_evidence_id="model-review",
        known_excitation_transfer_reviewed=True,
        complex_gain_phase_clock_calibration_reviewed=True,
        finite_linear_time_invariant_paths_reviewed=True,
    )
    return request, context


def test_two_path_complex_transfer_matches_closed_form_phase_interference():
    request, context = _case()
    record = evaluate_acoustic_multipath(request, context)

    assert record["status"] == "computed_diagnostic"
    prediction = record["prediction"]
    assert prediction["point_ids"] == [point.id for point in request.points]
    assert prediction["values"]["sensor_a_real"] == pytest.approx((2.0, 0.0), abs=1e-14)
    assert prediction["values"]["sensor_a_imag"] == pytest.approx((1.0, -1.0), abs=1e-14)
    assert prediction["diagnostics"]["field_qualification"] is False
    assert prediction["diagnostics"]["source_excitation_inferred"] is False
    assert verify_acoustic_multipath_record(record, request=request, context=context)


def test_bounded_delay_fit_recovers_closed_form_path_delay_and_heldout_transfer():
    from pipeline_integrity.acoustic_multipath import (
        AcousticMultipathFitRequest,
        evaluate_acoustic_multipath_fit,
        verify_acoustic_multipath_fit_record,
    )

    model, context = _case()
    frequencies = (100.0, 200.0, 300.0)
    points = tuple(
        OperatingPoint(
            id=f"fit_frequency_{frequency:g}_Hz",
            state={"frequency_Hz": frequency},
            state_units={"frequency_Hz": "Hz"},
            group_id="fixed_source_excitation",
            evidence_ids=("frequency-review",),
            source_digests={
                "frequency-data": next(e.sha256 for e in context.evidence if e.id == "frequency-review")
            },
        )
        for frequency in frequencies
    )
    bound_model = model.model_copy(
        update={
            "points": points,
            "paths": (
                model.paths[0].model_copy(update={"delay_parameter_id": "direct_delay"}),
                model.paths[1],
            ),
        }
    )
    true_delay_s = 0.001
    complex_response = tuple(
        2.0 * complex(math.cos(-math.tau * f * true_delay_s), math.sin(-math.tau * f * true_delay_s))
        + 1.0j
        for f in frequencies
    )
    values_real = tuple(value.real for value in complex_response)
    values_imag = tuple(value.imag for value in complex_response)
    fit_source_sha = digest("authored synthetic complex covariance")
    evidence = context.evidence + (
        EvidenceRef(
            id="complex-covariance",
            citation="Authored synthetic diagonal covariance for the complex transfer check",
            origin="synthetic",
            sha256=fit_source_sha,
        ),
        EvidenceRef(
            id="synthetic-scale",
            citation="Authored synthetic residual scale used by the fit fixture",
            origin="synthetic",
            sha256=digest("authored synthetic residual scale"),
        ),
    )
    context = context.model_copy(update={"evidence": evidence})
    channels = (
        ObservedChannel(
            id="sensor_a_real",
            scale=ResidualScale(value=1.0, unit="1", source_id="synthetic-scale"),
            values=values_real,
            valid=(True,) * len(frequencies),
        ),
        ObservedChannel(
            id="sensor_a_imag",
            scale=ResidualScale(value=1.0, unit="1", source_id="synthetic-scale"),
            values=values_imag,
            valid=(True,) * len(frequencies),
        ),
    )
    observations = OperatingPointObservations(
        id="authored_multipath_observations",
        asset_id=model.asset_id,
        origin="synthetic",
        source_digests={"frequency-data": points[0].source_digests["frequency-data"]},
        points=points,
        channels=channels,
    )
    rows = tuple(
        ResidualRow(point_id=point.id, channel_id=channel.id, unit="1")
        for point in points
        for channel in channels
    )
    covariance = OperatingPointResidualMetric(
        kind="covariance",
        rows=rows,
        matrix=tuple(tuple(1e-8 if i == j else 0.0 for j in range(len(rows))) for i in range(len(rows))),
        source_sha256=fit_source_sha,
        evidence_id="complex-covariance",
        definition_id="authored_diagonal_complex_covariance",
        applicability_reviewed=True,
    )
    fit = OperatingPointFitRequest(
        parameters=(
            FitParameter(id="direct_delay", unit="s", lower=0.0002, upper=0.002, initial=0.0007),
        ),
        provider_id="finite_coherent_acoustic_paths",
        provider_version="1",
        training_point_ids=(points[0].id, points[1].id),
        heldout_point_ids=(points[2].id,),
        residual_metric=covariance,
        maximum_model_evaluations=100,
        maximum_optimizer_evaluations=100,
        parameter_tolerance=1e-8,
    )
    request = AcousticMultipathFitRequest(model=bound_model, observations=observations, fit=fit)

    record = evaluate_acoustic_multipath_fit(request, context)

    assert record["status"] == "computed_diagnostic"
    native = record["native_fit"]
    assert native["status"] == "fitted"
    assert native["fitted_parameters"]["direct_delay"] == pytest.approx(true_delay_s, abs=1e-7)
    assert native["heldout_point_ids"] == [points[2].id]
    assert native["validation"]["fitted"]["normalized_rmse"] < 1e-5
    assert record["independent_path_identification_claim"] is False
    assert record["field_qualification"] is False
    assert verify_acoustic_multipath_fit_record(record, request=request, context=context)
