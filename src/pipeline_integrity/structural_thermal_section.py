"""Thermal fiber quadrature projected into the original elastic frame solver.

The section is homogeneous, linear elastic and represented in centroidal
principal axes. Arbitrary supplied fiber temperatures generate mean extension,
two bending curvatures and a self-equilibrated normal-stress residual. Plane
sections and a uniform thermal field along each selected beam are required.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .structural import FrameRequest, FrameInitialStrain, FrameInitialCurvature, solve_frame
from .thermal_expansion import ExpansionCurve, integrate_thermal_expansion


class ThermalFiber(Contract):
    id: Name
    y_m: float
    z_m: float
    quadrature_area_m2: Positive
    temperature_K: Positive


class ThermalSectionField(Contract):
    id: Name
    member_id: Name
    case_id: Name
    expansion_curve: ExpansionCurve
    stress_free_temperature_K: Positive
    fibers: tuple[ThermalFiber,...] = Field(min_length=3,max_length=10000)
    quadrature_relative_tolerance: Positive = Field(default=1e-6,le=1e-2)
    centroid_absolute_tolerance_m: Nonnegative = 1e-12
    plane_sections_uniform_elasticity_and_axial_temperature_reviewed: bool = False
    evidence_id: Name

    @model_validator(mode="after")
    def identifiers(self):
        if len({f.id for f in self.fibers})!=len(self.fibers):
            raise ValueError("thermal fiber IDs must be unique")
        return self


class ThermalSectionRequest(Contract):
    id: Name
    frame: FrameRequest
    fields: tuple[ThermalSectionField,...] = Field(min_length=1,max_length=1000)
    maximum_fiber_samples: int = Field(default=1000000,ge=1,le=10000000)
    maximum_absolute_normal_strain: Positive = 0.01

    @model_validator(mode="after")
    def targets(self):
        if len({(f.member_id,f.case_id) for f in self.fields})!=len(self.fields):
            raise ValueError("one thermal fiber field is allowed per member and case")
        known={m.id:m for m in self.frame.members}
        for field in self.fields:
            if field.member_id not in known or field.case_id not in self.frame.load_cases:
                raise ValueError("thermal fiber field must target an existing member and load case")
            if known[field.member_id].rotation_releases:
                raise ValueError("thermal fiber loading currently requires unreleased member rotations")
            if field.expansion_curve.material_id!=known[field.member_id].material_id:
                raise ValueError("thermal expansion curve and member material differ")
        for member_id in {f.member_id for f in self.fields}:
            fields=[f for f in self.fields if f.member_id==member_id]
            coordinates=[(f.y_m,f.z_m,f.quadrature_area_m2) for f in fields[0].fibers]
            if any([(f.y_m,f.z_m,f.quadrature_area_m2) for f in field.fibers]!=coordinates for field in fields[1:]):
                raise ValueError("fields combined on one member must share an ordered thermal quadrature")
        work=sum(len(f.fibers)*len(known[f.member_id].sample_fractions)*len(self.frame.combinations) for f in self.fields)
        if work>self.maximum_fiber_samples:
            raise ValueError("thermal normal-stress recovery exceeds its fiber-sample budget")
        return self


def _project(field, section):
    fibers=field.fibers
    area=math.fsum(f.quadrature_area_m2 for f in fibers)
    my=math.fsum(f.quadrature_area_m2*f.y_m for f in fibers)
    mz=math.fsum(f.quadrature_area_m2*f.z_m for f in fibers)
    iy=math.fsum(f.quadrature_area_m2*f.z_m*f.z_m for f in fibers)
    iz=math.fsum(f.quadrature_area_m2*f.y_m*f.y_m for f in fibers)
    iyz=math.fsum(f.quadrature_area_m2*f.y_m*f.z_m for f in fibers)
    tolerance=field.quadrature_relative_tolerance
    if (not math.isclose(area,section.area_m2,rel_tol=tolerance,abs_tol=0)
        or not math.isclose(iy,section.iy_m4,rel_tol=tolerance,abs_tol=0)
        or not math.isclose(iz,section.iz_m4,rel_tol=tolerance,abs_tol=0)
        or abs(my/area)>field.centroid_absolute_tolerance_m
        or abs(mz/area)>field.centroid_absolute_tolerance_m
        or abs(iyz)>tolerance*math.sqrt(section.iy_m4*section.iz_m4)):
        raise ValueError("thermal quadrature does not reproduce the declared centroidal principal section")
    free=[integrate_thermal_expansion(field.expansion_curve,field.stress_free_temperature_K,f.temperature_K)
          for f in fibers]
    strains=[r["free_engineering_strain"] for r in free]
    # Solve the actual quadrature Gram matrix, rather than forcing tiny accepted
    # centroid/product-moment residuals into the projected stress resultants.
    import numpy as np
    y_scale=math.sqrt(section.iz_m4/section.area_m2)
    z_scale=math.sqrt(section.iy_m4/section.area_m2)
    basis=np.array([[1.0,f.z_m/z_scale,-f.y_m/y_scale] for f in fibers])
    weights=np.array([f.quadrature_area_m2 for f in fibers])
    gram=basis.T@(weights[:,None]*basis)
    load=basis.T@(weights*np.array(strains))
    try:
        projected=np.linalg.solve(gram,load)
    except np.linalg.LinAlgError as error:
        raise ValueError("thermal fiber quadrature is rank deficient") from error
    residual=np.array(strains)-basis@projected
    physical_basis=np.array([[1.0,f.z_m,-f.y_m] for f in fibers])
    return dict(mean_free_strain=float(projected[0]),curvature_y_per_m=float(projected[1]/z_scale),
                curvature_z_per_m=float(projected[2]/y_scale),free_strains=strains,
                self_equilibrated_free_strain_residual=residual.tolist(),
                quadrature=dict(area_m2=area,iy_m4=iy,iz_m4=iz,iyz_m4=iyz,
                                centroid_y_m=my/area,centroid_z_m=mz/area),
                residual_area_moments=(physical_basis.T@(weights*residual)).tolist(),
                quadrature_matches_section_within_declared_tolerances=True,
                curve_evaluation_digests=[r["record_digest"] for r in free])


def evaluate_thermal_section_frame(request: ThermalSectionRequest, context: RunContext):
    q=ThermalSectionRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    sources={name:file_digest(Path(__file__).with_name(name+".py"))
             for name in ("structural_thermal_section","structural","thermal_expansion")}
    input_digest=digest(dict(request=q,context=c))
    if (q.frame.asset_id,q.frame.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("thermal section asset/clock differs from context")
    by_member={m.id:m for m in q.frame.members}
    sections={s.id:s for s in q.frame.sections}
    materials={m.id:m for m in q.frame.materials}
    available={e.id for e in c.evidence}
    projected=[]
    strains=list(q.frame.initial_strains)
    curvatures=list(q.frame.initial_curvatures)
    for field in q.fields:
        if not field.plane_sections_uniform_elasticity_and_axial_temperature_reviewed:
            raise ValueError("thermal section plane-section, material and axial-temperature review is required")
        if not {field.evidence_id,field.expansion_curve.evidence_id}<=available:
            raise ValueError("thermal section/expansion evidence is absent")
        if any(s.member_id==field.member_id and s.case_id==field.case_id and s.kind=="thermal"
               for s in (*q.frame.initial_strains,*q.frame.initial_curvatures)):
            raise ValueError("thermal fiber field would duplicate an existing thermal eigenstrain")
        member=by_member[field.member_id]
        projection=_project(field,sections[member.section_id])
        if max(abs(v) for v in projection["free_strains"])>q.maximum_absolute_normal_strain:
            raise ValueError("thermal fiber strain exceeds the reviewed small-strain domain")
        projected.append(dict(field_id=field.id,member_id=field.member_id,case_id=field.case_id,**projection))
        strains.append(FrameInitialStrain(id=field.id+":axial",member_id=field.member_id,case_id=field.case_id,
            value=projection["mean_free_strain"],kind="thermal",evidence_id=field.evidence_id,
            uniform_axial_applicability_reviewed=True))
        curvatures.append(FrameInitialCurvature(id=field.id+":curvature",member_id=field.member_id,case_id=field.case_id,
            curvature_y_per_m=projection["curvature_y_per_m"],curvature_z_per_m=projection["curvature_z_per_m"],
            kind="thermal",evidence_id=field.evidence_id,uniform_curvature_applicability_reviewed=True))
    data=q.frame.model_dump(mode="python")
    data.update(initial_strains=tuple(strains),initial_curvatures=tuple(curvatures))
    assembled=FrameRequest.model_validate(data)
    result=solve_frame(assembled,c)
    recovery={}
    maximum=None
    maximum_total=None
    if result["status"]=="computed":
        maximum=0.0
        maximum_total=0.0
        for combination in q.frame.combinations:
            combined={}
            for member_id in {f.member_id for f in q.fields}:
                member=by_member[member_id]
                section=sections[member.section_id]
                young=materials[member.material_id].young_modulus_pa
                fields=[(f,p) for f,p in zip(q.fields,projected) if f.member_id==member_id]
                output=[]
                # Each field may supply different quadrature locations. Recover
                # every requested field on its own fibers while evaluating the
                # other fields' residuals only when those locations coincide.
                first=fields[0][0]
                coordinates=[(f.y_m,f.z_m,f.quadrature_area_m2) for f in first.fibers]
                if any([(f.y_m,f.z_m,f.quadrature_area_m2) for f in field.fibers]!=coordinates for field,_ in fields[1:]):
                    raise ValueError("fields combined on one member must share one ordered thermal quadrature")
                residual=[math.fsum(combination.factors.get(field.case_id,0.0)*p["self_equilibrated_free_strain_residual"][i]
                                    for field,p in fields) for i in range(len(first.fibers))]
                member_result=result["combinations"][combination.id]["members"][member_id]
                for sample in member_result["samples"]:
                    force=sample["physical_section_force_local_n"]
                    moment=sample["physical_section_moment_local_nm"]
                    normal=[force[0]/section.area_m2+moment[1]*f.z_m/section.iy_m4
                            -moment[2]*f.y_m/section.iz_m4-young*r for f,r in zip(first.fibers,residual)]
                    total_strain=[force[0]/(young*section.area_m2)+moment[1]*f.z_m/(young*section.iy_m4)
                                  -moment[2]*f.y_m/(young*section.iz_m4)
                                  +member_result["uniform_stress_free_axial_strain"]
                                  +f.z_m*member_result["uniform_stress_free_curvature_y_per_m"]
                                  -f.y_m*member_result["uniform_stress_free_curvature_z_per_m"] for f in first.fibers]
                    maximum=max(maximum,max(abs(v/young) for v in normal))
                    maximum_total=max(maximum_total,max(abs(v) for v in total_strain))
                    output.append(dict(source_fraction=sample["fraction"],normal_stress_Pa=normal,
                                       total_axial_strain=total_strain,
                                       self_equilibrated_thermal_stress_Pa=[-young*r for r in residual]))
                combined[member_id]=dict(fiber_ids=[f.id for f in first.fibers],samples=output)
            recovery[combination.id]=combined
    status=result["status"]
    issues=[]
    if maximum is not None and max(maximum,maximum_total)>q.maximum_absolute_normal_strain:
        status="out_of_scope"
        issues.append("recovered fiber normal strain exceeds the reviewed small-strain limit")
    if sources!={name:file_digest(Path(__file__).with_name(name+".py")) for name in sources} or input_digest!=digest(dict(request=q,context=c)):
        raise ValueError("thermal section source or input/context changed during evaluation")
    body=dict(schema="pipeline_integrity.thermal_section_frame.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status=status,issues=issues,projected_fields=projected,
        source_pins=sources,
        assembled_frame=assembled.model_dump(mode="json"),result=result,normal_stress_recovery=recovery,
        maximum_absolute_fiber_elastic_normal_strain=maximum,hoop_shear_or_code_stress_intensification_included=False,
        maximum_absolute_fiber_total_axial_strain=maximum_total,
        numerical_validation_completed=False,implementation_sha256=file_digest(Path(__file__)),
        structural_owner_sha256=file_digest(Path(__file__).with_name("structural.py")))
    body["record_digest"]=digest(body)
    return body


def verify_thermal_section_frame_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("thermal section frame receipt changed")
    q=ThermalSectionRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("thermal section caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("thermal section caller context differs")
    if evaluate_thermal_section_frame(q,c)!=record:
        raise ValueError("thermal section frame replay differs")
    return True
