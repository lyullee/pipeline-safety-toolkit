"""Lumped melting/freezing with explicit sensible and latent enthalpy regions.

One fixed-mass uniform-temperature body exchanges heat with prescribed ambient
windows. This is an enthalpy storage model, not a spatial moving-front solver.
All material properties and the transition temperature are caller supplied.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest


class PhaseStorageWindow(Contract):
    id: Name
    duration_s: Positive
    ambient_temperature_K: Positive
    conductance_W_K: Nonnegative
    direct_heat_W: float = 0.0
    evidence_id: Name


class PhaseStorageRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    mass_kg: Positive
    transition_temperature_K: Positive
    solid_heat_capacity_J_kg_K: Positive
    liquid_heat_capacity_J_kg_K: Positive
    latent_heat_J_kg: Positive
    initial_enthalpy_J: float
    windows: tuple[PhaseStorageWindow, ...] = Field(min_length=1,max_length=10000)
    lumped_temperature_and_constant_properties_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers(self):
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("phase-storage window IDs must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("phase-storage evidence IDs must be unique")
        if self.initial_enthalpy_J <= -self.mass_kg*self.solid_heat_capacity_J_kg_K*self.transition_temperature_K:
            raise ValueError("initial solid temperature must be above absolute zero")
        return self


def evaluate_phase_storage(request: PhaseStorageRequest, context: RunContext) -> dict:
    q = PhaseStorageRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if not q.lumped_temperature_and_constant_properties_reviewed:
        raise ValueError("lumped thermal and constant-property applicability review is required")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("phase-storage asset/clock differs from context")
    if not (set(q.evidence_ids)|{w.evidence_id for w in q.windows}) <= {e.id for e in c.evidence}:
        raise ValueError("phase-storage boundary/material evidence is absent")
    latent = q.mass_kg*q.latent_heat_J_kg
    cs = q.mass_kg*q.solid_heat_capacity_J_kg_K
    cl = q.mass_kg*q.liquid_heat_capacity_J_kg_K
    enthalpy = q.initial_enthalpy_J
    time = q.start_time_s
    direct = ambient = 0.0
    rows,events = [],[]

    def row(window_id):
        if enthalpy<0:
            temperature,fraction,phase = q.transition_temperature_K+enthalpy/cs,0.0,"solid"
        elif enthalpy>latent:
            temperature,fraction,phase = q.transition_temperature_K+(enthalpy-latent)/cl,1.0,"liquid"
        else:
            temperature,fraction,phase = q.transition_temperature_K,enthalpy/latent,"transition"
        if not math.isfinite(temperature) or temperature<=0:
            raise ValueError("phase storage left the positive-temperature domain")
        return dict(window_id=window_id,time_s=time,enthalpy_J=enthalpy,
                    temperature_K=temperature,liquid_mass_fraction=fraction,phase=phase)

    rows.append(row(q.windows[0].id))
    for window in q.windows:
        remaining = window.duration_s
        # A constant boundary drives a monotonic enthalpy trajectory through at
        # most three regions. No user timestep or phase-fraction clipping is used.
        for _ in range(4):
            if remaining<=0:
                break
            drive = window.direct_heat_W+window.conductance_W_K*(window.ambient_temperature_K-q.transition_temperature_K)
            if enthalpy<0 or (enthalpy==0 and drive<0):
                capacity,offset,boundary = cs,0.0,0.0
                region="solid"
            elif enthalpy>latent or (enthalpy==latent and drive>0):
                capacity,offset,boundary = cl,latent,latent
                region="liquid"
            else:
                capacity,offset = None,0.0
                boundary = latent if drive>0 else 0.0
                region="transition"
            start = enthalpy
            rate = drive if capacity is None else drive-window.conductance_W_K*(enthalpy-offset)/capacity
            crossing = None
            if capacity is None or window.conductance_W_K==0:
                if rate!=0:
                    candidate=(boundary-enthalpy)/rate
                    if candidate>0:
                        crossing=candidate
                elapsed=min(remaining,crossing) if crossing is not None else remaining
                enthalpy += rate*elapsed
            else:
                decay=window.conductance_W_K/capacity
                if not math.isfinite(decay) or decay<=0:
                    raise ValueError("thermal relaxation rate is outside finite arithmetic")
                if rate!=0:
                    linear_time=(boundary-enthalpy)/rate
                    fraction=decay*linear_time
                    if linear_time>0 and 0<=fraction<1:
                        crossing=linear_time if fraction==0 else -math.log1p(-fraction)/decay
                elapsed=min(remaining,crossing) if crossing is not None else remaining
                enthalpy=start+rate*(-math.expm1(-decay*elapsed))/decay
            reached = crossing is not None and crossing<=remaining
            if reached:
                enthalpy=boundary
            direct += window.direct_heat_W*elapsed
            ambient += enthalpy-start-window.direct_heat_W*elapsed
            if elapsed>0 and time+elapsed<=time:
                raise ValueError("phase-storage clock cannot represent its phase event or boundary step")
            time += elapsed
            remaining -= elapsed
            rows.append(row(window.id))
            if reached:
                events.append(dict(window_id=window.id,time_s=time,from_region=region,
                                   boundary_enthalpy_J=boundary,direction="heating" if rate>0 else "cooling"))
            else:
                break
        if remaining>0:
            raise ValueError("phase-storage region traversal did not terminate")
    result=dict(schema="pipeline_integrity.phase_storage.v1",request=q.model_dump(mode="json"),
                status="computed",
                context=c.model_dump(mode="json"),states=rows,phase_events=events,
                direct_heat_J=direct,ambient_heat_J=ambient,
                energy_residual_J=enthalpy-q.initial_enthalpy_J-direct-ambient,
                implementation_sha256=file_digest(Path(__file__)),
                enthalpy_reference="solid_at_transition_temperature",numerical_validation_completed=False)
    result["record_digest"]=digest(result)
    return result


def verify_phase_storage_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("phase-storage receipt changed")
    q=PhaseStorageRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("phase-storage caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("phase-storage caller context differs")
    if evaluate_phase_storage(q,c)!=record:
        raise ValueError("phase-storage replay differs")
    return True
