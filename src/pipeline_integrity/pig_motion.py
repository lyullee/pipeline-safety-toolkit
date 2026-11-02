"""Reviewed prescribed-boundary 1D pig mechanics, integrated by SciPy.

No hydraulic engine, pressure prediction, seal correlation or impact law is supplied.
Numerical qualification is pending the separately scheduled HPC test execution.
"""

from __future__ import annotations

import inspect
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest

REVIEWS = (
    "prescribed_pressure_and_external_force_provider",
    "mass_area_slope_and_friction",
    "static_kinetic_transition_and_domain",
    "numerics_and_balance_limits",
)


class PigBoundaryWindow(Contract):
    id: Name
    duration_s: Positive
    upstream_pressure_pa_abs: Positive | None
    downstream_pressure_pa_abs: Positive | None
    external_axial_force_N: float | None
    provider_id: Name
    provider_version: Name
    evidence_id: Name


class PigMotionRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    start_time_s: Nonnegative
    upstream_port_id: Name
    downstream_port_id: Name
    pressure_basis: str = "absolute Pa; upstream drives positive x"
    mass_kg: Positive
    effective_pressure_area_m2: Positive
    length_m: Positive
    initial_position_m: Nonnegative
    initial_velocity_m_s: float
    inclination_rad: float = Field(ge=-math.pi / 2, le=math.pi / 2)
    gravity_m_s2: Nonnegative
    static_friction_N: Nonnegative
    kinetic_friction_N: Nonnegative
    quadratic_drag_N_s2_m2: Nonnegative
    geometry_friction_evidence_id: Name
    windows: tuple[PigBoundaryWindow, ...] = Field(min_length=1, max_length=256)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    runtime_pins: dict = Field(default_factory=dict)
    relative_tolerance: Positive = Field(default=1e-8, le=1e-3)
    position_atol_m: Positive = 1e-9
    velocity_atol_m_s: Positive = 1e-9
    work_atol_J: Positive = 1e-8
    impulse_atol_N_s: Positive = 1e-8
    maximum_step_s: Positive
    maximum_duration_s: Positive = 86400.0
    maximum_rhs_calls: int = Field(default=100000, ge=1, le=10000000)
    maximum_events: int = Field(default=1024, ge=1, le=100000)
    energy_budget_absolute_J: Nonnegative = 1e-6
    momentum_budget_absolute_N_s: Nonnegative = 1e-6
    balance_relative_tolerance: Nonnegative = Field(default=1e-6, le=1e-2)

    @model_validator(mode="after")
    def ordered(self):
        if self.initial_position_m > self.length_m:
            raise ValueError("initial pig position is outside the finite pipe")
        if self.initial_position_m == 0 and self.initial_velocity_m_s < 0:
            raise ValueError("initial velocity leaves the upstream endpoint")
        if self.initial_position_m == self.length_m and self.initial_velocity_m_s > 0:
            raise ValueError("initial velocity leaves the downstream endpoint")
        if self.static_friction_N < self.kinetic_friction_N:
            raise ValueError("static friction must not be below kinetic friction")
        if self.upstream_port_id == self.downstream_port_id:
            raise ValueError("pressure ports must be distinct")
        if self.pressure_basis != "absolute Pa; upstream drives positive x":
            raise ValueError("unsupported pressure reference/direction")
        if set(self.reviews) != set(REVIEWS):
            raise ValueError("all named review fields are required")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence selections must be unique")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("window IDs must be unique")
        return self


def runtime_metadata():
    """Pin the numerical owner and local force/boundary implementation."""
    from scipy.integrate import solve_ivp
    from scipy.integrate._ivp import base, common
    from scipy.integrate._ivp.rk import RK45
    from scipy.optimize import _zeros

    return {
        "python": platform.python_version(),
        "scipy": version("scipy"),
        "numpy": version("numpy"),
        "solve_ivp_sha256": file_digest(Path(inspect.getfile(solve_ivp))),
        "rk45_sha256": file_digest(Path(inspect.getfile(RK45))),
        "solver_base_sha256": file_digest(Path(base.__file__)),
        "solver_common_sha256": file_digest(Path(common.__file__)),
        "event_root_binary_sha256": file_digest(Path(_zeros.__file__)),
        "adapter_sha256": file_digest(Path(__file__)),
        "solver": "scipy.integrate.solve_ivp/RK45",
        "force_law": "constant prescribed pressures/external force per time window; Coulomb plus quadratic drag",
        "property_basis": "no fluid EOS; caller supplied pressure snapshots; no hydraulic feedback",
        "owner_license": "SciPy BSD-3-Clause; preserve installed distribution notices",
    }


def boundary_metadata(q):
    return {
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "event_id": q.event_id,
        "upstream_port_id": q.upstream_port_id,
        "downstream_port_id": q.downstream_port_id,
        "pressure_basis": q.pressure_basis,
        "request_digest": digest(q),
    }


def _policy(q, c):
    if (c.asset_id, c.clock_id, c.scenario_id) != (q.asset_id, q.clock_id, q.event_id) or c.metadata.get(
        "pig_motion_boundary"
    ) != boundary_metadata(q):
        raise ValueError("actual asset/clock/port/provider/request context differs")
    available = [e.id for e in c.evidence]
    used = {
        q.geometry_friction_evidence_id,
        *q.review_evidence_ids.values(),
        *(w.evidence_id for w in q.windows),
    }
    selected = set(q.evidence_ids)
    if len(set(available)) != len(available) or selected - set(available) or used - selected:
        raise ValueError("review/provider/geometry evidence must select unique actual context evidence")
    notes = ["review:" + k for k in REVIEWS if not q.reviews[k] or k not in q.review_evidence_ids]
    if not q.runtime_pins:
        notes.append("reviewed numerical owner/runtime pins")
    notes += [
        f"{w.id}:missing:{k}"
        for w in q.windows
        for k in ("upstream_pressure_pa_abs", "downstream_pressure_pa_abs", "external_axial_force_N")
        if getattr(w, k) is None
    ]
    return notes


def _integrate(q, body):
    import numpy as np
    from scipy.integrate import solve_ivp

    # x, v, pressure work, gravity work, external work, friction work, net impulse.
    y = np.array([q.initial_position_m, q.initial_velocity_m_s, 0.0, 0.0, 0.0, 0.0, 0.0])
    initial_ke = q.mass_kg * q.initial_velocity_m_s**2 / 2
    elapsed, calls, events_used = 0.0, 0, 0
    rows, transitions = [], []
    terminal = "duration_complete"
    gravity = -q.mass_kg * q.gravity_m_s2 * math.sin(q.inclination_rad)

    def transition(record):
        nonlocal events_used
        events_used += 1
        if events_used > q.maximum_events:
            raise ValueError("reviewed transition budget exceeded")
        transitions.append(record)

    def append(time, state, window, motion, direction):
        pressure = q.effective_pressure_area_m2 * (
            window.upstream_pressure_pa_abs - window.downstream_pressure_pa_abs
        )
        drive = pressure + gravity + window.external_axial_force_N
        friction = (
            -direction * q.kinetic_friction_N - q.quadratic_drag_N_s2_m2 * state[1] * abs(state[1])
            if motion
            else -drive
        )
        net = drive + friction
        work = math.fsum(float(v) for v in state[2:6])
        ke_change = q.mass_kg * state[1] ** 2 / 2 - initial_ke
        momentum_change = q.mass_kg * (state[1] - q.initial_velocity_m_s)
        energy_residual, momentum_residual = ke_change - work, momentum_change - state[6]
        energy_limit = q.energy_budget_absolute_J + q.balance_relative_tolerance * max(
            abs(ke_change), sum(abs(float(v)) for v in state[2:6])
        )
        momentum_limit = q.momentum_budget_absolute_N_s + q.balance_relative_tolerance * max(
            abs(momentum_change), abs(state[6])
        )
        rows.append(
            {
                "time_s": q.start_time_s + time,
                "window_id": window.id,
                "motion": motion,
                "position_m": float(state[0]),
                "velocity_m_s": float(state[1]),
                "pressure_force_N": pressure,
                "gravity_force_N": gravity,
                "external_force_N": window.external_axial_force_N,
                "friction_force_N": float(friction),
                "net_force_N": float(net),
                "acceleration_m_s2": float(net / q.mass_kg),
                "pressure_work_J": float(state[2]),
                "gravity_work_J": float(state[3]),
                "external_work_J": float(state[4]),
                "friction_work_J": float(state[5]),
                "net_impulse_N_s": float(state[6]),
                "energy_residual_J": float(energy_residual),
                "momentum_residual_N_s": float(momentum_residual),
                "energy_limit_J": float(energy_limit),
                "momentum_limit_N_s": float(momentum_limit),
                "within_budget": bool(
                    abs(energy_residual) <= energy_limit and abs(momentum_residual) <= momentum_limit
                ),
            }
        )

    for window in q.windows:
        end = elapsed + window.duration_s
        pressure = q.effective_pressure_area_m2 * (
            window.upstream_pressure_pa_abs - window.downstream_pressure_pa_abs
        )
        drive = pressure + gravity + window.external_axial_force_N
        while elapsed < end:
            if y[1] == 0:
                if abs(drive) <= q.static_friction_N:
                    append(elapsed, y, window, False, 0)
                    transition(
                        {
                            "time_s": q.start_time_s + elapsed,
                            "event": "stuck",
                            "window_id": window.id,
                            "static_reaction_N": -drive,
                        }
                    )
                    append(end, y, window, False, 0)
                    elapsed = end
                    break
                direction = 1 if drive > 0 else -1
                transition(
                    {
                        "time_s": q.start_time_s + elapsed,
                        "event": "breakaway",
                        "direction": direction,
                        "window_id": window.id,
                    }
                )
            else:
                direction = 1 if y[1] > 0 else -1
            if (y[0] == 0 and direction < 0) or (y[0] == q.length_m and direction > 0):
                terminal = "upstream_boundary" if direction < 0 else "downstream_boundary"
                append(elapsed, y, window, True, direction)
                break

            def rhs(time, state, direction=direction, drive=drive, pressure=pressure, window=window):
                nonlocal calls
                calls += 1
                if calls > q.maximum_rhs_calls:
                    raise ValueError("reviewed RHS work budget exceeded")
                velocity = state[1]
                friction = -direction * q.kinetic_friction_N - q.quadratic_drag_N_s2_m2 * velocity * abs(
                    velocity
                )
                force = drive + friction
                return (
                    velocity,
                    force / q.mass_kg,
                    pressure * velocity,
                    gravity * velocity,
                    window.external_axial_force_N * velocity,
                    friction * velocity,
                    force,
                )

            def stop(time, state):
                return state[1]

            stop.terminal = True
            stop.direction = -direction

            def upstream(time, state):
                return state[0]

            upstream.terminal = True
            upstream.direction = -1

            def downstream(time, state):
                return state[0] - q.length_m

            downstream.terminal = True
            downstream.direction = 1
            solution = solve_ivp(
                rhs,
                (elapsed, end),
                y,
                method="RK45",
                rtol=q.relative_tolerance,
                atol=[q.position_atol_m, q.velocity_atol_m_s, *([q.work_atol_J] * 4), q.impulse_atol_N_s],
                max_step=q.maximum_step_s,
                events=(stop, upstream, downstream),
            )
            if not solution.success:
                raise ValueError("SciPy failed: " + solution.message)
            for time, state in zip(solution.t, solution.y.T, strict=True):
                append(float(time), state, window, True, direction)
            previous_time = elapsed
            elapsed, y = float(solution.t[-1]), solution.y[:, -1].copy()
            hit = next((i for i, times in enumerate(solution.t_events) if len(times)), None)
            if hit is None:
                break
            event_name = ("velocity_zero", "upstream_boundary", "downstream_boundary")[hit]
            transition(
                {
                    "time_s": q.start_time_s + elapsed,
                    "event": event_name,
                    "window_id": window.id,
                    "raw_position_m": float(y[0]),
                    "raw_velocity_m_s": float(y[1]),
                }
            )
            if hit in (1, 2):
                terminal = event_name
                break
            # Root residual is retained above. Event reset changes v only within the
            # reviewed absolute velocity tolerance; reject rather than hide a larger jump.
            if abs(y[1]) > q.velocity_atol_m_s:
                raise ValueError("velocity event root exceeds reviewed reset tolerance")
            transitions[-1]["numerical_reset_energy_change_J"] = -q.mass_kg * float(y[1]) ** 2 / 2
            transitions[-1]["numerical_reset_momentum_change_N_s"] = -q.mass_kg * float(y[1])
            y[1] = 0.0
            # Keep the post-reset state in the same physical work/impulse audit,
            # including a reset exactly at the last operating-window endpoint.
            append(elapsed, y, window, abs(drive) > q.static_friction_N, 1 if drive > 0 else -1)
            if elapsed <= previous_time:
                raise ValueError("velocity event failed to advance time")
        if terminal != "duration_complete":
            break
    all_valid = all(r["within_budget"] for r in rows)
    domain_valid = all(-q.position_atol_m <= r["position_m"] <= q.length_m + q.position_atol_m for r in rows)
    body.update(
        status="computed_diagnostic" if all_valid and domain_valid else "diagnostic_rejected",
        notes=[]
        if all_valid and domain_valid
        else ["energy/momentum or finite pipe accepted-state budget failed"],
        history=rows,
        transitions=transitions,
        values={
            "termination": terminal,
            "actual_duration_s": elapsed,
            "requested_duration_s": math.fsum(w.duration_s for w in q.windows),
            "final_position_m": float(y[0]),
            "final_velocity_m_s": float(y[1]),
            "rhs_calls": calls,
            "transition_count": events_used,
            "endpoint_impact_energy_J": q.mass_kg * float(y[1]) ** 2 / 2
            if terminal != "duration_complete"
            else None,
            "domain_valid_with_reviewed_position_tolerance": domain_valid,
        },
    )


def evaluate_pig_motion(request: PigMotionRequest, context: RunContext):
    q = PigMotionRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": "pipeline_integrity.pig_motion.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "notes": _policy(q, c),
        "native_called": False,
        "provider": None,
        "history": None,
        "transitions": None,
        "values": None,
        "physical_validation_claim": False,
        "hydraulic_feedback_model": False,
        "qualification": "prescribed time-window boundary 1D rigid pig mechanics; independent validation pending",
    }
    if not body["notes"]:
        total = math.fsum(w.duration_s for w in q.windows)
        if total > q.maximum_duration_s or not math.isfinite(total + q.start_time_s):
            raise ValueError("requested duration exceeds reviewed clock/work domain")
        cursor = 0.0
        for window in q.windows:
            next_cursor = cursor + window.duration_s
            if next_cursor <= cursor or q.start_time_s + next_cursor <= q.start_time_s + cursor:
                raise ValueError("window boundaries are not representable on the declared clock")
            cursor = next_cursor
        before = runtime_metadata()
        if before != q.runtime_pins:
            raise ValueError("reviewed numerical owner/runtime/source pins differ")
        body["provider"] = before
        try:
            body["native_called"] = True
            _integrate(q, body)
            json_value(body)
        except (ValueError, RuntimeError, ArithmeticError, TypeError) as exc:
            body.update(
                status="out_of_scope",
                notes=[f"{type(exc).__name__}: {exc}"],
                history=None,
                transitions=None,
                values=None,
            )
        if runtime_metadata() != before:
            raise ValueError("numerical owner/runtime changed during integration")
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def verify_pig_motion_record(record, *, request=None, context=None):
    try:
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("pig receipt changed")
        q = PigMotionRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and request != q) or (context is not None and context != c):
            raise ValueError("caller request/context differs")
        if evaluate_pig_motion(q, c) != record:
            raise ValueError("actual mechanical replay differs")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frames(record, context):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_pig_motion_record(record, context=context)["valid"]
    ):
        raise ValueError("verified computed history with actual matching context is required")
    # Boundary switches/event roots can have left/right records at identical time.
    # Keep the last (right-hand) record for a single clock stream; raw history remains complete.
    rows = {r["time_s"]: r for r in record["history"]}
    return tuple(
        SignalFrame(
            stream_id=f"{context.asset_id}/{record['request']['event_id']}/pig_motion",
            clock_id=context.clock_id,
            time_s=time,
            values={
                "position_m": r["position_m"],
                "velocity_m_s": r["velocity_m_s"],
                "net_force_N": r["net_force_N"],
            },
            units={"position_m": "m", "velocity_m_s": "m/s", "net_force_N": "N"},
            valid=dict.fromkeys(("position_m", "velocity_m_s", "net_force_N"), True),
            origin="synthetic" if context.origin == "synthetic" else "simulation",
            operating_mode="prescribed_boundary_1d_pig",
        )
        for time, r in sorted(rows.items())
    )


def assess_receiver_arrival(
    record,
    *,
    request,
    context,
    earliest_time_s,
    latest_time_s,
    maximum_arrival_speed_m_s,
    receiver_evidence_id,
):
    """Assess a reviewed receiver window against a replayed mechanical trajectory.

    Endpoint arrival is observable in this model; capture, impact absorption and
    receiver integrity require separate models and are never inferred here.
    """
    limits = (earliest_time_s, latest_time_s, maximum_arrival_speed_m_s)
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in limits
    ):
        raise ValueError("receiver time and speed limits must be finite nonnegative numbers")
    if earliest_time_s > latest_time_s:
        raise ValueError("receiver arrival window is reversed")
    if receiver_evidence_id not in {item.id for item in context.evidence}:
        raise ValueError("receiver acceptance evidence is absent from the actual context")
    if not verify_pig_motion_record(record, request=request, context=context)["valid"]:
        raise ValueError("receiver assessment requires actual request/context mechanical replay")
    values = record.get("values")
    computed = record.get("status") == "computed_diagnostic" and isinstance(values, dict)
    arrived = computed and values["termination"] == "downstream_boundary"
    arrival = request.start_time_s + values["actual_duration_s"] if arrived else None
    speed = abs(values["final_velocity_m_s"]) if arrived else None
    outcome = "needs_data" if not computed else "not_arrived_within_simulated_horizon"
    if arrived:
        outcome = (
            "within_declared_limits"
            if (earliest_time_s <= arrival <= latest_time_s and speed <= maximum_arrival_speed_m_s)
            else "outside_declared_limits"
        )
    body = dict(
        schema="pipeline_integrity.pig_receiver_arrival.v1",
        source_record_digest=record["record_digest"],
        request_digest=digest(request),
        context_digest=digest(context),
        receiver_evidence_id=receiver_evidence_id,
        earliest_time_s=earliest_time_s,
        latest_time_s=latest_time_s,
        maximum_arrival_speed_m_s=maximum_arrival_speed_m_s,
        status=outcome,
        arrival_time_s=arrival,
        arrival_speed_m_s=speed,
        receiver_capture_or_impact_qualified=False,
    )
    return {**body, "record_digest": digest(body)}


def verify_receiver_arrival_assessment(assessment, pig_record, *, request, context):
    """Rebuild a receiver-window decision from its bound, replayed trajectory."""
    try:
        if assessment["record_digest"] != digest(
            {key: value for key, value in assessment.items() if key != "record_digest"}
        ):
            raise ValueError("receiver assessment digest changed")
        if assessment.get("schema") != "pipeline_integrity.pig_receiver_arrival.v1":
            raise ValueError("unsupported receiver assessment schema")
        if not verify_pig_motion_record(pig_record, request=request, context=context)["valid"]:
            raise ValueError("source pig trajectory does not match the supplied request/context")
        rebuilt = assess_receiver_arrival(
            pig_record,
            request=request,
            context=context,
            earliest_time_s=assessment["earliest_time_s"],
            latest_time_s=assessment["latest_time_s"],
            maximum_arrival_speed_m_s=assessment["maximum_arrival_speed_m_s"],
            receiver_evidence_id=assessment["receiver_evidence_id"],
        )
        if rebuilt != assessment:
            raise ValueError("receiver assessment does not replay from its source trajectory")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}
