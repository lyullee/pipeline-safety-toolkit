"""Frequency-band propagation comparisons composed from the existing acoustic solver.

Each band retains its own reviewed propagation speed and delay result. Bounds
are intersected only as deterministic consistency constraints; overlapping
bands are never treated as independent observations or probability evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, RunContext, digest
from .datasets import file_digest
from .acoustics import SampledTrace, DelayRequest, PipeSpan, localize_acoustic_leak


class PropagationBand(Contract):
    id: Name
    frequency_bounds_hz: tuple[Positive,Positive]
    span: PipeSpan
    narrowband_propagation_review_evidence_id: Name

    @model_validator(mode="after")
    def ordered(self):
        if self.frequency_bounds_hz[0]>=self.frequency_bounds_hz[1]:
            raise ValueError("acoustic frequency-band edges must increase")
        return self


class MultibandAcousticRequest(Contract):
    id: Name
    left: SampledTrace
    right: SampledTrace
    delay: DelayRequest
    bands: tuple[PropagationBand, ...] = Field(min_length=2,max_length=32)
    maximum_band_samples: int = Field(default=8000000,ge=64,le=64000000)

    @model_validator(mode="after")
    def coherent_geometry(self):
        if len({b.id for b in self.bands})!=len(self.bands):
            raise ValueError("acoustic band IDs must be unique")
        if self.delay.band_hz is not None:
            raise ValueError("multiband requests specify filter bands separately from the base delay settings")
        if len(self.bands)*(len(self.left.values)+len(self.right.values))>self.maximum_band_samples:
            raise ValueError("multiband acoustic work exceeds its sample budget")
        reference=self.bands[0].span.model_dump(mode="json")
        keys=("left_position_m","right_position_m","left_position_error_m","right_position_error_m",
              "left_sensor_id","right_sensor_id","geometry_source_id","source_bracketed")
        for band in self.bands[1:]:
            current=band.span.model_dump(mode="json")
            if any(current[k]!=reference[k] for k in keys):
                raise ValueError("frequency bands must share one reviewed sensor geometry")
        return self


def evaluate_multiband_acoustics(request: MultibandAcousticRequest, context: RunContext):
    q=MultibandAcousticRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    by_id={e.id:e for e in c.evidence}
    results=[]
    for band in q.bands:
        review=by_id.get(band.narrowband_propagation_review_evidence_id)
        if review is None or review.sha256 is None:
            raise ValueError("each propagation band requires a source-hash-pinned review in context")
        payload=q.delay.model_dump(mode="json")
        payload["band_hz"]=list(band.frequency_bounds_hz)
        delay=DelayRequest.model_validate_json(json.dumps(payload))
        result=localize_acoustic_leak(q.left,q.right,delay,band.span,c)
        results.append(dict(band_id=band.id,frequency_bounds_hz=list(band.frequency_bounds_hz),
                            review_evidence_id=review.id,localization=result))
    all_estimated=all(r["localization"]["status"]=="estimated" for r in results)
    intersection=None
    issues=[]
    if all_estimated:
        intervals=[r["localization"]["location"]["unconstrained_position_bounds_m"] for r in results]
        lower=max(i[0] for i in intervals)
        upper=min(i[1] for i in intervals)
        if lower<=upper:
            intersection=[lower,upper]
        else:
            issues.append("reviewed cross-band location bounds do not intersect")
    else:
        issues.append("one or more band delay/localization results were withheld")
    body=dict(schema="pipeline_integrity.acoustic_multiband.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),bands=results,
              status="estimated" if intersection is not None else "withheld",issues=issues,
              cross_band_position_constraint_m=intersection,
              independent_band_assumption=False,multipath_identified=False,
              probability_calculated=False,numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),
              acoustic_owner_sha256=file_digest(Path(__file__).with_name("acoustics.py")))
    body["record_digest"]=digest(body)
    return body


def verify_multiband_acoustic_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("multiband acoustic receipt changed")
    q=MultibandAcousticRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("multiband acoustic caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("multiband acoustic caller context differs")
    if evaluate_multiband_acoustics(q,c)!=record:
        raise ValueError("multiband acoustic replay differs")
    return True
