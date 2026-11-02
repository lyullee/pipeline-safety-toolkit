"""Piecewise-constant liquid filling and gas-displacement coupling.

The existing Fluids geometry owner supplies level/volume inversion. Liquid
flows and density are prescribed; this module does not invent pump curves or
infer pressure-driven liquid discharge from a level trace.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .tanks import TankGeometry, _shape
from .gas_headspace import HeadspaceRequest, evaluate_headspace


class FillWindow(Contract):
    id: Name
    duration_s: Positive
    liquid_inflow_m3_s: Nonnegative = 0.0
    liquid_outflow_m3_s: Nonnegative = 0.0
    evidence_id: Name


class TankFillRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    geometry: TankGeometry
    initial_liquid_volume_m3: Nonnegative
    liquid_density_kg_m3: Positive
    start_time_s: Nonnegative = 0.0
    windows: tuple[FillWindow, ...] = Field(min_length=1,max_length=10000)
    level_limits_m: tuple[Nonnegative,Nonnegative] | None = None
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if len({w.id for w in self.windows})!=len(self.windows):
            raise ValueError("filling window IDs must be unique")
        if self.level_limits_m is not None and self.level_limits_m[0]>=self.level_limits_m[1]:
            raise ValueError("filling level limits must increase")
        return self


def evaluate_tank_filling(request: TankFillRequest, context: RunContext):
    q=TankFillRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("tank-filling asset/clock differs from context")
    if not (set(q.evidence_ids)|{q.geometry.evidence_id}|{w.evidence_id for w in q.windows}) <= {e.id for e in c.evidence}:
        raise ValueError("tank-filling geometry/flow evidence is absent")
    height,capacity,volume_from_height,height_from_volume,owner=_shape(q.geometry,"brenth")
    if q.initial_liquid_volume_m3>capacity:
        raise ValueError("initial liquid volume exceeds tank capacity")
    if q.level_limits_m is not None and q.level_limits_m[1]>height:
        raise ValueError("declared tank level limit exceeds geometry height")
    volume=q.initial_liquid_volume_m3
    time=q.start_time_s
    inflow=outflow=0.0
    rows,events=[],[]

    def row(window_id):
        level=height_from_volume(volume)
        if not math.isfinite(level) or not 0<=level<=height:
            raise ValueError("tank geometry returned an invalid liquid level")
        return dict(window_id=window_id,time_s=time,liquid_volume_m3=volume,
                    headspace_volume_m3=capacity-volume,liquid_mass_kg=volume*q.liquid_density_kg_m3,
                    level_m=level)

    rows.append(row(q.windows[0].id))
    for window in q.windows:
        rate=window.liquid_inflow_m3_s-window.liquid_outflow_m3_s
        endpoint=volume+rate*window.duration_s
        if not 0<=endpoint<=capacity:
            raise ValueError("prescribed filling/draining leaves tank capacity; no silent clipping")
        if q.level_limits_m is not None and rate!=0:
            for label,level in zip(("lower","upper"),q.level_limits_m):
                target=volume_from_height(level)
                elapsed=(target-volume)/rate
                if 0<elapsed<=window.duration_s:
                    events.append(dict(kind="level_limit_crossing",limit=label,level_m=level,
                                       direction="rising" if rate>0 else "falling",time_s=time+elapsed,
                                       window_id=window.id))
        volume=endpoint
        inflow+=window.liquid_inflow_m3_s*window.duration_s
        outflow+=window.liquid_outflow_m3_s*window.duration_s
        if time+window.duration_s<=time:
            raise ValueError("tank-filling clock cannot represent the operating window")
        time+=window.duration_s
        rows.append(row(window.id))
    result=dict(schema="pipeline_integrity.tank_filling.v1",request=q.model_dump(mode="json"),
                status="computed",
                context=c.model_dump(mode="json"),states=rows,level_events=events,
                capacity_m3=capacity,geometry_owner=owner,inflow_volume_m3=inflow,outflow_volume_m3=outflow,
                volume_residual_m3=volume-q.initial_liquid_volume_m3-inflow+outflow,
                implementation_sha256=file_digest(Path(__file__)),numerical_validation_completed=False)
    result["record_digest"]=digest(result)
    return result


def verify_tank_filling_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("tank-filling receipt changed")
    q=TankFillRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("tank-filling caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("tank-filling caller context differs")
    if evaluate_tank_filling(q,c)!=record:
        raise ValueError("tank-filling replay differs")
    return True


class FillHeadspaceRequest(Contract):
    filling: TankFillRequest
    headspace: HeadspaceRequest


def evaluate_fill_headspace(request: FillHeadspaceRequest, context: RunContext):
    q=FillHeadspaceRequest.model_validate(request.model_dump(mode="python"))
    liquid=evaluate_tank_filling(q.filling,context)
    base=q.headspace
    if (base.asset_id,base.clock_id,base.start_time_s)!=(q.filling.asset_id,q.filling.clock_id,q.filling.start_time_s):
        raise ValueError("liquid/headspace asset, clock or start time differs")
    if [(w.id,w.duration_s) for w in base.windows]!=[(w.id,w.duration_s) for w in q.filling.windows]:
        raise ValueError("liquid/headspace operating windows must align exactly")
    payload=base.model_dump(mode="json")
    payload["volume_m3"]=liquid["states"][0]["headspace_volume_m3"]
    for gas,fill in zip(payload["windows"],q.filling.windows):
        gas["volume_change_m3_s"]=fill.liquid_outflow_m3_s-fill.liquid_inflow_m3_s
    derived=HeadspaceRequest.model_validate_json(json.dumps(payload))
    gas=evaluate_headspace(derived,context)
    body=dict(schema="pipeline_integrity.fill_headspace.v1",request=q.model_dump(mode="json"),
              status="computed",
              context=context.model_dump(mode="json"),liquid=liquid,gas=gas,
              derived_headspace_request=derived.model_dump(mode="json"),
              coupling="incompressible_liquid_displacement_with_gas_pressure_work",
              liquid_pressure_feedback_to_flow=False,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_fill_headspace_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("fill/headspace receipt changed")
    q=FillHeadspaceRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("fill/headspace caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("fill/headspace caller context differs")
    if evaluate_fill_headspace(q,c)!=record:
        raise ValueError("fill/headspace replay differs")
    return True
