"""Registered growth tracks and repeated native no-indication updates.

Physical detection and corrosion calculations stay in the material owner. A
performed repair resets supplied state; no repair-effectiveness law is inferred.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from . import inspection, inspection_planning, material_exchange
from .adapters import ExternalResult, MaterialClient
from .contracts import Contract, Name, Positive, Nonnegative, Fraction, RunContext, digest, json_value, require_context_extension
from .datasets import file_digest
from .inspection import InspectionRun, InspectionRegistration, InspectionTrack, FeatureMatchPolicy, match_inspections, evaluate_inspection_track
from .inspection_planning import InspectionCampaign, LogisticInspectionPOD, DeclaredInspectionPOD, plan_inspections
from .material_exchange import prepare_material_job, evaluate_material_job
from .material_source import material_source_guard


class EvolutionHypothesis(Contract):
    id: Name
    prior_mass: Fraction
    initial_remaining_thickness_m: Positive
    rate_m_s: Nonnegative
    evidence_id: Name


class EvolutionTarget(Contract):
    id: Name
    location_id: Name
    initial_time_s: Nonnegative
    nominal_wall_m: Positive
    required_thickness_m: Positive
    hypotheses: tuple[EvolutionHypothesis,...] = Field(min_length=1,max_length=100)
    growth_track_id: Name | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def normalized(self):
        if len({h.id for h in self.hypotheses})!=len(self.hypotheses) or not math.isclose(
                math.fsum(h.prior_mass for h in self.hypotheses),1.0,rel_tol=0,abs_tol=1e-12):
            raise ValueError("evolution hypotheses must have distinct IDs and normalized masses")
        if any(h.initial_remaining_thickness_m>self.nominal_wall_m for h in self.hypotheses):
            raise ValueError("remaining thickness cannot exceed its explicit nominal wall")
        return self


class EvolutionObservation(Contract):
    target_id: Name
    outcome: Literal["no_indication"] = "no_indication"
    coverage_fraction: Fraction
    detection: Annotated[LogisticInspectionPOD | DeclaredInspectionPOD,Field(discriminator="kind")]
    source_evidence_id: Name
    conditional_independence_given_hypothesis_reviewed: bool = False
    coverage_random_and_hypothesis_independent_reviewed: bool = False
    review_evidence_id: Name


class EvolutionRound(Contract):
    id: Name
    time_s: Nonnegative
    observations: tuple[EvolutionObservation,...] = Field(min_length=1,max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        if len({o.target_id for o in self.observations})!=len(self.observations):
            raise ValueError("one observation per target per round is required")
        return self


class PerformedRepairReset(Contract):
    id: Name
    target_id: Name
    time_s: Nonnegative
    new_nominal_wall_m: Positive
    new_required_thickness_m: Positive
    remaining_thickness_by_hypothesis_m: dict[Name,Positive]
    subsequent_rate_by_hypothesis_m_s: dict[Name,Nonnegative]
    source_evidence_id: Name
    review_evidence_id: Name
    actually_performed_geometry_and_regime_reviewed: bool = False


class InspectionEvolutionRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    targets: tuple[EvolutionTarget,...] = Field(min_length=1,max_length=1000)
    rounds: tuple[EvolutionRound,...] = Field(min_length=1,max_length=1000)
    repair_resets: tuple[PerformedRepairReset,...] = Field(default=(),max_length=1000)
    runs: tuple[InspectionRun,...] = Field(default=(),max_length=100)
    registrations: tuple[InspectionRegistration,...] = Field(default=(),max_length=99)
    matching_policy: FeatureMatchPolicy | None = None
    tracks: tuple[InspectionTrack,...] = Field(default=(),max_length=1000)
    trend_method: Literal["ols","theil_sen"] = "theil_sen"
    material_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    maximum_material_calls: int = Field(default=20000,ge=1,le=200000)
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def ordered(self):
        target_ids={t.id for t in self.targets}
        if len(target_ids)!=len(self.targets) or len({t.location_id for t in self.targets})!=len(self.targets):
            raise ValueError("evolution target and physical location IDs must be distinct")
        if len({r.id for r in self.rounds})!=len(self.rounds) or any(b.time_s<=a.time_s for a,b in zip(self.rounds,self.rounds[1:])):
            raise ValueError("inspection rounds need unique IDs and strictly increasing times")
        if any(t.initial_time_s>self.rounds[0].time_s for t in self.targets):
            raise ValueError("all initial target states must precede the first inspection round")
        if any(o.target_id not in target_ids for r in self.rounds for o in r.observations):
            raise ValueError("inspection observation refers to an unknown target")
        targets={t.id:t for t in self.targets}
        if len({r.id for r in self.repair_resets})!=len(self.repair_resets):
            raise ValueError("performed-repair IDs must be unique")
        seen=set()
        for r in self.repair_resets:
            if r.target_id not in targets or not targets[r.target_id].initial_time_s<r.time_s<=self.rounds[-1].time_s:
                raise ValueError("performed repair must fall inside the corresponding target's evolution interval")
            key=(r.target_id,r.time_s)
            if key in seen:
                raise ValueError("simultaneous alternative repairs need an explicit single reviewed state reset")
            seen.add(key)
            hypotheses={h.id for h in targets[r.target_id].hypotheses}
            if set(r.remaining_thickness_by_hypothesis_m)!=hypotheses or set(r.subsequent_rate_by_hypothesis_m_s)!=hypotheses:
                raise ValueError("repair states must preserve every declared latent hypothesis ID")
            if any(v>r.new_nominal_wall_m for v in r.remaining_thickness_by_hypothesis_m.values()):
                raise ValueError("post-repair thickness exceeds the declared replacement wall")
        if len({t.track_id for t in self.tracks})!=len(self.tracks):
            raise ValueError("growth track IDs must be unique")
        if self.runs and (len(self.runs)<3 or len(self.registrations)!=len(self.runs)-1 or self.matching_policy is None):
            raise ValueError("registered growth needs at least three ordered runs and every adjacent registration")
        if not self.runs and (self.registrations or self.tracks):
            raise ValueError("track/registration inputs need their actual source inspection runs")
        if any(t.growth_track_id is not None and t.growth_track_id not in {x.track_id for x in self.tracks} for t in self.targets):
            raise ValueError("target growth refers to an absent reviewed track")
        return self


def _sources():
    return {"evolution":file_digest(Path(__file__)),"matching":file_digest(Path(inspection.__file__)),
            "planning":file_digest(Path(inspection_planning.__file__)),"material_adapter":file_digest(Path(material_exchange.__file__))}


def evaluate_inspection_evolution(request: InspectionEvolutionRequest, context: RunContext, *, client=None, source_root=None):
    q=InspectionEvolutionRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("inspection evolution asset or clock differs from context")
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence):
        raise ValueError("inspection evolution evidence IDs must be unambiguous")
    required={t.evidence_id for t in q.targets}|{h.evidence_id for t in q.targets for h in t.hypotheses}
    required.update(x for r in q.rounds for o in r.observations for x in (o.source_evidence_id,o.review_evidence_id,o.detection.evidence_id))
    required.update(x for r in q.repair_resets for x in (r.source_evidence_id,r.review_evidence_id))
    required.update(x for run in q.runs for x in (run.source_id,run.time_basis_evidence_id) if x is not None)
    required.update(r.review_evidence_id for r in q.registrations if r.review_evidence_id is not None)
    required.update(review.evidence_id for track in q.tracks for review in track.reviews)
    if not required<=set(evidence):
        raise ValueError("growth, detection, registration, observation and repair evidence must be actual context members")
    source_ids={o.source_evidence_id for r in q.rounds for o in r.observations}|{r.source_evidence_id for r in q.repair_resets}
    if any(evidence[id].sha256 is None for id in source_ids):
        raise ValueError("observed inspection and performed-repair resets require source SHA-256 pins")
    if c.origin in {"measured","laboratory"} and any(evidence[id].origin!=c.origin for id in source_ids):
        raise ValueError("measured inspection evolution cannot relabel synthetic or differently sourced observations")
    if any(not o.conditional_independence_given_hypothesis_reviewed or not o.coverage_random_and_hypothesis_independent_reviewed
           for r in q.rounds for o in r.observations):
        raise ValueError("repeated detection dependence and random coverage require explicit review")
    if any(not r.actually_performed_geometry_and_regime_reviewed for r in q.repair_resets):
        raise ValueError("repair resets describe performed geometry/regime changes only")
    if source_root is None:
        raise ValueError("inspection evolution requires the reviewed material source checkout")
    materials=MaterialClient() if client is None else client
    before=_sources()
    matches=[]
    trends={}
    receipts=[]
    calls=0
    retained_bytes=0
    def retain(record):
        nonlocal retained_bytes
        retained_bytes+=len(json.dumps(json_value(record),allow_nan=False).encode("utf-8"))
        if retained_bytes>q.maximum_result_bytes:
            raise ValueError("inspection evolution retained-result byte budget exceeded")
    def owner(model,parameters,location):
        nonlocal calls
        calls+=1
        if calls>q.maximum_material_calls:
            raise ValueError("inspection evolution material-call budget exceeded")
        job=prepare_material_job(model,parameters,c,location_id=location,evidence_ids=tuple(sorted(required)),client=materials)
        receipt=evaluate_material_job(job,c,client=materials)
        receipt.check_integrity()
        retained_receipt=receipt.model_dump(mode="json")
        retain(retained_receipt)
        receipts.append(retained_receipt)
        if receipt.native.status!="computed":
            raise ValueError("native inspection evolution model withheld: "+model+":"+receipt.native.status)
        return receipt.native.payload["values"]
    with material_source_guard(source_root,q.material_source_sha256,materials) as source_pin:
        for run in q.runs:
            if (run.asset_id,run.clock_id,run.origin)!=(c.asset_id,c.clock_id,c.origin) or run.inspection_time_s is None:
                raise ValueError("growth inspection source must preserve actual asset, clock, time and observation origin")
            if evidence[run.source_id].sha256!=run.source_sha256 or evidence[run.source_id].origin!=run.origin:
                raise ValueError("growth inspection source digest or origin differs from context evidence")
        if any(b.inspection_time_s<=a.inspection_time_s for a,b in zip(q.runs,q.runs[1:])):
            raise ValueError("growth inspection runs must follow a strictly increasing actual clock")
        for i,registration in enumerate(q.registrations):
            if registration.review_evidence_id is None:
                raise ValueError("coordinate registration needs its explicit review evidence")
            matching=match_inspections(q.runs[i],q.runs[i+1],registration,q.matching_policy)
            retain(matching)
            matches.append(matching)
        for track in q.tracks:
            calls+=1
            if calls>q.maximum_material_calls:
                raise ValueError("inspection evolution material-call budget exceeded")
            trend=evaluate_inspection_track(track,q.runs,tuple(matches),materials,c,method=q.trend_method)
            retain(trend)
            if trend["status"]!="computed":
                raise ValueError("reviewed growth track is unresolved: "+track.track_id)
            external=ExternalResult.model_validate_json(json.dumps(trend["external_result"]))
            if digest(external.payload)!=external.payload_digest or external.owner!="matintegrity" or external.payload["model_id"]!="corrosion.thickness_trend":
                raise ValueError("growth track material payload identity or digest differs")
            if external.payload["inputs"]!=trend["parameters"] or external.payload["context"]["metadata"]["pipeline_context_digest"]!=digest(c):
                raise ValueError("growth track owner altered inputs or source context")
            trends[track.track_id]=trend
        states={}
        for target in q.targets:
            rate=None
            if target.growth_track_id is not None:
                if q.runs[-1].inspection_time_s>target.initial_time_s:
                    raise ValueError("future inspection evidence cannot determine an earlier target growth state")
                rate=trends[target.growth_track_id]["external_result"]["payload"]["values"]["rate_m_s"]
                if not math.isfinite(rate) or rate<0:
                    raise ValueError("native growth trend is not a nonnegative loss rate; no clamping is applied")
            states[target.id]=dict(time_s=target.initial_time_s,wall_m=target.nominal_wall_m,required_thickness_m=target.required_thickness_m,
                hypotheses={h.id:dict(thickness_m=h.initial_remaining_thickness_m,rate_m_s=h.rate_m_s if rate is None else rate,
                                     posterior_mass=h.prior_mass) for h in target.hypotheses})
        round_records=[]
        applied_repairs=[]
        path_likelihood={target.id:1.0 for target in q.targets}
        def advance(target,to_time):
            state=states[target.id]
            duration=to_time-state["time_s"]
            if duration<0:
                raise ValueError("inspection state time moved backwards")
            if duration:
                for hypothesis in target.hypotheses:
                    item=state["hypotheses"][hypothesis.id]
                    values=owner("corrosion.uniform_forecast",dict(current_thickness_m=item["thickness_m"],
                        required_thickness_m=state["required_thickness_m"],rate_m_s=item["rate_m_s"],horizon_s=duration),target.location_id)
                    predicted=values["predicted_thickness_m"]
                    if predicted is None or predicted<=0:
                        raise ValueError("positive-wall growth evolution exhausted its native owner domain")
                    item["thickness_m"]=predicted
                state["time_s"]=to_time
        for round in q.rounds:
            for target in q.targets:
                resets=sorted((r for r in q.repair_resets if r.target_id==target.id and states[target.id]["time_s"]<r.time_s<=round.time_s),key=lambda r:r.time_s)
                for repair in resets:
                    advance(target,repair.time_s)
                    states[target.id]["wall_m"]=repair.new_nominal_wall_m
                    states[target.id]["required_thickness_m"]=repair.new_required_thickness_m
                    for key,item in states[target.id]["hypotheses"].items():
                        item.update(thickness_m=repair.remaining_thickness_by_hypothesis_m[key],rate_m_s=repair.subsequent_rate_by_hypothesis_m_s[key])
                    applied_repairs.append(repair.id)
                advance(target,round.time_s)
            observations=[]
            for observation in round.observations:
                target=next(t for t in q.targets if t.id==observation.target_id)
                state=states[target.id]
                probabilities=[]
                sizes={h.id:state["wall_m"]-state["hypotheses"][h.id]["thickness_m"] for h in target.hypotheses}
                if any(size<0 for size in sizes.values()):
                    raise ValueError("growth hypothesis exceeds its nominal wall; size is not clamped")
                curve=observation.detection
                if isinstance(curve,DeclaredInspectionPOD) and set(curve.probabilities)!=set(sizes):
                    raise ValueError("declared detection probabilities must preserve latent hypothesis IDs")
                for hypothesis in target.hypotheses:
                    size=sizes[hypothesis.id]
                    if isinstance(curve,DeclaredInspectionPOD):
                        pod=curve.probabilities[hypothesis.id]
                        if size==0 and pod!=0:
                            raise ValueError("zero-size detection would require an explicit false-call owner model")
                    else:
                        if size!=0 and not curve.supported_minimum_size_m<=size<=curve.supported_maximum_size_m:
                            raise ValueError("evolved flaw size left the reviewed native POD support")
                        pod=owner("inspection.logistic_pod",dict(flaw_size_m=size,midpoint_size_m=curve.midpoint_size_m,
                            log_size_scale=curve.log_size_scale),target.location_id)["probability_of_detection"]
                    probabilities.append(pod)
                prior=[state["hypotheses"][h.id]["posterior_mass"] for h in target.hypotheses]
                update=owner("inspection.no_indication_update",dict(prior_weights=prior,probabilities_of_detection=probabilities,
                    coverage_fraction=observation.coverage_fraction),target.location_id)
                posterior=update["posterior_weights"]
                likelihood=update["probability_of_no_indication"]
                if len(posterior)!=len(prior) or not 0<likelihood<=1 or any(not math.isfinite(p) or p<0 or p>1 for p in posterior) or not math.isclose(math.fsum(posterior),1.0,abs_tol=1e-12):
                    raise ValueError("native repeated-inspection posterior is not a normalized finite distribution")
                for hypothesis,mass in zip(target.hypotheses,posterior):
                    state["hypotheses"][hypothesis.id]["posterior_mass"]=mass
                path_likelihood[target.id]*=likelihood
                observations.append(dict(target_id=target.id,sizes_m=sizes,prior_masses=prior,posterior_masses=posterior,
                    conditional_no_indication_probability=likelihood,source_evidence_id=observation.source_evidence_id))
            row=json_value(dict(round_id=round.id,time_s=round.time_s,observations=observations,states=states))
            retain(row)
            round_records.append(row)
    if _sources()!=before:
        raise ValueError("inspection evolution implementation changed during evaluation")
    body=json_value(dict(schema="pipeline_integrity.inspection_evolution.v1",status="computed_diagnostic",request=q,context=c,
        matches=matches,trend_records=trends,rounds=round_records,final_states=states,applied_repair_ids=applied_repairs,
        native_material_receipts=receipts,material_calls=calls,source_pin=source_pin,implementation_sha256=before,
        observed_path_likelihood_by_target=path_likelihood,probability_meaning="conditional_detection_history_given_declared_latent_hypotheses",
        cross_target_joint_probability_inferred=False,
        repairs_are_explicit_state_resets=True,repair_effectiveness_model_implemented=False,
        growth_track_rate_is_point_estimate=True,parameter_uncertainty_fitted=False,
        joint_adaptive_policy_optimization=False,field_validation_claim=False,numerical_validation_completed=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("inspection evolution receipt exceeds its retained-result byte budget")
    return body


def verify_inspection_evolution_record(record, *, request=None, context=None, client=None, source_root=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("inspection evolution receipt changed")
    q=InspectionEvolutionRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("inspection evolution caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("inspection evolution caller context differs")
    if evaluate_inspection_evolution(q,c,client=client,source_root=source_root)!=record:
        raise ValueError("inspection evolution owner/matching replay differs")
    return True


def prepare_evolved_inspection_campaign(record, template: InspectionCampaign, context: RunContext, *, client=None, source_root=None,
                                        declared_pod_transfer_to_evolved_sizes_reviewed=False):
    """Pass actual final posterior/size states to the existing next-round planner."""
    verify_inspection_evolution_record(record,client=client,source_root=source_root)
    original=RunContext.model_validate_json(json.dumps(record["context"]))
    target_context=RunContext.model_validate(context.model_dump(mode="python"))
    require_context_extension(original,target_context)
    campaign=InspectionCampaign.model_validate(template.model_dump(mode="python"))
    data=campaign.model_dump(mode="python")
    targets=[]
    native=[]
    materials=MaterialClient() if client is None else client
    original_targets={t["id"]:t for t in record["request"]["targets"]}
    with material_source_guard(source_root,record["request"]["material_source_sha256"],materials):
        for target in campaign.targets:
            if target.id not in record["final_states"] or target.location_id!=original_targets[target.id]["location_id"]:
                raise ValueError("next inspection target lacks the same replayed physical location")
            state=record["final_states"][target.id]
            actual={h.id for h in target.hypotheses}
            if actual!=set(state["hypotheses"]):
                raise ValueError("next inspection hypotheses must preserve the replayed latent identities")
            hypotheses=tuple({**h.model_dump(mode="python"),"prior_mass":state["hypotheses"][h.id]["posterior_mass"]} for h in target.hypotheses)
            options=[]
            for option in target.options:
                sizes={}
                for key,item in state["hypotheses"].items():
                    parameters=dict(current_thickness_m=item["thickness_m"],required_thickness_m=state["required_thickness_m"],
                        rate_m_s=item["rate_m_s"],horizon_s=option.start_s+option.duration_s)
                    job=prepare_material_job("corrosion.uniform_forecast",parameters,target_context,
                        location_id=target.location_id,evidence_ids=tuple(e.id for e in target_context.evidence),client=materials)
                    receipt=evaluate_material_job(job,target_context,client=materials)
                    receipt.check_integrity()
                    if receipt.native.status!="computed" or receipt.native.payload["values"]["predicted_thickness_m"] is None:
                        raise ValueError("next-inspection size forecast left the owner's positive-wall domain")
                    sizes[key]=state["wall_m"]-receipt.native.payload["values"]["predicted_thickness_m"]
                    native.append(receipt.model_dump(mode="json"))
                    if len(native)>record["request"]["maximum_material_calls"]:
                        raise ValueError("next-campaign projection exceeded the material-call budget")
                if isinstance(option.detection,DeclaredInspectionPOD) and sizes!=option.sizes_m and not declared_pod_transfer_to_evolved_sizes_reviewed:
                    raise ValueError("declared POD reuse at evolved sizes requires an explicit transfer review")
                options.append({**option.model_dump(mode="python"),"sizes_m":sizes})
            targets.append({**target.model_dump(mode="python"),"hypotheses":hypotheses,"options":tuple(options)})
    data["targets"]=tuple(targets)
    result=InspectionCampaign.model_validate(data)
    receipt=json_value(dict(schema="pipeline_integrity.evolved_inspection_campaign.v1",source_digest=record["record_digest"],
        template_digest=digest(campaign),campaign_digest=digest(result),context_digest=digest(target_context),
        time_basis="next_campaign_local_offsets_from_final_evolution_time",origin=original.origin,
        size_forecast_time="option_finish",native_forecast_receipts=native,
        declared_pod_transfer_reviewed=declared_pod_transfer_to_evolved_sizes_reviewed,
        independence_or_failure_probability_inferred=False))
    receipt["record_digest"]=digest(receipt)
    if len(json.dumps(receipt,allow_nan=False).encode("utf-8"))>record["request"]["maximum_result_bytes"]:
        raise ValueError("next-campaign forecast receipt exceeds its retained-result byte budget")
    return result,receipt


def plan_evolved_inspections(record, template: InspectionCampaign, context: RunContext, *, client=None, source_root=None, backend=None,
                             declared_pod_transfer_to_evolved_sizes_reviewed=False):
    campaign,binding=prepare_evolved_inspection_campaign(record,template,context,client=client,source_root=source_root,
        declared_pod_transfer_to_evolved_sizes_reviewed=declared_pod_transfer_to_evolved_sizes_reviewed)
    with material_source_guard(source_root,record["request"]["material_source_sha256"],client):
        plan=plan_inspections(campaign,context,materials=client,backend=backend)
    return plan,binding
