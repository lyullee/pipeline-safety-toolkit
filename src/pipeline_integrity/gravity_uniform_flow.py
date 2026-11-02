"""Uniform prismatic free-surface flow with a supplied dimensional Chezy factor."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Positive


class UniformChezyRequest(Contract):
    wetted_area_m2: Positive
    wetted_perimeter_m: Positive
    signed_downhill_bed_slope: float
    chezy_coefficient_m_half_per_s: Positive | None = None
    uniform_prismatic_free_surface_reviewed: bool = False


class UniformChezyResult(Contract):
    status: Literal["computed", "needs_data"]
    hydraulic_radius_m: Positive
    mean_velocity_m_s: float | None = None
    volume_flow_m3_s: float | None = None
    issues: tuple[str, ...] = ()


def evaluate_uniform_chezy_flow(request: UniformChezyRequest) -> UniformChezyResult:
    """Use area/perimeter and a signed downward bed slope along the caller's axis.

    The slope is the decrease in elevation per horizontal axial run (tan of
    the downward angle). The water surface is parallel to the bed under the
    declared uniform-flow assumption. C has units sqrt(m)/s; it is supplied,
    not inferred from roughness, and no pressurized or transient flow is solved.
    """
    if not isinstance(request, UniformChezyRequest):
        raise TypeError("UniformChezyRequest required")
    q=UniformChezyRequest.model_validate(request.model_dump(mode="python"))
    radius=q.wetted_area_m2/q.wetted_perimeter_m
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("hydraulic radius exceeds finite positive numerical support")
    issues=[]
    if q.chezy_coefficient_m_half_per_s is None:
        issues.append("missing dimensional Chezy coefficient")
    if not q.uniform_prismatic_free_surface_reviewed:
        issues.append("uniform prismatic free-surface assumption requires review")
    if issues:
        return UniformChezyResult(status="needs_data",hydraulic_radius_m=radius,issues=tuple(issues))
    speed=q.chezy_coefficient_m_half_per_s*math.sqrt(radius)*math.sqrt(abs(q.signed_downhill_bed_slope))
    velocity=math.copysign(speed,q.signed_downhill_bed_slope)
    flow=q.wetted_area_m2*velocity
    if not math.isfinite(flow) or not math.isfinite(velocity):
        raise ValueError("flow exceeds finite numerical support")
    if q.signed_downhill_bed_slope != 0 and (speed == 0 or flow == 0):
        raise ValueError("nonzero flow is below numerical support")
    return UniformChezyResult(status="computed",hydraulic_radius_m=radius,
        mean_velocity_m_s=velocity,volume_flow_m3_s=flow)
