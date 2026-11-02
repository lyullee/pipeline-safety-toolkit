"""Individual cable-position calculations with explicit path and timing bases.

Positions are measured from the interrogating end. Inputs are SI estimates;
the functions neither detect a leak nor infer measurement uncertainty.
"""

from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Nonnegative, Positive


class ResistiveCableRequest(Contract):
    voltage_v: Nonnegative | None
    current_a: Positive | None
    resistance_per_length_ohm_m: Positive | None
    resistance_basis: Literal["single_conductor", "complete_loop"]
    fixed_series_resistance_ohm: Nonnegative = 0.0
    cable_length_m: Positive | None = None


class TimeOfFlightRequest(Contract):
    elapsed_time_s: Nonnegative | None
    propagation_speed_m_s: Positive | None
    timing_basis: Literal["round_trip", "one_way"]
    fixed_latency_s: Nonnegative = 0.0
    cable_length_m: Positive | None = None


class SensingLocationResult(Contract):
    method: Literal["resistive_cable", "time_of_flight"]
    status: Literal["computed", "outside_declared_cable", "needs_data"]
    position_m: Nonnegative | None
    within_declared_cable: bool | None
    missing_inputs: tuple[str, ...] = ()
    uncertainty_interval_m: None = None
    uncertainty_interpretation: Literal["not_computed"] = "not_computed"
    leak_presence_inferred: Literal[False] = False


def _checked(request, cls):
    if not isinstance(request, cls):
        raise TypeError(f"{cls.__name__} required")
    return cls.model_validate(request.model_dump(mode="python"))


def _result(method, distance, length, missing=()):
    if missing:
        return SensingLocationResult(
            method=method, status="needs_data", position_m=None,
            within_declared_cable=None, missing_inputs=missing,
        )
    if not math.isfinite(distance) or distance < 0:
        raise ValueError("position exceeds finite nonnegative numerical support")
    inside = None if length is None else distance <= length
    return SensingLocationResult(
        method=method,
        status="outside_declared_cable" if inside is False else "computed",
        position_m=distance,
        within_declared_cable=inside,
    )


def locate_resistive_cable(request: ResistiveCableRequest) -> SensingLocationResult:
    """Infer an ideal short position from measured DC resistance.

    A single-conductor resistance coefficient counts both outgoing and return
    conductors. A complete-loop coefficient already includes that pair. The
    supplied fixed series resistance is subtracted once; it is never fitted.
    """
    q = _checked(request, ResistiveCableRequest)
    missing = tuple(k for k in ("voltage_v", "current_a", "resistance_per_length_ohm_m")
                    if getattr(q, k) is None)
    if missing:
        return _result("resistive_cable", None, q.cable_length_m, missing)
    total_resistance = q.voltage_v / q.current_a
    if not math.isfinite(total_resistance):
        raise ValueError("measured resistance exceeds finite numerical support")
    corrected_resistance = total_resistance - q.fixed_series_resistance_ohm
    if corrected_resistance < 0:
        raise ValueError("fixed series resistance exceeds measured resistance")
    paths = 2 if q.resistance_basis == "single_conductor" else 1
    distance = corrected_resistance / q.resistance_per_length_ohm_m / paths
    return _result("resistive_cable", distance, q.cable_length_m)


def locate_time_of_flight(request: TimeOfFlightRequest) -> SensingLocationResult:
    """Convert elapsed signal time to position using a declared path speed.

    Timing is an elapsed interval, not an absolute timestamp. Fixed instrument
    latency is removed once. A reflected signal traverses the path twice.
    """
    q = _checked(request, TimeOfFlightRequest)
    missing = tuple(k for k in ("elapsed_time_s", "propagation_speed_m_s")
                    if getattr(q, k) is None)
    if missing:
        return _result("time_of_flight", None, q.cable_length_m, missing)
    transit_time = q.elapsed_time_s - q.fixed_latency_s
    if transit_time < 0:
        raise ValueError("fixed latency exceeds elapsed time")
    paths = 2 if q.timing_basis == "round_trip" else 1
    distance = q.propagation_speed_m_s * (transit_time / paths)
    return _result("time_of_flight", distance, q.cable_length_m)
