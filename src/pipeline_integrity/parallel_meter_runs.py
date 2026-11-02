"""Conditional differential observation for identical quadratic meter runs.

This is a before/after observation on an unchanged total flow, not a new
network solver or an orifice coefficient predictor. Differential refers to the
declared meter-tap observation and is not silently relabelled permanent loss.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Contract, Name, Nonnegative

RunCount = Annotated[int, Field(strict=True, ge=1)]


class ParallelMeterRunsRequest(Contract):
    initial_run_count: RunCount | None = None
    remaining_run_count: RunCount | None = None
    initial_average_meter_differential_pa: Nonnegative | None = None
    common_branch_state_and_coefficient_basis_id: Name | None = None
    identical_parallel_runs_reviewed: bool = False
    unchanged_total_flow_reviewed: bool = False
    frozen_quadratic_meter_differential_law_reviewed: bool = False
    supplied_differential_measurement_limit_pa: Nonnegative | None = None


class ParallelMeterRunsResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    remaining_average_meter_differential_pa: Nonnegative | None = None
    remaining_over_initial_branch_flow_ratio: float | None = None
    remaining_over_initial_differential_ratio: float | None = None
    initial_total_flow_fraction_per_run: float | None = None
    remaining_total_flow_fraction_per_run: float | None = None
    supplied_differential_limit_margin_pa: float | None = None
    supplied_differential_limit_satisfied: bool | None = None
    coefficient_state_or_total_flow_inferred: bool = False
    permanent_loss_or_network_solution_inferred: bool = False
    field_or_standard_qualified: bool = False
    issues: tuple[str, ...] = ()


def observe_parallel_meter_run_change(request: ParallelMeterRunsRequest) -> ParallelMeterRunsResult:
    """Infer the shared meter differential after reviewed run withdrawal.

    Density, expansion, discharge coefficient and geometry must jointly retain
    the same quadratic flow-versus-differential relation. Unequal runs or
    changing gas state require their actual meter/network equations instead.
    A supplied instrument range is an observation bound, not an operating or
    safety approval.
    """
    if not isinstance(request, ParallelMeterRunsRequest):
        raise TypeError("ParallelMeterRunsRequest required")
    q = ParallelMeterRunsRequest.model_validate(request.model_dump(mode="python"))
    missing = ["missing " + name for name in ("initial_run_count", "remaining_run_count",
        "initial_average_meter_differential_pa", "common_branch_state_and_coefficient_basis_id") if getattr(q, name) is None]
    for name in ("identical_parallel_runs_reviewed", "unchanged_total_flow_reviewed", "frozen_quadratic_meter_differential_law_reviewed"):
        if not getattr(q, name):missing.append(name + " is required")
    if missing:
        return ParallelMeterRunsResult(status="needs_data", issues=tuple(missing))
    if q.remaining_run_count > q.initial_run_count:
        return ParallelMeterRunsResult(status="out_of_scope", issues=("this observation is defined for withdrawal from the initial active runs",))
    try:
        ratio = q.initial_run_count / q.remaining_run_count
        differential_ratio = ratio * ratio
        pressure = q.initial_average_meter_differential_pa * differential_ratio
        before_fraction, after_fraction = 1/q.initial_run_count, 1/q.remaining_run_count
        if not all(math.isfinite(x) and x > 0 for x in (ratio, differential_ratio, before_fraction, after_fraction)) or not math.isfinite(pressure):
            raise ArithmeticError
        if q.initial_average_meter_differential_pa and pressure <= 0:raise ArithmeticError
        values = dict(status="computed", remaining_average_meter_differential_pa=pressure,
            remaining_over_initial_branch_flow_ratio=ratio, remaining_over_initial_differential_ratio=differential_ratio,
            initial_total_flow_fraction_per_run=before_fraction, remaining_total_flow_fraction_per_run=after_fraction)
        if q.supplied_differential_measurement_limit_pa is not None:
            margin = q.supplied_differential_measurement_limit_pa - pressure
            if not math.isfinite(margin):raise ArithmeticError
            values.update(supplied_differential_limit_margin_pa=margin, supplied_differential_limit_satisfied=margin >= 0)
        return ParallelMeterRunsResult(**values)
    except (ArithmeticError, ValueError):
        return ParallelMeterRunsResult(status="out_of_scope", issues=("run-count/differential ratio exceeds finite arithmetic support",))
