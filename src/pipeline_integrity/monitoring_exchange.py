"""Separate reference, calibration and held-out scoring through the existing PMT owner."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import io
import json
import math
import os
import platform
import subprocess
import sys
import warnings
from contextlib import contextmanager, nullcontext, redirect_stdout
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .adapters import monitoring_batch
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest

FAMILIES = {
    "statistical": "statistics",
    "classical": "classical",
    "forecasting": "forecasting",
    "modern": "modern_library",
    "dynamic_subspace": "subspace_temporal",
    "neural": "neural",
    "temporal_neural": "temporal_neural",
}
NEURAL_FAMILIES = {"neural", "temporal_neural"}
_OWNER_LOCK = RLock()
REVIEWS = (
    "reference_and_calibration_population",
    "source_clocks_channels_units_and_missingness",
    "heldout_observations_and_causal_window_endpoints",
    "parameter_support_and_score_meaning",
)


class MonitoringFeatures(Contract):
    window: int = Field(default=1, ge=1, le=2000)
    statistics: tuple[Name, ...] = ("last",)
    max_gap_s: Positive | None = None


class MonitoringSplit(Contract):
    role: Literal["training", "calibration", "evaluation"]
    split_id: Name
    physical_run_id: Name
    source_group_id: Name
    source_evidence_id: Name
    observation_ids: tuple[Name, ...] = Field(min_length=1, max_length=100000)
    frames: tuple[SignalFrame, ...] = Field(min_length=1, max_length=100000)
    reference_kind: Literal["verified_normal", "caller_declared_reference", "not_used", "unresolved"] = (
        "unresolved"
    )

    @model_validator(mode="after")
    def rows(self):
        if len(self.observation_ids) != len(self.frames) or len(set(self.observation_ids)) != len(
            self.observation_ids
        ):
            raise ValueError("monitoring original row identities must be unique and aligned")
        first = self.frames[0]
        if any(not name.strip() or not first.units[name].strip() for name in first.values):
            raise ValueError("monitoring channels and source units require explicit nonempty identities")
        if any(
            (f.stream_id, f.clock_id, tuple(f.values), f.units, f.origin)
            != (first.stream_id, first.clock_id, tuple(first.values), first.units, first.origin)
            for f in self.frames
        ):
            raise ValueError(
                "monitoring split must preserve source stream, clock, channel order, units and origin"
            )
        if any(b.time_s <= a.time_s for a, b in zip(self.frames, self.frames[1:], strict=False)):
            raise ValueError("monitoring source clock must strictly increase without sorting")
        return self


def _parameter_budget(value, maximum):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > maximum or depth > 15:
            raise ValueError("monitoring parameter budget exceeded before serialization")
        if isinstance(item, (dict, list, tuple)):
            if len(item) > maximum - count:
                raise ValueError("monitoring parameter budget exceeded before serialization")
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))


def _strict_dump(model):
    # Keep valid timezone-bearing frame timestamps while rejecting NaN/Inf before
    # Pydantic's JSON mode could replace a nonfinite numeric value by null.
    stack = [model.model_dump(mode="python")]
    while stack:
        value = stack.pop()
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("monitoring nonfinite values must not be converted into unknown null")
        if isinstance(value, (dict, list, tuple)):
            stack.extend(value.values() if isinstance(value, dict) else value)
    return model.model_dump(mode="json")


class MonitoringRequest(Contract):
    family: Literal[
        "statistical", "classical", "forecasting", "modern", "dynamic_subspace", "neural", "temporal_neural"
    ]
    method: Name
    asset_id: Name
    clock_id: Name
    population_id: Name
    split_policy: Literal["chronological_within_run", "separate_physical_runs"]
    parameters: dict | None
    features: MonitoringFeatures = Field(default_factory=MonitoringFeatures)
    normalize: bool
    false_alarm_fraction: float = Field(gt=0, lt=1)
    history_policy: Literal["reset_each_split"] = "reset_each_split"
    expected_owner_version: Name | None = None
    expected_native_spec_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_rows_per_split: int = Field(default=20000, ge=2, le=100000)
    maximum_channels: int = Field(default=128, ge=1, le=512)
    maximum_feature_cells: int = Field(default=2000000, ge=1, le=5000000)
    maximum_parameter_nodes: int = Field(default=10000, ge=100, le=100000)
    maximum_training_epochs: int = Field(default=100, ge=1, le=10000)
    maximum_hidden_units: int = Field(default=1024, ge=1, le=65536)
    maximum_training_work: int = Field(default=100000000, ge=1, le=1000000000000)
    cpu_threads: int = Field(default=1, ge=1, le=32)

    @model_validator(mode="after")
    def recipe(self):
        _parameter_budget(self.parameters, self.maximum_parameter_nodes)
        json_value(self.parameters)
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("monitoring review keys must match this exchange contract")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("monitoring selected evidence must be unique")
        if not self.features.statistics or len(set(self.features.statistics)) != len(
            self.features.statistics
        ):
            raise ValueError("monitoring feature statistics must be nonempty and distinct")
        return self


def _validate(request, splits, context):
    _parameter_budget(request.parameters, request.maximum_parameter_nodes)
    if len(splits) != 3 or [s.role for s in splits] != ["training", "calibration", "evaluation"]:
        raise ValueError("three ordered training/calibration/evaluation splits are required")
    parameters = request.parameters or {}
    sequence = request.family in {"dynamic_subspace", "temporal_neural"} or (
        request.family == "forecasting" and request.method.startswith("lag_")
    )
    window = parameters.get("window", 12) if sequence else request.features.window
    if type(window) is not int or not (2 if sequence else 1) <= window <= 2000:
        raise ValueError("monitoring native/feature window must be a bounded integer")
    for split in splits:
        n, d = len(split.frames), len(split.frames[0].values)
        if (
            n > request.maximum_rows_per_split
            or d > request.maximum_channels
            or n * d * max(window, len(request.features.statistics)) > request.maximum_feature_cells
        ):
            raise ValueError("monitoring row/channel/feature work budget exceeded before serialization")
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("monitoring request and context asset or analysis clock differ")
    evidence = {e.id: e for e in context.evidence}
    if (
        len(evidence) != len(context.evidence)
        or not set(request.evidence_ids) <= set(evidence)
        or not set(request.review_evidence_ids.values()) <= set(request.evidence_ids)
        or any(s.source_evidence_id not in request.evidence_ids for s in splits)
    ):
        raise ValueError("monitoring source and review evidence must be selected and present")
    if len({s.split_id for s in splits}) != 3:
        raise ValueError("monitoring stage identities must differ")
    sets = [set(s.observation_ids) for s in splits]
    if any(sets[i] & sets[j] for i in range(3) for j in range(i)):
        raise ValueError("training/calibration/evaluation original observations overlap")
    first = splits[0].frames[0]
    if any(
        (tuple(s.frames[0].values), s.frames[0].units) != (tuple(first.values), first.units) for s in splits
    ):
        raise ValueError("monitoring ordered channels and units must match across stages")
    if request.split_policy == "chronological_within_run":
        if (
            len(
                {
                    (
                        s.source_group_id,
                        s.physical_run_id,
                        s.frames[0].clock_id,
                        s.frames[0].stream_id,
                        s.frames[0].origin,
                    )
                    for s in splits
                }
            )
            != 1
        ):
            raise ValueError(
                "chronological holdout requires the same physical run, source group and source clock"
            )
        if any(a.frames[-1].time_s >= b.frames[0].time_s for a, b in zip(splits, splits[1:], strict=False)):
            raise ValueError("chronological stages overlap or reverse physical time")
    elif len({s.physical_run_id for s in splits}) != 3 or len({s.source_group_id for s in splits}) != 3:
        raise ValueError(
            "separate-run holdout requires three distinct physical run and source-group identities"
        )
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    if request.family in NEURAL_FAMILIES:
        for key in ("epochs", "batch_size", "hidden", "seed"):
            if key not in parameters:
                issues.append("explicit neural training setting required: " + key)
            elif type(parameters[key]) is not int or (key != "seed" and parameters[key] < 1):
                raise ValueError("neural training settings require integer seed and positive integer sizes")
        if request.family == "temporal_neural" and "window" not in parameters:
            issues.append("explicit neural sequence window required")
        if parameters.get("epochs", 1) > request.maximum_training_epochs:
            raise ValueError("neural training epoch budget exceeded before owner import")
        if parameters.get("hidden", 1) > request.maximum_hidden_units:
            raise ValueError("neural hidden-unit budget exceeded before owner import")
        n, d = len(splits[0].frames), len(splits[0].frames[0].values)
        if (
            n * d * window * parameters.get("hidden", 1) * parameters.get("epochs", 1)
            > request.maximum_training_work
        ):
            raise ValueError("neural nominal training work budget exceeded before owner import")
    if request.parameters is None or any(
        v is None
        for v in (
            request.expected_owner_version,
            request.expected_native_spec_digest,
            request.owner_source_tree_sha256,
        )
    ):
        issues.append("detector parameters, owner version, native specification or source pin are unresolved")
    if any(s.reference_kind not in ("verified_normal", "caller_declared_reference") for s in splits[:2]):
        issues.append("training and calibration reference meanings require explicit declarations")
    if splits[2].reference_kind != "not_used":
        raise ValueError("held-out evaluation must not declare itself a fitted normal reference")
    return issues


def monitoring_source_fingerprint(root):
    root = Path(root)
    sources = sorted((root / "src").rglob("*.py"))
    if not sources:
        raise ValueError("monitoring source root has no Python sources")
    fingerprint = hashlib.sha256()
    for path in sources:
        fingerprint.update(path.relative_to(root).as_posix().encode())
        fingerprint.update(path.read_bytes())
    support = [
        root / "LICENSE",
        root / "THIRD_PARTY_NOTICES.md",
        *sorted((root / "third_party_licenses").glob("*.txt")),
        *sorted((root / "src").rglob("*.json")),
    ]
    if any(not p.is_file() for p in support):
        raise ValueError("monitoring source license and notices must be preserved")
    return dict(
        source_tree_sha256=fingerprint.hexdigest(),
        source_tree_files=len(sources),
        support_sha256={p.relative_to(root).as_posix(): file_digest(p) for p in support},
    )


def _runtime_versions():
    result = {"python": platform.python_version()}
    for name in (
        "numpy",
        "scipy",
        "scikit-learn",
        "pandas",
        "statsmodels",
        "pyod",
        "combo",
        "numba",
        "llvmlite",
        "torch",
    ):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


@contextmanager
def _neural_runtime(threads):
    """Bound CPU threads and restore CPU RNG and deterministic settings."""
    import torch

    previous_threads = torch.get_num_threads()
    deterministic = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    with torch.device("cpu"), torch.random.fork_rng(devices=[]):
        try:
            torch.set_num_threads(threads)
            torch.use_deterministic_algorithms(True)
            yield {
                "torch_version": torch.__version__,
                "device": "cpu",
                "cpu_threads": threads,
                "deterministic_algorithms": True,
                "pretrained_weights_loaded": False,
            }
        finally:
            torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
            torch.set_num_threads(previous_threads)


def _neural_state(detector):
    """Fingerprint fitted tensors and transforms without pickling or exporting weights."""
    import numpy as np

    state = {}

    def add(name, value):
        if hasattr(value, "detach"):
            if value.device.type != "cpu":
                raise ValueError("the reviewed neural boundary supports CPU tensors only")
            value = value.detach().contiguous().numpy()
        array = np.asarray(value)
        if array.dtype.kind not in "fiu" or not np.isfinite(array).all():
            raise ValueError("fitted neural state must contain finite numeric CPU arrays")
        state[name] = {
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
        }

    for attribute in ("model", "encoder", "decoder", "generator", "discriminator"):
        module = getattr(detector, attribute, None)
        if module is not None and hasattr(module, "state_dict"):
            for name, tensor in module.state_dict().items():
                add(attribute + "." + name, tensor)
    if not state:
        raise ValueError("native neural detector exposes no fitted tensor state")
    for attribute in ("center", "scale", "embedding_center", "embedding_scale", "query_noise", "alpha_bar"):
        if hasattr(detector, attribute):
            add(attribute, getattr(detector, attribute))
    return state


@contextmanager
def _native_owner_unlocked(root, expected):
    root = Path(root).resolve()
    before = monitoring_source_fingerprint(root)
    if before["source_tree_sha256"] != expected:
        raise ValueError("monitoring source differs from its declared pin")
    previous_path, bytecode = sys.path.copy(), sys.dont_write_bytecode
    report = dict(before=before, after=None, source_unchanged=False)
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root / "src"))
        owner = importlib.import_module("process_monitoring")
        for value in (
            owner,
            owner.Monitor,
            owner.create_detector,
            owner.list_detectors,
            owner.SignalBatch,
            owner.WindowFeatures,
        ):
            path = value.__file__ if inspect.ismodule(value) else inspect.getfile(value)
            if not Path(path).resolve().is_relative_to(root / "src"):
                raise ValueError("loaded monitoring package/factory does not belong to its pinned source")
        yield owner, report
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = bytecode
        after = monitoring_source_fingerprint(root)
        report.update(after=after, source_unchanged=before == after)
        if before != after:
            raise ValueError("monitoring source, license or supporting resources changed during execution")


@contextmanager
def _native_owner(root, expected):
    # Source selection changes process-global import state; serialize that local
    # boundary. A separate Python client gives callers independent processes.
    with _OWNER_LOCK, _native_owner_unlocked(root, expected) as selected:
        yield selected


class MonitoringClient(Protocol):
    def describe(self, method: str, *, expected_source_sha256: str) -> dict: ...
    def run(self, recipe: dict, splits: list[dict], context: dict) -> dict: ...


class NativeMonitoringClient:
    """Use only the selected owner's public Monitor/create_detector/batch contracts."""

    def __init__(self, source_root):
        self.source_root = Path(source_root)

    def describe(self, method, *, expected_source_sha256):
        with _native_owner(self.source_root, expected_source_sha256) as (owner, pin):
            rows = [row for row in owner.list_detectors() if row["name"] == method]
            if len(rows) != 1:
                raise ValueError("one registered native monitoring method is required")
            result = dict(
                owner_version=owner.__version__, native_spec=rows[0], native_spec_digest=digest(rows[0])
            )
        return dict(result, source_pin=pin)

    def run(self, recipe, splits, context):
        request = MonitoringRequest.model_validate_json(json.dumps(recipe, allow_nan=False))
        phases = [MonitoringSplit.model_validate_json(json.dumps(s, allow_nan=False)) for s in splits]
        ctx = RunContext.model_validate_json(json.dumps(context, allow_nan=False))
        if _validate(request, phases, ctx):
            raise ValueError("native monitoring client received unresolved exchange inputs")
        captured = io.StringIO()
        with _native_owner(self.source_root, request.owner_source_tree_sha256) as (owner, pin):
            specs = [s for s in owner.list_detectors() if s["name"] == request.method]
            if (
                len(specs) != 1
                or specs[0]["family"] != FAMILIES[request.family]
                or specs[0]["training"] != "normal"
            ):
                raise ValueError("selected method is not in the declared normal-reference family")
            spec = specs[0]
            if (
                owner.__version__ != request.expected_owner_version
                or digest(spec) != request.expected_native_spec_digest
            ):
                raise ValueError("monitoring owner version or native registry specification changed")
            with (
                warnings.catch_warnings(record=True) as warning_records,
                redirect_stdout(captured),
                _neural_runtime(request.cpu_threads)
                if request.family in NEURAL_FAMILIES
                else nullcontext(None) as execution,
            ):
                warnings.simplefilter("always")
                detector = owner.create_detector(request.method, **request.parameters)
                if (
                    not Path(inspect.getfile(type(detector)))
                    .resolve()
                    .is_relative_to(self.source_root.resolve() / "src")
                ):
                    raise ValueError("registered detector class is outside the selected owner source")
                if detector.sequence and (
                    request.features.window != 1
                    or request.features.statistics != ("last",)
                    or request.features.max_gap_s is not None
                ):
                    raise ValueError("sequence detector owns its window; use identity feature settings")
                features = owner.WindowFeatures(
                    window=request.features.window,
                    statistics=request.features.statistics,
                    max_gap_s=request.features.max_gap_s,
                )
                monitor = owner.Monitor(detector, features=features, normalize=request.normalize)
                batches = [
                    monitoring_batch(
                        s.frames,
                        factory=owner.SignalBatch,
                        metadata=dict(
                            pipeline_context_digest=digest(ctx),
                            split_id=s.split_id,
                            physical_run_id=s.physical_run_id,
                            source_group_id=s.source_group_id,
                            original_observation_ids=list(s.observation_ids),
                            analysis_clock_id=request.clock_id,
                        ),
                    )
                    for s in phases
                ]
                monitor.fit(batches[0])
                fitted = _neural_state(detector) if execution is not None else None
                monitor.calibrate(batches[1], false_alarm_fraction=request.false_alarm_fraction)
                calibration = monitor.score(batches[1]).to_dict()
                evaluation = monitor.score(batches[2]).to_dict()
                training = None
                if execution is not None:
                    after_queries = _neural_state(detector)
                    if fitted != after_queries:
                        raise ValueError("neural calibration or scoring changed fitted weights/transforms")
                    training = {
                        "seed": request.parameters["seed"],
                        "epochs": request.parameters["epochs"],
                        "loss_history": [float(v) for v in detector.loss_history],
                        "before_queries": fitted,
                        "after_queries": after_queries,
                        "unchanged": True,
                        "execution": execution,
                    }
                result = dict(
                    schema="pipeline_integrity.monitoring_native.v1",
                    owner="process-monitoring-toolkit",
                    owner_version=owner.__version__,
                    native_spec=spec,
                    native_spec_digest=digest(spec),
                    recipe_digest=digest(recipe),
                    context_digest=digest(context),
                    split_digests=[digest(s) for s in splits],
                    calibration=calibration,
                    evaluation=evaluation,
                    detector_config=json_value(detector.get_config()),
                    native_window_rows=getattr(detector, "window", request.features.window),
                    feature_transform_applied=not detector.sequence,
                    pipeline_scaler_applied=monitor.scaler is not None,
                    runtime_versions=_runtime_versions(),
                    neural_training=training,
                    warnings=[str(w.message)[:1000] for w in warning_records][:20],
                    captured_output=captured.getvalue()[-2000:],
                )
        body = dict(result, source_pin=pin)
        return dict(body, result_digest=digest(body))


class PythonMonitoringClient:
    """Isolate optional libraries in a selected Python; transport data as strict JSON."""

    def __init__(
        self, python_executable, source_root, *, maximum_seconds=180, maximum_payload_bytes=64000000
    ):
        self.python_executable, self.source_root = Path(python_executable), Path(source_root)
        if type(maximum_seconds) is not int or not 1 <= maximum_seconds <= 1800:
            raise ValueError("monitoring worker requires a bounded positive time budget")
        if type(maximum_payload_bytes) is not int or not 1 <= maximum_payload_bytes <= 64000000:
            raise ValueError("monitoring worker requires a bounded payload budget")
        self.maximum_seconds, self.maximum_payload_bytes = maximum_seconds, maximum_payload_bytes
        self.last_diagnostics = None

    def _call(self, payload):
        encoded = json.dumps(
            json_value(dict(payload, source_root=str(self.source_root.resolve()))), allow_nan=False
        )
        if len(encoded.encode("utf-8")) > self.maximum_payload_bytes:
            raise ValueError("monitoring worker JSON payload budget exceeded")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["NUMBA_NUM_THREADS"] = "1"
        result = subprocess.run(
            [str(self.python_executable), "-X", "utf8", "-m", "pipeline_integrity.monitoring_worker"],
            input=encoded,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            timeout=self.maximum_seconds,
        )
        self.last_diagnostics = dict(
            returncode=result.returncode, stderr=getattr(result, "stderr", "")[-20000:]
        )
        if len(result.stdout.encode("utf-8")) > self.maximum_payload_bytes:
            raise ValueError("monitoring worker response budget exceeded")
        if result.returncode:
            raise RuntimeError("monitoring JSON worker failed; inspect its private diagnostics")
        response = json.loads(result.stdout)
        if response.get("status") != "ok":
            self.last_diagnostics["worker_error"] = response
            raise RuntimeError(
                "monitoring worker rejected the request: " + response.get("error_type", "unknown")
            )
        return response["result"]

    def describe(self, method, *, expected_source_sha256):
        return self._call(
            dict(operation="describe", method=method, expected_source_sha256=expected_source_sha256)
        )

    def run(self, recipe, splits, context):
        return self._call(dict(operation="run", recipe=recipe, splits=splits, context=context))


def evaluate_monitoring(request, splits, context, *, client=None, owner_source_root=None):
    """Fit once on training only, calibrate on a second split and score a third split."""
    if (
        not isinstance(request, MonitoringRequest)
        or not isinstance(context, RunContext)
        or any(not isinstance(s, MonitoringSplit) for s in splits)
    ):
        raise TypeError("MonitoringRequest, three MonitoringSplit objects and RunContext are required")
    issues = _validate(request, splits, context)
    recipe, phases, ctx = _strict_dump(request), [_strict_dump(s) for s in splits], _strict_dump(context)
    request = MonitoringRequest.model_validate_json(json.dumps(recipe, allow_nan=False))
    splits = [MonitoringSplit.model_validate_json(json.dumps(s, allow_nan=False)) for s in phases]
    context = RunContext.model_validate_json(json.dumps(ctx, allow_nan=False))
    issues = _validate(request, splits, context)
    body = dict(
        schema="pipeline_integrity.monitoring_exchange.v1",
        request=request.model_dump(mode="json"),
        splits=[s.model_dump(mode="json") for s in splits],
        context=context.model_dump(mode="json"),
        status="needs_data",
        issues=issues,
        native_receipt=None,
        output_origin="simulation",
        score_meaning="ordered_abnormality_index_not_probability",
        score_is_probability_or_HEP=False,
        calibration_fraction_is_guaranteed_future_rate=False,
        field_detection_validation=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if not issues:
        if client is None:
            if owner_source_root is None:
                raise ValueError("select a monitoring client or explicit owner source root")
            client = NativeMonitoringClient(owner_source_root)
        native = client.run(body["request"], body["splits"], body["context"])
        body.update(
            native_receipt=native,
            status="computed" if any(native["evaluation"]["valid"]) else "needs_data",
            issues=[] if any(native["evaluation"]["valid"]) else ["no usable held-out evaluation scores"],
        )
    record = dict(body, record_digest=digest(body))
    verify_monitoring_record(record)
    return record


def _verify_detection(result, split, channels, *, window, max_gap_s):
    if result.get("schema") != "process-monitoring.detection.v1" or result.get("time_s") != [
        f.time_s for f in split.frames
    ]:
        raise ValueError("monitoring score schema or original endpoint clock changed")
    if any(len(result.get(k, [])) != len(split.frames) for k in ("scores", "valid", "alarms")):
        raise ValueError("monitoring scores must align with every original observation")
    threshold = result.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
        raise ValueError("monitoring calibrated threshold must be finite")
    if result.get("metadata", {}).get("channels") != channels:
        raise ValueError("monitoring native channels changed")
    for index, (frame, score, valid, alarm) in enumerate(
        zip(split.frames, result["scores"], result["valid"], result["alarms"], strict=True)
    ):
        if type(valid) is not bool or type(alarm) is not bool or (valid and not all(frame.valid.values())):
            raise ValueError("monitoring validity cannot repair source missingness")
        if valid and (
            isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)
        ):
            raise ValueError("valid monitoring scores require finite numbers")
        if (not valid and score is not None) or alarm != (valid and score > threshold):
            raise ValueError("monitoring missing score or strict threshold alarm meaning changed")
        if valid and window > 1:
            history = split.frames[max(0, index + 1 - window) : index + 1]
            if (
                len(history) != window
                or any(not all(f.valid.values()) for f in history)
                or any(f.operating_mode != frame.operating_mode for f in history)
                or max_gap_s is not None
                and any(b.time_s - a.time_s > max_gap_s for a, b in zip(history, history[1:], strict=False))
            ):
                raise ValueError("monitoring score claims an unavailable causal history window")


def _verify_neural_training(native, request):
    training = native.get("neural_training")
    if request.family not in NEURAL_FAMILIES:
        if training is not None:
            raise ValueError("non-neural monitoring path claimed a neural training receipt")
        return
    if (
        not isinstance(training, dict)
        or type(training.get("seed")) is not int
        or type(training.get("epochs")) is not int
        or training.get("unchanged") is not True
        or (training.get("seed"), training.get("epochs"))
        != (request.parameters["seed"], request.parameters["epochs"])
    ):
        raise ValueError("neural training seed, epochs or frozen-state claim changed")
    losses = training.get("loss_history")
    if (
        not isinstance(losses, list)
        or len(losses) != request.parameters["epochs"]
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in losses)
    ):
        raise ValueError("neural loss trace must retain every finite training epoch")
    before = training.get("before_queries")
    if (
        not isinstance(before, dict)
        or not before
        or before != training.get("after_queries")
        or not {"center", "scale"} <= before.keys()
        or not any(name.startswith(("model.", "encoder.", "generator.")) for name in before)
    ):
        raise ValueError("neural fitted tensors and preprocessing changed during held-out queries")
    for value in before.values():
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("shape"), list)
            or any(type(size) is not int or size < 0 for size in value["shape"])
            or value.get("dtype") not in {"float32", "float64", "int32", "int64"}
            or not isinstance(value.get("sha256"), str)
            or len(value["sha256"]) != 64
            or set(value["sha256"]) - set("0123456789abcdef")
        ):
            raise ValueError("neural fitted-state array fingerprint is malformed")
    execution = training.get("execution", {})
    if (
        execution.get("device") != "cpu"
        or type(execution.get("cpu_threads")) is not int
        or execution.get("cpu_threads") != request.cpu_threads
        or execution.get("deterministic_algorithms") is not True
        or execution.get("pretrained_weights_loaded") is not False
        or not isinstance(execution.get("torch_version"), str)
        or execution["torch_version"].split("+", 1)[0]
        != str(native.get("runtime_versions", {}).get("torch")).split("+", 1)[0]
    ):
        raise ValueError("neural execution differs from the reviewed CPU/runtime conditions")


def verify_monitoring_record(record):
    if record.get("schema") != "pipeline_integrity.monitoring_exchange.v1" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("monitoring recipe, observations or result changed after sealing")
    if any(
        record.get(k) is not False
        for k in (
            "score_is_probability_or_HEP",
            "calibration_fraction_is_guaranteed_future_rate",
            "field_detection_validation",
        )
    ):
        raise ValueError("monitoring score or qualification was relabelled")
    if (
        record.get("output_origin") != "simulation"
        or record.get("score_meaning") != "ordered_abnormality_index_not_probability"
    ):
        raise ValueError("monitoring calculated score was relabelled as an observation or probability")
    request = MonitoringRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    splits = [MonitoringSplit.model_validate_json(json.dumps(s, allow_nan=False)) for s in record["splits"]]
    context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    issues = _validate(request, splits, context)
    native = record.get("native_receipt")
    if native is None:
        if not issues or record.get("status") != "needs_data" or record.get("issues") != issues:
            raise ValueError("unexecuted monitoring record cannot claim a ready result")
        return
    if native.get("result_digest") != digest({k: v for k, v in native.items() if k != "result_digest"}):
        raise ValueError("monitoring native receipt changed after its original result was sealed")
    if issues or (
        native.get("schema"),
        native.get("owner"),
        native.get("owner_version"),
        native.get("recipe_digest"),
        native.get("context_digest"),
        native.get("split_digests"),
    ) != (
        "pipeline_integrity.monitoring_native.v1",
        "process-monitoring-toolkit",
        request.expected_owner_version,
        digest(record["request"]),
        digest(record["context"]),
        [digest(s) for s in record["splits"]],
    ):
        raise ValueError("monitoring native receipt differs from its owner, recipe or source splits")
    spec = native.get("native_spec", {})
    if (spec.get("name"), spec.get("family"), spec.get("training"), digest(spec)) != (
        request.method,
        FAMILIES[request.family],
        "normal",
        request.expected_native_spec_digest,
    ):
        raise ValueError("monitoring native registration changed family, method or training meaning")
    if native.get("native_spec_digest") != digest(spec):
        raise ValueError("monitoring native specification digest changed")
    _verify_neural_training(native, request)
    sequence = spec.get("temporal") is True
    config = native.get("detector_config", {})
    if config.get("registry_name") != request.method or config.get("parameters") != request.parameters:
        raise ValueError("monitoring detector recipe differs from supplied parameters")
    if native.get("feature_transform_applied") is not (not sequence) or native.get(
        "pipeline_scaler_applied"
    ) is not (request.normalize and not sequence):
        raise ValueError("monitoring feature/scaler application differs from the selected native path")
    expected_window = request.parameters.get("window", 12) if sequence else request.features.window
    if type(expected_window) is not int or native.get("native_window_rows") != expected_window:
        raise ValueError("monitoring original window size changed")
    max_gap = request.parameters.get("max_gap_s") if sequence else request.features.max_gap_s
    pin = native.get("source_pin", {})
    if (
        pin.get("source_unchanged") is not True
        or pin.get("before") != pin.get("after")
        or pin.get("before", {}).get("source_tree_sha256") != request.owner_source_tree_sha256
        or type(pin.get("before", {}).get("source_tree_files")) is not int
        or pin["before"]["source_tree_files"] < 1
        or not pin["before"].get("support_sha256")
    ):
        raise ValueError("monitoring source/license report differs from the selected source pin")
    channels = list(splits[0].frames[0].values)
    _verify_detection(native["calibration"], splits[1], channels, window=expected_window, max_gap_s=max_gap)
    _verify_detection(native["evaluation"], splits[2], channels, window=expected_window, max_gap_s=max_gap)
    if native["calibration"]["threshold"] != native["evaluation"]["threshold"]:
        raise ValueError("held-out evaluation changed the calibrated threshold")
    cal = native["evaluation"].get("metadata", {}).get("calibration", {})
    if (
        cal.get("rows") != sum(native["calibration"]["valid"])
        or cal.get("false_alarm_fraction") != request.false_alarm_fraction
        or cal != native["calibration"].get("metadata", {}).get("calibration")
    ):
        raise ValueError("monitoring calibration provenance differs from its original held-out stage")
    expected_status = "computed" if any(native["evaluation"]["valid"]) else "needs_data"
    if record["status"] != expected_status:
        raise ValueError("monitoring status differs from its held-out score availability")
    json_value(record)
