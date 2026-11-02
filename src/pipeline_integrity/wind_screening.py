"""Local aerodynamic load and shedding-frequency calculations in SI units.

Users supply the flow state, geometry and coefficients for their reviewed case.
Neutral multipliers select no exposure, gust, terrain or design-code convention.
The frequency comparison reports only proximity to a supplied mode frequency;
it does not calculate vibration amplitude, stability, damage or structural response.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class WindLoadInput(_Input):
    air_density_kg_m3: Positive
    wind_speed_m_s: Nonnegative
    drag_coefficient: Nonnegative
    projected_area_m2: Nonnegative
    pressure_multiplier: Nonnegative = 1.0
    force_multiplier: Nonnegative = 1.0


class WindLoadResult(_Input):
    dynamic_pressure_Pa: Nonnegative
    applied_pressure_Pa: Nonnegative
    wind_force_N: Nonnegative
    structural_response_assessed: Literal[False] = False


def evaluate_wind_load(request: WindLoadInput) -> WindLoadResult:
    """Return kinetic pressure and drag force for a caller-defined projected area."""
    if not isinstance(request, WindLoadInput):
        raise TypeError("WindLoadInput is required")
    x = WindLoadInput.model_validate(request.model_dump(mode="python"))
    dynamic = 0.5 * x.air_density_kg_m3 * x.wind_speed_m_s * x.wind_speed_m_s
    pressure = dynamic * x.pressure_multiplier
    force = pressure * x.drag_coefficient * x.projected_area_m2 * x.force_multiplier
    if not all(math.isfinite(v) for v in (dynamic, pressure, force)):
        raise ValueError("wind-load result exceeds finite numerical support")
    return WindLoadResult(
        dynamic_pressure_Pa=dynamic, applied_pressure_Pa=pressure, wind_force_N=force
    )


class VortexFrequencyInput(_Input):
    cross_flow_speed_m_s: Nonnegative
    outside_diameter_m: Positive
    strouhal_number: Positive
    natural_frequency_Hz: Positive | None = None
    relative_nearness_band: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def paired_screen_inputs(self):
        if (self.natural_frequency_Hz is None) != (self.relative_nearness_band is None):
            raise ValueError("frequency and caller-selected nearness band must be supplied together")
        return self


class VortexFrequencyResult(_Input):
    lift_frequency_Hz: Nonnegative
    drag_frequency_Hz: Nonnegative
    lift_relative_offset: Nonnegative | None = None
    drag_relative_offset: Nonnegative | None = None
    lift_within_supplied_band: bool | None = None
    drag_within_supplied_band: bool | None = None
    amplitude_or_fatigue_assessed: Literal[False] = False


def evaluate_vortex_frequency(request: VortexFrequencyInput) -> VortexFrequencyResult:
    """Calculate cross-flow shedding and its inline second harmonic.

    The supplied outside diameter includes any insulation relevant to the flow.
    A positive Strouhal value is supplied explicitly; no Reynolds-dependent
    coefficient, natural-frequency model or resonance acceptance is inferred.
    """
    if not isinstance(request, VortexFrequencyInput):
        raise TypeError("VortexFrequencyInput is required")
    x = VortexFrequencyInput.model_validate(request.model_dump(mode="python"))
    lift = x.strouhal_number * x.cross_flow_speed_m_s / x.outside_diameter_m
    drag = 2.0 * lift
    if not all(math.isfinite(v) for v in (lift, drag)):
        raise ValueError("shedding frequency exceeds finite numerical support")
    values = dict(lift_frequency_Hz=lift, drag_frequency_Hz=drag)
    if x.natural_frequency_Hz is not None:
        for name, frequency in (("lift", lift), ("drag", drag)):
            offset = abs(frequency - x.natural_frequency_Hz) / x.natural_frequency_Hz
            if not math.isfinite(offset):
                raise ValueError("frequency comparison exceeds finite numerical support")
            values[name + "_relative_offset"] = offset
            values[name + "_within_supplied_band"] = offset <= x.relative_nearness_band
    return VortexFrequencyResult(**values)
