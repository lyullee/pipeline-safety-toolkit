"""Ideal passive-parcel transit in a sensing tube at declared constant air speed.

The delay starts when vapor is already inside the tube. Migration to the tube,
membrane permeation, mixing, dispersion and detector chemistry are not inferred.
A sampling cycle period is not automatically a parcel's residual waiting time.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Nonnegative, Positive
from .sensing_location import TimeOfFlightRequest, locate_time_of_flight


class VaporTubeDelayRequest(Contract):
    distance_to_detector_m: Nonnegative | None
    air_speed_m_s: Positive | None
    operation: Literal["continuous", "single_purge"]
    waiting_time_to_airflow_s: Nonnegative | None = None
    uninterrupted_purge_duration_s: Nonnegative | None = None
    tube_length_m: Positive | None = None
    constant_speed_passive_parcel_and_delay_origin_reviewed: bool = False

    @model_validator(mode="after")
    def operation_definition(self):
        if self.operation == "continuous":
            if self.waiting_time_to_airflow_s not in (None, 0.0):
                raise ValueError("continuous operation has zero waiting time")
            if self.uninterrupted_purge_duration_s is not None:
                raise ValueError("a finite purge window belongs to single_purge operation")
        return self


class VaporTubeLocationRequest(Contract):
    elapsed_since_airflow_start_s: Nonnegative | None
    air_speed_m_s: Positive | None
    fixed_detector_latency_s: Nonnegative = 0.0
    tube_length_m: Positive | None = None
    parcel_present_at_airflow_start_reviewed: bool = False
    constant_speed_uninterrupted_advection_reviewed: bool = False


class VaporTubeResult(Contract):
    method: Literal["parcel_delay", "parcel_location"]
    status: Literal["computed", "needs_data", "outside_declared_tube", "purge_too_short"]
    transit_time_s: Nonnegative | None = None
    waiting_time_s: Nonnegative | None = None
    parcel_arrival_delay_s: Nonnegative | None = None
    distance_to_detector_m: Nonnegative | None = None
    within_declared_tube: bool | None = None
    issues: tuple[str, ...] = ()
    uncertainty_computed: Literal[False] = False
    leak_presence_inferred: Literal[False] = False


def _checked(q, cls):
    if not isinstance(q, cls):
        raise TypeError(f"{cls.__name__} required")
    return cls.model_validate(q.model_dump(mode="python"))


def evaluate_vapor_tube_delay(request: VaporTubeDelayRequest) -> VaporTubeResult:
    """Compute transit plus a supplied wait, without inventing purge phase.

    A single purge must remain active long enough to deliver the parcel. A shorter
    purge returns an unresolved delay; motion through later cycles is not fitted.
    """
    q = _checked(request, VaporTubeDelayRequest)
    issues = []
    if not q.constant_speed_passive_parcel_and_delay_origin_reviewed:
        issues.append("passive-parcel constant-speed assumptions and vapor-entry time origin require review")
    for name in ("distance_to_detector_m", "air_speed_m_s"):
        if getattr(q, name) is None:
            issues.append("missing " + name)
    if q.operation == "single_purge":
        for name in ("waiting_time_to_airflow_s", "uninterrupted_purge_duration_s"):
            if getattr(q, name) is None:
                issues.append("missing " + name)
    if issues:
        return VaporTubeResult(method="parcel_delay", status="needs_data", issues=tuple(issues))
    travel = q.distance_to_detector_m / q.air_speed_m_s
    wait = 0.0 if q.operation == "continuous" else q.waiting_time_to_airflow_s
    if not math.isfinite(travel) or not math.isfinite(travel + wait):
        raise ValueError("parcel transit exceeds finite numerical support")
    inside = None if q.tube_length_m is None else q.distance_to_detector_m <= q.tube_length_m
    if inside is False:
        return VaporTubeResult(method="parcel_delay",status="outside_declared_tube",
            distance_to_detector_m=q.distance_to_detector_m,within_declared_tube=False,
            issues=("declared position lies beyond tube length",))
    if q.operation == "single_purge" and q.uninterrupted_purge_duration_s < travel:
        return VaporTubeResult(method="parcel_delay",status="purge_too_short",transit_time_s=travel,
            waiting_time_s=wait,distance_to_detector_m=q.distance_to_detector_m,
            within_declared_tube=inside,issues=("later-cycle transport is unresolved",))
    return VaporTubeResult(method="parcel_delay",status="computed",transit_time_s=travel,
        waiting_time_s=wait,parcel_arrival_delay_s=travel + wait,
        distance_to_detector_m=q.distance_to_detector_m,within_declared_tube=inside)


def locate_vapor_tube_parcel(request: VaporTubeLocationRequest) -> VaporTubeResult:
    """Locate a parcel present before airflow starts using one-way travel time.

    The detector latency is a supplied deterministic offset, removed once. Vapor
    entering after airflow starts cannot be located from that start clock alone.
    """
    q = _checked(request, VaporTubeLocationRequest)
    issues = []
    if not q.parcel_present_at_airflow_start_reviewed:
        issues.append("parcel presence at airflow start requires review")
    if not q.constant_speed_uninterrupted_advection_reviewed:
        issues.append("constant-speed uninterrupted parcel advection requires review")
    if issues:
        return VaporTubeResult(method="parcel_location",status="needs_data",issues=tuple(issues))
    result = locate_time_of_flight(TimeOfFlightRequest(
        elapsed_time_s=q.elapsed_since_airflow_start_s,propagation_speed_m_s=q.air_speed_m_s,
        timing_basis="one_way",fixed_latency_s=q.fixed_detector_latency_s,
        cable_length_m=q.tube_length_m))
    return VaporTubeResult(method="parcel_location",
        status="outside_declared_tube" if result.status == "outside_declared_cable" else result.status,
        distance_to_detector_m=result.position_m,within_declared_tube=result.within_declared_cable,
        issues=tuple("missing " + name for name in result.missing_inputs))
