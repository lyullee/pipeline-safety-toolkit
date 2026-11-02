"""Ideal opening/reseat relay around original pure-hydrogen tank integration.

The relay is held constant during every original RK step. Pressure crossings
are bracketed by native states and reintegrated from the same conserved state;
no relay memory is changed inside an RHS callback. Switching occurs at the
crossed-side endpoint of a declared numerical event bracket. This requires a
reviewed no-hidden-crossing assumption within each scan step, not dense output
or a physical actuator-delay model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import tank_breathing as owner
from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .tank_breathing import TankBreathingRequest, TankBreathingState

REVIEWS = (
    "ideal_instantaneous_binary_relay_without_actuator_dynamics",
    "source_reviewed_opening_reseat_and_initial_relay_memory",
    "monotonic_event_pressure_and_no_hidden_crossings_within_each_scan_step",
    "identical_conserved_initial_state_for_native_event_reintegration",
    "phase_selective_outward_vent_and_preserved_closed_leakage",
    "event_pressure_time_and_source_clock_resolution",
    "native_integrated_mass_energy_ledgers_across_event_splits",
)


class HystereticTankRequest(Contract):
    id: Name
    base: TankBreathingRequest
    opening_pressure_pa_abs: Positive
    reseat_pressure_pa_abs: Positive
    initial_relay_state: Literal["closed", "open"]
    open_position: Fraction = Field(default=1.0, gt=0)
    minimum_native_duration_s: Positive = Field(default=1e-10, gt=1e-12)
    event_time_absolute_tolerance_s: Positive = 1e-8
    clock_representation_absolute_tolerance_s: Nonnegative = 1e-9
    event_pressure_absolute_tolerance_Pa: Positive = 10.0
    event_pressure_relative_tolerance: Nonnegative = Field(default=1e-8, le=1e-3)
    maximum_event_iterations: int = Field(default=80, ge=1, le=500)
    maximum_owner_evaluations: int = Field(default=120000, ge=2, le=1000000)
    maximum_native_steps: int = Field(default=100000, ge=1, le=1000000)
    maximum_native_RK_stage_work_units: int = Field(default=400000, ge=4, le=4000000)
    maximum_switch_events: int = Field(default=10000, ge=1, le=100000)
    maximum_accepted_segments: int = Field(default=100000, ge=1, le=1000000)
    maximum_retained_result_bytes: int = Field(default=256000000, ge=1000, le=1000000000)
    global_mass_absolute_tolerance_kg: Nonnegative = 1e-8
    global_energy_absolute_tolerance_J: Nonnegative = 1e-3
    global_relative_balance_tolerance: Nonnegative = Field(default=1e-8, le=1e-3)
    relay_mapping_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    controller_owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def relay_domain(self):
        if self.base.vent is None or self.base.vent.mode!="pressure_relief_hysteresis":
            raise ValueError("hysteretic wrapper requires the explicit pressure_relief_hysteresis source mode")
        if self.reseat_pressure_pa_abs>=self.opening_pressure_pa_abs:
            raise ValueError("reseat pressure must be below opening pressure")
        largest_tolerance=self.event_pressure_absolute_tolerance_Pa+self.event_pressure_relative_tolerance*self.opening_pressure_pa_abs
        if 4*largest_tolerance>=self.opening_pressure_pa_abs-self.reseat_pressure_pa_abs:
            raise ValueError("relay hysteresis band must exceed the declared event-pressure resolution")
        if self.event_time_absolute_tolerance_s<self.minimum_native_duration_s:
            raise ValueError("event tolerance cannot demand time resolution below the allowed native trial duration")
        if self.base.closures is not None and not self.base.closures.minimum_pressure_Pa<self.reseat_pressure_pa_abs<self.opening_pressure_pa_abs<self.base.closures.maximum_pressure_Pa:
            raise ValueError("relay thresholds must lie strictly inside the native tank pressure-search domain")
        if len(set(self.evidence_ids))!=len(self.evidence_ids) or set(self.reviews)!=set(REVIEWS) or set(self.review_evidence_ids)-set(REVIEWS):
            raise ValueError("distinct relay evidence and every named applicability review are required")
        for window in self.base.windows:
            if window.duration_s is not None and window.duration_s<self.minimum_native_duration_s:
                raise ValueError("source operating window is below the allowed native integration resolution")
            if window.timestep_s is not None and window.timestep_s<self.minimum_native_duration_s:
                raise ValueError("source scan timestep is below the allowed native integration resolution")
        if all(w.duration_s is not None for w in self.base.windows):
            total=math.fsum(w.duration_s for w in self.base.windows)
            if not math.isfinite(total) or total>self.base.maximum_duration_s or not math.isfinite(self.base.start_time_s+total):
                raise ValueError("hysteretic source windows exceed the original duration/clock budget")
        return self


class _NativeTankWithheld(RuntimeError):
    def __init__(self, record):
        self.record=record
        super().__init__("original tank owner withheld a relay trial")


def hysteretic_tank_metadata():
    return dict(implementation_sha256=file_digest(Path(__file__)),
                tank_facade_source_sha256=file_digest(Path(owner.__file__)),
                shared_source_sha256={name:file_digest(Path(__file__).with_name(name+".py"))
                                      for name in ("contracts","datasets")},
                relay="held_constant_per_native_step_with_conserved_state_event_reintegration")


def hysteretic_tank_boundary(request):
    q=request
    return dict(request_digest=digest(q),base_request_digest=digest(q.base),asset_id=q.base.asset_id,
                event_id=q.base.event_id,clock_id=q.base.clock_id,start_time_s=q.base.start_time_s,
                opening_pressure_pa_abs=q.opening_pressure_pa_abs,reseat_pressure_pa_abs=q.reseat_pressure_pa_abs,
                relay_mapping_evidence_id=q.relay_mapping_evidence_id)


def bind_hysteretic_tank_context(request: HystereticTankRequest,context: RunContext):
    q=HystereticTankRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    data=c.model_dump(mode="json")
    data["metadata"]={**c.metadata,"hysteretic_tank_boundary":hysteretic_tank_boundary(q)}
    result=RunContext.model_validate_json(json.dumps(data))
    _policy(q,result)
    return result


def _derive(q,c,window_index,relay,start_time, *, duration=None,state=None,kind="preflight"):
    data=q.base.model_dump(mode="json")
    original=q.base.windows[window_index]
    window=original.model_dump(mode="json")
    window["id"]=f"{q.id}:source-window:{window_index}"
    window["vent_opening"]=q.open_position if relay=="open" else 0.0
    if duration is not None:
        window["duration_s"]=duration
        window["timestep_s"]=duration
    data["windows"]=[window]
    data["start_time_s"]=start_time
    vent=data["vent"]
    vent["mode"]="closed" if relay=="closed" and q.base.vent.closed_leakage_CdA_m2==0 else "specified_opening"
    if state is not None:
        data["initial_state"]=state.model_dump(mode="json")
        for key in ("initial_pressure_pa_abs","initial_liquid_volume_fraction",
                    "initial_lower_wall_temperature_K","initial_upper_wall_temperature_K"):
            data[key]=None
    data["evidence_ids"]=list(dict.fromkeys((*q.base.evidence_ids,*q.evidence_ids)))
    derived=TankBreathingRequest.model_validate_json(json.dumps(data,allow_nan=False))
    if derived.owner_pins!=q.base.owner_pins:
        raise ValueError("relay wrapper changed original native/property source pins")
    context=c.model_dump(mode="json")
    context["metadata"]={**c.metadata,"tank_breathing_boundary":owner.boundary_metadata(derived),
        "hysteretic_tank_trial":dict(parent_request_digest=digest(q),source_window_index=window_index,
             source_window_id=original.id,relay_state=relay,trial_kind=kind,
             replaces_source_window_opening=True,relay_mapping_evidence_id=q.relay_mapping_evidence_id,
             initial_conserved_state_digest=None if state is None else digest(state))}
    return derived,RunContext.model_validate_json(json.dumps(context,allow_nan=False))


def _policy(q,c):
    if (q.base.asset_id,q.base.event_id,q.base.clock_id)!=(c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("hysteretic tank asset/event/clock differs from context")
    if c.metadata.get("hysteretic_tank_boundary")!=hysteretic_tank_boundary(q):
        raise ValueError("hysteretic context must bind the exact thresholds, source windows and mapping")
    evidence={e.id:e for e in c.evidence}
    selected=set(q.evidence_ids)
    if len(evidence)!=len(c.evidence) or not selected<=set(evidence) or not {q.relay_mapping_evidence_id,*q.review_evidence_ids.values()}<=selected:
        raise ValueError("relay review/mapping evidence must be selected and present in context")
    mapping=evidence[q.relay_mapping_evidence_id]
    if mapping.sha256 is None or mapping.origin=="synthetic":
        raise ValueError("relay source/clock mapping requires reviewed source-hash-pinned evidence")
    issues=["review/evidence required: "+key for key in REVIEWS if not q.reviews[key] or key not in q.review_evidence_ids]
    for index in range(len(q.base.windows)):
        for relay in ("closed","open"):
            derived,local=_derive(q,c,index,relay,q.base.start_time_s)
            issues.extend(f"source window {index} {relay}: "+item for item in owner._policy(derived,local))
    if not q.base.owner_pins or not q.controller_owner_pins:
        issues.append("reviewed original tank/property and controller source pins are required")
    if q.base.vent.discharge_coefficient is not None and q.base.vent.discharge_coefficient<=0:
        issues.append("native vent discharge coefficient must be positive")
    return issues


def _check_record(record,request,context, *, initialization=False):
    schema="pipeline_integrity.tank_breathing_initial.v1" if initialization else "pipeline_integrity.tank_breathing.v1"
    if record.get("schema")!=schema or record.get("record_digest")!=digest({k:v for k,v in record.items() if k!="record_digest"}):
        raise ValueError("original tank receipt schema or digest differs")
    if record.get("request")!=request.model_dump(mode="json") or record.get("context")!=context.model_dump(mode="json"):
        raise ValueError("original tank trial changed its request, conserved state or context")
    if record.get("implementation_sha256")!=file_digest(Path(owner.__file__)):
        raise ValueError("original tank facade source differs from its receipt")
    if record["status"]!="computed_diagnostic":
        raise _NativeTankWithheld(record)
    if record.get("native_called") is not True or record.get("provider")!=request.owner_pins:
        raise ValueError("original tank result lacks its reviewed native/property execution binding")
    if not isinstance(record.get("initial_state"),dict) or not isinstance(record.get("initial_thermo"),dict):
        raise ValueError("original tank result lacks explicit initial conserved and thermodynamic states")


def evaluate_hysteretic_tank(request: HystereticTankRequest,context: RunContext):
    q=HystereticTankRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    body=dict(schema="pipeline_integrity.hysteretic_tank.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="needs_data",issues=_policy(q,c),
              initialization_record=None,accepted_segments=[],trial_log=[],relay_events=[],history=[],values=None,
              numerical_validation_completed=False,physical_validation_claim=False,
              relay_model="instantaneous_binary_memory_with_bracket_high_endpoint_switching",
              actuator_dynamics_modelled=False,dense_pressure_extrema_resolved=False,
              mixed_gas_atmospheric_breathing_modelled=False,
              implementation_sha256=file_digest(Path(__file__)))
    if not body["issues"]:
        before=hysteretic_tank_metadata()
        if before!=q.controller_owner_pins:
            raise ValueError("hysteretic controller/facade source differs from reviewed pins")
        try:
            _solve(q,c,body)
        except _NativeTankWithheld as error:
            status=error.record["status"]
            body.update(status=status if status!="computed_diagnostic" else "diagnostic_rejected",
                        issues=["original tank withheld relay initialization or integration",*error.record.get("notes",())],
                        withheld_native_trial=error.record,values=None)
        except (ValueError,RuntimeError,ArithmeticError,TypeError) as error:
            body.update(status="out_of_scope",issues=[f"{type(error).__name__}: {error}"],values=None)
        if hysteretic_tank_metadata()!=before:
            raise ValueError("relay controller or original tank facade changed during calculation")
        body["controller_owner_pins"]=before
    body=json_value(body)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
        raise ValueError("hysteretic tank receipt exceeds its retained-result byte budget")
    return body


def _solve(q,c,body):
    calls=steps=stages=retained=0
    relay=q.initial_relay_state
    first,local=_derive(q,c,0,relay,q.base.start_time_s,kind="initialization")
    calls+=1
    body["execution_budget"]=dict(owner_evaluations_started=calls,native_steps_reserved=0,
        native_RK_stage_work_units_reserved=0,
        work_definition="initial conserved-state recovery only; no native time integration performed")
    initialization=owner.initialize_tank_breathing(first,local)
    _check_record(initialization,first,local,initialization=True)
    body["initialization_record"]=initialization
    retained+=len(json.dumps(initialization,ensure_ascii=False,allow_nan=False).encode("utf-8"))
    if retained>q.maximum_retained_result_bytes:
        raise ValueError("tank initialization receipt exceeds the relay retained-result budget")
    state=TankBreathingState.model_validate_json(json.dumps(initialization["initial_state"]))
    pressure=initialization["initial_thermo"]["pressure_Pa"]
    initial=state
    initial_mass=initial.liquid_mass_kg+initial.vapor_mass_kg
    initial_energy=owner._total_energy(initial,q.base.closures)
    durations=[]
    clock_time=q.base.start_time_s
    boundary_mass=boundary_energy=vented=transfer=0.0

    def pressure_tolerance(threshold):
        return q.event_pressure_absolute_tolerance_Pa+q.event_pressure_relative_tolerance*threshold

    def current_time():
        return clock_time

    def event_record(kind,new_relay,at_pressure,bracket=None,window_index=None):
        nonlocal relay
        if len(body["relay_events"])>=q.maximum_switch_events:
            raise ValueError("hysteretic relay exceeded its switch-event budget")
        body["relay_events"].append(dict(kind=kind,time_s=current_time(),pressure_pa_abs=at_pressure,
            previous_relay_state=relay,new_relay_state=new_relay,time_bracket_s=bracket,
            source_window_index=window_index,event_time_exact=False))
        relay=new_relay

    if relay=="closed" and pressure>=q.opening_pressure_pa_abs:
        event_record("initial_opening", "open",pressure)
    elif relay=="open" and pressure<=q.reseat_pressure_pa_abs:
        event_record("initial_reseat", "closed",pressure)
    body["history"].append(dict(time_s=current_time(),state=state.model_dump(mode="json"),pressure_pa_abs=pressure,
                                relay_state=relay,cumulative_boundary_mass_kg=0.0,cumulative_boundary_energy_J=0.0,
                                cumulative_vented_mass_kg=0.0,cumulative_net_phase_transfer_kg=0.0))

    def run(duration,window_index,start_state,start_pressure,start_time,relay_state,kind):
        nonlocal calls,steps,stages,retained
        if duration<q.minimum_native_duration_s or start_time+duration<=start_time:
            raise ValueError("relay event requires a native duration/source clock finer than the reviewed minimum")
        if calls>=q.maximum_owner_evaluations or steps+1>q.maximum_native_steps or stages+4>q.maximum_native_RK_stage_work_units:
            raise ValueError("hysteretic tank native evaluation/RK-step work budget would be exceeded")
        derived,context=_derive(q,c,window_index,relay_state,start_time,duration=duration,state=start_state,kind=kind)
        calls+=1
        steps+=1
        stages+=4
        body["execution_budget"]=dict(owner_evaluations_started=calls,native_steps_reserved=steps,
            native_RK_stage_work_units_reserved=stages,
            work_definition="one original RK4 step per integration trial; source initialization/property internals are not stage-work estimates")
        record=owner.evaluate_tank_breathing(derived,context)
        _check_record(record,derived,context)
        if record["initial_state"]!=start_state.model_dump(mode="json"):
            raise ValueError("native relay trial did not restart from the identical conserved initial state")
        restart_pressure=record["initial_thermo"]["pressure_Pa"]
        if abs(restart_pressure-start_pressure)>pressure_tolerance(start_pressure):
            raise ValueError("native thermodynamic restart changed the reviewed pressure branch")
        threshold=q.opening_pressure_pa_abs if relay_state=="closed" else q.reseat_pressure_pa_abs
        direction=1.0 if relay_state=="closed" else -1.0
        if direction*(start_pressure-threshold)<0<=direction*(restart_pressure-threshold):
            raise ValueError("native pressure restart changes relay-threshold membership; the event is unresolved at the supplied pressure tolerance")
        history=record.get("history")
        if not isinstance(history,list) or len(history)!=1 or not history[0]["within_budget"]:
            raise ValueError("relay source integration must return exactly one accepted native RK step")
        if record["values"]["actual_simulated_duration_s"]!=duration or record["values"]["unintegrated_tail_s"]!=0:
            raise ValueError("relay source integration left an unintegrated duration tail")
        row=history[0]
        if row["time_s"]!=start_time+duration:
            raise ValueError("native relay endpoint clock differs from its source duration")
        p=row["thermo"]["pressure_Pa"]
        log=dict(kind=kind,source_window_index=window_index,relay_state=relay_state,start_time_s=start_time,
                 duration_s=duration,initial_conserved_state_digest=digest(start_state),
                 initial_pressure_pa_abs=start_pressure,restart_pressure_difference_Pa=restart_pressure-start_pressure,
                 final_pressure_pa_abs=p,native_record_digest=record["record_digest"],
                 derived_request_digest=digest(derived),derived_context_digest=digest(context))
        retained+=len(json.dumps(log,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        if retained>q.maximum_retained_result_bytes or len(json.dumps(record,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
            raise ValueError("relay trial/source receipt exceeds the retained-result budget")
        body["trial_log"].append(log)
        return record

    def accept(record,duration,window_index,reason):
        nonlocal state,pressure,boundary_mass,boundary_energy,vented,transfer,retained,clock_time
        if len(body["accepted_segments"])>=min(q.maximum_accepted_segments,q.base.maximum_steps):
            raise ValueError("hysteretic tank exceeded its accepted-segment budget")
        row=record["history"][-1]
        candidate=TankBreathingState.model_validate_json(json.dumps(row["state"]))
        item=dict(source_window_index=window_index,reason=reason,relay_state=relay,
                  duration_s=duration,native_record=record)
        retained+=len(json.dumps(item,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        if retained>q.maximum_retained_result_bytes:
            raise ValueError("accepted relay/native histories exceed the retained-result budget")
        boundary_mass+=row["cumulative_boundary_mass_kg"]
        boundary_energy+=row["cumulative_boundary_energy_J"]
        vented+=record["values"]["cumulative_vented_mass_kg"]
        transfer+=record["values"]["cumulative_net_liquid_to_vapor_transfer_kg"]
        durations.append(duration)
        clock_time=row["time_s"]
        clock_residual=clock_time-(q.base.start_time_s+math.fsum(durations))
        if abs(clock_residual)>q.clock_representation_absolute_tolerance_s:
            raise ValueError("event-split native clock accumulation exceeds its declared representation tolerance")
        state=candidate
        pressure=row["thermo"]["pressure_Pa"]
        body["accepted_segments"].append(item)
        body["history"].append(dict(time_s=current_time(),state=state.model_dump(mode="json"),pressure_pa_abs=pressure,
            relay_state=relay,cumulative_boundary_mass_kg=boundary_mass,cumulative_boundary_energy_J=boundary_energy,
            cumulative_vented_mass_kg=vented,cumulative_net_phase_transfer_kg=transfer))
        body["history"][-1]["clock_representation_residual_s"]=clock_residual

    for window_index,window in enumerate(q.base.windows):
        accepted_window_durations=[]
        while math.fsum(accepted_window_durations)<window.duration_s:
            remaining=window.duration_s-math.fsum(accepted_window_durations)
            duration=min(window.timestep_s,remaining)
            start_time=current_time()
            scan=run(duration,window_index,state,pressure,start_time,relay,"scan")
            endpoint_pressure=scan["history"][-1]["thermo"]["pressure_Pa"]
            threshold=q.opening_pressure_pa_abs if relay=="closed" else q.reseat_pressure_pa_abs
            direction=1.0 if relay=="closed" else -1.0
            lo_value=direction*(pressure-threshold)
            hi_value=direction*(endpoint_pressure-threshold)
            if hi_value<0:
                accept(scan,duration,window_index,"no_sampled_relay_crossing")
                accepted_window_durations.append(duration)
                continue
            if lo_value>=0:
                raise ValueError("relay memory was not stabilized before a source integration step")
            lo,hi=0.0,duration
            high_record=scan
            limit=pressure_tolerance(threshold)
            for _ in range(q.maximum_event_iterations):
                if hi-lo<=q.event_time_absolute_tolerance_s and abs(hi_value)<=limit:
                    break
                midpoint=(lo+hi)/2
                if midpoint<=lo or midpoint>=hi:
                    raise ValueError("native pressure crossing cannot be resolved on the declared floating-point clock")
                trial=run(midpoint,window_index,state,pressure,start_time,relay,"event_reintegration")
                trial_pressure=trial["history"][-1]["thermo"]["pressure_Pa"]
                value=direction*(trial_pressure-threshold)
                if value<lo_value-limit or value>hi_value+limit:
                    raise ValueError("native event-reintegration pressure violates the reviewed monotone crossing bracket")
                if value>=0:
                    hi,hi_value,high_record=midpoint,value,trial
                else:
                    lo,lo_value=midpoint,value
            if hi-lo>q.event_time_absolute_tolerance_s or abs(hi_value)>limit:
                raise ValueError("native pressure-event bracket exceeds its time/pressure tolerance or iteration budget")
            old_relay=relay
            accept(high_record,hi,window_index,"resolved_sampled_pressure_crossing")
            accepted_window_durations.append(hi)
            event_record("opening" if old_relay=="closed" else "reseat",
                         "open" if old_relay=="closed" else "closed",pressure,
                         bracket=[start_time+lo,start_time+hi],window_index=window_index)
            body["history"][-1]["relay_state_after_event"]=relay
    final_mass=state.liquid_mass_kg+state.vapor_mass_kg
    final_energy=owner._total_energy(state,q.base.closures)
    mass_residual=final_mass-initial_mass-boundary_mass
    energy_residual=final_energy-initial_energy-boundary_energy
    mass_limit=q.global_mass_absolute_tolerance_kg+q.global_relative_balance_tolerance*max(abs(final_mass-initial_mass),abs(boundary_mass))
    energy_limit=q.global_energy_absolute_tolerance_J+q.global_relative_balance_tolerance*max(abs(final_energy-initial_energy),abs(boundary_energy))
    requested_duration=math.fsum(w.duration_s for w in q.base.windows)
    elapsed=math.fsum(durations)
    duration_residual=requested_duration-elapsed
    clock_residual=current_time()-(q.base.start_time_s+elapsed)
    valid=(abs(mass_residual)<=mass_limit and abs(energy_residual)<=energy_limit
           and abs(duration_residual)<=q.clock_representation_absolute_tolerance_s
           and abs(clock_residual)<=q.clock_representation_absolute_tolerance_s)
    accepted_pressures=[row["pressure_pa_abs"] for row in body["history"]]
    limits=q.base.declared_pressure_limits_pa_abs
    body.update(status="computed_diagnostic" if valid else "diagnostic_rejected",
        issues=[] if valid else ["global event-split mass/energy/duration budget failed"],
        values=dict(final_state=state.model_dump(mode="json"),final_pressure_pa_abs=pressure,final_relay_state=relay,
            actual_simulated_duration_s=elapsed,requested_duration_s=requested_duration,
            aggregate_duration_representation_difference_s=duration_residual,
            common_clock_representation_residual_s=clock_residual,
            every_native_segment_unintegrated_tail_s=0.0,
            cumulative_boundary_mass_kg=boundary_mass,
            cumulative_boundary_energy_J=boundary_energy,cumulative_vented_mass_kg=vented,
            cumulative_net_liquid_to_vapor_transfer_kg=transfer,global_mass_residual_kg=mass_residual,
            global_energy_residual_J=energy_residual,global_mass_balance_limit_kg=mass_limit,
            global_energy_balance_limit_J=energy_limit,switch_events=len(body["relay_events"]),
            minimum_accepted_pressure_pa_abs=min(accepted_pressures),maximum_accepted_pressure_pa_abs=max(accepted_pressures),
            pressure_within_supplied_limits=None if limits is None else min(accepted_pressures)>=limits[0] and max(accepted_pressures)<=limits[1],
            pressure_limit_support="initial and accepted native-step endpoints; no dense RK-stage extrema claim",
            boundary_energy_integration="sum_original_native_RK_stage_integrals_not_sample_trapezoids"))


def verify_hysteretic_tank_record(record, *, request=None,context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("hysteretic tank receipt changed")
    q=HystereticTankRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    c=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("hysteretic tank caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("hysteretic tank caller context differs")
    if evaluate_hysteretic_tank(q,c)!=record:
        raise ValueError("hysteretic tank original-owner event replay differs")
    return True


def hysteretic_tank_signal_frames(record, *, stream_id=None):
    """Replay the event calculation, then expose accepted post-switch process states."""
    verify_hysteretic_tank_record(record)
    if record["status"]!="computed_diagnostic":
        raise ValueError("only a completed event-split tank diagnostic can produce process frames")
    q=HystereticTankRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    c=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    identity=stream_id or q.base.asset_id+"/"+q.base.event_id+"/hysteretic_tank"
    frames=[]
    units=dict(pressure_pa_abs="Pa",liquid_mass_kg="kg",vapor_mass_kg="kg",relay_open="dimensionless",
               valve_opening="dimensionless",cumulative_boundary_mass_kg="kg",cumulative_boundary_energy_J="J",
               cumulative_vented_mass_kg="kg",cumulative_net_phase_transfer_kg="kg")
    for row in record["history"]:
        relay=row.get("relay_state_after_event",row["relay_state"])
        values=dict(pressure_pa_abs=row["pressure_pa_abs"],liquid_mass_kg=row["state"]["liquid_mass_kg"],
                    vapor_mass_kg=row["state"]["vapor_mass_kg"],relay_open=1.0 if relay=="open" else 0.0,
                    valve_opening=q.open_position if relay=="open" else 0.0,
                    cumulative_boundary_mass_kg=row["cumulative_boundary_mass_kg"],
                    cumulative_boundary_energy_J=row["cumulative_boundary_energy_J"],
                    cumulative_vented_mass_kg=row["cumulative_vented_mass_kg"],
                    cumulative_net_phase_transfer_kg=row["cumulative_net_phase_transfer_kg"])
        frames.append(SignalFrame(stream_id=identity,clock_id=q.base.clock_id,time_s=row["time_s"],values=values,
            units=units,valid=dict.fromkeys(values,True),origin="synthetic" if c.origin=="synthetic" else "simulation",
            operating_mode="instantaneous_relay_"+relay))
    body=dict(schema="pipeline_integrity.hysteretic_tank_frames.v1",source_record_digest=record["record_digest"],
              stream_id=identity,clock_id=q.base.clock_id,frame_count=len(frames),frames_digest=digest(frames),
              event_endpoint_state="post_switch_relay_memory_with_continuous_thermodynamic_state",
              instantaneous_post_switch_vent_rate_inferred=False)
    body["record_digest"]=digest(body)
    return tuple(frames),body
