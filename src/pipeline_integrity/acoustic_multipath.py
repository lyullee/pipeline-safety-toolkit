"""Finite reviewed complex transfer paths and native bounded point calibration."""
from __future__ import annotations

import cmath
import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest
from .datasets import file_digest
from . import inverse
from .inverse import OperatingPoint, OperatingPointObservations, OperatingPointPrediction, OperatingPointFitRequest


class AcousticTransferPath(Contract):
    id: Name
    sensor_id: Name
    amplitude_gain: float = Field(ge=0)
    delay_s: float = Field(ge=0)
    phase_rad: float
    amplitude_parameter_id: Name | None = None
    delay_parameter_id: Name | None = None
    phase_parameter_id: Name | None = None
    evidence_id: Name


class AcousticTransferSensor(Contract):
    id: Name
    real_channel_id: Name
    imaginary_channel_id: Name
    calibration_evidence_id: Name


class AcousticMultipathRequest(Contract):
    asset_id: Name
    clock_id: Name
    points: tuple[OperatingPoint, ...] = Field(min_length=1, max_length=100000)
    sensors: tuple[AcousticTransferSensor, ...] = Field(min_length=1, max_length=128)
    paths: tuple[AcousticTransferPath, ...] = Field(min_length=1, max_length=512)
    source_excitation_evidence_id: Name
    model_review_evidence_id: Name
    known_excitation_transfer_reviewed: bool = False
    complex_gain_phase_clock_calibration_reviewed: bool = False
    finite_linear_time_invariant_paths_reviewed: bool = False
    maximum_path_frequency_products: int = Field(default=1000000, ge=1, le=10000000)

    @model_validator(mode="after")
    def identities(self):
        if len({p.id for p in self.points}) != len(self.points) or len({p.id for p in self.paths}) != len(self.paths):
            raise ValueError("point and path IDs must be unique")
        sensor_ids = {s.id for s in self.sensors}
        channels = [c for s in self.sensors for c in (s.real_channel_id, s.imaginary_channel_id)]
        if len(sensor_ids) != len(self.sensors) or len(set(channels)) != len(channels):
            raise ValueError("sensor IDs and real/imaginary channel IDs must be unique")
        if any(p.sensor_id not in sensor_ids for p in self.paths) or any(not any(p.sensor_id == s for p in self.paths) for s in sensor_ids):
            raise ValueError("every sensor requires explicitly assigned paths")
        for point in self.points:
            if set(point.state) != {"frequency_Hz"} or point.state_units != {"frequency_Hz": "Hz"}:
                raise ValueError("spectral operating points require an explicit frequency_Hz axis in Hz")
            f = point.state["frequency_Hz"]
            if f is not None and (not math.isfinite(f) or f < 0):
                raise ValueError("frequency must be finite and nonnegative")
        if len(self.points) * len(self.paths) > self.maximum_path_frequency_products:
            raise ValueError("spectral path/frequency work budget exceeded")
        return self


class AcousticMultipathFitRequest(Contract):
    model: AcousticMultipathRequest
    observations: OperatingPointObservations
    fit: OperatingPointFitRequest

    @model_validator(mode="after")
    def alignment(self):
        if self.observations.asset_id != self.model.asset_id or self.observations.points != self.model.points:
            raise ValueError("transfer observations must preserve model point order, axis and source identity")
        expected = {c for s in self.model.sensors for c in (s.real_channel_id, s.imaginary_channel_id)}
        if {c.id for c in self.observations.channels} != expected or any(c.scale.unit != "1" for c in self.observations.channels):
            raise ValueError("calibrated dimensionless real/imaginary transfer channels must match sensors")
        metric = self.fit.residual_metric
        if metric is None or metric.kind != "covariance":
            raise ValueError("complex transfer calibration requires an explicit joint real/imaginary covariance")
        if any(r.unit != "1" for r in metric.rows):
            raise ValueError("complex transfer covariance rows must be dimensionless")
        bindings = _bindings(self.model)
        parameters = {p.id: p for p in self.fit.parameters}
        if set(bindings) != set(parameters) or any(parameters[k].unit != unit for k, unit in bindings.items()):
            raise ValueError("fit parameters must exactly match declared path bindings and units")
        for path in self.model.paths:
            for field, identity in (("amplitude_gain", path.amplitude_parameter_id), ("delay_s", path.delay_parameter_id)):
                if identity is not None and parameters[identity].lower < 0:
                    raise ValueError("path amplitude and delay parameter bounds must remain nonnegative")
        return self


def _bindings(model):
    result = {}
    for path in model.paths:
        for identity, unit in ((path.amplitude_parameter_id, "1"), (path.delay_parameter_id, "s"), (path.phase_parameter_id, "rad")):
            if identity is not None:
                if identity in result and result[identity] != unit:
                    raise ValueError("shared path parameters require identical units")
                result[identity] = unit
    return result


def _sources():
    return {"model": file_digest(Path(__file__)), "inverse": file_digest(Path(inverse.__file__)),
            "contracts": file_digest(Path(__file__).with_name("contracts.py")),
            "datasets": file_digest(Path(__file__).with_name("datasets.py"))}


def _policy(model, context):
    if (model.asset_id, model.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("transfer asset and calibration clock must match context")
    required = {model.source_excitation_evidence_id, model.model_review_evidence_id}
    required |= {p.evidence_id for p in model.paths} | {s.calibration_evidence_id for s in model.sensors}
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence):
        raise ValueError("context evidence IDs must be unique")
    if any(k not in evidence or evidence[k].sha256 is None for k in required):
        return "needs_data", "hash-pinned excitation, calibration and finite-path review evidence required"
    for point in model.points:
        if any(k not in evidence for k in point.evidence_ids) or not any(
                evidence[k].sha256 in point.source_digests.values() for k in point.evidence_ids):
            return "needs_data", "frequency operating points require matching source evidence"
    if not all((model.known_excitation_transfer_reviewed, model.complex_gain_phase_clock_calibration_reviewed,
                model.finite_linear_time_invariant_paths_reviewed)):
        return "out_of_scope", "known excitation and linear calibrated complex transfer reviews required"
    if any(p.state["frequency_Hz"] is None for p in model.points):
        return "needs_data", "frequency unresolved"
    return None, None


def _prediction(model, parameters, points, context, source_digest):
    values = {c: [] for s in model.sensors for c in (s.real_channel_id, s.imaginary_channel_id)}
    for point in points:
        frequency = point.state["frequency_Hz"]
        for sensor in model.sensors:
            terms = []
            for path in model.paths:
                if path.sensor_id != sensor.id:
                    continue
                amplitude = parameters[path.amplitude_parameter_id] if path.amplitude_parameter_id else path.amplitude_gain
                delay = parameters[path.delay_parameter_id] if path.delay_parameter_id else path.delay_s
                phase = parameters[path.phase_parameter_id] if path.phase_parameter_id else path.phase_rad
                angle = phase - math.tau * frequency * delay
                if not all(math.isfinite(v) for v in (amplitude, delay, angle)) or amplitude < 0 or delay < 0:
                    raise ValueError("resolved path parameters or phase exceed finite linear-transfer scope")
                terms.append(amplitude * cmath.exp(1j * angle))
            real = math.fsum(t.real for t in terms)
            imaginary = math.fsum(t.imag for t in terms)
            values[sensor.real_channel_id].append(real)
            values[sensor.imaginary_channel_id].append(imaginary)
    payload = dict(parameters=parameters, point_digests={p.id: digest(p) for p in points}, values=values,
                   model_digest=digest(model), context_digest=digest(context))
    return OperatingPointPrediction(status="computed", asset_id=context.asset_id,
        point_ids=tuple(p.id for p in points), point_digests=payload["point_digests"],
        values={k: tuple(v) for k, v in values.items()}, units={k: "1" for k in values},
        provider_source_digest=source_digest, evaluation_digest=digest(payload),
        diagnostics=dict(axis="frequency_Hz; not time", source_excitation_inferred=False,
                         independent_path_identification_claim=False, field_qualification=False))


def _seal(body):
    return dict(body, record_digest=digest(body))


def evaluate_acoustic_multipath(request: AcousticMultipathRequest, context: RunContext):
    q = AcousticMultipathRequest.model_validate_json(request.model_dump_json())
    pins = _sources()
    snapshot, context_snapshot = digest(q), digest(context)
    status, issue = _policy(q, context)
    if _bindings(q):
        status, issue = "out_of_scope", "forward requests require fixed paths; parameter bindings belong to calibration"
    prediction = None if status else _prediction(q, {}, q.points, context, digest(pins)).model_dump(mode="json")
    if _sources() != pins or digest(q) != snapshot or digest(context) != context_snapshot:
        raise ValueError("multipath source/configuration/context changed during evaluation")
    return _seal(dict(schema="pipeline_integrity.acoustic_multipath.v1", request=q.model_dump(mode="json"),
                      context=context.model_dump(mode="json"), source_pins=pins,
                      status=status or "computed_diagnostic", issue=issue, prediction=prediction,
                      field_qualification=False, source_location_calculated=False))


def evaluate_acoustic_multipath_fit(request: AcousticMultipathFitRequest, context: RunContext):
    q = AcousticMultipathFitRequest.model_validate_json(request.model_dump_json())
    pins = _sources()
    snapshot, context_snapshot = digest(q), digest(context)
    status, issue = _policy(q.model, context)
    native = None
    if status is None:
        point_hashes = {p.id: digest(p) for p in q.model.points}

        def predict(parameters, points, actual_context):
            if _sources() != pins or digest(q) != snapshot or digest(actual_context) != context_snapshot:
                raise ValueError("multipath fit source, configuration or context changed")
            if any(point_hashes.get(p.id) != digest(p) for p in points):
                raise ValueError("fit provider received an undeclared frequency/source state")
            if set(parameters) != set(_bindings(q.model)):
                raise ValueError("fit provider parameter bindings differ")
            result = _prediction(q.model, parameters, points, actual_context, digest(pins))
            if _sources() != pins or digest(q) != snapshot or digest(actual_context) != context_snapshot:
                raise ValueError("multipath fit configuration changed during prediction")
            return result

        predict.parameter_units = _bindings(q.model)
        native = inverse.fit_operating_points(q.observations, q.fit, context, predict)
        if native.get("status") != "fitted":
            status, issue = "out_of_scope", "native bounded fit did not establish a converged locally identifiable fit"
            matrix=q.fit.residual_metric.matrix
            if native.get("status")=="withheld" and (matrix is None or any(v is None for row in matrix for v in row)):
                status, issue = "needs_data", "native fit withheld unresolved complex-transfer covariance"
    if _sources() != pins or digest(q) != snapshot or digest(context) != context_snapshot:
        raise ValueError("multipath fit source/configuration/context changed")
    return _seal(dict(schema="pipeline_integrity.acoustic_multipath_fit.v1", request=q.model_dump(mode="json"),
                      context=context.model_dump(mode="json"), source_pins=pins, status=status or "computed_diagnostic",
                      issue=issue, native_fit=native, field_qualification=False,
                      independent_path_identification_claim=False, source_location_calculated=False,
                      limitations=["finite coherent dimensionless transfer with known source excitation",
                                   "bounded local calibration; label switching and phase/delay aliasing remain possible",
                                   "native rank and heldout diagnostics are retained without an identification guarantee"]))


def _verify(record, request, context, evaluator):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("multipath record seal changed")
    replay = evaluator(request, context)
    if digest(replay) != digest(record):
        raise ValueError("multipath replay differs")
    return True


def verify_acoustic_multipath_record(record, *, request=None, context=None):
    return _verify(record, request or AcousticMultipathRequest.model_validate_json(json.dumps(record["request"], allow_nan=False)),
                   context or RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False)), evaluate_acoustic_multipath)


def verify_acoustic_multipath_fit_record(record, *, request=None, context=None):
    return _verify(record, request or AcousticMultipathFitRequest.model_validate_json(json.dumps(record["request"], allow_nan=False)),
                   context or RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False)), evaluate_acoustic_multipath_fit)
