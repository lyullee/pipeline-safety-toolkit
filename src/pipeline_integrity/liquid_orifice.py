"""Conditional incompressible release through an actual stationary opening.

The discharge coefficient is supplied for the reviewed opening and liquid
domain. There is no finite pipe-beta correction, inferred default coefficient,
vena-contracta model, tank depressurization solve or cavitating-flow extension.
"""
from __future__ import annotations

import math
import sys
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Contract, Name, Nonnegative, Positive

DischargeCoefficient = Annotated[float, Field(gt=0, le=1)]


class LiquidOpeningReleaseRequest(Contract):
    opening_area_m2: Nonnegative | None = None
    upstream_available_pressure_pa_abs: Positive | None = None
    receiver_static_pressure_pa_abs: Positive | None = None
    liquid_density_kg_m3: Positive | None = None
    supplied_discharge_coefficient: DischargeCoefficient | None = None
    liquid_state_basis_id: Name | None = None
    coefficient_geometry_basis_id: Name | None = None
    saturation_pressure_pa_abs: Positive | None = None
    steady_duration_s: Nonnegative | None = None
    available_pressure_head_and_stationary_opening_reviewed: bool = False
    noncavitating_single_liquid_incompressible_domain_reviewed: bool = False
    supplied_coefficient_applicability_reviewed: bool = False
    constant_boundary_state_for_duration_reviewed: bool = False


class LiquidOpeningReleaseResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    available_pressure_difference_pa: float | None = None
    actual_volume_flow_m3_s: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    opening_area_mean_flux_velocity_m_s: Nonnegative | None = None
    ideal_available_head_speed_m_s: Nonnegative | None = None
    effective_total_head_coefficient: Positive | None = None
    native_pressure_relation_residual_pa: float | None = None
    available_pressure_volume_power_W: Nonnegative | None = None
    amount_status: Literal["not_requested", "computed_steady", "needs_data"] = "not_requested"
    released_volume_m3: Nonnegative | None = None
    released_mass_kg: Nonnegative | None = None
    discharge_coefficient_estimated: bool = False
    vena_contracta_or_actual_jet_velocity_inferred: bool = False
    cavitation_or_pressure_decay_solved: bool = False
    field_calibration_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_liquid_opening_release(request: LiquidOpeningReleaseRequest) -> LiquidOpeningReleaseResult:
    """Return one-way flow from supplied available head and opening data.

    Available upstream pressure is a mechanical-head coordinate at the
    opening, referred to the receiver static pressure. Approach kinetic or
    elevation terms must be included or demonstrably negligible under the
    caller's review. Q/A is an area-mean flux coordinate, not a prediction of
    the velocity or area of the contracted external jet. Optional saturation
    input screens endpoints only; it cannot establish a throat phase margin.
    """
    if not isinstance(request, LiquidOpeningReleaseRequest):
        raise TypeError("LiquidOpeningReleaseRequest required")
    q = LiquidOpeningReleaseRequest.model_validate(request.model_dump(mode="python"))
    missing = []
    for field in ("opening_area_m2", "upstream_available_pressure_pa_abs", "receiver_static_pressure_pa_abs",
                  "liquid_density_kg_m3", "supplied_discharge_coefficient", "liquid_state_basis_id", "coefficient_geometry_basis_id"):
        if getattr(q, field) is None:missing.append("missing " + field)
    for field in ("available_pressure_head_and_stationary_opening_reviewed",
                  "noncavitating_single_liquid_incompressible_domain_reviewed", "supplied_coefficient_applicability_reviewed"):
        if not getattr(q, field):missing.append(field + " is required")
    if missing:
        return LiquidOpeningReleaseResult(status="needs_data", issues=tuple(missing))
    dp = q.upstream_available_pressure_pa_abs - q.receiver_static_pressure_pa_abs
    if dp < 0:
        return LiquidOpeningReleaseResult(status="out_of_scope", available_pressure_difference_pa=dp,
            issues=("reverse release is outside this one-way liquid/coefficient contract",))
    if q.saturation_pressure_pa_abs is not None and min(q.upstream_available_pressure_pa_abs, q.receiver_static_pressure_pa_abs) <= q.saturation_pressure_pa_abs:
        return LiquidOpeningReleaseResult(status="out_of_scope", available_pressure_difference_pa=dp,
            issues=("endpoint pressure is incompatible with the supplied single-liquid saturation screen",))
    ideal = flux_speed = coefficient = residual = None
    if q.opening_area_m2 == 0:
        flow = mass = power = 0.
    else:
        try:
            ideal = math.exp(.5 * (math.log(2.) + math.log(dp) - math.log(q.liquid_density_kg_m3))) if dp else 0.
            log_coefficient = -2 * math.log(q.supplied_discharge_coefficient)
            if log_coefficient > math.log(sys.float_info.max):raise OverflowError
            coefficient = math.exp(log_coefficient)
            flux_speed = q.supplied_discharge_coefficient * ideal
            flow = q.opening_area_m2 * flux_speed
            mass = q.liquid_density_kg_m3 * flow
            power = dp * flow
            from fluids.core import dP_from_K
            reconstructed = dP_from_K(coefficient, q.liquid_density_kg_m3, flux_speed)
            residual = dp - reconstructed
            if not all(math.isfinite(x) for x in (ideal, flux_speed, flow, mass, power, reconstructed, residual)):
                raise OverflowError
            if dp and min(ideal, flux_speed, flow, mass, reconstructed) <= 0:
                raise OverflowError
        except (OverflowError, ZeroDivisionError):
            return LiquidOpeningReleaseResult(status="out_of_scope", available_pressure_difference_pa=dp,
                issues=("nonzero release lies outside finite native pressure/flux arithmetic support",))
    values = dict(status="computed", available_pressure_difference_pa=dp, actual_volume_flow_m3_s=flow,
        mass_flow_kg_s=mass, opening_area_mean_flux_velocity_m_s=flux_speed, ideal_available_head_speed_m_s=ideal,
        effective_total_head_coefficient=coefficient, native_pressure_relation_residual_pa=residual,
        available_pressure_volume_power_W=power)
    if q.steady_duration_s is not None:
        if q.steady_duration_s and not q.constant_boundary_state_for_duration_reviewed:
            values.update(amount_status="needs_data", issues=("release amount needs a reviewed constant-boundary time interval",))
        else:
            volume, released_mass = flow * q.steady_duration_s, mass * q.steady_duration_s
            if not math.isfinite(volume) or not math.isfinite(released_mass):
                return LiquidOpeningReleaseResult(status="out_of_scope", available_pressure_difference_pa=dp,
                    issues=("integrated steady amount exceeds finite support",))
            values.update(amount_status="computed_steady", released_volume_m3=volume, released_mass_kg=released_mass)
    return LiquidOpeningReleaseResult(**values)
