"""Original chronology inputs; thresholds and times are illustrative assumptions."""

from pipeline_integrity.alarm_exchange import AlarmReplaySpec
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.response_trace import ResponseTraceSpec, SampledEndpointRule
from pipeline_integrity.valve_motion import ConductanceCurve, ValveCommand, ValveMotionRequest


def make_case(*, offset_s=0.0):
    context = RunContext(
        asset_id="authored_line",
        scenario_id="alarm_command_endpoint_reference",
        clock_id="explicit_elapsed",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(id=id, citation=text, origin="synthetic", license_id="MIT")
            for id, text in (
                ("source", "Original score and process endpoint values for chronology checks"),
                ("policy", "Illustrative thresholds and persistence; not a tuned leak detector"),
                ("mapping", "Authored shared clock, asset mapping, cue delay and two distinct deadlines"),
                ("command", "Original issue time and finite stroke assumption"),
                ("endpoint", "Illustrative absolute flow threshold and one-second sampled hold"),
            )
        ),
    )
    alarm_frames = tuple(
        SignalFrame(
            stream_id="declared_score",
            clock_id=context.clock_id,
            time_s=offset_s + float(i),
            values={"score": float(value)},
            units={"score": "1"},
            valid={"score": True},
            origin="synthetic",
        )
        for i, value in enumerate((0, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0))
    )
    policy = AlarmReplaySpec(
        alarm_id="low_flow_warning",
        source_stream_id="declared_score",
        score_channel="score",
        score_unit="1",
        trigger=1.0,
        reset=0.0,
        persistence_s=2.0,
        recovery_s=1.0,
        max_gap_s=1.0,
        policy_reviewed=True,
        policy_evidence_id="policy",
        source_evidence_id="source",
    )
    motion = ValveMotionRequest(
        component_id="V-1",
        initial_opening=1.0,
        full_stroke_time_s=4.0,
        sample_time_s=tuple(float(i) for i in range(int(offset_s + 13))),
        commands=(
            ValveCommand(id="close", time_s=offset_s + 5.0, target_opening=0.0, evidence_id="command"),
        ),
        conductance=ConductanceCurve(
            opening=(0.0, 1.0),
            effective_area_m2=(1e-8, 1e-3),
            parameter_source_id="authored_nonzero_closed_conductance",
        ),
    )
    endpoint_frames = tuple(
        SignalFrame(
            stream_id="process_flow",
            clock_id=context.clock_id,
            time_s=offset_s + float(i),
            values={"q": value},
            units={"q": "m3/s"},
            valid={"q": True},
            origin="synthetic",
        )
        for i, value in enumerate((1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.7, 0.4, 0.2, 0.15, 0.15))
    )
    trace = ResponseTraceSpec(
        id="original_trace",
        alarm_activation_id="replace_with_replay_activation_id",
        alarm_asset_id=context.asset_id,
        valve_component_id="V-1",
        command_id="close",
        protection_id="isolation_command",
        condition_id="usable_warning_in_authored_case",
        opportunity_id="one_authored_response",
        alarm_to_cue_delay_s=1.0,
        command_deadline_s=offset_s + 6.0,
        process_limit_time_s=offset_s + 10.0,
        endpoint=SampledEndpointRule(
            stream_id="process_flow",
            channel="q",
            unit="m3/s",
            relation="abs_le",
            threshold=0.16,
            required_hold_s=1.0,
            maximum_sample_interval_s=1.0,
            evidence_id="endpoint",
            interpretation_reviewed=True,
        ),
        maximum_motion_sample_interval_s=1.0,
        mapping_evidence_ids=("mapping",),
        asset_clock_and_origin_mapping_reviewed=True,
        cue_delivery_reviewed=True,
        command_endpoint_reviewed=True,
        process_limit_reviewed=True,
    )
    return alarm_frames, policy, motion, endpoint_frames, trace, context
