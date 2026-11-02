"""Temperature-dependent tangent expansion curves connected to the material owner."""
from __future__ import annotations

import json
import math
from pathlib import Path
from .datasets import file_digest
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .structural import FrameRequest
from .frame_loading import UniformFrameTemperature, prepare_uniform_thermal_frame


class ExpansionKnot(Contract):
    temperature_K: Positive
    tangent_expansion_per_K: Nonnegative


class ExpansionCurve(Contract):
    id: Name
    material_id: Name
    knots: tuple[ExpansionKnot, ...] = Field(min_length=2,max_length=10000)
    evidence_id: Name
    tangent_small_engineering_strain_basis_reviewed: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if any(b.temperature_K <= a.temperature_K for a,b in zip(self.knots,self.knots[1:])):
            raise ValueError("expansion knots must increase without sorting or extrapolation")
        return self


def integrate_thermal_expansion(curve: ExpansionCurve, reference_temperature_K: float,
                                temperature_K: float) -> dict:
    """Integrate the supplied tangent engineering-strain coefficient exactly per linear segment."""
    q = ExpansionCurve.model_validate(curve.model_dump(mode="python"))
    if not q.tangent_small_engineering_strain_basis_reviewed:
        raise ValueError("tangent engineering-strain basis requires explicit review")
    lo,hi = sorted((reference_temperature_K,temperature_K))
    if not all(math.isfinite(t) and t>0 for t in (lo,hi)):
        raise ValueError("thermal temperatures must be finite and positive")
    if lo < q.knots[0].temperature_K or hi > q.knots[-1].temperature_K:
        raise ValueError("thermal temperatures exceed the supplied coefficient support")
    segments = []
    for a,b in zip(q.knots,q.knots[1:]):
        left,right = max(lo,a.temperature_K),min(hi,b.temperature_K)
        if right <= left:
            continue
        slope = (b.tangent_expansion_per_K-a.tangent_expansion_per_K)/(b.temperature_K-a.temperature_K)
        al = a.tangent_expansion_per_K+slope*(left-a.temperature_K)
        ar = a.tangent_expansion_per_K+slope*(right-a.temperature_K)
        segments.append(dict(lower_temperature_K=left,upper_temperature_K=right,
                             integrated_strain=(al+ar)*(right-left)/2))
    sign = 1 if temperature_K>=reference_temperature_K else -1
    strain = sign*math.fsum(s["integrated_strain"] for s in segments)
    delta = temperature_K-reference_temperature_K
    # Equal endpoint temperatures produce zero strain; any supported nonnegative
    # coefficient is equivalent for the owner's zero-temperature-change input.
    equivalent = strain/delta if delta else q.knots[0].tangent_expansion_per_K
    result = dict(curve=q.model_dump(mode="json"),reference_temperature_K=reference_temperature_K,
                  temperature_K=temperature_K,free_engineering_strain=strain,
                  equivalent_expansion_per_K=equivalent,segments=segments)
    result["record_digest"] = digest(result)
    return result


class CurveFrameRequest(Contract):
    id: Name
    frame: FrameRequest
    member_id: Name
    case_id: Name
    curve: ExpansionCurve
    reference_temperature_K: Positive
    temperature_K: Positive
    uniform_member_temperature_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)


def evaluate_curve_thermal_frame(request: CurveFrameRequest, context: RunContext, *, client=None):
    q = CurveFrameRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    evidence = tuple(dict.fromkeys((*q.evidence_ids,q.curve.evidence_id)))
    if not set(evidence) <= {e.id for e in c.evidence}:
        raise ValueError("thermal expansion curve lacks source evidence in context")
    integrated = integrate_thermal_expansion(q.curve,q.reference_temperature_K,q.temperature_K)
    specification = UniformFrameTemperature(
        id=q.id,member_id=q.member_id,material_id=q.curve.material_id,case_id=q.case_id,
        temperature_k=q.temperature_K,stress_free_temperature_k=q.reference_temperature_K,
        expansion_coefficient_per_k=integrated["equivalent_expansion_per_K"],
        coefficient_temperature_support_k=(q.curve.knots[0].temperature_K,q.curve.knots[-1].temperature_K),
        evidence_ids=evidence,uniform_temperature_and_constant_coefficient_reviewed=q.uniform_member_temperature_reviewed)
    contribution = prepare_uniform_thermal_frame(q.frame,specification,c,client=client)
    body = dict(schema="pipeline_integrity.curve_thermal_frame.v1",request=q.model_dump(mode="json"),
                implementation_sha256=file_digest(Path(__file__)),
                context=c.model_dump(mode="json"),integrated_expansion=integrated,
                contribution=contribution.model_dump(mode="json"),
                status=contribution.preparation_status,numerical_validation_completed=False)
    body["record_digest"] = digest(body)
    return body


def verify_curve_thermal_frame_record(record, *, request=None, context=None, client=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"}) != record.get("record_digest"):
        raise ValueError("curve thermal frame receipt changed")
    q = CurveFrameRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("curve thermal frame caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("curve thermal frame caller context differs")
    if evaluate_curve_thermal_frame(q,c,client=client) != record:
        raise ValueError("curve thermal frame owner replay differs")
    return True
