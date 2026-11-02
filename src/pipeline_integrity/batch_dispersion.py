"""Constant-coefficient batch spreading on an unbounded axial coordinate.

Concentrations are volume fractions. A positive diffusivity smooths a moving
interface; it cannot describe a sharp finite-speed front or pipe-end effects.
The finite-pipe solver remains in ``batch_transport_solver``.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Nonnegative, Positive


class AxialDispersionRequest(Contract):
    method: Literal["supplied", "turbulent_taylor", "paired_turbulent"] = "supplied"
    supplied_dispersion_m2_s: Nonnegative | None = None
    mean_velocity_m_s: float | None = None
    internal_diameter_m: Positive | None = None
    darcy_friction_factor: Positive | None = None
    second_darcy_friction_factor: Positive | None = None
    reynolds_number: Positive | None = None
    second_reynolds_number: Positive | None = None
    turbulent_coefficient: Positive = 1.785
    turbulent_calibration_and_similar_properties_reviewed: bool = False
    paired_mixing_width_heuristic_reviewed: bool = False


class AxialDispersionResult(Contract):
    status: Literal["ok", "needs_data", "needs_review", "out_of_scope"]
    axial_dispersion_m2_s: Nonnegative | None = None
    reason: str
    empirical_accuracy_claim: bool = False


def evaluate_axial_dispersion(request: AxialDispersionRequest) -> AxialDispersionResult:
    q = AxialDispersionRequest.model_validate(request.model_dump())
    if q.method == "supplied":
        if q.supplied_dispersion_m2_s is None:
            return AxialDispersionResult(status="needs_data", reason="Supply the effective axial dispersion.")
        return AxialDispersionResult(status="ok", axial_dispersion_m2_s=q.supplied_dispersion_m2_s,
                                     reason="Caller-supplied effective dispersion, without calibration inference.")
    required = [q.mean_velocity_m_s, q.internal_diameter_m, q.darcy_friction_factor, q.reynolds_number]
    if q.method == "paired_turbulent":
        required.extend([q.second_darcy_friction_factor, q.second_reynolds_number])
    if any(value is None for value in required):
        return AxialDispersionResult(status="needs_data", reason="Each turbulent branch needs speed, diameter, Darcy factor and Reynolds number.")
    if min(q.reynolds_number, q.second_reynolds_number or q.reynolds_number) < 30000:
        return AxialDispersionResult(status="out_of_scope", reason="The selected high-Reynolds dispersion closure is not used below 30000.")
    if not q.turbulent_calibration_and_similar_properties_reviewed:
        return AxialDispersionResult(status="needs_review", reason="Review turbulent calibration and the component-property similarity assumption.")
    if q.method == "paired_turbulent" and not q.paired_mixing_width_heuristic_reviewed:
        return AxialDispersionResult(status="needs_review", reason="Averaging two predicted mixing widths is an additional empirical heuristic.")
    scale = q.turbulent_coefficient * abs(q.mean_velocity_m_s) * q.internal_diameter_m
    first = scale * math.sqrt(q.darcy_friction_factor)
    coefficient = first
    if q.method == "paired_turbulent":
        second = scale * math.sqrt(q.second_darcy_friction_factor)
        # Use one consistent prefactor: identical branches recover the single law.
        coefficient = ((math.sqrt(first) + math.sqrt(second)) / 2) ** 2
    if not math.isfinite(coefficient):
        return AxialDispersionResult(status="out_of_scope", reason="The coefficient exceeds finite arithmetic.")
    return AxialDispersionResult(status="ok", axial_dispersion_m2_s=coefficient,
                                 reason="Reviewed constant effective dispersion; field accuracy requires independent calibration.")


class BatchInterfaceRequest(Contract):
    axial_positions_m: tuple[float, ...] = Field(min_length=1)
    elapsed_time_s: Nonnegative
    axial_dispersion_m2_s: Nonnegative | None = None
    mean_velocity_m_s: float = 0.0
    initial_interface_position_m: float = 0.0
    cross_section_area_m2: Positive | None = None
    lower_volume_fraction: float = Field(default=0.01, gt=0, lt=1)
    upper_volume_fraction: float = Field(default=0.99, gt=0, lt=1)

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if self.lower_volume_fraction >= self.upper_volume_fraction:
            raise ValueError("mixing thresholds must be strictly increasing")
        return self


class BatchInterfaceResult(Contract):
    status: Literal["ok", "needs_data", "out_of_scope"]
    displacing_component_volume_fractions: tuple[float, ...] | None = None
    interface_centre_m: float | None = None
    mixing_interval_m: tuple[float, float] | None = None
    mixing_length_m: Nonnegative | None = None
    mixing_volume_m3: Nonnegative | None = None
    reason: str
    finite_pipe_boundary_claim: bool = False


def evaluate_batch_interface(request: BatchInterfaceRequest) -> BatchInterfaceResult:
    """Translate and diffuse a unit step with the positive-diffusion heat kernel."""
    from scipy.special import erfcinv

    q = BatchInterfaceRequest.model_validate(request.model_dump())
    if q.axial_dispersion_m2_s is None:
        return BatchInterfaceResult(status="needs_data", reason="Effective dispersion is required; missing does not mean zero.")
    centre = q.initial_interface_position_m + q.mean_velocity_m_s * q.elapsed_time_s
    variance_scale = q.axial_dispersion_m2_s * q.elapsed_time_s
    if not math.isfinite(centre) or not math.isfinite(variance_scale):
        return BatchInterfaceResult(status="out_of_scope", reason="The translated coordinate or spreading scale exceeds finite arithmetic.")
    if q.axial_dispersion_m2_s > 0 and q.elapsed_time_s > 0 and variance_scale == 0:
        return BatchInterfaceResult(status="out_of_scope", reason="The positive spreading scale is below floating-point resolution.")
    if variance_scale == 0:
        values = tuple(1.0 if x < centre else 0.0 if x > centre else 0.5 for x in q.axial_positions_m)
        interval, length = (centre, centre), 0.0
    else:
        width = 2 * math.sqrt(variance_scale)
        values = tuple(0.5 * math.erfc((x - centre) / width) for x in q.axial_positions_m)
        left_quantile = float(erfcinv(2 * q.upper_volume_fraction))
        right_quantile = float(erfcinv(2 * q.lower_volume_fraction))
        interval = (centre + width * left_quantile, centre + width * right_quantile)
        length = width * (right_quantile - left_quantile)
        if not interval[1] > interval[0]:
            return BatchInterfaceResult(status="out_of_scope", reason="The mixing interval is unresolved at the supplied coordinate origin.")
    volume = None if q.cross_section_area_m2 is None else q.cross_section_area_m2 * length
    if not all(math.isfinite(x) for x in (*interval, length)) or (volume is not None and not math.isfinite(volume)):
        return BatchInterfaceResult(status="out_of_scope", reason="The requested mixing interval exceeds finite arithmetic.")
    return BatchInterfaceResult(status="ok", displacing_component_volume_fractions=values,
        interface_centre_m=centre, mixing_interval_m=interval, mixing_length_m=length,
        mixing_volume_m3=volume,
        reason="Unbounded constant-speed, constant-dispersion volume-fraction step; thresholds are caller selected.")
