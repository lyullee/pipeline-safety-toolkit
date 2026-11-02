"""Traceable local measurement covariance and explicitly selected decision rules.

Sensitivities belong to the caller's actual measurement model at the supplied
input estimates. This service does not fit a model or infer missing correlation.
"""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .units import _registry

SHA = r"^[a-f0-9]{64}$"


class MeasurementReferenceState(Contract):
    pressure_pa_abs: Positive
    temperature_k: Positive
    composition_id: Name
    moisture_basis: Literal["dry", "wet", "as_measured"]
    evidence_id: Name


class MeasurementBasis(Contract):
    quantity: Name
    unit: Name
    definition_id: Name
    evidence_id: Name
    pressure_reference: Literal["absolute", "gauge", "differential"] | None = None
    reference_state: MeasurementReferenceState | None = None

    @model_validator(mode="after")
    def coherent_si(self):
        units = _registry()
        try:
            value = units.Quantity(1.0, self.unit).to_base_units()
        except Exception as exc:
            raise ValueError("an explicit coherent SI unit is required") from exc
        if value.magnitude != 1.0:
            raise ValueError("convert scaled or offset units before constructing the measurement budget")
        is_pressure = value.check("[pressure]")
        if is_pressure != (self.pressure_reference is not None):
            raise ValueError("pressure quantities require their explicit pressure reference only")
        return self


class MeasurementInput(Contract):
    id: Name
    estimate: float | None
    basis: MeasurementBasis
    evidence_id: Name
    valid: bool | None = None

    @model_validator(mode="after")
    def validity(self):
        if self.valid is True and self.estimate is None:
            raise ValueError("a valid measurement input cannot have a missing estimate")
        return self


class MeasurementOutput(Contract):
    id: Name
    estimate: float
    basis: MeasurementBasis


class MeasurementLinearization(Contract):
    model_id: Name
    model_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    configuration_sha256: str = Field(pattern=SHA)
    inputs_sha256: str = Field(pattern=SHA)
    outputs: tuple[MeasurementOutput, ...] = Field(min_length=1, max_length=64)
    jacobian: tuple[tuple[float, ...], ...]
    evidence_id: Name
    local_approximation_reviewed: bool = False


class StandardCovariance(Contract):
    input_ids: tuple[Name, ...] = Field(min_length=1, max_length=64)
    values: tuple[tuple[float | None, ...], ...]
    evidence_id: Name
    definition_reviewed: bool = False

    @model_validator(mode="after")
    def matrix(self):
        n = len(self.input_ids)
        if len(set(self.input_ids)) != n or len(self.values) != n or any(len(r) != n for r in self.values):
            raise ValueError("a square standard covariance must follow unique input identifiers")
        for i, row in enumerate(self.values):
            if row[i] is not None and row[i] < 0:
                raise ValueError("standard variances must be nonnegative")
            for j, entry in enumerate(row):
                if entry != self.values[j][i]:
                    raise ValueError("covariance, including unresolved entries, must be exactly symmetric")
                if row[i] == 0 and entry not in (None, 0):
                    raise ValueError("a zero-variance input cannot have nonzero covariance")
        return self


class CoverageDeclaration(Contract):
    factor: Positive
    evidence_id: Name
    reviewed: bool = False
    supplied_probability: Fraction | None = None
    probability_basis: Name | None = None

    @model_validator(mode="after")
    def supplied_basis(self):
        if (self.supplied_probability is None) != (self.probability_basis is None):
            raise ValueError("a supplied coverage probability and its basis must be paired")
        return self


class MeasurementDecisionRule(Contract):
    output_id: Name
    lower_limit: float | None = None
    upper_limit: float | None = None
    guard_band: Nonnegative = 0.0
    comparison: Literal["nominal", "expanded_interval"]
    evidence_id: Name
    reviewed: bool = False

    @model_validator(mode="after")
    def limits(self):
        if self.lower_limit is None and self.upper_limit is None:
            raise ValueError("a decision rule requires at least one finite limit")
        if self.lower_limit is not None and self.upper_limit is not None:
            if self.lower_limit + self.guard_band > self.upper_limit - self.guard_band:
                raise ValueError("guard band cannot reverse the acceptance limits")
        return self


class MeasurementBudgetRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    inputs: tuple[MeasurementInput, ...] = Field(min_length=1, max_length=64)
    linearization: MeasurementLinearization | None = None
    covariance: StandardCovariance | None = None
    coverage: CoverageDeclaration | None = None
    decision_rules: tuple[MeasurementDecisionRule, ...] = ()

    @model_validator(mode="after")
    def align(self):
        ids = tuple(v.id for v in self.inputs)
        if len(set(ids)) != len(ids):
            raise ValueError("measurement input identifiers must be unique")
        if self.covariance and self.covariance.input_ids != ids:
            raise ValueError("covariance order must exactly match input order")
        model = self.linearization
        if model:
            if model.inputs_sha256 != digest(self.inputs):
                raise ValueError("sensitivities must bind the exact input estimates, units and bases")
            output_ids = tuple(v.id for v in model.outputs)
            if len(set(output_ids)) != len(output_ids):
                raise ValueError("output identifiers must be unique")
            if len(model.jacobian) != len(output_ids) or any(len(r) != len(ids) for r in model.jacobian):
                raise ValueError("Jacobian rows follow outputs and columns follow inputs")
            if any(r.output_id not in output_ids for r in self.decision_rules):
                raise ValueError("each decision rule must identify a declared model output")
        elif self.decision_rules:
            raise ValueError("decision rules require declared model outputs")
        if len({r.output_id for r in self.decision_rules}) != len(self.decision_rules):
            raise ValueError("choose one explicit decision rule per output")
        return self


def _evidence(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("measurement context must match the asset and clock")
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("measurement evidence identifiers must be unique")
    required = set()
    for value in request.inputs:
        required.add(value.evidence_id)
    bases = [v.basis for v in request.inputs]
    if request.linearization:
        bases.extend(v.basis for v in request.linearization.outputs)
        required.add(request.linearization.evidence_id)
    for basis in bases:
        required.add(basis.evidence_id)
        if basis.reference_state:
            required.add(basis.reference_state.evidence_id)
    for declaration in (request.covariance, request.coverage, *request.decision_rules):
        if declaration:
            required.add(declaration.evidence_id)
    if not required <= set(ids):
        raise ValueError("all observations, bases, model and uncertainty declarations need context evidence")


def _seal(record):
    record["record_digest"] = digest(record)
    return record


def _decision(output, uncertainty, coverage, rule):
    result = {"status": "indeterminate", "rule": rule.model_dump(mode="json"), "evaluated_interval": None}
    if not rule.reviewed or (rule.comparison == "expanded_interval" and not coverage):
        return result
    radius = 0.0 if rule.comparison == "nominal" else uncertainty * coverage.factor
    lo, hi = output.estimate - radius, output.estimate + radius
    lower = -math.inf if rule.lower_limit is None else rule.lower_limit + rule.guard_band
    upper = math.inf if rule.upper_limit is None else rule.upper_limit - rule.guard_band
    if not all(math.isfinite(v) for v in (lo, hi)):
        return result
    result["evaluated_interval"] = [lo, hi]
    if lo >= lower and hi <= upper:
        result["status"] = "accepted_by_declared_rule"
    elif hi < lower or lo > upper:
        result["status"] = "rejected_by_declared_rule"
    return result


def propagate_measurement_uncertainty(request: MeasurementBudgetRequest, context: RunContext):
    """Apply caller-supplied local sensitivities to a complete standard covariance.

    Every covariance entry has units input[i].unit * input[j].unit. A Jacobian
    entry has units output[row].unit / input[column].unit. No unit conversion,
    model differentiation, covariance estimation or coverage selection is hidden.
    """
    request = MeasurementBudgetRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    _evidence(request, context)
    record = {
        "schema": "pipeline_integrity.measurement_uncertainty.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "computed",
        "issues": [],
        "outputs": {},
        "output_covariance": None,
        "input_matrix_diagnostics": None,
        "coverage_probability_computed": False,
        "calibration_fit_performed": False,
        "custody_transfer_certified": False,
        "implementation_sha256": file_digest(Path(__file__)),
        "unit_boundary_sha256": file_digest(Path(__file__).with_name("units.py")),
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pint": version("pint")},
    }
    model, covariance = request.linearization, request.covariance
    issues = record["issues"]
    if any(v.valid is False for v in request.inputs):
        record.update(status="out_of_scope", issues=["explicitly invalid measurement inputs are retained"])
        return _seal(record)
    if any(v.valid is None for v in request.inputs):
        issues.append("measurement input validity is unresolved")
    if any(v.estimate is None for v in request.inputs):
        issues.append("measurement input estimates are unresolved")
    if model is None or not model.local_approximation_reviewed:
        issues.append("a model-bound reviewed local linearization is required")
    if covariance is None or not covariance.definition_reviewed:
        issues.append("a reviewed standard covariance definition is required")
    if covariance and any(v is None for row in covariance.values for v in row):
        issues.append("unresolved covariance entries are not assumed independent")
    if issues:
        record["status"] = "needs_data"
        return _seal(record)

    matrix = np.asarray(covariance.values, dtype=float)
    deviations = np.sqrt(np.diag(matrix))
    indices = np.flatnonzero(deviations > 0)
    if len(indices):
        selected = matrix[np.ix_(indices, indices)]
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            correlation = selected / deviations[indices, None] / deviations[None, indices]
        if not np.isfinite(correlation).all():
            record.update(status="out_of_scope", issues=["covariance normalization is not representable"])
            return _seal(record)
        try:
            eigenvalues = np.linalg.eigvalsh(correlation)
        except np.linalg.LinAlgError:
            record.update(status="out_of_scope", issues=["covariance eigendecomposition did not converge"])
            return _seal(record)
        if not np.isfinite(eigenvalues).all():
            record.update(status="out_of_scope", issues=["covariance eigenvalues are not representable"])
            return _seal(record)
        minimum = float(eigenvalues.min())
        tolerance = 64 * np.finfo(float).eps * len(indices)
    else:
        minimum, tolerance = 0.0, 0.0
    record["input_matrix_diagnostics"] = {
        "minimum_scaled_eigenvalue": minimum,
        "roundoff_tolerance": tolerance,
        "matrix_repaired": False,
        "zero_variance_inputs": [request.inputs[int(i)].id for i in np.flatnonzero(deviations == 0)],
    }
    if minimum < -tolerance:
        record.update(status="out_of_scope", issues=["input covariance is not positive semidefinite"])
        return _seal(record)

    jacobian = model.jacobian
    n = len(request.inputs)
    try:
        propagated = [[0.0] * len(jacobian) for _ in jacobian]
        for row, a in enumerate(jacobian):
            for column in range(row, len(jacobian)):
                b = jacobian[column]
                value = math.fsum(a[i] * float(matrix[i, j]) * b[j] for i in range(n) for j in range(n))
                # One bilinear form supplies both symmetric entries, including roundoff.
                propagated[row][column] = propagated[column][row] = value
        for row, sensitivity in enumerate(jacobian):
            if propagated[row][row] == 0 and any(
                v != 0 and matrix[i, i] > 0 for i, v in enumerate(sensitivity)
            ):
                from fractions import Fraction as ExactFraction

                # Distinguish a true null-space cancellation from floating underflow.
                exact_variance = sum(
                    ExactFraction(sensitivity[i])
                    * ExactFraction(float(matrix[i, j]))
                    * ExactFraction(sensitivity[j])
                    for i in range(n)
                    for j in range(n)
                )
                if exact_variance != 0:
                    raise ArithmeticError(
                        "zero output variance is a numerical range/cancellation failure; no exact zero uncertainty inferred"
                    )
        if any(not math.isfinite(v) for row in propagated for v in row) or any(
            row[i] < 0 for i, row in enumerate(propagated)
        ):
            raise ArithmeticError("nonfinite propagation or negative variance; no clipping")
        coverage = request.coverage if request.coverage and request.coverage.reviewed else None
        rules = {r.output_id: r for r in request.decision_rules}
        for index, output in enumerate(model.outputs):
            sensitivity = jacobian[index]
            diagonal = [sensitivity[i] * float(matrix[i, i]) * sensitivity[i] for i in range(n)]
            paired = [
                {
                    "input_ids": [request.inputs[i].id, request.inputs[j].id],
                    "variance_contribution": (sensitivity[i] * float(matrix[i, j]) * sensitivity[j]) * 2,
                }
                for i in range(n)
                for j in range(i + 1, n)
            ]
            uncertainty = math.sqrt(propagated[index][index])
            expanded = None if coverage is None else uncertainty * coverage.factor
            if expanded is not None and not math.isfinite(expanded):
                raise ArithmeticError("expanded uncertainty is not representable")
            record["outputs"][output.id] = {
                "estimate": output.estimate,
                "basis": output.basis.model_dump(mode="json"),
                "standard_uncertainty": uncertainty,
                "expanded_uncertainty": expanded,
                "supplied_coverage": coverage.model_dump(mode="json") if coverage else None,
                "diagonal_variance_contributions": dict(
                    zip((v.id for v in request.inputs), diagonal, strict=True)
                ),
                "paired_covariance_contributions": paired,
                "decision": _decision(output, uncertainty, coverage, rules[output.id])
                if output.id in rules
                else None,
            }
        record["output_covariance"] = propagated
        digest(record)
    except (OverflowError, ArithmeticError, ValueError) as exc:
        record.update(status="out_of_scope", issues=[str(exc)], outputs={}, output_covariance=None)
    return _seal(record)


def verify_measurement_uncertainty(record):
    """Reconstruct the entire local budget and decision, including rehashed mutations."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("measurement uncertainty record seal is invalid")
    if record.get("implementation_sha256") != file_digest(Path(__file__)):
        raise ValueError("measurement implementation differs from the recorded source")
    request = MeasurementBudgetRequest.model_validate_json(json.dumps(record["request"]))
    context = RunContext.model_validate_json(json.dumps(record["context"]))
    expected = propagate_measurement_uncertainty(request, context)
    if record != expected:
        raise ValueError("measurement record differs from reconstructed covariance and decision semantics")
    return True
