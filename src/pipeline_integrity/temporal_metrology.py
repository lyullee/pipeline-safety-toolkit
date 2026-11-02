"""Linear-time uncertainty for weighted measurements with reset-aware OU errors.

White errors, an exponential temporal component, and independent shared error
modes are explicitly supplied. Shared modes can represent reviewed bias/drift
loadings. No statistical parameters, reset times or missing data are fitted.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .metrology import MeasurementBasis
from .units import convert_quantity


class SharedTemporalMode(Contract):
    id: Name
    standard_effect_by_sample: tuple[float, ...] = Field(min_length=1,max_length=1000000)
    evidence_id: Name


class TemporalWeightedRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    times_s: tuple[Nonnegative, ...] = Field(min_length=1,max_length=1000000)
    estimates: tuple[float, ...]
    weights: tuple[float, ...]
    weight_unit: Name
    input_basis: MeasurementBasis
    output_basis: MeasurementBasis
    white_standard_uncertainties: tuple[Nonnegative, ...]
    temporal_standard_uncertainties: tuple[Nonnegative, ...]
    correlation_time_s: Positive
    independent_reset_groups: tuple[Name, ...]
    shared_modes: tuple[SharedTemporalMode, ...] = Field(default=(),max_length=128)
    supplied_error_components_independent_reviewed: bool = False
    exact_linear_measurand_reviewed: bool = False
    maximum_mode_samples: int = Field(default=4000000,ge=1,le=16000000)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def aligned(self):
        n=len(self.times_s)
        if any(len(v)!=n for v in (self.estimates,self.weights,self.white_standard_uncertainties,
                                    self.temporal_standard_uncertainties,self.independent_reset_groups)):
            raise ValueError("temporal metrology arrays must use one sample order")
        for i,(a,b) in enumerate(zip(self.times_s,self.times_s[1:])):
            if b<a or (b==a and self.independent_reset_groups[i]==self.independent_reset_groups[i+1]):
                raise ValueError("temporal times must increase; equal instants require distinct reset groups")
        seen=set()
        previous=None
        for group in self.independent_reset_groups:
            if group!=previous:
                if group in seen:
                    raise ValueError("a reset group cannot recur after another group")
                seen.add(group)
                previous=group
        if len({m.id for m in self.shared_modes})!=len(self.shared_modes):
            raise ValueError("shared temporal mode IDs must be unique")
        if any(len(m.standard_effect_by_sample)!=n for m in self.shared_modes):
            raise ValueError("each shared temporal mode must follow every sample")
        if len(self.shared_modes)*n>self.maximum_mode_samples:
            raise ValueError("shared temporal modes exceed the declared work budget")
        return self


def evaluate_temporal_weighted_budget(request: TemporalWeightedRequest, context: RunContext):
    q=TemporalWeightedRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.supplied_error_components_independent_reviewed or not q.exact_linear_measurand_reviewed:
        raise ValueError("independent component and linear measurand reviews are required")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("temporal metrology asset or clock differs from context")
    required=set(q.evidence_ids)|{q.input_basis.evidence_id,q.output_basis.evidence_id}|{m.evidence_id for m in q.shared_modes}
    if not required <= {e.id for e in c.evidence}:
        raise ValueError("temporal uncertainty or basis evidence is absent")
    if q.input_basis.reference_state!=q.output_basis.reference_state:
        raise ValueError("temporal weighting cannot change a measurement reference state")
    scale=convert_quantity(1.0,f"({q.input_basis.unit}) * ({q.weight_unit})",q.output_basis.unit)
    zero=convert_quantity(0.0,f"({q.input_basis.unit}) * ({q.weight_unit})",q.output_basis.unit)
    if not math.isfinite(scale) or scale<=0 or zero!=0:
        raise ValueError("temporal weighting units are not linearly compatible")
    white=math.fsum((w*s)**2 for w,s in zip(q.weights,q.white_standard_uncertainties))
    a=[w*s for w,s in zip(q.weights,q.temporal_standard_uncertainties)]
    # Backward innovation coefficients avoid cancellation from large positive
    # and negative covariance terms and require no dense N-by-N matrix.
    temporal_terms=[]
    beta=0.0
    n=len(a)
    for i in range(n-1,-1,-1):
        if i==n-1 or q.independent_reset_groups[i]!=q.independent_reset_groups[i+1]:
            beta=a[i]
        else:
            phi=math.exp(-(q.times_s[i+1]-q.times_s[i])/q.correlation_time_s)
            beta=a[i]+phi*beta
        if i==0 or q.independent_reset_groups[i]!=q.independent_reset_groups[i-1]:
            temporal_terms.append(beta*beta)
        else:
            dt=q.times_s[i]-q.times_s[i-1]
            innovation=-math.expm1(-2*dt/q.correlation_time_s)
            temporal_terms.append(innovation*beta*beta)
    temporal=math.fsum(temporal_terms)
    modes={m.id:math.fsum(w*s for w,s in zip(q.weights,m.standard_effect_by_sample))**2 for m in q.shared_modes}
    variance=(white+temporal+math.fsum(modes.values()))*scale*scale
    estimate=math.fsum(w*x for w,x in zip(q.weights,q.estimates))*scale
    if not math.isfinite(variance) or not math.isfinite(estimate):
        raise ValueError("temporal uncertainty exceeded finite arithmetic")
    body=dict(schema="pipeline_integrity.temporal_weighted_budget.v1",request=q.model_dump(mode="json"),
              status="computed",
              context=c.model_dump(mode="json"),estimate=estimate,variance=variance,
              standard_uncertainty=math.sqrt(variance),output_basis=q.output_basis.model_dump(mode="json"),
              variance_components=dict(white=white*scale*scale,temporal=temporal*scale*scale,
                                       shared_modes={k:v*scale*scale for k,v in modes.items()}),
              time_uncertainty_included=False,parameter_estimation_uncertainty_included=False,
              reset_independence="caller_reviewed",probability_or_coverage_inferred=False,
              implementation_sha256=file_digest(Path(__file__)),numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_temporal_weighted_budget_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("temporal weighted receipt changed")
    q=TemporalWeightedRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("temporal weighted caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("temporal weighted caller context differs")
    if evaluate_temporal_weighted_budget(q,c)!=record:
        raise ValueError("temporal weighted replay differs")
    return True


def temporal_budget_from_totalization(totalization, context: RunContext, *,
                                      white_standard_uncertainties,
                                      temporal_standard_uncertainties,
                                      correlation_time_s, independent_reset_groups,
                                      shared_modes=(), sample_error_times_s=None,
                                      evidence_ids, components_independence_reviewed=False):
    """Build fixed-time linear weights from the existing totalization contract.

    Interval-mean errors need explicit caller-reviewed correlation reference
    times; their temporal covariance is not inferred from midpoint timestamps.
    Counter reset groups describe error independence, not meter counter jumps.
    """
    from .totalization import TotalizationRequest, _policy
    q=TotalizationRequest.model_validate(totalization.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if _policy(q,c):
        raise ValueError("totalization applicability reviews remain unresolved")
    if q.maximum_interval_s is None:
        raise ValueError("a reviewed maximum totalization interval is required")
    if any(v.valid is not True or v.estimate is None for v in (*q.times,*q.readings)):
        raise ValueError("temporal budget requires complete valid readings and timestamps")
    times=[v.estimate+q.clock_offset_s for v in q.times]
    dt=[b-a for a,b in zip(times,times[1:])]
    if any(v>q.maximum_interval_s for v in dt):
        raise ValueError("temporal budget cannot bridge unobserved totalization gaps")
    values=[v.estimate for v in q.readings]
    if q.sign_convention=="nonnegative":
        checked=[b-a for a,b in zip(values,values[1:])] if q.sample_meaning=="cumulative_counter" else values
        if any(v<0 for v in checked):
            raise ValueError("counter decrease or negative rate conflicts with the declared direction")
    weights=[0.0]*len(values)
    if q.sample_meaning=="point_linear":
        for i,duration in enumerate(dt):
            weights[i]+=duration/2
            weights[i+1]+=duration/2
        weight_unit="s"
        sample_times=times
    elif q.sample_meaning=="interval_mean":
        if sample_error_times_s is None:
            raise ValueError("interval-mean correlation reference times must be explicitly supplied")
        weights=dt
        weight_unit="s"
        sample_times=list(sample_error_times_s)
        if len(sample_times)!=len(dt) or any(not a<=t<=b for a,t,b in zip(times,sample_times,times[1:])):
            raise ValueError("interval-mean error reference times must lie inside their original intervals")
    else:
        weights[0],weights[-1]=-1.0,1.0
        weight_unit="dimensionless"
        sample_times=times
    if sample_error_times_s is not None and q.sample_meaning!="interval_mean":
        raise ValueError("point/counter error times must use the original observed clock")
    request=TemporalWeightedRequest(
        id=q.id+":temporal_budget",asset_id=q.asset_id,clock_id=q.clock_id,
        times_s=tuple(sample_times),estimates=tuple(values),weights=tuple(weights),weight_unit=weight_unit,
        input_basis=q.readings[0].basis,output_basis=q.output_basis,
        white_standard_uncertainties=tuple(white_standard_uncertainties),
        temporal_standard_uncertainties=tuple(temporal_standard_uncertainties),
        correlation_time_s=correlation_time_s,independent_reset_groups=tuple(independent_reset_groups),
        shared_modes=tuple(shared_modes),supplied_error_components_independent_reviewed=components_independence_reviewed,
        exact_linear_measurand_reviewed=True,evidence_ids=tuple(dict.fromkeys((q.evidence_id,*evidence_ids))))
    body=dict(schema="pipeline_integrity.totalization_temporal_binding.v1",totalization_digest=digest(q),
              context_digest=digest(c),derived_request=request.model_dump(mode="json"),
              timestamp_uncertainty_included=False,counter_jumps_corrected=False)
    body["record_digest"]=digest(body)
    return request,body


class CounterEpoch(Contract):
    id: Name
    start_time_s: Nonnegative
    end_time_s: Positive
    start_reading: float
    end_reading: float
    white_standard_uncertainties: tuple[Nonnegative,Nonnegative]
    temporal_standard_uncertainties: tuple[Nonnegative,Nonnegative]
    evidence_id: Name

    @model_validator(mode="after")
    def ordered(self):
        if self.end_time_s<=self.start_time_s:
            raise ValueError("counter epoch must cover a positive observed duration")
        if self.end_reading<self.start_reading:
            raise ValueError("a nonnegative counter epoch cannot contain a counter decrease")
        return self


class ResetCounterRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    basis: MeasurementBasis
    epochs: tuple[CounterEpoch, ...] = Field(min_length=1,max_length=10000)
    correlation_time_s: Positive
    reset_boundary_evidence_ids: tuple[Name, ...] = ()
    shared_modes: tuple[SharedTemporalMode, ...] = ()
    instantaneous_resets_and_epoch_error_independence_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def continuous_coverage(self):
        if len({e.id for e in self.epochs})!=len(self.epochs):
            raise ValueError("counter epochs must have unique IDs")
        if len(self.reset_boundary_evidence_ids)!=len(self.epochs)-1:
            raise ValueError("every counter reset requires its explicit boundary evidence")
        if any(a.end_time_s!=b.start_time_s for a,b in zip(self.epochs,self.epochs[1:])):
            raise ValueError("counter epochs must cover contiguous intervals without inferring missing transfers")
        return self


def evaluate_reset_counter(request: ResetCounterRequest, context: RunContext):
    q=ResetCounterRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.instantaneous_resets_and_epoch_error_independence_reviewed:
        raise ValueError("instantaneous reset and error-independence review is required")
    evidence={e.id:e for e in c.evidence}
    for identity in q.reset_boundary_evidence_ids:
        if identity not in evidence or evidence[identity].sha256 is None:
            raise ValueError("each counter reset needs source-hash-pinned boundary evidence")
    weighted=TemporalWeightedRequest(
        id=q.id+":weighted_epochs",asset_id=q.asset_id,clock_id=q.clock_id,
        times_s=tuple(t for e in q.epochs for t in (e.start_time_s,e.end_time_s)),
        estimates=tuple(v for e in q.epochs for v in (e.start_reading,e.end_reading)),
        weights=(-1.0,1.0)*len(q.epochs),weight_unit="dimensionless",input_basis=q.basis,output_basis=q.basis,
        white_standard_uncertainties=tuple(v for e in q.epochs for v in e.white_standard_uncertainties),
        temporal_standard_uncertainties=tuple(v for e in q.epochs for v in e.temporal_standard_uncertainties),
        correlation_time_s=q.correlation_time_s,independent_reset_groups=tuple(e.id for e in q.epochs for _ in range(2)),
        shared_modes=q.shared_modes,supplied_error_components_independent_reviewed=True,
        exact_linear_measurand_reviewed=True,
        evidence_ids=tuple(dict.fromkeys((*q.evidence_ids,*q.reset_boundary_evidence_ids,*(e.evidence_id for e in q.epochs)))))
    result=evaluate_temporal_weighted_budget(weighted,c)
    body=dict(schema="pipeline_integrity.reset_counter_total.v1",request=q.model_dump(mode="json"),
              status="computed",
              context=c.model_dump(mode="json"),total=result["estimate"],uncertainty=result,
              observed_start_time_s=q.epochs[0].start_time_s,observed_end_time_s=q.epochs[-1].end_time_s,
              unobserved_transfer_imputed=False,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_reset_counter_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("reset counter receipt changed")
    q=ResetCounterRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("reset counter caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("reset counter caller context differs")
    if evaluate_reset_counter(q,c)!=record:
        raise ValueError("reset counter replay differs")
    return True
