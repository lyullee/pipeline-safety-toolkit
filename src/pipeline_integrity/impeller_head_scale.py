"""Impeller tip-speed head scale with a supplied dimensionless head coefficient."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field
from .contracts import Contract, Positive


class ImpellerHeadScaleRequest(Contract):
    impeller_diameter_m: Positive
    rotational_frequency_hz: Positive
    head_coefficient: Positive | None = None
    identical_stages: int = Field(default=1,ge=1)
    gravity_m_s2: Positive = 9.80665
    coefficient_and_identical_stage_basis_reviewed: bool = False


class ImpellerHeadScaleResult(Contract):
    status: Literal["computed","needs_data"]
    tip_speed_m_s: Positive
    estimated_head_m: Positive | None = None
    issues: tuple[str,...] = ()


def evaluate_impeller_head_scale(request: ImpellerHeadScaleRequest) -> ImpellerHeadScaleResult:
    """Use H_stage = psi*U_tip**2/g; do not infer a performance curve.

    psi refers to this exact tip-speed convention, and stages are identical.
    No universal empirical design coefficient, hydraulic efficiency, flow-rate
    operating point or field accuracy is inferred from diameter and speed.
    """
    if not isinstance(request,ImpellerHeadScaleRequest):raise TypeError("ImpellerHeadScaleRequest required")
    q=ImpellerHeadScaleRequest.model_validate(request.model_dump(mode="python"))
    speed=math.pi*q.impeller_diameter_m*q.rotational_frequency_hz
    if not math.isfinite(speed) or speed<=0:raise ValueError("tip speed exceeds finite positive support")
    issues=[]
    if q.head_coefficient is None:issues.append("missing supplied dimensionless head coefficient")
    if not q.coefficient_and_identical_stage_basis_reviewed:
        issues.append("coefficient and identical-stage basis require review")
    if issues:return ImpellerHeadScaleResult(status="needs_data",tip_speed_m_s=speed,issues=tuple(issues))
    head=q.head_coefficient*speed*speed/q.gravity_m_s2*q.identical_stages
    if not math.isfinite(head) or head<=0:raise ValueError("head scale exceeds finite positive support")
    return ImpellerHeadScaleResult(status="computed",tip_speed_m_s=speed,estimated_head_m=head)
