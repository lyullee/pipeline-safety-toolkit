"""Explicit exchange with application-owned model classes and calculation engines."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .contracts import Contract, FlowBoundary, Name, RunContext, SignalFrame, digest, json_value


@dataclass(frozen=True)
class PortHandoff:
    native: Any
    boundary: FlowBoundary
    context: RunContext
    owner: str

    def to_dict(self):
        return {
            "schema": "pipeline_integrity.port_handoff.v1",
            "owner": self.owner,
            "native_payload": json_value(self.native),
            "boundary": self.boundary.model_dump(mode="json"),
            "context": self.context.model_dump(mode="json"),
            "boundary_digest": digest(self.boundary),
            "clock_and_property_basis_retained": True,
            "consequence_or_frequency_calculated": False,
        }


def lh2dt_state(
    boundary: FlowBoundary, *, properties, expected_property_basis: str, temperature_tolerance_k=1e-5
):
    """Resolve the owner's P/h state and reject a temperature, phase or energy-reference mismatch."""
    if boundary.fluid not in {"Hydrogen", "ParaHydrogen"} or properties.fluid != boundary.fluid:
        raise ValueError("the hydrogen equipment adapter requires an identical explicit fluid")
    if boundary.property_basis != expected_property_basis or not expected_property_basis.strip():
        raise ValueError("property reference bases differ")
    if (
        isinstance(temperature_tolerance_k, bool)
        or not math.isfinite(temperature_tolerance_k)
        or temperature_tolerance_k <= 0
    ):
        raise ValueError("provide a positive finite temperature tolerance")
    state = properties.from_ph(boundary.pressure_pa_abs, boundary.specific_enthalpy_j_kg)
    if not math.isclose(
        state.temperature_K, boundary.temperature_k, rel_tol=0, abs_tol=temperature_tolerance_k
    ):
        raise ValueError("the receiving P/h state contradicts the supplied temperature or energy reference")
    phases = {
        "gas": {"gas"},
        "liquid": {"liquid"},
        "two_phase": {"twophase"},
        "supercritical": {"supercritical", "supercritical_gas", "supercritical_liquid"},
    }
    if state.phase not in phases[boundary.phase]:
        raise ValueError("the receiving state contradicts the declared phase")
    if boundary.phase == "two_phase" and (
        state.quality is None or not math.isclose(state.quality, boundary.vapour_mass_fraction, abs_tol=1e-7)
    ):
        raise ValueError("the receiving quality contradicts the boundary")
    return state


def prism_port_state(
    boundary: FlowBoundary,
    context: RunContext,
    *,
    density_kg_m3: float,
    position_enu_m: tuple[float, float, float],
    direction_enu: tuple[float, float, float],
    entropy_j_kg_k: float | None = None,
    factory=None,
):
    """Construct the actual PRISM equipment-port contract, retaining absent target metadata in an envelope."""
    if boundary.clock_id != context.clock_id or boundary.component_id != context.asset_id:
        raise ValueError("port and context identities or clocks differ")
    if boundary.direction != "out_of_component":
        raise ValueError("an atmospheric-source handoff requires an outward equipment port")
    phases = {"liquid": "liquid", "gas": "vapour", "two_phase": "two_phase"}
    if boundary.fluid not in {"Hydrogen", "ParaHydrogen"} or boundary.phase not in phases:
        raise ValueError("the inspected PRISM port schema does not support this fluid/phase")
    if factory is None:
        from lh2_tno.equipment_boundary import ProviderPortState

        factory = ProviderPortState
    native = factory(
        component_id=boundary.component_id,
        port_id=boundary.port_id,
        time_s=boundary.time_s,
        fluid=boundary.fluid,
        phase=phases[boundary.phase],
        pressure_pa_abs=boundary.pressure_pa_abs,
        temperature_k=boundary.temperature_k,
        density_kg_m3=density_kg_m3,
        specific_enthalpy_j_kg=boundary.specific_enthalpy_j_kg,
        specific_entropy_j_kgk=entropy_j_kg_k,
        vapour_quality=boundary.vapour_mass_fraction,
        mass_flow_kg_s=boundary.mass_flow_kg_s,
        position_enu_m=position_enu_m,
        direction_enu=direction_enu,
        provenance=f"pipeline context {digest(context)}; clock {boundary.clock_id}; basis {boundary.property_basis}",
        source_digest=digest(boundary),
    )
    if native.schema_version != "prism.provider_port_state.v1":
        raise ValueError("unexpected receiving port schema")
    return PortHandoff(
        native=native,
        boundary=boundary.model_copy(deep=True),
        context=context.model_copy(deep=True),
        owner="PRISM",
    )


class ExternalResult(Contract):
    schema_version: Literal["pipeline_integrity.external_result.v1"] = "pipeline_integrity.external_result.v1"
    owner: Name
    owner_schema: Name
    status: Name
    asset_id: Name
    payload: dict[str, Any]
    payload_digest: Name
    field_or_protection_qualification: Literal[False] = False


class MaterialClient:
    """Call the material project's API while preserving its original result envelope."""

    def __init__(
        self,
        *,
        evaluator: Callable | None = None,
        context_factory=None,
        evidence_factory=None,
        registry_factory=None,
    ):
        if evaluator is None:
            import matintegrity
            from matintegrity.contracts import Evidence
            from matintegrity.contracts import RunContext as MaterialContext
            from matintegrity.registry import default_registry

            evaluator = matintegrity.evaluate
            context_factory = MaterialContext
            evidence_factory = Evidence
            registry_factory = default_registry if registry_factory is None else registry_factory
        if context_factory is None or evidence_factory is None:
            raise ValueError("a supplied evaluator needs its actual context and evidence factories")
        self.evaluator = evaluator
        self.context_factory = context_factory
        self.evidence_factory = evidence_factory
        self.registry_factory = registry_factory

    def models(self):
        if self.registry_factory is None:
            raise ValueError("provide the owner's actual registry factory for model discovery")
        names = tuple(self.registry_factory().list_models())
        if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != len(
            names
        ):
            raise ValueError("the owner registry returned invalid or duplicate model identifiers")
        return names

    def describe(self, model_id: str):
        if self.registry_factory is None:
            raise ValueError("provide the owner's actual registry factory for model discovery")
        description = json_value(self.registry_factory().describe(model_id))
        if description.get("model_id") != model_id:
            raise ValueError("the owner schema changed the requested model identity")
        return description

    def evaluate(
        self,
        model_id: str,
        parameters: dict,
        context: RunContext,
        *,
        location_id: str,
        input_mode: Literal["python", "json"] = "python",
    ):
        if input_mode not in {"python", "json"}:
            raise ValueError("choose strict Python or strict JSON material input validation")
        if input_mode == "json" and self.registry_factory is None:
            raise ValueError("strict JSON evaluation needs the owner's registry run_json API")
        origins = {
            "measured": "measured",
            "laboratory": "measured",
            "simulation": "model_prediction",
            "literature": "user_declared",
            "user_declared": "user_declared",
            "synthetic": "synthetic",
        }
        evidence = tuple(
            self.evidence_factory(
                source_id=row.id,
                origin=origins[row.origin],
                citation=row.citation,
                license_id=row.license_id,
                sha256=row.sha256,
            )
            for row in context.evidence
        )
        external_context = self.context_factory(
            asset_id=context.asset_id,
            location_id=location_id,
            time_basis=context.clock_id,
            evidence=evidence,
            metadata={
                "pipeline_scenario_id": context.scenario_id,
                "pipeline_origin": context.origin,
                "pipeline_evidence_origins": {row.id: row.origin for row in context.evidence},
                "pipeline_context_digest": digest(context),
                **({"pipeline_input_validation_mode": "json"} if input_mode == "json" else {}),
            },
        )
        result = (
            self.registry_factory().run_json(
                model_id,
                json.dumps(json_value(parameters), ensure_ascii=False, allow_nan=False),
                external_context,
            )
            if input_mode == "json"
            else self.evaluator(model_id, parameters, external_context)
        )
        payload = json_value(result)
        if payload.get("schema_version") != "matintegrity.assessment.v1":
            raise ValueError("unexpected material result contract")
        if (
            payload["context"]["asset_id"] != context.asset_id
            or payload["context"]["location_id"] != location_id
        ):
            raise ValueError("material result changed the physical identity")
        return ExternalResult(
            owner="matintegrity",
            owner_schema=payload["schema_version"],
            status=payload["status"],
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


def _aligned_frames(frames):
    frames = tuple(frames)
    if not frames or any(not isinstance(frame, SignalFrame) for frame in frames):
        raise ValueError("a nonempty sequence of SignalFrame objects is required")
    first = frames[0]
    channels = tuple(first.values)
    if any(
        frame.clock_id != first.clock_id
        or frame.stream_id != first.stream_id
        or frame.units != first.units
        or tuple(frame.values) != channels
        or frame.origin != first.origin
        for frame in frames
    ):
        raise ValueError("frame series must preserve clock, stream, channel order, units and origin")
    if any(b.time_s <= a.time_s for a, b in zip(frames, frames[1:], strict=False)):
        raise ValueError("frame series clock must strictly increase")
    return frames, channels


def monitoring_batch(frames, *, factory=None, metadata=None):
    """Construct the monitoring project's actual SignalBatch, without inferring labels."""
    import numpy as np

    if factory is None:
        from process_monitoring.contracts import SignalBatch

        factory = SignalBatch
    frames, channels = _aligned_frames(frames)
    tags = json_value({} if metadata is None else metadata)
    if not isinstance(tags, dict):
        raise ValueError("monitoring metadata must be a JSON object")
    fixed = {"clock_id": frames[0].clock_id, "stream_id": frames[0].stream_id}
    for key, value in {**fixed, "origin": frames[0].origin}.items():
        if key in tags and tags[key] != value:
            raise ValueError("monitoring metadata contradicts the frame clock, stream or origin")
    tags = {**tags, **fixed}
    modes = [frame.operating_mode for frame in frames]
    if any(mode is None for mode in modes) and not all(mode is None for mode in modes):
        raise ValueError("mode identity must be complete or absent, not filled from later observations")
    return factory(
        time_s=np.asarray([f.time_s for f in frames]),
        values=np.asarray(
            [[f.values[c] if f.values[c] is not None else np.nan for c in channels] for f in frames]
        ),
        channels=channels,
        units=tuple(frames[0].units[c] for c in channels),
        valid=np.asarray([[f.valid[c] for c in channels] for f in frames], dtype=bool),
        modes=np.asarray(modes) if modes[0] is not None else None,
        origin=frames[0].origin,
        metadata=tags,
    )


def hra_signal_series(frames, channel, *, factory=None):
    if factory is None:
        from human_reliability import SignalSeries

        factory = SignalSeries
    frames, channels = _aligned_frames(frames)
    if channel not in channels:
        raise ValueError("explicit channel is absent")
    return factory(
        time_s=tuple(f.time_s for f in frames),
        values=tuple(f.values[channel] if f.valid[channel] else None for f in frames),
        valid=tuple(f.valid[channel] for f in frames),
        clock_id=frames[0].clock_id,
        unit=frames[0].units[channel],
        origin=frames[0].origin,
    )
