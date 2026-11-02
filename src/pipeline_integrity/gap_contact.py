"""Frictionless finite-gap elastic supports around the original frame solver.

For closing sign s in {-1,+1}, closure is s*d-gap and the physical support
force is -s*k*max(closure,0). An active branch is represented by the owner's
bilateral spring plus the affine anchor load. The anchor load is removed from
the physical external-load ledger and included in the recovered support force.
No stabilization stiffness, friction, plasticity or gap clipping is introduced.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .structural import (FrameRequest, FrameSupport, FrameNodeLoad, FrameCombination,
                         DOFS, FORCES, solve_frame, audit_frame_equilibrium)


class GapContact(Contract):
    id: Name
    node_id: Name
    dof: Literal["DX","DY","DZ"]
    closing_direction: Literal["positive_displacement","negative_displacement"]
    gap_m: Nonnegative
    stiffness_n_m: Positive
    evidence_id: Name
    elastic_frictionless_small_displacement_contact_reviewed: bool = False


class GapContactRequest(Contract):
    id: Name
    frame: FrameRequest
    contacts: tuple[GapContact,...] = Field(min_length=1,max_length=1000)
    maximum_active_set_iterations: int = Field(default=100,ge=1,le=10000)
    maximum_total_native_solves: int = Field(default=1000,ge=1,le=10000)
    maximum_retained_native_bytes: int = Field(default=64000000,ge=1,le=1000000000)
    gap_absolute_tolerance_m: Nonnegative = 1e-10
    contact_force_absolute_tolerance_n: Positive = 1e-6
    contact_force_relative_tolerance: Nonnegative = Field(default=1e-8,le=1e-3)
    initialization: Literal["all_active","explicit"] = "all_active"
    initially_active_contact_ids: tuple[Name,...] = ()

    @model_validator(mode="after")
    def compatible(self):
        if len({s.id for s in self.contacts})!=len(self.contacts):
            raise ValueError("gap contact IDs must be unique")
        if len({(s.node_id,s.dof) for s in self.contacts})!=len(self.contacts):
            raise ValueError("one gap contact is allowed per global node/translation DOF")
        nodes={n.id for n in self.frame.nodes}
        supports={s.node_id:s for s in self.frame.supports}
        for contact in self.contacts:
            if contact.node_id not in nodes:
                raise ValueError("gap contact must reference an existing explicit frame node")
            support=supports.get(contact.node_id)
            if support is not None and (contact.dof in support.fixed_dofs
                    or contact.dof in support.translation_springs_n_m
                    or contact.dof in support.one_way_translation_directions
                    or contact.dof in support.imposed_translations_m):
                raise ValueError("gap contact overlaps an existing fixed, imposed, bilateral or unilateral support DOF")
        known={s.id for s in self.contacts}
        if len(set(self.initially_active_contact_ids))!=len(self.initially_active_contact_ids) or not set(self.initially_active_contact_ids)<=known:
            raise ValueError("initial active contact IDs must be unique and declared")
        if self.initialization=="all_active" and self.initially_active_contact_ids:
            raise ValueError("all-active initialization cannot also carry an explicit active set")
        for combo in self.frame.combinations:
            if self.id+":gap_preload:"+combo.id in self.frame.load_cases:
                raise ValueError("generated affine-load case collides with a physical load case")
        return self


def _native_branch(q, combination, active):
    supports={s.node_id:s.model_dump(mode="python") for s in q.frame.supports}
    loads=list(q.frame.node_loads)
    case=q.id+":gap_preload:"+combination.id
    branches=[]
    for contact in q.contacts:
        if contact.id not in active:
            continue
        sign=1.0 if contact.closing_direction=="positive_displacement" else -1.0
        anchor=sign*contact.gap_m
        if contact.node_id not in supports:
            supports[contact.node_id]=FrameSupport(node_id=contact.node_id,evidence_id=contact.evidence_id).model_dump(mode="python")
        supports[contact.node_id]["translation_springs_n_m"][contact.dof]=contact.stiffness_n_m
        preload=contact.stiffness_n_m*anchor
        if not math.isfinite(preload):
            raise ValueError("gap-contact affine preload exceeds finite arithmetic")
        loads.append(FrameNodeLoad(node_id=contact.node_id,direction=FORCES[DOFS.index(contact.dof)],
                                   value=preload,case_id=case,evidence_id=contact.evidence_id))
        branches.append(dict(contact_id=contact.id,node_id=contact.node_id,dof=contact.dof,
                             stiffness_n_m=contact.stiffness_n_m,signed_anchor_m=anchor,
                             fictitious_nodal_preload_n=preload,case_id=case))
    combo=combination.model_dump(mode="python")
    combo["factors"]={**combo["factors"],case:1.0}
    payload=q.frame.model_dump(mode="python")
    payload.update(supports=tuple(FrameSupport.model_validate(s) for s in supports.values()),
                   node_loads=tuple(loads),load_cases=q.frame.load_cases+(case,),
                   combinations=(FrameCombination.model_validate(combo),))
    return FrameRequest.model_validate(payload),branches


def _contact_recovery(q, combination, active, native):
    raw=native["combinations"][combination.id]
    physical=json.loads(json.dumps(raw,allow_nan=False))
    audit_rows=[]
    next_active=set(active)
    all_valid=True
    for contact in q.contacts:
        index=DOFS.index(contact.dof)
        displacement=physical["nodes"][contact.node_id]["translation_m"][index]
        sign=1.0 if contact.closing_direction=="positive_displacement" else -1.0
        closure=sign*displacement-contact.gap_m
        is_active=contact.id in active
        affine_preload=contact.stiffness_n_m*sign*contact.gap_m if is_active else 0.0
        native_reaction=raw["nodes"][contact.node_id]["reaction_force_n"][index]
        recovered=native_reaction+affine_preload
        constitutive=-sign*contact.stiffness_n_m*max(closure,0.0)
        limit=q.contact_force_absolute_tolerance_n+q.contact_force_relative_tolerance*max(abs(recovered),abs(constitutive))
        gap_valid=closure>=-q.gap_absolute_tolerance_m if is_active else closure<=q.gap_absolute_tolerance_m
        force_valid=abs(recovered-constitutive)<=limit
        valid=gap_valid and force_valid
        all_valid=all_valid and valid
        if closure>q.gap_absolute_tolerance_m:
            next_active.add(contact.id)
        elif closure<-q.gap_absolute_tolerance_m:
            next_active.discard(contact.id)
        elif not force_valid:
            # At the numerical gap boundary retain the current active set only
            # if its force also satisfies the physical one-sided contact law.
            if closure>0:
                next_active.add(contact.id)
            elif closure<0:
                next_active.discard(contact.id)
        physical["nodes"][contact.node_id]["reaction_force_n"][index]=recovered
        spring_energy=0.5*contact.stiffness_n_m*max(closure,0.0)**2
        audit_rows.append(dict(contact_id=contact.id,node_id=contact.node_id,dof=contact.dof,
            closing_direction=contact.closing_direction,active=is_active,displacement_m=displacement,
            signed_closure_m=closure,remaining_open_gap_m=max(-closure,0.0),
            native_origin_spring_reaction_n=native_reaction,fictitious_preload_removed_from_external_loads_n=affine_preload,
            physical_contact_force_n=recovered,constitutive_contact_force_n=constitutive,
            contact_force_residual_n=recovered-constitutive,force_limit_n=limit,
            active_branch_gap_condition_passed=gap_valid,contact_law_passed=force_valid,
            stored_contact_energy_J=spring_energy))
    reactions={node:[*data["reaction_force_n"],*data["reaction_moment_nm"]] for node,data in physical["nodes"].items()}
    equilibrium=audit_frame_equilibrium(q.frame,combination.id,reactions,
                                       RunContext.model_validate_json(json.dumps(native["context"])),
                                       member_results=physical["members"])
    physical.update(static_balance=equilibrium["static_balance"],nodal_balance=equilibrium["nodal_balance"],
                    equilibrium_audit_record=equilibrium,contact_states=audit_rows,
                    contact_stored_elastic_energy_J=math.fsum(r["stored_contact_energy_J"] for r in audit_rows))
    return physical,next_active,all_valid,equilibrium["passed"]


def evaluate_gap_contact_frame(request: GapContactRequest, context: RunContext):
    q=GapContactRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.frame.asset_id,q.frame.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("gap-contact asset or clock differs from context")
    evidence={e.id:e for e in c.evidence}
    for contact in q.contacts:
        if not contact.elastic_frictionless_small_displacement_contact_reviewed:
            raise ValueError("elastic frictionless small-displacement gap-contact review is required")
        if contact.evidence_id not in evidence or evidence[contact.evidence_id].sha256 is None:
            raise ValueError("gap-contact stiffness/gap source must be context-bound and source-hash pinned")
    owner=Path(__file__).with_name("structural.py")
    source_pin=file_digest(owner)
    implementation_pin=file_digest(Path(__file__))
    input_digest=digest(dict(request=q,context=c))
    results={}
    statuses=[]
    runs=0
    retained_native_bytes=0
    for combination in q.frame.combinations:
        active={s.id for s in q.contacts} if q.initialization=="all_active" else set(q.initially_active_contact_ids)
        seen=set()
        history=[]
        outcome=None
        for iteration in range(q.maximum_active_set_iterations):
            key=tuple(sorted(active))
            if key in seen:
                outcome=dict(status="withheld",issues=["gap-contact active set repeated without satisfying the contact law"],
                             iteration_history=history,active_contact_ids=list(key))
                break
            seen.add(key)
            if runs>=q.maximum_total_native_solves:
                outcome=dict(status="withheld",issues=["gap-contact native-solve budget exhausted"],iteration_history=history)
                break
            branch,affine=_native_branch(q,combination,active)
            runs+=1
            native=solve_frame(branch,c)
            if file_digest(owner)!=source_pin:
                raise ValueError("gap-contact structural owner source changed during execution")
            native_bytes=len(json.dumps(native,ensure_ascii=False,allow_nan=False).encode("utf-8"))
            if retained_native_bytes+native_bytes>q.maximum_retained_native_bytes:
                outcome=dict(status="withheld",issues=["gap-contact native history receipt budget exhausted"],
                             iteration_history=history,unretained_native_record_digest=native["record_digest"],
                             unretained_native_status=native["status"])
                break
            retained_native_bytes+=native_bytes
            trial=dict(iteration=iteration+1,active_contact_ids=list(key),affine_contact_branches=affine,
                       native_status=native["status"],native_record=native)
            history.append(trial)
            if native["status"]!="computed":
                outcome=dict(status="needs_data" if native["status"]=="needs_data" else "withheld",
                    issues=["native frame withheld a contact branch; no numerical stabilizer or linearized substitute applied"],
                    iteration_history=history,native_issues=native["issues"])
                break
            physical,next_active,law_valid,equilibrium_valid=_contact_recovery(q,combination,active,native)
            trial["contact_states"]=physical["contact_states"]
            trial["physical_equilibrium_passed"]=equilibrium_valid
            trial["contact_constitutive_conditions_passed"]=law_valid
            if next_active==active and law_valid and equilibrium_valid:
                outcome=dict(status="computed",issues=[],physical_result=physical,iteration_history=history,
                             active_contact_ids=list(key),iterations=iteration+1)
                break
            if next_active==active:
                outcome=dict(status="withheld",issues=["unchanged active set fails physical contact or equilibrium conditions"],
                             iteration_history=history,physical_candidate=physical,active_contact_ids=list(key))
                break
            active=next_active
        if outcome is None:
            outcome=dict(status="withheld",issues=["gap-contact active-set iteration limit reached"],iteration_history=history)
        results[combination.id]=outcome
        statuses.append(outcome["status"])
    status="computed" if all(s=="computed" for s in statuses) else "partial" if "computed" in statuses else "needs_data" if "needs_data" in statuses else "withheld"
    if file_digest(owner)!=source_pin or file_digest(Path(__file__))!=implementation_pin or input_digest!=digest(dict(request=q,context=c)):
        raise ValueError("gap-contact source or input/context changed during evaluation")
    body=dict(schema="pipeline_integrity.gap_contact_frame.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status=status,combinations=results,total_native_solves=runs,
        retained_native_history_bytes=retained_native_bytes,
        structural_owner_sha256=source_pin,implementation_sha256=implementation_pin,
        active_sets_independent_per_combination=True,contact_law="force=-sign*k*max(sign*displacement-gap,0)",
        affine_preloads_are_physical_external_loads=False,friction_plasticity_or_geometric_nonlinearity_solved=False,
        numerical_stabilization_applied=False,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_gap_contact_frame_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("gap-contact frame receipt changed")
    q=GapContactRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("gap-contact caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("gap-contact caller context differs")
    if evaluate_gap_contact_frame(q,c)!=record:
        raise ValueError("gap-contact original-frame replay differs")
    return True
