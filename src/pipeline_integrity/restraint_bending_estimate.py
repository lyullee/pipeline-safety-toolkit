"""A supplied load-to-moment coefficient and elastic section-stress coordinate."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative


class RestraintBendingEstimateRequest(Contract):
    uniform_transverse_load_n_m: Nonnegative | None = None
    span_m: Positive | None = None
    section_modulus_m3: Positive | None = None
    supplied_moment_coefficient: Nonnegative | None = None
    coefficient_basis_id: Name | None = None
    coefficient_and_elastic_section_basis_reviewed: bool = False


class RestraintBendingEstimateResult(Contract):
    status: Literal['computed','needs_data']
    coefficient_basis_id: Name | None
    estimated_moment_nm: Nonnegative | None = None
    nominal_extreme_fibre_stress_pa: Nonnegative | None = None
    issues: tuple[str,...] = ()
    support_stiffness_or_real_stress_qualified: Literal[False] = False


def evaluate_restraint_bending_estimate(request: RestraintBendingEstimateRequest) -> RestraintBendingEstimateResult:
    """Expose the supplied coefficient without identifying actual end stiffness.

    The caller may provide a reviewed heuristic or a coefficient from an
    existing frame solution. Section modulus must belong to the bending axis;
    no geometry, allowable stress or support category is inferred.
    """
    if not isinstance(request,RestraintBendingEstimateRequest):raise TypeError('RestraintBendingEstimateRequest required')
    q=RestraintBendingEstimateRequest.model_validate(request.model_dump(mode='python'))
    base=dict(coefficient_basis_id=q.coefficient_basis_id)
    missing=['missing '+k for k in ('uniform_transverse_load_n_m','span_m','section_modulus_m3','supplied_moment_coefficient','coefficient_basis_id') if getattr(q,k) is None]
    if not q.coefficient_and_elastic_section_basis_reviewed:missing.append('coefficient and elastic section basis require review')
    if missing:return RestraintBendingEstimateResult(status='needs_data',issues=tuple(missing),**base)
    moment=q.supplied_moment_coefficient*q.uniform_transverse_load_n_m*q.span_m**2
    stress=moment/q.section_modulus_m3
    if not all(math.isfinite(v) for v in (moment,stress)):raise ValueError('bending estimate exceeds finite numeric support')
    return RestraintBendingEstimateResult(status='computed',**base,estimated_moment_nm=moment,nominal_extreme_fibre_stress_pa=stress)
