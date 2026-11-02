"""Bounded continuous decisions evaluated by the actual shared adapter API."""
from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Contract, Name, Positive, RunContext, digest, require_context_extension
from .datasets import file_digest
from .adapter_jobs import AdapterJob, adapter_specs, run_adapter_job
from .adapter_campaign import _tokens, _read, bind_adapter_job_context
from .units import convert_quantity


class ContinuousDecision(Contract):
    id: Name
    parameter_pointer: str
    lower_bound: float
    upper_bound: float
    unit: Name
    target_unit: Name
    evidence_id: Name


class OptimizationReadout(Contract):
    id: Name
    result_pointer: str
    source_unit: Name
    unit: Name
    normalization_scale: Positive
    evidence_id: Name
    lower_bound: float | None = None
    upper_bound: float | None = None


class ContinuousOptimizationRequest(Contract):
    id: Name
    job: AdapterJob
    decisions: tuple[ContinuousDecision,...] = Field(min_length=1,max_length=30)
    objective: OptimizationReadout
    sense: Literal["minimize","maximize"] = "minimize"
    constraints: tuple[OptimizationReadout,...] = Field(default=(),max_length=100)
    accepted_statuses: tuple[Literal["computed","estimated","ready","computed_diagnostic"],...] = ("computed",)
    context_boundary_review_evidence_id: Name | None = None
    domain_units_objective_and_constraints_reviewed: bool = False
    seed: int = Field(default=0,ge=0,le=4294967295)
    population_multiplier: int = Field(default=10,ge=5,le=50)
    maximum_generations: int = Field(default=50,ge=1,le=1000)
    maximum_model_evaluations: int = Field(default=10000,ge=10,le=1000000)
    relative_solver_tolerance: Positive = Field(default=1e-5,lt=0.1)
    maximum_result_bytes: int = Field(default=128000000,ge=1000,le=1000000000)


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError("continuous optimization requires finite scalar quantities")
    return float(value)


def _reject_recursive_searches(job):
    forbidden={"economics.continuous_optimization"}
    pending=[job.model_dump(mode="json")]
    inspected=0
    while pending:
        value=pending.pop()
        inspected+=1
        if inspected>200000:
            raise ValueError("optimization job exceeds nested input inspection budget")
        if isinstance(value,dict):
            identity=value.get("adapter_id")
            if isinstance(identity,str) and identity in forbidden:
                raise ValueError("optimization cannot recursively dispatch a nested continuous search")
            pending.extend(value.values())
        elif isinstance(value,list):
            pending.extend(value)


def evaluate_continuous_optimization(request: ContinuousOptimizationRequest, context: RunContext, *, providers=None):
    import numpy as np
    import scipy
    from scipy.optimize import differential_evolution, NonlinearConstraint
    q=ContinuousOptimizationRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.domain_units_objective_and_constraints_reviewed:
        raise ValueError("decision domain, units, scalar objective and constraints require review")
    _reject_recursive_searches(q.job)
    require_context_extension(q.job.context,c)
    ids=[d.id for d in q.decisions]
    if len(set(ids))!=len(ids) or not q.accepted_statuses or len(set(q.accepted_statuses))!=len(q.accepted_statuses):
        raise ValueError("decision IDs and accepted model statuses must be explicit")
    evidence={e.id:e for e in c.evidence}
    required={v.evidence_id for v in (*q.decisions,q.objective,*q.constraints)}
    if q.context_boundary_review_evidence_id is not None:
        required.add(q.context_boundary_review_evidence_id)
    if any(key not in evidence or evidence[key].sha256 is None for key in required):
        raise ValueError("continuous optimization requires source-pinned mapping and quantity evidence")
    paths=[]
    for d in q.decisions:
        path=_tokens(d.parameter_pointer)
        if d.lower_bound>=d.upper_bound:
            raise ValueError("continuous decision bounds require positive width")
        if not math.isfinite(d.upper_bound-d.lower_bound):
            raise ValueError("continuous decision interval width exceeds finite arithmetic")
        _number(_read(q.job.parameters,path))
        for other in paths:
            if path[:len(other)]==other or other[:len(path)]==path:
                raise ValueError("continuous decision parameter writes cannot overlap")
        paths.append(path)
        convert_quantity(d.lower_bound,d.unit,d.target_unit)
        convert_quantity(d.upper_bound,d.unit,d.target_unit)
    if q.objective.lower_bound is not None or q.objective.upper_bound is not None:
        raise ValueError("objective bounds belong in explicit feasibility constraints")
    for metric in (q.objective,*q.constraints):
        _tokens(metric.result_pointer,root_allowed=True)
        convert_quantity(0.0,metric.source_unit,metric.unit)
    if len({metric.id for metric in (q.objective,*q.constraints)})!=1+len(q.constraints):
        raise ValueError("objective and constraint readout IDs must be unique")
    for metric in q.constraints:
        if metric.lower_bound is None and metric.upper_bound is None:
            raise ValueError("each optimization constraint needs an explicit bound")
        if metric.lower_bound is not None and metric.upper_bound is not None and metric.lower_bound>metric.upper_bound:
            raise ValueError("optimization constraint bounds are reversed")
    bound_count=1+sum((v.lower_bound is not None)+(v.upper_bound is not None) for v in q.constraints)
    cache={}; samples=[]; total_bytes=0; model_evaluations=0; dispatch_identity=None
    selected=[spec for spec in adapter_specs() if spec["id"]==q.job.adapter_id]
    if len(selected)!=1:
        raise ValueError("optimization requires an actual allowlisted adapter")
    sources={name:file_digest(Path(__file__).with_name(name+".py"))
             for name in {"continuous_optimization","adapter_jobs","adapter_campaign","units","contracts","datasets",selected[0]["module"]}}
    reserve=len(json.dumps(dict(request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),
                                source_sha256=sources),allow_nan=False).encode("utf-8"))+4096
    if reserve>=q.maximum_result_bytes:
        raise ValueError("optimization request/context/source metadata alone exceeds retained-byte budget")
    sample_byte_budget=q.maximum_result_bytes-reserve
    if providers is not None and not isinstance(providers,dict):
        raise TypeError("optimization providers require an explicit alias-to-object dictionary")
    provider_registry=None if providers is None else dict(providers)
    sign=1 if q.sense=="minimize" else -1
    class WorkBudgetExceeded(RuntimeError):
        pass
    def evaluate(x):
        nonlocal total_bytes,model_evaluations,dispatch_identity
        key=tuple(float(v) for v in x)
        if len(key)!=len(q.decisions) or any(not math.isfinite(v) or not 0<=v<=1 for v in key):
            raise ValueError("optimizer supplied an invalid normalized decision vector")
        if key in cache:
            return cache[key]
        if model_evaluations>=q.maximum_model_evaluations:
            raise WorkBudgetExceeded("continuous optimization model-evaluation budget exhausted")
        payload=q.job.model_dump(mode="json"); payload["context"]=c.model_dump(mode="json")
        delivered={}
        for d,path,value in zip(q.decisions,paths,key):
            physical=d.lower_bound+value*(d.upper_bound-d.lower_bound)
            target=convert_quantity(physical,d.unit,d.target_unit)
            parent=_read(payload["parameters"],path[:-1])
            if isinstance(parent,dict):
                parent[path[-1]]=target
            else:
                parent[int(path[-1])]=target
            delivered[d.id]=dict(value=physical,unit=d.unit,delivered_value=target,target_unit=d.target_unit)
        job=AdapterJob.model_validate_json(json.dumps(payload,allow_nan=False))
        rebinding=None
        if q.context_boundary_review_evidence_id is not None:
            job,rebinding=bind_adapter_job_context(job,review_evidence_id=q.context_boundary_review_evidence_id)
        model_evaluations+=1
        receipt=run_adapter_job(job,providers=provider_registry)
        if dispatch_identity is None:
            dispatch_identity=receipt["identity"]
        elif dispatch_identity!=receipt["identity"]:
            raise ValueError("optimization sampled different adapter source identities")
        result=receipt["result"]; native_status=result.get("status")
        valid=native_status in q.accepted_statuses
        row=dict(index=len(samples),decisions=delivered,receipt=receipt,context_rebinding=rebinding,
                 native_status=native_status,accepted_model_status=valid,objective=None,constraint_values=None)
        objective=1e300; slacks=[-1.0]*bound_count
        if valid:
            values=[]
            for metric in (q.objective,*q.constraints):
                raw=_number(_read(result,_tokens(metric.result_pointer,root_allowed=True)))
                values.append(convert_quantity(raw,metric.source_unit,metric.unit))
            objective=sign*values[0]/q.objective.normalization_scale
            if not math.isfinite(objective):
                raise ValueError("normalized optimization objective is nonfinite")
            slacks=[0.0]
            for metric,value in zip(q.constraints,values[1:]):
                if metric.lower_bound is not None:
                    slacks.append((value-metric.lower_bound)/metric.normalization_scale)
                if metric.upper_bound is not None:
                    slacks.append((metric.upper_bound-value)/metric.normalization_scale)
            row.update(objective=values[0],constraint_values=values[1:])
        slacks=[_number(value) for value in slacks]
        row["normalized_feasibility_slacks"]=slacks
        row["feasible"]=valid and all(v>=0 for v in slacks)
        row_bytes=len(json.dumps(row,allow_nan=False).encode("utf-8"))+4
        if total_bytes+row_bytes>sample_byte_budget:
            raise WorkBudgetExceeded("continuous optimization result budget exhausted")
        samples.append(row)
        total_bytes+=row_bytes
        cache[key]=(objective,np.array(slacks))
        return cache[key]
    outcome=None; budget_exhausted=False
    try:
        outcome=differential_evolution(lambda x:evaluate(x)[0],[(0.0,1.0)]*len(q.decisions),
            constraints=(NonlinearConstraint(lambda x:evaluate(x)[1],0.0,np.inf),),
            maxiter=q.maximum_generations,popsize=q.population_multiplier,
            tol=q.relative_solver_tolerance,seed=q.seed,polish=False,workers=1,updating="immediate")
    except WorkBudgetExceeded:
        budget_exhausted=True
    feasible=[row for row in samples if row["feasible"]]
    best=min(feasible,key=lambda row:sign*row["objective"]) if feasible else None
    if sources!={name:file_digest(Path(__file__).with_name(name+".py")) for name in sources}:
        raise ValueError("continuous optimization source changed during evaluation")
    if digest(request)!=digest(q) or digest(context)!=digest(c):
        raise ValueError("continuous optimization caller request/context changed during evaluation")
    body=dict(schema="pipeline_integrity.continuous_optimization.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="computed_diagnostic" if best is not None and not budget_exhausted else "withheld",
        best_sample_index=None if best is None else best["index"],samples=samples,
        solver_success=False if outcome is None else bool(outcome.success),
        solver_message="declared_work_or_byte_budget_exhausted" if outcome is None else str(outcome.message),
        budget_exhausted=budget_exhausted,model_evaluations=model_evaluations,
        retained_model_evaluations=len(samples),unretained_model_evaluations=model_evaluations-len(samples),
        model_dispatch_identity=dispatch_identity,source_sha256=sources,
        scipy_version=scipy.__version__,search="bounded_seeded_differential_evolution_without_polishing",
        runtime_versions={name:version(name) for name in ("numpy","scipy","pydantic","pint")},
        global_optimum_proven=False,constraint_values_are_not_risk_qualification=True,
        budget_scope="outer_allowlisted_adapter_dispatches; native_internal_work_limits_stay_in_each_job",
        numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("complete continuous optimization receipt exceeds byte budget")
    return body


def verify_continuous_optimization_record(record, *, request=None, context=None, providers=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("continuous optimization receipt changed")
    q=ContinuousOptimizationRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("continuous optimization caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("continuous optimization caller context differs")
    if evaluate_continuous_optimization(q,c,providers=providers)!=record:
        raise ValueError("continuous optimization native-model search replay differs")
    return True
