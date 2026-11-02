"""Linear calibration with declared singular covariance and exact constraints.

The stochastic residual is whitened only in the positive covariance subspace.
Declared covariance-null directions remain exact equalities, not discarded
observations. Parameter and observation scales are supplied for rank decisions.
This complements the existing nonlinear full-covariance calibration service.
"""
from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path

import numpy as np
from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest
from .metrology import (
    MeasurementBasis, MeasurementInput, MeasurementOutput, MeasurementLinearization,
    MeasurementBudgetRequest, StandardCovariance, propagate_measurement_uncertainty,
)
from .units import _registry


class GLSParameter(Contract):
    id: Name
    basis: MeasurementBasis
    scale: Positive
    evidence_id: Name


class ExactParameterConstraint(Contract):
    id: Name
    coefficients: tuple[float, ...]
    coefficient_units: tuple[Name, ...]
    target: float
    target_basis: MeasurementBasis
    residual_scale: Positive
    evidence_id: Name


class ConstrainedGLSRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    observations: tuple[MeasurementInput, ...] = Field(min_length=1,max_length=64)
    parameters: tuple[GLSParameter, ...] = Field(min_length=1,max_length=32)
    design_matrix: tuple[tuple[float, ...], ...]
    design_coefficient_units: tuple[Name, ...]
    observation_scales: tuple[Positive, ...]
    covariance: StandardCovariance
    declared_covariance_rank: int = Field(ge=0,le=64)
    exact_constraints: tuple[ExactParameterConstraint, ...] = Field(default=(),max_length=64)
    relative_covariance_eigenvalue_tolerance: Positive = Field(default=1e-12,le=1e-4)
    relative_design_rank_tolerance: Positive = Field(default=1e-12,le=1e-4)
    normalized_equality_tolerance: Positive = Field(default=1e-9,le=1e-2)
    exact_linear_model_and_absolute_covariance_reviewed: bool = False
    singular_rank_and_exact_directions_reviewed: bool = False
    model_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def aligned(self):
        n,p=len(self.observations),len(self.parameters)
        ids=tuple(o.id for o in self.observations)
        if len(set(ids))!=n or len({v.id for v in self.parameters})!=p:
            raise ValueError("constrained GLS observation and parameter IDs must be unique")
        if len({v.id for v in self.exact_constraints})!=len(self.exact_constraints):
            raise ValueError("exact parameter constraint IDs must be unique")
        if self.covariance.input_ids!=ids or self.declared_covariance_rank>n:
            raise ValueError("GLS covariance must follow the exact observation order and rank bounds")
        if len(self.design_matrix)!=n or any(len(r)!=p for r in self.design_matrix):
            raise ValueError("GLS design rows follow observations and columns follow parameters")
        if len(self.design_coefficient_units)!=p or len(self.observation_scales)!=n:
            raise ValueError("GLS units and scales must align with the declared axes")
        if any(o.basis!=self.observations[0].basis for o in self.observations):
            raise ValueError("linear GLS observations must share one exact physical basis")
        for constraint in self.exact_constraints:
            if len(constraint.coefficients)!=p or len(constraint.coefficient_units)!=p:
                raise ValueError("exact constraint columns must follow all parameters")
        return self


def _compatible_coefficient(unit,parameter_basis,target_basis):
    units=_registry()
    try:
        magnitude=(units.Quantity(1.0,unit)*units.Quantity(1.0,parameter_basis.unit)).to(target_basis.unit).magnitude
    except Exception as error:
        raise ValueError("GLS coefficient and parameter units do not produce the target unit") from error
    if magnitude!=1.0:
        raise ValueError("GLS coefficient units must already use coherent target scaling")


def _seal(body):
    body["record_digest"]=digest(body)
    return body


def evaluate_constrained_gls(request: ConstrainedGLSRequest, context: RunContext):
    """Solve fully identified linear GLS without adding covariance jitter.

    Exact equalities are reduced with an SVD null-space parameterization. The
    positive covariance eigenvectors supply a whitened least-squares problem.
    All residual scales and covariance-rank tolerances remain in the receipt.
    """
    q=ConstrainedGLSRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("constrained GLS asset/clock differs from context")
    evidence={e.id for e in c.evidence}
    required=set(q.evidence_ids)|{q.model_evidence_id,q.covariance.evidence_id}
    for observation in q.observations:
        required|={observation.evidence_id,observation.basis.evidence_id}
    for parameter in q.parameters:
        required|={parameter.evidence_id,parameter.basis.evidence_id}
    for constraint in q.exact_constraints:
        required|={constraint.evidence_id,constraint.target_basis.evidence_id}
    for basis in (q.observations[0].basis,*(p.basis for p in q.parameters),
                  *(v.target_basis for v in q.exact_constraints)):
        if basis.reference_state is not None:
            required.add(basis.reference_state.evidence_id)
    if not required<=evidence or len(evidence)!=len(c.evidence):
        raise ValueError("GLS observations, constraints, scales and bases need unique context evidence")
    body=dict(schema="pipeline_integrity.constrained_gls.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="needs_data",issues=[],
              implementation_sha256=file_digest(Path(__file__)),
              metrology_source_sha256=file_digest(Path(__file__).with_name("metrology.py")),
              numpy_version=version("numpy"),numerical_validation_completed=False,
              covariance_jitter_added=False,covariance_null_directions_discarded=False,
              residual_variance_rescaling_applied=False)
    if (not q.exact_linear_model_and_absolute_covariance_reviewed
        or not q.singular_rank_and_exact_directions_reviewed
        or not q.covariance.definition_reviewed):
        body["issues"]=["linear model, absolute covariance and declared exact covariance directions require review"]
        return _seal(body)
    if any(o.estimate is None or o.valid is not True for o in q.observations):
        body["issues"]=["complete valid observations are required; no imputation"]
        return _seal(body)
    if any(v is None for row in q.covariance.values for v in row):
        body["issues"]=["complete covariance entries are required"]
        return _seal(body)
    basis=q.observations[0].basis
    for unit,parameter in zip(q.design_coefficient_units,q.parameters):
        _compatible_coefficient(unit,parameter.basis,basis)
    for constraint in q.exact_constraints:
        for unit,parameter in zip(constraint.coefficient_units,q.parameters):
            _compatible_coefficient(unit,parameter.basis,constraint.target_basis)
    n,p=len(q.observations),len(q.parameters)
    scales=np.asarray(q.observation_scales,dtype=float)
    parameter_scales=np.asarray([v.scale for v in q.parameters],dtype=float)
    design=np.asarray(q.design_matrix,dtype=float)
    y=np.asarray([o.estimate for o in q.observations],dtype=float)
    covariance=np.asarray(q.covariance.values,dtype=float)
    normalized=(covariance/scales[:,None])/scales[None,:]
    for i in range(n):
        for j in range(i):
            normalized[j,i]=normalized[i,j]
    b=(design*parameter_scales[None,:])/scales[:,None]
    ys=y/scales
    if not all(np.isfinite(a).all() for a in (normalized,b,ys)):
        raise ValueError("GLS normalization exceeded finite arithmetic")
    if np.any((covariance!=0)&(normalized==0)) or np.any((design!=0)&(b==0)) or np.any((y!=0)&(ys==0)):
        raise ValueError("GLS normalization underflowed a nonzero covariance, coefficient or observation")
    eigenvalues,eigenvectors=np.linalg.eigh(normalized)
    if not np.isfinite(eigenvalues).all() or not np.isfinite(eigenvectors).all():
        raise ValueError("GLS covariance eigendecomposition exceeded finite arithmetic")
    eigen_scale=float(np.max(np.abs(eigenvalues))) if np.any(eigenvalues) else 1.0
    cutoff=q.relative_covariance_eigenvalue_tolerance*eigen_scale
    zero_count=n-q.declared_covariance_rank
    exact_values=eigenvalues[:zero_count]
    positive_values=eigenvalues[zero_count:]
    body["covariance_decomposition"]=dict(declared_rank=q.declared_covariance_rank,
        normalized_eigenvalues=eigenvalues.tolist(),absolute_eigenvalue_cutoff=cutoff,
        observation_scales=list(q.observation_scales))
    if (np.any(eigenvalues < -cutoff) or np.any(np.abs(exact_values)>cutoff)
        or np.any(positive_values<=cutoff)):
        body.update(status="out_of_scope",issues=["covariance spectrum conflicts with the supplied rank or PSD tolerance; no repair"])
        return _seal(body)
    null_vectors=eigenvectors[:,:zero_count]
    positive_vectors=eigenvectors[:,zero_count:]
    equality_rows=[row for row in null_vectors.T@b]
    equality_targets=[float(v) for v in null_vectors.T@ys]
    for constraint in q.exact_constraints:
        equality_rows.append(np.asarray(constraint.coefficients)*parameter_scales/constraint.residual_scale)
        equality_targets.append(constraint.target/constraint.residual_scale)
    body["normalized_exact_equalities"]=dict(
        matrix=[np.asarray(row).tolist() for row in equality_rows],targets=equality_targets,
        covariance_null_direction_count=zero_count,
        explicit_constraint_ids=[v.id for v in q.exact_constraints],
        parameter_scales=parameter_scales.tolist())
    if equality_rows:
        equality=np.asarray(equality_rows,dtype=float)
        target=np.asarray(equality_targets,dtype=float)
        if not np.isfinite(equality).all() or not np.isfinite(target).all():
            raise ValueError("GLS exact constraint normalization exceeded finite arithmetic")
        u,s,vh=np.linalg.svd(equality,full_matrices=True)
        cutoff_e=q.relative_design_rank_tolerance*float(s[0]) if len(s) and s[0] else 0.0
        rank=int(np.sum(s>cutoff_e))
        body["equality_rank_diagnostics"]=dict(singular_values=s.tolist(),cutoff=cutoff_e,rank=rank)
        x0=vh[:rank,:].T@((u[:,:rank].T@target)/s[:rank]) if rank else np.zeros(p)
        equality_error=equality@x0-target
        max_error=float(np.max(np.abs(equality_error)))
        if max_error>q.normalized_equality_tolerance:
            body.update(status="out_of_scope",issues=["covariance-null and explicit exact constraints are mutually inconsistent"],
                        maximum_normalized_equality_error=max_error)
            return _seal(body)
        z=vh[rank:,:].T
    else:
        equality=np.empty((0,p))
        target=np.empty(0)
        rank=0
        x0=np.zeros(p)
        z=np.eye(p)
    whitened=(positive_vectors.T@b)/np.sqrt(positive_values)[:,None] if len(positive_values) else np.empty((0,p))
    whitened_y=(positive_vectors.T@ys)/np.sqrt(positive_values) if len(positive_values) else np.empty(0)
    reduced=whitened@z
    reduced_target=whitened_y-whitened@x0
    if not all(np.isfinite(a).all() for a in (whitened,whitened_y,reduced,reduced_target)):
        raise ValueError("GLS stochastic whitening exceeded finite arithmetic")
    free=z.shape[1]
    if free:
        u,s,vh=np.linalg.svd(reduced,full_matrices=False)
        cutoff_d=q.relative_design_rank_tolerance*float(s[0]) if len(s) and s[0] else 0.0
        design_rank=int(np.sum(s>cutoff_d))
        body["reduced_design_rank_diagnostics"]=dict(singular_values=s.tolist(),cutoff=cutoff_d,rank=design_rank)
        if design_rank!=free:
            body.update(status="out_of_scope",issues=["stochastic observations and exact constraints do not identify every parameter"],
                        equality_rank=rank,free_parameter_count=free,reduced_design_rank=design_rank)
            return _seal(body)
        free_estimate=vh.T@((u.T@reduced_target)/s)
        x=x0+z@free_estimate
        covariance_factor=(z@vh.T)/s[None,:]
    else:
        x=x0
        covariance_factor=np.empty((p,0))
    beta=x*parameter_scales
    covariance_factor=parameter_scales[:,None]*covariance_factor
    if np.any((x!=0)&(beta==0)) or not np.isfinite(covariance_factor).all():
        raise ValueError("GLS physical parameter scaling is outside finite arithmetic")
    parameter_covariance=covariance_factor@covariance_factor.T
    if any(parameter_covariance[i,i]==0 and np.any(covariance_factor[i]!=0) for i in range(p)):
        raise ValueError("GLS parameter variance underflow must not be interpreted as exact certainty")
    # The same dot product defines both triangles, rather than modifying a
    # caller covariance to make an asymmetric matrix appear valid.
    for i in range(p):
        for j in range(i):
            parameter_covariance[j,i]=parameter_covariance[i,j]
    prediction=design@beta
    if not all(np.isfinite(a).all() for a in (beta,parameter_covariance,prediction)):
        raise ValueError("constrained GLS estimate/covariance exceeded finite arithmetic")
    equality_error=float(np.max(np.abs(equality@x-target))) if len(target) else 0.0
    if equality_error>q.normalized_equality_tolerance:
        body.update(status="out_of_scope",issues=["fitted parameters fail the declared normalized exact equalities"],
                    maximum_normalized_equality_error=equality_error)
        return _seal(body)
    fitted_inputs=tuple(MeasurementInput(id=parameter.id,estimate=float(value),basis=parameter.basis,
                          evidence_id=parameter.evidence_id,valid=True) for parameter,value in zip(q.parameters,beta))
    fitted_covariance=StandardCovariance(input_ids=tuple(v.id for v in q.parameters),
        values=tuple(tuple(float(v) for v in row) for row in parameter_covariance),
        evidence_id=q.model_evidence_id,definition_reviewed=True)
    prediction_request=MeasurementBudgetRequest(id=q.id+":fitted_predictions",asset_id=q.asset_id,
        clock_id=q.clock_id,event_id=q.event_id,time_s=q.time_s,inputs=fitted_inputs,covariance=fitted_covariance,
        linearization=MeasurementLinearization(model_id="constrained_linear_gls_predictions",model_version="1",
            provider_source_sha256=body["implementation_sha256"],configuration_sha256=digest(q),
            inputs_sha256=digest(fitted_inputs),outputs=tuple(MeasurementOutput(id=o.id,estimate=float(v),basis=o.basis)
                for o,v in zip(q.observations,prediction)),jacobian=q.design_matrix,
            evidence_id=q.model_evidence_id,local_approximation_reviewed=True))
    prediction_budget=propagate_measurement_uncertainty(prediction_request,c)
    stochastic_residual=whitened_y-whitened@x
    body.update(status="computed" if prediction_budget["status"]=="computed" else "partial",
        issues=[] if prediction_budget["status"]=="computed" else ["shared metrology prediction propagation withheld a result"],
        parameter_order=[v.id for v in q.parameters],parameter_estimates=beta.tolist(),
        parameter_covariance=fitted_covariance.model_dump(mode="json"),
        fitted_predictions=prediction.tolist(),observation_residuals=(y-prediction).tolist(),
        stochastic_sum_squares=float(stochastic_residual@stochastic_residual),
        stochastic_degrees_of_freedom=q.declared_covariance_rank-free,
        equality_rank=rank,free_parameter_count=free,maximum_normalized_equality_error=equality_error,
        prediction_uncertainty=prediction_budget,
        prediction_covariance_is_for_fitted_mean=True,independent_validation_completed=False,
        probability_or_coverage_inferred=False,parameter_bounds_enforced=False)
    return _seal(body)


def verify_constrained_gls_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("constrained GLS receipt changed")
    q=ConstrainedGLSRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("constrained GLS caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("constrained GLS caller context differs")
    if evaluate_constrained_gls(q,c)!=record:
        raise ValueError("constrained GLS replay differs")
    return True


def prepare_affine_singular_gls(calibration, context: RunContext, *, parameter_scales: dict,
                                observation_scales: dict, declared_covariance_rank: int,
                                offset_id="offset", slope_id="slope", exact_constraints=(),
                                singular_rank_and_exact_directions_reviewed=False):
    """Translate reviewed exact-x affine calibration data into this GLS model.

    The original calibration's source, split, domain and evidence gates are
    retained. This factory does not convert nonlinear errors-in-variables into
    linear y errors, apply parameter bounds, or claim held-out validation.
    """
    from .calibration import CalibrationRequest, affine_provider, _policy, _scope
    q=CalibrationRequest.model_validate(calibration.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    parameters={p.id:p for p in q.parameters}
    if set(parameters)!={offset_id,slope_id} or offset_id==slope_id:
        raise ValueError("the singular affine bridge requires exactly its declared offset and slope")
    provider=affine_provider(x_unit=q.x_basis.unit,y_unit=q.y_basis.unit,
                            slope_unit=parameters[slope_id].unit,offset=offset_id,slope=slope_id)
    issues=_policy(q,c,provider)
    if issues:
        raise ValueError("affine source calibration requires its original applicability reviews: "+"; ".join(issues))
    scope_issue=_scope(q)
    if scope_issue is not None:
        raise ValueError("affine source calibration is outside its original scope: "+scope_issue)
    fit=[i for i,row in enumerate(q.observations) if row.role=="fit"]
    if not {q.observations[i].id for i in fit}<=set(q.exact_x_ids):
        raise ValueError("singular affine GLS requires exact source abscissae; stochastic x remains with the nonlinear calibration owner")
    if set(parameter_scales)!=set(parameters) or set(observation_scales)!={q.observations[i].id for i in fit}:
        raise ValueError("affine GLS scales must identify exactly every parameter and fitted observation")
    evidence_id=q.review_evidence_ids["model_domain_and_error_structure"]
    units=_registry()
    fitted_parameters=[]
    for parameter in q.parameters:
        pressure=units.Quantity(1.0,parameter.unit).check("[pressure]")
        basis=MeasurementBasis(quantity="calibration_parameter",unit=parameter.unit,
            definition_id=q.model_id+":"+parameter.id,evidence_id=evidence_id,
            pressure_reference=q.y_basis.pressure_reference if pressure else None,
            reference_state=q.y_basis.reference_state)
        fitted_parameters.append(GLSParameter(id=parameter.id,basis=basis,
            scale=parameter_scales[parameter.id],evidence_id=evidence_id))
    observations=tuple(MeasurementInput(id="y:"+q.observations[i].id,
        estimate=q.observations[i].y,basis=q.y_basis,evidence_id=q.observations[i].evidence_id,valid=True) for i in fit)
    n=len(q.observations)
    covariance=StandardCovariance(input_ids=tuple(o.id for o in observations),
        values=tuple(tuple(q.covariance.values[n+i][n+j] for j in fit) for i in fit),
        evidence_id=q.covariance.evidence_id,definition_reviewed=q.covariance.definition_reviewed)
    design=tuple(tuple(1.0 if parameter.id==offset_id else q.observations[i].x
                       for parameter in q.parameters) for i in fit)
    request=ConstrainedGLSRequest(id=q.event_id+":singular_affine",asset_id=q.asset_id,
        clock_id=q.clock_id,event_id=q.event_id,
        time_s=max(q.observations[i].observation_time_s for i in fit)
            if all(q.observations[i].observation_time_s is not None for i in fit) else None,
        observations=observations,parameters=tuple(fitted_parameters),design_matrix=design,
        design_coefficient_units=tuple("dimensionless" if parameter.id==offset_id else q.x_basis.unit for parameter in q.parameters),
        observation_scales=tuple(observation_scales[q.observations[i].id] for i in fit),covariance=covariance,
        declared_covariance_rank=declared_covariance_rank,exact_constraints=tuple(exact_constraints),
        exact_linear_model_and_absolute_covariance_reviewed=True,
        singular_rank_and_exact_directions_reviewed=singular_rank_and_exact_directions_reviewed,
        model_evidence_id=evidence_id,evidence_ids=q.evidence_ids)
    receipt=dict(schema="pipeline_integrity.affine_singular_gls_binding.v1",source_calibration_digest=digest(q),
        context_digest=digest(c),derived_request=request.model_dump(mode="json"),
        original_parameter_bounds={p.id:[p.lower,p.upper] for p in q.parameters},parameter_bounds_enforced=False,
        validation_observation_ids=[r.id for r in q.observations if r.role=="validation"],
        independent_validation_executed=False,stochastic_x_reinterpreted_as_exact=False,
        calibration_source_sha256=file_digest(Path(__file__).with_name("calibration.py")))
    receipt["record_digest"]=digest(receipt)
    return request,receipt


class GLSPredictionTarget(Contract):
    id: Name
    coefficients: tuple[float, ...]
    coefficient_units: tuple[Name, ...]
    basis: MeasurementBasis
    evidence_id: Name


class ConstrainedGLSPredictionRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    source_fit: dict
    targets: tuple[GLSPredictionTarget, ...] = Field(min_length=1,max_length=64)
    mapping_evidence_id: Name
    linear_prediction_applicability_reviewed: bool = False

    @field_validator("source_fit")
    @classmethod
    def source_budget(cls,value):
        value=json_value(value)
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8"))>16000000:
            raise ValueError("constrained GLS source receipt exceeds the prediction input budget")
        return value

    @model_validator(mode="after")
    def identifiers(self):
        if len({t.id for t in self.targets})!=len(self.targets):
            raise ValueError("constrained GLS prediction target IDs must be unique")
        return self


def evaluate_constrained_gls_prediction(request: ConstrainedGLSPredictionRequest, context: RunContext):
    """Apply reviewed linear functionals of a source-bound fitted parameter vector."""
    q=ConstrainedGLSPredictionRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.linear_prediction_applicability_reviewed:
        raise ValueError("linear prediction applicability requires explicit review")
    verify_constrained_gls_record(q.source_fit)
    if q.source_fit["status"]!="computed":
        raise ValueError("a computed constrained GLS source fit is required")
    source=ConstrainedGLSRequest.model_validate_json(json.dumps(q.source_fit["request"]))
    source_context=RunContext.model_validate_json(json.dumps(q.source_fit["context"]))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id) or (source.asset_id,source.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("GLS prediction cannot silently transfer the fitted asset or clock")
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence):
        raise ValueError("prediction context evidence IDs must be unique")
    for old in source_context.evidence:
        if old.id not in evidence or evidence[old.id].model_dump(mode="json")!=old.model_dump(mode="json"):
            raise ValueError("prediction context must preserve the source fit evidence unchanged")
    mapping=evidence.get(q.mapping_evidence_id)
    if mapping is None or mapping.sha256 is None:
        raise ValueError("prediction mapping needs source-hash-pinned context evidence")
    p=len(source.parameters)
    for target in q.targets:
        if len(target.coefficients)!=p or len(target.coefficient_units)!=p:
            raise ValueError("prediction coefficients must follow the fitted parameter order")
        if target.evidence_id not in evidence or target.basis.evidence_id not in evidence:
            raise ValueError("prediction model and output bases need context evidence")
        for unit,parameter in zip(target.coefficient_units,source.parameters):
            _compatible_coefficient(unit,parameter.basis,target.basis)
    beta=np.asarray(q.source_fit["parameter_estimates"],dtype=float)
    matrix=np.asarray([t.coefficients for t in q.targets],dtype=float)
    prediction=matrix@beta
    if not np.isfinite(prediction).all():
        raise ValueError("GLS linear prediction exceeded finite arithmetic")
    inputs=tuple(MeasurementInput(id=parameter.id,estimate=float(value),basis=parameter.basis,
        evidence_id=parameter.evidence_id,valid=True) for parameter,value in zip(source.parameters,beta))
    covariance=StandardCovariance.model_validate_json(json.dumps(q.source_fit["parameter_covariance"]))
    budget=MeasurementBudgetRequest(id=q.id,asset_id=q.asset_id,clock_id=q.clock_id,event_id=q.event_id,
        time_s=q.time_s,inputs=inputs,covariance=covariance,
        linearization=MeasurementLinearization(model_id="constrained_gls_linear_prediction",model_version="1",
            provider_source_sha256=file_digest(Path(__file__)),configuration_sha256=digest(q),
            inputs_sha256=digest(inputs),outputs=tuple(MeasurementOutput(id=t.id,estimate=float(v),basis=t.basis)
                for t,v in zip(q.targets,prediction)),jacobian=tuple(t.coefficients for t in q.targets),
            evidence_id=q.mapping_evidence_id,local_approximation_reviewed=True))
    propagated=propagate_measurement_uncertainty(budget,c)
    body=dict(schema="pipeline_integrity.constrained_gls_prediction.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),source_fit_digest=q.source_fit["record_digest"],
        status=propagated["status"],uncertainty=propagated,
        prediction_is_conditional_on_supplied_exact_linear_model=True,
        future_observation_error_included=False,independent_validation_completed=False,
        implementation_sha256=file_digest(Path(__file__)),numerical_validation_completed=False)
    return _seal(body)


def verify_constrained_gls_prediction_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("constrained GLS prediction receipt changed")
    q=ConstrainedGLSPredictionRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("constrained GLS prediction caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("constrained GLS prediction caller context differs")
    if evaluate_constrained_gls_prediction(q,c)!=record:
        raise ValueError("constrained GLS prediction replay differs")
    return True
