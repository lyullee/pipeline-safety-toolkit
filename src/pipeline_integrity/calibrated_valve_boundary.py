"""Bind calibrated conductance observations to the existing liquid-valve transient API."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from .calibration_application import verify_calibration_application
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest
from .transient import History, LiquidValve


class CalibratedValveBoundary(Contract):
    schema_version: Literal["pipeline_integrity.calibrated_valve_boundary.v1"] = (
        "pipeline_integrity.calibrated_valve_boundary.v1"
    )
    valve: LiquidValve
    calibration_application_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_frames_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_ids: tuple[Name, ...]
    source_stream_id: Name
    calibrated_channel: Name
    instrument_asset_id: Name
    valve_component_id: Name
    scenario_id: Name
    clock_id: Name
    source_origin: str
    calibration_unit: Literal["m2"]
    source_evidence_id: Name
    application_evidence_id: Name
    mapping_evidence_id: Name
    network_context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_duration_s: Positive
    uncertainty_propagated: Literal[False] = False
    clock_transformed: Literal[False] = False
    transient_solver_executed: Literal[False] = False
    field_qualification: Literal[False] = False
    semantic_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = CalibratedValveBoundary.model_validate_json(self.model_dump_json())
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != checked.result_digest:
            raise ValueError("calibrated valve boundary receipt changed")
        if checked.valve.binding_digest != digest(
            checked.valve.model_dump(mode="json", exclude={"binding_digest"})
        ):
            raise ValueError("bound liquid valve digest changed")
        evidence = checked.valve.evidence
        if evidence.get("calibrated_boundary_digest") != checked.semantic_digest:
            raise ValueError("liquid valve no longer refers to this calibrated boundary")
        semantic = checked.model_dump(mode="json", exclude={"valve", "semantic_digest", "result_digest"})
        if digest(semantic) != checked.semantic_digest:
            raise ValueError("calibrated valve semantic binding changed")
        if digest(checked.valve.effective_area_m2) != evidence.get("history_digest"):
            raise ValueError("liquid-valve conductance history changed")
        return True


def bind_calibrated_valve_boundary(
    application_record: dict,
    source_frames,
    observation_ids,
    *,
    calibrated_channel: str,
    source_stream_id: str,
    valve_component_id: str,
    start_node: str,
    end_node: str,
    initial_volume_flow_m3_s: float,
    required_duration_s: float,
    parameter_source_id: str,
    network_context: RunContext,
    source_evidence_id: str,
    application_evidence_id: str,
    mapping_evidence_id: str,
):
    """Create an existing ``LiquidValve`` from a complete calibrated effective-CdA trace.

    Calibration remains owned by ``calibration_application``. The caller must map the
    instrument channel to effective CdA in square metres and provide a reviewed crosswalk
    in the calibration and network contexts. Invalid rows, unit conversion, clock changes,
    and extrapolation are not silently repaired.
    """
    if not isinstance(application_record, dict):
        raise TypeError("a calibration application receipt is required")
    receipt = json.loads(json.dumps(application_record, allow_nan=False))
    verified = verify_calibration_application(receipt)
    if not verified.get("valid") or receipt.get("status") != "computed":
        raise ValueError("a structurally valid computed calibration application is required")
    context = RunContext.model_validate_json(json.dumps(receipt["context"]))
    network = RunContext.model_validate_json(network_context.model_dump_json())
    application_evidence = {item.id for item in context.evidence}
    network_evidence = {item.id for item in network.evidence}
    if not {source_evidence_id, application_evidence_id, mapping_evidence_id} <= application_evidence:
        raise ValueError("source, calibration application and channel mapping evidence must bind the application")
    if mapping_evidence_id not in network_evidence:
        raise ValueError("the network context must contain the reviewed instrument-to-valve mapping")
    if (context.scenario_id, context.clock_id, context.origin) != (
        network.scenario_id, network.clock_id, network.origin
    ):
        raise ValueError("calibration and network require the same scenario, physical clock and observation origin")
    frames = tuple(SignalFrame.model_validate_json(frame.model_dump_json()) for frame in source_frames)
    ids = tuple(observation_ids)
    if not frames or len(ids) != len(frames) or len(set(ids)) != len(ids):
        raise ValueError("every conductance observation requires one unique row identity")
    if start_node == end_node:
        raise ValueError("a liquid valve requires distinct network nodes")
    first = frames[0]
    if first.time_s != 0.0 or first.stream_id != source_stream_id:
        raise ValueError("source trace must identify the declared stream and start at network time zero")
    for frame in frames:
        if (
            frame.stream_id != source_stream_id or frame.clock_id != context.clock_id
            or frame.origin != context.origin or calibrated_channel not in frame.values
        ):
            raise ValueError("source stream, clock, origin or calibrated channel differs")
        value = frame.values[calibrated_channel]
        if not frame.valid[calibrated_channel] or value is None or value < 0:
            raise ValueError("valve conductance requires complete valid nonnegative effective-CdA observations")
    if any(b.time_s <= a.time_s for a, b in zip(frames, frames[1:], strict=False)):
        raise ValueError("source observations must remain in strictly increasing order")
    if frames[-1].time_s < required_duration_s:
        raise ValueError("calibrated conductance history must cover the liquid transient horizon")
    x_values = receipt.get("input_values")
    prediction = receipt.get("prediction", {})
    areas = prediction.get("y")
    if (
        prediction.get("x") != x_values or prediction.get("x_unit") != first.units[calibrated_channel]
        or prediction.get("y_unit") != "m2"
        or prediction.get("uncertainty") is not None or receipt.get("application_uncertainty") is not None
        or not isinstance(x_values, list) or not isinstance(areas, list) or len(areas) != len(x_values)
    ):
        raise ValueError(
            "calibration must provide a complete effective-CdA series in m2 without uncertainty output"
        )
    observed = [frame.values[calibrated_channel] for frame in frames]
    if observed != x_values or len(areas) != len(frames):
        raise ValueError("calibration inputs must cover all source observations exactly once in original order")
    if any(value < 0 for value in areas):
        raise ValueError("calibrated effective conductance cannot be negative")
    history = History(time_s=tuple(frame.time_s for frame in frames), values=tuple(areas))
    body = {
        "schema_version": "pipeline_integrity.calibrated_valve_boundary.v1",
        "calibration_application_digest": receipt["record_digest"],
        "source_frames_digest": digest(frames),
        "observation_ids": ids,
        "source_stream_id": source_stream_id,
        "calibrated_channel": calibrated_channel,
        "instrument_asset_id": context.asset_id,
        "valve_component_id": valve_component_id,
        "scenario_id": context.scenario_id,
        "clock_id": context.clock_id,
        "source_origin": context.origin,
        "calibration_unit": "m2",
        "source_evidence_id": source_evidence_id,
        "application_evidence_id": application_evidence_id,
        "mapping_evidence_id": mapping_evidence_id,
        "network_context_digest": digest(network),
        "required_duration_s": required_duration_s,
        "uncertainty_propagated": False,
        "clock_transformed": False,
        "transient_solver_executed": False,
        "field_qualification": False,
    }
    semantic_digest = digest(body)
    valve_evidence = {
        "kind": "calibrated_valve_boundary",
        "calibration_application_digest": receipt["record_digest"],
        "source_frames_digest": digest(frames),
        "history_digest": digest(history),
        "network_context": network.model_dump(mode="json"),
        "channel_mapping_evidence_id": mapping_evidence_id,
        "source_evidence_id": source_evidence_id,
        "calibrated_boundary_digest": semantic_digest,
    }
    valve = LiquidValve(
        id=valve_component_id,
        start_node=start_node,
        end_node=end_node,
        effective_area_m2=history,
        initial_volume_flow_m3_s=initial_volume_flow_m3_s,
        parameter_source_id=parameter_source_id,
        evidence=valve_evidence,
    )
    record_body = body | {
        "semantic_digest": semantic_digest,
        "valve": valve.model_dump(mode="json"),
    }
    return CalibratedValveBoundary.model_validate_json(
        json.dumps(record_body | {"result_digest": digest(record_body)})
    )
