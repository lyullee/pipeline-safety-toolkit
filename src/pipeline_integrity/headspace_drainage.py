"""Pressure feedback between native tank drainage and shared ideal-gas storage."""
from __future__ import annotations

import json
import math
from pathlib import Path

import scipy
from scipy.integrate import solve_ivp
from pydantic import Field, model_validator

from . import gas_headspace, tank_drainage, tanks
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .gas_headspace import HeadspaceRequest, headspace_thermodynamic_state, headspace_boundary_energy_rates
from .tank_drainage import DrainageRequest, drainage_outflow
from .tanks import _shape


class HeadspaceDrainageRequest(Contract):
    id: Name
    drainage: DrainageRequest
    headspace: HeadspaceRequest
    coupling_evidence_id: Name
    rigid_shell_and_interface_volume_displacement_reviewed: bool = False
    ideal_gas_fixed_composition_and_constant_liquid_properties_reviewed: bool = False
    minimum_forward_driving_pressure_pa: Positive
    sample_step_s: Positive
    relative_tolerance: Positive = Field(default=1e-8,lt=0.1)
    absolute_volume_tolerance_m3: Positive = 1e-10
    absolute_gas_mass_tolerance_kg: Positive = 1e-10
    absolute_gas_energy_tolerance_J: Positive = 1e-4
    absolute_energy_balance_tolerance_J: Nonnegative = 1e-3
    relative_energy_balance_tolerance: Nonnegative = 1e-7
    maximum_rhs_evaluations: int = Field(default=100000,ge=4,le=1000000)
    maximum_samples: int = Field(default=10000,ge=2,le=100000)
    maximum_result_bytes: int = Field(default=20000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def shared_boundary(self):
        a,b=self.drainage,self.headspace
        if (a.asset_id,a.clock_id,a.start_time_s)!=(b.asset_id,b.clock_id,b.start_time_s):
            raise ValueError("liquid and gas must share the actual asset, clock and start time")
        if any(w.volume_change_m3_s!=0 for w in b.windows):
            raise ValueError("coupled headspace windows must leave volume displacement to the liquid solver")
        duration=math.fsum(w.duration_s for w in b.windows)
        if not math.isclose(duration,a.duration_s,rel_tol=1e-12,abs_tol=1e-10):
            raise ValueError("headspace boundary windows must cover the drainage interval exactly")
        if 1+sum(math.ceil(w.duration_s/self.sample_step_s)+1 for w in b.windows)>self.maximum_samples:
            raise ValueError("coupled drainage requested samples exceed their budget")
        return self


def _sources():
    return {"coupling":file_digest(Path(__file__)),"gas":file_digest(Path(gas_headspace.__file__)),
            "drainage":file_digest(Path(tank_drainage.__file__)),"geometry":file_digest(Path(tanks.__file__)),
            "hydraulics":file_digest(Path(tank_drainage.__file__).parent/"models"/"hydraulics.py")}


def evaluate_headspace_drainage(request: HeadspaceDrainageRequest, context: RunContext):
    q=HeadspaceDrainageRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    a,b=q.drainage,q.headspace
    if (a.asset_id,a.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("coupled drainage asset or clock differs from context")
    ids=[e.id for e in c.evidence]
    required={q.coupling_evidence_id,a.geometry.evidence_id,*a.evidence_ids,*b.evidence_ids}
    if len(ids)!=len(set(ids)) or not required<=set(ids):
        raise ValueError("coupled gas/liquid/geometry review evidence must exist unambiguously")
    if not (a.quasi_steady_nonflashing_single_phase_reviewed
            and q.rigid_shell_and_interface_volume_displacement_reviewed
            and q.ideal_gas_fixed_composition_and_constant_liquid_properties_reviewed):
        raise ValueError("coupled rigid-shell, ideal-gas and nonflashing liquid applicability require review")
    before=_sources()
    _,capacity,_,level_at,owner=_shape(a.geometry,"brenth")
    if not 0<a.stop_volume_m3<a.initial_volume_m3<capacity:
        raise ValueError("coupled drainage requires a nonempty initial headspace and positive stop volume")
    if abs(b.volume_m3-(capacity-a.initial_volume_m3))>q.absolute_volume_tolerance_m3:
        raise ValueError("initial gas/liquid volumes do not partition the original tank capacity")
    energy0=b.initial_mass_kg*b.cv_J_kg_K*b.initial_temperature_K
    initial=headspace_thermodynamic_state(mass_kg=b.initial_mass_kg,internal_energy_J=energy0,volume_m3=capacity-a.initial_volume_m3,
        cv_J_kg_K=b.cv_J_kg_K,gas_constant_J_kg_K=b.gas_constant_J_kg_K)
    if not math.isclose(initial["pressure_pa_abs"],a.tank_gas_pressure_pa_abs,rel_tol=1e-10,abs_tol=1e-6):
        raise ValueError("drainage template pressure must agree with the actual initial headspace state")
    flow_budget={"count":0}
    rhs_evaluations=0
    energy_keys=("direct_heat_W","ambient_heat_W","inlet_enthalpy_W","outlet_enthalpy_W","volume_work_into_gas_W")
    y=[a.initial_volume_m3,b.initial_mass_kg,energy0,0.0,*([0.0]*5)]
    rows=[]
    event_records=[]
    time=a.start_time_s
    expected_mass=b.initial_mass_kg
    stopped=False
    def state(values):
        volume,mass,energy=map(float,values[:3])
        gasvolume=capacity-volume
        thermo=headspace_thermodynamic_state(mass_kg=mass,internal_energy_J=energy,volume_m3=gasvolume,
            cv_J_kg_K=b.cv_J_kg_K,gas_constant_J_kg_K=b.gas_constant_J_kg_K)
        level=level_at(volume)
        drive=thermo["pressure_pa_abs"]-a.downstream_pressure_pa_abs+a.density_kg_m3*a.gravity_m_s2*(level+a.tank_bottom_minus_outlet_elevation_m)
        return volume,mass,energy,gasvolume,level,drive,thermo
    initial_drive=state(y)[5]
    if initial_drive<=q.minimum_forward_driving_pressure_pa:
        raise ValueError("initial drainage pressure must exceed the explicit positive forward-flow stop threshold")
    if b.operating_pressure_bounds_pa_abs is not None:
        lo,hi=b.operating_pressure_bounds_pa_abs
        if not lo<initial["pressure_pa_abs"]<hi:
            raise ValueError("coupled initial gas pressure must lie strictly inside its operating bounds")
    for window in b.windows:
        window_start=time
        window_expected_mass=expected_mass
        def rhs(t,values):
            nonlocal rhs_evaluations
            rhs_evaluations+=1
            if rhs_evaluations>q.maximum_rhs_evaluations:
                raise ValueError("coupled drainage exceeded its RHS evaluation budget")
            volume,mass,energy,gasvolume,level,drive,thermo=state(values)
            rate=drainage_outflow(a,level_m=level,gas_pressure_pa_abs=thermo["pressure_pa_abs"],evaluation_budget=flow_budget)
            components=headspace_boundary_energy_rates(b,window,mass_kg=mass,internal_energy_J=energy,
                volume_m3=gasvolume,volume_change_m3_s=rate)
            rates=[components[key] for key in energy_keys]
            return [-rate,window.inlet_mass_flow_kg_s-window.outlet_mass_flow_kg_s,math.fsum(rates),rate,*rates]
        def stop_volume(t,values):
            return float(values[0])-a.stop_volume_m3
        def stop_drive(t,values):
            return state(values)[5]-q.minimum_forward_driving_pressure_pa
        stop_volume.terminal=True
        stop_volume.direction=-1
        stop_drive.terminal=True
        stop_drive.direction=-1
        events=[stop_volume,stop_drive]
        labels=["stop_liquid_volume","minimum_forward_driving_pressure"]
        if b.operating_pressure_bounds_pa_abs is not None:
            def pressure_lower(t,values):
                return state(values)[6]["pressure_pa_abs"]-b.operating_pressure_bounds_pa_abs[0]
            def pressure_upper(t,values):
                return b.operating_pressure_bounds_pa_abs[1]-state(values)[6]["pressure_pa_abs"]
            pressure_lower.terminal=pressure_upper.terminal=True
            pressure_lower.direction=pressure_upper.direction=-1
            events.extend((pressure_lower,pressure_upper))
            labels.extend(("minimum_headspace_pressure","maximum_headspace_pressure"))
        end=time+window.duration_s
        if end<=time:
            raise ValueError("coupled drainage clock cannot represent its requested window")
        count=math.ceil(window.duration_s/q.sample_step_s)
        samples=[time]+[time+window.duration_s*i/count for i in range(1,count)]+[end]
        if any(v<=u for u,v in zip(samples,samples[1:])):
            raise ValueError("coupled drainage output clock cannot represent distinct samples")
        step=min(a.maximum_step_s,b.maximum_step_s,a.stop_volume_m3/(4*a.maximum_volume_flow_m3_s))
        solution=solve_ivp(rhs,(time,end),y,method="DOP853",t_eval=samples,max_step=step,rtol=q.relative_tolerance,
            atol=[q.absolute_volume_tolerance_m3,q.absolute_gas_mass_tolerance_kg,q.absolute_gas_energy_tolerance_J,
                  q.absolute_volume_tolerance_m3,*([q.absolute_gas_energy_tolerance_J]*5)],events=events)
        if not solution.success:
            raise ValueError("coupled drainage integrator could not complete its supported interval")
        entries=[(float(t),solution.y[:,i]) for i,t in enumerate(solution.t)]
        for label,times,states in zip(labels,solution.t_events,solution.y_events):
            if len(times):
                event_time=float(times[0])
                if not entries or entries[-1][0]!=event_time:
                    entries.append((event_time,states[0]))
                event_records.append(dict(kind=label,time_s=event_time))
                stopped=True
        for index,(t,values) in enumerate(entries):
            if rows and index==0 and t==rows[-1]["time_s"]:
                continue
            volume,mass,energy,gasvolume,level,drive,thermo=state(values)
            flow=drainage_outflow(a,level_m=level,gas_pressure_pa_abs=thermo["pressure_pa_abs"],evaluation_budget=flow_budget)
            cumulative={key.removesuffix("_W")+"_J":float(values[i+4]) for i,key in enumerate(energy_keys)}
            residual=energy-energy0-math.fsum(cumulative.values())
            volume_residual=volume-a.initial_volume_m3+float(values[3])
            expected_at_time=window_expected_mass+(window.inlet_mass_flow_kg_s-window.outlet_mass_flow_kg_s)*(t-window_start)
            mass_residual=mass-expected_at_time
            if abs(volume_residual)>q.absolute_volume_tolerance_m3+q.relative_tolerance*max(volume,a.initial_volume_m3,abs(float(values[3]))):
                raise ValueError("coupled liquid volume balance exceeds its tolerance")
            if abs(mass_residual)>q.absolute_gas_mass_tolerance_kg+q.relative_tolerance*max(mass,abs(expected_at_time)):
                raise ValueError("coupled headspace mass balance exceeds its tolerance")
            tolerance=q.absolute_energy_balance_tolerance_J+q.relative_energy_balance_tolerance*max(energy0,abs(energy),*[abs(v) for v in cumulative.values()],1.0)
            if abs(residual)>tolerance:
                raise ValueError("coupled sampled gas energy ledger exceeds its tolerance")
            rows.append(dict(window_id=window.id,time_s=t,liquid_volume_m3=volume,liquid_level_m=level,
                gas_mass_kg=mass,gas_internal_energy_J=energy,gas_volume_m3=gasvolume,**thermo,
                liquid_outflow_m3_s=flow,driving_pressure_pa=drive,discharged_volume_m3=float(values[3]),
                gas_boundary_energy_J=cumulative,gas_energy_residual_J=residual,
                liquid_volume_residual_m3=volume_residual,gas_mass_residual_kg=mass_residual))
        if not entries:
            raise ValueError("coupled drainage produced no supported output state")
        time,y=entries[-1][0],[float(v) for v in entries[-1][1]]
        expected_mass=window_expected_mass+(window.inlet_mass_flow_kg_s-window.outlet_mass_flow_kg_s)*(time-window_start)
        if stopped:
            break
    if _sources()!=before:
        raise ValueError("coupled gas/drainage implementation changed during evaluation")
    body=json_value(dict(schema="pipeline_integrity.headspace_drainage.v1",status="computed_diagnostic",request=q,context=c,
        states=rows,events=event_records,completed_requested_duration=math.isclose(time,a.start_time_s+a.duration_s,rel_tol=1e-12,abs_tol=1e-10),geometry_owner=owner,
        rhs_evaluations=rhs_evaluations,hydraulic_evaluations=flow_budget["count"],source_sha256=before,
        numerical_backend=dict(name="scipy.integrate.solve_ivp:DOP853",version=scipy.__version__),
        gas_pressure_feedback_to_liquid_flow=True,liquid_displacement_feedback_to_gas_work=True,
        liquid_thermal_storage_implemented=False,reverse_flow_implemented=False,
        field_validation_claim=False,numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("coupled gas/drainage retained receipt exceeds its byte budget")
    return body


def verify_headspace_drainage_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("coupled gas/drainage receipt changed")
    q=HeadspaceDrainageRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("coupled gas/drainage caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("coupled gas/drainage caller context differs")
    if evaluate_headspace_drainage(q,c)!=record:
        raise ValueError("coupled gas/drainage replay differs")
    return True


def headspace_drainage_signal_frames(record, *, stream_id: str):
    verify_headspace_drainage_record(record)
    q=HeadspaceDrainageRequest.model_validate_json(json.dumps(record["request"]))
    units={"liquid_volume_m3":"m^3","liquid_level_m":"m","gas_volume_m3":"m^3","gas_mass_kg":"kg",
           "temperature_K":"K","pressure_pa_abs":"Pa","liquid_outflow_m3_s":"m^3/s"}
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.drainage.clock_id,time_s=row["time_s"],
        values={key:row[key] for key in units},units=units,valid={key:True for key in units},origin="simulation",
        operating_mode=row["window_id"]) for row in record["states"])
    body=json_value(dict(schema="pipeline_integrity.headspace_drainage_frames.v1",source_digest=record["record_digest"],
        stream_id=stream_id,clock_id=q.drainage.clock_id,asset_id=q.drainage.asset_id,origin="simulation",
        pressure_reference="absolute",frames_digest=digest(frames),frame_count=len(frames)))
    body["record_digest"]=digest(body)
    return frames,body
