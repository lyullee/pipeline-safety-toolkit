"""Nonlinear pressure/ovality equilibrium of an axially uniform elastic ring.

Reference-arc extension and rotation-gradient bending enter a conservative
energy with pressure times current enclosed area. Fourier shape coordinates
retain tangential motion. This is a thin-ring cross-section model, not a
finite-cylinder, plastic-collapse or contact assessment.
"""
from __future__ import annotations

import importlib
import json
import math
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest

THEORY_SOURCES=("https://doi.org/10.3389/fams.2023.1115227",)


class ElasticShellRingRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    cross_section_id: Name
    coordinate_frame_id: Name
    mean_reference_centerline_radius_m: Positive
    wall_thickness_m: Positive
    axial_strip_width_m: Positive
    young_modulus_Pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=0.5)
    axial_constraint: Literal["plane_stress_ring","plane_strain_axially_uniform_shell"]
    initial_diameter_ovality_fraction: float = Field(default=0.0,ge=0,le=0.1)
    external_minus_internal_pressure_Pa: tuple[Nonnegative,...] = Field(min_length=2,max_length=1000)
    maximum_cartesian_harmonic: int = Field(default=5,ge=3,le=12)
    quadrature_points: int = Field(default=256,ge=64,le=4096)
    maximum_thickness_to_radius_ratio: Positive = Field(default=0.05,le=0.1)
    maximum_axial_extension_strain_magnitude: Positive = Field(default=0.01,le=0.05)
    maximum_bending_surface_strain_magnitude: Positive = Field(default=0.01,le=0.05)
    maximum_shape_change_per_step_over_radius: Positive = Field(default=0.1,le=0.5)
    maximum_second_moment_ellipse_ovality_fraction: Positive = Field(default=0.5,le=1.0)
    absolute_equilibrium_force_tolerance_N: Positive = 1e-5
    relative_equilibrium_force_tolerance: Positive = Field(default=1e-8,le=1e-3)
    absolute_pressure_work_tolerance_J: Positive = 1e-4
    relative_pressure_work_tolerance: Positive = Field(default=1e-4,le=1e-2)
    positive_tangent_stiffness_tolerance_N_m: Nonnegative = 1e-5
    absolute_quadrature_stiffness_tolerance_N_m: Positive = 1e-5
    relative_quadrature_stiffness_tolerance: Positive = Field(default=1e-6,le=1e-2)
    relative_equilibrium_coordinate_tolerance: Positive = Field(default=1e-10,le=1e-3)
    minimum_pressure_step_Pa: Positive
    pressure_event_tolerance_Pa: Positive
    maximum_equilibrium_evaluations: int = Field(default=10000,ge=10,le=1000000)
    maximum_steps: int = Field(default=1000,ge=2,le=10000)
    maximum_event_refinements: int = Field(default=60,ge=1,le=200)
    native_backend_pins: dict
    geometry_evidence_id: Name
    material_evidence_id: Name
    pressure_evidence_id: Name
    scope_review_evidence_id: Name
    reference_ovality_is_stress_free_geometry_reviewed: bool = False
    homogeneous_linear_elastic_thin_unshearable_ring_reviewed: bool = False
    axial_constraint_and_omitted_finite_length_modes_reviewed: bool = False
    conservative_uniform_differential_pressure_reviewed: bool = False
    maximum_result_bytes: int = Field(default=30000000,ge=1000,le=200000000)

    @model_validator(mode="after")
    def support(self):
        if self.external_minus_internal_pressure_Pa[0]!=0 or any(b<=a for a,b in zip(
                self.external_minus_internal_pressure_Pa,self.external_minus_internal_pressure_Pa[1:])):
            raise ValueError("ring continuation pressure must increase strictly from the stress-free zero-pressure state")
        if self.wall_thickness_m/self.mean_reference_centerline_radius_m>self.maximum_thickness_to_radius_ratio:
            raise ValueError("ring thickness is outside the reviewed thin-wall support")
        if self.quadrature_points<16*self.maximum_cartesian_harmonic:
            raise ValueError("ring Fourier modes require at least sixteen quadrature samples per highest harmonic")
        return self


def elastic_shell_ring_backend_metadata():
    """Discover numerical-library provenance without evaluating a ring."""
    package=importlib.import_module("autograd")
    root=Path(package.__file__).resolve().parent
    tree={p.relative_to(root).as_posix():file_digest(p) for p in sorted(root.rglob("*.py"))}
    dist=distribution("autograd")
    notices=[Path(dist.locate_file(p)) for p in dist.files or () if Path(p).name.lower() in {"license.txt","license"}]
    notice=next((p for p in notices if p.is_file() and "Permission is hereby granted" in p.read_text(encoding="utf-8")),None)
    if not tree or notice is None:
        raise ValueError("native autograd Python source and MIT notice must remain available")
    return dict(autograd_version=dist.version,autograd_source_tree_sha256=digest(tree),autograd_source_files=len(tree),
        autograd_license_sha256=file_digest(notice),autograd_license="MIT",autograd_source="https://github.com/HIPS/autograd",
        scipy_version=version("scipy"),numpy_version=np.__version__)


def _ring_discretization(q,npoints):
    import autograd.numpy as anp
    from autograd import grad,hessian
    radius=q.mean_reference_centerline_radius_m
    angle=np.arange(npoints,dtype=float)*(2*math.pi/npoints)
    rx=radius*(1+q.initial_diameter_ovality_fraction/2)
    ry=radius*(1-q.initial_diameter_ovality_fraction/2)
    reference=(rx*np.cos(angle),ry*np.sin(angle))
    first=(-rx*np.sin(angle),ry*np.cos(angle))
    second=(-rx*np.cos(angle),-ry*np.sin(angle))
    speed=np.sqrt(first[0]**2+first[1]**2)
    reference_rotation_rate=(first[0]*second[1]-first[1]*second[0])/speed**2
    labels=[("x","cos",1),("y","cos",1),("y","sin",1)]
    labels.extend((axis,trig,n) for n in range(2,q.maximum_cartesian_harmonic+1) for axis in ("x","y") for trig in ("cos","sin"))
    bases={}
    for axis in ("x","y"):
        for derivative in (0,1,2):
            columns=[]
            for component,trig,n in labels:
                if component!=axis:
                    values=np.zeros(npoints)
                elif derivative==0:
                    values=radius*(np.cos(n*angle) if trig=="cos" else np.sin(n*angle))
                elif derivative==1:
                    values=radius*n*(-np.sin(n*angle) if trig=="cos" else np.cos(n*angle))
                else:
                    values=-radius*n*n*(np.cos(n*angle) if trig=="cos" else np.sin(n*angle))
                columns.append(values)
            bases[(axis,derivative)]=anp.array(np.column_stack(columns))
    effective_modulus=q.young_modulus_Pa/(1-q.poisson_ratio**2) if q.axial_constraint=="plane_strain_axially_uniform_shell" else q.young_modulus_Pa
    ea=effective_modulus*q.wall_thickness_m*q.axial_strip_width_m
    ei=effective_modulus*q.wall_thickness_m**3*q.axial_strip_width_m/12
    energy_scale=ei/radius
    weight=2*math.pi/npoints
    def fields(coefficients):
        x=reference[0]+anp.dot(bases[("x",0)],coefficients)
        y=reference[1]+anp.dot(bases[("y",0)],coefficients)
        x1=first[0]+anp.dot(bases[("x",1)],coefficients)
        y1=first[1]+anp.dot(bases[("y",1)],coefficients)
        x2=second[0]+anp.dot(bases[("x",2)],coefficients)
        y2=second[1]+anp.dot(bases[("y",2)],coefficients)
        current_speed=anp.sqrt(x1*x1+y1*y1)
        extension=current_speed/speed-1
        curvature=((x1*y2-y1*x2)/(current_speed*current_speed)-reference_rotation_rate)/speed
        area=0.5*weight*anp.sum(x*y1-y*x1)
        axial=0.5*ea*weight*anp.sum(extension*extension*speed)
        bending=0.5*ei*weight*anp.sum(curvature*curvature*speed)
        return x,y,x1,y1,x2,y2,extension,curvature,area,axial,bending
    def potential(coefficients,pressure):
        values=fields(coefficients)
        return (values[9]+values[10]+pressure*q.axial_strip_width_m*values[8])/energy_scale
    return dict(angle=angle,labels=labels,fields=fields,potential=potential,gradient=grad(potential,0),hessian=hessian(potential,0),
                ea=ea,ei=ei,energy_scale=energy_scale,reference_length_m=float(weight*np.sum(speed)))


def evaluate_elastic_shell_ring(request: ElasticShellRingRequest, context: RunContext):
    from scipy.optimize import root
    q=ElasticShellRingRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("shell-ring asset/clock differs from context")
    required={q.geometry_evidence_id,q.material_evidence_id,q.pressure_evidence_id,q.scope_review_evidence_id}
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence) or not required<=set(evidence) or any(evidence[id].sha256 is None for id in required):
        raise ValueError("ring geometry, elastic material, pressure and scope need source-pinned context evidence")
    if not all((q.reference_ovality_is_stress_free_geometry_reviewed,q.homogeneous_linear_elastic_thin_unshearable_ring_reviewed,
                q.axial_constraint_and_omitted_finite_length_modes_reviewed,q.conservative_uniform_differential_pressure_reviewed)):
        raise ValueError("stress-free reference geometry, ring constitutive law, axial constraint and follower pressure require review")
    backend=elastic_shell_ring_backend_metadata()
    if backend!=q.native_backend_pins:
        raise ValueError("ring derivative/equilibrium libraries differ from their reviewed source/runtime pins")
    source=file_digest(Path(__file__))
    base=_ring_discretization(q,q.quadrature_points)
    fine=_ring_discretization(q,2*q.quadrature_points)
    radius=q.mean_reference_centerline_radius_m
    scale=base["energy_scale"]
    if not all(math.isfinite(v) and v>0 for v in (base["ea"],base["ei"],scale)):
        raise ValueError("ring material/geometric energy scales exceed finite positive arithmetic")
    evaluations=0
    trials=[]
    rows=[]
    retained=0
    def consume():
        nonlocal evaluations
        evaluations+=1
        if evaluations>q.maximum_equilibrium_evaluations:
            raise ValueError("ring equilibrium/derivative work budget exceeded")
    def equilibrium(pressure,initial):
        def fun(values):
            consume()
            result=np.asarray(base["gradient"](values,pressure),dtype=float)
            if not np.isfinite(result).all():
                raise ValueError("ring equilibrium gradient exceeds finite arithmetic")
            return result
        def jac(values):
            consume()
            result=np.asarray(base["hessian"](values,pressure),dtype=float)
            if not np.isfinite(result).all():
                raise ValueError("ring tangent Hessian exceeds finite arithmetic")
            return result
        solution=root(fun,np.asarray(initial,dtype=float),jac=jac,method="hybr",
            options=dict(xtol=q.relative_equilibrium_coordinate_tolerance,maxfev=q.maximum_equilibrium_evaluations-evaluations))
        coefficients=np.asarray(solution.x,dtype=float)
        gradient=fun(coefficients)
        force_residual=float(np.max(np.abs(gradient))*scale/radius)
        force_scale=max(pressure*q.axial_strip_width_m*radius,base["ei"]/radius**2,1.0)
        tolerance=q.absolute_equilibrium_force_tolerance_N+q.relative_equilibrium_force_tolerance*force_scale
        if force_residual>tolerance:
            raise ValueError("ring equilibrium residual exceeds its physical force tolerance")
        tangent=jac(coefficients)*scale/radius**2
        if not np.allclose(tangent,tangent.T,rtol=1e-9,atol=q.absolute_quadrature_stiffness_tolerance_N_m):
            raise ValueError("conservative ring tangent Hessian is not symmetric")
        consume()
        finer=np.asarray(fine["hessian"](coefficients,pressure),dtype=float)*scale/radius**2
        eigenvalues,eigenvectors=np.linalg.eigh((tangent+tangent.T)/2)
        fine_eigenvalues=np.linalg.eigvalsh((finer+finer.T)/2)
        if not np.isfinite(finer).all() or not np.isfinite(eigenvalues).all():
            raise ValueError("ring tangent stability calculation exceeds finite arithmetic")
        quadrature_error=abs(float(eigenvalues[0]-fine_eigenvalues[0]))
        if quadrature_error>q.absolute_quadrature_stiffness_tolerance_N_m+q.relative_quadrature_stiffness_tolerance*max(abs(float(fine_eigenvalues[0])),1.0):
            raise ValueError("ring soft-mode stiffness is not resolved by the supplied nested angular quadrature")
        values=[np.asarray(v,dtype=float) for v in fine["fields"](coefficients)]
        if any(not np.isfinite(v).all() for v in values):
            raise ValueError("ring geometric/material fields exceed finite arithmetic")
        x,y,x1,y1,x2,y2,extension,curvature,area,axial,bending=values
        rotation_numerator=x1*y2-y1*x2
        radial=np.sqrt(x*x+y*y)
        angle_numerator=x*y1-y*x1
        if float(area)<=0 or np.any(rotation_numerator<=0) or np.any(angle_numerator<=0) or float(np.min(radial))<=q.wall_thickness_m:
            raise ValueError("ring left the convex, star-shaped open cross-section domain; contact is unresolved")
        axial_strain=float(np.max(np.abs(extension)))
        bending_strain=float(np.max(np.abs(curvature)))*q.wall_thickness_m/2
        if axial_strain>q.maximum_axial_extension_strain_magnitude or bending_strain>q.maximum_bending_surface_strain_magnitude:
            raise ValueError("ring left its declared small elastic material-strain domain")
        if math.hypot(*[float(v) for v in coefficients-np.asarray(initial)])>q.maximum_shape_change_per_step_over_radius:
            raise ValueError("ring equilibrium moved beyond the reviewed continuation branch-step bound")
        covariance=np.array([[float(np.mean(x*x)),float(np.mean(x*y))],[float(np.mean(x*y)),float(np.mean(y*y))]])
        axes=np.sqrt(2*np.linalg.eigvalsh(covariance))
        ovality=2*float(axes[1]-axes[0])/float(axes[1]+axes[0])
        if ovality>q.maximum_second_moment_ellipse_ovality_fraction:
            raise ValueError("ring ovalization exceeded its declared supported geometry range")
        minimum=float(eigenvalues[0])
        stable=minimum>q.positive_tangent_stiffness_tolerance_N_m
        return json_value(dict(pressure_Pa=pressure,coefficients=coefficients.tolist(),force_residual_N=force_residual,
            force_tolerance_N=tolerance,optimizer_success=bool(solution.success),optimizer_status=int(solution.status),
            enclosed_area_m2=float(area),axial_energy_J=float(axial),bending_energy_J=float(bending),
            pressure_potential_J=pressure*q.axial_strip_width_m*float(area),
            total_potential_J=float(axial+bending)+pressure*q.axial_strip_width_m*float(area),
            maximum_axial_strain=axial_strain,maximum_bending_surface_strain=bending_strain,
            second_moment_ellipse_ovality_fraction=ovality,angular_coordinate_rad=fine["angle"].tolist(),
            centerline_x_m=x.tolist(),centerline_y_m=y.tolist(),extension_strain=extension.tolist(),
            material_curvature_change_per_m=curvature.tolist(),minimum_tangent_stiffness_N_m=minimum,
            tangent_eigenvalues_N_m=eigenvalues.tolist(),soft_mode_coefficients=eigenvectors[:,0].tolist(),
            nested_quadrature_soft_eigenvalue_difference_N_m=quadrature_error,positive_tangent=stable))
    def retain(row):
        nonlocal retained
        retained+=len(json.dumps(row,allow_nan=False).encode("utf-8"))
        if retained>q.maximum_result_bytes:
            raise ValueError("ring retained-equilibrium byte budget exceeded")
    def attach_pressure_work(candidate,old):
        work=-0.5*(candidate["pressure_Pa"]+old["pressure_Pa"])*q.axial_strip_width_m*(candidate["enclosed_area_m2"]-old["enclosed_area_m2"])
        energy=candidate["axial_energy_J"]+candidate["bending_energy_J"]
        old_energy=old["axial_energy_J"]+old["bending_energy_J"]
        residual=energy-old_energy-work
        tolerance=q.absolute_pressure_work_tolerance_J+q.relative_pressure_work_tolerance*max(abs(energy-old_energy),abs(work))
        if abs(residual)>tolerance:
            raise ValueError("ring pressure-step work does not resolve the conservative strain-energy change")
        cumulative=old["cumulative_external_pressure_work_J"]+work
        initial_energy=rows[0]["axial_energy_J"]+rows[0]["bending_energy_J"]
        global_residual=energy-initial_energy-cumulative
        global_tolerance=q.absolute_pressure_work_tolerance_J+q.relative_pressure_work_tolerance*max(abs(energy-initial_energy),abs(cumulative))
        if abs(global_residual)>global_tolerance:
            raise ValueError("ring accumulated pressure-work/strain-energy balance requires finer continuation steps")
        candidate.update(external_pressure_work_step_J=work,cumulative_external_pressure_work_J=cumulative,
            pressure_work_step_energy_residual_J=residual,cumulative_pressure_work_energy_residual_J=global_residual)
        return candidate
    initial=equilibrium(0.0,np.zeros(len(base["labels"])))
    initial.update(external_pressure_work_step_J=0.0,cumulative_external_pressure_work_J=0.0,
        pressure_work_step_energy_residual_J=0.0,cumulative_pressure_work_energy_residual_J=initial["axial_energy_J"]+initial["bending_energy_J"])
    if not initial["positive_tangent"]:
        raise ValueError("stress-free ring has no resolved positive tangent after removing rigid gauges")
    rows.append(initial)
    retain(initial)
    event=None
    failure=None
    try:
        for target_pressure in q.external_minus_internal_pressure_Pa[1:]:
            while rows[-1]["pressure_Pa"]<target_pressure:
                if len(trials)>=q.maximum_steps:
                    raise ValueError("ring continuation exceeded its trial-step budget")
                old=rows[-1]
                attempted=target_pressure
                while True:
                    try:
                        candidate=attach_pressure_work(equilibrium(attempted,old["coefficients"]),old)
                        trials.append(dict(pressure_Pa=attempted,accepted_equilibrium=True))
                        break
                    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as error:
                        trials.append(dict(pressure_Pa=attempted,accepted_equilibrium=False,reason=type(error).__name__+": "+str(error)))
                        if len(trials)>=q.maximum_steps or attempted-old["pressure_Pa"]<=q.minimum_pressure_step_Pa or evaluations>=q.maximum_equilibrium_evaluations:
                            raise ValueError("ring continuation has an unresolved equilibrium/domain boundary, not a qualified collapse pressure") from error
                        attempted=0.5*(old["pressure_Pa"]+attempted)
                        if attempted<=old["pressure_Pa"]:
                            raise ValueError("ring pressure clock cannot represent its continuation refinement")
                retain(candidate)
                if candidate["positive_tangent"]:
                    rows.append(candidate)
                    continue
                low,high=old,candidate
                refinements=0
                while high["pressure_Pa"]-low["pressure_Pa"]>q.pressure_event_tolerance_Pa:
                    refinements+=1
                    if refinements>q.maximum_event_refinements:
                        raise ValueError("ring marginal-stability pressure bracket exceeded its refinement budget")
                    pressure=0.5*(low["pressure_Pa"]+high["pressure_Pa"])
                    if not low["pressure_Pa"]<pressure<high["pressure_Pa"]:
                        raise ValueError("ring stability bracket is below pressure floating-point resolution")
                    middle=attach_pressure_work(equilibrium(pressure,low["coefficients"]),low)
                    retain(middle)
                    if middle["positive_tangent"]:
                        low=middle
                    else:
                        high=middle
                high=attach_pressure_work(high,low)
                event=dict(kind="loss_of_resolved_positive_tangent_stiffness",pressure_bracket_Pa=[low["pressure_Pa"],high["pressure_Pa"]],
                    stiffness_threshold_N_m=q.positive_tangent_stiffness_tolerance_N_m,stable_equilibrium=low,
                    marginal_or_unstable_equilibrium=high,refinements=refinements,
                    mathematical_ring_instability_only=True)
                break
            if event is not None:
                break
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as error:
        failure=type(error).__name__+": "+str(error)
    if elastic_shell_ring_backend_metadata()!=backend or file_digest(Path(__file__))!=source:
        raise ValueError("ring mechanics or numerical-library source/runtime changed during continuation")
    body=json_value(dict(schema="pipeline_integrity.elastic_shell_ring.v1",request=q,context=c,
        status="withheld" if failure else "computed_diagnostic",failure=failure,states=rows,continuation_trials=trials,
        stability_event=event,backend=backend,implementation_sha256=source,derivative_evaluations=evaluations,
        coordinate_labels=[list(v) for v in base["labels"]],rigid_gauges="no Cartesian translations; x-sine fundamental fixed",
        axial_rigidity_EA_N=base["ea"],bending_rigidity_EI_N_m2=base["ei"],reference_perimeter_m=base["reference_length_m"],
        strain_measure="reference-arc extension and cross-section rotation gradient per reference arc",
        pressure_loading="uniform external-minus-internal pressure; current signed area conservative potential",
        nonlinear_ovality_equilibrium_solved=True,stress_free_ovality_geometry_used=True,
        arbitrary_knockdown_applied=False,finite_length_axial_shell_modes_solved=False,
        plasticity_or_wall_contact_solved=False,physical_ultimate_collapse_calculated=False,
        sampled_geometric_domain_gates_only=True,fourier_truncation_convergence_completed=False,
        theory_sources=THEORY_SOURCES,numerical_validation_completed=False,field_validation_claim=False))
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("ring complete source/result receipt exceeds its byte budget")
    return body


def verify_elastic_shell_ring_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("elastic shell-ring receipt changed")
    q=ElasticShellRingRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("elastic shell-ring caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("elastic shell-ring caller context differs")
    if record["status"]=="withheld":
        raise ValueError("unresolved ring continuation does not establish completed mechanical replay")
    if evaluate_elastic_shell_ring(q,c)!=record:
        raise ValueError("nonlinear elastic-ring pressure/ovality replay differs")
    return True
