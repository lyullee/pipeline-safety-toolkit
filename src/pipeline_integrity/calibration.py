"""Full-joint-covariance calibration through the shared native least-squares engine.

Latent abscissae are physical model inputs. Observation indices and campaign IDs
are never interpreted as time or as independent repetitions.
"""

from __future__ import annotations

import inspect
import json
import platform
import types
from dataclasses import dataclass, field
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from pydantic import Field, model_validator

from . import contracts, inverse, metrology
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .inverse import FitParameter
from .metrology import MeasurementBasis, StandardCovariance
from .units import _registry

SCHEMA = "pipeline_integrity.calibration.v1"
SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "measurement_pairs_units_and_full_covariance",
    "model_domain_and_error_structure",
    "run_population_split_and_shared_errors",
    "absolute_covariance_local_linearization",
)


class CalibrationObservation(Contract):
    id: Name
    run_id: Name | None
    population_id: Name | None
    role: Literal["fit", "validation", "excluded"]
    x: float | None
    y: float | None
    valid: bool | None
    evidence_id: Name
    observation_time_s: Nonnegative | None = None


class CalibrationRequest(Contract):
    asset_id: Name
    clock_id: Name
    event_id: Name
    x_basis: MeasurementBasis
    y_basis: MeasurementBasis
    observations: tuple[CalibrationObservation, ...] = Field(min_length=2, max_length=32)
    covariance: StandardCovariance | None
    parameters: tuple[FitParameter, ...] = Field(min_length=1, max_length=12)
    x_domain: tuple[float, float]
    model_id: Name
    model_version: Name
    provider_source_sha256: str | None = Field(default=None, pattern=SHA)
    configuration_sha256: str | None = Field(default=None, pattern=SHA)
    exact_x_ids: tuple[Name, ...] = ()
    exact_x_evidence_id: Name | None = None
    split_policy: Literal["separate_runs", "separate_populations", "fit_only"] = "separate_runs"
    require_independent_validation: bool = True
    evidence_ids: tuple[Name, ...]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    max_nfev: int = Field(default=400, ge=1, le=5000)
    max_model_calls: int = Field(default=20000, ge=1, le=200000)
    tolerance: Positive = Field(default=1e-11, ge=1e-14, le=1e-4)
    relative_rank_tolerance: Positive = Field(default=1e-10, ge=1e-14, le=1e-4)

    @model_validator(mode="after")
    def align(self):
        ids = tuple(p.id for p in self.observations)
        if len(set(ids)) != len(ids) or len({p.id for p in self.parameters}) != len(self.parameters):
            raise ValueError("observation and parameter identifiers must be unique")
        if not self.x_domain[0] < self.x_domain[1] or not np.isfinite(np.subtract(*self.x_domain)):
            raise ValueError("the physical model input domain must have finite positive width")
        if len(set(self.exact_x_ids)) != len(self.exact_x_ids) or set(self.exact_x_ids) - set(ids):
            raise ValueError("exact input declarations identify unique existing observations")
        if self.covariance and self.covariance.input_ids != covariance_order(ids):
            raise ValueError("joint covariance must use all x identifiers followed by all y identifiers")
        return self


def covariance_order(ids):
    return tuple("x:" + v for v in ids) + tuple("y:" + v for v in ids)


@dataclass(frozen=True)
class CalibrationProvider:
    """A deterministic top-level callback receiving x, physical parameters and config."""

    model_id: str
    version: str
    x_unit: str
    y_unit: str
    parameter_units: dict[str, str]
    predict: Callable
    configuration: dict = field(default_factory=dict)
    derivatives: Callable | None = None


def _affine(x, parameters, configuration):
    return parameters[configuration["offset"]] + parameters[configuration["slope"]] * x


def _affine_derivatives(x, parameters, configuration):
    return {
        configuration["offset"]: np.ones_like(x),
        configuration["slope"]: np.asarray(x),
    }, np.full_like(x, parameters[configuration["slope"]])


def affine_provider(*, x_unit, y_unit, slope_unit, offset="offset", slope="slope"):
    if offset == slope:
        raise ValueError("affine offset and slope identifiers must differ")
    units = _registry()
    coefficient = units.Quantity(1.0, slope_unit) * units.Quantity(1.0, x_unit)
    try:
        compatible = coefficient.to(y_unit).magnitude == 1.0
    except Exception as exc:
        raise ValueError("affine slope dimensionality does not map input to output") from exc
    if not compatible:
        raise ValueError("affine slope units must convert input units to output units without scaling")
    return CalibrationProvider(
        "affine",
        "1",
        x_unit,
        y_unit,
        {offset: y_unit, slope: slope_unit},
        _affine,
        {"offset": offset, "slope": slope},
        _affine_derivatives,
    )


def model_identity(provider):
    def code_identity(code):
        def constant(value):
            if isinstance(value, types.CodeType):
                return code_identity(value)
            if isinstance(value, tuple):
                return [constant(v) for v in value]
            if isinstance(value, frozenset):
                return sorted((constant(v) for v in value), key=repr)
            return repr(value)

        return {
            "bytecode": code.co_code.hex(),
            "constants": [constant(v) for v in code.co_consts],
            "names": code.co_names,
            "free_variables": code.co_freevars,
            "argument_count": code.co_argcount,
            "keyword_only_argument_count": code.co_kwonlyargcount,
        }

    entries = []
    for callback in (provider.predict, provider.derivatives):
        if callback is None:
            entries.append(None)
            continue
        if not inspect.isfunction(callback) or callback.__closure__:
            raise ValueError(
                "provider callbacks must be top-level source-backed functions; pass state as config"
            )
        path = inspect.getsourcefile(callback)
        if path is None:
            raise ValueError("provider callback source file is required")
        entries.append(
            {
                "qualified_name": callback.__qualname__,
                "source_file_sha256": file_digest(Path(path)),
                "code_sha256": digest(code_identity(callback.__code__)),
            }
        )
    configuration = {
        "model_id": provider.model_id,
        "version": provider.version,
        "x_unit": provider.x_unit,
        "y_unit": provider.y_unit,
        "parameter_units": provider.parameter_units,
        "configuration": provider.configuration,
    }
    return {"provider_source_sha256": digest(entries), "configuration_sha256": digest(configuration)}


def boundary_metadata(request):
    return {
        "event_id": request.event_id,
        "model_id": request.model_id,
        "x_basis_sha256": digest(request.x_basis),
        "y_basis_sha256": digest(request.y_basis),
        "observation_order": [r.id for r in request.observations],
        "split_policy": request.split_policy,
        "x_domain": list(request.x_domain),
        "observation_times_meaning": "optional provenance only; no inferred sampling clock",
    }


def _policy(request, context, provider):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("calibration asset/clock differs from RunContext")
    ids = [v.id for v in context.evidence]
    selected = request.evidence_ids
    if len(set(ids)) != len(ids) or len(set(selected)) != len(selected) or set(selected) - set(ids):
        raise ValueError("selected evidence must be unique members of RunContext")
    needed = {
        request.x_basis.evidence_id,
        request.y_basis.evidence_id,
        *(r.evidence_id for r in request.observations),
        *request.review_evidence_ids.values(),
    }
    for basis in (request.x_basis, request.y_basis):
        if basis.reference_state:
            needed.add(basis.reference_state.evidence_id)
    if request.covariance:
        needed.add(request.covariance.evidence_id)
    if request.exact_x_evidence_id:
        needed.add(request.exact_x_evidence_id)
    if needed - set(selected):
        raise ValueError("every measurement/model/review evidence reference must be selected")
    if context.metadata.get("calibration_boundary") != boundary_metadata(request):
        raise ValueError("RunContext does not bind calibration model/bases/domain/order/split")
    missing = [
        "review and evidence required: " + key
        for key in REVIEWS
        if request.applicability_reviews.get(key) is not True or key not in request.review_evidence_ids
    ]
    active = [r for r in request.observations if r.role != "excluded"]
    if any(v is None for r in active for v in (r.x, r.y, r.valid, r.run_id, r.population_id)):
        missing.append("active values, validity, actual run and population identifiers are required")
    if request.covariance is None or any(v is None for row in request.covariance.values for v in row):
        missing.append("complete standard joint covariance is required; missing correlations are not zero")
    elif not request.covariance.definition_reviewed:
        missing.append("standard covariance definition must be reviewed")
    if request.exact_x_ids and request.exact_x_evidence_id is None:
        missing.append("exact abscissae require explicit evidence")
    if not request.provider_source_sha256 or not request.configuration_sha256 or provider is None:
        missing.append("source/configuration-pinned model provider is required")
    if missing:
        return missing
    if model_identity(provider) != {
        "provider_source_sha256": request.provider_source_sha256,
        "configuration_sha256": request.configuration_sha256,
    }:
        raise ValueError("model provider source or configuration differs from requested pins")
    if (provider.model_id, provider.version, provider.x_unit, provider.y_unit, provider.parameter_units) != (
        request.model_id,
        request.model_version,
        request.x_basis.unit,
        request.y_basis.unit,
        {p.id: p.unit for p in request.parameters},
    ):
        raise ValueError("model identifiers, input/output units or parameter units differ")
    for parameter in request.parameters:
        if _registry().Quantity(1.0, parameter.unit).to_base_units().magnitude != 1.0:
            raise ValueError("physical parameter units must be coherent SI")
    return []


def _scope(request):
    rows = request.observations
    fit = [i for i, r in enumerate(rows) if r.role == "fit"]
    validation = [i for i, r in enumerate(rows) if r.role == "validation"]
    active = fit + validation
    if any(rows[i].valid is not True for i in active):
        return "invalid active observations cannot be silently removed"
    if len(fit) <= len(request.parameters):
        return "fit needs more independent point pairs than physical parameters"
    if any(not request.x_domain[0] <= rows[i].x <= request.x_domain[1] for i in active):
        return "observed model input lies outside its declared physical domain"
    if request.split_policy == "fit_only":
        if validation or request.require_independent_validation:
            return "fit-only mode cannot provide or claim independent validation"
    elif validation:
        key = "run_id" if request.split_policy == "separate_runs" else "population_id"
        if {getattr(rows[i], key) for i in fit} & {getattr(rows[i], key) for i in validation}:
            return "fit and validation share the split unit " + key
    elif request.require_independent_validation:
        return "a physically separate validation cohort is required"
    n = len(rows)
    a = fit + [n + i for i in fit]
    b = validation + [n + i for i in validation]
    shared = any(request.covariance.values[i][j] != 0 for i in a for j in b)
    if shared and request.require_independent_validation:
        return "fit and validation measurement errors are correlated; independent validation is unproven"
    exact = set(request.exact_x_ids)
    for i, r in enumerate(rows):
        variance = request.covariance.values[i][i]
        if (r.id in exact) != (variance == 0):
            return "zero x uncertainty and explicit exact-x declarations must coincide"
    return None


def _backend():
    from scipy.linalg import solve_triangular

    dist = distribution("scipy")
    entries = {}
    for entry in sorted(dist.files, key=str):
        name = str(entry).replace("\\", "/")
        if (
            name.startswith(("scipy/optimize/_lsq/", "scipy/linalg/", "scipy.libs/"))
            or name
            in {
                "scipy/optimize/_numdiff.py",
                "scipy/optimize/_differentiable_functions.py",
                "scipy/optimize/_constraints.py",
                "scipy/optimize/_optimize.py",
            }
        ) and name.endswith((".py", ".pyd", ".so", ".dll")):
            entries[name] = file_digest(Path(dist.locate_file(entry)))
    numpy_dist = distribution("numpy")
    numpy_binaries = {
        str(v).replace("\\", "/"): file_digest(Path(numpy_dist.locate_file(v)))
        for v in numpy_dist.files
        if str(v).endswith((".pyd", ".so", ".dll"))
    }
    pin = {
        "scipy_version": version("scipy"),
        "numpy_version": version("numpy"),
        "python_version": platform.python_version(),
        "runtime_platform": platform.platform(),
        "scipy_selected_source_and_binary_sha256": digest(entries),
        "scipy_selected_files": len(entries),
        "shared_inverse_sha256": file_digest(Path(inverse.__file__)),
        "shared_contracts_sha256": file_digest(Path(contracts.__file__)),
        "shared_metrology_sha256": file_digest(Path(metrology.__file__)),
        "numpy_linalg_module_sha256": file_digest(Path(np.linalg.__file__)),
        "numpy_linalg_binary_sha256": file_digest(Path(np.linalg._umath_linalg.__file__)),
        "numpy_binary_tree_sha256": digest(numpy_binaries),
        "numpy_binary_files": len(numpy_binaries),
    }
    return solve_triangular, pin


def _native(request, provider, body):
    solve_triangular, body["backend"] = _backend()
    rows = request.observations
    fi = [i for i, r in enumerate(rows) if r.role == "fit"]
    vi = [i for i, r in enumerate(rows) if r.role == "validation"]
    n, p, total = len(fi), len(request.parameters), len(rows)
    # Check the complete active measurement law, including heldout cross-correlation.
    active = fi + vi
    full_order = active + [total + i for i in active]
    stochastic = [i for i in full_order if not (i < total and rows[i].id in request.exact_x_ids)]
    full_covariance = np.array(request.covariance.values)[np.ix_(stochastic, stochastic)]
    full_sigma = np.sqrt(np.diag(full_covariance))
    if np.any(full_sigma <= 0):
        raise ValueError("exact response errors or zero stochastic variances are unsupported")
    try:
        full_correlation = full_covariance / np.outer(full_sigma, full_sigma)
        eigenvalues = np.linalg.eigvalsh(full_correlation)
        if eigenvalues[0] <= len(eigenvalues) * np.finfo(float).eps * eigenvalues[-1]:
            raise ValueError("complete stochastic covariance is numerically singular; no repair is applied")
        np.linalg.cholesky(full_correlation)
    except np.linalg.LinAlgError as exc:
        raise ValueError("complete active covariance must be positive definite without repair") from exc
    x = np.array([rows[i].x for i in fi])
    y = np.array([rows[i].y for i in fi])
    exact = np.array([rows[i].id in request.exact_x_ids for i in fi])
    unknown = np.flatnonzero(~exact)
    order = fi + [total + i for i in fi]
    cov = np.array(request.covariance.values)[np.ix_(order, order)]
    selected = np.r_[unknown, np.arange(n, 2 * n)]
    cov = cov[np.ix_(selected, selected)]
    if np.any(np.diag(cov) <= 0):
        raise ValueError(
            "positive stochastic variances are required; exact y or general singular errors are unsupported"
        )
    sigma = np.sqrt(np.diag(cov))
    correlation = cov / np.outer(sigma, sigma)
    if not np.all(np.isfinite(correlation)):
        raise ValueError("joint covariance scaling is not numerically representable")
    try:
        lower = np.linalg.cholesky(correlation)
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "stochastic covariance must be positive definite; no jitter, diagonalization or pseudoinverse"
        ) from exc
    initial = np.array([v.initial for v in request.parameters])
    beta0 = np.array([v.lower for v in request.parameters])
    widths = np.array([v.upper - v.lower for v in request.parameters])
    xmin, xmax = request.x_domain
    dx = xmax - xmin
    start = np.r_[(initial - beta0) / widths, (x[unknown] - xmin) / dx]
    calls = 0

    def unpack(z):
        parameters = dict(zip((v.id for v in request.parameters), beta0 + widths * z[:p], strict=True))
        latent = x.copy()
        latent[unknown] = xmin + dx * z[p:]
        return parameters, latent

    def model(xx, parameters):
        nonlocal calls
        calls += 1
        body["model_calls"] = calls
        if calls > request.max_model_calls:
            raise RuntimeError("model evaluation budget exceeded")
        values = np.asarray(
            provider.predict(xx.copy(), parameters.copy(), json_value(provider.configuration)), dtype=float
        )
        if values.shape != xx.shape or not np.all(np.isfinite(values)):
            raise ValueError("provider must return one finite prediction per physical x")
        return values

    def whiten(values):
        return solve_triangular(lower, values / sigma, lower=True, check_finite=True)

    def fun(z):
        parameters, latent = unpack(z)
        return whiten(np.r_[latent - x, model(latent, parameters) - y][selected])

    def jac(z):
        parameters, latent = unpack(z)
        beta_derivatives, x_derivative = provider.derivatives(
            latent.copy(), parameters.copy(), json_value(provider.configuration)
        )
        raw = np.zeros((2 * n, p + len(unknown)))
        for j, parameter in enumerate(request.parameters):
            values = np.asarray(beta_derivatives[parameter.id], dtype=float)
            if values.shape != latent.shape or not np.all(np.isfinite(values)):
                raise ValueError("parameter derivative has incorrect dimensions or nonfinite values")
            raw[n:, j] = values * widths[j]
        x_derivative = np.asarray(x_derivative, dtype=float)
        if x_derivative.shape != latent.shape or not np.all(np.isfinite(x_derivative)):
            raise ValueError("input derivative has incorrect dimensions or nonfinite values")
        for j, index in enumerate(unknown):
            raw[index, p + j] = dx
            raw[n + index, p + j] = x_derivative[index] * dx
        return solve_triangular(lower, raw[selected] / sigma[:, None], lower=True)

    options = {
        "method": "trf",
        "loss": "linear",
        "jac": "3-point" if provider.derivatives is None else "provider_derivatives",
        "xtol": request.tolerance,
        "ftol": request.tolerance,
        "gtol": request.tolerance,
        "max_nfev": request.max_nfev,
        "x_scale": "jac",
    }
    body["native_called"] = True
    body["native_calls"].append(
        {
            "call": "inverse.solve_bounded_least_squares",
            "initial_normalized": start.tolist(),
            "bounds": [0.0, 1.0],
            "options": options,
            "fit_ids": [rows[i].id for i in fi],
            "fit_covariance": cov.tolist(),
            "stochastic_coordinate_indices": selected.tolist(),
            "latent_x_ids": [rows[fi[i]].id for i in unknown],
        }
    )
    result = inverse.solve_bounded_least_squares(
        fun,
        start,
        bounds=(0.0, 1.0),
        **{**options, "jac": "3-point" if provider.derivatives is None else jac},
    )
    parameters, latent = unpack(result.x)
    predicted = model(latent, parameters)
    raw_result = {
        key: getattr(result, key).tolist()
        if isinstance(getattr(result, key), np.ndarray)
        else getattr(result, key)
        for key in (
            "x",
            "fun",
            "jac",
            "grad",
            "cost",
            "optimality",
            "active_mask",
            "nfev",
            "njev",
            "status",
            "message",
            "success",
        )
    }
    body["native_calls"][-1]["result"] = raw_result
    jacobian = np.asarray(result.jac)
    derivative_error = None
    if provider.derivatives is not None:
        # Compare supplied analytical derivatives with model evaluations, not a second fit.
        numerical = np.empty_like(jacobian)
        for j in range(len(start)):
            h = np.cbrt(np.finfo(float).eps) * max(1.0, abs(result.x[j]))
            h = min(h, 0.25 * min(result.x[j], 1 - result.x[j]))
            if h <= np.finfo(float).eps:
                raise ValueError("a model derivative cannot be qualified at an active bound")
            left, right = result.x.copy(), result.x.copy()
            left[j] -= h
            right[j] += h
            numerical[:, j] = (fun(right) - fun(left)) / (2 * h)
        denominator = np.maximum(1.0, np.maximum(abs(numerical), abs(jacobian)))
        derivative_error = float(np.max(abs(numerical - jacobian) / denominator))
        if derivative_error > 1e-5:
            raise ValueError("provider derivatives disagree with independent central model differences")
    _, singular, vh = np.linalg.svd(jacobian, full_matrices=False)
    rank = int(np.sum(singular > singular[0] * request.relative_rank_tolerance)) if len(singular) else 0
    full_rank = rank == len(start)
    active_bounds = bool(np.any(result.active_mask)) or bool(
        np.any(np.minimum(result.x, 1 - result.x) < 1e-8)
    )
    body["audits"] = {
        "joint_jacobian_rank": rank,
        "unknown_count": len(start),
        "singular_values": singular.tolist(),
        "full_rank": full_rank,
        "active_bounds": active_bounds,
        "native_success": bool(result.success),
        "analytical_derivative_relative_error": derivative_error,
        "degrees_of_freedom": len(selected) - len(start),
        "covariance_residual_scaled": False,
        "fit_validation_shared_covariance": any(
            request.covariance.values[a][b] != 0 for a in order for b in vi + [total + i for i in vi]
        ),
    }
    if not result.success or not full_rank or active_bounds:
        body.update(
            status="out_of_scope",
            notes=[
                "native convergence, full local rank and inactive bounds are required for calibration covariance"
            ],
        )
        return
    normalized_covariance = (vh.T / singular**2) @ vh
    parameter_covariance = normalized_covariance[:p, :p] * widths[:, None] * widths[None, :]
    # Mirror the same computed entry; an asymmetric rounded covariance is not a valid contract.
    parameter_covariance = np.triu(parameter_covariance) + np.triu(parameter_covariance, 1).T
    if not np.all(np.isfinite(parameter_covariance)) or np.any(np.diag(parameter_covariance) <= 0):
        raise ValueError("physical parameter covariance is not representable with positive variances")
    validation = None
    if vi:
        vx = np.array([rows[i].x for i in vi])
        values = model(vx, parameters)
        validation = {
            "ids": [rows[i].id for i in vi],
            "prediction_at_observed_x": values.tolist(),
            "residual_y": (values - np.array([rows[i].y for i in vi])).tolist(),
            "meaning": "held-out descriptive residuals at uncertain observed x; no refit, no independent coverage claim",
            "statistically_independent_errors": not body["audits"]["fit_validation_shared_covariance"],
        }
    body["values"] = {
        "parameters": parameters,
        "parameter_order": [v.id for v in request.parameters],
        "parameter_units": provider.parameter_units,
        "parameter_standard_covariance": parameter_covariance.tolist(),
        "parameter_standard_uncertainty": np.sqrt(np.diag(parameter_covariance)).tolist(),
        "latent_x": latent.tolist(),
        "delta_x": (latent - x).tolist(),
        "epsilon_y": (predicted - y).tolist(),
        "fit_ids": [rows[i].id for i in fi],
        "weighted_sum_squares": float(2 * result.cost),
        "application_domain": [float(min(x)), float(max(x))],
        "validation": validation,
        "covariance_meaning": "absolute supplied measurement covariance; local joint-Jacobian nuisance-marginal approximation; no residual scaling or probability law",
    }
    body.update(status="computed", notes=["fit-only; independent validation not supplied"] if not vi else [])


def fit_calibration(
    request: CalibrationRequest, context: RunContext, provider: CalibrationProvider | None = None
):
    request = CalibrationRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": SCHEMA,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "values": None,
        "audits": None,
        "backend": None,
        "native_called": False,
        "model_calls": 0,
        "native_calls": [],
        "adapter_sha256": file_digest(Path(__file__)),
        "qualification": "research calibration; no custody certification, automatic confidence coverage or independent field qualification",
    }
    body["input_digest"] = digest({"request": request, "context": context})
    missing = _policy(request, context, provider)
    if missing:
        body["notes"] = missing
    elif outside := _scope(request):
        body.update(status="out_of_scope", notes=[outside])
    else:
        try:
            _native(request, provider, body)
        except (
            ValueError,
            RuntimeError,
            ArithmeticError,
            np.linalg.LinAlgError,
            TypeError,
            KeyError,
            ImportError,
        ) as exc:
            body.update(status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"])
    body = json_value(body)
    body["record_digest"] = digest(body)
    return body


def verify_calibration_record(record, provider=None, *, request=None, context=None):
    """Replay current pinned mathematics; unsigned receipts cannot prove past execution."""
    try:
        body = {k: v for k, v in record.items() if k != "record_digest"}
        if record["schema"] != SCHEMA or digest(body) != record["record_digest"]:
            raise ValueError("calibration record changed")
        q = CalibrationRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        ctx = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and q != request) or (context is not None and ctx != context):
            raise ValueError("expected request/context differs")
        if fit_calibration(q, ctx, provider) != record:
            raise ValueError(
                "native objective, covariance, model source, heldout split or result differs on replay"
            )
        return {
            "valid": True,
            "meaning": "unsigned current-source numerical replay, not approval or historical execution",
        }
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError) as exc:
        return {"valid": False, "reason": str(exc)}


def predict_calibration(record, x_values, context: RunContext, provider: CalibrationProvider):
    if (
        record.get("status") != "computed"
        or not verify_calibration_record(record, provider, context=context)["valid"]
    ):
        raise ValueError("matching verified computed calibration required")
    x = np.asarray(x_values, dtype=float)
    low, high = record["values"]["application_domain"]
    if x.ndim != 1 or len(x) == 0 or not np.all(np.isfinite(x)) or np.any((x < low) | (x > high)):
        raise ValueError("finite inputs inside the observed fitted domain required; extrapolation is refused")
    y = np.asarray(
        provider.predict(x.copy(), record["values"]["parameters"].copy(), json_value(provider.configuration)),
        dtype=float,
    )
    if y.shape != x.shape or not np.all(np.isfinite(y)):
        raise ValueError("provider application returned invalid values")
    return {
        "x": x.tolist(),
        "y": y.tolist(),
        "x_unit": provider.x_unit,
        "y_unit": provider.y_unit,
        "calibration_digest": record["record_digest"],
        "uncertainty": None,
        "meaning": "forward model at supplied x; application input uncertainty is not silently assumed zero",
    }
