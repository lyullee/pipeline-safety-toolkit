"""Evaluate an explicitly supplied, instrument-specific period calibration.

This observation operator estimates no calibration coefficients, fluid EOS,
temperature correction or sensor uncertainty. All coefficients use SI period
seconds and density kg/m3, irrespective of a device's original display units.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Positive


class VibratingDensityCalibration(Contract):
    instrument_id: Name
    calibration_id: Name
    quadratic_kg_m3_s2: float
    linear_kg_m3_s: float
    constant_kg_m3: float
    period_support_s: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    pressure_support_pa_abs: tuple[Positive, Positive]
    density_support_kg_m3: tuple[Positive, Positive] | None = None
    coefficient_units_and_applicability_reviewed: bool = False

    @model_validator(mode="after")
    def ordered_support(self):
        for name in ("period_support_s", "temperature_support_K", "pressure_support_pa_abs", "density_support_kg_m3"):
            bounds = getattr(self, name)
            if bounds is not None and bounds[0] > bounds[1]:
                raise ValueError(name + " must be ordered")
        return self


class VibratingDensityRequest(Contract):
    calibration: VibratingDensityCalibration | None = None
    observed_instrument_id: Name | None = None
    oscillation_period_s: Positive | None = None
    temperature_K: Positive | None = None
    pressure_pa_abs: Positive | None = None
    phase: Literal["single_liquid", "single_gas", "two_phase", "unknown"] = "unknown"
    single_phase_and_measurement_state_reviewed: bool = False


class VibratingDensityResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    density_kg_m3: Positive | None = None
    evaluated_period_s: Positive | None = None
    instrument_id: Name | None = None
    calibration_id: Name | None = None
    phase: Literal["single_liquid", "single_gas", "two_phase", "unknown"] = "unknown"
    coefficients_estimated: bool = False
    temperature_pressure_compensation_inferred: bool = False
    measurement_uncertainty_or_field_accuracy_qualified: bool = False
    issues: tuple[str, ...] = ()


def observe_vibrating_element_density(request: VibratingDensityRequest) -> VibratingDensityResult:
    """Apply reviewed dimensioned coefficients inside their stated support.

    The current temperature and absolute pressure screen the supplied
    calibration's domain. They are not substituted into an invented correction
    equation. Neither a calibration identity nor a predicted fluid density can
    replace a measured oscillation period or an instrument identity.
    """
    if not isinstance(request, VibratingDensityRequest):
        raise TypeError("VibratingDensityRequest required")
    q = VibratingDensityRequest.model_validate(request.model_dump(mode="python"))
    unknown = tuple("missing " + name for name in (
        "calibration", "observed_instrument_id", "oscillation_period_s", "temperature_K", "pressure_pa_abs"
    ) if getattr(q, name) is None)
    if unknown:
        return VibratingDensityResult(status="needs_data", issues=unknown)
    c = q.calibration
    metadata = dict(instrument_id=c.instrument_id, calibration_id=c.calibration_id, phase=q.phase)
    if q.phase == "two_phase":
        return VibratingDensityResult(status="out_of_scope", **metadata,
            issues=("multiphase response is not this single-phase calibration operator",))
    if q.phase == "unknown" or not q.single_phase_and_measurement_state_reviewed or not c.coefficient_units_and_applicability_reviewed:
        return VibratingDensityResult(status="needs_data", **metadata,
            issues=("single-phase measurement state and supplied coefficient units/applicability must be reviewed",))
    if q.observed_instrument_id != c.instrument_id:
        return VibratingDensityResult(status="out_of_scope", **metadata,
            issues=("observed instrument does not match the supplied calibration",))
    for value, bounds, name in (
        (q.oscillation_period_s, c.period_support_s, "period"),
        (q.temperature_K, c.temperature_support_K, "temperature"),
        (q.pressure_pa_abs, c.pressure_support_pa_abs, "absolute pressure"),
    ):
        if not bounds[0] <= value <= bounds[1]:
            return VibratingDensityResult(status="out_of_scope", **metadata,
                issues=(name + " is outside the supplied calibration support",))
    try:
        period = q.oscillation_period_s
        density = math.fsum((c.quadratic_kg_m3_s2 * period * period,
                             c.linear_kg_m3_s * period, c.constant_kg_m3))
        if not math.isfinite(density) or density <= 0:
            raise ArithmeticError
    except (ArithmeticError, ValueError):
        return VibratingDensityResult(status="out_of_scope", **metadata,
            issues=("calibration does not produce a finite positive supported density",))
    if c.density_support_kg_m3 is not None and not c.density_support_kg_m3[0] <= density <= c.density_support_kg_m3[1]:
        return VibratingDensityResult(status="out_of_scope", **metadata,
            issues=("evaluated density is outside the supplied calibration support",))
    return VibratingDensityResult(status="computed", density_kg_m3=density, evaluated_period_s=period, **metadata)
