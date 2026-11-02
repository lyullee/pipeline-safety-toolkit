"""Time to a supplied wall-loss boundary under a constant penetration rate."""
from __future__ import annotations

import math

from .contracts import Contract, Name, Nonnegative, Positive, digest


class ConstantLossLifeRequest(Contract):
    basis_id: Name
    current_remaining_wall_m: Positive
    supplied_critical_remaining_wall_m: Nonnegative
    supplied_penetration_rate_m_s: Nonnegative
    constant_loss_and_critical_boundary_reviewed: bool = False

def evaluate_constant_loss_life(request: ConstantLossLifeRequest):
    q = ConstantLossLifeRequest.model_validate(request.model_dump())
    base = dict(request_digest=digest(q), basis_id=q.basis_id,
                critical_wall_predicted=False, field_life_qualified=False)
    if not q.constant_loss_and_critical_boundary_reviewed:
        return dict(status="needs_data", **base)
    margin = q.current_remaining_wall_m - q.supplied_critical_remaining_wall_m
    if margin <= 0:
        return dict(status="boundary_already_reached", time_to_boundary_s=0.0,
                    wall_margin_m=margin, **base)
    if q.supplied_penetration_rate_m_s == 0:
        return dict(status="boundary_not_reached_under_zero_rate", time_to_boundary_s=None,
                    wall_margin_m=margin, **base)
    time = margin / q.supplied_penetration_rate_m_s
    if not math.isfinite(time):
        raise ValueError("time to boundary exceeds finite support")
    return dict(status="computed", time_to_boundary_s=time,
                wall_margin_m=margin, **base)
