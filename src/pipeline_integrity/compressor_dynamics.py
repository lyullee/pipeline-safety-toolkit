"""Common-shaft inertia driven by native static compressor-map evaluations.

The imposed inlet state includes external recycle conditioning. No gas volume,
surge oscillation, throttle characteristic or controller is invented here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import scipy
from scipy.integrate import solve_ivp
from pydantic import Field, model_validator

from . import compressor_train
from .compressor_train import CompressorTrainRequest, evaluate_compressor_train
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest


class CompressorDynamicWindow(Contract):
    id: Name
    duration_s: Positive
    fresh_mass_flow_kg_s: Positive
    recycle_mass_flow_kg_s: Nonnegative = 0.0
    inlet_total_pressure_pa_abs: Positive
    inlet_total_temperature_K: Positive
    drive_torque_N_m: float
    drive_speed_feedback_N_m_s: Nonnegative = 0.0
    viscous_drag_N_m_s: Nonnegative = 0.0
    evidence_id: Name


class CompressorDynamicsRequest(Contract):
    id: Name
    base: CompressorTrainRequest
    shaft_id: Name
    rotor_inertia_kg_m2: Positive
    initial_speed_rad_s: Positive
    windows: tuple[CompressorDynamicWindow, ...] = Field(min_length=1,max_length=1000)
    rotor_evidence_id: Name
    quasi_steady_map_and_common_shaft_reviewed: bool = False
    external_inlet_conditioning_and_prescribed_recycle_reviewed: bool = False
    maximum_step_s: Positive
    sample_step_s: Positive
    relative_tolerance: Positive = Field(default=1e-7,lt=0.1)
    absolute_speed_tolerance_rad_s: Positive = 1e-8
    absolute_integral_tolerance_J: Positive = 1e-4
    absolute_energy_residual_tolerance_J: Nonnegative = 0.01
    relative_energy_residual_tolerance: Nonnegative = 1e-6
    maximum_native_calls: int = Field(default=5000,ge=4,le=100000)
    maximum_samples: int = Field(default=10000,ge=2,le=200000)
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def common_shaft(self):
        if any(s.shaft_id!=self.shaft_id for s in self.base.stages):
            raise ValueError("dynamic compressor requires every stage on the declared common shaft")
        if self.base.time_s!=0 or self.base.clock_offset_s!=0:
            raise ValueError("dynamic template must declare elapsed time zero with zero clock offset")
        if len({w.id for w in self.windows})!=len(self.windows):
            raise ValueError("compressor operating-window IDs must be unique")
        if 1+sum(math.ceil(w.duration_s/self.sample_step_s) for w in self.windows)>self.maximum_samples:
            raise ValueError("compressor requested samples exceed their declared budget")
        return self


def compressor_dynamics_boundary(request):
    return compressor_train.boundary_metadata(request.base)


def _trial(q,window,speed,time,context):
    data=q.base.model_dump(mode="python")
    data.update(time_s=float(time),inlet_total_pressure_pa_abs=window.inlet_total_pressure_pa_abs,
                inlet_total_temperature_K=window.inlet_total_temperature_K,
                mass_flow_kg_s=window.fresh_mass_flow_kg_s+window.recycle_mass_flow_kg_s)
    data["stages"]=tuple(stage.model_copy(update={"speed_rad_s":float(speed)}) for stage in q.base.stages)
    request=CompressorTrainRequest.model_validate(data)
    metadata=dict(context.metadata)
    metadata["compressor_train_boundary"]=compressor_train.boundary_metadata(request)
    metadata["compressor_dynamics_trial"]=dict(parent_request_digest=digest(q),window_id=window.id,
                                               shaft_id=q.shaft_id,speed_rad_s=float(speed),time_s=float(time))
    child=RunContext.model_validate({**context.model_dump(mode="python"),"metadata":metadata})
    return request,child


def evaluate_compressor_dynamics(request: CompressorDynamicsRequest, context: RunContext):
    q=CompressorDynamicsRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.base.asset_id,q.base.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("compressor dynamic asset/clock differs from context")
    if c.metadata.get("compressor_train_boundary")!=compressor_train.boundary_metadata(q.base):
        raise ValueError("dynamic context must preserve the reviewed original compressor-train boundary")
    ids=[e.id for e in c.evidence]
    if len(ids)!=len(set(ids)) or not {q.rotor_evidence_id,*[w.evidence_id for w in q.windows]}<=set(ids):
        raise ValueError("rotor and prescribed operating boundaries need unambiguous context evidence")
    if not q.quasi_steady_map_and_common_shaft_reviewed or not q.external_inlet_conditioning_and_prescribed_recycle_reviewed:
        raise ValueError("quasi-steady shaft and external recycle-conditioning assumptions need explicit review")
    before={"dynamic":file_digest(Path(__file__)),"static":file_digest(Path(compressor_train.__file__))}
    # Integrated channels use the same RK stages as angular acceleration.
    channels=("drive","shaft_load","drag","gas","mechanical_loss","train_cooling","recycle_conditioning","net_enthalpy")
    y=[q.initial_speed_rad_s]+[0.0]*len(channels)
    native=[]
    byte_count=0
    rows=[]
    t=0.0
    for window in q.windows:
        def rhs(time,state):
            nonlocal byte_count
            speed=float(state[0])
            if not math.isfinite(speed) or speed<=0:
                raise ValueError("compressor dynamic speed left the positive-speed map domain")
            if len(native)>=q.maximum_native_calls:
                raise ValueError("compressor dynamic owner-call budget exceeded")
            trial,child=_trial(q,window,speed,time,c)
            result=evaluate_compressor_train(trial,child)
            retained=dict(window_id=window.id,time_s=float(time),speed_rad_s=speed,record=result)
            byte_count+=len(json.dumps(retained,allow_nan=False).encode("utf-8"))
            if byte_count>q.maximum_result_bytes:
                raise ValueError("compressor dynamic native receipts exceed their byte budget")
            native.append(retained)
            if result.get("status")!="computed_diagnostic" or result.get("audits",{}).get("within_energy_budget") is not True:
                raise ValueError("native compressor map/thermodynamic evaluation withheld: "+str(result.get("notes")))
            values=result["values"]
            load=values["shaft_input_power_W"]
            drive_torque=window.drive_torque_N_m-window.drive_speed_feedback_N_m_s*speed
            drag=window.viscous_drag_N_m_s*speed**2
            hin=values["inlet_total_state"]["specific_enthalpy_J_kg"]
            hout=values["outlet_total_state"]["specific_enthalpy_J_kg"]
            if window.recycle_mass_flow_kg_s>0 and values["outlet_total_state"]["pressure_Pa"]<=window.inlet_total_pressure_pa_abs:
                raise ValueError("prescribed recycle requires a high-to-low pressure return boundary")
            # Return throttling preserves h; the external conditioner returns h_out to h_in.
            recycle=window.recycle_mass_flow_kg_s*(hout-hin)
            net=window.fresh_mass_flow_kg_s*(hout-hin)
            acceleration=(drive_torque-load/speed-window.viscous_drag_N_m_s*speed)/q.rotor_inertia_kg_m2
            rates=(drive_torque*speed,load,drag,values["gas_power_W"],
                   values["mechanical_loss_to_surroundings_W"],values["heat_rejected_W"],recycle,net)
            return [acceleration,*rates]
        end=t+window.duration_s
        if end<=t:
            raise ValueError("compressor dynamic clock cannot represent this window")
        count=math.ceil(window.duration_s/q.sample_step_s)
        times=[t]+[t+window.duration_s*i/count for i in range(1,count)]+[end]
        if any(b<=a for a,b in zip(times,times[1:])):
            raise ValueError("compressor sample clock cannot represent distinct requested times")
        solution=solve_ivp(rhs,(t,end),y,method="DOP853",t_eval=times,max_step=q.maximum_step_s,
            rtol=q.relative_tolerance,atol=[q.absolute_speed_tolerance_rad_s]+[q.absolute_integral_tolerance_J]*len(channels))
        if not solution.success or len(solution.t)!=count+1:
            raise ValueError("compressor shaft integration did not complete its exact requested interval")
        for index,time in enumerate(solution.t):
            if rows and index==0:
                continue
            state=solution.y[:,index]
            row=dict(window_id=window.id,time_s=float(time),speed_rad_s=float(state[0]),
                     rotor_energy_J=0.5*q.rotor_inertia_kg_m2*float(state[0])**2,
                     cumulative_energy_J={key:float(state[i+1]) for i,key in enumerate(channels)},
                     fresh_mass_flow_kg_s=window.fresh_mass_flow_kg_s,recycle_mass_flow_kg_s=window.recycle_mass_flow_kg_s,
                     compressor_mass_flow_kg_s=window.fresh_mass_flow_kg_s+window.recycle_mass_flow_kg_s)
            work_at_time=row["cumulative_energy_J"]
            initial_energy=0.5*q.rotor_inertia_kg_m2*q.initial_speed_rad_s**2
            residual_at_time=math.fsum((initial_energy,work_at_time["drive"],-work_at_time["shaft_load"],
                                        -work_at_time["drag"],-row["rotor_energy_J"]))
            sample_tolerance=q.absolute_energy_residual_tolerance_J+q.relative_energy_residual_tolerance*max(
                initial_energy,row["rotor_energy_J"],*[abs(v) for v in work_at_time.values()],1.0)
            if abs(residual_at_time)>sample_tolerance:
                raise ValueError("sampled compressor rotor energy residual exceeds its declared tolerance")
            row["rotor_energy_residual_J"]=residual_at_time
            rows.append(row)
        y=[float(v) for v in solution.y[:,-1]]
        t=end
    initial=0.5*q.rotor_inertia_kg_m2*q.initial_speed_rad_s**2
    final=0.5*q.rotor_inertia_kg_m2*y[0]**2
    work={key:y[i+1] for i,key in enumerate(channels)}
    rotor_residual=math.fsum((initial,work["drive"],-work["shaft_load"],-work["drag"],-final))
    gas_residual=math.fsum((work["gas"],-work["train_cooling"],-work["recycle_conditioning"],-work["net_enthalpy"]))
    total_residual=math.fsum((initial,work["drive"],-final,-work["drag"],-work["mechanical_loss"],
                             -work["train_cooling"],-work["recycle_conditioning"],-work["net_enthalpy"]))
    tolerance=q.absolute_energy_residual_tolerance_J+q.relative_energy_residual_tolerance*max(initial,final,*[abs(v) for v in work.values()],1.0)
    if max(abs(rotor_residual),abs(gas_residual),abs(total_residual))>tolerance:
        raise ValueError("compressor dynamic rotor/gas/overall energy ledger exceeds the declared tolerance")
    after={"dynamic":file_digest(Path(__file__)),"static":file_digest(Path(compressor_train.__file__))}
    if before!=after:
        raise ValueError("compressor dynamic/static implementation changed during evaluation")
    body=json_value(dict(schema="pipeline_integrity.compressor_dynamics.v1",status="computed_diagnostic",
        request=q,context=c,states=rows,native_calls=native,source_sha256=before,
        numerical_backend=dict(name="scipy.integrate.solve_ivp:DOP853",version=scipy.__version__),
        energy_ledger_J=dict(initial_rotor=initial,final_rotor=final,**work,
                            rotor_residual=rotor_residual,gas_residual=gas_residual,overall_residual=total_residual),
        energy_tolerance_J=tolerance,recycle_pressure_reduction="external_isenthalpic_throttle",
        mass_ledger_kg=dict(fresh=math.fsum(w.fresh_mass_flow_kg_s*w.duration_s for w in q.windows),
            net_product=math.fsum(w.fresh_mass_flow_kg_s*w.duration_s for w in q.windows),
            recycle=math.fsum(w.recycle_mass_flow_kg_s*w.duration_s for w in q.windows),
            through_compressor=math.fsum((w.fresh_mass_flow_kg_s+w.recycle_mass_flow_kg_s)*w.duration_s for w in q.windows),
            accumulated_gas=0.0),
        recycle_conditioning="external_heat_removal_positive; prescribed_inlet_state",
        drive_boundary="mechanical_shaft_torque; native_static_electrical_power_is_not_dynamic_drive_power",
        storage="rotor_only",net_product_mass_equals_fresh_feed=True,
        surge_dynamics_executed=False,automatic_recycle_control=False,field_validation_claim=False,
        numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("compressor dynamic receipt exceeds its byte budget")
    return body


def verify_compressor_dynamics_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("compressor dynamic receipt changed")
    q=CompressorDynamicsRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("compressor dynamic caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("compressor dynamic caller context differs")
    if evaluate_compressor_dynamics(q,c)!=record:
        raise ValueError("compressor dynamic native replay differs")
    return True


def compressor_dynamics_signal_frames(record, *, stream_id: str):
    verify_compressor_dynamics_record(record)
    q=CompressorDynamicsRequest.model_validate_json(json.dumps(record["request"]))
    units={"speed_rad_s":"rad/s","rotor_energy_J":"J","compressor_mass_flow_kg_s":"kg/s",
           "fresh_mass_flow_kg_s":"kg/s","recycle_mass_flow_kg_s":"kg/s"}
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.base.clock_id,time_s=row["time_s"],
        values={key:row[key] for key in units},units=units,valid={key:True for key in units},origin="simulation",
        operating_mode=row["window_id"]) for row in record["states"])
    body=json_value(dict(schema="pipeline_integrity.compressor_dynamics_frames.v1",source_digest=record["record_digest"],
        stream_id=stream_id,clock_id=q.base.clock_id,asset_id=q.base.asset_id,origin="simulation",
        frames_digest=digest(frames),frame_count=len(frames)))
    body["record_digest"]=digest(body)
    return frames,body
