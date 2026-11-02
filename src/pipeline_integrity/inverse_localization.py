"""Finite leak-location comparison; physical predictions belong to their original engine."""

from __future__ import annotations

import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest
from .inverse import Prediction
from .metrology import MeasurementBasis

SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "candidate_geometry_and_fixed_nuisance_parameters",
    "clock_synchronization_and_bias_corrections",
    "joint_residual_noise_includes_model_discrepancy",
    "forward_model_applicability_and_units",
)


class LocalizationRow(Contract):
    id: Name
    channel_id: Name
    sensor_id: Name
    physical_port_id: Name
    time_s: Nonnegative
    basis: MeasurementBasis
    value: float | None
    valid: bool | None


class LocationCandidate(Contract):
    id: Name
    location_id: Name
    coordinate_frame_id: Name
    position_m: tuple[float, float, float] | None
    physical_port_id: Name
    parameters: dict[Name, float | None]
    evidence_id: Name


class LocalizationRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    rows: tuple[LocalizationRow, ...] = Field(min_length=1, max_length=512)
    candidates: tuple[LocationCandidate, ...] = Field(min_length=2, max_length=256)
    parameter_bases: dict[Name, MeasurementBasis]
    covariance: tuple[tuple[float | None, ...], ...] | None
    covariance_row_ids: tuple[Name, ...]
    noise_law: Literal["common_candidate_independent_gaussian"] | None = None
    provider_id: Name
    provider_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    provider_configuration_sha256: str = Field(pattern=SHA)
    observation_source_sha256: str = Field(pattern=SHA)
    evidence_id: Name
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    indistinguishable_distance_squared: Nonnegative = 1e-8

    @model_validator(mode="after")
    def alignment(self):
        ids = tuple(r.id for r in self.rows)
        if len(set(ids)) != len(ids) or self.covariance_row_ids != ids:
            raise ValueError("covariance order must exactly match distinct observation row IDs")
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError("candidate IDs must be distinct")
        if any(set(c.parameters) != set(self.parameter_bases) for c in self.candidates):
            raise ValueError("every candidate supplies every declared fixed parameter, including unknowns")
        keys = [(r.channel_id, r.time_s) for r in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("channel and report-time pairs must be distinct")
        for channel in {r.channel_id for r in self.rows}:
            rr = [r for r in self.rows if r.channel_id == channel]
            if len({digest((r.sensor_id, r.physical_port_id, r.basis)) for r in rr}) != 1:
                raise ValueError("a channel must retain its sensor, physical port and exact basis")
        n = len(ids)
        if self.covariance is not None and (
            len(self.covariance) != n or any(len(row) != n for row in self.covariance)
        ):
            raise ValueError("joint residual covariance dimensions differ from the row order")
        return self


class LocalizationProvider(Protocol):
    """Bind a native wrapper identity; call the original engine without surrogate physics."""

    id: str
    version: str
    source_sha256: str
    configuration_sha256: str

    def __call__(
        self, candidate: LocationCandidate, time_s: tuple[float, ...], context: RunContext
    ) -> Prediction: ...


def boundary_metadata(q):
    return {
        "request_sha256": digest(q),
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "event_id": q.event_id,
        "scope": "conditional finite fixed-candidate relative fit; no leak-existence probability",
    }


def prediction_binding(q, candidate, context, prediction):
    """Provider uses this after native calculation to seal exact inputs and returned values."""
    return digest(
        {
            "request": digest(q),
            "candidate": candidate,
            "context": context,
            "prediction": prediction.model_dump(mode="python", exclude={"evaluation_digest"}),
        }
    )


def _policy(q, c):
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "inverse_localization_boundary"
    ) != boundary_metadata(q):
        raise ValueError("context must bind the exact localization request, asset and clock")
    evidence = [e.id for e in c.evidence]
    required = {q.evidence_id, *q.review_evidence_ids.values()}
    bases = [r.basis for r in q.rows] + list(q.parameter_bases.values())
    required.update(b.evidence_id for b in bases)
    required.update(b.reference_state.evidence_id for b in bases if b.reference_state)
    required.update(k.evidence_id for k in q.candidates)
    if len(set(evidence)) != len(evidence) or required - set(evidence):
        raise ValueError("all distinct model, measurement and review evidence must be selected")
    missing = [k for k in REVIEWS if q.reviews.get(k) is not True or k not in q.review_evidence_ids]
    if any(r.value is None or r.valid is not True for r in q.rows):
        missing.append("all declared rows require known valid observations; no row deletion")
    if any(r.value is not None and r.basis.pressure_reference == "absolute" and r.value <= 0 for r in q.rows):
        raise ValueError("absolute pressure observations must be positive")
    if q.covariance is None or any(v is None for row in q.covariance for v in row):
        missing.append("complete joint residual covariance")
    if q.noise_law is None:
        missing.append("reviewed candidate-independent Gaussian residual law")
    if any(k.position_m is None or any(v is None for v in k.parameters.values()) for k in q.candidates):
        missing.append("known geometry and fixed nuisance/source parameters for every candidate")
    return missing


def localize_leak(request: LocalizationRequest, context: RunContext, provider=None):
    """Compare a finite fixed hypothesis set, conditional on every named physical review."""
    q = LocalizationRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    missing = _policy(q, c)
    result = {
        "schema": "inverse_localization.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "status": "needs_data",
        "missing": missing,
        "candidate_results": [],
        "prediction_records": [],
        "pairwise_distances": [],
        "best_candidate_ids": [],
        "best_indistinguishable_candidate_ids": [],
        "best_is_separated_from_every_alternative": None,
        "selection_meaning": "nominal minimum loss among finite fixed hypotheses; not a confirmed location",
        "runtime": None,
        "module_sha256": file_digest(Path(__file__)),
        "dependency_source_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py"))
            for name in ("contracts", "inverse", "metrology", "units", "datasets")
        },
        "leak_existence_probability": None,
        "location_posterior": None,
        "field_qualified": False,
        "work_authorized": False,
    }

    def seal():
        json_value(result)
        return dict(result, record_sha256=digest(result))

    if missing:
        return seal()
    if provider is None:
        result["missing"].append("explicit original-engine prediction provider")
        return seal()
    identity = (provider.id, provider.version, provider.source_sha256, provider.configuration_sha256)
    if identity != (
        q.provider_id,
        q.provider_version,
        q.provider_source_sha256,
        q.provider_configuration_sha256,
    ):
        raise ValueError("native provider identity/source/configuration differs from request")
    import numpy as np
    import scipy.linalg as la
    import scipy.linalg._flapack as lapack

    result["runtime"] = {
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "scipy": version("scipy"),
        "pydantic": version("pydantic"),
        "pydantic_core": version("pydantic-core"),
        "lapack_binary_sha256": file_digest(Path(lapack.__file__)),
    }
    covariance = np.asarray(q.covariance, dtype=float)
    if not np.array_equal(covariance, covariance.T):
        raise ValueError("joint residual covariance must be exactly symmetric; no silent repair")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            scale = np.sqrt(np.diag(covariance))
            if not np.isfinite(scale).all() or np.any(scale <= 0):
                raise ValueError("residual variances must be finite positive")
            correlation = covariance / scale[:, None] / scale[None, :]
            factor = la.cholesky(correlation, lower=True, check_finite=True)
    except (np.linalg.LinAlgError, FloatingPointError) as exc:
        raise ValueError("joint residual covariance must be positive definite without repair") from exc
    times = tuple(sorted({r.time_s for r in q.rows}))
    time_indices = {t: i for i, t in enumerate(times)}
    raw_predictions = {}
    for candidate in q.candidates:
        prediction = provider(candidate, times, c)
        if not isinstance(prediction, Prediction):
            raise TypeError("provider must return the shared inverse.Prediction contract")
        p = Prediction.model_validate(prediction.model_dump(mode="python"))
        if p.provider_source_digest != q.provider_source_sha256 or p.evaluation_digest != prediction_binding(
            q, candidate, c, p
        ):
            raise ValueError("native prediction source/input/output binding changed")
        if (p.asset_id, p.clock_id, p.time_s) != (q.asset_id, q.clock_id, times):
            raise ValueError("native prediction must report exact asset, clock and requested times")
        result["prediction_records"].append(
            {"candidate_id": candidate.id, "prediction": p.model_dump(mode="json")}
        )
        if p.status != "computed":
            result["missing"].append("prediction withheld: " + candidate.id)
            continue
        values = []
        for row in q.rows:
            channel = p.values.get(row.channel_id)
            if p.units.get(row.channel_id) != row.basis.unit or channel is None or len(channel) != len(times):
                raise ValueError("native prediction channel units or report count differ")
            values.append(channel[time_indices[row.time_s]])
            if values[-1] is not None and row.basis.pressure_reference == "absolute" and values[-1] <= 0:
                raise ValueError("absolute pressure predictions must be positive")
        if any(v is None for v in values):
            result["missing"].append("unknown native prediction: " + candidate.id)
            continue
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            predicted = np.asarray(values, dtype=float)
            residual = (np.asarray([r.value for r in q.rows]) - predicted) / scale
            white = la.solve_triangular(factor, residual, lower=True, check_finite=True)
            loss = float(white @ white)
            if not np.isfinite(loss):
                raise ValueError("residual loss is nonfinite; revise numerical scales and reviewed model")
            raw_predictions[candidate.id] = predicted
        result["candidate_results"].append({"candidate_id": candidate.id, "mahalanobis_squared": loss})
    rows = result["candidate_results"]
    if rows:
        best = min(r["mahalanobis_squared"] for r in rows)
        for row in rows:
            row["relative_log_likelihood"] = -0.5 * (row["mahalanobis_squared"] - best)
        # Exact ties remain ties; no rounding creates a unique candidate.
        if not result["missing"]:
            result["best_candidate_ids"] = [
                r["candidate_id"] for r in rows if r["mahalanobis_squared"] == best
            ]
    ids = list(raw_predictions)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                difference = (raw_predictions[left] - raw_predictions[right]) / scale
                delta = la.solve_triangular(factor, difference, lower=True, check_finite=True)
                distance = float(delta @ delta)
            if not np.isfinite(distance):
                raise ValueError("candidate separation is nonfinite; revise numerical scales and model")
            result["pairwise_distances"].append(
                {
                    "left": left,
                    "right": right,
                    "distance_squared": distance,
                    "indistinguishable_by_declared_threshold": distance
                    <= q.indistinguishable_distance_squared,
                }
            )
    if not result["missing"]:
        result["status"] = "computed"
        best_ids = set(result["best_candidate_ids"])
        ambiguous = set(best_ids)
        for pair in result["pairwise_distances"]:
            if pair["indistinguishable_by_declared_threshold"] and (
                pair["left"] in best_ids or pair["right"] in best_ids
            ):
                ambiguous.update((pair["left"], pair["right"]))
        result["best_indistinguishable_candidate_ids"] = [k.id for k in q.candidates if k.id in ambiguous]
        result["best_is_separated_from_every_alternative"] = len(ambiguous) == 1
    return seal()


def verify_inverse_localization_record(record, provider=None, *, request=None, context=None):
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if digest(body) != record.get("record_sha256"):
        raise ValueError("localization record seal changed")
    import json

    q = LocalizationRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request) != digest(q):
        raise ValueError("caller request differs from record")
    if context is not None and digest(context) != digest(c):
        raise ValueError("caller context differs from record")
    if localize_leak(q, c, provider) != record:
        raise ValueError("localization native replay/source/runtime differs from record")
    return True
