"""Deterministic fault scenarios around the owner's transmitter, retaining true and reported clocks."""

import json
import math
from dataclasses import asdict
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .control_exchange import ControlAxis, ControlObservation, _evidence, native_identity
from .monitoring_exchange import MonitoringSplit


class FaultWindow(Contract):
    start_s: Nonnegative
    end_s: Positive

    @model_validator(mode="after")
    def ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("fault windows have positive duration and use [start, end)")
        return self


class TransmitterScenario(Contract):
    instrument_id: Name
    source_stream_id: Name
    channel: Name
    axis: ControlAxis
    lower_range: float
    upper_range: float
    initial_filtered_value: float
    time_constant_s: Nonnegative = 0.0
    scale_factor: Positive = 1.0
    bias: float = 0.0
    bias_drift_per_s: float = 0.0
    resolution: Nonnegative = 0.0
    accuracy_fraction_of_span: Nonnegative = 0.0
    delivery_delay_s: Nonnegative = 0.0
    reported_clock_offset_s: float = 0.0
    reported_clock_rate_error: float = Field(default=0.0, gt=-1)
    reported_clock_id: Name | None = None
    dropouts: tuple[FaultWindow, ...] = ()
    unknown_input_policy: Literal["withhold", "reset_on_next_valid"] = "withhold"
    parameter_evidence_id: Name
    source_evidence_id: Name

    @model_validator(mode="after")
    def limits(self):
        if self.upper_range <= self.lower_range:
            raise ValueError("transmitter range must increase")
        return self


def replay_transmitter(
    specification: TransmitterScenario, frames, context: RunContext, *, native_module=None
):
    s = TransmitterScenario.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    frames = tuple(SignalFrame.model_validate(f.model_dump()) for f in frames)
    if not frames or context.asset_id != s.instrument_id:
        raise ValueError("provide source frames and the instrument's context")
    clock_fault = s.reported_clock_offset_s != 0 or s.reported_clock_rate_error != 0
    if clock_fault and (s.reported_clock_id is None or s.reported_clock_id == context.clock_id):
        raise ValueError("a clock fault needs a distinct, explicitly named reported clock")
    reported_clock_id = s.reported_clock_id or context.clock_id
    _evidence(context, s.parameter_evidence_id, s.source_evidence_id)
    for f in frames:
        if f.clock_id != context.clock_id or f.stream_id != s.source_stream_id:
            raise ValueError("source stream or physical scenario clock differs")
        if s.channel not in f.values or f.units[s.channel] != s.axis.unit:
            raise ValueError("source channel or canonical unit differs")
    if any(b.time_s <= a.time_s for a, b in zip(frames, frames[1:], strict=False)):
        raise ValueError("source clocks must strictly increase without sorting or deduplication")
    if native_module is None:
        from lh2dt import instrumentation as native_module
    parameters = {
        k: getattr(s, k)
        for k in (
            "lower_range",
            "upper_range",
            "time_constant_s",
            "scale_factor",
            "resolution",
            "accuracy_fraction_of_span",
        )
    }
    transmitter = native_module.FirstOrderTransmitter(
        native_module.TransmitterParameters(**parameters, bias=s.bias)
    )
    provider = native_identity(type(transmitter))
    state = transmitter.initialize(s.initial_filtered_value)
    previous = None
    unknown = False
    packets = []
    start = frames[0].time_s
    for f in frames:
        t = f.time_s
        raw_valid = f.valid[s.channel] and f.values[s.channel] is not None
        reset = False
        if previous is not None and not unknown:
            # The left source value is held only over its following interval; no future sample drives the lag.
            state, _ = transmitter.advance(state, previous.values[s.channel], t - previous.time_s)
        if not raw_valid:
            unknown = True
        elif s.time_constant_s == 0 or (unknown and s.unknown_input_policy == "reset_on_next_valid"):
            state = transmitter.initialize(f.values[s.channel])
            reset = unknown and s.time_constant_s > 0
            unknown = False
        transmitter = native_module.FirstOrderTransmitter(
            native_module.TransmitterParameters(**parameters, bias=s.bias + s.bias_drift_per_s * (t - start))
        )
        reading = None if unknown else asdict(transmitter.read(state))
        reported = start + (t - start) * (1 + s.reported_clock_rate_error) + s.reported_clock_offset_s
        dropout = any(w.start_s <= t < w.end_s for w in s.dropouts)
        value = None if reading is None else reading["indicated_value"]
        output = (
            None
            if reported < 0
            else SignalFrame(
                stream_id=s.instrument_id,
                clock_id=reported_clock_id,
                time_s=reported,
                values={s.channel: value},
                units={s.channel: s.axis.unit},
                valid={s.channel: value is not None and not dropout},
                origin="simulation",
            )
        )
        packets.append(
            dict(
                source_frame_digest=digest(f),
                physical_source_time_s=t,
                source_clock_id=context.clock_id,
                reported_clock_id=reported_clock_id,
                reported_time_s=reported,
                available_at_s=t + s.delivery_delay_s,
                frame=None if output is None else output.model_dump(mode="json"),
                reading=reading,
                dropout=dropout,
                input_unknown=unknown,
                reset_on_valid_input=reset,
                source_origin=f.origin,
                axis=s.axis.model_dump(mode="json"),
            )
        )
        packets[-1]["packet_digest"] = digest(packets[-1])
        previous = f
    body = dict(
        schema="pipeline_integrity.transmitter_replay.v1",
        specification=s.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        source_clock_id=context.clock_id,
        reported_clock_id=reported_clock_id,
        provider=provider,
        packets=packets,
        input_hold="left_sample_zero_order_hold",
        output_origin="simulation",
        drift_and_timing_are_declared_fault_scenarios=True,
        field_calibrated=False,
    )
    body["record_digest"] = digest(body)
    return body


class TransmitterMonitoringInput(Contract):
    schema_version: Literal["pipeline_integrity.transmitter_monitoring_input.v1"] = (
        "pipeline_integrity.transmitter_monitoring_input.v1"
    )
    split: MonitoringSplit
    transmitter_replay_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_clock_id: Name
    reported_clock_id: Name
    first_packet_index: int = Field(ge=0)
    stop_packet_index: int = Field(ge=1)
    preserved_invalid_samples: int = Field(ge=0)
    all_selected_packets_preserved: Literal[True] = True
    delivery_delay_s: Literal[0.0] = 0.0
    output_origin: Literal["simulation"] = "simulation"
    monitoring_probability_or_field_claim: Literal[False] = False
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = TransmitterMonitoringInput.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("transmitter-to-monitoring input receipt changed")

    @model_validator(mode="after")
    def aligned_split(self):
        if self.stop_packet_index - self.first_packet_index != len(self.split.frames):
            raise ValueError("monitoring split length differs from the selected transmitter packets")
        if self.source_clock_id != self.reported_clock_id:
            raise ValueError("current monitoring split cannot represent a separate reported clock")
        if any(frame.clock_id != self.reported_clock_id for frame in self.split.frames):
            raise ValueError("monitoring frame clock differs from the reported clock receipt")
        return self


def transmitter_monitoring_input(
    replay,
    *,
    role,
    split_id,
    physical_run_id,
    source_group_id,
    source_evidence_id,
    reference_kind,
    first_packet_index=0,
    stop_packet_index=None,
):
    """Preserve an undelayed identity-clock transmitter slice for the existing PMT contract."""
    if not isinstance(replay, dict) or replay.get("schema") != "pipeline_integrity.transmitter_replay.v1":
        raise ValueError("a sealed transmitter replay record is required")
    record_body = {key: value for key, value in replay.items() if key != "record_digest"}
    if digest(record_body) != replay.get("record_digest"):
        raise ValueError("transmitter replay record digest differs")
    specification = TransmitterScenario.model_validate_json(json.dumps(replay["specification"]))
    source_clock_id = replay.get("source_clock_id")
    reported_clock_id = replay.get("reported_clock_id")
    packets = replay.get("packets")
    if not isinstance(packets, list) or not packets or len(packets) > 100000:
        raise ValueError("monitoring conversion requires a complete bounded packet list")
    if specification.delivery_delay_s != 0:
        raise ValueError("monitoring input cannot represent packet availability delay; do not hide it")
    stop = len(packets) if stop_packet_index is None else stop_packet_index
    if (
        type(first_packet_index) is not int
        or type(stop) is not int
        or not 0 <= first_packet_index < stop <= len(packets)
    ):
        raise ValueError("select a nonempty in-range contiguous packet slice")
    selected = packets[first_packet_index:stop]
    frames_out = []
    observation_ids = []
    invalid = 0
    previous_physical = previous_reported = None
    for index, packet in enumerate(selected, first_packet_index):
        packet_body = {key: value for key, value in packet.items() if key != "packet_digest"}
        if digest(packet_body) != packet.get("packet_digest"):
            raise ValueError("a transmitter packet digest differs")
        frame_payload = packet.get("frame")
        if frame_payload is None:
            raise ValueError("negative reported time has no monitoring frame; retain it for clock review")
        frame = SignalFrame.model_validate_json(json.dumps(frame_payload))
        if (
            frame.stream_id != specification.instrument_id
            or frame.clock_id != reported_clock_id
            or frame.origin != "simulation"
            or tuple(frame.values) != (specification.channel,)
            or frame.units[specification.channel] != specification.axis.unit
            or packet.get("source_clock_id") != source_clock_id
            or packet.get("reported_clock_id") != reported_clock_id
            or packet.get("reported_time_s") != frame.time_s
        ):
            raise ValueError("packet clock, channel, unit or simulated-frame identity differs")
        if reported_clock_id != source_clock_id:
            raise ValueError("monitoring split cannot represent an unmapped reported clock")
        physical_time = packet.get("physical_source_time_s")
        if (
            isinstance(physical_time, bool)
            or not isinstance(physical_time, (int, float))
            or not math.isfinite(physical_time)
            or (previous_physical is not None and physical_time <= previous_physical)
            or (previous_reported is not None and frame.time_s <= previous_reported)
        ):
            raise ValueError("selected packet clocks must strictly increase without repair")
        previous_physical = physical_time
        previous_reported = frame.time_s
        invalid += not frame.valid[specification.channel]
        frames_out.append(frame)
        observation_ids.append(
            f"{specification.instrument_id}:packet:{index:08d}:{packet['source_frame_digest'][:12]}"
        )
    split = MonitoringSplit(
        role=role,
        split_id=split_id,
        physical_run_id=physical_run_id,
        source_group_id=source_group_id,
        source_evidence_id=source_evidence_id,
        observation_ids=tuple(observation_ids),
        frames=tuple(frames_out),
        reference_kind=reference_kind,
    )
    body = {
        "schema_version": "pipeline_integrity.transmitter_monitoring_input.v1",
        "split": split,
        "transmitter_replay_digest": replay["record_digest"],
        "source_clock_id": source_clock_id,
        "reported_clock_id": reported_clock_id,
        "first_packet_index": first_packet_index,
        "stop_packet_index": stop,
        "preserved_invalid_samples": invalid,
        "all_selected_packets_preserved": True,
        "delivery_delay_s": 0.0,
        "output_origin": "simulation",
        "monitoring_probability_or_field_claim": False,
    }
    result = TransmitterMonitoringInput(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def packet_observation(packet, *, axis, evidence_id):
    """Keep a deliberately invalid reported clock or absent packet out of a PID update."""
    if digest({k: v for k, v in packet.items() if k != "packet_digest"}) != packet.get("packet_digest"):
        raise ValueError("transmitter packet content changed")
    if ControlAxis.model_validate_json(json.dumps(packet["axis"])) != axis:
        raise ValueError("transmitter packet quantity, pressure reference or datum differs")
    if packet.get("frame") is not None and packet.get("reported_clock_id") != packet["frame"].get(
        "clock_id"
    ):
        raise ValueError("transmitter reported-clock identity differs from its frame")
    return ControlObservation(
        frame=None if packet["frame"] is None else SignalFrame.model_validate_json(json.dumps(packet["frame"])),
        axis=axis,
        available_at_s=packet["available_at_s"],
        evidence_id=evidence_id,
        receipt={k: v for k, v in packet.items() if k != "frame"},
    )
