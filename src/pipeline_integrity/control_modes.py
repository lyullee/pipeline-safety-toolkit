"""Manual/interlock supervision and native-PID bumpless state tracking.

Tracking searches the actual owner's unsaturated output through public preview
and checkpoint APIs. PID gains, filtering and anti-windup are not reimplemented.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from scipy.optimize import brentq

from . import control_exchange
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .control_exchange import PIDLoop, PIDSession, ControlObservation
from .datasets import file_digest


class RegulationModeScan(Contract):
    id: Name
    decision_time_s: Nonnegative
    setpoint: float
    requested_mode: Literal["auto","manual"]
    manual_output: float | None = None
    interlock_active: bool | None
    process_observation: ControlObservation
    actual_actuator_observation: ControlObservation
    mode_state_evidence_id: Name
    external_tracking_requested: bool = False


class RegulationModesRequest(Contract):
    id: Name
    loop: PIDLoop
    initial_time_s: Nonnegative = 0.0
    initial_mode: Literal["auto","manual"] = "manual"
    scans: tuple[RegulationModeScan,...] = Field(min_length=1,max_length=100000)
    actuator_stream_id: Name
    actuator_channel: Name
    maximum_actual_actuator_age_s: Nonnegative
    interlock_output: float
    track_on_auto_entry: bool = True
    tracking_integral_bounds: tuple[float,float]
    tracking_integral_absolute_tolerance: Positive = 1e-9
    tracking_output_absolute_tolerance: Positive = 1e-8
    maximum_tracking_root_iterations: int = Field(default=100,ge=1,le=1000)
    maximum_native_previews: int = Field(default=10000,ge=1,le=1000000)
    native_pid_identity: dict
    tracking_and_mode_priority_evidence_id: Name
    manual_interlock_auto_priority_and_integral_state_tracking_reviewed: bool = False
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def clocks(self):
        if self.scans[0].decision_time_s<=self.initial_time_s or any(
            b.decision_time_s<=a.decision_time_s for a,b in zip(self.scans,self.scans[1:])):
            raise ValueError("regulation scans must advance a strictly increasing decision clock")
        if len({scan.id for scan in self.scans})!=len(self.scans):
            raise ValueError("regulation scan IDs must be unique")
        if self.tracking_integral_bounds[0]>=self.tracking_integral_bounds[1]:
            raise ValueError("native integral tracking needs a strictly ordered finite state bracket")
        if any(not control_exchange._in_domain(self.loop.measurement_axis,scan.setpoint) for scan in self.scans):
            raise ValueError("controller setpoints must remain inside their declared physical input domain")
        if not self.loop.output_minimum<=self.interlock_output<=self.loop.output_maximum:
            raise ValueError("interlock command lies outside the declared controller output limits")
        return self


def _sources():
    return {"modes":file_digest(Path(__file__)),"pid_adapter":file_digest(Path(control_exchange.__file__))}


def evaluate_regulation_modes(request: RegulationModesRequest, context: RunContext):
    q=RegulationModesRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if c.asset_id!=q.loop.loop_id:
        raise ValueError("supervised controller context must identify the actual PID loop")
    evidence={e.id:e for e in c.evidence}
    required={q.tracking_and_mode_priority_evidence_id,q.loop.parameter_evidence_id}
    required.update(scan.mode_state_evidence_id for scan in q.scans)
    required.update(obs.evidence_id for scan in q.scans for obs in (scan.process_observation,scan.actual_actuator_observation))
    if len(evidence)!=len(c.evidence) or not required<=set(evidence):
        raise ValueError("controller, mode, interlock and actuator evidence must exist unambiguously")
    if any(evidence[id].sha256 is None for id in required):
        raise ValueError("controller mode and actual-position tracking require source-pinned evidence")
    if not q.manual_interlock_auto_priority_and_integral_state_tracking_reviewed:
        raise ValueError("manual/interlock priority and bounded native-state tracking require explicit review")
    sources=_sources()
    session=PIDSession(q.loop,c,initial_time_s=q.initial_time_s)
    if session.provider!=q.native_pid_identity:
        raise ValueError("native PID provider differs from the reviewed source identity")
    previous_mode=q.initial_mode
    rows=[]
    previews=0
    retained=0
    def suppress(scan):
        # Actual measurements remain in the outer receipt. This synthetic missing
        # observation is an intentional mode gate, not a claim of sensor failure.
        original=scan.process_observation
        observation=ControlObservation(frame=None,axis=q.loop.measurement_axis,available_at_s=scan.decision_time_s,
            evidence_id=original.evidence_id,receipt=dict(intentional_mode_suppression=True,
                actual_observation_digest=digest(original),scan_id=scan.id))
        before=session.checkpoint()
        decision=session.step(scan.decision_time_s,scan.setpoint,observation)
        after=session.checkpoint()
        if after.memory!=before.memory:
            raise ValueError("mode-suppressed native PID changed its frozen memory")
        return decision
    def actual_position(scan):
        observation=scan.actual_actuator_observation
        frame=observation.frame
        if observation.axis!=q.loop.output_axis:
            raise ValueError("actual actuator quantity, unit or physical reference differs from the PID output")
        if frame is None or frame.clock_id!=c.clock_id or frame.stream_id!=q.actuator_stream_id:
            raise ValueError("actual actuator position lacks its exact source stream and clock")
        if q.actuator_channel not in frame.values or frame.units[q.actuator_channel]!=q.loop.output_axis.unit:
            raise ValueError("actual actuator position channel or SI unit differs")
        if not frame.valid[q.actuator_channel] or frame.values[q.actuator_channel] is None:
            raise ValueError("actual actuator position is missing or invalid")
        if observation.available_at_s>scan.decision_time_s or frame.time_s>scan.decision_time_s or scan.decision_time_s-frame.time_s>q.maximum_actual_actuator_age_s:
            raise ValueError("actual actuator position is unavailable, future-dated or stale")
        value=frame.values[q.actuator_channel]
        if not q.loop.output_minimum<=value<=q.loop.output_maximum:
            raise ValueError("actual actuator position lies outside the native output range; it is not clamped")
        return value
    for scan in q.scans:
        checkpoint_before=session.checkpoint()
        native=None
        tracking=None
        error=None
        command=None
        status="computed_diagnostic"
        if scan.interlock_active is None:
            mode="unknown_interlock"
            native=suppress(scan)
            status="withheld"
            error="interlock state is unknown; no output command is authorized by this calculation"
        elif scan.interlock_active:
            mode="interlock"
            native=suppress(scan)
            command=q.interlock_output
        elif scan.requested_mode=="manual":
            mode="manual"
            native=suppress(scan)
            if scan.manual_output is None or not q.loop.output_minimum<=scan.manual_output<=q.loop.output_maximum:
                status="withheld"
                error="manual output is missing or outside the declared output limits"
            else:
                command=scan.manual_output
        else:
            mode="auto"
            needs_tracking=scan.external_tracking_requested or (q.track_on_auto_entry and previous_mode!="auto")
            try:
                if needs_tracking:
                    target=actual_position(scan)
                    base=session.checkpoint()
                    def residual(integral):
                        nonlocal previews
                        previews+=1
                        if previews>q.maximum_native_previews:
                            raise ValueError("native PID preview work budget exceeded")
                        memory=base.memory.model_copy(update={"integral_error_s":float(integral)})
                        result=session.preview(scan.decision_time_s,scan.setpoint,scan.process_observation,memory=memory)
                        if result["status"]!="computed" or result["native"] is None:
                            raise ValueError("native PID preview withheld the actual process observation")
                        output=result["native"].get("unsaturated_output")
                        if isinstance(output,bool) or not isinstance(output,(int,float)) or not math.isfinite(output):
                            raise ValueError("native PID has no finite unsaturated tracking output")
                        return output-target
                    lo,hi=q.tracking_integral_bounds
                    current=base.memory.integral_error_s
                    current_matches=lo<=current<=hi and abs(residual(current))<=q.tracking_output_absolute_tolerance
                    flo,fhi=(0.0,0.0) if current_matches else (residual(lo),residual(hi))
                    if current_matches:
                        integral=current
                    elif abs(flo)<=q.tracking_output_absolute_tolerance:
                        integral=lo
                    elif abs(fhi)<=q.tracking_output_absolute_tolerance:
                        integral=hi
                    elif flo*fhi>=0:
                        raise ValueError("actual actuator output is not bracketed by the declared native integral states")
                    else:
                        integral=float(brentq(residual,lo,hi,xtol=q.tracking_integral_absolute_tolerance,
                            rtol=4*2.220446049250313e-16,maxiter=q.maximum_tracking_root_iterations))
                    error_at_root=residual(integral)
                    if abs(error_at_root)>q.tracking_output_absolute_tolerance:
                        raise ValueError("native anti-windup branch did not satisfy the actual tracking residual")
                    memory=base.memory.model_copy(update={"integral_error_s":integral})
                    tracked=session.checkpoint_with_memory(memory)
                    session.restore(tracked)
                    tracking=dict(target_actual_output=target,integral_error_before_s=base.memory.integral_error_s,
                        integral_error_tracked_s=integral,unsaturated_output_residual=error_at_root,
                        restored_checkpoint=tracked.model_dump(mode="json"),committed=False,native_pid_formula_reimplemented=False)
                native=session.step(scan.decision_time_s,scan.setpoint,scan.process_observation)
                if native["status"]!="computed" or native["output"] is None:
                    raise ValueError("native automatic PID withheld its process measurement")
                if needs_tracking and abs(native["output"]-tracking["target_actual_output"])>q.tracking_output_absolute_tolerance:
                    raise ValueError("committed native PID differs from its actual-position tracking preview")
                command=native["output"]
                if tracking is not None:
                    tracking["committed"]=True
            except (ValueError,RuntimeError,ArithmeticError) as exc:
                session.restore(checkpoint_before)
                native=suppress(scan)
                status="withheld"
                command=None
                error=type(exc).__name__+": "+str(exc)
                mode="auto_withheld"
        row=json_value(dict(scan_id=scan.id,decision_time_s=scan.decision_time_s,effective_mode=mode,status=status,
            output_command=command,output_unit=q.loop.output_axis.unit,issue=error,
            actual_process_observation=scan.process_observation,actual_actuator_observation=scan.actual_actuator_observation,
            intentional_native_mode_suppression=mode!="auto",native_decision=native,tracking=tracking,
            before_checkpoint=checkpoint_before,after_checkpoint=session.checkpoint(),
            priority="unknown_interlock_withhold; active_interlock; manual; automatic",
            command_is_actual_actuator_action=False))
        retained+=len(json.dumps(row,allow_nan=False).encode("utf-8"))
        if retained>q.maximum_result_bytes:
            raise ValueError("supervised PID retained-result byte budget exceeded")
        rows.append(row)
        previous_mode=mode
    if _sources()!=sources or control_exchange.native_identity(type(session._controller))!=q.native_pid_identity:
        raise ValueError("supervised PID native or adapter source changed during evaluation")
    body=json_value(dict(schema="pipeline_integrity.regulation_modes.v1",request=q,context=c,
        status="withheld" if any(row["status"]=="withheld" for row in rows) else "computed_diagnostic",
        states=rows,provider=session.provider,source_sha256=sources,native_previews=previews,
        final_checkpoint=session.checkpoint(),manual_interlock_memory_policy="freeze native memory; advance decision clock; restart derivative on recovery",
        integral_state_unit="("+q.loop.measurement_axis.unit+")*s",
        actuator_dynamics_implemented=False,interlock_safety_qualification=False,numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("supervised PID full receipt exceeds its byte budget")
    return body


def verify_regulation_modes_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("supervised PID receipt changed")
    q=RegulationModesRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("supervised PID caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("supervised PID caller context differs")
    if evaluate_regulation_modes(q,c)!=record:
        raise ValueError("native supervised PID/memory tracking replay differs")
    return True
