"""Clocked, unit-qualified use of the owner's PID; commands remain distinct from plant action."""

import inspect
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .units import convert_quantity


class ControlAxis(Contract):
    quantity: Literal["pressure", "temperature", "mass_flow", "volume_flow", "level", "fraction", "power"]
    unit: Name
    reference_id: Name
    pressure_reference: Literal["absolute", "gauge", "differential"] | None = None

    @model_validator(mode="after")
    def canonical(self):
        units = {
            "pressure": "Pa",
            "temperature": "K",
            "mass_flow": "kg/s",
            "volume_flow": "m3/s",
            "level": "m",
            "fraction": "1",
            "power": "W",
        }
        if self.unit != units[self.quantity]:
            raise ValueError("control axes require canonical SI units before entering the controller")
        if (self.quantity == "pressure") != (self.pressure_reference is not None):
            raise ValueError("pressure axes require an explicit pressure reference only")
        return self


class PIDGains(Contract):
    proportional: Nonnegative
    integral: Nonnegative
    derivative: Nonnegative
    proportional_unit: Name
    integral_unit: Name
    derivative_unit: Name


class PIDMemory(Contract):
    integral_error_s: float = 0.0
    previous_measurement: float | None = None
    filtered_measurement_rate_per_s: float = 0.0


class PIDLoop(Contract):
    loop_id: Name
    measurement_stream_id: Name
    measurement_channel: Name
    actuator_id: Name
    measurement_axis: ControlAxis
    output_axis: ControlAxis
    gains: PIDGains
    output_minimum: float
    output_maximum: float
    output_bias: float = 0.0
    derivative_filter_time_s: Nonnegative = 0.0
    reverse_acting: bool = False
    maximum_measurement_age_s: Nonnegative
    maximum_scan_interval_s: Positive
    missing_policy: Literal["withhold", "hold_last", "fixed_output"] = "withhold"
    fallback_output: float | None = None
    initial_output: float | None = None
    parameter_evidence_id: Name

    @model_validator(mode="after")
    def policy(self):
        if self.output_maximum <= self.output_minimum:
            raise ValueError("controller output limits must be strictly increasing")
        if not all(_in_domain(self.output_axis, v) for v in (self.output_minimum, self.output_maximum)):
            raise ValueError("output limits lie outside the actuator command's physical domain")
        for value in (self.output_bias, self.initial_output, self.fallback_output):
            if value is not None and not self.output_minimum <= value <= self.output_maximum:
                raise ValueError("initial, bias and fallback outputs must lie inside the declared limits")
        if (
            self.output_axis.quantity == "fraction"
            and not 0 <= self.output_minimum < self.output_maximum <= 1
        ):
            raise ValueError("fraction commands stay inside zero and one")
        if self.missing_policy == "hold_last" and self.initial_output is None:
            raise ValueError("hold_last needs an explicit initial command")
        if (self.missing_policy == "fixed_output") != (self.fallback_output is not None):
            raise ValueError("only fixed_output uses an explicit fallback value")
        return self


class ControlObservation(Contract):
    frame: SignalFrame | None
    axis: ControlAxis
    available_at_s: Nonnegative
    evidence_id: Name
    receipt: dict = Field(default_factory=dict)


class PIDCheckpoint(Contract):
    request_digest: Name
    context_digest: Name
    provider_digest: Name
    time_s: Nonnegative
    memory: PIDMemory
    last_output: float | None
    last_measurement_time_s: Nonnegative | None
    reset_derivative: bool
    previous_record_digest: str | None
    checkpoint_digest: Name


def native_identity(cls):
    return {
        "id": cls.__module__ + "." + cls.__qualname__,
        "source_sha256": file_digest(Path(inspect.getfile(cls))),
    }


def _evidence(context, *ids):
    if len({e.id for e in context.evidence}) != len(context.evidence):
        raise ValueError("control and instrumentation evidence identifiers must be unique")
    if set(ids) - {e.id for e in context.evidence}:
        raise ValueError("control and instrumentation evidence is absent from RunContext")


def _in_domain(axis, value):
    if axis.quantity == "temperature" or axis.pressure_reference == "absolute":
        return value > 0
    if axis.quantity == "level":
        return value >= 0
    if axis.quantity == "fraction":
        return 0 <= value <= 1
    return True


class PIDSession:
    """One decision clock, frozen state during data gaps and explicit derivative restart on recovery."""

    def __init__(
        self, specification: PIDLoop, context: RunContext, *, initial_time_s=0.0, native_module=None
    ):
        self.specification = PIDLoop.model_validate(specification.model_dump())
        self.context = RunContext.model_validate(context.model_dump())
        if context.asset_id != specification.loop_id:
            raise ValueError("the controller context must identify its loop")
        _evidence(context, specification.parameter_evidence_id)
        if native_module is None:
            from lh2dt import control as native_module
        self._native = native_module
        s = self.specification
        inp, out = s.measurement_axis.unit, s.output_axis.unit
        gains = s.gains
        self.normalized_gains = {
            "proportional_gain": convert_quantity(
                gains.proportional, gains.proportional_unit, f"({out})/({inp})"
            ),
            "integral_gain_per_s": convert_quantity(
                gains.integral, gains.integral_unit, f"({out})/({inp}*s)"
            ),
            "derivative_gain_s": convert_quantity(
                gains.derivative, gains.derivative_unit, f"({out})*s/({inp})"
            ),
        }
        self._controller = native_module.PIDController(
            native_module.PIDParameters(
                **self.normalized_gains,
                output_minimum=s.output_minimum,
                output_maximum=s.output_maximum,
                output_bias=s.output_bias,
                derivative_filter_time_s=s.derivative_filter_time_s,
                reverse_acting=s.reverse_acting,
            )
        )
        self.provider = native_identity(type(self._controller))
        self._memory = PIDMemory()
        self._time = NonnegativeTime(value=initial_time_s).value
        self._last_output = s.initial_output
        self._last_measurement_time = None
        self._reset_derivative = False
        self._previous_digest = None
        self._bindings = self._binding_digests()

    def _binding_digests(self):
        return tuple(
            digest(value)
            for value in (self.specification, self.context, self.provider, self.normalized_gains)
        )

    def _check_bindings(self):
        if self._binding_digests() != self._bindings:
            raise ValueError("the PID session's bound parameters, context or provider changed")

    def checkpoint(self):
        self._check_bindings()
        body = dict(
            request_digest=digest(self.specification),
            context_digest=digest(self.context),
            provider_digest=digest(self.provider),
            time_s=self._time,
            memory=self._memory.model_dump(),
            last_output=self._last_output,
            last_measurement_time_s=self._last_measurement_time,
            reset_derivative=self._reset_derivative,
            previous_record_digest=self._previous_digest,
        )
        return PIDCheckpoint(**body, checkpoint_digest=digest(body))

    def restore(self, checkpoint: PIDCheckpoint):
        checkpoint = PIDCheckpoint.model_validate(checkpoint.model_dump())
        body = checkpoint.model_dump(exclude={"checkpoint_digest"})
        if digest(body) != checkpoint.checkpoint_digest:
            raise ValueError("PID checkpoint content changed")
        if any(
            body[k] != getattr(self.checkpoint(), k)
            for k in ("request_digest", "context_digest", "provider_digest")
        ):
            raise ValueError("PID checkpoint belongs to a different loop, context or provider")
        if checkpoint.last_output is not None and not (
            self.specification.output_minimum <= checkpoint.last_output <= self.specification.output_maximum
        ):
            raise ValueError("checkpoint output exceeds the controller limits")
        if (
            checkpoint.last_measurement_time_s is not None
            and checkpoint.last_measurement_time_s > checkpoint.time_s
        ):
            raise ValueError("checkpoint contains a future measurement")
        self._time, self._memory = checkpoint.time_s, checkpoint.memory
        self._last_output = checkpoint.last_output
        self._last_measurement_time = checkpoint.last_measurement_time_s
        self._reset_derivative = checkpoint.reset_derivative
        self._previous_digest = checkpoint.previous_record_digest

    def checkpoint_with_memory(self, memory: PIDMemory):
        """Prepare an explicit state-edit checkpoint without committing a controller step."""
        self._check_bindings()
        memory=PIDMemory.model_validate(memory.model_dump(mode="python"))
        data=self.checkpoint().model_dump(mode="python",exclude={"checkpoint_digest"})
        data["memory"]=memory.model_dump(mode="python")
        return PIDCheckpoint.model_validate({**data,"checkpoint_digest":digest(data)})

    def preview(self, decision_time_s, setpoint, observation: ControlObservation, *, memory: PIDMemory | None = None):
        """Run the actual native step on a separate session, preserving this checkpoint."""
        before=self.checkpoint()
        trial=PIDSession(self.specification,self.context,initial_time_s=before.time_s,native_module=self._native)
        trial.restore(before if memory is None else self.checkpoint_with_memory(memory))
        result=trial.step(decision_time_s,setpoint,observation)
        if self.checkpoint()!=before:
            raise ValueError("native PID preview changed the committed original session")
        return result

    def step(self, decision_time_s, setpoint, observation: ControlObservation):
        self._check_bindings()
        decision_time_s = NonnegativeTime(value=decision_time_s).value
        setpoint = FiniteScalar(value=setpoint).value
        observation = ControlObservation.model_validate(observation.model_dump())
        s, ctx = self.specification, self.context
        if observation.axis != s.measurement_axis:
            raise ValueError("measurement quantity, pressure reference or physical datum differs")
        if decision_time_s <= self._time:
            raise ValueError("controller decisions must advance a strictly increasing clock")
        _evidence(ctx, observation.evidence_id)
        f = observation.frame
        if f is not None and (f.clock_id != ctx.clock_id or f.stream_id != s.measurement_stream_id):
            raise ValueError("measurement stream or clock does not belong to the controller")
        if f is not None and (
            s.measurement_channel not in f.values or f.units[s.measurement_channel] != s.measurement_axis.unit
        ):
            raise ValueError("measurement channel or SI unit differs from the control axis")
        if not _in_domain(s.measurement_axis, setpoint):
            raise ValueError("setpoint lies outside its declared physical domain")
        dt = decision_time_s - self._time
        reason = None
        if dt > s.maximum_scan_interval_s:
            reason = "scan_interval_exceeded"
        elif observation.available_at_s > decision_time_s:
            reason = "not_yet_available"
        elif f is None or not f.valid[s.measurement_channel] or f.values[s.measurement_channel] is None:
            reason = "missing_or_invalid_measurement"
        elif f.time_s > decision_time_s:
            reason = "future_measurement"
        elif decision_time_s - f.time_s > s.maximum_measurement_age_s:
            reason = "stale_measurement"
        elif self._last_measurement_time is not None and f.time_s <= self._last_measurement_time:
            reason = "repeated_or_reordered_measurement"
        elif not _in_domain(s.measurement_axis, f.values[s.measurement_channel]):
            reason = "measurement_outside_physical_domain"
        native = None
        memory = self._memory
        output = None
        reset = False
        if reason is None:
            if self._reset_derivative:
                memory = memory.model_copy(
                    update={"previous_measurement": None, "filtered_measurement_rate_per_s": 0.0}
                )
                reset = True
            value = self._controller.step(
                self._native.PIDState(**memory.model_dump()), setpoint, f.values[s.measurement_channel], dt
            )
            native = asdict(value)
            # A malformed provider must not advance the session's committed state.
            digest(native)
            output = FiniteScalar(value=value.output).value
            memory = PIDMemory(**native["state"])
            if not s.output_minimum <= output <= s.output_maximum:
                raise ValueError("native PID output exceeded the declared actuator limits")
        elif s.missing_policy == "hold_last":
            output = self._last_output
        elif s.missing_policy == "fixed_output":
            output = s.fallback_output
        body = dict(
            schema="pipeline_integrity.pid_decision.v1",
            time_s=decision_time_s,
            status="computed" if reason is None else "needs_data",
            reason=reason,
            setpoint=setpoint,
            observation=observation.model_dump(mode="json"),
            output=output,
            output_unit=s.output_axis.unit,
            native=native,
            interval_s=dt,
            derivative_restarted=reset,
            memory=memory.model_dump(),
            request=s.model_dump(mode="json"),
            context=ctx.model_dump(mode="json"),
            provider=self.provider,
            normalized_gains=self.normalized_gains,
            previous_record_digest=self._previous_digest,
            output_origin="simulation",
            actuator_action_or_safety_authorization=False,
        )
        body["record_digest"] = digest(body)
        self._time = decision_time_s
        self._memory = memory
        self._reset_derivative = reason is not None
        self._last_output = output
        if reason is None:
            self._last_measurement_time = f.time_s
        self._previous_digest = body["record_digest"]
        return body


class FiniteScalar(Contract):
    value: float


class NonnegativeTime(Contract):
    value: Nonnegative
