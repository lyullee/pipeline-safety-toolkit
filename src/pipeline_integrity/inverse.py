"""Bounded parameter estimation delegates all physical predictions to an explicit provider."""

import math
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .units import _registry


class FitParameter(Contract):
    id: Name
    unit: Name
    lower: float
    upper: float
    initial: float

    @model_validator(mode="after")
    def bounds(self):
        if (
            not self.lower < self.upper
            or not self.lower <= self.initial <= self.upper
            or not math.isfinite(self.upper - self.lower)
        ):
            raise ValueError("parameter bounds must increase and contain the initial value")
        return self


class ResidualScale(Contract):
    value: Positive
    unit: Name
    source_id: Name


class ObservedChannel(Contract):
    id: Name
    scale: ResidualScale
    values: tuple[float | None, ...]
    valid: tuple[bool, ...]

    @model_validator(mode="after")
    def samples(self):
        if len(self.values) != len(self.valid) or any(
            flag and value is None for flag, value in zip(self.valid, self.values, strict=True)
        ):
            raise ValueError("observation values and validity must align")
        return self


class Observations(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    source_digests: dict[str, str]
    time_s: tuple[Nonnegative, ...] = Field(min_length=2)
    channels: tuple[ObservedChannel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def alignment(self):
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("observation times must strictly increase")
        if len({c.id for c in self.channels}) != len(self.channels) or any(
            len(c.values) != len(self.time_s) for c in self.channels
        ):
            raise ValueError("distinct observation channels must align with the clock")
        if not self.source_digests or any(
            not key or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for key, value in self.source_digests.items()
        ):
            raise ValueError("observations require named SHA-256 source bindings")
        return self


def observations_from_frames(frames, *, observation_id, asset_id, source_digests, scales):
    frames = tuple(frames)
    if not frames or not all(isinstance(f, SignalFrame) for f in frames) or not scales:
        raise ValueError("signal frames and explicit residual scales are required")
    frames = tuple(SignalFrame.model_validate(f.model_dump(mode="python")) for f in frames)
    scales = {
        name: ResidualScale.model_validate(scale.model_dump(mode="python")) for name, scale in scales.items()
    }
    first = frames[0]
    for f in frames:
        if (f.stream_id, f.clock_id, f.origin) != (first.stream_id, first.clock_id, first.origin):
            raise ValueError("observations cannot mix streams, clocks or generation origins")
        if any(name not in f.values or f.units[name] != scale.unit for name, scale in scales.items()):
            raise ValueError("selected observation channels and residual-scale units must match")
    return Observations(
        id=observation_id,
        asset_id=asset_id,
        clock_id=first.clock_id,
        origin=first.origin,
        source_digests=source_digests,
        time_s=tuple(f.time_s for f in frames),
        channels=tuple(
            ObservedChannel(
                id=name,
                scale=scale,
                values=tuple(f.values[name] for f in frames),
                valid=tuple(f.valid[name] for f in frames),
            )
            for name, scale in scales.items()
        ),
    )


class Prediction(Contract):
    status: Literal["computed", "withheld"]
    asset_id: Name
    clock_id: Name
    time_s: tuple[Nonnegative, ...]
    values: dict[str, tuple[float | None, ...]]
    units: dict[str, str]
    provider_source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    diagnostics: dict = Field(default_factory=dict)


class FitRequest(Contract):
    parameters: tuple[FitParameter, ...] = Field(min_length=1)
    provider_id: Name
    provider_version: Name
    fit_end_s: Nonnegative
    validation_start_s: Nonnegative
    validation_end_s: Nonnegative
    loss: Literal["linear", "soft_l1", "huber"] = "linear"
    loss_scale: Positive = 1.0
    difference_step_fraction: float = Field(default=0.005, gt=0, le=0.1)
    identifiability_relative_tolerance: float = Field(default=1e-6, gt=0, lt=1)
    maximum_model_evaluations: int = Field(default=500, ge=1, le=100000)
    maximum_optimizer_evaluations: int = Field(default=50, ge=1, le=10000)
    parameter_tolerance: float = Field(default=1e-5, ge=1e-12, le=0.1)

    @model_validator(mode="after")
    def split(self):
        if not self.fit_end_s < self.validation_start_s <= self.validation_end_s:
            raise ValueError("validation must start strictly after the fitting horizon")
        if len({p.id for p in self.parameters}) != len(self.parameters):
            raise ValueError("fit parameter identifiers must be distinct")
        return self


def _source_hashes(sources):
    if not sources or any(
        not key or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        for key, value in sources.items()
    ):
        raise ValueError("named SHA-256 source bindings are required")


class OperatingPoint(Contract):
    id: Name
    state: dict[Name, float | None] = Field(min_length=1)
    state_units: dict[Name, Name]
    group_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    source_digests: dict[Name, str]

    @model_validator(mode="after")
    def binding(self):
        if set(self.state) != set(self.state_units):
            raise ValueError("each operating-state variable needs its explicit unit")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("operating-point evidence IDs must be unique")
        _source_hashes(self.source_digests)
        return self


class OperatingPointObservations(Contract):
    id: Name
    asset_id: Name
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    source_digests: dict[Name, str]
    points: tuple[OperatingPoint, ...] = Field(min_length=2, max_length=100000)
    channels: tuple[ObservedChannel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def alignment(self):
        _source_hashes(self.source_digests)
        if len({p.id for p in self.points}) != len(self.points):
            raise ValueError("operating-point IDs must be unique")
        if len({c.id for c in self.channels}) != len(self.channels) or any(
            len(c.values) != len(self.points) for c in self.channels
        ):
            raise ValueError("distinct channels must align with explicit point order")
        for point in self.points:
            if any(self.source_digests.get(key) != value for key, value in point.source_digests.items()):
                raise ValueError("point source hashes differ from their enclosing observations")
        return self


class OperatingPointPrediction(Contract):
    status: Literal["computed", "withheld"]
    asset_id: Name
    point_ids: tuple[Name, ...]
    point_digests: dict[Name, str]
    values: dict[str, tuple[float | None, ...]]
    units: dict[str, str]
    provider_source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    diagnostics: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def identities(self):
        if len(set(self.point_ids)) != len(self.point_ids) or set(self.point_ids) != set(self.point_digests):
            raise ValueError("prediction point IDs and state/source digest keys must match uniquely")
        _source_hashes(self.point_digests)
        return self


class ResidualRow(Contract):
    point_id: Name
    channel_id: Name
    unit: Name

    @model_validator(mode="after")
    def physical_unit(self):
        normalized = re.sub(r"\b(m|cm|mm|km)([23])\b", r"\1**\2", self.unit)
        try:
            value = _registry().Quantity(1.0, normalized).to_base_units()
        except Exception as exc:
            raise ValueError("joint residual rows require explicit coherent SI units") from exc
        if value.magnitude != 1.0:
            raise ValueError("convert scaled/offset residual units to coherent SI before supplying a metric")
        return self


class OperatingPointResidualMetric(Contract):
    kind: Literal["covariance", "residual_scale_matrix"]
    rows: tuple[ResidualRow, ...] = Field(min_length=1, max_length=2048)
    matrix: (
        Annotated[
            tuple[Annotated[tuple[float | None, ...], Field(max_length=2048)], ...], Field(max_length=2048)
        ]
        | None
    )
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_id: Name
    definition_id: Name
    applicability_reviewed: bool = False
    maximum_rows: int = Field(default=512, ge=1, le=2048)
    maximum_matrix_cells: int = Field(default=262144, ge=1, le=4194304)

    @model_validator(mode="before")
    @classmethod
    def raw_budget(cls, value):
        if isinstance(value, dict) and isinstance(value.get("rows"), (tuple, list)):
            n = len(value["rows"])
            rows = value.get("maximum_rows", 512)
            cells = value.get("maximum_matrix_cells", 262144)
            if (
                type(rows) is not int
                or type(cells) is not int
                or not 1 <= n <= min(rows, 2048)
                or n * n > min(cells, 4194304)
            ):
                raise ValueError("joint residual raw row/cell budget exceeded before numeric coercion")
            matrix = value.get("matrix")
            if matrix is not None and (
                not isinstance(matrix, (tuple, list))
                or len(matrix) != n
                or any(not isinstance(row, (tuple, list)) or len(row) != n for row in matrix)
            ):
                raise ValueError("joint residual raw matrix shape differs from its declared row order")
            # model_validate_json decodes JSON arrays as lists, while the public
            # contract deliberately requires bounded tuples. Normalize only
            # after the raw dimensions have been checked so strict validation
            # accepts serialized requests without weakening the work budget.
            normalized = dict(value)
            normalized["rows"] = tuple(value["rows"])
            if matrix is not None:
                normalized["matrix"] = tuple(tuple(row) for row in matrix)
            return normalized
        return value

    @model_validator(mode="after")
    def shape(self):
        _metric_budget(self)
        keys = [(r.point_id, r.channel_id) for r in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("joint residual row identities must be unique")
        return self


def _metric_budget(metric):
    if not isinstance(metric, OperatingPointResidualMetric):
        raise ValueError("an actual typed operating-point residual metric is required")
    n = len(metric.rows)
    if type(metric.maximum_rows) is not int or type(metric.maximum_matrix_cells) is not int:
        raise ValueError("joint residual row/cell budgets must be actual integers")
    if not 1 <= n <= min(metric.maximum_rows, 2048) or not n * n <= min(metric.maximum_matrix_cells, 4194304):
        raise ValueError("joint residual row/cell work budget exceeded before numerical work")
    if metric.matrix is not None and (len(metric.matrix) != n or any(len(row) != n for row in metric.matrix)):
        raise ValueError("joint residual matrix must match its complete explicit row order")


class OperatingPointFitRequest(Contract):
    parameters: tuple[FitParameter, ...] = Field(min_length=1)
    provider_id: Name
    provider_version: Name
    training_point_ids: tuple[Name, ...] = Field(min_length=1)
    heldout_point_ids: tuple[Name, ...] = Field(min_length=1)
    residual_metric: OperatingPointResidualMetric | None = None
    loss: Literal["linear", "soft_l1", "huber"] = "linear"
    loss_scale: Positive = 1.0
    difference_step_fraction: float = Field(default=0.005, gt=0, le=0.1)
    identifiability_relative_tolerance: float = Field(default=1e-6, gt=0, lt=1)
    maximum_model_evaluations: int = Field(default=500, ge=1, le=100000)
    maximum_optimizer_evaluations: int = Field(default=50, ge=1, le=10000)
    parameter_tolerance: float = Field(default=1e-5, ge=1e-12, le=0.1)

    @model_validator(mode="after")
    def split(self):
        if self.residual_metric is not None and self.loss != "linear":
            raise ValueError(
                "joint whitening supports linear loss only; componentwise robust loss depends on the whitening coordinates"
            )
        if len({p.id for p in self.parameters}) != len(self.parameters):
            raise ValueError("fit parameter identifiers must be distinct")
        if (
            len(set(self.training_point_ids)) != len(self.training_point_ids)
            or len(set(self.heldout_point_ids)) != len(self.heldout_point_ids)
            or set(self.training_point_ids) & set(self.heldout_point_ids)
        ):
            raise ValueError("explicit training/heldout point IDs must be unique and disjoint")
        return self


class _ModelUnavailable(Exception):
    pass


class _BudgetExhausted(Exception):
    pass


def _scipy_least_squares():
    from scipy.optimize import least_squares

    return least_squares


def solve_bounded_least_squares(fun, x0, *, bounds, **options):
    """Shared native solver; each caller defines its error model, units and evidence."""
    return _scipy_least_squares()(fun, x0, bounds=bounds, **options)


def _point_metric(observations, request, context, train, validation):
    """Build split-local principal-block whitening; cross-split errors are never conditioned away."""
    metric = request.residual_metric
    _metric_budget(metric)
    row_keys = [(r.point_id, r.channel_id) for r in metric.rows]
    channels = {c.id: c for c in observations.channels}
    points = {p.id: i for i, p in enumerate(observations.points)}
    if set(row_keys) != {(p.id, c.id) for p in observations.points for c in observations.channels}:
        raise ValueError("joint metric must identify every declared point/channel row exactly once")
    if any(r.unit != channels[r.channel_id].scale.unit for r in metric.rows):
        raise ValueError("joint metric row units differ from the actual observation channel units")
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence):
        raise ValueError("joint metric context evidence IDs must be unique and unambiguous")
    if metric.evidence_id not in evidence:
        raise ValueError("joint metric evidence is absent from the actual context")
    source = evidence[metric.evidence_id].sha256
    if source is not None and source != metric.source_sha256:
        raise ValueError("joint metric source differs from its selected actual evidence hash")
    selected, permutations = {}, {}
    for label, indices in (("training", train), ("heldout", validation)):
        physical_order = [
            (observations.points[int(i)].id, c.id)
            for c in observations.channels
            for i in indices
            if c.valid[int(i)]
        ]
        lookup = {key: i for i, key in enumerate(physical_order)}
        selected[label] = [j for j, key in enumerate(row_keys) if key in lookup]
        permutations[label] = [lookup[row_keys[j]] for j in selected[label]]
    info = {
        "kind": metric.kind,
        "definition_id": metric.definition_id,
        "source_sha256": metric.source_sha256,
        "evidence_id": metric.evidence_id,
        "metric_digest": digest(metric),
        "row_order": [r.model_dump(mode="json") for r in metric.rows],
        "matrix_entry_units": "row_i.unit * row_j.unit; C is covariance/scaling, not precision",
        "training_rows": [metric.rows[j].model_dump(mode="json") for j in selected["training"]],
        "heldout_rows": [metric.rows[j].model_dump(mode="json") for j in selected["heldout"]],
        "masked_rows": [
            r.model_dump(mode="json")
            for r in metric.rows
            if not channels[r.channel_id].valid[points[r.point_id]]
        ],
        "unused_rows": [
            r.model_dump(mode="json")
            for r in metric.rows
            if r.point_id not in set(request.training_point_ids) | set(request.heldout_point_ids)
        ],
        "scalar_residual_scale_values_applied": False,
        "training_block": "principal submatrix of valid training residual rows only",
        "heldout_block": "marginal principal submatrix only; not conditioned on training residuals",
        "cross_split_independence_assumed": False,
        "distribution_assumed": None,
        "full_matrix_spd_confirmed": None,
        "selected_principal_blocks_spd_confirmed": False,
        "cross_split_block_known": False,
        "cross_split_block_all_zero": None,
        "native": None,
    }
    missing = []
    if not metric.applicability_reviewed:
        missing.append("joint residual applicability review")
    if source is None:
        missing.append("joint metric evidence source hash")
    matrix = metric.matrix
    if matrix is None:
        missing.append("joint residual matrix")
    else:
        for i in range(len(matrix)):
            for j in range(i, len(matrix)):
                if matrix[i][j] is not None and matrix[j][i] is not None and matrix[i][j] != matrix[j][i]:
                    raise ValueError("joint residual matrix must be exactly symmetric; no repair")
        cross = [matrix[i][j] for i in selected["training"] for j in selected["heldout"]]
        cross_reverse = [matrix[j][i] for i in selected["training"] for j in selected["heldout"]]
        known = all(v is not None for v in (*cross, *cross_reverse))
        info.update(
            cross_split_block_known=known,
            cross_split_block_all_zero=all(v == 0 for v in cross) if known else None,
        )
        for label, rows in selected.items():
            if any(matrix[i][j] is None for i in rows for j in rows):
                missing.append("known " + label + " principal residual block")
    if missing:
        return None, info, missing
    import scipy.linalg._flapack as lapack
    from scipy.linalg import cholesky, solve_triangular

    _scipy_least_squares()
    info["native"] = {
        "numpy": version("numpy"),
        "scipy": version("scipy"),
        "lapack_binary_sha256": file_digest(Path(lapack.__file__)),
    }

    def factor(indices):
        covariance = np.asarray([[matrix[i][j] for j in indices] for i in indices], dtype=float)
        if not np.isfinite(covariance).all() or not np.array_equal(covariance, covariance.T):
            raise ValueError("finite exactly symmetric residual covariance/scaling block required")
        diagonal = np.diag(covariance)
        if np.any(diagonal <= 0):
            raise ValueError("joint residual variances/scales must be positive")
        scale = np.sqrt(diagonal)
        correlation = np.empty_like(covariance)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            # One algebraically identical triangle avoids division-order asymmetry; no input repair.
            for i in range(len(indices)):
                for j in range(i, len(indices)):
                    small, large = min(scale[i], scale[j]), max(scale[i], scale[j])
                    correlation[i, j] = correlation[j, i] = covariance[i, j] / small / large
        try:
            lower = cholesky(correlation, lower=True, check_finite=True)
        except np.linalg.LinAlgError as exc:
            raise ValueError(
                "joint residual covariance/scaling block must be positive definite without regularization"
            ) from exc
        return scale, lower

    if all(v is not None for row in matrix for v in row):
        factor(list(range(len(matrix))))
        info["full_matrix_spd_confirmed"] = True
    factors = {}
    for label, indices in (("training", train), ("heldout", validation)):
        factors[tuple(int(i) for i in indices)] = (*factor(selected[label]), permutations[label])
    info["selected_principal_blocks_spd_confirmed"] = True

    def whiten(raw, indices):
        scale, lower, order = factors[tuple(int(i) for i in indices)]
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            value = solve_triangular(
                lower, np.asarray(raw, dtype=float)[order] / scale, lower=True, check_finite=True
            )
        if not np.isfinite(value).all():
            raise ValueError("joint-whitened residual cannot be represented as a finite vector")
        return value

    return whiten, info, []


def fit_operating_points(
    observations: OperatingPointObservations, request: OperatingPointFitRequest, context: RunContext, predict
):
    """Fit explicit steady point IDs through the same numerical kernel, without a time axis."""
    if request.residual_metric is not None:
        _metric_budget(request.residual_metric)
        if len(request.residual_metric.rows) != len(observations.points) * len(observations.channels):
            raise ValueError(
                "joint metric must cover every declared observation point/channel before cloning"
            )
    observations = OperatingPointObservations.model_validate(observations.model_dump(mode="python"))
    request = OperatingPointFitRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    if context.asset_id != observations.asset_id:
        raise ValueError("point-fit context and observation asset differ")
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence):
        raise ValueError("point-fit context evidence IDs must be unique")
    for point in observations.points:
        if set(point.evidence_ids) - set(evidence):
            raise ValueError("point state/source evidence is absent from the actual context")
        if not any(evidence[key].sha256 in point.source_digests.values() for key in point.evidence_ids):
            raise ValueError("each point needs actual evidence matching one of its declared source hashes")
    if any(c.scale.source_id not in evidence for c in observations.channels):
        raise ValueError("point residual-scale evidence is absent from the actual context")
    if hasattr(predict, "parameter_units") and predict.parameter_units != {
        p.id: p.unit for p in request.parameters
    }:
        raise ValueError("fit parameter identifiers and units differ from the point provider")
    lookup = {p.id: i for i, p in enumerate(observations.points)}
    if (set(request.training_point_ids) | set(request.heldout_point_ids)) - set(lookup):
        raise ValueError("training/heldout selection contains an unreported operating-point ID")
    train = np.asarray([lookup[key] for key in request.training_point_ids], dtype=int)
    validation = np.asarray([lookup[key] for key in request.heldout_point_ids], dtype=int)
    coordinates = tuple(p.id for p in observations.points)
    point_hashes = {p.id: digest(p) for p in observations.points}
    context_hash = digest(context)

    def make_prediction(parameters, indices):
        points = tuple(observations.points[int(i)] for i in indices)
        ids = tuple(coordinates[int(i)] for i in indices)
        expected = {key: point_hashes[key] for key in ids}
        if any(digest(p) != expected[key] for key, p in zip(ids, points, strict=True)):
            raise ValueError("operating-point state/source changed during fitting")
        result = predict(parameters, points, context)
        if digest(context) != context_hash:
            raise ValueError("point provider mutated the actual execution/provenance context")
        if not isinstance(result, OperatingPointPrediction):
            raise TypeError("point provider must return OperatingPointPrediction")
        result = OperatingPointPrediction.model_validate(result.model_dump(mode="python"))
        digest(result.diagnostics)
        if any(digest(p) != expected[key] for key, p in zip(ids, points, strict=True)):
            raise ValueError("operating-point provider mutated the requested source/state")
        if result.asset_id != context.asset_id or result.point_ids != ids or result.point_digests != expected:
            raise ValueError("prediction point order, source/state digest or asset differs")
        return result

    def trace_axis(indices):
        points = tuple(observations.points[int(i)] for i in indices)
        return {
            "point_ids": [p.id for p in points],
            "point_group_ids": [p.group_id for p in points],
            "point_digests": {p.id: point_hashes[p.id] for p in points},
        }

    training_groups = sorted({observations.points[int(i)].group_id for i in train})
    heldout_groups = sorted({observations.points[int(i)].group_id for i in validation})
    if request.residual_metric is None:
        _scipy_least_squares()
    try:
        scipy_version = version("scipy")
    except PackageNotFoundError:
        scipy_version = None
    body = {
        "schema": "pipeline_integrity.operating_point_fit.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "observation_digest": digest(observations),
        "observation_origin": observations.origin,
        "source_digests": observations.source_digests,
        "operating_points": [p.model_dump(mode="json") for p in observations.points],
        "observation_axis": "explicit operating-point IDs; no measurement clock or time coordinate",
        "context_clock_role": "execution/provenance context only; not the observation axis",
        "training_point_ids": list(request.training_point_ids),
        "heldout_point_ids": list(request.heldout_point_ids),
        "training_groups": training_groups,
        "heldout_groups": heldout_groups,
        "same_group_holdout": bool(set(training_groups) & set(heldout_groups)),
        "fit_rows": len(train),
        "validation_rows": len(validation),
        "unused_rows": len(coordinates) - len(train) - len(validation),
        "field_validation_claim": False,
        "independent_experiment_validation": False,
        "covariance_or_error_independence_inferred": False,
        "parameter_confidence_intervals_calculated": False,
        "residual_scale_meaning": "caller-supplied normalization; not automatically measurement standard deviation",
        "implementation_sha256": file_digest(Path(__file__)),
        "scipy_version": scipy_version,
    }
    result = _fit_axis(
        observations,
        request,
        train,
        validation,
        coordinates,
        make_prediction,
        trace_axis,
        body,
        metric_factory=(lambda: _point_metric(observations, request, context, train, validation))
        if request.residual_metric is not None
        else None,
    )
    if digest(context) != context_hash or any(
        digest(p) != point_hashes[key] for key, p in zip(coordinates, observations.points, strict=True)
    ):
        raise ValueError("point state/source or actual context changed during fitting")
    return result


def fit_parameters(observations: Observations, request: FitRequest, context: RunContext, predict):
    """Fit training rows only; validation is evaluated after parameter selection, with no refit."""
    observations = Observations.model_validate(observations.model_dump(mode="python"))
    request = FitRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    context_hash = digest(context)
    observation_hash = digest(observations)

    if hasattr(predict, "parameter_units") and predict.parameter_units != {
        p.id: p.unit for p in request.parameters
    }:
        raise ValueError("fit parameter identifiers and units differ from the forward provider")

    if (context.asset_id, context.clock_id) != (observations.asset_id, observations.clock_id):
        raise ValueError("fit context must identify the observation asset and clock")
    t = np.array(observations.time_s)
    train = np.flatnonzero(t <= request.fit_end_s)
    validation = np.flatnonzero((t >= request.validation_start_s) & (t <= request.validation_end_s))
    if not len(train) or not len(validation):
        raise ValueError("fitting and held-out validation both require reported observation times")
    coordinates = tuple(float(value) for value in t)
    _scipy_least_squares()

    def make_prediction(parameters, indices):
        times = tuple(coordinates[int(i)] for i in indices)
        result = predict(parameters, times, context)
        if digest(context) != context_hash:
            raise ValueError("time provider mutated the actual execution/provenance context")
        if not isinstance(result, Prediction):
            raise TypeError("the forward provider must return Prediction")
        result = Prediction.model_validate(result.model_dump(mode="python"))
        digest(result.diagnostics)
        if (result.asset_id, result.clock_id, result.time_s) != (
            context.asset_id,
            context.clock_id,
            times,
        ):
            raise ValueError("forward prediction asset, clock or exact report times differ")
        return result

    def trace_axis(indices):
        return {"time_range_s": [coordinates[int(indices[0])], coordinates[int(indices[-1])]]}

    body = {
        "schema": "pipeline_integrity.parameter_fit.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "observation_digest": digest(observations),
        "observation_origin": observations.origin,
        "source_digests": observations.source_digests,
        "fit_rows": len(train),
        "validation_rows": len(validation),
        "unused_rows": len(coordinates) - len(train) - len(validation),
        "field_validation_claim": False,
        "parameter_confidence_intervals_calculated": False,
        "implementation_sha256": file_digest(Path(__file__)),
        "scipy_version": version("scipy"),
    }
    result = _fit_axis(
        observations, request, train, validation, coordinates, make_prediction, trace_axis, body
    )
    if digest(context) != context_hash or digest(observations) != observation_hash:
        raise ValueError("time observations or actual context changed during fitting")
    return result


def _fit_axis(
    observations,
    request,
    train,
    validation,
    coordinates,
    make_prediction,
    trace_axis,
    body,
    *,
    metric_factory=None,
):
    """One bounded least-squares/Jacobian/identifiability kernel for both observation axes."""
    lower = np.array([p.lower for p in request.parameters])
    width = np.array([p.upper - p.lower for p in request.parameters])
    initial = np.array([p.initial for p in request.parameters])
    z0 = (initial - lower) / width
    cache, trace, source = {}, [], None
    for indices in (train, validation):
        if any(not any(c.valid[int(i)] for i in indices) for c in observations.channels):
            raise ValueError("each selected channel needs valid fitting and validation samples")
    count = sum(sum(c.valid[int(i)] for i in train) for c in observations.channels)
    if count < len(request.parameters):
        raise ValueError("there are fewer valid fitting observations than parameters")
    transform = None
    if metric_factory is not None:
        transform, info, missing = metric_factory()
        body["residual_metric"] = info
        if missing:
            body.update(
                status="withheld",
                reason="; ".join(missing),
                fitted_parameters=None,
                provider_source_digest=None,
                model_evaluations=0,
                evaluations=[],
            )
            return {**body, "result_digest": digest(body)}

    def prediction(z, indices):
        nonlocal source
        values = dict(
            zip((p.id for p in request.parameters), (float(v) for v in lower + z * width), strict=True)
        )
        labels = tuple(coordinates[int(i)] for i in indices)
        key = digest((values, labels))
        if key in cache:
            return cache[key]
        if len(trace) >= request.maximum_model_evaluations:
            raise _BudgetExhausted("the declared forward-model evaluation budget was exhausted")
        arguments_hash = digest(values)
        result = make_prediction(values, indices)
        if digest(values) != arguments_hash:
            raise ValueError("forward provider mutated the requested parameter values")
        trace.append(
            {
                "parameters": dict(values),
                **trace_axis(indices),
                "requested_rows": len(labels),
                "status": result.status,
                "evaluation_digest": result.evaluation_digest,
                "diagnostics": result.diagnostics,
            }
        )
        if source is None:
            source = result.provider_source_digest
        if source != result.provider_source_digest:
            raise ValueError("the forward provider source changed during fitting")
        if result.status != "computed":
            raise _ModelUnavailable("the forward provider withheld a requested parameter evaluation")
        for channel in observations.channels:
            row = result.values.get(channel.id)
            if result.units.get(channel.id) != channel.scale.unit or row is None or len(row) != len(labels):
                raise ValueError("prediction channel units or sample counts differ")
            if any(v is None or not math.isfinite(v) for v in row):
                raise _ModelUnavailable("the forward prediction contains missing or invalid selected values")
        cache[key] = result
        return result

    def residual(z):
        result = prediction(z, train)
        values = np.array(
            [
                (result.values[c.id][j] - c.values[int(i)]) / (c.scale.value if transform is None else 1.0)
                for c in observations.channels
                for j, i in enumerate(train)
                if c.valid[int(i)]
            ]
        )
        if not np.isfinite(values).all():
            raise ValueError("scaled residuals overflow; revise the declared units and residual scales")
        return values if transform is None else transform(values, train)

    def jacobian(z):
        columns = []
        for j in range(len(z)):
            left, right = z.copy(), z.copy()
            left[j], right[j] = (
                max(0.0, z[j] - request.difference_step_fraction),
                min(1.0, z[j] + request.difference_step_fraction),
            )
            columns.append((residual(right) - residual(left)) / (right[j] - left[j]))
        return np.column_stack(columns)

    def metrics(z, indices):
        result = prediction(z, indices)
        channels, normalized = {}, []
        for c in observations.channels:
            errors = np.array(
                [result.values[c.id][j] - c.values[int(i)] for j, i in enumerate(indices) if c.valid[int(i)]]
            )
            if not np.isfinite(errors).all():
                raise ValueError("physical residuals overflow; revise the declared units and model")
            if transform is None:
                with np.errstate(over="raise", invalid="raise", divide="raise"):
                    scaled = errors / c.scale.value
                if not np.isfinite(scaled).all():
                    raise ValueError("scaled metrics are not finite")
                normalized.extend(scaled)
            else:
                normalized.extend(errors)
            magnitude = float(np.max(np.abs(errors)))
            relative = errors / magnitude if magnitude else errors
            channels[c.id] = {
                "valid_samples": len(errors),
                "unit": c.scale.unit,
                "rmse": magnitude * float(np.sqrt(np.mean(relative**2))),
                "mae": magnitude * float(np.mean(np.abs(relative))),
                "bias": magnitude * float(np.mean(relative)),
                "residual_scale": c.scale.model_dump(mode="json"),
            }
        normalized = np.asarray(normalized)
        if transform is not None:
            normalized = transform(normalized, indices)
        magnitude = float(np.max(np.abs(normalized)))
        relative = normalized / magnitude if magnitude else normalized
        return {"channels": channels, "normalized_rmse": magnitude * float(np.sqrt(np.mean(relative**2)))}

    try:
        baseline = metrics(z0, train)
        solution = solve_bounded_least_squares(
            residual,
            z0,
            jac=jacobian,
            bounds=(np.zeros(len(z0)), np.ones(len(z0))),
            loss=request.loss,
            f_scale=request.loss_scale,
            max_nfev=request.maximum_optimizer_evaluations,
            xtol=request.parameter_tolerance,
            ftol=request.parameter_tolerance,
            gtol=request.parameter_tolerance,
        )
        singular = np.linalg.svd(jacobian(solution.x), compute_uv=False)
        rank = (
            int(np.sum(singular > (singular[0] * request.identifiability_relative_tolerance)))
            if len(singular) and singular[0] > 0
            else 0
        )
        fitted = metrics(solution.x, train)
        # Held-out observations never appear in the optimizer residual or Jacobian.
        before_validation, after_validation = metrics(z0, validation), metrics(solution.x, validation)
        status = "not_converged" if not solution.success else "unidentifiable" if rank < len(z0) else "fitted"
        body.update(
            status=status,
            fitted_parameters=dict(
                zip(
                    (p.id for p in request.parameters),
                    (float(v) for v in lower + solution.x * width),
                    strict=True,
                )
            ),
            optimizer={
                "success": bool(solution.success),
                "status": int(solution.status),
                "message": str(solution.message),
                "reported_nfev": int(solution.nfev),
                "loss_cost": float(solution.cost),
            },
            local_identifiability={
                "rank": rank,
                "parameter_count": len(z0),
                "singular_values": singular.tolist(),
                "coordinates": "unit_interval_parameters_and_scaled_training_residuals",
                "condition_number": float(singular[0] / singular[-1]) if rank == len(z0) else None,
                "bound_active_parameters": [
                    p.id
                    for p, z in zip(request.parameters, solution.x, strict=True)
                    if min(z, 1 - z) <= request.parameter_tolerance
                ],
            },
            training={"baseline": baseline, "fitted": fitted},
            validation={
                "baseline": before_validation,
                "fitted": after_validation,
                "used_for_parameter_selection": False,
            },
        )
    except (_ModelUnavailable, _BudgetExhausted) as error:
        body.update(
            status="withheld" if isinstance(error, _ModelUnavailable) else "budget_exhausted",
            reason=str(error),
            fitted_parameters=None,
        )
    body.update(provider_source_digest=source, model_evaluations=len(trace), evaluations=trace)
    return {**body, "result_digest": digest(body)}
