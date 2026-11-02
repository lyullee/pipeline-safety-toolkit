"""Conservative prescribed transfers between explicit species/energy inventories.

Stream composition and total flow energy are supplied at every window. No
equilibrium, phase split, pressure-driven release or mixing closure is inferred.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest
from .datasets import file_digest


class ConservedInventory(Contract):
    id: Name
    species_mass_kg: dict[str, Nonnegative]
    stored_total_energy_J: float
    evidence_id: Name


class InventoryStream(Contract):
    id: Name
    source_id: Name | None = None
    destination_id: Name | None = None
    species_mass_flow_kg_s: dict[str, Nonnegative]
    specific_total_flow_energy_J_kg: float
    evidence_id: Name

    @model_validator(mode="after")
    def ports(self):
        if self.source_id == self.destination_id:
            raise ValueError("a stream requires distinct ports and at least one inventory")
        return self


class InventoryTransferWindow(Contract):
    id: Name
    duration_s: Positive
    streams: tuple[InventoryStream, ...] = Field(default=(), max_length=10000)
    heat_and_work_into_inventory_W: dict[str, float] = Field(default_factory=dict)
    evidence_id: Name


class InventoryTransferRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    species_ids: tuple[Name, ...] = Field(min_length=1, max_length=200)
    total_energy_reference_id: Name
    inventories: tuple[ConservedInventory, ...] = Field(min_length=1, max_length=1000)
    windows: tuple[InventoryTransferWindow, ...] = Field(min_length=1, max_length=10000)
    prescribed_streams_common_energy_reference_reviewed: bool = False
    absolute_species_balance_tolerance_kg: Positive = 1e-8
    absolute_energy_balance_tolerance_J: Positive = 1e-5
    maximum_retained_values: int = Field(default=1000000, ge=1, le=10000000)
    maximum_stream_species_operations: int = Field(default=10000000, ge=1, le=100000000)
    maximum_result_bytes: int = Field(default=64000000, ge=1, le=1000000000)


def evaluate_inventory_transfers(request: InventoryTransferRequest, context: RunContext):
    q=InventoryTransferRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("inventory asset/clock differs from context")
    if not q.prescribed_streams_common_energy_reference_reviewed:
        raise ValueError("stream composition, energy reference and prescribed boundary review required")
    species=set(q.species_ids)
    ids=[v.id for v in q.inventories]
    evidence=[v.id for v in c.evidence]
    if len(species)!=len(q.species_ids) or len(set(ids))!=len(ids) or len(set(evidence))!=len(evidence):
        raise ValueError("species, inventory and evidence IDs must be unique")
    if len({w.id for w in q.windows})!=len(q.windows):
        raise ValueError("window IDs must be unique")
    if (len(q.windows)+1)*len(ids)*(len(species)+1)>q.maximum_retained_values:
        raise ValueError("inventory trajectory exceeds retained-value budget")
    operations=sum(len(w.streams) for w in q.windows)*2*(len(species)+1)
    if operations>q.maximum_stream_species_operations:
        raise ValueError("inventory transfer exceeds its stream/species work budget")
    masses={v.id:dict(v.species_mass_kg) for v in q.inventories}
    energies={v.id:v.stored_total_energy_J for v in q.inventories}
    needed={v.evidence_id for v in q.inventories}
    for v in q.inventories:
        if set(v.species_mass_kg)!=species:
            raise ValueError("each inventory must explicitly include every species, including zeros")
    for w in q.windows:
        needed.add(w.evidence_id)
        if not set(w.heat_and_work_into_inventory_W)<=set(ids):
            raise ValueError("heat/work references unknown inventory")
        if len({s.id for s in w.streams})!=len(w.streams):
            raise ValueError("stream IDs must be unique within a window")
        for s in w.streams:
            needed.add(s.evidence_id)
            if set(s.species_mass_flow_kg_s)!=species:
                raise ValueError("each stream must explicitly include every species")
            if any(p is not None and p not in masses for p in (s.source_id,s.destination_id)):
                raise ValueError("stream references unknown inventory")
    if not needed<=set(evidence):
        raise ValueError("inventory and boundary evidence must be actual context members")
    initial_mass={s:math.fsum(masses[i][s] for i in ids) for s in q.species_ids}
    initial_energy=math.fsum(energies.values())
    external_mass={s:0.0 for s in q.species_ids}
    external_energy=0.0
    heat_work=0.0
    time=q.start_time_s
    def snapshot():
        return dict(time_s=time,inventories=[dict(id=i,species_mass_kg=dict(masses[i]),
                    stored_total_energy_J=energies[i]) for i in ids])
    rows=[snapshot()]
    ledgers=[]
    for w in q.windows:
        if not math.isfinite(time+w.duration_s) or time+w.duration_s<=time:
            raise ValueError("window clock is not representable")
        dm={i:{s:[] for s in q.species_ids} for i in ids}
        de={i:[w.heat_and_work_into_inventory_W.get(i,0.0)*w.duration_s] for i in ids}
        for stream in w.streams:
            amounts={s:rate*w.duration_s for s,rate in stream.species_mass_flow_kg_s.items()}
            energy=math.fsum(amounts.values())*stream.specific_total_flow_energy_J_kg
            for port,sign in ((stream.source_id,-1),(stream.destination_id,1)):
                if port is not None:
                    for s,amount in amounts.items():
                        dm[port][s].append(sign*amount)
                    de[port].append(sign*energy)
                else:
                    for s,amount in amounts.items():
                        external_mass[s]-=sign*amount
                    external_energy-=sign*energy
        for i in ids:
            for s in q.species_ids:
                updated=math.fsum([masses[i][s],*dm[i][s]])
                if updated<0 or not math.isfinite(updated):
                    raise ValueError("prescribed transfer depletes a species; split or revise its window")
                masses[i][s]=updated
            energies[i]=math.fsum([energies[i],*de[i]])
            if not math.isfinite(energies[i]):
                raise ValueError("inventory energy is nonfinite")
        heat_work+=math.fsum(w.heat_and_work_into_inventory_W.values())*w.duration_s
        residuals={s:math.fsum(masses[i][s] for i in ids)-initial_mass[s]-external_mass[s]
                   for s in q.species_ids}
        energy_residual=math.fsum(energies.values())-initial_energy-external_energy-heat_work
        if any(abs(v)>q.absolute_species_balance_tolerance_kg for v in residuals.values()) or abs(energy_residual)>q.absolute_energy_balance_tolerance_J:
            raise ValueError("inventory conservation residual exceeds caller tolerance")
        time+=w.duration_s
        rows.append(snapshot())
        ledgers.append(dict(window_id=w.id,cumulative_external_species_mass_kg=dict(external_mass),
                            cumulative_external_flow_energy_J=external_energy,
                            cumulative_heat_work_J=heat_work,species_residual_kg=residuals,
                            energy_residual_J=energy_residual))
    result=dict(schema="pipeline_integrity.inventory_transfers.v1",status="computed",
                request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),
                states=rows,ledgers=ledgers,implementation_sha256=file_digest(Path(__file__)),
                stream_species_operations=operations,
                thermodynamic_closure=False,numerical_validation_completed=False)
    result["record_digest"]=digest(result)
    if len(json.dumps(result,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("inventory transfer exceeds its retained-result byte budget")
    return result


def verify_inventory_transfer_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("inventory transfer receipt changed")
    q=InventoryTransferRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("inventory transfer caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("inventory transfer caller context differs")
    if evaluate_inventory_transfers(q,c)!=record:
        raise ValueError("inventory transfer replay differs")
    return True


def inventory_transfer_signal_frames(record, *, inventory_id: str, stream_id: str):
    """Expose the exact replayed conserved states without thermodynamic inference."""
    verify_inventory_transfer_record(record)
    q=InventoryTransferRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if inventory_id not in {v.id for v in q.inventories}:
        raise ValueError("requested inventory does not exist in this transfer calculation")
    frames=[]
    for row in record["states"]:
        state=next(v for v in row["inventories"] if v["id"]==inventory_id)
        values={"species_mass_kg/"+s:state["species_mass_kg"][s] for s in q.species_ids}
        values["total_mass_kg"]=math.fsum(state["species_mass_kg"].values())
        values["stored_total_energy_J"]=state["stored_total_energy_J"]
        units={key:"kg" for key in values}
        units["stored_total_energy_J"]="J"
        frames.append(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
            values=values,units=units,valid=dict.fromkeys(values,True),
            origin="synthetic" if c.origin=="synthetic" else "simulation",
            operating_mode="prescribed_inventory_transfer"))
    receipt=dict(schema="pipeline_integrity.inventory_transfer_frames.v1",
                 source_record_digest=record["record_digest"],inventory_id=inventory_id,
                 stream_id=stream_id,frame_count=len(frames),frames_digest=digest(frames),
                 total_energy_reference_id=q.total_energy_reference_id)
    receipt["record_digest"]=digest(receipt)
    return tuple(frames),receipt
