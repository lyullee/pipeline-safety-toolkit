"""Replay-qualified process histories exposed through the shared SignalFrame API."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from .contracts import Contract, Name, RunContext, digest, json_value, require_context_extension
from .datasets import file_digest


SOURCES={
    "dynamic_fracture":("dynamic_fracture","dynamic_fracture_signal_frames","history"),
    "vapor_cavity_moc":("vapor_cavity_moc","vapor_cavity_moc_signal_frames","states"),
    "hydrate_kinetics":("hydrate_kinetics","hydrate_kinetics_signal_frames","states"),
    "particle_settling":("particle_settling","particle_settling_signal_frames","states"),
    "fiber_interferometry":("fiber_interferometry","fiber_interferometry_signal_frames","states"),
    "cavity_moc":("cavity_moc","cavity_moc_signal_frames","states"),
    "pump_moc":("pump_moc_exchange","pump_moc_signal_frames","states"),
    "rupture_inventory":("rupture_inventory","rupture_inventory_signal_frames","states"),
    "headspace_drainage":("headspace_drainage","headspace_drainage_signal_frames","states"),
    "gas_headspace":("gas_headspace","headspace_signal_frames","states"),
    "hysteretic_tank":("hysteretic_tank","hysteretic_tank_signal_frames","history"),
    "rotating_shaft":("rotating_dynamics","rotating_shaft_signal_frames","states"),
    "compressor_dynamics":("compressor_dynamics","compressor_dynamics_signal_frames","states"),
    "spatial_phase":("spatial_phase_change","spatial_phase_signal_frames","states"),
    "frost_growth":("frost_growth","frost_growth_signal_frames","states"),
    "inventory_transfers":("inventory_transfers","inventory_transfer_signal_frames","states"),
}


class ProcessSignalRequest(Contract):
    id: Name
    source_kind: Literal["gas_headspace","hysteretic_tank","rotating_shaft","compressor_dynamics",
                         "spatial_phase","frost_growth","inventory_transfers","pump_moc",
                         "rupture_inventory","headspace_drainage","particle_settling",
                         "fiber_interferometry","cavity_moc","dynamic_fracture",
                         "vapor_cavity_moc","hydrate_kinetics"]
    source_record: dict
    expected_source_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_evidence_id: Name
    stream_id: Name
    cell_id: Name | None = None
    inventory_id: Name | None = None
    pump_id: Name | None = None
    particle_id: Name | None = None
    gauge_id: Name | None = None
    section_index: int | None = Field(default=None,ge=1)
    channel_aliases: dict[str,str] = Field(default_factory=dict)
    channel_mapping_evidence_id: Name | None = None
    maximum_frames: int = Field(default=100000,ge=1,le=1000000)
    maximum_source_bytes: int = Field(default=64000000,ge=1,le=1000000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1,le=1000000000)

    @field_validator("source_record")
    @classmethod
    def finite_record(cls,value):
        return json_value(value)


def evaluate_process_signals(request: ProcessSignalRequest, context: RunContext):
    q=ProcessSignalRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    source=q.source_record
    if len(json.dumps(source,allow_nan=False).encode("utf-8"))>q.maximum_source_bytes:
        raise ValueError("process source exceeds its byte budget")
    if source.get("record_digest")!=q.expected_source_record_digest:
        raise ValueError("process source differs from its explicit receipt pin")
    if digest({k:v for k,v in source.items() if k!="record_digest"})!=q.expected_source_record_digest:
        raise ValueError("process source receipt changed")
    required=[e for e in c.evidence if e.id==q.source_evidence_id]
    if len({e.id for e in c.evidence})!=len(c.evidence) or len(required)!=1 or required[0].sha256!=q.expected_source_record_digest:
        raise ValueError("process source receipt requires matching unambiguous context evidence")
    sc=RunContext.model_validate_json(json.dumps(source["context"]))
    require_context_extension(sc,c)
    module_name,function_name,history_key=SOURCES[q.source_kind]
    history=(source.get("native",{}).get("pumps",{}).get(q.pump_id)
             if q.source_kind=="pump_moc" else source.get(history_key))
    if q.source_kind in {"particle_settling","fiber_interferometry"}:
        collection,key,selected=("particle_classes","particle_id",q.particle_id) if q.source_kind=="particle_settling" else ("gauges","gauge_id",q.gauge_id)
        matches=[v for v in source.get(collection,[]) if v.get(key)==selected]
        history=matches[0].get("states") if len(matches)==1 else None
    if not isinstance(history,list) or not 1<=len(history)<=q.maximum_frames:
        raise ValueError("process source requires a complete bounded state history")
    if (q.cell_id is not None)!=(q.source_kind=="spatial_phase"):
        raise ValueError("cell selector is required only for a spatial phase source")
    if (q.inventory_id is not None)!=(q.source_kind in {"inventory_transfers","rupture_inventory"}):
        raise ValueError("inventory selector is required only for an inventory source")
    if (q.pump_id is not None)!=(q.source_kind=="pump_moc"):
        raise ValueError("pump selector is required only for a pump/MOC source")
    if (q.particle_id is not None)!=(q.source_kind=="particle_settling"):
        raise ValueError("particle selector is required only for a particle-settling source")
    if (q.gauge_id is not None)!=(q.source_kind=="fiber_interferometry"):
        raise ValueError("gauge selector is required only for an optical fiber source")
    if (q.section_index is not None)!=(q.source_kind in {"cavity_moc","vapor_cavity_moc"}):
        raise ValueError("section selector is required only for a discrete-cavity source")
    module=importlib.import_module("pipeline_integrity."+module_name)
    before=file_digest(Path(module.__file__))
    kwargs=dict(stream_id=q.stream_id)
    if q.cell_id is not None:
        kwargs["cell_id"]=q.cell_id
    if q.inventory_id is not None:
        kwargs["inventory_id"]=q.inventory_id
    if q.pump_id is not None:
        kwargs["pump_id"]=q.pump_id
    if q.particle_id is not None:
        kwargs["particle_id"]=q.particle_id
    if q.gauge_id is not None:
        kwargs["gauge_id"]=q.gauge_id
    if q.section_index is not None:
        kwargs["section_index"]=q.section_index
    frames,native_receipt=getattr(module,function_name)(source,**kwargs)
    if file_digest(Path(module.__file__))!=before:
        raise ValueError("process conversion implementation changed during replay")
    if not frames or len(frames)>q.maximum_frames:
        raise ValueError("process conversion must preserve every source row")
    groups=native_receipt.get("source_row_groups")
    if q.source_kind=="vapor_cavity_moc":
        if not isinstance(groups,list) or len(groups)!=len(frames) or any(not isinstance(g,list) or not g for g in groups):
            raise ValueError("event-split vapor signals must retain complete source row groups")
        if [index for group in groups for index in group]!=list(range(len(history))):
            raise ValueError("vapor signal row groups must preserve every original row exactly once")
        if any(any(type(i) is not int or history[i]["time_s"]!=frame.time_s for i in group)
               for group,frame in zip(groups,frames)):
            raise ValueError("event source rows must map to their exact common physical time")
    elif len(frames)!=len(history):
        raise ValueError("process conversion must preserve every source row")
    if any(f.clock_id!=c.clock_id or f.stream_id!=q.stream_id for f in frames):
        raise ValueError("process conversion changed the requested stream or source clock")
    if any(b.time_s<=a.time_s for a,b in zip(frames,frames[1:])):
        raise ValueError("process signal history must preserve a strictly ordered clock")
    native_frames_digest=digest(frames)
    if bool(q.channel_aliases)!=(q.channel_mapping_evidence_id is not None):
        raise ValueError("channel aliases and their mapping evidence must be supplied together")
    if q.channel_aliases:
        mapping=[e for e in c.evidence if e.id==q.channel_mapping_evidence_id]
        if len(mapping)!=1 or mapping[0].sha256 is None:
            raise ValueError("process channel aliases require source-pinned quantity/meaning review")
        renamed=[]
        for frame in frames:
            if not set(q.channel_aliases)<=set(frame.values):
                raise ValueError("process channel alias references an absent source quantity")
            names=[q.channel_aliases.get(key,key) for key in frame.values]
            if any(not value.strip() for value in names) or len(set(names))!=len(names):
                raise ValueError("process channel aliases must be nonempty and collision-free")
            payload=frame.model_dump(mode="python")
            for field in ("values","units","valid"):
                payload[field]={q.channel_aliases.get(key,key):value for key,value in payload[field].items()}
            renamed.append(type(frame).model_validate(payload))
        frames=tuple(renamed)
    body=dict(schema="pipeline_integrity.process_signals.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="computed_diagnostic",
              frames=[f.model_dump(mode="json") for f in frames],native_receipt=native_receipt,
              source_record_digest=q.expected_source_record_digest,
              source_module_sha256=before,implementation_sha256=file_digest(Path(__file__)),
              source_row_count=len(history),published_frame_count=len(frames),native_frames_digest=native_frames_digest,
              delivered_frames_digest=digest(frames),channel_aliases=q.channel_aliases,
              rows_interpolated=False,measurement_origin_inferred=False,
              field_validation_conferred=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("process signal receipt exceeds its result budget")
    return body


def verify_process_signals_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("process signal receipt changed")
    q=ProcessSignalRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("process signal caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("process signal caller context differs")
    if evaluate_process_signals(q,c)!=record:
        raise ValueError("process signal source replay differs")
    return True
