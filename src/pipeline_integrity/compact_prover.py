"""Two-interval pulse interpolation at an explicitly supplied volume state.

The observed pulse factor has units pulses/m3. A dimensionless true/indicated
volume multiplier is a different output and needs a declared nominal factor.
This module neither calculates steel/fluid state corrections nor establishes
timing, prover or custody-transfer accuracy.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Contract, Name, Positive

WholePulses = Annotated[int, Field(strict=True, ge=1)]


class CompactProverRequest(Contract):
    meter_id: Name | None = None
    proving_run_id: Name | None = None
    corrected_reference_volume_m3: Positive | None = None
    meter_volume_state_basis_id: Name | None = None
    reference_volume_correction_basis_id: Name | None = None
    prover_sweep_elapsed_time_s: Positive | None = None
    whole_pulse_interval_elapsed_time_s: Positive | None = None
    whole_meter_pulses_in_interval: WholePulses | None = None
    elapsed_time_clock_basis_id: Name | None = None
    volume_at_declared_meter_state_reviewed: bool = False
    clock_scale_and_interval_pairing_reviewed: bool = False
    representative_stationary_pulse_frequency_reviewed: bool = False
    nominal_indicated_pulse_factor_per_m3: Positive | None = None
    nominal_factor_basis_id: Name | None = None


class CompactProverResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    observed_pulse_factor_per_m3: Positive | None = None
    reciprocal_volume_per_pulse_m3: Positive | None = None
    interpolated_pulses_during_sweep: Positive | None = None
    volume_state_basis_id: Name | None = None
    dimensionless_factor_status: Literal["not_requested", "computed", "needs_data"] = "not_requested"
    true_over_indicated_volume_multiplier: Positive | None = None
    state_or_steel_corrections_inferred: bool = False
    field_or_custody_transfer_accuracy_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_compact_prover(request: CompactProverRequest) -> CompactProverResult:
    """Infer pulses/m3 from a reviewed same-state swept volume and two times.

    The whole-pulse interval is not assumed equal to, or wholly inside, the
    sweep interval. Its measured average pulse frequency must represent the
    sweep. Timestamps, elapsed times and clock counts are not interchangeable;
    callers supply positive elapsed seconds on a reviewed timing basis.
    """
    if not isinstance(request, CompactProverRequest):
        raise TypeError("CompactProverRequest required")
    q = CompactProverRequest.model_validate(request.model_dump(mode="python"))
    missing = ["missing " + name for name in (
        "meter_id", "proving_run_id", "corrected_reference_volume_m3", "meter_volume_state_basis_id",
        "reference_volume_correction_basis_id", "prover_sweep_elapsed_time_s",
        "whole_pulse_interval_elapsed_time_s", "whole_meter_pulses_in_interval", "elapsed_time_clock_basis_id"
    ) if getattr(q, name) is None]
    for name in ("volume_at_declared_meter_state_reviewed", "clock_scale_and_interval_pairing_reviewed",
                 "representative_stationary_pulse_frequency_reviewed"):
        if not getattr(q, name):
            missing.append(name + " is required")
    if missing:
        return CompactProverResult(status="needs_data", issues=tuple(missing))
    try:
        log_pulses = math.log(q.whole_meter_pulses_in_interval) + math.log(q.prover_sweep_elapsed_time_s) - math.log(q.whole_pulse_interval_elapsed_time_s)
        log_factor = log_pulses - math.log(q.corrected_reference_volume_m3)
        pulses, factor, reciprocal = math.exp(log_pulses), math.exp(log_factor), math.exp(-log_factor)
        if not all(math.isfinite(v) and v > 0 for v in (pulses, factor, reciprocal)):
            raise ArithmeticError
    except (ArithmeticError, ValueError):
        return CompactProverResult(status="out_of_scope", issues=("pulse/volume interpolation exceeds finite positive arithmetic support",))
    values = dict(status="computed", observed_pulse_factor_per_m3=factor,
        reciprocal_volume_per_pulse_m3=reciprocal, interpolated_pulses_during_sweep=pulses,
        volume_state_basis_id=q.meter_volume_state_basis_id)
    if q.nominal_indicated_pulse_factor_per_m3 is not None:
        if q.nominal_factor_basis_id is None:
            values.update(dimensionless_factor_status="needs_data", issues=("nominal pulse factor needs its same-state/convention basis",))
        else:
            multiplier = q.nominal_indicated_pulse_factor_per_m3 / factor
            if not math.isfinite(multiplier) or multiplier <= 0:
                return CompactProverResult(status="out_of_scope", issues=("true/indicated multiplier exceeds arithmetic support",))
            values.update(dimensionless_factor_status="computed", true_over_indicated_volume_multiplier=multiplier)
    return CompactProverResult(**values)
