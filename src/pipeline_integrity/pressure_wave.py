"""Negative-pressure edge features with explicit timing and topology meaning."""

import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field

from . import acoustics
from .acoustics import RelativeTiming, SampledTrace
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest


class PressureWaveRequest(Contract):
    id: Name
    left: SampledTrace
    right: SampledTrace
    pressure_reference: Literal["absolute", "gauge", "dynamic_perturbation"]
    minimum_negative_rate_pa_s: Positive
    minimum_rate_prominence_pa_s: Positive
    left_feature_to_arrival_error_s: Nonnegative | None = None
    right_feature_to_arrival_error_s: Nonnegative | None = None
    timing: RelativeTiming | None = None
    topology: Literal["through_pair", "source_between", "unresolved"] = "unresolved"
    source_event_id: Name
    evidence_id: Name
    feature_policy_reviewed: bool = False
    same_event_reviewed: bool = False
    topology_reviewed: bool = False
    source_pair_qualification: dict | None = None
    maximum_samples: int = Field(default=100000, ge=32, le=2000000)


def boundary_metadata(q):
    return {
        "request_sha256": digest(q),
        "id": q.id,
        "event_id": q.source_event_id,
        "sensors": [q.left.sensor_id, q.right.sensor_id],
        "trace_digests": [digest(q.left), digest(q.right)],
        "pressure_reference": q.pressure_reference,
        "topology": q.topology,
        "feature": "interior peak of negative first-difference pressure rate",
        "source_pair_qualification_digest": None
        if q.source_pair_qualification is None
        else digest(q.source_pair_qualification),
    }


def bind_pressure_wave_pair_request(
    trace_record,
    qualification,
    context,
    *,
    expected_trace_record_digest,
    expected_qualification_digest,
    request_id,
    left_sensor_id,
    right_sensor_id,
    sensor_role_evidence_id,
    source_event_id,
    evidence_id,
    minimum_negative_rate_pa_s,
    minimum_rate_prominence_pa_s,
    left_feature_to_arrival_error_s=None,
    right_feature_to_arrival_error_s=None,
    timing=None,
    topology="unresolved",
    feature_policy_reviewed=False,
    same_event_reviewed=False,
    topology_reviewed=False,
    maximum_samples=100000,
):
    """Bind source-qualified traces to left/right roles without inferring sensor geometry."""
    if (
        not isinstance(trace_record, dict)
        or not isinstance(expected_trace_record_digest, str)
        or trace_record.get("record_digest") != expected_trace_record_digest
        or expected_trace_record_digest
        != digest({key: value for key, value in trace_record.items() if key != "record_digest"})
    ):
        raise ValueError("pressure pair trace record differs from the caller-pinned boundary")
    if (
        not isinstance(qualification, dict)
        or not isinstance(expected_qualification_digest, str)
        or qualification.get("record_digest") != expected_qualification_digest
        or expected_qualification_digest
        != digest({key: value for key, value in qualification.items() if key != "record_digest"})
        or trace_record.get("qualification_digest") != expected_qualification_digest
    ):
        raise ValueError("pressure pair qualification differs from the caller-pinned boundary")
    if left_sensor_id == right_sensor_id or {left_sensor_id, right_sensor_id} != set(
        trace_record.get("traces", {})
    ):
        raise ValueError("explicit distinct left/right roles must cover the qualified sensor pair")
    traces = {
        sensor_id: SampledTrace.model_validate_json(json.dumps(payload))
        for sensor_id, payload in trace_record["traces"].items()
    }
    c = RunContext.model_validate(context.model_dump(mode="python"))
    evidence_ids = [item.id for item in c.evidence]
    if (
        len(evidence_ids) != len(set(evidence_ids))
        or sensor_role_evidence_id not in evidence_ids
        or evidence_id not in evidence_ids
    ):
        raise ValueError("sensor-role and analysis evidence must be unique actual context members")
    for trace in traces.values():
        if (trace.asset_id, trace.clock_id, trace.origin) != (c.asset_id, c.clock_id, c.origin):
            raise ValueError("qualified traces differ from the requested asset, clock or origin")
    if qualification.get("source_sha256_by_sensor") != {
        sensor_id: trace.source_digest for sensor_id, trace in traces.items()
    }:
        raise ValueError("qualification and trace-record source identities differ")
    q = PressureWaveRequest(
        id=request_id,
        left=traces[left_sensor_id],
        right=traces[right_sensor_id],
        pressure_reference=qualification["pressure_reference"],
        minimum_negative_rate_pa_s=minimum_negative_rate_pa_s,
        minimum_rate_prominence_pa_s=minimum_rate_prominence_pa_s,
        left_feature_to_arrival_error_s=left_feature_to_arrival_error_s,
        right_feature_to_arrival_error_s=right_feature_to_arrival_error_s,
        timing=timing,
        topology=topology,
        source_event_id=source_event_id,
        evidence_id=evidence_id,
        feature_policy_reviewed=feature_policy_reviewed,
        same_event_reviewed=same_event_reviewed,
        topology_reviewed=topology_reviewed,
        source_pair_qualification=qualification,
        maximum_samples=maximum_samples,
    )
    metadata = dict(c.metadata)
    metadata.update(
        pressure_wave_boundary=boundary_metadata(q),
        pressure_wave_sensor_roles={
            "left": left_sensor_id,
            "right": right_sensor_id,
            "evidence_id": sensor_role_evidence_id,
        },
        pressure_pair_trace_record_digest=expected_trace_record_digest,
    )
    return q, c.model_copy(update={"metadata": metadata})


def _source_pair_issue(q, evidence_ids):
    qualification = q.source_pair_qualification
    actual_source_pair = q.left.origin in {"laboratory", "measured"}
    if qualification is None:
        return (
            "measured/laboratory traces require a ready source-pair qualification"
            if actual_source_pair
            else None
        )
    if qualification.get(
        "schema"
    ) != "pipeline_integrity.pressure_pair_qualification.v1" or qualification.get("record_digest") != digest(
        {key: value for key, value in qualification.items() if key != "record_digest"}
    ):
        raise ValueError("source-pair qualification seal or schema changed")
    if qualification.get("pairing_evidence_id") not in evidence_ids:
        raise ValueError("source-pair qualification evidence is absent from the actual context")
    expected_sources = {
        q.left.sensor_id: q.left.source_digest,
        q.right.sensor_id: q.right.source_digest,
    }
    if (
        qualification.get("sensor_ids") != sorted(expected_sources)
        or qualification.get("source_sha256_by_sensor") != expected_sources
        or qualification.get("pressure_reference") != q.pressure_reference
    ):
        raise ValueError(
            "source-pair qualification differs from trace sensors, sources or pressure reference"
        )
    if qualification.get("status") != "ready_for_paired_analysis":
        return "source-pair qualification is not ready for paired analysis"
    if not qualification.get("common_trigger_verified") or not qualification.get("sample_alignment_verified"):
        raise ValueError("ready source-pair qualification contradicts its review flags")
    return None


def _feature(trace, q):
    import numpy as np
    from scipy.signal import find_peaks

    if not all(trace.valid) or any(v is None for v in trace.values):
        return {
            "status": "needs_data",
            "reason": "unknown/invalid samples retained; no interpolation",
            "candidates": [],
        }
    if q.pressure_reference == "absolute" and any(v <= 0 for v in trace.values):
        raise ValueError("nonpositive absolute pressure is outside this single pressure-reference trace")
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        rate = -np.diff(np.asarray(trace.values, dtype=float)) / trace.sample_period_s
    peaks, properties = find_peaks(
        rate,
        height=q.minimum_negative_rate_pa_s,
        prominence=q.minimum_rate_prominence_pa_s,
        plateau_size=(1, None),
    )
    candidates = []
    for j, i in enumerate(peaks):
        first = int(properties["left_edges"][j])
        last = int(properties["right_edges"][j]) + 1
        low = trace.start_time_s + first * trace.sample_period_s
        high = trace.start_time_s + last * trace.sample_period_s
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            raise ValueError("edge support cannot be represented on the source clock")
        candidates.append(
            {
                "left_sample_index": first,
                "right_sample_index": last,
                "peak_rate_index": int(i),
                "plateau_rate_samples": int(properties["plateau_sizes"][j]),
                "feature_support_s": [low, high],
                "negative_rate_pa_s": float(rate[i]),
                "prominence_pa_s": float(properties["prominences"][j]),
            }
        )
    truncated = bool(rate[0] >= q.minimum_negative_rate_pa_s or rate[-1] >= q.minimum_negative_rate_pa_s)
    return {
        "status": "selected" if len(candidates) == 1 and not truncated else "needs_data",
        "reason": "unique interior reviewed edge feature"
        if len(candidates) == 1 and not truncated
        else "missing, multiple, or endpoint-truncated features; no strongest-peak selection",
        "endpoint_truncated": truncated,
        "candidates": candidates,
    }


def evaluate_pressure_wave(request: PressureWaveRequest, context: RunContext):
    if not 32 <= request.maximum_samples <= 2000000 or any(
        not 32 <= len(t.values) <= request.maximum_samples or len(t.valid) != len(t.values)
        for t in (request.left, request.right)
    ):
        raise ValueError("each trace must fit its declared sample-work budget before cloning")
    q = PressureWaveRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    for t in (q.left, q.right):
        if (
            (t.asset_id, t.clock_id) != (c.asset_id, c.clock_id)
            or t.quantity_id != "pressure"
            or t.unit != "Pa"
        ):
            raise ValueError(
                "pressure traces need the actual context asset/clock and explicit Pa pressure quantity"
            )
        if len(t.values) > q.maximum_samples or len(t.values) < 32:
            raise ValueError("each trace must fit its declared sample-work budget")
    if q.left.sensor_id == q.right.sensor_id or q.left.id == q.right.id:
        raise ValueError("distinct pressure sensor records are required")
    if q.left.origin != q.right.origin or q.left.origin != c.origin:
        raise ValueError("paired source generation origin differs from the actual context")
    ids = [e.id for e in c.evidence]
    if len(set(ids)) != len(ids) or q.evidence_id not in ids:
        raise ValueError("pressure wave evidence must be a unique actual context member")
    if q.timing is not None and q.timing.source_id not in ids:
        raise ValueError("relative timing calibration evidence is not present in context")
    if c.metadata.get("pressure_wave_boundary") != boundary_metadata(q):
        raise ValueError("the pressure traces, event and topology are not bound to context")
    record = {
        "schema": "pipeline_integrity.pressure_wave.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "shared_trace_contract_sha256": file_digest(Path(acoustics.__file__)),
        "status": "needs_data",
        "native_called": False,
        "backend": None,
        "features": None,
        "feature_delay_bounds_s": None,
        "arrival_delay_bounds_s": None,
        "first_arrival_receiver": None,
        "conditional_travel_direction": None,
        "leak_detected": None,
        "leak_probability": None,
        "position_m": None,
        "field_qualified": False,
        "issues": [],
    }

    def seal():
        record["record_digest"] = digest(record)
        return record

    pair_issue = _source_pair_issue(q, ids)
    if pair_issue is not None:
        record["issues"] = [pair_issue]
        return seal()

    if not q.feature_policy_reviewed:
        record["issues"] = ["negative-edge policy requires review; no native feature search"]
        return seal()
    try:
        record.update(
            native_called=True,
            backend={name: version(name) for name in ("numpy", "scipy", "pydantic", "pydantic-core")},
        )
        left, right = _feature(q.left, q), _feature(q.right, q)
        record["features"] = {"left": left, "right": right}
        if any(r["status"] != "selected" for r in (left, right)):
            record["issues"] = ["no unique pair of source-supported negative-edge features"]
            return seal()
        ll, lh = left["candidates"][0]["feature_support_s"]
        rl, rh = right["candidates"][0]["feature_support_s"]
        feature = (rl - lh, rh - ll)
        if not all(math.isfinite(v) for v in feature):
            raise ValueError("feature separation is not representable")
        record["feature_delay_bounds_s"] = list(feature)
        if (
            q.timing is None
            or q.left_feature_to_arrival_error_s is None
            or q.right_feature_to_arrival_error_s is None
            or not q.same_event_reviewed
        ):
            record["issues"] = [
                "features retained; relative timing, feature-to-arrival bounds and same-event evidence required"
            ]
            return seal()
        error = (
            q.timing.absolute_error_bound_s
            + q.left_feature_to_arrival_error_s
            + q.right_feature_to_arrival_error_s
        )
        bounds = [
            feature[0] - q.timing.right_minus_left_latency_s - error,
            feature[1] - q.timing.right_minus_left_latency_s + error,
        ]
        if not all(math.isfinite(v) for v in bounds):
            raise ValueError("arrival uncertainty bounds are not representable")
        record["arrival_delay_bounds_s"] = bounds
        first = "left" if bounds[0] > 0 else "right" if bounds[1] < 0 else "indeterminate"
        record["first_arrival_receiver"] = first
        if q.topology == "through_pair" and q.topology_reviewed and first != "indeterminate":
            record["conditional_travel_direction"] = "left_to_right" if first == "left" else "right_to_left"
        record["status"] = "computed_diagnostic"
        record["issues"] = [
            "arrival bounds are supplied-model support intervals, not a confidence interval or leak-cause diagnosis"
        ]
    except (ArithmeticError, ValueError) as exc:
        record.update(
            status="out_of_scope",
            issues=[str(exc)],
            arrival_delay_bounds_s=None,
            first_arrival_receiver=None,
            conditional_travel_direction=None,
        )
    return seal()


def verify_pressure_wave_record(record, *, request=None, context=None):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("pressure-wave record seal changed")
    q = PressureWaveRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if (request is not None and q != request) or (context is not None and c != context):
        raise ValueError("pressure-wave record differs from the caller boundary")
    if evaluate_pressure_wave(q, c) != record:
        raise ValueError("pressure-wave features, timing, topology or qualification differ on replay")
    return True
