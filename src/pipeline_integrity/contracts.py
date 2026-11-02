"""Strict quantities and traceable calculation boundaries."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from numbers import Real
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator
from pydantic_core import SchemaSerializer

Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Name = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


def _foreign_json(value):
    # A private serializer instance retains generated nonfinite numbers until the
    # caller checks them.  The owner class/schema/serializer is never modified.
    schema = deepcopy(type(value).__pydantic_core_schema__)

    def configure(node):
        if isinstance(node, dict):
            if node.get("type") == "model":
                node["config"] = dict(node.get("config", {}), ser_json_inf_nan="constants")
            for child in node.values():
                configure(child)
        elif isinstance(node, (tuple, list)):
            for child in node:
                configure(child)

    configure(schema)
    return SchemaSerializer(schema, config={"ser_json_inf_nan": "constants"}).to_python(value, mode="json")


def _finite_tree(value, active=None):
    """Inspect raw fields before a JSON serializer can replace nonfinite numbers."""
    # Most FE result leaves are ordinary floats or text. Avoid expensive numeric
    # ABC and container checks for these leaves; subclasses still use the strict
    # general path below (including Decimal and foreign numeric scalar types).
    kind = type(value)
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite numbers cannot be serialized as missing values")
        return
    if kind is str or kind is int or kind is bool or value is None:
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite numbers cannot be serialized as missing values")
    elif isinstance(value, Real) and not isinstance(value, (int, bool)):
        if not math.isfinite(value):
            raise ValueError("nonfinite numbers cannot be serialized as missing values")
    elif isinstance(value, (BaseModel, Mapping, tuple, list, set, frozenset)):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic values cannot cross the JSON boundary")
        active.add(identity)
        try:
            if isinstance(value, BaseModel):
                for name in type(value).model_fields:
                    if name in value.__dict__:
                        _finite_tree(value.__dict__[name], active)
                _finite_tree(value.__pydantic_extra__, active)
                if not isinstance(value, Contract):
                    _finite_tree(_foreign_json(value), active)
            elif isinstance(value, Mapping):
                for key, item in value.items():
                    _finite_tree(key, active)
                    _finite_tree(item, active)
            else:
                for item in value:
                    _finite_tree(item, active)
        finally:
            active.remove(identity)


def json_value(value: Any):
    _finite_tree(value)

    def normalize(item):
        if isinstance(item, BaseModel):
            item = item.model_dump(mode="json") if isinstance(item, Contract) else _foreign_json(item)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        return item

    result = normalize(value)
    json.dumps(result, allow_nan=False)
    return result


def digest(value: Any) -> str:
    encoded = json.dumps(
        json_value(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, ser_json_inf_nan="constants"
    )

    @model_serializer(mode="wrap")
    def finite_json(self, handler, info):
        if info.mode == "json":
            _finite_tree(self)
        result = handler(self)
        if info.mode == "json":
            _finite_tree(result)
        return result


class EvidenceRef(Contract):
    id: Name
    citation: Name
    origin: Literal["measured", "laboratory", "simulation", "literature", "user_declared", "synthetic"]
    locator: str = ""
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    license_id: str | None = None
    limitations: tuple[str, ...] = ()


class RunContext(Contract):
    asset_id: Name
    scenario_id: Name
    clock_id: Name
    origin: Literal["measured", "laboratory", "simulation", "user_declared", "synthetic"]
    evidence: tuple[EvidenceRef, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def serializable_metadata(cls, value):
        return json_value(value)


class Calculation(Contract):
    values: dict[str, Any]
    status: Literal["computed", "out_of_scope", "needs_data"] = "computed"
    notes: tuple[str, ...] = ()

    @field_validator("values")
    @classmethod
    def serializable_values(cls, value):
        return json_value(value)


def require_context_extension(source: RunContext, target: RunContext) -> bool:
    """Permit downstream review evidence while preserving the original case context."""
    original=RunContext.model_validate(source.model_dump(mode="python"))
    downstream=RunContext.model_validate(target.model_dump(mode="python"))
    if (original.asset_id,original.scenario_id,original.clock_id,original.origin)!=(
        downstream.asset_id,downstream.scenario_id,downstream.clock_id,downstream.origin):
        raise ValueError("downstream context changes the original asset, scenario, clock or origin")
    before={e.id:e.model_dump(mode="json") for e in original.evidence}
    after={e.id:e.model_dump(mode="json") for e in downstream.evidence}
    if len(before)!=len(original.evidence) or len(after)!=len(downstream.evidence):
        raise ValueError("context extension requires unambiguous evidence IDs")
    if any(identity not in after or digest(value)!=digest(after[identity]) for identity,value in before.items()):
        raise ValueError("downstream context changes or removes original evidence")
    if any(key not in downstream.metadata or digest(value)!=digest(downstream.metadata[key])
           for key,value in original.metadata.items()):
        raise ValueError("downstream context changes or removes original metadata")
    return True


class ModelInfo(Contract):
    id: Name
    version: Name
    title: Name
    category: Name
    input_units: dict[str, str]
    output_units: dict[str, str]
    sources: tuple[str, ...]
    assumptions: tuple[str, ...]
    dependency: str | None = None
    implementation: Literal["native", "library_adapter", "external_adapter", "user_model"]
    qualification: Literal["engineering_screening", "user_model"] = "engineering_screening"


class AssessmentResult(Contract):
    schema_version: Literal["pipeline_integrity.result.v1"] = "pipeline_integrity.result.v1"
    model_id: Name
    model_version: Name
    status: Literal["computed", "out_of_scope", "needs_data"]
    inputs: dict[str, Any]
    values: dict[str, Any]
    input_units: dict[str, str]
    output_units: dict[str, str]
    assumptions: tuple[str, ...]
    notes: tuple[str, ...]
    source_ids: tuple[str, ...]
    context: RunContext
    input_digest: Name
    model_source_digest: Name
    runtime: dict[str, str]
    qualification: Literal["engineering_screening", "user_model"]


class Pressure(Contract):
    value: float
    unit: Literal["Pa", "kPa", "MPa", "bar", "psi"]
    reference: Literal["absolute", "gauge", "differential"]
    ambient_pa_abs: Positive | None = None

    @model_validator(mode="after")
    def reference_required(self):
        if self.reference == "gauge" and self.ambient_pa_abs is None:
            raise ValueError("gauge pressure needs an explicit atmospheric reference")
        if self.reference != "gauge" and self.ambient_pa_abs is not None:
            raise ValueError("atmospheric reference belongs to gauge-pressure conversion")
        return self

    def pascals(self):
        scales = {"Pa": 1.0, "kPa": 1000.0, "MPa": 1e6, "bar": 1e5, "psi": 6894.757293168}
        return self.value * scales[self.unit]

    def absolute_pa(self):
        if self.reference == "differential":
            raise ValueError("a pressure difference is not an absolute process state")
        value = self.pascals() + (self.ambient_pa_abs if self.reference == "gauge" else 0.0)
        if value <= 0:
            raise ValueError("absolute pressure must be positive")
        return value


class FlowBoundary(Contract):
    schema_version: Literal["pipeline_integrity.flow_boundary.v1"] = "pipeline_integrity.flow_boundary.v1"
    component_id: Name
    port_id: Name
    clock_id: Name
    time_s: Nonnegative
    direction: Literal["into_component", "out_of_component"]
    fluid: Name
    property_basis: Name
    pressure_pa_abs: Positive
    temperature_k: Positive
    mass_flow_kg_s: Nonnegative
    specific_enthalpy_j_kg: float
    phase: Literal["liquid", "gas", "supercritical", "two_phase"]
    vapour_mass_fraction: Fraction | None = None
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def resolved_phase(self):
        if self.phase == "two_phase" and self.vapour_mass_fraction is None:
            raise ValueError("two-phase source needs a resolved vapour mass fraction")
        if self.phase != "two_phase" and self.vapour_mass_fraction is not None:
            raise ValueError("mass quality belongs to a two-phase boundary")
        if not self.evidence_ids:
            raise ValueError("a boundary must retain its model or observation evidence")
        return self


class SignalFrame(Contract):
    schema_version: Literal["pipeline_integrity.signal_frame.v1"] = "pipeline_integrity.signal_frame.v1"
    stream_id: Name
    clock_id: Name
    time_s: Nonnegative
    timestamp_utc: datetime | None = None
    values: dict[str, float | None]
    units: dict[str, str]
    valid: dict[str, bool]
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    operating_mode: str | None = None

    @model_validator(mode="after")
    def aligned(self):
        if not self.values or set(self.values) != set(self.units) or set(self.values) != set(self.valid):
            raise ValueError("values, units and quality must identify the same nonempty channels")
        if any(ok and self.values[name] is None for name, ok in self.valid.items()):
            raise ValueError("a missing channel cannot be valid")
        if self.timestamp_utc is not None and self.timestamp_utc.utcoffset() is None:
            raise ValueError("an absolute timestamp needs an explicit timezone")
        return self
