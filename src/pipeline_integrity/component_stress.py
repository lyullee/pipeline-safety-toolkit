"""Caller-qualified component stress transfer from actual frame resultants.

Transfer coefficients describe the declared component and stress location.
They are not inferred code factors, flexibility data or acceptance criteria.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, RunContext, digest
from .datasets import file_digest
from .structural import FrameRequest, solve_frame


class ComponentStressTransfer(Contract):
    id: Name
    member_id: Name
    stress_location_id: Name
    stress_coordinate_frame_id: Name
    sample_fraction: float = Field(ge=0,le=1)
    stress_per_force_Pa_N: tuple[tuple[float,float,float], ...] = Field(min_length=6,max_length=6)
    stress_per_moment_Pa_N_m: tuple[tuple[float,float,float], ...] = Field(min_length=6,max_length=6)
    stress_per_differential_pressure: tuple[float,float,float,float,float,float]
    differential_pressure_Pa_by_combination: dict[str,float]
    evidence_id: Name
    physical_resultant_signs_and_local_axes_reviewed: bool = False
    pressure_term_excludes_resultant_effects_reviewed: bool = False
    linear_elastic_component_transfer_reviewed: bool = False


class ComponentStressRequest(Contract):
    id: Name
    frame: FrameRequest
    transfers: tuple[ComponentStressTransfer, ...] = Field(min_length=1,max_length=10000)
    maximum_absolute_tensor_component_Pa: float = Field(gt=0)
    maximum_stress_locations: int = Field(default=100000,ge=1,le=1000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1,le=1000000000)

    @model_validator(mode="after")
    def unique_locations(self):
        if len({t.id for t in self.transfers})!=len(self.transfers):
            raise ValueError("component stress transfer IDs must be unique")
        if len(self.transfers)*len(self.frame.combinations)>self.maximum_stress_locations:
            raise ValueError("component stress output exceeds its declared location/combination budget")
        return self


def evaluate_component_stress(request: ComponentStressRequest, context: RunContext):
    q=ComponentStressRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    required={t.evidence_id for t in q.transfers}
    evidence=[e.id for e in c.evidence]
    if len(evidence)!=len(set(evidence)) or not required<=set(evidence):
        raise ValueError("component stress transfer requires unambiguous source evidence")
    evidence_by_id={e.id:e for e in c.evidence}
    if any(evidence_by_id[id].sha256 is None for id in required):
        raise ValueError("component transfer coefficients require a source-pinned applicability review")
    members={m.id:m for m in q.frame.members}
    combinations={v.id for v in q.frame.combinations}
    for t in q.transfers:
        if t.member_id not in members or set(t.differential_pressure_Pa_by_combination)!=combinations:
            raise ValueError("component transfer member or pressure load combinations differ from frame")
        if t.sample_fraction not in members[t.member_id].sample_fractions:
            raise ValueError("component stress location must be an exact declared frame sample before solving")
        if not all((t.physical_resultant_signs_and_local_axes_reviewed,
                    t.pressure_term_excludes_resultant_effects_reviewed,
                    t.linear_elastic_component_transfer_reviewed)):
            raise ValueError("component transfer axes, pressure partition and elasticity require review")
    sources={name:file_digest(Path(__file__).with_name(name+".py")) for name in ("structural","component_stress","contracts")}
    source=solve_frame(q.frame,c)
    rows=[]
    retained_bytes=len(json.dumps(source,allow_nan=False).encode("utf-8"))
    if retained_bytes>q.maximum_result_bytes:
        raise ValueError("native frame source result exceeds the component retained-result budget")
    status=source["status"]
    if status=="computed":
        for combo in q.frame.combinations:
            for t in q.transfers:
                samples=source["combinations"][combo.id]["members"][t.member_id]["samples"]
                selected=[s for s in samples if s["fraction"]==t.sample_fraction]
                if len(selected)!=1:
                    raise ValueError("component stress location must identify an exact existing frame sample")
                sample=selected[0]
                force=sample["physical_section_force_local_n"]
                moment=sample["physical_section_moment_local_nm"]
                pressure=t.differential_pressure_Pa_by_combination[combo.id]
                force_stress=[math.fsum(a*b for a,b in zip(row,force)) for row in t.stress_per_force_Pa_N]
                moment_stress=[math.fsum(a*b for a,b in zip(row,moment)) for row in t.stress_per_moment_Pa_N_m]
                pressure_stress=[pressure*p for p in t.stress_per_differential_pressure]
                stress=[math.fsum(values) for values in zip(force_stress,moment_stress,pressure_stress)]
                if any(not math.isfinite(v) for v in stress):
                    raise ValueError("component stress transfer produced nonfinite stress")
                within=max(abs(v) for v in stress)<=q.maximum_absolute_tensor_component_Pa
                if not within:
                    status="out_of_scope"
                row=dict(combination_id=combo.id,transfer_id=t.id,member_id=t.member_id,
                    sample_fraction=t.sample_fraction,stress_location_id=t.stress_location_id,
                    stress_coordinate_frame_id=t.stress_coordinate_frame_id,
                    stress_tensor_components_Pa=dict(zip(("xx","yy","zz","xy","yz","zx"),stress)),
                    force_contribution_Pa=dict(zip(("xx","yy","zz","xy","yz","zx"),force_stress)),
                    moment_contribution_Pa=dict(zip(("xx","yy","zz","xy","yz","zx"),moment_stress)),
                    residual_pressure_contribution_Pa=dict(zip(("xx","yy","zz","xy","yz","zx"),pressure_stress)),
                    source_sample_digest=digest(sample),source_force_local_N=force,source_moment_local_N_m=moment,
                    source_global_to_member_local=source["combinations"][combo.id]["members"][t.member_id]["global_to_local"],
                    source_resultant_face="right_face_of_explicit_left_member_segment",
                    differential_pressure_Pa=pressure,within_declared_elastic_transfer_limit=within)
                retained_bytes+=len(json.dumps(row,allow_nan=False).encode("utf-8"))
                if retained_bytes>q.maximum_result_bytes:
                    raise ValueError("component stress rows exceed their retained-result byte budget")
                rows.append(row)
    if sources!={name:file_digest(Path(__file__).with_name(name+".py")) for name in sources}:
        raise ValueError("component stress or frame implementation changed during evaluation")
    body=dict(schema="pipeline_integrity.component_stress.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status=status,frame_result=source,stress_locations=rows,
              source_sha256=sources,global_component_flexibility_added=False,
              unrepresented_thermal_self_stress_added=False,acceptance_or_code_compliance_calculated=False,
              output_origin="simulation",pressure_double_counting_partition_is_caller_reviewed=True,
              shear_convention="physical_stress_tensor_components",numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("component stress receipt exceeds retained-result budget")
    return body


def verify_component_stress_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("component stress receipt changed")
    q=ComponentStressRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("component stress caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("component stress caller context differs")
    if evaluate_component_stress(q,c)!=record:
        raise ValueError("component stress original frame replay differs")
    return True
