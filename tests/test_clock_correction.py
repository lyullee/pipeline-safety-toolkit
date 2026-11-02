"""Synthetic contract regressions for explicit source-clock correction candidates."""

import pytest

from pipeline_integrity.clock_correction import (
    ClockCorrectionModel,
    apply_clock_correction_candidate,
    verify_clock_correction_candidate,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame


def fixture(reviewed=True):
    context = RunContext(
        asset_id="sensor-a",
        scenario_id="run-a",
        clock_id="sensor-clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="model", citation="synthetic clock model", origin="synthetic"),
            EvidenceRef(id="review", citation="synthetic reviewer decision", origin="synthetic"),
        ),
    )
    model = ClockCorrectionModel(
        model_id="clock-model-a",
        source_clock_id="sensor-clock",
        reference_clock_id="plant-clock",
        source_epoch_s=100.0,
        reference_epoch_s=100.0,
        offset_s=2.0,
        rate_error=0.01,
        valid_source_start_s=90.0,
        valid_source_end_s=200.0,
        residual_bound_s=0.05,
        model_evidence_id="model",
        review_evidence_id="review",
        caller_reviewed=reviewed,
    )
    frames = tuple(
        SignalFrame(
            stream_id="sensor-a",
            clock_id="sensor-clock",
            time_s=110.0 + i,
            values={"pressure": 1.0 + i},
            units={"pressure": "Pa"},
            valid={"pressure": True},
            origin="synthetic",
        )
        for i in range(3)
    )
    return context, model, frames


def test_reviewed_clock_model_emits_order_preserving_candidate_and_keeps_source():
    context, model, frames = fixture()
    result = apply_clock_correction_candidate(
        frames, model, context, observation_ids=("obs-1", "obs-2", "obs-3")
    )
    assert result.status == "computed"
    assert result.source_times_s == (110.0, 111.0, 112.0)
    assert result.observation_ids == ("obs-1", "obs-2", "obs-3")
    assert result.candidate_times_s == pytest.approx((107.9207920792, 108.9108910891, 109.9009900990))
    assert tuple(item.time_s for item in result.source_frames) == result.source_times_s
    assert all(item.clock_id == "plant-clock" for item in result.candidate_frames)
    assert result.candidate_context.clock_id == "plant-clock"
    assert result.candidate_context.asset_id == context.asset_id
    assert result.candidate_context.scenario_id == context.scenario_id
    assert result.candidate_context.evidence == context.evidence
    assert result.observation_order_preserved and result.source_times_retained
    assert not result.synchronization_proven and not result.causal_alignment_proven
    assert verify_clock_correction_candidate(result)


def test_unreviewed_clock_model_cannot_emit_candidate_times():
    context, model, frames = fixture(reviewed=False)
    with pytest.raises(ValueError, match="caller-reviewed"):
        apply_clock_correction_candidate(frames, model, context, observation_ids=("1", "2", "3"))


def test_missing_review_evidence_is_rejected():
    context, model, frames = fixture()
    context = context.model_copy(update={"evidence": context.evidence[:1]})
    with pytest.raises(ValueError, match="evidence"):
        apply_clock_correction_candidate(frames, model, context, observation_ids=("1", "2", "3"))


def test_nonmonotone_source_rows_are_rejected_without_sorting():
    context, model, frames = fixture()
    reordered = (frames[1], frames[0], frames[2])
    with pytest.raises(ValueError, match="never sorted"):
        apply_clock_correction_candidate(reordered, model, context, observation_ids=("2", "1", "3"))


def test_tampered_candidate_receipt_fails_verification():
    context, model, frames = fixture()
    record = apply_clock_correction_candidate(frames, model, context, observation_ids=("1", "2", "3"))
    altered = record.model_copy(update={"candidate_times_s": (0.0, 1.0, 2.0)})
    with pytest.raises(ValueError, match="receipt changed"):
        verify_clock_correction_candidate(altered)
