"""Sampled native PID feedback to an explicitly ideal recycle-flow actuator."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import compressor_dynamics, control_exchange
from .compressor_dynamics import CompressorDynamicsRequest, evaluate_compressor_dynamics, _trial
from .compressor_train import evaluate_compressor_train
from .control_exchange import PIDLoop, PIDSession, ControlObservation
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest


class ControlledCompressorRequest(Contract):
    id: Name
    plant: CompressorDynamicsRequest
    controller: PIDLoop
    pid_provider_identity: dict
    measurement: Literal["outlet_total_pressure_pa_abs","compressor_mass_flow_kg_s"]
    setpoint: Positive
    scan_interval_s: Positive
    maximum_recycle_mass_flow_kg_s: Positive
    mapping_evidence_id: Name
    ideal_instantaneous_recycle_flow_actuator_reviewed: bool = False
    measurement_datum_and_control_direction_reviewed: bool = False
    maximum_control_intervals: int = Field(default=1000,ge=1,le=10000)
    maximum_result_bytes: int = Field(default=50000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def boundary(self):
        loop=self.controller
        expected="pressure" if self.measurement=="outlet_total_pressure_pa_abs" else "mass_flow"
        if loop.measurement_axis.quantity!=expected or loop.output_axis.quantity!="fraction":
            raise ValueError("compressor PID requires the selected pressure/mass-flow input and fraction output")
        if expected=="pressure" and loop.measurement_axis.pressure_reference!="absolute":
            raise ValueError("compressor total-pressure feedback requires an absolute pressure axis")
        if loop.output_axis.reference_id!=loop.actuator_id:
            raise ValueError("controller fraction reference must identify its actual recycle actuator")
        port=self.plant.base.target_port_id if expected=="pressure" else self.plant.base.source_port_id
        if loop.measurement_axis.reference_id!=port:
            raise ValueError("compressor measurement axis must identify the actual source or target port")
        if loop.initial_output is None:
            raise ValueError("sampled recycle control needs an explicit initial command")
        if self.scan_interval_s>loop.maximum_scan_interval_s:
            raise ValueError("requested compressor control scan exceeds native loop policy")
        if any(w.recycle_mass_flow_kg_s!=0 for w in self.plant.windows):
            raise ValueError("controlled plant windows must leave recycle zero; the controller supplies it")
        if sum(math.ceil(w.duration_s/self.scan_interval_s) for w in self.plant.windows)>self.maximum_control_intervals:
            raise ValueError("controlled compressor exceeds its scan interval budget")
        return self


def controlled_compressor_boundary(request):
    return compressor_dynamics.compressor_dynamics_boundary(request.plant)


def evaluate_controlled_compressor(request: ControlledCompressorRequest, context: RunContext):
    q=ControlledCompressorRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.ideal_instantaneous_recycle_flow_actuator_reviewed or not q.measurement_datum_and_control_direction_reviewed:
        raise ValueError("ideal recycle action and actual feedback datum/direction require explicit review")
    if q.mapping_evidence_id not in {e.id for e in c.evidence}:
        raise ValueError("compressor/control mapping evidence is absent")
    before={"control":file_digest(Path(__file__)),"plant":file_digest(Path(compressor_dynamics.__file__)),
            "pid_adapter":file_digest(Path(control_exchange.__file__))}
    loop_data=c.model_dump(mode="python")
    loop_data["asset_id"]=q.controller.loop_id
    loop_data["metadata"]={**c.metadata,"compressor_control_mapping":dict(parent_request_digest=digest(q),
        plant_asset_id=q.plant.base.asset_id,measurement=q.measurement,mapping_evidence_id=q.mapping_evidence_id)}
    loop_context=RunContext.model_validate(loop_data)
    session=PIDSession(q.controller,loop_context)
    if session.provider!=q.pid_provider_identity:
        raise ValueError("native PID identity differs from the reviewed source pin")
    command=q.controller.initial_output
    speed=q.plant.initial_speed_rad_s
    time=0.0
    calls=0
    byte_count=0
    intervals=[]
    decisions=[]
    snapshots=[]
    totals={key:0.0 for key in ("drive","shaft_load","drag","gas","mechanical_loss","train_cooling","recycle_conditioning","net_enthalpy")}
    for window in q.plant.windows:
        count=math.ceil(window.duration_s/q.scan_interval_s)
        start=time
        for index in range(count):
            end=start+window.duration_s*(index+1)/count
            duration=end-time
            if duration<=0:
                raise ValueError("controlled compressor clock cannot represent its scan interval")
            operating=window.model_copy(update={"duration_s":duration,
                "recycle_mass_flow_kg_s":command*q.maximum_recycle_mass_flow_kg_s})
            remaining=q.plant.maximum_native_calls-calls-1
            if remaining<4:
                raise ValueError("controlled compressor exhausted its aggregate native call budget")
            plant_data=q.plant.model_dump(mode="python")
            plant_data.update(initial_speed_rad_s=speed,windows=(operating,),maximum_native_calls=remaining)
            plant=CompressorDynamicsRequest.model_validate(plant_data)
            result=evaluate_compressor_dynamics(plant,c)
            calls+=len(result["native_calls"])
            speed=result["states"][-1]["speed_rad_s"]
            native_request,child=_trial(plant,operating,speed,duration,c)
            endpoint=evaluate_compressor_train(native_request,child)
            calls+=1
            if endpoint.get("status")!="computed_diagnostic":
                raise ValueError("controlled compressor endpoint native evaluation withheld")
            actual=endpoint["values"]
            measurement=(actual["outlet_total_state"]["pressure_Pa"] if q.measurement=="outlet_total_pressure_pa_abs"
                         else actual["mass_flow_kg_s"])
            frame=SignalFrame(stream_id=q.controller.measurement_stream_id,clock_id=c.clock_id,time_s=end,
                values={q.controller.measurement_channel:measurement},units={q.controller.measurement_channel:q.controller.measurement_axis.unit},
                valid={q.controller.measurement_channel:True},origin="simulation",operating_mode=window.id)
            observation=ControlObservation(frame=frame,axis=q.controller.measurement_axis,available_at_s=end,
                evidence_id=q.mapping_evidence_id,receipt=dict(source_digest=endpoint["result_digest"],
                    source_local_time_s=duration,global_time_s=end,interval_offset_s=time))
            decision=session.step(end,q.setpoint,observation)
            if decision["status"]!="computed" or decision["output"] is None:
                raise ValueError("native controller withheld this compressor feedback decision")
            interval=dict(start_time_s=time,end_time_s=end,held_command_fraction=command,
                actual_recycle_mass_flow_kg_s=operating.recycle_mass_flow_kg_s,
                local_time_mapping="global_time = source_local_time + start_time_s",plant_record=result)
            for key in totals:
                totals[key]+=result["energy_ledger_J"][key]
            intervals.append(interval)
            decisions.append(decision)
            snapshots.append(endpoint)
            byte_count+=sum(len(json.dumps(record,allow_nan=False).encode("utf-8")) for record in (interval,decision,endpoint))
            if byte_count>q.maximum_result_bytes:
                raise ValueError("controlled compressor retained-source byte budget exceeded")
            command=decision["output"]
            time=end
    initial=0.5*q.plant.rotor_inertia_kg_m2*q.plant.initial_speed_rad_s**2
    final=0.5*q.plant.rotor_inertia_kg_m2*speed**2
    residual=math.fsum((initial,totals["drive"],-final,-totals["drag"],-totals["mechanical_loss"],
                       -totals["train_cooling"],-totals["recycle_conditioning"],-totals["net_enthalpy"]))
    tolerance=q.plant.absolute_energy_residual_tolerance_J+q.plant.relative_energy_residual_tolerance*max(
        initial,final,*[abs(v) for v in totals.values()],1.0)
    if abs(residual)>tolerance:
        raise ValueError("controlled compressor aggregate rotor/gas energy ledger exceeds tolerance")
    after={"control":file_digest(Path(__file__)),"plant":file_digest(Path(compressor_dynamics.__file__)),
            "pid_adapter":file_digest(Path(control_exchange.__file__))}
    if before!=after or session.provider!=q.pid_provider_identity or control_exchange.native_identity(type(session._controller))!=q.pid_provider_identity:
        raise ValueError("controlled compressor implementation or native PID binding changed")
    body=json_value(dict(schema="pipeline_integrity.controlled_compressor.v1",status="computed_diagnostic",
        request=q,context=c,intervals=intervals,decisions=decisions,endpoint_records=snapshots,
        native_calls=calls,source_sha256=before,final_speed_rad_s=speed,final_time_s=time,
        energy_ledger_J=dict(initial_rotor=initial,final_rotor=final,**totals,overall_residual=residual),
        energy_tolerance_J=tolerance,final_command_applied=False,
        actuator_model="ideal_instantaneous_recycle_flow; held_between_samples",
        field_validation_claim=False,surge_dynamics_executed=False,anti_surge_qualification=False,
        numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("controlled compressor receipt exceeds its byte budget")
    return body


def verify_controlled_compressor_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("controlled compressor receipt changed")
    q=ControlledCompressorRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("controlled compressor caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("controlled compressor caller context differs")
    if evaluate_controlled_compressor(q,c)!=record:
        raise ValueError("controlled compressor plant/PID replay differs")
    return True
