"""Inertial shaft with reviewed signed maps and an explicit imposed-flow boundary.

The midpoint torque equation supplies its own discrete kinetic-energy ledger.
Flow is an input; this model does not solve a pipe network or a surge cycle.
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest


class SignedRotatingMap(Contract):
    id: Name
    speed_rad_s: tuple[float, ...] = Field(min_length=2,max_length=200)
    volume_flow_m3_s: tuple[float, ...] = Field(min_length=2,max_length=200)
    resisting_torque_N_m: tuple[tuple[float, ...], ...]
    pressure_rise_Pa: tuple[tuple[float, ...], ...]
    evidence_id: Name
    signed_axes_and_torque_pressure_conventions_reviewed: bool = False

    @model_validator(mode="after")
    def shape(self):
        for axis in (self.speed_rad_s,self.volume_flow_m3_s):
            if any(b<=a for a,b in zip(axis,axis[1:])):
                raise ValueError("signed map axes must increase without extrapolation")
        for table in (self.resisting_torque_N_m,self.pressure_rise_Pa):
            if len(table)!=len(self.speed_rad_s) or any(len(row)!=len(self.volume_flow_m3_s) for row in table):
                raise ValueError("signed map rows are speed and columns are volume flow")
        return self


class ShaftWindow(Contract):
    id: Name
    duration_s: Positive
    volume_flow_m3_s: float
    drive_torque_N_m: float
    drive_speed_feedback_N_m_s: Nonnegative = 0.0
    viscous_drag_N_m_s: Nonnegative = 0.0
    evidence_id: Name


class RotatingShaftRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    shaft_id: Name
    inlet_port_id: Name
    outlet_port_id: Name
    inertia_kg_m2: Positive
    initial_speed_rad_s: float
    performance_map: SignedRotatingMap
    windows: tuple[ShaftWindow, ...] = Field(min_length=1,max_length=1000)
    maximum_step_s: Positive
    maximum_steps: int = Field(default=10000,ge=1,le=200000)
    maximum_root_evaluations: int = Field(default=100000,ge=4,le=2000000)
    maximum_result_bytes: int = Field(default=20000000,ge=1000,le=200000000)
    speed_root_tolerance_rad_s: Positive = 1e-10
    torque_residual_tolerance_N_m: Nonnegative = 1e-7
    energy_residual_tolerance_J: Nonnegative = 1e-5
    map_power_loss_tolerance_W: Nonnegative = 1e-6
    map_power_loss_nonnegative_reviewed: bool = False
    imposed_flow_and_no_network_feedback_reviewed: bool = False
    rotor_and_drive_evidence_id: Name

    @model_validator(mode="after")
    def identity(self):
        if self.inlet_port_id==self.outlet_port_id or len({w.id for w in self.windows})!=len(self.windows):
            raise ValueError("distinct ports and unique operating windows are required")
        if not self.performance_map.speed_rad_s[0]<=self.initial_speed_rad_s<=self.performance_map.speed_rad_s[-1]:
            raise ValueError("initial shaft speed lies outside the supplied map")
        if sum(math.ceil(w.duration_s/self.maximum_step_s) for w in self.windows)>self.maximum_steps:
            raise ValueError("requested shaft integration exceeds its step budget")
        return self


def _interpolate(axis,values,x):
    if not axis[0]<=x<=axis[-1]:
        raise ValueError("rotating-map support does not cover the requested operating point")
    i=min(max(bisect.bisect_right(axis,x)-1,0),len(axis)-2)
    fraction=(x-axis[i])/(axis[i+1]-axis[i])
    return values[i]+fraction*(values[i+1]-values[i])


def rotating_map_point(performance_map: SignedRotatingMap, speed_rad_s: float, volume_flow_m3_s: float):
    """Bilinear interpolation on supplied signed coordinates; no affinity scaling."""
    m=SignedRotatingMap.model_validate(performance_map.model_dump(mode="python"))
    result={}
    for key,table in (("resisting_torque_N_m",m.resisting_torque_N_m),("pressure_rise_Pa",m.pressure_rise_Pa)):
        column=[_interpolate(m.volume_flow_m3_s,row,volume_flow_m3_s) for row in table]
        result[key]=_interpolate(m.speed_rad_s,column,speed_rad_s)
    return result


def evaluate_rotating_shaft(request: RotatingShaftRequest, context: RunContext):
    q=RotatingShaftRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("shaft asset and clock differ from their run context")
    ids=[e.id for e in c.evidence]
    required={q.rotor_and_drive_evidence_id,q.performance_map.evidence_id,*[w.evidence_id for w in q.windows]}
    if len(ids)!=len(set(ids)) or not required<=set(ids):
        raise ValueError("shaft, map and operating-window evidence must exist unambiguously")
    if not (q.performance_map.signed_axes_and_torque_pressure_conventions_reviewed
            and q.map_power_loss_nonnegative_reviewed and q.imposed_flow_and_no_network_feedback_reviewed):
        raise ValueError("map signs, power losses and imposed-flow applicability require explicit review")
    try:
        import scipy
        from scipy.optimize import brentq
    except ImportError as exc:
        raise ImportError("Rotating-shaft calculations require the engineering extra (scipy)") from exc

    source=file_digest(Path(__file__))
    m=q.performance_map
    omega=q.initial_speed_rad_s
    t=0.0
    drive_work=load_work=drag_work=hydraulic_work=0.0
    evaluations=0
    rows=[dict(time_s=t,speed_rad_s=omega,rotor_energy_J=0.5*q.inertia_kg_m2*omega**2)]
    steps=[]
    for window in q.windows:
        flow=window.volume_flow_m3_s
        torques=[_interpolate(m.volume_flow_m3_s,row,flow) for row in m.resisting_torque_N_m]
        slopes=[(b-a)/(v-u) for a,b,u,v in zip(torques,torques[1:],m.speed_rad_s,m.speed_rad_s[1:])]
        count=math.ceil(window.duration_s/q.maximum_step_s)
        start=t
        for index in range(count):
            target=start+window.duration_s*(index+1)/count
            dt=target-t
            if dt<=0:
                raise ValueError("shaft clock cannot represent the requested step")
            if any(q.inertia_kg_m2/dt+0.5*(window.drive_speed_feedback_N_m_s+window.viscous_drag_N_m_s+s)<=0 for s in slopes):
                raise ValueError("shaft midpoint residual is not uniquely increasing; reduce maximum_step_s")
            previous=omega
            def residual(candidate):
                nonlocal evaluations
                evaluations+=1
                if evaluations>q.maximum_root_evaluations:
                    raise ValueError("shaft root-evaluation budget exceeded")
                midpoint=0.5*(previous+candidate)
                load=_interpolate(m.speed_rad_s,torques,midpoint)
                drive=window.drive_torque_N_m-window.drive_speed_feedback_N_m_s*midpoint
                return q.inertia_kg_m2*(candidate-previous)/dt-drive+load+window.viscous_drag_N_m_s*midpoint
            lo,hi=m.speed_rad_s[0],m.speed_rad_s[-1]
            lower,upper=residual(lo),residual(hi)
            if lower>0 or upper<0:
                raise ValueError("next shaft state would leave the supplied signed map; no clipping is applied")
            omega=lo if lower==0 else hi if upper==0 else brentq(residual,lo,hi,xtol=q.speed_root_tolerance_rad_s,
                                                              rtol=4*2.220446049250313e-16,maxiter=200)
            error=residual(omega)
            midpoint=0.5*(previous+omega)
            point=rotating_map_point(m,midpoint,flow)
            drive=window.drive_torque_N_m-window.drive_speed_feedback_N_m_s*midpoint
            drive_energy=drive*midpoint*dt
            load_energy=point["resisting_torque_N_m"]*midpoint*dt
            drag_energy=window.viscous_drag_N_m_s*midpoint**2*dt
            fluid_energy=point["pressure_rise_Pa"]*flow*dt
            kinetic_change=0.5*q.inertia_kg_m2*(omega-previous)*(omega+previous)
            energy_error=drive_energy-load_energy-drag_energy-kinetic_change
            power_loss=(load_energy-fluid_energy)/dt
            if power_loss < -q.map_power_loss_tolerance_W:
                raise ValueError("supplied torque/pressure map creates fluid power beyond shaft work")
            if abs(error)>q.torque_residual_tolerance_N_m or abs(energy_error)>q.energy_residual_tolerance_J:
                raise ValueError("shaft step exceeds its explicit torque or energy residual tolerance")
            drive_work+=drive_energy
            load_work+=load_energy
            drag_work+=drag_energy
            hydraulic_work+=fluid_energy
            steps.append(dict(window_id=window.id,start_time_s=t,end_time_s=target,
                midpoint_speed_rad_s=midpoint,volume_flow_m3_s=flow,**point,
                drive_work_J=drive_energy,load_work_J=load_energy,drag_work_J=drag_energy,
                hydraulic_work_J=fluid_energy,map_loss_power_W=power_loss,
                torque_residual_N_m=error,energy_residual_J=energy_error))
            t=target
            rows.append(dict(time_s=t,speed_rad_s=omega,rotor_energy_J=0.5*q.inertia_kg_m2*omega**2))
    initial=rows[0]["rotor_energy_J"]
    final=rows[-1]["rotor_energy_J"]
    drive_work=math.fsum(step["drive_work_J"] for step in steps)
    load_work=math.fsum(step["load_work_J"] for step in steps)
    drag_work=math.fsum(step["drag_work_J"] for step in steps)
    hydraulic_work=math.fsum(step["hydraulic_work_J"] for step in steps)
    global_residual=math.fsum((initial,drive_work,-load_work,-drag_work,-final))
    if abs(global_residual)>q.energy_residual_tolerance_J:
        raise ValueError("shaft accumulated energy residual exceeds its declared tolerance")
    if file_digest(Path(__file__))!=source:
        raise ValueError("shaft implementation changed during evaluation")
    body=json_value(dict(schema="pipeline_integrity.rotating_shaft.v1",status="computed_diagnostic",
        request=q,context=c,states=rows,steps=steps,root_evaluations=evaluations,
        energy_ledger_J=dict(initial_rotor=initial,drive=drive_work,load=load_work,drag=drag_work,
                            hydraulic=hydraulic_work,map_loss=load_work-hydraulic_work,final_rotor=final,
                            residual=global_residual),
        numerical_backend=dict(name="scipy.optimize.brentq",version=scipy.__version__),
        implementation_sha256=source,flow_direction="positive_inlet_to_outlet",
        pressure_rise_definition="outlet_minus_inlet",shaft_load_definition="positive_torque_opposes_positive_rotation",
        imposed_flow=True,simultaneous_network_coupling=False,field_validation_claim=False,
        numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("shaft receipt exceeds its retained-result byte budget")
    return body


def verify_rotating_shaft_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("shaft record changed")
    q=RotatingShaftRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("shaft caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("shaft caller context differs")
    if evaluate_rotating_shaft(q,c)!=record:
        raise ValueError("shaft replay differs")
    return True


def rotating_shaft_signal_frames(record, *, stream_id: str):
    """Publish state samples without interpreting the midpoint work as point power."""
    verify_rotating_shaft_record(record)
    q=RotatingShaftRequest.model_validate_json(json.dumps(record["request"]))
    units={"speed_rad_s":"rad/s","rotor_energy_J":"J"}
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
        values={key:row[key] for key in units},units=units,valid={key:True for key in units},
        origin="simulation",operating_mode="prescribed_flow_inertial_shaft") for row in record["states"])
    body=json_value(dict(schema="pipeline_integrity.rotating_shaft_frames.v1",source_digest=record["record_digest"],
        stream_id=stream_id,asset_id=q.asset_id,clock_id=q.clock_id,origin="simulation",
        frames_digest=digest(frames),frame_count=len(frames),interval_work_retained_in_source=True))
    body["record_digest"]=digest(body)
    return frames,body
