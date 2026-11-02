"""Conditional additive two-phase required-Cv estimate on one coefficient basis."""
from __future__ import annotations

import math
from typing import Literal
from .contracts import Contract,Name,Nonnegative


class PhaseCapacitySumRequest(Contract):
    required_liquid_cv: Nonnegative | None = None
    required_vapor_cv: Nonnegative | None = None
    common_cv_and_phase_partition_basis: Name
    common_pressure_phase_partition_and_cv_basis_reviewed: bool = False
    additive_two_phase_capacity_approximation_reviewed: bool = False


class PhaseCapacitySumResult(Contract):
    status: Literal["computed","needs_data"]
    approximate_combined_required_cv: Nonnegative | None = None
    phase_split_or_phase_sizing_performed: Literal[False] = False
    issues: tuple[str,...] = ()


def evaluate_phase_capacity_sum(request: PhaseCapacitySumRequest) -> PhaseCapacitySumResult:
    """Aggregate separately supplied phase requirements, not installed valve sizes.

    Both inputs must already use the same conventional Cv definition, reviewed
    pressure conditions and phase partition. This additive approximation does
    not calculate flashing/phase fractions, slip, actual two-phase capacity or
    cavitation; those are independent inputs/validation responsibilities.
    """
    if not isinstance(request,PhaseCapacitySumRequest):raise TypeError("PhaseCapacitySumRequest required")
    q=PhaseCapacitySumRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    if q.required_liquid_cv is None:issues.append("missing liquid phase required Cv")
    if q.required_vapor_cv is None:issues.append("missing vapor phase required Cv")
    if not q.common_pressure_phase_partition_and_cv_basis_reviewed:
        issues.append("common pressure, phase partition and Cv basis require review")
    if not q.additive_two_phase_capacity_approximation_reviewed:
        issues.append("additive two-phase capacity approximation requires review")
    if issues:return PhaseCapacitySumResult(status="needs_data",issues=tuple(issues))
    total=q.required_liquid_cv+q.required_vapor_cv
    if not math.isfinite(total):raise ValueError("combined phase coefficient exceeds finite support")
    return PhaseCapacitySumResult(status="computed",approximate_combined_required_cv=total)
