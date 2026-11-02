"""Explicit dataflow across allowlisted engineering adapter jobs.

Bindings copy selected JSON values into existing parameter fields. They never
import Python code, change context evidence, or promote a withheld child result.
"""
from __future__ import annotations

import json
import importlib
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract, Name, digest, json_value
from .datasets import file_digest
from .adapter_jobs import AdapterJob, run_adapter_job, adapter_specs
from .units import convert_quantity


CONTEXT_BOUNDARIES = {
    "structural.dynamic_fracture": ("dynamic_fracture_boundary","dynamic_fracture_boundary"),
    "construction.elastic_pull": ("elastic_pull_boundary","elastic_pull_boundary"),
    "release.shared_hem_inventory": ("rupture_inventory_boundary","rupture_inventory_boundary"),
    "equipment.controlled_compressor": ("compressor_train_boundary","controlled_compressor_boundary"),
    "equipment.compressor_dynamics": ("compressor_train_boundary","compressor_dynamics_boundary"),
    "tanks.hysteretic_breathing": ("hysteretic_tank_boundary","hysteretic_tank_boundary"),
    "thermal.adaptive_heated_transport": ("adaptive_heated_transport_boundary","adaptive_heated_transport_boundary"),
    "construction.pull": ("pulling_boundary","boundary_metadata"),
    "construction.route": ("route_boundary","route_boundary_metadata"),
    "leak.sensor_placement": ("sensor_placement_boundary","boundary_metadata"),
    "tanks.breathing": ("tank_breathing_boundary","boundary_metadata"),
    "thermal.heated_oil": ("heated_transport_boundary","boundary_metadata"),
    "transport.slurry": ("slurry_transport_boundary","boundary_metadata"),
    "transport.heated_slurry": ("heated_slurry_boundary","boundary_metadata"),
    "thermal.fluid_soil_feedback": ("fluid_soil_feedback_boundary","fluid_soil_feedback_boundary"),
    "thermal.phase_change": ("phase_change_boundary","boundary_metadata"),
    "transient.gas_nonisothermal": ("gas_thermal_transient_boundary","boundary_metadata"),
    "structural.soil_pipe": ("soil_pipe_boundary","soil_pipe_boundary_metadata"),
    "economics.optimization": ("operating_boundary","operating_boundary_metadata"),
}


def _tokens(pointer, *, root_allowed=False):
    if pointer=="" and root_allowed:
        return []
    if not isinstance(pointer,str) or not pointer.startswith("/") or len(pointer)>2048:
        raise ValueError("a bounded JSON pointer is required")
    result=[]
    for raw in pointer[1:].split("/"):
        i=0
        while i<len(raw):
            if raw[i]=="~":
                if i+1>=len(raw) or raw[i+1] not in "01":
                    raise ValueError("invalid JSON pointer escape")
                i+=2
            else:
                i+=1
        result.append(raw.replace("~1","/").replace("~0","~"))
    if len(result)>64:
        raise ValueError("JSON pointer nesting exceeds the campaign limit")
    return result


def _read(value,tokens):
    for token in tokens:
        if isinstance(value,dict):
            if token not in value:
                raise ValueError("campaign pointer identifies an absent field")
            value=value[token]
        elif isinstance(value,list):
            if not token.isascii() or not token.isdigit() or (len(token)>1 and token[0]=="0"):
                raise ValueError("campaign list index must be canonical and nonnegative")
            index=int(token)
            if index>=len(value):
                raise ValueError("campaign pointer index exceeds its source list")
            value=value[index]
        else:
            raise ValueError("campaign pointer traverses a scalar")
    return value


class CampaignBinding(Contract):
    source_node_id: Name
    source_pointer: str
    target_parameter_pointer: str
    target_scope: Literal["parameters","payloads"] = "parameters"
    mapping_evidence_id: Name
    source_unit: Name | None = None
    target_unit: Name | None = None

    @model_validator(mode="after")
    def pointers(self):
        _tokens(self.source_pointer,root_allowed=True)
        _tokens(self.target_parameter_pointer)
        if (self.source_unit is None)!=(self.target_unit is None):
            raise ValueError("campaign conversion requires both source and target units")
        return self


class CampaignNode(Contract):
    id: Name
    job: AdapterJob
    depends_on: tuple[Name, ...] = ()
    input_bindings: tuple[CampaignBinding, ...] = Field(default=(),max_length=256)
    accepted_statuses: tuple[Literal["computed","estimated","ready","computed_diagnostic"], ...] = ("computed","estimated","ready")
    context_boundary_review_evidence_id: Name | None = None

    @model_validator(mode="after")
    def targets(self):
        paths=[(binding.target_scope,_tokens(binding.target_parameter_pointer)) for binding in self.input_bindings]
        for i,(scope,a) in enumerate(paths):
            for other_scope,b in paths[i+1:]:
                if scope==other_scope and (a[:len(b)]==b or b[:len(a)]==a):
                    raise ValueError("campaign parameter writes must not overlap")
        if not self.accepted_statuses:
            raise ValueError("campaign nodes require explicit accepted result statuses")
        return self


class AdapterCampaign(Contract):
    id: Name
    nodes: tuple[CampaignNode, ...] = Field(min_length=1,max_length=256)
    maximum_job_bytes: int = Field(default=64000000,ge=1,le=1000000000)
    maximum_request_bytes: int = Field(default=64000000,ge=1,le=1000000000)
    maximum_total_result_bytes: int = Field(default=128000000,ge=1,le=1000000000)
    maximum_converted_values: int = Field(default=1000000,ge=1,le=10000000)

    @model_validator(mode="after")
    def graph(self):
        known={n.id for n in self.nodes}
        if len(known)!=len(self.nodes):
            raise ValueError("campaign node IDs must be unique")
        allowed={s["id"] for s in adapter_specs()}
        for node in self.nodes:
            dependencies=set(node.depends_on)|{b.source_node_id for b in node.input_bindings}
            if node.id in dependencies or not dependencies<=known:
                raise ValueError("campaign dependency is unknown or self-referential")
            if node.job.adapter_id not in allowed:
                raise ValueError("campaign contains an adapter outside the allowlist")
        _order(self.nodes)
        return self


def _order(nodes):
    done=set()
    order=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n.id not in done and
               (set(n.depends_on)|{b.source_node_id for b in n.input_bindings})<=done]
        if not ready:
            raise ValueError("campaign dependency graph contains a cycle")
        for node in ready:
            done.add(node.id)
            order.append(node)
    return order


def bind_adapter_job_context(job: AdapterJob, *, review_evidence_id: str):
    """Refresh a declared model boundary after an explicitly reviewed input mapping."""
    q=AdapterJob.model_validate_json(json.dumps(job.model_dump(mode="json"),allow_nan=False))
    evidence=[e for e in q.context.evidence if e.id==review_evidence_id]
    if len(evidence)!=1 or evidence[0].sha256 is None:
        raise ValueError("adapter context rebinding requires source-hash-pinned review evidence")
    declaration=CONTEXT_BOUNDARIES.get(q.adapter_id)
    if declaration is None:
        raise ValueError("adapter has no declared context-boundary builder")
    spec=next(s for s in adapter_specs() if s["id"]==q.adapter_id)
    module=importlib.import_module("pipeline_integrity."+spec["module"])
    source_before=file_digest(Path(module.__file__))
    parameters=getattr(module,spec["inputs"]).model_validate_json(json.dumps(q.parameters,allow_nan=False))
    key,function=declaration
    boundary=json_value(getattr(module,function)(parameters))
    if file_digest(Path(module.__file__))!=source_before:
        raise ValueError("adapter context-boundary implementation changed during preparation")
    payload=q.model_dump(mode="json")
    previous=payload["context"]["metadata"].get(key)
    payload["context"]["metadata"][key]=boundary
    derived=AdapterJob.model_validate_json(json.dumps(payload,allow_nan=False))
    receipt=dict(schema="pipeline_integrity.adapter_context_binding.v1",key=key,
                 original_job_digest=digest(q),derived_job_digest=digest(derived),
                 previous_digest=digest(previous),derived_digest=digest(boundary),
                 review_evidence_id=review_evidence_id,adapter_module_sha256=source_before,
                 implementation_sha256=file_digest(Path(__file__)))
    receipt["record_digest"]=digest(receipt)
    return derived,receipt


def run_adapter_campaign(request: AdapterCampaign, *, providers=None):
    document=json.dumps(json_value(request),ensure_ascii=False,allow_nan=False)
    maximum=request.maximum_request_bytes
    if type(maximum) is not int or not 0<maximum<=1000000000:
        raise ValueError("campaign request-byte limit must be a positive bounded integer")
    if len(document)>maximum or len(document.encode("utf-8"))>maximum:
        raise ValueError("campaign exceeds its request-byte budget before importing model engines")
    q=AdapterCampaign.model_validate_json(document)
    originals={n.id:n for n in q.nodes}
    results={}
    total_bytes=0
    converted_values=0
    def converted(value,source_unit,target_unit,depth=0):
        nonlocal converted_values
        if depth>64:
            raise ValueError("campaign unit conversion nesting exceeds its limit")
        if isinstance(value,list):
            return [converted(v,source_unit,target_unit,depth+1) for v in value]
        converted_values+=1
        if converted_values>q.maximum_converted_values:
            raise ValueError("campaign unit conversions exceeded the declared work budget")
        return convert_quantity(value,source_unit,target_unit)
    for node in _order(q.nodes):
        dependencies=set(node.depends_on)|{b.source_node_id for b in node.input_bindings}
        blocked=sorted(k for k in dependencies if results[k]["status"]!="accepted")
        if blocked:
            outcome=dict(status="blocked",blocked_by=blocked,executed=False)
        else:
            document=node.job.model_dump(mode="json")
            binding_receipts=[]
            for binding in node.input_bindings:
                source=originals[binding.source_node_id].job.context
                target=node.job.context
                if (source.asset_id,source.clock_id)!=(target.asset_id,target.clock_id):
                    raise ValueError("campaign value transfer requires the same asset and clock; use an explicit mapping adapter")
                evidence=[e for e in target.evidence if e.id==binding.mapping_evidence_id]
                if len(evidence)!=1 or evidence[0].sha256 is None:
                    raise ValueError("campaign transfer requires a context-bound source-hash-pinned mapping review")
                source_result=results[binding.source_node_id]["receipt"]
                value=_read(source_result["result"],_tokens(binding.source_pointer,root_allowed=True))
                tokens=_tokens(binding.target_parameter_pointer)
                parent=_read(document[binding.target_scope],tokens[:-1])
                # Require the destination to exist before writing; list append
                # and schema-shape inference are deliberately absent.
                _read(parent,tokens[-1:])
                copied=json.loads(json.dumps(value,allow_nan=False))
                if binding.source_unit is not None:
                    copied=converted(copied,binding.source_unit,binding.target_unit)
                if isinstance(parent,dict):
                    parent[tokens[-1]]=copied
                else:
                    parent[int(tokens[-1])]=copied
                binding_receipts.append(dict(source_node_id=binding.source_node_id,
                    source_receipt_digest=source_result["record_digest"],source_pointer=binding.source_pointer,
                    target_parameter_pointer=binding.target_parameter_pointer,value_digest=digest(value),
                    target_scope=binding.target_scope,
                    delivered_value_digest=digest(copied),source_unit=binding.source_unit,target_unit=binding.target_unit,
                    mapping_evidence_id=binding.mapping_evidence_id))
            context_rebinding=None
            resolved=AdapterJob.model_validate_json(json.dumps(document,allow_nan=False))
            if node.context_boundary_review_evidence_id is not None:
                resolved,context_rebinding=bind_adapter_job_context(resolved,review_evidence_id=node.context_boundary_review_evidence_id)
            try:
                receipt=run_adapter_job(resolved,providers=providers,maximum_job_bytes=q.maximum_job_bytes)
            except Exception as error:
                outcome=dict(status="failed",executed=True,error_type=type(error).__name__,
                             resolved_job=resolved.model_dump(mode="json"),bindings=binding_receipts,
                             context_rebinding=context_rebinding)
            else:
                native_status=receipt["result"].get("status")
                outcome=dict(status="accepted" if native_status in node.accepted_statuses else "withheld",
                             executed=True,native_status=native_status,receipt=receipt,bindings=binding_receipts,
                             context_rebinding=context_rebinding)
        outcome=json_value(outcome)
        total_bytes+=len(json.dumps(outcome,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        if total_bytes>q.maximum_total_result_bytes:
            raise ValueError("campaign exceeded its declared total result budget")
        results[node.id]=outcome
    body=dict(schema="pipeline_integrity.adapter_campaign.v1",request=q.model_dump(mode="json"),
              execution_order=[n.id for n in _order(q.nodes)],nodes=results,
              status="computed" if all(r["status"]=="accepted" for r in results.values()) else "partial",
              total_result_bytes=total_bytes,engineering_qualification_conferred=False,
              converted_values=converted_values,
              implementation_sha256=file_digest(Path(__file__)))
    body["record_digest"]=digest(body)
    return body


def verify_adapter_campaign_record(record, *, request=None, providers=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("adapter campaign receipt changed")
    if any(node.get("status")=="failed" for node in record["nodes"].values()):
        raise ValueError("failed model executions require diagnosis; matching error types do not establish replay")
    q=AdapterCampaign.model_validate_json(json.dumps(record["request"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("campaign caller request differs")
    if run_adapter_campaign(q,providers=providers)!=record:
        raise ValueError("adapter campaign replay differs")
    return True
