"""Two-pressure density observation for one reviewed uniform static column.

Upward elevation and the pressure reference are explicit at each observation.
The output is a conditional uniform density, not a reconstructed density
profile, a fluid identification, or a custody-transfer accuracy statement.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative, Positive


class HydrostaticPressurePoint(Contract):
    pressure_pa: float | None = None
    upward_elevation_m: float | None = None
    pressure_reference: Literal["absolute", "common_gauge"]
    pressure_datum_id: Name
    vertical_frame_id: Name
    state_clock_id: Name

    @model_validator(mode="after")
    def absolute_pressure(self):
        if self.pressure_reference == "absolute" and self.pressure_pa is not None and self.pressure_pa <= 0:
            raise ValueError("absolute observed pressure must be positive")
        return self


class HydrostaticDensityObservationRequest(Contract):
    first: HydrostaticPressurePoint
    second: HydrostaticPressurePoint
    gravity_m_s2: Positive = 9.80665
    density_support_kg_m3: tuple[Positive, Positive] | None = None
    minimum_reviewed_vertical_separation_m: Nonnegative = 0.
    uniform_stationary_single_liquid_column_reviewed: bool = False
    common_pressure_datum_and_vertical_frame_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if self.density_support_kg_m3 is not None and self.density_support_kg_m3[0] >= self.density_support_kg_m3[1]:
            raise ValueError("density support bounds must increase strictly")
        return self


class HydrostaticDensityObservationResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope", "unidentifiable"]
    density_kg_m3: Positive | None = None
    second_minus_first_upward_elevation_m: float | None = None
    first_minus_second_pressure_pa: float | None = None
    reconstructed_first_minus_second_pressure_pa: float | None = None
    pressure_balance_residual_pa: float | None = None
    pressure_subtraction_condition_number: Positive | None = None
    pressure_input_half_ulp_sum_pa: Nonnegative | None = None
    binary_representation_diagnostic_only: bool = True
    pressure_reference: Literal["absolute", "common_gauge"] | None = None
    pressure_datum_id: str | None = None
    vertical_frame_id: str | None = None
    state_clock_id: str | None = None
    uniform_density_assumed: bool = True
    stratified_profile_resolved: bool = False
    absolute_pressure_or_phase_margin_inferred: bool = False
    uncertainty_computed: bool = False
    field_or_device_accuracy_qualified: bool = False
    forward_owner: str = ""
    issues: tuple[str, ...] = ()


def observe_hydrostatic_density(request: HydrostaticDensityObservationRequest) -> HydrostaticDensityObservationResult:
    """Infer rho from p1-p2=rho*g*(z2-z1), with no absolute-value sign repair.

    A common gauge reference cancels from the pressure difference; it is never
    converted into an invented absolute pressure. A zero height difference is
    unidentifiable only when its pressure difference is also zero. Unknown
    stratification is withheld rather than treated as a local density profile.
    The condition number and half-ULP sum describe binary floating-point
    representation only: neither estimates sensor error or measurement
    uncertainty, and neither reconstructs a difference lost before this call.
    """
    if not isinstance(request, HydrostaticDensityObservationRequest):
        raise TypeError("HydrostaticDensityObservationRequest required")
    q = HydrostaticDensityObservationRequest.model_validate(request.model_dump(mode="python"))
    a, b = q.first, q.second
    missing = []
    if not q.uniform_stationary_single_liquid_column_reviewed:
        missing.append("uniform stationary single-liquid column, including stratification, requires review")
    if not q.common_pressure_datum_and_vertical_frame_reviewed:
        missing.append("pressure datum and upward vertical frame require review")
    for field in ("pressure_reference", "pressure_datum_id", "vertical_frame_id", "state_clock_id"):
        if getattr(a, field) != getattr(b, field):missing.append("incompatible observation " + field)
    for label, point in (("first", a), ("second", b)):
        if point.pressure_pa is None:missing.append(label + " pressure is unknown")
        if point.upward_elevation_m is None:missing.append(label + " elevation is unknown")
    if missing:
        return HydrostaticDensityObservationResult(status="needs_data", issues=tuple(missing))
    dz = b.upward_elevation_m - a.upward_elevation_m
    dp = a.pressure_pa - b.pressure_pa
    if not math.isfinite(dz) or not math.isfinite(dp):
        return HydrostaticDensityObservationResult(status="out_of_scope", issues=("observed differences exceed finite support",))
    scale = max(abs(a.pressure_pa), abs(b.pressure_pa))
    relative_difference = abs(dp) / scale if scale else 0.
    condition = ((abs(a.pressure_pa) / scale + abs(b.pressure_pa) / scale) / relative_difference
                 if relative_difference else None)
    if condition is not None and not math.isfinite(condition):condition = None
    base = dict(second_minus_first_upward_elevation_m=dz, first_minus_second_pressure_pa=dp,
                pressure_subtraction_condition_number=condition,
                pressure_input_half_ulp_sum_pa=.5*(math.ulp(a.pressure_pa)+math.ulp(b.pressure_pa)),
                pressure_reference=a.pressure_reference, pressure_datum_id=a.pressure_datum_id,
                vertical_frame_id=a.vertical_frame_id, state_clock_id=a.state_clock_id)
    if dz == 0:
        return HydrostaticDensityObservationResult(status="unidentifiable" if dp == 0 else "out_of_scope", **base,
            issues=("equal elevations cannot identify density" if dp == 0 else "unequal pressures at the same height violate the reviewed static column",))
    if abs(dz) < q.minimum_reviewed_vertical_separation_m:
        return HydrostaticDensityObservationResult(status="out_of_scope", **base, issues=("reviewed minimum vertical separation not reached",))
    if dp == 0 or (dp > 0) != (dz > 0):
        return HydrostaticDensityObservationResult(status="out_of_scope", **base,
            issues=("pressure/elevation signs do not define a positive uniform static liquid density",))
    denominator = q.gravity_m_s2 * dz
    try:
        rho = dp / denominator if math.isfinite(denominator) and denominator else math.exp(
            math.log(abs(dp)) - math.log(abs(dz)) - math.log(q.gravity_m_s2))
    except OverflowError:
        rho = math.inf
    if not math.isfinite(rho) or rho <= 0:
        return HydrostaticDensityObservationResult(status="out_of_scope", **base, issues=("inferred density exceeds finite positive support",))
    if q.density_support_kg_m3 is not None and not q.density_support_kg_m3[0] <= rho <= q.density_support_kg_m3[1]:
        return HydrostaticDensityObservationResult(status="out_of_scope", **base, issues=("inferred density leaves caller-reviewed material support",))
    if a.pressure_reference == "absolute":
        from .models.hydraulics import Hydrostatic, hydrostatic
        forward = hydrostatic(Hydrostatic(inlet_pressure_pa_abs=a.pressure_pa, density_kg_m3=rho,
            outlet_minus_inlet_elevation_m=dz, gravity_m_s2=q.gravity_m_s2))
        if forward.status != "computed":
            return HydrostaticDensityObservationResult(status="out_of_scope", **base, issues=("existing absolute-pressure owner rejected reconstructed column",))
        reconstructed = forward.values["pressure_difference_pa"]
        owner = "pipeline_integrity.models.hydraulics.hydrostatic"
    else:
        from fluids.core import P_from_head
        reconstructed = P_from_head(dz, rho, q.gravity_m_s2)
        owner = "fluids.core.P_from_head (pressure difference only; absolute datum not invented)"
    residual = dp - reconstructed
    if not math.isfinite(reconstructed) or not math.isfinite(residual):
        return HydrostaticDensityObservationResult(status="out_of_scope", **base, issues=("forward pressure check exceeds native finite support",))
    return HydrostaticDensityObservationResult(status="computed", **base, density_kg_m3=rho,
        reconstructed_first_minus_second_pressure_pa=reconstructed, pressure_balance_residual_pa=residual,
        forward_owner=owner)
