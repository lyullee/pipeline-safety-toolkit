"""Reuse native actuator kinematics with independently supplied liquid conductance data."""

import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest


class ConductanceCurve(Contract):
    opening: tuple[Fraction, ...] = Field(min_length=2)
    effective_area_m2: tuple[Nonnegative, ...] = Field(min_length=2)
    parameter_source_id: Name
    interpolation: Literal["piecewise_linear", "pchip"] = "piecewise_linear"

    @model_validator(mode="after")
    def ordered(self):
        if (
            self.opening[0] != 0
            or self.opening[-1] != 1
            or len(self.opening) != len(self.effective_area_m2)
            or any(b <= a for a, b in zip(self.opening, self.opening[1:], strict=False))
            or any(b < a for a, b in zip(self.effective_area_m2, self.effective_area_m2[1:], strict=False))
            or self.effective_area_m2[-1] <= 0
        ):
            raise ValueError(
                "a conductance curve spans openings 0..1 with increasing positions and nondecreasing effective CdA"
            )
        return self


class ValveCommand(Contract):
    id: Name
    time_s: Nonnegative
    target_opening: Fraction
    evidence_id: Name


class ValveMotionRequest(Contract):
    component_id: Name
    initial_opening: Fraction
    full_stroke_time_s: Positive
    sample_time_s: tuple[Nonnegative, ...] = Field(min_length=2)
    commands: tuple[ValveCommand, ...] = ()
    conductance: ConductanceCurve
    maximum_samples: int = Field(default=100000, ge=2, le=1000000)
    position_tolerance: Positive = Field(default=1e-12, lt=1)

    @model_validator(mode="after")
    def clock(self):
        if self.sample_time_s[0] != 0 or any(
            b <= a for a, b in zip(self.sample_time_s, self.sample_time_s[1:], strict=False)
        ):
            raise ValueError("motion sampling starts at zero with strictly increasing times")
        if (
            len({c.id for c in self.commands}) != len(self.commands)
            or any(b.time_s <= a.time_s for a, b in zip(self.commands, self.commands[1:], strict=False))
            or any(c.time_s > self.sample_time_s[-1] for c in self.commands)
        ):
            raise ValueError(
                "commands need unique IDs, an ordered clock and a time inside the sampled horizon"
            )
        if len(set(self.sample_time_s).union(c.time_s for c in self.commands)) > self.maximum_samples:
            raise ValueError("the command/motion sample budget was exceeded")
        return self


class ValveMotionResult(Contract):
    schema_version: Literal["pipeline_integrity.valve_motion.v1"] = "pipeline_integrity.valve_motion.v1"
    request: ValveMotionRequest
    context: RunContext
    time_s: tuple[Nonnegative, ...]
    opening: tuple[Fraction, ...]
    target_opening: tuple[Fraction, ...]
    effective_area_m2: tuple[Nonnegative, ...]
    command_records: tuple[dict, ...]
    provider: dict
    output_origin: Literal["simulation"] = "simulation"
    kinematics_only: Literal[True] = True
    hydraulic_or_field_validation: Literal[False] = False
    implementation_sha256: str
    result_digest: str

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the motion result or its command/curve evidence changed")


class _KinematicsOnlyProperties:
    def __getattr__(self, name):
        raise RuntimeError("an actuator-motion calculation must not query a fluid property or gas flow model")


def simulate_valve_motion(
    specification: ValveMotionRequest, context: RunContext, *, factory=None, provider=None
):
    specification = ValveMotionRequest.model_validate_json(specification.model_dump_json())
    if context.asset_id != specification.component_id:
        raise ValueError("the motion context must identify its actual valve component")
    if factory is None:
        import lh2dt
        from lh2dt import valve as native

        def factory(request):
            # The native wrapper requires a Valve carrier. Only its public
            # motion API is used; all fluid evaluation is explicitly disabled.
            carrier = native.Valve(
                request.conductance.effective_area_m2[-1], 1.0, _KinematicsOnlyProperties()
            )
            return native.StrokeLimitedCommandedValve(
                carrier, stroke_time_s=request.full_stroke_time_s, initial_opening=request.initial_opening
            )

        provider = {
            "id": "lh2dt_models.StrokeLimitedCommandedValve",
            "version": getattr(lh2dt, "__version__", None),
            "source_sha256": file_digest(Path(native.__file__)),
            "fluid_evaluation_enabled": False,
        }
    if provider is None:
        raise ValueError("an injected motion provider needs explicit identity and source/version evidence")
    actuator = factory(specification)
    if actuator.opening != specification.initial_opening:
        raise ValueError("the motion provider changed the declared initial position")
    if specification.conductance.interpolation == "pchip":
        from scipy.interpolate import PchipInterpolator

        curve = PchipInterpolator(
            specification.conductance.opening, specification.conductance.effective_area_m2, extrapolate=False
        )
    else:

        def curve(position):
            return np.interp(
                position, specification.conductance.opening, specification.conductance.effective_area_m2
            )

    times = sorted(set(specification.sample_time_s).union(c.time_s for c in specification.commands))
    commands = {command.time_s: command for command in specification.commands}
    positions, targets, areas, records = [], [], [], []
    current = None
    previous = 0.0
    for time_s in times:
        if time_s > previous:
            advanced = actuator.advance(time_s - previous)
            if (
                not isinstance(advanced, (float, int))
                or isinstance(advanced, bool)
                or not math.isfinite(advanced)
                or advanced != actuator.opening
            ):
                raise ValueError("the motion provider returned an inconsistent actuator state")
        position = actuator.opening
        if (
            not isinstance(position, (float, int))
            or isinstance(position, bool)
            or not math.isfinite(position)
            or not 0 <= position <= 1
        ):
            raise ValueError("the native actuator position is outside 0..1")
        if (
            current is not None
            and current["completion_bracket_s"] is None
            and abs(position - current["target_opening"]) <= specification.position_tolerance
        ):
            current["completion_bracket_s"] = [previous, time_s]
        if time_s in commands:
            command = commands[time_s]
            if current is not None and current["completion_bracket_s"] is None:
                current["superseded_at_s"] = time_s
            actuator.set_target_opening(command.target_opening)
            if actuator.opening != position or actuator.target_opening != command.target_opening:
                raise ValueError("a target command changed physical position or was not retained")
            current = {
                "command_id": command.id,
                "issued_time_s": time_s,
                "target_opening": command.target_opening,
                "opening_at_issue": position,
                "evidence_id": command.evidence_id,
                "completion_bracket_s": [time_s, time_s]
                if abs(position - command.target_opening) <= specification.position_tolerance
                else None,
                "superseded_at_s": None,
            }
            records.append(current)
        area = float(curve(position))
        if not math.isfinite(area) or area < 0:
            raise ValueError("the declared curve returned an invalid liquid conductance")
        positions.append(float(position))
        targets.append(float(actuator.target_opening))
        areas.append(area)
        previous = time_s
    body = {
        "schema_version": "pipeline_integrity.valve_motion.v1",
        "request": specification.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "time_s": times,
        "opening": positions,
        "target_opening": targets,
        "effective_area_m2": areas,
        "command_records": records,
        "provider": json_value(provider),
        "output_origin": "simulation",
        "kinematics_only": True,
        "hydraulic_or_field_validation": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return ValveMotionResult.model_validate_json(
        json.dumps({**body, "result_digest": digest(body)}, allow_nan=False)
    )


def liquid_valve_from_motion(
    motion: ValveMotionResult,
    *,
    start_node: str,
    end_node: str,
    initial_volume_flow_m3_s: float,
    network_context: RunContext,
):
    from .transient import History, LiquidValve

    motion.check_integrity()
    if (motion.context.scenario_id, motion.context.clock_id) != (
        network_context.scenario_id,
        network_context.clock_id,
    ):
        raise ValueError("motion and liquid network require an explicit shared scenario and clock")
    return LiquidValve(
        id=motion.request.component_id,
        start_node=start_node,
        end_node=end_node,
        initial_volume_flow_m3_s=initial_volume_flow_m3_s,
        effective_area_m2=History(time_s=motion.time_s, values=motion.effective_area_m2),
        parameter_source_id=motion.request.conductance.parameter_source_id,
        evidence={
            "kind": "valve_motion_binding",
            "motion": motion.model_dump(mode="json"),
            "network_context": network_context.model_dump(mode="json"),
        },
    )


def check_liquid_motion_binding(valve, context: RunContext, duration_s: float):
    motion = ValveMotionResult.model_validate_json(json.dumps(valve.evidence["motion"], allow_nan=False))
    motion.check_integrity()
    bound_context = RunContext.model_validate_json(json.dumps(valve.evidence["network_context"]))
    if (
        bound_context != context
        or motion.request.component_id != valve.id
        or (motion.context.scenario_id, motion.context.clock_id) != (context.scenario_id, context.clock_id)
        or valve.effective_area_m2.time_s != motion.time_s
        or valve.effective_area_m2.values != motion.effective_area_m2
        or valve.parameter_source_id != motion.request.conductance.parameter_source_id
    ):
        raise ValueError("the actuator/area/network binding changed")
    if motion.time_s[-1] < duration_s:
        raise ValueError("the simulated actuator history does not cover the liquid analysis horizon")


def motion_frames(motion: ValveMotionResult):
    """Export simulated positions/conductance, preserving the original motion receipt separately."""
    from .contracts import SignalFrame

    motion.check_integrity()
    for t, opening, target, area in zip(
        motion.time_s, motion.opening, motion.target_opening, motion.effective_area_m2, strict=True
    ):
        values = {"opening": opening, "target_opening": target, "effective_CdA": area}
        yield SignalFrame(
            stream_id=f"valve-motion:{motion.request.component_id}:{motion.result_digest[:12]}",
            clock_id=motion.context.clock_id,
            time_s=t,
            values=values,
            units={"opening": "1", "target_opening": "1", "effective_CdA": "m2"},
            valid=dict.fromkeys(values, True),
            origin="simulation",
            operating_mode=motion.context.scenario_id,
        )
