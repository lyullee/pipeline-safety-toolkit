"""Clock, physical reference, recovery and transactional contracts using an algorithm-free provider spy."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import (
    ControlAxis,
    ControlObservation,
    PIDGains,
    PIDLoop,
    PIDMemory,
    PIDSession,
)

AXIS = ControlAxis(
    quantity="pressure", unit="Pa", pressure_reference="absolute", reference_id="process:absolute"
)
OUTPUT = ControlAxis(quantity="fraction", unit="1", reference_id="valve:target_opening")
CTX = RunContext(
    asset_id="pressure-loop",
    scenario_id="authored",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(
        EvidenceRef(id="parameters", citation="Authored contract inputs", origin="synthetic"),
        EvidenceRef(id="observation", citation="Authored test frames", origin="synthetic"),
    ),
)


@dataclass
class SpyState:
    integral_error_s: float = 0.0
    previous_measurement: float | None = None
    filtered_measurement_rate_per_s: float = 0.0


@dataclass
class SpyResult:
    output: float
    state: SpyState


class SpyController:
    def __init__(self, parameters):
        self.parameters = parameters
        self.calls = []
        self.output = 0.5

    def step(self, state, setpoint, measurement, dt):
        self.calls.append((state, setpoint, measurement, dt))
        return SpyResult(self.output, SpyState(state.integral_error_s + 7.25, measurement, 3.0))


NATIVE = SimpleNamespace(PIDController=SpyController, PIDState=SpyState, PIDParameters=lambda **kw: kw)


def spec(**changes):
    fields = dict(
        loop_id="pressure-loop",
        measurement_stream_id="PT",
        measurement_channel="pressure",
        actuator_id="V",
        measurement_axis=AXIS,
        output_axis=OUTPUT,
        gains=PIDGains(
            proportional=2.0,
            integral=3.0,
            derivative=4.0,
            proportional_unit="1/kPa",
            integral_unit="1/(kPa*s)",
            derivative_unit="s/kPa",
        ),
        output_minimum=0.0,
        output_maximum=1.0,
        maximum_measurement_age_s=2.0,
        maximum_scan_interval_s=2.0,
        parameter_evidence_id="parameters",
    )
    return PIDLoop(**(fields | changes))


def observation(time=1.0, value=200000.0, **changes):
    f = SignalFrame(
        stream_id="PT",
        clock_id="elapsed",
        time_s=time,
        values={"pressure": value},
        units={"pressure": "Pa"},
        valid={"pressure": value is not None},
        origin="synthetic",
    )
    return ControlObservation(
        **(dict(frame=f, axis=AXIS, available_at_s=time, evidence_id="observation") | changes)
    )


def session(**changes):
    return PIDSession(spec(**changes), CTX, native_module=NATIVE)


def test_dimensional_gains_native_call_and_context_are_preserved():
    s = session()
    r = s.step(1.0, 210000.0, observation())
    assert s.normalized_gains == pytest.approx(
        dict(proportional_gain=0.002, integral_gain_per_s=0.003, derivative_gain_s=0.004)
    )
    assert s._controller.calls[0][1:] == (210000.0, 200000.0, 1.0)
    assert r["native"]["state"]["integral_error_s"] == 7.25
    assert r["output_origin"] == "simulation" and not r["actuator_action_or_safety_authorization"]


@pytest.mark.parametrize("kind", ["missing", "future", "late_delivery", "stale", "repeat", "long_scan"])
def test_unusable_observations_do_not_call_the_native_controller(kind):
    s = session(**({"maximum_measurement_age_s": 0.5} if kind == "stale" else {}))
    s.step(1.0, 210000.0, observation())
    o = observation(2.0)
    t = 2.0
    if kind == "missing":
        o = observation(2.0, None)
    elif kind == "future":
        o = observation(3.0, available_at_s=2.0)
    elif kind == "late_delivery":
        o = observation(2.0, available_at_s=2.5)
    elif kind == "stale":
        o = observation(0.5)
    elif kind == "repeat":
        o = observation(1.0)
    elif kind == "long_scan":
        t, o = 4.0, observation(4.0)
    r = s.step(t, 210000.0, o)
    assert r["status"] == "needs_data" and r["output"] is None
    assert len(s._controller.calls) == 1
    assert s.checkpoint().memory.integral_error_s == 7.25


@pytest.mark.parametrize("policy,expected", [("withhold", None), ("hold_last", 0.5), ("fixed_output", 0.25)])
def test_gap_policy_freezes_integral_and_restarts_derivative_on_recovery(policy, expected):
    options = dict(missing_policy=policy)
    if policy == "hold_last":
        options["initial_output"] = 0.1
    if policy == "fixed_output":
        options["fallback_output"] = 0.25
    s = session(**options)
    s.step(1.0, 210000.0, observation())
    missing = s.step(2.0, 210000.0, observation(2.0, None))
    assert missing["output"] == expected
    r = s.step(3.0, 210000.0, observation(3.0))
    memory, _, _, dt = s._controller.calls[-1]
    assert memory == SpyState(7.25, None, 0.0) and dt == 1.0
    assert r["derivative_restarted"] and r["memory"]["integral_error_s"] == 14.5


def test_clock_unit_pressure_reference_and_evidence_conflicts_are_not_relabelled():
    s = session()
    o = observation()
    variants = [
        o.model_copy(update={"axis": AXIS.model_copy(update={"pressure_reference": "gauge"})}),
        o.model_copy(update={"evidence_id": "unknown"}),
        o.model_copy(update={"frame": o.frame.model_copy(update={"clock_id": "other"})}),
        o.model_copy(update={"frame": o.frame.model_copy(update={"units": {"pressure": "kPa"}})}),
    ]
    for invalid in variants:
        with pytest.raises(ValueError):
            s.step(1.0, 210000.0, invalid)
    assert not s._controller.calls


def test_checkpoint_resume_equivalence_and_tampering_rejection():
    a, b = session(), session()
    a.step(1.0, 210000.0, observation())
    c = a.checkpoint()
    b.restore(c)
    assert a.step(2.0, 210000.0, observation(2.0)) == b.step(2.0, 210000.0, observation(2.0))
    with pytest.raises(ValueError, match="content changed"):
        b.restore(c.model_copy(update={"memory": PIDMemory(integral_error_s=900.0)}))
    with pytest.raises(ValueError, match="different loop"):
        session(output_bias=0.2).restore(c)


@pytest.mark.parametrize("bad", [float("nan"), 1.5])
def test_invalid_native_output_cannot_commit_state(bad):
    s = session()
    before = s.checkpoint()
    s._controller.output = bad
    with pytest.raises(ValueError):
        s.step(1.0, 210000.0, observation())
    assert s.checkpoint() == before


def test_physical_domain_and_strict_boolean_boundaries():
    s = session()
    r = s.step(1.0, 210000.0, observation(value=-2.0))
    assert r["reason"] == "measurement_outside_physical_domain" and r["output"] is None
    with pytest.raises(ValueError):
        s.step(True, 210000.0, observation(2.0))
    with pytest.raises(ValueError):
        s.step(2.0, -1.0, observation(2.0))
    with pytest.raises(ValueError):
        spec(missing_policy="hold_last")
    with pytest.raises(ValueError):
        ControlAxis(quantity="pressure", unit="Pa", reference_id="unknown")
    with pytest.raises((ValueError, TypeError)):
        session(gains=spec().gains.model_copy(update={"integral_unit": "m/s"}))


@pytest.mark.parametrize("changed", ["parameters", "context", "provider", "normalized_gains"])
def test_bound_session_cannot_silently_relabel_parameters_or_evidence(changed):
    s = session()
    if changed == "parameters":
        s.specification = spec(output_bias=0.2)
    elif changed == "context":
        s.context.metadata["new_unreviewed_basis"] = True
    elif changed == "provider":
        s.provider["id"] = "different"
    else:
        s.normalized_gains["proportional_gain"] = 900.0
    with pytest.raises(ValueError, match="bound parameters"):
        s.step(1.0, 210000.0, observation())
    assert not s._controller.calls


def test_nonphysical_absolute_command_limits_are_rejected():
    with pytest.raises(ValueError, match="physical domain"):
        spec(output_axis=AXIS, output_minimum=0.0, output_maximum=100000.0)
