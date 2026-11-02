"""Typed calculation registration without configuration-driven code execution."""

from __future__ import annotations

import hashlib
import inspect
import platform
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from .contracts import AssessmentResult, Calculation, Contract, ModelInfo, RunContext, digest


@dataclass(frozen=True)
class ModelSpec:
    info: ModelInfo
    inputs: type[Contract]
    calculate: Callable[[Contract], Calculation]


def _model_source_identity(spec):
    target = spec.calculate.func if isinstance(spec.calculate, partial) else spec.calculate
    source_target = target
    if not inspect.isroutine(source_target) and callable(source_target):
        source_target = type(source_target).__call__
    try:
        source = inspect.getsource(source_target)
        kind = "callable_source"
    except (OSError, TypeError):
        source = repr(type(spec.calculate)) + spec.info.id + spec.info.version
        kind = "declared_callable_fallback"
    try:
        filename = inspect.getsourcefile(source_target)
    except TypeError:
        filename = None
    module_sha = "unavailable"
    if filename is not None:
        path = Path(filename)
        try:
            if path.is_file():
                module_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            module_sha = "unavailable"
    binding = _callable_binding_identity(spec.calculate)
    identity = {
        "source_fingerprint_schema": "pipeline_integrity.model_source.v3",
        "model_callable_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "model_module_source_sha256": module_sha,
        "model_source_scope": kind
        + ("_and_module_bytes" if module_sha != "unavailable" else "_without_module_bytes"),
    }
    if binding is not None:
        identity["callable_binding_sha256"] = digest(binding["value"])
        identity["callable_binding_scope"] = binding["scope"]
        identity["model_source_scope"] += "_and_callable_binding_" + binding["scope"]
    return identity


def _binding_value(value, active=None):
    """Create a value-free fingerprint payload for captured/bound callable state."""
    if value is None or isinstance(value, (str, int, bool)):
        return {"value": value, "complete": True}
    if isinstance(value, float):
        if value == value and abs(value) != float("inf"):
            return {"value": value, "complete": True}
        return {"value": {"type": "nonfinite_float"}, "complete": False}
    if isinstance(value, (date, datetime, time)):
        return {"value": {"type": type(value).__name__, "iso": value.isoformat()}, "complete": True}
    if isinstance(value, (list, tuple)):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            return {"value": {"type": "cyclic_sequence"}, "complete": False}
        active.add(identity)
        try:
            items = [_binding_value(item, active) for item in value]
            return {
                "value": [item["value"] for item in items],
                "complete": all(item["complete"] for item in items),
            }
        finally:
            active.remove(identity)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            return {"value": {"type": "cyclic_mapping"}, "complete": False}
        active.add(identity)
        try:
            items = {key: _binding_value(item, active) for key, item in sorted(value.items())}
            return {
                "value": {key: item["value"] for key, item in items.items()},
                "complete": all(item["complete"] for item in items.values()),
            }
        finally:
            active.remove(identity)
    if isinstance(value, Contract):
        return {"value": value.model_dump(mode="json"), "complete": True}

    value_type = value if isinstance(value, type) else type(value)
    type_name = f"{value_type.__module__}.{value_type.__qualname__}"
    try:
        state = getattr(value, "__dict__", None)
    except Exception:
        state = None
    if isinstance(state, dict):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            return {"value": {"type": type_name, "state": "cyclic"}, "complete": False}
        active.add(identity)
        try:
            items = {
                key: _binding_value(item, active)
                for key, item in sorted(state.items())
                if isinstance(key, str)
            }
            try:
                filename = inspect.getsourcefile(value_type)
            except TypeError:
                filename = None
            try:
                source_path = Path(filename) if filename else None
                module_sha = (
                    hashlib.sha256(source_path.read_bytes()).hexdigest()
                    if source_path is not None and source_path.is_file()
                    else "unavailable"
                )
            except OSError:
                module_sha = "unavailable"
            payload = {
                "type": type_name,
                "module_source_sha256": module_sha,
                "state": {key: item["value"] for key, item in items.items()},
            }
            complete = (
                module_sha != "unavailable"
                and len(items) == len(state)
                and all(item["complete"] for item in items.values())
            )
            return {"value": payload, "complete": complete}
        finally:
            active.remove(identity)
    return {"value": {"type": type_name, "state": "unavailable"}, "complete": False}


def _callable_binding_identity(calculate):
    target = calculate.func if isinstance(calculate, partial) else calculate
    bindings = {}
    if isinstance(calculate, partial):
        bindings["partial_args"] = calculate.args
        bindings["partial_keywords"] = calculate.keywords or {}
    unavailable_cell = False
    if inspect.isfunction(target):
        if target.__defaults__:
            bindings["defaults"] = target.__defaults__
        if target.__kwdefaults__:
            bindings["keyword_defaults"] = target.__kwdefaults__
        # Globals referenced by the function can be mutable even when its
        # closure is empty. Include them in the digest; opaque dependencies
        # deliberately downgrade the binding scope to partial.
        try:
            global_bindings = inspect.getclosurevars(target).globals
        except (NameError, TypeError):
            global_bindings = None
            unavailable_cell = True
        if global_bindings:
            bindings["globals"] = global_bindings
        closure = target.__closure__ or ()
        if closure:
            captured = {}
            for name, cell in zip(target.__code__.co_freevars, closure, strict=True):
                try:
                    captured[name] = cell.cell_contents
                except ValueError:
                    captured[name] = None
                    unavailable_cell = True
            bindings["closure"] = captured
    elif inspect.ismethod(target) and target.__self__ is not None:
        bindings["callable_instance"] = target.__self__
    elif not isinstance(calculate, partial) and not inspect.isroutine(target):
        bindings["callable_instance"] = target
    if not bindings:
        return None
    normalized = _binding_value(bindings)
    complete = normalized["complete"] and not unavailable_cell
    return {"value": normalized["value"], "scope": "complete" if complete else "partial"}


class Registry:
    def __init__(self):
        self._models: dict[str, ModelSpec] = {}

    def register(self, spec: ModelSpec, *, replace=False):
        if spec.info.id in self._models and not replace:
            raise ValueError("model ID already registered")
        if not issubclass(spec.inputs, Contract) or not callable(spec.calculate):
            raise TypeError("a strict input contract and callable calculation are required")
        if set(spec.inputs.model_fields) != set(spec.info.input_units):
            raise ValueError("every input field requires its declared unit or semantic type")
        self._models[spec.info.id] = ModelSpec(spec.info.model_copy(deep=True), spec.inputs, spec.calculate)

    def describe(self, model_id):
        spec = self._models[model_id]
        return {"model": spec.info.model_dump(mode="json"), "input_schema": spec.inputs.model_json_schema()}

    def models(self):
        return tuple(spec.info.model_copy(deep=True) for _, spec in sorted(self._models.items()))

    def implementation_identity(self, model_id):
        """Describe implementation identity without executing the calculation."""
        spec=self._models[model_id]
        source=_model_source_identity(spec)
        return dict(model_id=model_id,model_version=spec.info.version,
                    source=source,model_source_digest=digest(source))

    def validate_inputs(self, model_id, parameters, *, json_input=False):
        """Validate the registered input contract without evaluating physics."""
        spec=self._models[model_id]
        return spec.inputs.model_validate_json(parameters) if json_input else spec.inputs.model_validate(parameters)

    def evaluate(self, model_id: str, parameters: dict | Contract, context: RunContext, *, json_input=False):
        spec = self._models[model_id]
        if not isinstance(context, RunContext):
            raise TypeError("RunContext is required")
        if json_input:
            inputs=spec.inputs.model_validate_json(parameters)
        else:
            inputs=spec.inputs.model_validate(parameters)
        runtime = {"python": platform.python_version()}
        if spec.info.dependency:
            try:
                runtime[spec.info.dependency] = version(spec.info.dependency)
            except PackageNotFoundError as error:
                raise ImportError(f"install the {spec.info.dependency} dependency for {model_id}") from error
        source_identity = _model_source_identity(spec)
        calculation = spec.calculate(inputs)
        if _model_source_identity(spec) != source_identity:
            raise ValueError("registered calculation source changed during evaluation")
        if not isinstance(calculation, Calculation):
            raise TypeError("registered calculations must return Calculation")
        if set(calculation.values) - set(spec.info.output_units):
            raise ValueError("calculation returned outputs without declared units")
        if calculation.status == "computed" and set(calculation.values) != set(spec.info.output_units):
            raise ValueError("a completed calculation must supply every declared output")
        runtime.update(source_identity)
        signature = digest({"model": spec.info, "inputs": inputs, "context": context})
        return AssessmentResult(
            model_id=model_id,
            model_version=spec.info.version,
            status=calculation.status,
            inputs=inputs.model_dump(mode="json"),
            values=calculation.values,
            input_units=spec.info.input_units,
            output_units=spec.info.output_units,
            assumptions=spec.info.assumptions,
            notes=calculation.notes,
            source_ids=spec.info.sources,
            context=context.model_copy(deep=True),
            input_digest=signature,
            model_source_digest=digest(source_identity),
            runtime=runtime,
            qualification=spec.info.qualification,
        )
