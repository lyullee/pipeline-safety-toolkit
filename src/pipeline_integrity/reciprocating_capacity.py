"""Single-acting plunger displacement and caller-supplied volumetric delivery."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from .contracts import Contract, Fraction, Nonnegative, Positive


class SingleActingCapacityRequest(Contract):
    bore_diameter_m: Positive
    stroke_length_m: Positive
    delivery_strokes_per_second: Nonnegative
    active_plungers: int = Field(default=1,ge=1)
    volumetric_efficiency: Fraction | None = None
    single_acting_geometry_and_delivery_rate_reviewed: bool = False


class SingleActingCapacityResult(Contract):
    status: Literal["computed","needs_data"]
    displacement_per_delivery_stroke_m3: Positive
    theoretical_volume_flow_m3_s: Nonnegative
    delivered_volume_flow_m3_s: Nonnegative | None = None
    issues: tuple[str,...] = ()


def evaluate_single_acting_capacity(request: SingleActingCapacityRequest) -> SingleActingCapacityResult:
    """Use actual delivery strokes per second for each identical active plunger.

    A delivery stroke displaces one full bore area times stroke length. This
    is not a double-acting rod-side model; no slip or volumetric efficiency
    is predicted from rotational speed, pressure or fluid properties.
    """
    if not isinstance(request,SingleActingCapacityRequest):
        raise TypeError("SingleActingCapacityRequest required")
    q=SingleActingCapacityRequest.model_validate(request.model_dump(mode="python"))
    displacement=math.pi/4*q.bore_diameter_m*q.bore_diameter_m*q.stroke_length_m
    theoretical=displacement*q.delivery_strokes_per_second*q.active_plungers
    if not math.isfinite(displacement) or displacement<=0 or not math.isfinite(theoretical):
        raise ValueError("displacement or capacity exceeds finite numerical support")
    if q.delivery_strokes_per_second>0 and theoretical==0:
        raise ValueError("nonzero theoretical capacity is below finite numerical support")
    issues=[]
    if q.volumetric_efficiency is None:issues.append("missing supplied volumetric efficiency")
    if not q.single_acting_geometry_and_delivery_rate_reviewed:
        issues.append("single-acting geometry and effective delivery-stroke rate require review")
    if issues:
        return SingleActingCapacityResult(status="needs_data",displacement_per_delivery_stroke_m3=displacement,
            theoretical_volume_flow_m3_s=theoretical,issues=tuple(issues))
    delivered=theoretical*q.volumetric_efficiency
    if theoretical>0 and q.volumetric_efficiency>0 and delivered==0:
        raise ValueError("nonzero delivered capacity is below finite numerical support")
    return SingleActingCapacityResult(status="computed",displacement_per_delivery_stroke_m3=displacement,
        theoretical_volume_flow_m3_s=theoretical,delivered_volume_flow_m3_s=delivered)
