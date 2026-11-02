"""Replayable public request for native liquid-MOC/inertial-pump coupling."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, model_validator

from . import transient, pump_moc_coupling, rotating_dynamics
from .contracts import Contract, Name, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .transient import LiquidTransient, simulate_liquid_transient


class PumpMOCRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    specification: LiquidTransient
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def pumps_required(self):
        if not self.specification.pumps:
            raise ValueError("pump/MOC request requires at least one actual pump")
        return self


def _sources():
    return {"exchange":file_digest(Path(__file__)),
            **{key:file_digest(Path(module.__file__)) for key,module in
               (("moc",transient),("pump_closure",pump_moc_coupling),("signed_map",rotating_dynamics))}}


def evaluate_pump_moc(request: PumpMOCRequest, context: RunContext):
    q=PumpMOCRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("pump/MOC asset and clock differ from context")
    before=_sources()
    result=simulate_liquid_transient(q.specification,c)
    if _sources()!=before:
        raise ValueError("pump/MOC implementation changed during the coupled solve")
    body=json_value(dict(schema="pipeline_integrity.pump_moc_exchange.v1",
        status="computed_diagnostic" if result["status"]=="computed" else result["status"],
        request=q,context=c,native=result,implementation_sha256=before,
        simultaneous_characteristic_pump_rotor_closure=True,
        accepted_coupled_steps=sum(audit["root_qualified"] for audit in result["pump_solver"]),
        full_fluid_energy_balance_implemented=False,
        numerical_validation_completed=False,field_validation_claim=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("pump/MOC receipt exceeds its retained-byte budget")
    return body


def verify_pump_moc_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("pump/MOC receipt changed")
    q=PumpMOCRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("pump/MOC caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("pump/MOC caller context differs")
    if evaluate_pump_moc(q,c)!=record:
        raise ValueError("pump/MOC coupled solver replay differs")
    return True


def pump_moc_signal_frames(record, *, pump_id: str, stream_id: str):
    verify_pump_moc_record(record)
    if record["status"]!="computed_diagnostic":
        raise ValueError("pump/MOC signal mapping requires a complete in-domain coupled result")
    q=PumpMOCRequest.model_validate_json(json.dumps(record["request"]))
    if pump_id not in record["native"]["pumps"]:
        raise ValueError("pump signal selection does not identify an actual simulated pump")
    units={"speed_rad_s":"rad/s","volume_flow_m3_s":"m^3/s","head_gain_m":"m","rotor_energy_J":"J"}
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
        values={key:row[key] for key in units},units=units,valid={key:True for key in units},origin="simulation",
        operating_mode="simultaneous_liquid_moc_pump_rotor") for row in record["native"]["pumps"][pump_id])
    body=json_value(dict(schema="pipeline_integrity.pump_moc_frames.v1",source_digest=record["record_digest"],
        pump_id=pump_id,stream_id=stream_id,asset_id=q.asset_id,clock_id=q.clock_id,origin="simulation",
        frames_digest=digest(frames),frame_count=len(frames)))
    body["record_digest"]=digest(body)
    return frames,body
