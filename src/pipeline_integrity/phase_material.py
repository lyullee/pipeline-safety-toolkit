"""Apparent-capacity phase material tables for the existing pipe/soil heat owner.

The reviewed transition interval uses a smooth liquid fraction. Table spacing
is selected from analytic interpolation/integral error bounds, not from a fitted
latent heat or an inferred moisture content. The receiving solver remains the
owner of spatial conduction and its nonlinear integration.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .pipe_soil_transient import PipeSoilMaterial


class PhaseMaterialRequest(Contract):
    id: Name
    material_id: Name
    temperature_support_K: tuple[Positive,Positive]
    solidus_temperature_K: Positive
    liquidus_temperature_K: Positive
    solid_volumetric_heat_capacity_J_m3_K: Positive
    liquid_volumetric_heat_capacity_J_m3_K: Positive
    volumetric_latent_heat_J_m3: Nonnegative
    solid_conductivity_W_m_K: Positive
    liquid_conductivity_W_m_K: Positive
    maximum_enthalpy_table_error_J_m3: Positive
    maximum_conductivity_interpolation_error_W_m_K: Positive
    maximum_knots: int = Field(default=200,ge=5,le=200)
    evidence_id: Name
    apparent_capacity_and_transition_interval_reviewed: bool = False

    @model_validator(mode="after")
    def domain(self):
        lo,hi=self.temperature_support_K
        if not lo<self.solidus_temperature_K<self.liquidus_temperature_K<hi:
            raise ValueError("material support must strictly enclose the solidus/liquidus interval")
        return self


def phase_material_state(request: PhaseMaterialRequest, temperature_K: float):
    q=request
    lo,hi=q.temperature_support_K
    if not math.isfinite(temperature_K) or not lo<=temperature_K<=hi:
        raise ValueError("phase material temperature lies outside its declared support")
    ts,tl=q.solidus_temperature_K,q.liquidus_temperature_K
    cs,cl=q.solid_volumetric_heat_capacity_J_m3_K,q.liquid_volumetric_heat_capacity_J_m3_K
    latent=q.volumetric_latent_heat_J_m3
    width=tl-ts
    if temperature_K<=ts:
        fraction,derivative=0.0,0.0
        enthalpy=cs*(temperature_K-ts)
    elif temperature_K>=tl:
        fraction,derivative=1.0,0.0
        enthalpy=width*(cs+(cl-cs)/2)+latent+cl*(temperature_K-tl)
    else:
        x=(temperature_K-ts)/width
        fraction=3*x*x-2*x*x*x
        derivative=6*x*(1-x)/width
        enthalpy=cs*width*x+(cl-cs)*width*(x**3-x**4/2)+latent*fraction
    capacity=cs+(cl-cs)*fraction+latent*derivative
    conductivity=q.solid_conductivity_W_m_K+(q.liquid_conductivity_W_m_K-q.solid_conductivity_W_m_K)*fraction
    return dict(temperature_K=temperature_K,liquid_fraction=fraction,
                volumetric_enthalpy_J_m3=enthalpy,volumetric_heat_capacity_J_m3_K=capacity,
                conductivity_W_m_K=conductivity)


def build_phase_material(request: PhaseMaterialRequest, context: RunContext):
    q=PhaseMaterialRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.apparent_capacity_and_transition_interval_reviewed:
        raise ValueError("apparent-capacity phase transition applicability review is required")
    if q.evidence_id not in {e.id for e in c.evidence}:
        raise ValueError("phase material source evidence is absent")
    width=q.liquidus_temperature_K-q.solidus_temperature_K
    dc=q.liquid_volumetric_heat_capacity_J_m3_K-q.solid_volumetric_heat_capacity_J_m3_K
    latent=q.volumetric_latent_heat_J_m3
    # cp'' is affine within the transition interval; its absolute maximum is
    # attained at an endpoint. Composite trapezoid bounds are accumulated over
    # the whole interval, including partial final intervals.
    second=max(abs(6*dc/width**2-12*latent/width**3),
               abs(-6*dc/width**2-12*latent/width**3))
    energy_factor=second*width**3/12
    dk=abs(q.liquid_conductivity_W_m_K-q.solid_conductivity_W_m_K)
    n=max(2,math.ceil(math.sqrt(energy_factor/q.maximum_enthalpy_table_error_J_m3)),
          math.ceil(math.sqrt(0.75*dk/q.maximum_conductivity_interpolation_error_W_m_K)))
    if n+3>q.maximum_knots:
        raise ValueError("phase material interpolation tolerances exceed the receiving owner's knot budget")
    temperatures=[q.temperature_support_K[0]]+[
        q.solidus_temperature_K+width*i/n for i in range(n+1)]+[q.temperature_support_K[1]]
    states=[phase_material_state(q,t) for t in temperatures]
    material=PipeSoilMaterial(id=q.material_id,temperature_K=tuple(temperatures),
        conductivity_W_m_K=tuple(s["conductivity_W_m_K"] for s in states),
        volumetric_heat_capacity_J_m3_K=tuple(s["volumetric_heat_capacity_J_m3_K"] for s in states),
        evidence_id=q.evidence_id)
    body=dict(schema="pipeline_integrity.phase_material.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),material=material.model_dump(mode="json"),
              status="computed",liquid_fraction_law="3*x^2-2*x^3_between_solidus_and_liquidus",
              enthalpy_reference="solid_at_solidus",enthalpy_table_error_bound_J_m3=energy_factor/n**2,
              conductivity_interpolation_error_bound_W_m_K=0.75*dk/n**2,
              transition_subintervals=n,spatial_phase_front_solved=False,
              implementation_sha256=file_digest(Path(__file__)),numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_phase_material_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("phase material receipt changed")
    q=PhaseMaterialRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("phase material caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("phase material caller context differs")
    if build_phase_material(q,c)!=record:
        raise ValueError("phase material replay differs")
    return True


def bind_phase_material_to_pipe_soil(record, request, context: RunContext, *, applicability_evidence_id: str):
    """Install the bounded apparent-capacity table in a declared radial model."""
    from .pipe_soil_transient import PipeSoilTransientRequest
    verify_phase_material_record(record)
    base=PipeSoilTransientRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    material=PipeSoilMaterial.model_validate_json(json.dumps(record["material"]))
    source_context=RunContext.model_validate_json(json.dumps(record["context"]))
    source_evidence=[e for e in source_context.evidence if e.id==material.evidence_id]
    target_evidence=[e for e in c.evidence if e.id==material.evidence_id]
    if len(source_evidence)!=1 or len(target_evidence)!=1 or source_evidence[0].model_dump(mode="json")!=target_evidence[0].model_dump(mode="json"):
        raise ValueError("phase material reuse must preserve its original property evidence")
    if (base.asset_id,base.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("phase material receiving model asset/clock differs")
    reviews=[e for e in c.evidence if e.id==applicability_evidence_id]
    if len(reviews)!=1 or reviews[0].sha256 is None:
        raise ValueError("phase material application requires source-hash-pinned review evidence")
    if material.id not in {m.id for m in base.materials} or material.id not in {s.material_id for s in base.shells}:
        raise ValueError("phase material must replace an existing material used by a receiving shell")
    lower,upper=material.temperature_K[0],material.temperature_K[-1]
    for shell in base.shells:
        if shell.material_id==material.id and any(t is not None and not lower<=t<=upper for t in shell.initial_cell_temperature_K):
            raise ValueError("receiving shell initial temperatures exceed phase material support")
    payload=base.model_dump(mode="json")
    payload["materials"]=[material.model_dump(mode="json") if m.id==material.id else m.model_dump(mode="json") for m in base.materials]
    review="phase_change_absent_or_represented_in_supplied_capacity"
    payload["applicability_reviews"][review]=True
    payload["review_evidence_ids"][review]=applicability_evidence_id
    payload["evidence_ids"]=list(dict.fromkeys((*base.evidence_ids,material.evidence_id,applicability_evidence_id)))
    derived=PipeSoilTransientRequest.model_validate_json(json.dumps(payload))
    body=dict(schema="pipeline_integrity.phase_pipe_soil_binding.v1",base_request_digest=digest(base),
              material_record_digest=record["record_digest"],derived_request=derived.model_dump(mode="json"),
              context_digest=digest(c),applicability_evidence_id=applicability_evidence_id,
              moisture_transport_added=False,spatial_front_tracking_added=False)
    body["record_digest"]=digest(body)
    return derived,body
