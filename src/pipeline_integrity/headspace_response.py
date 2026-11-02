"""Bind simulated tank history to the existing alarm/HRA/actuator trace APIs.

Pressure threshold transitions remain sampled simulation observations. The
monitoring owner determines alarm states, the HRA owner computes supplied
response models, and response_trace retains command/endpoint chronology. This
adapter never derives HEP, event frequency or causality from a pressure trace.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .gas_headspace import headspace_signal_frames
from .alarm_exchange import AlarmReplaySpec, replay_alarm
from .response_trace import SampledEndpointRule, ResponseTraceSpec, trace_response
from .response_exchange import (
    ResponseModel, ResponseWindow, DeadlineEffect, DeadlineScenarioSet, EmpiricalResponse,
    HumanResponseRequest, HumanResponseClient,
)
from .valve_motion import ValveMotionResult, simulate_valve_motion

SHA=r"^[a-f0-9]{64}$"
CHANNEL_UNITS={"pressure_pa_abs":"Pa","temperature_K":"K","mass_kg":"kg",
               "internal_energy_J":"J","volume_m3":"m^3"}


class HeadspaceAlarmPolicy(Contract):
    id: Name
    pressure_direction: Literal["high_pressure","low_pressure"]
    trigger_pressure_pa_abs: Positive
    reset_pressure_pa_abs: Positive
    persistence_s: Nonnegative = 0.0
    recovery_s: Nonnegative = 0.0
    maximum_sample_gap_s: Positive
    latch: bool = False
    policy_evidence_id: Name
    policy_and_pressure_direction_reviewed: bool = False
    reset_requested_time_s: tuple[Nonnegative, ...] = ()
    reset_evidence_id: Name | None = None

    @model_validator(mode="after")
    def hysteresis_direction(self):
        if self.pressure_direction=="high_pressure" and self.reset_pressure_pa_abs>self.trigger_pressure_pa_abs:
            raise ValueError("high-pressure alarm reset must not exceed its trigger")
        if self.pressure_direction=="low_pressure" and self.reset_pressure_pa_abs<self.trigger_pressure_pa_abs:
            raise ValueError("low-pressure alarm reset must not be below its trigger")
        return self


class HeadspaceTraceMapping(Contract):
    alarm_activation_index: int = Field(ge=0)
    valve_component_id: Name
    command_id: Name
    protection_id: Name
    condition_id: Name
    opportunity_id: Name
    alarm_to_cue_delay_s: Nonnegative | None
    command_deadline_s: Nonnegative | None
    process_limit_time_s: Nonnegative | None
    maximum_motion_sample_interval_s: Positive
    maximum_human_deadline_scenarios: int = Field(default=256,ge=1,le=10000)
    asset_clock_and_origin_mapping_reviewed: bool = False
    cue_delivery_reviewed: bool = False
    command_endpoint_reviewed: bool = False
    process_limit_reviewed: bool = False

    @model_validator(mode="after")
    def deadlines(self):
        if self.command_deadline_s is not None and self.process_limit_time_s is not None and self.command_deadline_s>self.process_limit_time_s:
            raise ValueError("headspace command deadline must not follow its process limit")
        return self


class HeadspaceHumanTiming(Contract):
    initial_estimate: dict
    native_event: dict
    source_binding_digest: str = Field(pattern=SHA)
    source_evidence_id: Name
    source_evidence_sha256: str = Field(pattern=SHA)
    expected_owner_provider_digest: str = Field(pattern=SHA)
    time_model: ResponseModel
    population_definition: Name
    applicability_evidence_id: Name
    applicability_rationale: Name
    response_window_evidence_id: Name
    deadline_scenarios: DeadlineScenarioSet | None = None

    @field_validator("initial_estimate","native_event")
    @classmethod
    def bounded_owner_input(cls,value):
        value=json_value(value)
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8"))>4000000:
            raise ValueError("headspace HRA source input exceeds its declared owner-input budget")
        return value

    @model_validator(mode="after")
    def source_binding(self):
        if digest(dict(initial_estimate=self.initial_estimate,native_event=self.native_event))!=self.source_binding_digest:
            raise ValueError("headspace HRA source event/estimate differs from its pinned binding")
        return self


class HeadspaceResponseRequest(Contract):
    id: Name
    source_kind: Literal["gas_headspace", "fill_headspace", "hysteretic_tank", "headspace_drainage", "process_signals"] = "gas_headspace"
    headspace_record: dict
    expected_headspace_receipt_digest: str = Field(pattern=SHA)
    headspace_source_evidence_id: Name
    process_stream_id: Name
    alarm_stream_id: Name
    alarm_score_channel: Name = "pressure_alarm_score"
    alarm: HeadspaceAlarmPolicy
    mapping: HeadspaceTraceMapping
    endpoint: SampledEndpointRule
    mapping_evidence_ids: tuple[Name, ...] = Field(min_length=1)
    declared_command_issue_time_s: Nonnegative | None
    command_declaration_evidence_id: Name | None = None
    motion_receipt: ValveMotionResult | None = None
    expected_motion_receipt_digest: str | None = Field(default=None,pattern=SHA)
    motion_source_evidence_id: Name | None = None
    human_timing: HeadspaceHumanTiming | None = None
    maximum_source_frames: int = Field(default=100000,ge=1,le=1000000)
    maximum_result_bytes: int = Field(default=128000000,ge=1,le=1000000000)

    @field_validator("headspace_record")
    @classmethod
    def bounded_headspace(cls,value):
        value=json_value(value)
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8"))>64000000:
            raise ValueError("headspace source receipt exceeds the adapter input budget")
        return value

    @model_validator(mode="after")
    def coherent_mapping(self):
        if self.headspace_record.get("record_digest")!=self.expected_headspace_receipt_digest:
            raise ValueError("headspace response source receipt differs from its explicit pin")
        if self.process_stream_id==self.alarm_stream_id:
            raise ValueError("raw process and pressure-score streams need distinct explicit identifiers")
        if self.endpoint.stream_id!=self.process_stream_id:
            raise ValueError("headspace endpoint must select its declared process stream")
        source_units=(CHANNEL_UNITS if self.source_kind!="hysteretic_tank" else {
            "pressure_pa_abs":"Pa","liquid_mass_kg":"kg","vapor_mass_kg":"kg",
            "relay_open":"dimensionless","valve_opening":"dimensionless",
            "cumulative_boundary_mass_kg":"kg","cumulative_boundary_energy_J":"J",
            "cumulative_vented_mass_kg":"kg","cumulative_net_phase_transfer_kg":"kg"})
        if self.source_kind=="headspace_drainage":
            source_units={"liquid_volume_m3":"m^3","liquid_level_m":"m","gas_volume_m3":"m^3",
                          "gas_mass_kg":"kg","temperature_K":"K","pressure_pa_abs":"Pa",
                          "liquid_outflow_m3_s":"m^3/s"}
        if self.source_kind=="process_signals":
            frames=self.headspace_record.get("frames")
            if not isinstance(frames,list) or not frames or not isinstance(frames[0],dict):
                raise ValueError("process response needs a complete replay-qualified frame history")
            source_units=frames[0].get("units",{})
            if source_units.get("pressure_pa_abs")!="Pa":
                raise ValueError("pressure response requires an explicit absolute-pressure process channel")
        if self.endpoint.channel not in source_units or self.endpoint.unit!=source_units[self.endpoint.channel]:
            raise ValueError("headspace endpoint channel/unit must match an existing source state quantity")
        if self.endpoint.channel in {"pressure_pa_abs","temperature_K","mass_kg","volume_m3"} and self.endpoint.threshold<=0:
            raise ValueError("headspace physical endpoint threshold must be positive in its declared SI basis")
        if len(set(self.mapping_evidence_ids))!=len(self.mapping_evidence_ids):
            raise ValueError("headspace response mapping evidence IDs must be distinct")
        if (self.declared_command_issue_time_s is None)!=(self.command_declaration_evidence_id is None):
            raise ValueError("command issue declaration and its source evidence must be supplied together")
        if self.motion_receipt is None:
            if self.expected_motion_receipt_digest is not None or self.motion_source_evidence_id is not None:
                raise ValueError("motion source pins cannot stand in for an absent actuator receipt")
        elif (self.expected_motion_receipt_digest is None or self.motion_source_evidence_id is None
              or self.declared_command_issue_time_s is None):
            raise ValueError("a motion receipt needs its explicit source pin and declared command issue time")
        return self


def _pinned_evidence(evidence,identity,*,expected=None,label="mapping"):
    item=evidence.get(identity)
    if item is None or item.sha256 is None or (expected is not None and item.sha256!=expected):
        raise ValueError("headspace response "+label+" evidence is absent or differs from its source hash")
    return item


def _time_evidence(model):
    if isinstance(model,EmpiricalResponse):
        return {model.censoring_review_evidence_id,*(o.evidence_id for o in model.observations)}
    return {model.evidence_id}


def _seal(body,maximum):
    body=json_value(body)
    if body.get("implementation_sha256")!=file_digest(Path(__file__)):
        raise ValueError("headspace response implementation changed during evaluation")
    if any(file_digest(Path(__file__).with_name(name+".py"))!=source
           for name,source in body.get("owner_source_sha256",{}).items()):
        raise ValueError("a headspace response owner source changed during evaluation")
    body["record_digest"]=digest(body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>maximum:
        raise ValueError("complete headspace response receipt exceeds its result budget")
    return body


def _initial_body(q,c):
    return dict(schema="pipeline_integrity.headspace_response.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="needs_data",issues=[],
        source_headspace_digest=q.expected_headspace_receipt_digest,
        source_origin="simulation",source_frame_receipt=None,source_row_lineage=[],
        pressure_alarm_mapping=None,alarm_receipt=None,selected_activation=None,
        response_trace_specification=None,response_trace=None,human_response_request=None,
        human_response_result=None,human_response_scenarios_result=None,
        declared_command_issue_time_s=q.declared_command_issue_time_s,
        native_motion_replayed=False,human_owner_replayed=False,
        headspace_history_modified_by_command=False,actuator_to_headspace_feedback_computed=False,
        headspace_threshold_crossing_used_as_HEP=False,event_frequency_calculated=False,
        human_probability_updated_from_headspace=False,dynamic_risk_probability=None,
        human_time_distribution_fitted_from_headspace=False,initial_human_estimate_modified=False,
        human_model_window_uses_sampled_alarm_cue=False,trace_status_is_not_process_success=True,
        source_rows_deleted=False,threshold_crossing_onset_interpolated=False,
        causal_effect_of_command_proven=False,field_validation=False,numerical_validation_completed=False,
        implementation_sha256=file_digest(Path(__file__)),
        owner_source_sha256={name:file_digest(Path(__file__).with_name(name+".py"))
                            for name in ("gas_headspace","tank_filling","hysteretic_tank","headspace_drainage","signal_exchange","alarm_exchange","response_trace","response_exchange","valve_motion")})


def _process_source(q):
    """Replay the declared source without rewriting its row or receipt identity."""
    record=q.headspace_record
    expected={"gas_headspace":"pipeline_integrity.gas_headspace.v1",
              "fill_headspace":"pipeline_integrity.fill_headspace.v1",
              "hysteretic_tank":"pipeline_integrity.hysteretic_tank.v1",
              "headspace_drainage":"pipeline_integrity.headspace_drainage.v1",
              "process_signals":"pipeline_integrity.process_signals.v1"}[q.source_kind]
    if record.get("schema")!=expected:
        raise ValueError("process source schema differs from its explicit source kind")
    if q.source_kind=="process_signals":
        from .signal_exchange import verify_process_signals_record
        rows=record.get("frames")
        convert=None
    elif q.source_kind=="hysteretic_tank":
        from .hysteretic_tank import hysteretic_tank_signal_frames
        rows=record.get("history")
        convert=hysteretic_tank_signal_frames
    elif q.source_kind=="headspace_drainage":
        from .headspace_drainage import headspace_drainage_signal_frames
        rows=record.get("states")
        convert=headspace_drainage_signal_frames
    elif q.source_kind=="fill_headspace":
        from .tank_filling import verify_fill_headspace_record
        rows=record.get("gas",{}).get("states")
        convert=None
    else:
        rows=record.get("states")
        convert=headspace_signal_frames
    if not isinstance(rows,list) or not 1<=len(rows)<=q.maximum_source_frames:
        raise ValueError("process response requires a bounded complete source state history")
    if q.source_kind=="process_signals":
        verify_process_signals_record(record)
        frames=tuple(SignalFrame.model_validate_json(json.dumps(row)) for row in rows)
        if any(f.stream_id!=q.process_stream_id for f in frames):
            raise ValueError("process response must preserve the explicitly mapped source stream")
        receipt=dict(schema="pipeline_integrity.response_process_frames.v1",
                     source_record_digest=record["record_digest"],frame_count=len(frames),frames_digest=digest(frames))
        receipt["record_digest"]=digest(receipt)
    elif q.source_kind=="fill_headspace":
        verify_fill_headspace_record(record)
        frames,receipt=headspace_signal_frames(record["gas"],stream_id=q.process_stream_id)
        receipt=dict(receipt,parent_source_receipt_digest=record["record_digest"])
        receipt.pop("record_digest")
        receipt["record_digest"]=digest(receipt)
    else:
        frames,receipt=convert(record,stream_id=q.process_stream_id)
    return rows,frames,receipt


def evaluate_headspace_response(request: HeadspaceResponseRequest, context: RunContext, *,
                                alarm_factory=None, alarm_provider=None,
                                motion_factory=None, motion_provider=None,
                                human_response_client: HumanResponseClient | None = None):
    """Build real owner inputs and trace supplied simulation/command chronology."""
    q=HeadspaceResponseRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if c.origin!="simulation" or q.headspace_record.get("context") is None:
        raise ValueError("headspace source and response require explicit simulation contexts")
    source_context=RunContext.model_validate_json(json.dumps(q.headspace_record["context"]))
    if len({e.id for e in source_context.evidence})!=len(source_context.evidence):
        raise ValueError("source headspace simulation evidence IDs must be unique")
    if (source_context.asset_id,source_context.clock_id,source_context.scenario_id,source_context.origin)!=(
        c.asset_id,c.clock_id,c.scenario_id,c.origin):
        raise ValueError("headspace source asset, clock, scenario and simulation origin must remain unchanged")
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence):
        raise ValueError("headspace response evidence IDs must be unique")
    for old in source_context.evidence:
        if old.id not in evidence or old.model_dump(mode="json")!=evidence[old.id].model_dump(mode="json"):
            raise ValueError("response context must preserve the source simulation evidence unchanged")
    if any(key not in c.metadata or c.metadata[key]!=value for key,value in source_context.metadata.items()):
        raise ValueError("response context must preserve original simulation metadata while adding downstream bindings")
    _pinned_evidence(evidence,q.headspace_source_evidence_id,expected=q.expected_headspace_receipt_digest,label="simulation source receipt")
    for identity in (*q.mapping_evidence_ids,q.endpoint.evidence_id,q.alarm.policy_evidence_id):
        _pinned_evidence(evidence,identity)
    if q.alarm.reset_evidence_id is not None:
        _pinned_evidence(evidence,q.alarm.reset_evidence_id,label="alarm reset")
    if q.command_declaration_evidence_id is not None:
        _pinned_evidence(evidence,q.command_declaration_evidence_id,label="declared command")
    source_rows,frames,frame_receipt=_process_source(q)
    if not frames or len(frames)!=len(source_rows):
        raise ValueError("headspace frame conversion must preserve the complete original state sequence")
    body=_initial_body(q,c)
    body["source_simulation_context_digest"]=digest(source_context)
    body["source_frame_receipt"]=frame_receipt
    body["source_kind"]=q.source_kind
    body["source_row_lineage"]=[dict(source_row_index=i,source_window_id=row.get("window_id"),
        source_time_s=row["time_s"],source_state_digest=digest(row),source_frame_digest=digest(frame))
        for i,(row,frame) in enumerate(zip(source_rows,frames))]
    sign=1.0 if q.alarm.pressure_direction=="high_pressure" else -1.0
    alarm_frames=[]
    for frame in frames:
        pressure=frame.values["pressure_pa_abs"]
        alarm_frames.append(SignalFrame(stream_id=q.alarm_stream_id,clock_id=frame.clock_id,time_s=frame.time_s,
            timestamp_utc=frame.timestamp_utc,values={q.alarm_score_channel:None if pressure is None else sign*pressure},
            units={q.alarm_score_channel:"Pa"},valid={q.alarm_score_channel:frame.valid["pressure_pa_abs"]},
            origin=frame.origin,operating_mode=frame.operating_mode))
    alarm_spec=AlarmReplaySpec(alarm_id=q.alarm.id,source_stream_id=q.alarm_stream_id,
        score_channel=q.alarm_score_channel,score_unit="Pa",trigger=sign*q.alarm.trigger_pressure_pa_abs,
        reset=sign*q.alarm.reset_pressure_pa_abs,persistence_s=q.alarm.persistence_s,recovery_s=q.alarm.recovery_s,
        max_gap_s=q.alarm.maximum_sample_gap_s,latch=q.alarm.latch,
        policy_reviewed=q.alarm.policy_and_pressure_direction_reviewed,
        policy_evidence_id=q.alarm.policy_evidence_id,source_evidence_id=q.headspace_source_evidence_id,
        reset_requested_time_s=q.alarm.reset_requested_time_s,reset_evidence_id=q.alarm.reset_evidence_id,
        maximum_samples=q.maximum_source_frames)
    body["pressure_alarm_mapping"]=dict(pressure_direction=q.alarm.pressure_direction,score_multiplier=sign,
        source_pressure_channel="pressure_pa_abs",source_pressure_reference="absolute",score_is_probability=False,
        source_frames_digest=digest(frames),alarm_frames_digest=digest(tuple(alarm_frames)),
        alarm_specification=alarm_spec.model_dump(mode="json"))
    try:
        alarm=replay_alarm(tuple(alarm_frames),alarm_spec,c,factory=alarm_factory,provider=alarm_provider)
    except ImportError:
        body["issues"]=["monitoring alarm owner is unavailable; no alarm activation or cue is fabricated"]
        return _seal(body,q.maximum_result_bytes)
    body["alarm_receipt"]=alarm.model_dump(mode="json")
    body["issues"].extend(alarm.issues)
    index=q.mapping.alarm_activation_index
    if alarm.status!="exchange_checked" or index>=len(alarm.activation_records):
        body["issues"].append("the explicitly selected alarm activation is not observed in the complete simulation horizon")
        return _seal(body,q.maximum_result_bytes)
    activation=alarm.activation_records[index]
    source_index=next((i for i,f in enumerate(frames) if f.time_s==activation["time_s"]),None)
    if source_index is None:
        raise ValueError("alarm activation time must identify an original headspace row")
    body["selected_activation"]=dict(activation=activation,activation_index=index,source_row_index=source_index,
        source_state_digest=digest(source_rows[source_index]),
        sampled_transition_only=True,true_physical_onset_estimated=False)
    mapping_ids=tuple(dict.fromkeys((*q.mapping_evidence_ids,q.headspace_source_evidence_id)))
    spec=ResponseTraceSpec(id=q.id,alarm_activation_id=activation["id"],alarm_asset_id=c.asset_id,
        valve_component_id=q.mapping.valve_component_id,command_id=q.mapping.command_id,
        protection_id=q.mapping.protection_id,condition_id=q.mapping.condition_id,
        opportunity_id=q.mapping.opportunity_id,alarm_to_cue_delay_s=q.mapping.alarm_to_cue_delay_s,
        command_deadline_s=q.mapping.command_deadline_s,process_limit_time_s=q.mapping.process_limit_time_s,
        endpoint=q.endpoint,maximum_motion_sample_interval_s=q.mapping.maximum_motion_sample_interval_s,
        maximum_human_deadline_scenarios=q.mapping.maximum_human_deadline_scenarios,
        maximum_endpoint_samples=q.maximum_source_frames,mapping_evidence_ids=mapping_ids,
        asset_clock_and_origin_mapping_reviewed=q.mapping.asset_clock_and_origin_mapping_reviewed,
        cue_delivery_reviewed=q.mapping.cue_delivery_reviewed,
        command_endpoint_reviewed=q.mapping.command_endpoint_reviewed,process_limit_reviewed=q.mapping.process_limit_reviewed)
    body["response_trace_specification"]=spec.model_dump(mode="json")
    motion=None
    if q.motion_receipt is not None:
        q.motion_receipt.check_integrity()
        if q.motion_receipt.result_digest!=q.expected_motion_receipt_digest:
            raise ValueError("native motion receipt differs from its explicit source pin")
        _pinned_evidence(evidence,q.motion_source_evidence_id,expected=q.expected_motion_receipt_digest,label="native motion receipt")
        if (q.motion_receipt.request.component_id,q.motion_receipt.context.asset_id,
            q.motion_receipt.context.clock_id,q.motion_receipt.context.scenario_id)!=(
            q.mapping.valve_component_id,q.mapping.valve_component_id,c.clock_id,c.scenario_id):
            raise ValueError("native motion receipt must identify the explicitly mapped valve, scenario and common clock")
        command=next((command for command in q.motion_receipt.request.commands if command.id==q.mapping.command_id),None)
        if command is None or command.time_s!=q.declared_command_issue_time_s:
            raise ValueError("caller command issue time must match the selected original native command")
        try:
            replayed_motion=simulate_valve_motion(q.motion_receipt.request,q.motion_receipt.context,
                                                  factory=motion_factory,provider=motion_provider)
        except ImportError:
            body["issues"].append("native actuator owner is unavailable; supplied command is not replay-qualified")
        else:
            if replayed_motion!=q.motion_receipt:
                raise ValueError("native actuator source receipt differs on owner replay")
            motion=replayed_motion
            body["native_motion_replayed"]=True
    elif q.declared_command_issue_time_s is not None:
        body["issues"].append("command time was declared without a native actuator receipt; no actuator action is inferred")
    human=None
    scenarios=None
    if q.human_timing is not None:
        timing=q.human_timing
        _pinned_evidence(evidence,timing.source_evidence_id,expected=timing.source_evidence_sha256,label="independent HRA source input")
        needed={timing.applicability_evidence_id,timing.response_window_evidence_id}|_time_evidence(timing.time_model)
        if timing.deadline_scenarios is not None:
            needed.add(timing.deadline_scenarios.distribution_evidence_id)
            for row in timing.deadline_scenarios.scenarios:
                needed.add(row.evidence_id)
                if row.time_model is not None:
                    needed|=_time_evidence(row.time_model)
        for identity in needed:
            _pinned_evidence(evidence,identity,label="HRA timing model/window")
        cue=activation["time_s"]+spec.alarm_to_cue_delay_s if spec.alarm_to_cue_delay_s is not None and spec.cue_delivery_reviewed else None
        if cue is not None and not math.isfinite(cue):
            raise ValueError("headspace usable cue exceeds the finite common-clock domain")
        deadline=spec.command_deadline_s if spec.command_endpoint_reviewed else None
        if cue is None or deadline is None or deadline<=cue:
            body["issues"].append("reviewed positive cue-to-command opportunity is unresolved; no timed human model is evaluated")
        else:
            human_request=HumanResponseRequest(condition_id=spec.condition_id,opportunity_id=spec.opportunity_id,
                protection_ids=(spec.protection_id,),applicability_evidence_id=timing.applicability_evidence_id,
                applicability_rationale=timing.applicability_rationale,
                effect=DeadlineEffect(window=ResponseWindow(clock_id=c.clock_id,cue_time_s=cue,
                    limit_time_s=deadline,evidence_id=timing.response_window_evidence_id),
                    time_model=timing.time_model,population_definition=timing.population_definition))
            body["human_response_request"]=human_request.model_dump(mode="json")
            body["human_model_window_uses_sampled_alarm_cue"]=True
            mapping_ready=(spec.asset_clock_and_origin_mapping_reviewed and spec.process_limit_reviewed
                           and q.endpoint.interpretation_reviewed)
            if not mapping_ready:
                body["issues"].append("process/asset mapping or endpoint interpretation remains unreviewed; no HRA probability is bound")
            elif human_response_client is None:
                body["issues"].append("source-bound HumanResponseClient is absent; no HRA probability is fabricated")
            else:
                if not isinstance(human_response_client,HumanResponseClient):
                    raise TypeError("a source-bound HumanResponseClient provider is required")
                if digest(human_response_client.provider)!=timing.expected_owner_provider_digest:
                    raise ValueError("HRA response provider differs from the explicitly reviewed owner source")
                initial=human_response_client.api.Estimate.model_validate_json(json.dumps(timing.initial_estimate,allow_nan=False))
                event=human_response_client.api.HumanFailureEvent.model_validate_json(json.dumps(timing.native_event,allow_nan=False))
                if timing.deadline_scenarios is None:
                    human=human_response_client.evaluate(initial,event,human_request,c)
                    replay=human_response_client.verify_result(human)
                    body["human_response_result"]=human.model_dump(mode="json")
                else:
                    scenarios=human_response_client.evaluate_deadline_scenarios(initial,event,human_request,timing.deadline_scenarios,c)
                    replay=human_response_client.verify_deadline_scenario_result(scenarios)
                    body["human_response_scenarios_result"]=scenarios.model_dump(mode="json")
                if not isinstance(replay,dict) or replay.get("valid") is not True:
                    raise ValueError("HRA response owner did not confirm source-bound replay")
                if digest(human_response_client.provider)!=timing.expected_owner_provider_digest:
                    raise ValueError("HRA response provider identity changed during evaluation/replay")
                body["human_owner_replayed"]=True
    trace=trace_response(alarm,motion,spec,c,process_frames=frames,human_response=human,
                         human_response_scenarios=scenarios,human_response_client=human_response_client)
    body["response_trace"]=trace
    body["issues"]=list(dict.fromkeys((*body["issues"],*trace["issues"])))
    body["status"]="exchange_checked" if trace["status"]=="exchange_checked" and not body["issues"] else "needs_data"
    if any(file_digest(Path(__file__).with_name(name+".py"))!=source for name,source in body["owner_source_sha256"].items()):
        raise ValueError("a headspace response owner source changed during evaluation")
    return _seal(body,q.maximum_result_bytes)


def verify_headspace_response_record(record, *, request=None, context=None, alarm_factory=None,
                                     alarm_provider=None,motion_factory=None,motion_provider=None,
                                     human_response_client=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("headspace response receipt changed")
    q=HeadspaceResponseRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("headspace response caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("headspace response caller context differs")
    expected=evaluate_headspace_response(q,c,alarm_factory=alarm_factory,alarm_provider=alarm_provider,
        motion_factory=motion_factory,motion_provider=motion_provider,human_response_client=human_response_client)
    if expected!=record:
        raise ValueError("headspace process, alarm, command or HRA response replay differs")
    return True
