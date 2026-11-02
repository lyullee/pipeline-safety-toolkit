"""Registered callable/module provenance checks; execution deferred to Nurion."""

from functools import partial
from importlib.util import module_from_spec, spec_from_file_location

import pytest

from pipeline_integrity.contracts import Calculation, Contract, ModelInfo, RunContext
from pipeline_integrity.registry import ModelSpec, Registry


class Input(Contract):
    value_m: float


GLOBAL_BINDING_STATE = {"scale": 2.0}


def spec(calculate):
    return ModelSpec(
        ModelInfo(
            id="authored.helper",
            version="1",
            title="Authored helper",
            category="custom",
            input_units={"value_m": "m"},
            output_units={"double_m": "m"},
            sources=("authored",),
            assumptions=("demonstration only",),
            implementation="user_model",
            qualification="user_model",
        ),
        Input,
        calculate,
    )


def context():
    return RunContext(asset_id="authored", scenario_id="authored", clock_id="execution", origin="synthetic")


def test_same_public_function_detects_helper_module_change(tmp_path):
    path = tmp_path / "authored_model.py"
    source = 'from pipeline_integrity.contracts import Calculation\ndef helper(v):\n    return v*2\ndef calculate(p):\n    return Calculation(values={"double_m":helper(p.value_m)})\n'
    path.write_text(source, encoding="utf-8")
    module_spec = spec_from_file_location("authored_registered_module", path)
    module = module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    engine = Registry()
    engine.register(spec(module.calculate))
    before = engine.evaluate("authored.helper", {"value_m": 1.0}, context())
    path.write_text(source.replace("return v*2", "return v*3"), encoding="utf-8")
    after = engine.evaluate("authored.helper", {"value_m": 1.0}, context())
    assert before.runtime["model_callable_source_sha256"] == after.runtime["model_callable_source_sha256"]
    assert before.runtime["model_module_source_sha256"] != after.runtime["model_module_source_sha256"]
    assert before.model_source_digest != after.model_source_digest


def test_partial_callable_binding_changes_source_fingerprint():
    def calculate(parameters, scale):
        return Calculation(values={"double_m": scale * parameters.value_m})

    first, second = Registry(), Registry()
    first.register(spec(partial(calculate, scale=2.0)))
    second.register(spec(partial(calculate, scale=3.0)))
    before = first.evaluate("authored.helper", {"value_m": 1.0}, context())
    after = second.evaluate("authored.helper", {"value_m": 1.0}, context())
    assert before.runtime["callable_binding_scope"] == "partial"
    assert before.runtime["callable_binding_sha256"] != after.runtime["callable_binding_sha256"]
    assert before.model_source_digest != after.model_source_digest


def test_closure_binding_changes_source_fingerprint():
    def build(scale):
        def calculate(parameters):
            return Calculation(values={"double_m": scale * parameters.value_m})

        return calculate

    first, second = Registry(), Registry()
    first.register(spec(build(2.0)))
    second.register(spec(build(3.0)))
    before = first.evaluate("authored.helper", {"value_m": 1.0}, context())
    after = second.evaluate("authored.helper", {"value_m": 1.0}, context())
    assert before.runtime["callable_binding_scope"] == "partial"
    assert before.model_source_digest != after.model_source_digest


def test_bound_method_instance_state_changes_source_fingerprint():
    class Calculator:
        def __init__(self, scale):
            self.scale = scale

        def calculate(self, parameters):
            return Calculation(values={"double_m": self.scale * parameters.value_m})

    first, second = Registry(), Registry()
    first.register(spec(Calculator(2.0).calculate))
    second.register(spec(Calculator(3.0).calculate))
    before = first.evaluate("authored.helper", {"value_m": 1.0}, context())
    after = second.evaluate("authored.helper", {"value_m": 1.0}, context())
    assert before.runtime["callable_binding_sha256"] != after.runtime["callable_binding_sha256"]
    assert before.model_source_digest != after.model_source_digest


def test_mutating_captured_state_during_evaluation_is_rejected():
    state = {"scale": 2.0}

    def calculate(parameters):
        result = Calculation(values={"double_m": state["scale"] * parameters.value_m})
        state["scale"] = 3.0
        return result

    engine = Registry()
    engine.register(spec(calculate))
    with pytest.raises(ValueError, match="source changed"):
        engine.evaluate("authored.helper", {"value_m": 1.0}, context())


def test_mutating_referenced_global_during_evaluation_is_rejected():
    GLOBAL_BINDING_STATE["scale"] = 2.0

    def calculate(parameters):
        result = Calculation(values={"double_m": GLOBAL_BINDING_STATE["scale"] * parameters.value_m})
        GLOBAL_BINDING_STATE["scale"] = 3.0
        return result

    engine = Registry()
    engine.register(spec(calculate))
    with pytest.raises(ValueError, match="source changed"):
        engine.evaluate("authored.helper", {"value_m": 1.0}, context())


def test_source_identity_change_during_evaluation_rejected(monkeypatch):
    import pipeline_integrity.registry as owner

    identities = iter(({"source": "before"}, {"source": "after"}))
    monkeypatch.setattr(owner, "_model_source_identity", lambda s: next(identities))
    engine = Registry()
    engine.register(spec(lambda p: Calculation(values={"double_m": 2 * p.value_m})))
    with pytest.raises(ValueError, match="source changed"):
        engine.evaluate("authored.helper", {"value_m": 1.0}, context())


def test_source_unavailable_is_explicit_without_inventing_a_module_pin(monkeypatch):
    import pipeline_integrity.registry as owner

    monkeypatch.setattr(owner.inspect, "getsourcefile", lambda target: None)
    engine = Registry()
    engine.register(spec(lambda p: Calculation(values={"double_m": 2 * p.value_m})))
    result = engine.evaluate("authored.helper", {"value_m": 1.0}, context())
    assert result.runtime["model_module_source_sha256"] == "unavailable"
    assert "without_module_bytes" in result.runtime["model_source_scope"]
    assert result.runtime["model_source_scope"].endswith("callable_binding_partial")
    assert result.runtime["source_fingerprint_schema"] == "pipeline_integrity.model_source.v3"
