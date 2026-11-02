"""The owner's public JSON route must preserve strict nested contracts and Python compatibility."""

from types import SimpleNamespace

import pytest

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import Contract, RunContext


class NestedInput(Contract):
    points: tuple[float, ...]
    reviewed: bool


def result(model, inputs, context):
    return dict(
        schema_version="matintegrity.assessment.v1",
        status="computed",
        model_id=model,
        inputs=inputs.model_dump(mode="json"),
        context=vars(context),
        values={},
        units={},
    )


class Registry:
    def run_json(self, model, text, context):
        return result(model, NestedInput.model_validate_json(text), context)


def client():
    return MaterialClient(
        evaluator=lambda model, inputs, ctx: result(model, NestedInput.model_validate(inputs), ctx),
        context_factory=lambda **fields: SimpleNamespace(**fields),
        evidence_factory=lambda **fields: fields,
        registry_factory=Registry,
    )


CTX = RunContext(asset_id="wall", scenario_id="reference", clock_id="elapsed", origin="synthetic")


def test_json_nested_arrays_use_owner_json_validation_and_default_python_stays_strict():
    c = client()
    parameters = {"points": [1.0, 2.0], "reviewed": True}
    r = c.evaluate("nested", parameters, CTX, location_id="section", input_mode="json")
    assert r.payload["context"]["metadata"]["pipeline_input_validation_mode"] == "json"
    with pytest.raises(ValueError):
        c.evaluate("nested", parameters, CTX, location_id="section")
    r = c.evaluate("nested", {"points": (1.0, 2.0), "reviewed": True}, CTX, location_id="section")
    assert "pipeline_input_validation_mode" not in r.payload["context"]["metadata"]


@pytest.mark.parametrize(
    "parameters", [{"points": [True, 2.0], "reviewed": True}, {"points": [1.0], "reviewed": "true"}]
)
def test_json_route_does_not_coerce_booleans_numbers_or_review_flags(parameters):
    with pytest.raises(ValueError):
        client().evaluate("nested", parameters, CTX, location_id="section", input_mode="json")
