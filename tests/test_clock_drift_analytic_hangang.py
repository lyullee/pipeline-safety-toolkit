"""Synthetic analytic reference for reviewed affine clock-drift correction.

No timestamps in this fixture are field measurements.
"""
import pytest

from pipeline_integrity.clock_correction import ClockCorrectionModel
from pipeline_integrity.clock_epochs import ClockEpoch, ClockEpochRequest, evaluate_clock_epochs
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest


def _ref(identity):
    return EvidenceRef(id=identity, citation="Authored synthetic clock-drift fixture", origin="synthetic",
                       sha256=digest(("clock-drift-test", identity)))


def test_epoch_clock_drift_mapping_matches_affine_solution_and_keeps_invalid_rows():
    refs = tuple(_ref(k) for k in ("source", "mapping", "review", "boundary", "order"))
    source_context = RunContext(asset_id="pipe", scenario_id="clock-drift-fixture", clock_id="sensor-clock",
                                origin="synthetic", evidence=refs[:4])
    frames = tuple(SignalFrame(stream_id="pressure", clock_id="sensor-clock", time_s=t,
        values={"pressure": value}, units={"pressure": "Pa"}, valid={"pressure": valid}, origin="synthetic")
        for t, value, valid in ((101.0, 2.0e6, True), (102.0, None, False), (103.0, 1.9e6, True)))
    rate_error, offset, residual = 0.002, 0.25, 0.0003
    model = ClockCorrectionModel(model_id="affine-drift", source_clock_id="sensor-clock",
        reference_clock_id="plant-clock", source_epoch_s=100.0, reference_epoch_s=5.0,
        offset_s=offset, rate_error=rate_error, valid_source_start_s=100.0, valid_source_end_s=104.0,
        residual_bound_s=residual, model_evidence_id="mapping", review_evidence_id="review",
        caller_reviewed=True)
    epoch = ClockEpoch(id="clock-segment-0", boundary_kind="initial", source_record_id="synthetic-record",
        source_record_sha256=refs[0].sha256, source_context=source_context, model=model,
        source_frames=frames, source_frames_digest=digest(frames),
        observation_ids=("row-a", "row-b", "row-c"), source_row_indices=(0, 1, 2),
        source_time_error_bounds_s=(0.0002, 0.0002, 0.0002), source_coverage_start_s=100.5,
        source_coverage_end_s=103.5, source_evidence_id="source",
        model_evidence_sha256=refs[1].sha256, review_evidence_sha256=refs[2].sha256,
        boundary_evidence_id="boundary", boundary_evidence_sha256=refs[3].sha256,
        mapping_residual_bound_basis="reference_clock_seconds",
        mapping_residual_covers_parameter_uncertainty_reviewed=True)
    context = RunContext(asset_id="pipe", scenario_id="clock-drift-fixture", clock_id="plant-clock",
                         origin="synthetic", evidence=refs)
    request = ClockEpochRequest(id="drift-review", asset_id="pipe", reference_clock_id="plant-clock",
        epochs=(epoch,), epoch_order_evidence_id="order", epoch_order_evidence_sha256=refs[4].sha256,
        epoch_order_and_boundaries_reviewed=True)

    record = evaluate_clock_epochs(request, context)
    expected = tuple(5.0 + (frame.time_s - 100.0 - offset) / (1.0 + rate_error) for frame in frames)
    actual = tuple(frame["time_s"] for frame in record["candidate_frames"])
    assert record["status"] == "computed"
    assert actual == pytest.approx(expected, abs=2e-15)
    assert record["target_frame_count"] == record["source_frame_count"] == 3
    assert record["dropped_frame_count"] == 0
    assert record["source_invalid_channel_count"] == record["target_invalid_channel_count"] == 1
    assert [frame["valid"]["pressure"] for frame in record["candidate_frames"]] == [True, False, True]
    expected_bound = 0.0002 / (1.0 + rate_error) + residual
    assert all(row["target_time_error_bound_s"] >= expected_bound for row in record["lineage"])
    assert record["field_qualification"] is False
