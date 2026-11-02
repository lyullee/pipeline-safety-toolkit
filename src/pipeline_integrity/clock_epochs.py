"""Caller-declared clock reset/recalibration epochs around the affine owner API.

Epoch boundaries, original row identities, source clock domains and review
records are supplied explicitly. Source times are never sorted, reset points
are never inferred, and no invalid observations are deleted. Candidate target
time intervals must remain ordered and disjoint under the supplied bounds.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .clock_correction import ClockCorrectionModel, apply_clock_correction_candidate

SHA=r"^[a-f0-9]{64}$"


class ClockEpoch(Contract):
    id: Name
    boundary_kind: Literal["initial","clock_reset","recalibration","source_clock_change"]
    source_record_id: Name
    source_record_sha256: str = Field(pattern=SHA)
    source_context: RunContext
    model: ClockCorrectionModel
    source_frames: tuple[SignalFrame, ...] = Field(min_length=1,max_length=1000000)
    source_frames_digest: str = Field(pattern=SHA)
    observation_ids: tuple[Name, ...]
    source_row_indices: tuple[int, ...]
    source_time_error_bounds_s: tuple[Nonnegative, ...]
    source_coverage_start_s: Nonnegative
    source_coverage_end_s: Positive
    source_evidence_id: Name
    model_evidence_sha256: str = Field(pattern=SHA)
    review_evidence_sha256: str = Field(pattern=SHA)
    boundary_evidence_id: Name
    boundary_evidence_sha256: str = Field(pattern=SHA)
    mapping_residual_bound_basis: Literal["reference_clock_seconds"]
    mapping_residual_covers_parameter_uncertainty_reviewed: bool = False

    @model_validator(mode="after")
    def aligned(self):
        n=len(self.source_frames)
        if any(len(v)!=n for v in (self.observation_ids,self.source_row_indices,self.source_time_error_bounds_s)):
            raise ValueError("epoch source frames, row identities and timing bounds must align")
        if len(set(self.observation_ids))!=n:
            raise ValueError("observation identifiers must be unique inside each clock epoch")
        if any(type(i) is not int or i<0 for i in self.source_row_indices):
            raise ValueError("source row indices must be explicit nonnegative integers")
        if any(b<=a for a,b in zip(self.source_row_indices,self.source_row_indices[1:])):
            raise ValueError("epoch source row indices must increase without reordering")
        if self.source_coverage_end_s<=self.source_coverage_start_s:
            raise ValueError("epoch source coverage must have positive duration")
        if digest(self.source_frames)!=self.source_frames_digest:
            raise ValueError("clock epoch source frames differ from their declared digest")
        if self.source_context.clock_id!=self.model.source_clock_id:
            raise ValueError("clock epoch source context must identify its model source domain")
        return self


class ClockEpochRequest(Contract):
    id: Name
    asset_id: Name
    reference_clock_id: Name
    epochs: tuple[ClockEpoch, ...] = Field(min_length=1,max_length=256)
    epoch_order_evidence_id: Name
    epoch_order_evidence_sha256: str = Field(pattern=SHA)
    epoch_order_and_boundaries_reviewed: bool = False
    maximum_frames: int = Field(default=100000,ge=1,le=1000000)
    maximum_channel_values: int = Field(default=2000000,ge=1,le=10000000)
    maximum_result_bytes: int = Field(default=128000000,ge=1,le=1000000000)

    @model_validator(mode="after")
    def epoch_sequence(self):
        if len({e.id for e in self.epochs})!=len(self.epochs):
            raise ValueError("clock epoch IDs must be unique")
        if self.epochs[0].boundary_kind!="initial" or any(e.boundary_kind=="initial" for e in self.epochs[1:]):
            raise ValueError("only the first declared clock epoch is the initial epoch")
        if sum(len(e.source_frames) for e in self.epochs)>self.maximum_frames:
            raise ValueError("clock epochs exceed the explicit frame budget")
        if sum(len(f.values) for e in self.epochs for f in e.source_frames)>self.maximum_channel_values:
            raise ValueError("clock epochs exceed the explicit channel-value budget")
        seen=set()
        for index,epoch in enumerate(self.epochs):
            if epoch.model.reference_clock_id!=self.reference_clock_id:
                raise ValueError("every epoch must explicitly map to the same declared reference clock")
            for row in epoch.source_row_indices:
                key=(epoch.source_record_id,epoch.source_record_sha256,row)
                if key in seen:
                    raise ValueError("one original source row cannot be silently reused across clock epochs")
                seen.add(key)
            if index:
                previous=self.epochs[index-1]
                changed=epoch.model.source_clock_id!=previous.model.source_clock_id
                if epoch.boundary_kind=="source_clock_change" and not changed:
                    raise ValueError("source-clock-change boundaries must identify a different explicit source clock")
                if epoch.boundary_kind in {"clock_reset","recalibration"} and changed:
                    raise ValueError("a different source clock requires an explicit source_clock_change boundary")
        return self


def _evidence_bound(identity,expected,evidence,label):
    value=evidence.get(identity)
    if value is None or value.sha256!=expected:
        raise ValueError(label+" evidence is absent or differs from its pinned source hash")
    return value


def evaluate_clock_epochs(request: ClockEpochRequest, context: RunContext):
    """Correct complete explicit epochs and join them in the supplied source order.

    Target bounds use worst-case addition: source bound divided by the reviewed
    positive clock-rate ratio, plus the model residual bound in reference-clock
    seconds. This is not a confidence interval or an estimated reset detector.
    """
    q=ClockEpochRequest.model_validate(request.model_dump(mode="python"))
    target=RunContext.model_validate(context.model_dump(mode="python"))
    implementation_source=file_digest(Path(__file__))
    affine_source=file_digest(Path(__file__).with_name("clock_correction.py"))
    if (q.asset_id,q.reference_clock_id)!=(target.asset_id,target.clock_id):
        raise ValueError("joined clock-epoch asset/reference domain differs from context")
    if not q.epoch_order_and_boundaries_reviewed:
        raise ValueError("caller review of clock-epoch order and reset boundaries is required")
    target_evidence={e.id:e for e in target.evidence}
    if len(target_evidence)!=len(target.evidence):
        raise ValueError("clock epoch target evidence IDs must be unique")
    _evidence_bound(q.epoch_order_evidence_id,q.epoch_order_evidence_sha256,target_evidence,"epoch order")
    frames=[]
    lineage=[]
    epoch_results=[]
    previous_target_upper=None
    previous_coverage_upper=None
    for epoch in q.epochs:
        source=epoch.source_context
        if (source.asset_id,source.scenario_id,source.origin)!=(target.asset_id,target.scenario_id,target.origin):
            raise ValueError("clock epochs cannot silently merge assets, scenarios or observation origins")
        source_evidence={e.id:e for e in source.evidence}
        if len(source_evidence)!=len(source.evidence):
            raise ValueError("each clock epoch needs unique source evidence IDs")
        for old in source.evidence:
            if old.id not in target_evidence or old.model_dump(mode="json")!=target_evidence[old.id].model_dump(mode="json"):
                raise ValueError("joined target context must preserve every source-epoch evidence record unchanged")
        _evidence_bound(epoch.source_evidence_id,epoch.source_record_sha256,source_evidence,"epoch original source")
        _evidence_bound(epoch.model.model_evidence_id,epoch.model_evidence_sha256,source_evidence,"epoch affine mapping")
        _evidence_bound(epoch.model.review_evidence_id,epoch.review_evidence_sha256,source_evidence,"epoch mapping review")
        _evidence_bound(epoch.boundary_evidence_id,epoch.boundary_evidence_sha256,source_evidence,"epoch boundary")
        if not epoch.mapping_residual_covers_parameter_uncertainty_reviewed:
            raise ValueError("each epoch needs an explicit end-to-end affine residual-bound review")
        model=epoch.model
        if not (model.valid_source_start_s<=epoch.source_coverage_start_s
                and epoch.source_coverage_end_s<=model.valid_source_end_s):
            raise ValueError("the complete declared source epoch must lie inside its affine model support")
        denominator=1.0+model.rate_error
        factor=1.0/denominator
        if not math.isfinite(factor) or factor<=0:
            raise ValueError("clock epoch rate mapping is outside positive finite arithmetic")

        def mapped(value):
            result=model.reference_epoch_s+(value-model.source_epoch_s-model.offset_s)/denominator
            if not math.isfinite(result):
                raise ValueError("epoch coverage mapping exceeded finite arithmetic")
            return result

        nominal_coverage_lower=mapped(epoch.source_coverage_start_s)
        nominal_coverage_upper=mapped(epoch.source_coverage_end_s)
        if nominal_coverage_lower<0 or nominal_coverage_upper<=nominal_coverage_lower:
            raise ValueError("the complete nominal target epoch must remain in the nonnegative reference domain")
        coverage_lower=nominal_coverage_lower-model.residual_bound_s
        coverage_upper=nominal_coverage_upper+model.residual_bound_s
        if model.residual_bound_s>0:
            coverage_lower=math.nextafter(coverage_lower,-math.inf)
            coverage_upper=math.nextafter(coverage_upper,math.inf)
        if not math.isfinite(coverage_lower) or not math.isfinite(coverage_upper) or coverage_upper<=coverage_lower:
            raise ValueError("clock epoch target coverage is not a finite positive-duration interval")
        if previous_coverage_upper is not None and coverage_lower<previous_coverage_upper:
            raise ValueError("target clock epoch coverage overlaps or reverses; input is never sorted or trimmed")
        for frame,error in zip(epoch.source_frames,epoch.source_time_error_bounds_s):
            lower=frame.time_s-error
            upper=frame.time_s+error
            if error>0:
                lower=math.nextafter(lower,-math.inf)
                upper=math.nextafter(upper,math.inf)
            if not math.isfinite(lower) or not math.isfinite(upper):
                raise ValueError("source clock error bound exceeded finite arithmetic")
            if lower<epoch.source_coverage_start_s or upper>epoch.source_coverage_end_s:
                raise ValueError("an epoch observation/error interval extends outside the complete declared source coverage")
            if lower<model.valid_source_start_s or upper>model.valid_source_end_s:
                raise ValueError("an epoch observation/error interval extends outside affine-model validity")
        original=apply_clock_correction_candidate(epoch.source_frames,model,source,
                                                   observation_ids=epoch.observation_ids)
        original.check_integrity()
        if len(original.candidate_frames)!=len(epoch.source_frames):
            raise ValueError("affine owner changed the epoch frame count")
        begin=len(frames)
        for position,(before,after,error,row_index,observation_id) in enumerate(zip(
            epoch.source_frames,original.candidate_frames,epoch.source_time_error_bounds_s,
            epoch.source_row_indices,epoch.observation_ids)):
            source_payload=before.model_dump(mode="json",exclude={"clock_id","time_s"})
            target_payload=after.model_dump(mode="json",exclude={"clock_id","time_s"})
            if source_payload!=target_payload:
                raise ValueError("clock correction must preserve values, invalid flags, source origin and absolute timestamp")
            scaled_source_error=error*math.nextafter(factor,math.inf) if error else 0.0
            if error>0:
                if scaled_source_error==0:
                    raise ValueError("nonzero source timing uncertainty underflow is not exact target timing")
                scaled_source_error=math.nextafter(scaled_source_error,math.inf)
            target_error=math.fsum((scaled_source_error,model.residual_bound_s))
            if target_error>0:
                target_error=math.nextafter(target_error,math.inf)
            lower=after.time_s-target_error
            upper=after.time_s+target_error
            if target_error>0:
                lower=math.nextafter(lower,-math.inf)
                upper=math.nextafter(upper,math.inf)
            if not all(math.isfinite(v) for v in (target_error,lower,upper)):
                raise ValueError("target clock error-bound propagation exceeded finite arithmetic")
            if previous_target_upper is not None and lower<=previous_target_upper:
                raise ValueError("target observation time/error intervals overlap or reorder; no rows are discarded")
            joined_id=digest(dict(epoch_id=epoch.id,source_observation_id=observation_id,
                                  source_record_id=epoch.source_record_id,source_row_index=row_index))
            frames.append(after)
            lineage.append(dict(joined_observation_id=joined_id,epoch_id=epoch.id,
                epoch_boundary_kind=epoch.boundary_kind,source_observation_id=observation_id,
                source_record_id=epoch.source_record_id,source_record_sha256=epoch.source_record_sha256,
                source_row_index=row_index,source_epoch_position=position,
                source_clock_id=before.clock_id,source_time_s=before.time_s,
                source_time_error_bound_s=error,target_clock_id=after.clock_id,target_time_s=after.time_s,
                target_time_error_bound_s=target_error,target_time_interval_s=[lower,upper],
                propagated_source_time_error_bound_s=scaled_source_error,
                affine_mapping_residual_bound_s=model.residual_bound_s,
                source_error_reference_scale=factor,source_frame_digest=digest(before),candidate_frame_digest=digest(after)))
            previous_target_upper=upper
        previous_coverage_upper=coverage_upper
        epoch_results.append(dict(epoch_id=epoch.id,boundary_kind=epoch.boundary_kind,
            source_record_id=epoch.source_record_id,source_record_sha256=epoch.source_record_sha256,
            source_clock_id=model.source_clock_id,reference_clock_id=model.reference_clock_id,
            source_coverage_s=[epoch.source_coverage_start_s,epoch.source_coverage_end_s],
            target_coverage_with_mapping_bound_s=[coverage_lower,coverage_upper],
            output_start_index=begin,output_count=len(original.candidate_frames),
            affine_owner_record=original.model_dump(mode="json")))
    body=dict(schema="pipeline_integrity.clock_epochs.v1",request=q.model_dump(mode="json"),
        context=target.model_dump(mode="json"),status="computed",epoch_results=epoch_results,
        candidate_frames=[f.model_dump(mode="json") for f in frames],lineage=lineage,
        candidate_frames_digest=digest(tuple(frames)),source_frame_count=sum(len(e.source_frames) for e in q.epochs),
        target_frame_count=len(frames),dropped_frame_count=0,source_order_preserved=True,
        source_invalid_channel_count=sum(not valid for e in q.epochs for f in e.source_frames for valid in f.valid.values()),
        target_invalid_channel_count=sum(not valid for f in frames for valid in f.valid.values()),
        reset_boundaries_inferred=False,source_clock_domains_automatically_identified=False,
        per_epoch_candidate_contexts_preserved=True,source_metadata_automatically_merged=False,
        error_interval_outward_rounding_applied=True,
        timestamp_utc_reinterpreted=False,error_bounds_are_probability_intervals=False,
        synchronization_proven=False,causal_alignment_proven=False,field_qualification=False,
        implementation_sha256=implementation_source,
        affine_owner_source_sha256=affine_source,
        numerical_validation_completed=False)
    body=json_value(body)
    if (file_digest(Path(__file__))!=implementation_source
        or file_digest(Path(__file__).with_name("clock_correction.py"))!=affine_source):
        raise ValueError("clock epoch or affine owner source changed during evaluation")
    body["record_digest"]=digest(body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("complete clock epoch receipt exceeds its result budget; no partial epoch is emitted")
    return body


def verify_clock_epochs_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("clock epoch receipt changed")
    q=ClockEpochRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("clock epoch caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("clock epoch caller context differs")
    if evaluate_clock_epochs(q,c)!=record:
        raise ValueError("clock epoch source mapping or joined timeline replay differs")
    return True


def joined_clock_epoch_frames(record):
    """Return all replayed target frames and their unchanged source-row lineage."""
    verify_clock_epochs_record(record)
    frames=tuple(SignalFrame.model_validate_json(json.dumps(f)) for f in record["candidate_frames"])
    return frames,tuple(record["lineage"])
