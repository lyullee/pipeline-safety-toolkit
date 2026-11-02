"""Source-sealed clock correction candidates; caller review is always explicit."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest


class ClockCorrectionModel(Contract):
    """Caller-reviewed affine mapping from one clock domain to another.

    The model states that at ``source_epoch_s`` the reported clock reads
    ``reference_epoch_s + offset_s``. ``rate_error`` is the fractional rate
    difference of the source clock relative to the reference clock.
    """

    model_id: Name
    source_clock_id: Name
    reference_clock_id: Name
    source_epoch_s: float
    reference_epoch_s: float
    offset_s: float
    rate_error: float = Field(gt=-1)
    valid_source_start_s: Nonnegative
    valid_source_end_s: Positive
    residual_bound_s: Nonnegative
    model_evidence_id: Name
    review_evidence_id: Name
    caller_reviewed: bool = False

    @model_validator(mode="after")
    def valid_model(self):
        if self.reference_clock_id == self.source_clock_id:
            raise ValueError("clock correction requires distinct source and reference clock IDs")
        if self.valid_source_end_s <= self.valid_source_start_s:
            raise ValueError("clock model validity interval must have positive duration")
        if self.model_evidence_id == self.review_evidence_id:
            raise ValueError("model and caller review require distinct evidence records")
        return self


class ClockCorrectionRecord(Contract):
    schema: Literal["pipeline_integrity.clock_correction_candidate.v1"] = (
        "pipeline_integrity.clock_correction_candidate.v1"
    )
    status: Literal["computed", "needs_data", "out_of_scope"]
    issues: tuple[str, ...] = ()
    model: ClockCorrectionModel
    context: RunContext
    candidate_context: RunContext
    source_frames: tuple[SignalFrame, ...]
    candidate_frames: tuple[SignalFrame, ...]
    source_frames_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_frames_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_ids: tuple[Name, ...]
    source_times_s: tuple[float, ...]
    candidate_times_s: tuple[float, ...]
    observation_order_preserved: Literal[True] = True
    source_times_retained: Literal[True] = True
    clock_model_reviewed: bool
    residual_bound_s: Nonnegative
    synchronization_proven: Literal[False] = False
    causal_alignment_proven: Literal[False] = False
    field_qualification: Literal[False] = False
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = ClockCorrectionRecord.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("clock correction candidate receipt changed")
        if digest(checked.source_frames) != checked.source_frames_digest:
            raise ValueError("clock correction source frames changed")
        if digest(checked.candidate_frames) != checked.candidate_frames_digest:
            raise ValueError("clock correction candidate frames changed")
        expected_context = checked.context.model_copy(
            update={"clock_id": checked.model.reference_clock_id}
        )
        if checked.candidate_context != expected_context:
            raise ValueError("candidate context projection differs from the reviewed clock mapping")
        if len(checked.observation_ids) != len(checked.source_frames) or len(
            set(checked.observation_ids)
        ) != len(checked.observation_ids):
            raise ValueError("clock correction observation identities changed")
        if tuple(frame.time_s for frame in checked.source_frames) != checked.source_times_s:
            raise ValueError("clock correction source times changed")
        if tuple(frame.time_s for frame in checked.candidate_frames) != checked.candidate_times_s:
            raise ValueError("clock correction candidate times changed")
        return True


def _seal(body):
    return ClockCorrectionRecord(**body, record_digest=digest(body))


def apply_clock_correction_candidate(
    frames,
    model: ClockCorrectionModel,
    context: RunContext,
    *,
    observation_ids,
):
    """Create a candidate clock projection without mutating or reordering observations.

    A caller-reviewed model and evidence are required before candidate times are emitted.
    The original frames/times remain in the sealed receipt. No synchronization, causal
    ordering across streams, drift fit, or uncertainty propagation is inferred here.
    """
    model = ClockCorrectionModel.model_validate(model.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    source = tuple(SignalFrame.model_validate(item.model_dump(mode="python")) for item in frames)
    ids = tuple(observation_ids)
    if not source or len(ids) != len(source) or len(set(ids)) != len(ids):
        raise ValueError("each source frame requires one unique observation ID")
    if not model.caller_reviewed:
        raise ValueError("a clock correction model must be caller-reviewed before candidates are emitted")
    evidence_ids = {item.id for item in context.evidence}
    if not {model.model_evidence_id, model.review_evidence_id} <= evidence_ids:
        raise ValueError("clock model and caller-review evidence must be present in context")
    first = source[0]
    if first.clock_id != model.source_clock_id or context.clock_id != model.source_clock_id:
        raise ValueError("source frames, model and context must share the declared source clock")
    # A stream identifier names the observation channel, not the physical asset.
    # Asset identity is bound by the source RunContext and, for epoch joins, by
    # the epoch/request contexts. SignalFrame has no asset_id field to compare.
    if context.origin != first.origin:
        raise ValueError("source frame origin differs from the reviewed context")
    if any(
        frame.clock_id != first.clock_id
        or frame.stream_id != first.stream_id
        or frame.origin != first.origin
        for frame in source
    ):
        raise ValueError("source clock, stream and origin must remain consistent")
    source_times = tuple(frame.time_s for frame in source)
    if any(b <= a for a, b in zip(source_times, source_times[1:], strict=False)):
        raise ValueError("source observations must be strictly increasing; input is never sorted")
    if any(
        time < model.valid_source_start_s or time > model.valid_source_end_s for time in source_times
    ):
        raise ValueError("all source times must lie inside the reviewed clock-model validity interval")
    candidate_times = tuple(
        model.reference_epoch_s
        + (time - model.source_epoch_s - model.offset_s) / (1.0 + model.rate_error)
        for time in source_times
    )
    if any(not math.isfinite(time) or time < 0 for time in candidate_times):
        raise ValueError("corrected candidate times must be finite and nonnegative")
    if any(b <= a for a, b in zip(candidate_times, candidate_times[1:], strict=False)):
        raise ValueError("clock model does not preserve a strictly increasing candidate time axis")
    candidates = tuple(
        SignalFrame.model_validate(
            frame.model_dump(mode="python")
            | {"clock_id": model.reference_clock_id, "time_s": corrected}
        )
        for frame, corrected in zip(source, candidate_times, strict=True)
    )
    candidate_context = context.model_copy(update={"clock_id": model.reference_clock_id})
    body = dict(
        schema="pipeline_integrity.clock_correction_candidate.v1",
        status="computed",
        issues=(),
        model=model,
        context=context,
        candidate_context=candidate_context,
        source_frames=source,
        candidate_frames=candidates,
        source_frames_digest=digest(source),
        candidate_frames_digest=digest(candidates),
        observation_ids=ids,
        source_times_s=source_times,
        candidate_times_s=candidate_times,
        observation_order_preserved=True,
        source_times_retained=True,
        clock_model_reviewed=model.caller_reviewed,
        residual_bound_s=model.residual_bound_s,
        synchronization_proven=False,
        causal_alignment_proven=False,
        field_qualification=False,
    )
    return _seal(body)


def verify_clock_correction_candidate(record: ClockCorrectionRecord):
    """Recompute receipt digests and confirm no synchronization claim was added."""
    if not isinstance(record, ClockCorrectionRecord):
        raise TypeError("a ClockCorrectionRecord is required")
    record.check_integrity()
    if record.synchronization_proven or record.causal_alignment_proven or record.field_qualification:
        raise ValueError("clock correction candidates cannot assert synchronization or field qualification")
    return True
