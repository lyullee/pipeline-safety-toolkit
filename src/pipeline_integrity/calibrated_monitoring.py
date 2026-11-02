"""Bind verified calibration applications to the existing monitoring split contract."""

from __future__ import annotations

import json

from .calibration_application import verify_calibration_application
from .contracts import Contract, Name, SignalFrame, digest
from .monitoring_exchange import MonitoringSplit


class CalibratedMonitoringInput(Contract):
    split: MonitoringSplit
    calibration_application_digest: str
    source_frames_digest: str
    output_frames_digest: str
    observation_ids: tuple[Name, ...]
    calibrated_observation_ids: tuple[Name, ...]
    calibrated_channel: Name
    source_stream_id: Name
    asset_id: Name
    scenario_id: Name
    clock_id: Name
    source_origin: str
    source_unit: Name
    calibrated_unit: Name
    application_evidence_id: Name
    mapping_evidence_id: Name
    uncertainty_propagated: bool = False
    clock_transformed: bool = False
    field_qualification: bool = False
    record_digest: str


def build_calibrated_monitoring_input(
    application_record: dict,
    frames,
    observation_ids,
    *,
    calibrated_channel: str,
    source_stream_id: str,
    role: str,
    split_id: str,
    physical_run_id: str,
    source_group_id: str,
    source_evidence_id: str,
    application_evidence_id: str,
    mapping_evidence_id: str,
    reference_kind: str = "unresolved",
):
    """Apply a sealed calibration while retaining each original row and missing state.

    The application must cover every valid source observation, in source order. A changed
    clock, unsupported unit, extrapolation or partial-row calibration is refused. Uncertainty
    is not propagated by this adapter.
    """
    if not isinstance(application_record, dict):
        raise TypeError("a calibration application receipt is required")
    receipt = json.loads(json.dumps(application_record, allow_nan=False))
    verified = verify_calibration_application(receipt)
    if not verified.get("valid"):
        raise ValueError(verified.get("reason", "invalid calibration application receipt"))
    if receipt.get("status") != "computed":
        raise ValueError("a structurally valid computed calibration application is required")
    application_digest = receipt["record_digest"]
    context = receipt.get("context", {})
    evidence_ids = {row.get("id") for row in context.get("evidence", [])}
    if not {application_evidence_id, mapping_evidence_id, source_evidence_id} <= evidence_ids:
        raise ValueError("application, source and mapping evidence must be present in the calibrated context")
    prediction = receipt.get("prediction", {})
    x_values = receipt.get("input_values")
    predicted_x, predicted_y = prediction.get("x"), prediction.get("y")
    source_unit, calibrated_unit = prediction.get("x_unit"), prediction.get("y_unit")
    if (
        not isinstance(x_values, list)
        or predicted_x != x_values
        or not isinstance(predicted_y, list)
        or len(predicted_y) != len(x_values)
        or not isinstance(source_unit, str)
        or not isinstance(calibrated_unit, str)
        or prediction.get("calibration_digest") != receipt.get("calibration_record_digest")
        or prediction.get("uncertainty") is not None
        or receipt.get("application_uncertainty") is not None
    ):
        raise ValueError("calibration application values, units or source digest are incomplete")
    source_frames = tuple(SignalFrame.model_validate_json(frame.model_dump_json()) for frame in frames)
    ids = tuple(observation_ids)
    if not source_frames or len(ids) != len(source_frames) or len(set(ids)) != len(ids):
        raise ValueError("all source rows need aligned, unique observation identities")
    first = source_frames[0]
    if (
        first.stream_id != source_stream_id
        or first.clock_id != context.get("clock_id")
        or first.origin != context.get("origin")
    ):
        raise ValueError(
            "source frames and calibration application require the same explicit clock and origin"
        )
    for frame in source_frames:
        if (
            frame.stream_id != first.stream_id
            or frame.clock_id != first.clock_id
            or frame.origin != first.origin
            or calibrated_channel not in frame.values
            or frame.units[calibrated_channel] != source_unit
        ):
            raise ValueError("source stream, clock, origin, channel or calibration input unit differs")
    if any(b.time_s <= a.time_s for a, b in zip(source_frames, source_frames[1:], strict=False)):
        raise ValueError("source observations must remain in strictly increasing original time order")
    valid_indices = tuple(
        i for i, frame in enumerate(source_frames)
        if frame.valid[calibrated_channel] and frame.values[calibrated_channel] is not None
    )
    calibrated_ids = tuple(ids[i] for i in valid_indices)
    valid_x = [source_frames[i].values[calibrated_channel] for i in valid_indices]
    if len(valid_x) != len(x_values) or valid_x != x_values:
        raise ValueError("calibration must cover every valid source row exactly once and in order")
    y_by_index = dict(zip(valid_indices, predicted_y, strict=True))
    calibrated_frames = []
    for i, frame in enumerate(source_frames):
        values = dict(frame.values)
        if i in y_by_index:
            values[calibrated_channel] = y_by_index[i]
        calibrated_frames.append(
            SignalFrame.model_validate(
                frame.model_dump(mode="python") | {
                    "values": values,
                    "units": dict(frame.units) | {calibrated_channel: calibrated_unit},
                }
            )
        )
    split = MonitoringSplit(
        role=role,
        split_id=split_id,
        physical_run_id=physical_run_id,
        source_group_id=source_group_id,
        source_evidence_id=source_evidence_id,
        observation_ids=ids,
        frames=tuple(calibrated_frames),
        reference_kind=reference_kind,
    )
    body = {
        "split": split.model_dump(mode="json"),
        "calibration_application_digest": application_digest,
        "source_frames_digest": digest(source_frames),
        "output_frames_digest": digest(calibrated_frames),
        "observation_ids": ids,
        "calibrated_observation_ids": calibrated_ids,
        "calibrated_channel": calibrated_channel,
        "source_stream_id": source_stream_id,
        "asset_id": context["asset_id"],
        "scenario_id": context["scenario_id"],
        "clock_id": context["clock_id"],
        "source_origin": context["origin"],
        "source_unit": source_unit,
        "calibrated_unit": calibrated_unit,
        "application_evidence_id": application_evidence_id,
        "mapping_evidence_id": mapping_evidence_id,
        "uncertainty_propagated": False,
        "clock_transformed": False,
        "field_qualification": False,
    }
    return CalibratedMonitoringInput.model_validate_json(json.dumps(body | {"record_digest": digest(body)}))


def verify_calibrated_monitoring_input(record: CalibratedMonitoringInput):
    checked = CalibratedMonitoringInput.model_validate_json(record.model_dump_json())
    body = checked.model_dump(mode="json", exclude={"record_digest"})
    if digest(body) != checked.record_digest:
        raise ValueError("calibrated monitoring receipt changed")
    if digest(checked.split.frames) != checked.output_frames_digest:
        raise ValueError("calibrated monitoring frames changed after receipt creation")
    return True
