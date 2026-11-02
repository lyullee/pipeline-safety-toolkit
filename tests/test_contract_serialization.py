"""Invalid numbers must not become unknowns across shared/native JSON boundaries."""

import json
import math
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, computed_field, field_serializer, model_serializer

from pipeline_integrity.consequence_exchange import _checked as consequence_checked
from pipeline_integrity.contracts import Contract, RunContext, SignalFrame, digest, json_value
from pipeline_integrity.risk_exchange import _checked as risk_checked


def context():
    return RunContext(asset_id="pipe", scenario_id="serialization", clock_id="clock", origin="synthetic")


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("method", ["json_object", "json_string", "digest", "json_value"])
def test_cloned_metadata_nonfinite_is_rejected_before_null_conversion(invalid, method):
    value = context().model_copy(update={"metadata": {"nested": [{"invalid": invalid}]}})
    with pytest.raises(ValueError):
        if method == "json_object":
            value.model_dump(mode="json")
        elif method == "json_string":
            value.model_dump_json()
        elif method == "digest":
            digest(value)
        else:
            json_value(value)
    assert value.metadata["nested"][0]["invalid"] is invalid


@pytest.mark.parametrize("checked", [risk_checked, consequence_checked])
@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_existing_owner_adapter_roundtrips_do_not_reclassify_invalid_context(checked, invalid):
    value = context().model_copy(update={"metadata": {"invalid": invalid}})
    with pytest.raises(ValueError):
        checked(value, RunContext)


class Foreign(BaseModel):
    value: float | None


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, Decimal("NaN"), Decimal("Infinity")])
def test_foreign_native_model_is_checked_before_its_default_serializer(invalid):
    native = Foreign(value=1.0).model_copy(update={"value": invalid})
    with pytest.raises(ValueError):
        json_value({"native": native})
    with pytest.raises(ValueError):
        RunContext(
            asset_id="pipe",
            scenario_id="native",
            clock_id="clock",
            origin="synthetic",
            metadata={"native": native},
        )


def test_foreign_model_inserted_by_clone_is_not_hidden_in_parent_serializer():
    value = context().model_copy(update={"metadata": {"native": Foreign(value=math.nan)}})
    with pytest.raises(ValueError):
        value.model_dump_json()


class Generated(Contract):
    value: float

    @computed_field
    @property
    def derived(self) -> float:
        return math.inf


class Transformed(Contract):
    value: float

    @field_serializer("value")
    def generated(self, value):
        return math.nan


@pytest.mark.parametrize("cls", [Generated, Transformed])
def test_generated_nonfinite_serializer_outputs_are_rejected(cls):
    with pytest.raises(ValueError):
        cls(value=1.0).model_dump_json()


def test_python_mode_preserves_invalid_for_explicit_validation_or_diagnostics():
    value = context().model_copy(update={"metadata": {"invalid": math.nan}})
    assert math.isnan(value.model_dump(mode="python")["metadata"]["invalid"])
    with pytest.raises(ValueError):
        RunContext.model_validate(value.model_dump(mode="python"))


def test_valid_null_zero_timestamp_and_digest_remain_unchanged():
    frame = SignalFrame(
        stream_id="signal",
        clock_id="clock",
        time_s=0.0,
        timestamp_utc=datetime(2026, 10, 27, tzinfo=timezone.utc),
        values={"zero": 0.0, "unknown": None},
        units={"zero": "Pa", "unknown": "K"},
        valid={"zero": True, "unknown": False},
        origin="synthetic",
    )
    value = frame.model_dump(mode="json")
    assert value["values"] == {"zero": 0.0, "unknown": None}
    assert value["timestamp_utc"] == "2026-10-27T00:00:00Z"
    assert json.loads(frame.model_dump_json()) == value
    assert digest(frame) == digest(value)


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    value: float | None
    sequence: tuple[float, ...]
    stamp: datetime


class Guarded(Contract):
    value: float | None
    sequence: tuple[float, ...]
    stamp: datetime


def test_valid_native_serialization_and_schema_are_preserved():
    data = dict(value=None, sequence=(0.0, 1.5), stamp=datetime(2026, 10, 27, tzinfo=timezone.utc))
    before, after = Reference(**data), Guarded(**data)
    assert before.model_dump_json() == after.model_dump_json()
    assert before.model_dump(mode="json") == after.model_dump(mode="json")
    for mode in ("validation", "serialization"):
        original, guarded = Reference.model_json_schema(mode=mode), Guarded.model_json_schema(mode=mode)
        original.pop("title")
        guarded.pop("title")
        assert original == guarded


def test_shared_subtree_is_not_misclassified_as_cycle_but_actual_cycle_is_rejected():
    branch = {"value": 1.0}
    assert json_value([branch, branch]) == [{"value": 1.0}, {"value": 1.0}]
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="cyclic"):
        json_value(cycle)


def test_nonfinite_dictionary_keys_are_not_coerced_to_ordinary_text():
    with pytest.raises(ValueError):
        context().model_copy(update={"metadata": {math.nan: "value"}}).model_dump_json()


class ForeignJSONField(BaseModel):
    value: float

    @field_serializer("value", when_used="json")
    def transformed(self, value):
        return math.nan


class ForeignJSONModel(BaseModel):
    value: float

    @model_serializer(when_used="json")
    def transformed(self):
        return {"value": math.inf}


@pytest.mark.parametrize("cls", [ForeignJSONField, ForeignJSONModel])
def test_foreign_json_only_generated_nonfinite_cannot_be_censored_and_owner_is_unchanged(cls):
    value = cls(value=1.0)
    serializer, schema = cls.__pydantic_serializer__, cls.__pydantic_core_schema__
    assert value.model_dump(mode="python") == {"value": 1.0}
    assert value.model_dump(mode="json") == {"value": None}
    with pytest.raises(ValueError):
        json_value({"native": value})
    with pytest.raises(ValueError):
        context().model_copy(update={"metadata": {"native": value}}).model_dump_json()
    assert cls.__pydantic_serializer__ is serializer and cls.__pydantic_core_schema__ is schema
    assert value.model_dump(mode="json") == {"value": None}
