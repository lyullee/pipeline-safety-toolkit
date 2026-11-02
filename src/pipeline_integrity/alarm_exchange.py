"""Bind qualified score observations to the monitoring owner's alarm policy.

The owner retains threshold, persistence, recovery and latch mathematics.  This
adapter checks the observation boundary and records observed activations; it
does not turn a monitoring score into a probability or human-error estimate.
"""

from __future__ import annotations

import importlib
import json
import math
from numbers import Real
from pathlib import Path
from typing import Literal

from pydantic import Field, PrivateAttr, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest

_SHA256 = r"^[a-f0-9]{64}$"


class AlarmReplaySpec(Contract):
    alarm_id: Name
    source_stream_id: Name
    score_channel: Name
    score_unit: Name
    trigger: float
    reset: float
    persistence_s: Nonnegative = 0.0
    recovery_s: Nonnegative = 0.0
    max_gap_s: Positive
    latch: bool = False
    policy_reviewed: bool = False
    policy_evidence_id: Name
    source_evidence_id: Name
    reset_requested_time_s: tuple[Nonnegative, ...] = ()
    reset_evidence_id: Name | None = None
    maximum_samples: int = Field(default=100_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def ordered(self):
        if self.reset > self.trigger:
            raise ValueError("the alarm reset threshold cannot exceed its trigger")
        if any(
            b <= a for a, b in zip(self.reset_requested_time_s, self.reset_requested_time_s[1:], strict=False)
        ):
            raise ValueError("reset request timestamps must be unique and strictly increasing")
        if bool(self.reset_requested_time_s) != (self.reset_evidence_id is not None):
            raise ValueError("explicit reset requests and their evidence must be supplied together")
        return self


class AlarmReplayResult(Contract):
    schema_version: Literal["pipeline_integrity.alarm_replay.v1"] = "pipeline_integrity.alarm_replay.v1"
    context: RunContext
    specification: AlarmReplaySpec
    records: tuple[dict, ...]
    activation_records: tuple[dict, ...]
    source_frames_digest: str = Field(pattern=_SHA256)
    provider: dict | None
    status: Literal["exchange_checked", "needs_data"]
    issues: tuple[str, ...]
    score_is_probability: Literal[False] = False
    field_qualified: Literal[False] = False
    activation_time_basis: Literal["recorded_first_valid_alarm_transition"] = (
        "recorded_first_valid_alarm_transition"
    )
    true_onset_estimated: Literal[False] = False
    result_digest: str = Field(pattern=_SHA256)
    _source_frames: tuple[SignalFrame, ...] = PrivateAttr(default=())
    _sealed_result_digest: str = PrivateAttr(default="")

    def check_integrity(self):
        """Check the local source binding as well as the exportable receipt.

        Public JSON contains a source digest, not raw observations.  Rebuilding
        a locally verifiable result from that JSON requires its source frames.
        """
        if not self._source_frames or not self._sealed_result_digest:
            raise ValueError("alarm replay has no local source-integrity binding")
        body = self.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest or self.result_digest != self._sealed_result_digest:
            raise ValueError("the alarm result, policy, context or provider changed after replay")
        if digest(self._source_frames) != self.source_frames_digest:
            raise ValueError("the alarm replay's bound source frames changed")


def _inputs(frames, specification, context):
    if not isinstance(specification, AlarmReplaySpec) or not isinstance(context, RunContext):
        raise TypeError("alarm replay requires AlarmReplaySpec and RunContext")
    # JSON revalidation also checks cloned Pydantic objects whose updates bypassed validation.
    specification = AlarmReplaySpec.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    evidence_ids = {row.id for row in context.evidence}
    required = {specification.policy_evidence_id, specification.source_evidence_id}
    if specification.reset_evidence_id is not None:
        required.add(specification.reset_evidence_id)
    if len(evidence_ids) != len(context.evidence) or required - evidence_ids:
        raise ValueError("alarm source, policy and reset evidence must occur uniquely in RunContext")
    snapshots = []
    for index, source in enumerate(frames):
        if index >= specification.maximum_samples:
            raise ValueError("alarm observations exceed maximum_samples; no downsampling was performed")
        if not isinstance(source, SignalFrame):
            raise TypeError("alarm observations must be SignalFrame instances")
        frame = SignalFrame.model_validate_json(source.model_dump_json())
        if frame.stream_id != specification.source_stream_id or frame.clock_id != context.clock_id:
            raise ValueError("alarm source stream or clock differs from its declared context")
        if frame.origin != context.origin:
            raise ValueError("alarm observation origin differs from its declared context")
        if specification.score_channel not in frame.values:
            raise ValueError("alarm score channel is absent from a source observation")
        if frame.units[specification.score_channel] != specification.score_unit:
            raise ValueError("alarm score unit differs from the reviewed policy unit")
        if snapshots and frame.time_s <= snapshots[-1].time_s:
            raise ValueError("alarm observations must strictly increase without clock shifts or sorting")
        snapshots.append(frame)
    if not snapshots:
        raise ValueError("alarm replay requires at least one source observation")
    times = {frame.time_s for frame in snapshots}
    if set(specification.reset_requested_time_s) - times:
        raise ValueError("reset requests must exactly match recorded source timestamps")
    return tuple(snapshots), specification, context


def _provider_identity(provider):
    if (
        not isinstance(provider, dict)
        or not isinstance(provider.get("id"), str)
        or not provider["id"].strip()
    ):
        raise ValueError("an injected alarm factory requires an explicit provider identity")
    version = provider.get("version")
    source = provider.get("source_sha256")
    has_version = isinstance(version, str) and bool(version.strip())
    has_source = (
        isinstance(source, str) and len(source) == 64 and all(c in "0123456789abcdef" for c in source)
    )
    if not has_version and not has_source:
        raise ValueError("alarm provider identity requires source SHA-256 or version evidence")
    return json.loads(json.dumps(json_value(provider), allow_nan=False))


def _new_policy(specification, *, factory, provider):
    if factory is None:
        if provider is not None:
            raise ValueError("explicit alarm provider identity belongs to an injected factory")
        owner = importlib.import_module("process_monitoring")
        native = importlib.import_module("process_monitoring.events")
        factory = native.AlarmPolicy
        provider = {
            "id": "process_monitoring.events.AlarmPolicy",
            "version": getattr(owner, "__version__", None),
            "source_sha256": file_digest(Path(native.__file__)),
        }
    if not callable(factory):
        raise TypeError("alarm factory must create a fresh owner-compatible policy")
    provider = _provider_identity(provider)
    parameters = {
        name: getattr(specification, name)
        for name in ("trigger", "reset", "persistence_s", "recovery_s", "max_gap_s", "latch")
    }
    policy = factory(**parameters)
    if not callable(getattr(policy, "update", None)) or any(
        getattr(policy, name, None) != value for name, value in parameters.items()
    ):
        raise ValueError("alarm provider changed the reviewed policy parameters or update API")
    if (
        getattr(policy, "last", "missing") is not None
        or getattr(policy, "since", "missing") is not None
        or getattr(policy, "clear_since", "missing") is not None
        or getattr(policy, "active", None) is not False
    ):
        raise ValueError("alarm replay requires a fresh inactive owner policy")
    return policy, provider


def _receipt(frames, specification, context, *, records, activations, provider, status, issues):
    provisional = AlarmReplayResult(
        context=context,
        specification=specification,
        records=tuple(records),
        activation_records=tuple(activations),
        source_frames_digest=digest(frames),
        provider=provider,
        status=status,
        issues=tuple(issues),
        result_digest="0" * 64,
    )
    sealed = digest(provisional.model_dump(mode="json", exclude={"result_digest"}))
    result = provisional.model_copy(update={"result_digest": sealed}, deep=True)
    result._source_frames = frames
    result._sealed_result_digest = sealed
    result.check_integrity()
    return result


def replay_alarm(frames, specification: AlarmReplaySpec, context: RunContext, *, factory=None, provider=None):
    """Replay a reviewed policy using the owner's state machine and native clocks.

    ``factory`` accepts the six AlarmPolicy constructor keywords.  Custom
    factories require provider identity and source/version evidence.  Missing
    values are passed as invalid input, preserving the owner's active memory.
    """
    frames, specification, context = _inputs(frames, specification, context)
    if not specification.policy_reviewed:
        return _receipt(
            frames,
            specification,
            context,
            records=(),
            activations=(),
            provider=None,
            status="needs_data",
            issues=("alarm_policy_not_reviewed",),
        )
    policy, provider = _new_policy(specification, factory=factory, provider=provider)
    records, activations, issues = [], [], []
    previous_time = None
    resets = set(specification.reset_requested_time_s)
    for index, frame in enumerate(frames):
        active_before = policy.active
        gap = previous_time is not None and frame.time_s - previous_time > specification.max_gap_s
        value = frame.values[specification.score_channel]
        valid = frame.valid[specification.score_channel] and value is not None
        reset_requested = frame.time_s in resets
        native = policy.update(
            frame.time_s,
            value if valid else float("nan"),
            valid=valid,
            reset_requested=reset_requested,
        )
        if not isinstance(native, dict) or set(native) != {"time_s", "state", "active"}:
            raise ValueError("alarm provider returned a malformed observation record")
        time, state, active = (native[name] for name in ("time_s", "state", "active"))
        if (
            isinstance(time, bool)
            or not isinstance(time, Real)
            or not math.isfinite(time)
            or time != frame.time_s
            or not isinstance(state, str)
            or state not in {"UNKNOWN", "NORMAL", "PENDING", "ALARM"}
            or type(active) is not bool
            or policy.last != frame.time_s
            or policy.active != active
            or (not valid and (state != "UNKNOWN" or active != active_before))
            or (valid and (state == "UNKNOWN" or active != (state == "ALARM")))
        ):
            raise ValueError("alarm provider changed time, validity or active-state semantics")
        record = {
            "time_s": frame.time_s,
            "state": state,
            "active": active,
            "valid": valid,
            "gap_before": gap,
            "reset_requested": reset_requested,
            "source_index": index,
            "source_frame_digest": digest(frame),
        }
        records.append(record)
        if valid and state == "ALARM" and not active_before:
            activation_id = digest(
                {
                    "specification": specification,
                    "context": context,
                    "source_frame_digest": record["source_frame_digest"],
                }
            )
            activations.append({"id": f"{specification.alarm_id}:{activation_id}", "time_s": frame.time_s})
        if not valid and "missing_or_invalid_score_observations" not in issues:
            issues.append("missing_or_invalid_score_observations")
        if gap and "observation_gap_exceeded" not in issues:
            issues.append("observation_gap_exceeded")
        previous_time = frame.time_s
    return _receipt(
        frames,
        specification,
        context,
        records=records,
        activations=activations,
        provider=provider,
        status="exchange_checked",
        issues=issues,
    )
