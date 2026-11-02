"""Pass bounded pressure histories to the existing material-owned fatigue workflow."""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import ExternalResult, MaterialClient
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .transient import LiquidTransient

Origin = Literal["measured", "laboratory", "simulation", "user_declared", "synthetic"]


class PressureHistory(Contract):
    asset_id: Name
    location_id: Name
    scenario_id: Name
    clock_id: Name
    time_s: tuple[Nonnegative, ...] = Field(min_length=3, max_length=1000000)
    pressure_pa: tuple[float | None, ...]
    pressure_reference: Literal["absolute", "gauge", "unspecified"]
    ambient_pressure_pa_abs: Positive | None = None
    temperature_k: tuple[Positive | None, ...]
    valid: tuple[bool, ...]
    pressure_origin: Origin
    temperature_origin: Origin
    source_id: Name
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    temperature_evidence_id: Name
    binding_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def alignment(self):
        if len({len(self.time_s), len(self.pressure_pa), len(self.temperature_k), len(self.valid)}) != 1:
            raise ValueError("pressure, temperature, validity and time must have the same length")
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("operating samples require a strictly increasing elapsed clock")
        if self.pressure_reference != "gauge" and self.ambient_pressure_pa_abs is not None:
            raise ValueError("an ambient conversion pressure belongs only to gauge observations")
        expected = digest(self.model_dump(mode="json", exclude={"binding_digest"}))
        if self.binding_digest is None:
            object.__setattr__(self, "binding_digest", expected)
        elif self.binding_digest != expected:
            raise ValueError("the pressure-history receipt has been changed")
        return self


class FatigueGeometry(Contract):
    mean_radius_m: Positive
    wall_thickness_m: Positive
    maximum_thickness_to_radius_ratio: Positive = Field(le=0.1)
    evidence_id: Name


class FatigueCurve(Contract):
    reference_stress_range_pa: Positive
    reference_cycles: Positive
    basquin_exponent: Positive
    minimum_stress_range_pa: Positive
    maximum_stress_range_pa: Positive
    minimum_temperature_k: Positive
    maximum_temperature_k: Positive
    minimum_stress_ratio: float
    maximum_stress_ratio: float = Field(lt=1)
    basis: Literal["synthetic_reference", "caller_calibrated"]
    evidence_id: Name

    @model_validator(mode="after")
    def bounds(self):
        if any(
            high < low
            for low, high in (
                (self.minimum_stress_range_pa, self.maximum_stress_range_pa),
                (self.minimum_temperature_k, self.maximum_temperature_k),
                (self.minimum_stress_ratio, self.maximum_stress_ratio),
            )
        ):
            raise ValueError("fatigue calibration intervals must be ordered")
        return self


class PressureFatigueRequest(Contract):
    history: PressureHistory
    geometry: FatigueGeometry
    curve: FatigueCurve
    external_pressure_pa_abs: Positive
    external_pressure_evidence_id: Name
    initial_damage: Nonnegative
    initial_damage_evidence_id: Name
    declared_damage_limit: Positive
    damage_limit_evidence_id: Name
    maximum_samples: int = Field(default=100000, ge=3, le=1000000)


class PressureFatigueResult(Contract):
    schema_version: Literal["pipeline_integrity.pressure_fatigue.v1"] = (
        "pipeline_integrity.pressure_fatigue.v1"
    )
    status: Name
    context: RunContext
    request_digest: Name
    history_digest: Name
    source_sha256: Name
    adapter_sha256: Name
    pressure_origin: Origin
    temperature_origin: Origin
    curve_basis: Literal["synthetic_reference", "caller_calibrated"]
    native_history_origin: Literal["measured", "model_prediction"] | None
    native_result: ExternalResult | None
    values: dict
    notes: tuple[str, ...]
    result_origin: Literal["calculated_screening"] = "calculated_screening"
    field_validation: Literal[False] = False
    failure_probability_calculated: Literal[False] = False
    result_digest: Name

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the fatigue result receipt has been changed")
        if (
            self.native_result is not None
            and digest(self.native_result.payload) != self.native_result.payload_digest
        ):
            raise ValueError("the original material result has been changed")


def _result(request, context, *, status, notes, native=None, native_origin=None):
    values = {} if native is None else native.payload["values"]
    data = dict(
        status=status,
        context=context.model_dump(mode="json"),
        request_digest=digest(request),
        history_digest=request.history.binding_digest,
        source_sha256=request.history.source_sha256,
        adapter_sha256=file_digest(Path(__file__)),
        pressure_origin=request.history.pressure_origin,
        temperature_origin=request.history.temperature_origin,
        curve_basis=request.curve.basis,
        native_history_origin=native_origin,
        native_result=None if native is None else native.model_dump(mode="json"),
        values=values,
        notes=notes,
    )
    # Defaults form part of the sealed receipt, including its non-probability meaning.
    provisional = PressureFatigueResult.model_validate_json(json.dumps({**data, "result_digest": "pending"}))
    sealed = provisional.model_dump(mode="json", exclude={"result_digest"})
    return PressureFatigueResult.model_validate_json(json.dumps({**sealed, "result_digest": digest(sealed)}))


def assess_pressure_fatigue(request: PressureFatigueRequest, context: RunContext, *, client=None):
    """Keep missing references and gaps unresolved; delegate all fatigue mathematics."""
    request = PressureFatigueRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    h, g, c = request.history, request.geometry, request.curve
    if (h.asset_id, h.scenario_id, h.clock_id) != (context.asset_id, context.scenario_id, context.clock_id):
        raise ValueError("operating history and assessment context identities or clocks differ")
    if len(h.time_s) > request.maximum_samples:
        raise ValueError("operating history exceeds the declared assessment budget")
    evidence = {row.id: row for row in context.evidence}
    required = {
        h.source_id,
        h.temperature_evidence_id,
        g.evidence_id,
        c.evidence_id,
        request.external_pressure_evidence_id,
        request.initial_damage_evidence_id,
        request.damage_limit_evidence_id,
    }
    if len(evidence) != len(context.evidence) or required - evidence.keys():
        raise ValueError("history, geometry, curve and damage assumptions require unique context evidence")
    if evidence[h.source_id].sha256 not in (None, h.source_sha256):
        raise ValueError("pressure-history source hash contradicts its evidence")
    missing = []
    if h.pressure_reference == "unspecified":
        missing.append(
            "Pressure reference is unresolved; bar/Pa alone does not identify absolute or gauge pressure."
        )
    if h.pressure_reference == "gauge" and h.ambient_pressure_pa_abs is None:
        missing.append("Gauge observations need their atmospheric conversion pressure.")
    if not all(h.valid) or any(v is None for v in h.pressure_pa + h.temperature_k):
        missing.append("Incomplete samples need reviewed segmentation; counting across gaps is disabled.")
    if missing:
        return _result(request, context, status="needs_data", notes=tuple(missing))
    pressures = [v + (h.ambient_pressure_pa_abs or 0.0) for v in h.pressure_pa]
    if any(p <= 0 or not math.isfinite(p) for p in pressures):
        raise ValueError("converted absolute operating pressures must be finite and positive")
    native_origin = (
        "measured"
        if {h.pressure_origin, h.temperature_origin} <= {"measured", "laboratory"}
        else "model_prediction"
    )
    parameters = dict(
        history=dict(
            schema_version="matintegrity.operating_history.v1",
            asset_id=h.asset_id,
            location_id=h.location_id,
            clock_id=h.clock_id,
            origin=native_origin,
            time_s=list(h.time_s),
            pressure_absolute_Pa=pressures,
            temperature_K=list(h.temperature_k),
            valid=list(h.valid),
            source_digest=h.binding_digest,
        ),
        external_absolute_pressure_Pa=request.external_pressure_pa_abs,
        mean_radius_m=g.mean_radius_m,
        wall_thickness_m=g.wall_thickness_m,
        maximum_thickness_to_radius_ratio=g.maximum_thickness_to_radius_ratio,
        reference_stress_range_Pa=c.reference_stress_range_pa,
        reference_cycles=c.reference_cycles,
        basquin_exponent=c.basquin_exponent,
        minimum_calibrated_stress_range_Pa=c.minimum_stress_range_pa,
        maximum_calibrated_stress_range_Pa=c.maximum_stress_range_pa,
        minimum_calibrated_temperature_K=c.minimum_temperature_k,
        maximum_calibrated_temperature_K=c.maximum_temperature_k,
        minimum_calibrated_stress_ratio=c.minimum_stress_ratio,
        maximum_calibrated_stress_ratio=c.maximum_stress_ratio,
        initial_damage=request.initial_damage,
        declared_damage_limit=request.declared_damage_limit,
    )
    native = (MaterialClient() if client is None else client).evaluate(
        "workflow.pressure_fatigue", parameters, context, location_id=h.location_id
    )
    native = ExternalResult.model_validate_json(native.model_dump_json())
    payload = native.payload
    if (
        native.owner != "matintegrity"
        or native.owner_schema != "matintegrity.assessment.v1"
        or native.payload_digest != digest(payload)
        or native.status != payload["status"]
        or native.asset_id != h.asset_id
        or payload["model_id"] != "workflow.pressure_fatigue"
        or payload["inputs"] != json_value(parameters)
        or payload["context"]["asset_id"] != h.asset_id
        or payload["context"]["location_id"] != h.location_id
        or payload["context"]["time_basis"] != h.clock_id
    ):
        raise ValueError("the material response changed the history, parameters, context or result identity")
    if native.status == "computed":
        v = payload["values"]
        increment, total, margin = (v[key] for key in ("damage_increment", "total_damage", "damage_margin"))
        if (
            any(isinstance(x, bool) or not math.isfinite(x) for x in (increment, total, margin))
            or increment < 0
            or v["initial_damage"] != request.initial_damage
            or not math.isclose(total, request.initial_damage + increment, rel_tol=1e-10, abs_tol=1e-14)
            or not math.isclose(margin, request.declared_damage_limit - total, rel_tol=1e-10, abs_tol=1e-14)
            or v["history_source_digest"] != h.binding_digest
        ):
            raise ValueError("the original damage ledger is inconsistent")
    return _result(
        request,
        context,
        status=native.status,
        native=native,
        native_origin=native_origin,
        notes=(
            "Original material workflow retained; residual half cycles are neither deleted nor periodically repeated.",
            "Declared geometry, curve and temperature domains define screening applicability, not field validation.",
            "Cumulative fatigue damage is not a failure probability or an observed release frequency.",
        ),
    )


def pressure_history_from_transient(
    result: dict,
    specification: LiquidTransient,
    *,
    pipe_id: str,
    endpoint: Literal["start", "end"],
    temperature_k: float,
    temperature_evidence_id: str,
    source_id: str,
):
    """Convert piezometric head at one accepted pipe endpoint without adding a thermal model."""
    specification = LiquidTransient.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(json.dumps(result["context"]))
    if (
        endpoint not in {"start", "end"}
        or result.get("schema") != "pipeline_integrity.liquid_transient.v1"
        or result.get("status") != "computed"
        or result.get("specification_digest") != digest(specification)
        or result.get("context_digest") != digest(context)
        or not result.get("all_volume_balances_within_tolerance")
        or not result.get("volume_balance", {}).get("within_tolerance")
    ):
        raise ValueError(
            "fatigue needs the computed, balanced source transient and its original specification"
        )
    pipes = {p.id: p for p in specification.pipes}
    nodes = {n.id: n for n in specification.nodes}
    if pipe_id not in pipes or pipe_id not in result["pipes"]:
        raise ValueError("select an existing pipe endpoint")
    pipe = pipes[pipe_id]
    node = nodes[pipe.start_node if endpoint == "start" else pipe.end_node]
    heads = result["pipes"][pipe_id][f"{endpoint}_head_m"]
    return PressureHistory(
        asset_id=context.asset_id,
        location_id=f"{pipe_id}:{endpoint}",
        scenario_id=context.scenario_id,
        clock_id=context.clock_id,
        time_s=tuple(result["time_s"]),
        pressure_pa=tuple(
            specification.ambient_pressure_pa_abs
            + specification.density_kg_m3 * specification.gravity_m_s2 * (h - node.elevation_m)
            for h in heads
        ),
        pressure_reference="absolute",
        temperature_k=tuple(temperature_k for _ in heads),
        valid=tuple(True for _ in heads),
        pressure_origin="simulation",
        temperature_origin="user_declared",
        source_id=source_id,
        source_sha256=digest(result),
        temperature_evidence_id=temperature_evidence_id,
    )
