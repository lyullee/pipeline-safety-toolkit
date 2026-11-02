"""Reviewed scalar fixed-point coupling over existing allowlisted model campaigns."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .adapter_campaign import AdapterCampaign, _tokens, _read, run_adapter_campaign
from .units import convert_quantity


class FeedbackLink(Contract):
    id: Name
    source_node_id: Name
    source_result_pointer: str
    target_node_id: Name
    target_scope: Literal["parameters", "payloads"] = "parameters"
    target_pointer: str
    source_unit: Name
    target_unit: Name
    lower_bound: float
    upper_bound: float
    absolute_tolerance: Positive
    relative_tolerance: Nonnegative = 0.0
    relaxation: float = Field(default=1.0, gt=0, le=1)
    mapping_evidence_id: Name


class AdapterFeedbackRequest(Contract):
    id: Name
    campaign: AdapterCampaign
    links: tuple[FeedbackLink, ...] = Field(min_length=1,max_length=256)
    maximum_iterations: int = Field(default=30,ge=1,le=1000)
    maximum_total_result_bytes: int = Field(default=128000000,ge=1,le=1000000000)
    fixed_point_mapping_and_bounds_reviewed: bool = False


def _scalar(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError("feedback mappings require finite scalar values")
    return float(value)


def evaluate_adapter_feedback(request: AdapterFeedbackRequest, context: RunContext, *, providers=None):
    q=AdapterFeedbackRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.fixed_point_mapping_and_bounds_reviewed:
        raise ValueError("feedback equations, branch bounds and scalar mapping require review")
    if len({v.id for v in q.links})!=len(q.links):
        raise ValueError("feedback link IDs must be unique")
    nodes={v.id:v for v in q.campaign.nodes}
    if any(n.job.adapter_id=="workflow.fixed_point" for n in nodes.values()):
        raise ValueError("feedback campaigns cannot recursively dispatch another feedback campaign")
    writes=[]
    identities=[e.id for e in c.evidence]
    if len(set(identities))!=len(identities):
        raise ValueError("feedback context evidence must be unambiguous")
    for node in nodes.values():
        nc=node.job.context
        if (nc.asset_id,nc.clock_id,nc.scenario_id,nc.origin)!=(c.asset_id,c.clock_id,c.scenario_id,c.origin):
            raise ValueError("feedback nodes require a common reviewed asset, scenario, clock and origin")
    for link in q.links:
        if link.source_node_id not in nodes or link.target_node_id not in nodes:
            raise ValueError("feedback link references an unknown campaign node")
        _tokens(link.source_result_pointer,root_allowed=True)
        target=_tokens(link.target_pointer)
        if link.lower_bound>=link.upper_bound:
            raise ValueError("feedback lower bound must be less than upper bound")
        evidence=[e for e in c.evidence if e.id==link.mapping_evidence_id]
        target_evidence=[e for e in nodes[link.target_node_id].job.context.evidence if e.id==link.mapping_evidence_id]
        if len(evidence)!=1 or evidence[0].sha256 is None or len(target_evidence)!=1 or digest(evidence[0])!=digest(target_evidence[0]):
            raise ValueError("feedback requires the same source-pinned mapping evidence in request and destination")
        for other_node,other_scope,other in writes:
            if (other_node,other_scope)==(link.target_node_id,link.target_scope) and (target[:len(other)]==other or other[:len(target)]==target):
                raise ValueError("feedback target writes must not overlap")
        for binding in nodes[link.target_node_id].input_bindings:
            other=_tokens(binding.target_parameter_pointer)
            if binding.target_scope==link.target_scope and (target[:len(other)]==other or other[:len(target)]==target):
                raise ValueError("campaign feedforward binding would overwrite the feedback unknown")
        writes.append((link.target_node_id,link.target_scope,target))
        initial=_scalar(_read(getattr(nodes[link.target_node_id].job,link.target_scope),target))
        if not link.lower_bound<=initial<=link.upper_bound:
            raise ValueError("initial feedback unknown lies outside its reviewed branch bounds")
    current=q.campaign.model_dump(mode="json")
    positions={n["id"]:i for i,n in enumerate(current["nodes"])}
    history=[]
    total_bytes=0
    source_before={name:file_digest(Path(__file__).with_name(name+".py"))
                   for name in ("adapter_feedback","adapter_campaign","adapter_jobs","contracts","units")}
    status="withheld"
    reason="iteration_limit"
    for iteration in range(q.maximum_iterations):
        campaign=AdapterCampaign.model_validate_json(json.dumps(current,allow_nan=False))
        outcome=run_adapter_campaign(campaign,providers=providers)
        retained=dict(iteration=iteration,campaign=outcome,links=[])
        if outcome["status"]!="computed":
            reason="child_model_not_accepted"
            history.append(retained)
            total_bytes+=len(json.dumps(retained,allow_nan=False).encode("utf-8"))
            if total_bytes>q.maximum_total_result_bytes:
                raise ValueError("feedback history exceeded its result budget")
            break
        updates=[]
        converged=True
        for link in q.links:
            result=outcome["nodes"][link.source_node_id]["receipt"]["result"]
            raw=_scalar(_read(result,_tokens(link.source_result_pointer,root_allowed=True)))
            mapped=_scalar(convert_quantity(raw,link.source_unit,link.target_unit))
            document=current["nodes"][positions[link.target_node_id]]["job"][link.target_scope]
            tokens=_tokens(link.target_pointer)
            previous=_scalar(_read(document,tokens))
            residual=mapped-previous
            tolerance=link.absolute_tolerance+link.relative_tolerance*max(abs(mapped),abs(previous))
            within_bounds=link.lower_bound<=mapped<=link.upper_bound
            accepted=within_bounds and abs(residual)<=tolerance
            converged=converged and accepted
            delivered=_scalar(previous+link.relaxation*residual)
            retained["links"].append(dict(id=link.id,source_value=raw,mapped_value=mapped,
                current_input=previous,residual=residual,tolerance=tolerance,converged=accepted,
                source_unit=link.source_unit,target_unit=link.target_unit,within_bounds=within_bounds,
                next_input=delivered,mapping_evidence_id=link.mapping_evidence_id))
            if not within_bounds:
                reason="mapped_value_outside_reviewed_bounds"
            updates.append((document,tokens,delivered))
        history.append(retained)
        total_bytes+=len(json.dumps(retained,allow_nan=False).encode("utf-8"))
        if total_bytes>q.maximum_total_result_bytes:
            raise ValueError("feedback history exceeded its result budget")
        if converged:
            status="computed_diagnostic"
            reason="all_unrelaxed_mapping_residuals_within_tolerance"
            break
        if reason=="mapped_value_outside_reviewed_bounds":
            break
        for document,tokens,value in updates:
            parent=_read(document,tokens[:-1])
            if isinstance(parent,dict):
                parent[tokens[-1]]=value
            else:
                parent[int(tokens[-1])]=value
    if source_before!={name:file_digest(Path(__file__).with_name(name+".py")) for name in source_before}:
        raise ValueError("feedback implementation changed during coupling")
    body=dict(schema="pipeline_integrity.adapter_feedback.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status=status,reason=reason,history=history,
              total_result_bytes=total_bytes,source_sha256=source_before,
              solution_uniqueness_proven=False,transient_stability_proven=False,
              numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_adapter_feedback_record(record, *, request=None, context=None, providers=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("feedback receipt changed")
    if any(any(node.get("status")=="failed" for node in row["campaign"]["nodes"].values()) for row in record["history"]):
        raise ValueError("failed model execution requires diagnosis before feedback replay")
    q=AdapterFeedbackRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("feedback caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("feedback caller context differs")
    if evaluate_adapter_feedback(q,c,providers=providers)!=record:
        raise ValueError("feedback replay differs")
    return True
