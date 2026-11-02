"""Additive-volume two-liquid density conversion with explicit identifiability."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Fraction, Nonnegative, Positive


class TwoLiquidMixtureRequest(Contract):
    mode: Literal["density_from_fraction", "fraction_from_density"]
    first_density_kg_m3: Positive | None = None
    second_density_kg_m3: Positive | None = None
    first_volume_fraction: Fraction | None = None
    mixture_density_kg_m3: Positive | None = None
    minimum_identifiable_density_contrast_kg_m3: Nonnegative = 0.0
    additive_volumes_and_common_state_reviewed: bool = False


class TwoLiquidMixtureResult(Contract):
    status: Literal["computed", "needs_data", "non_identifiable", "out_of_scope"]
    mixture_density_kg_m3: Positive | None = None
    first_volume_fraction: Fraction | None = None
    second_volume_fraction: Fraction | None = None
    density_contrast_kg_m3: Nonnegative | None = None
    issues: tuple[str, ...] = ()


def evaluate_two_liquid_mixture(request: TwoLiquidMixtureRequest) -> TwoLiquidMixtureResult:
    """Convert density/fraction at one common reviewed temperature/pressure.

    Fractions are volume fractions. Measured densities outside the convex
    component interval are reported outside scope, without clipping. The
    caller's minimum contrast is an identifiability policy, not a fitted
    uncertainty bound. No contraction, chemical reaction or state correction
    is inferred. Only the input selected by mode is consumed.
    """
    if not isinstance(request, TwoLiquidMixtureRequest):
        raise TypeError("TwoLiquidMixtureRequest required")
    q = TwoLiquidMixtureRequest.model_validate(request.model_dump(mode="python"))
    needed = ["first_density_kg_m3", "second_density_kg_m3"]
    needed.append("first_volume_fraction" if q.mode == "density_from_fraction" else "mixture_density_kg_m3")
    issues = ["missing " + name for name in needed if getattr(q, name) is None]
    if not q.additive_volumes_and_common_state_reviewed:
        issues.append("additive volumes and common thermodynamic state require review")
    if issues:
        return TwoLiquidMixtureResult(status="needs_data", issues=tuple(issues))
    a, b = q.first_density_kg_m3, q.second_density_kg_m3
    contrast = abs(a-b)
    if q.mode == "density_from_fraction":
        first = q.first_volume_fraction
        density = math.fsum((first*a, (1-first)*b))
    else:
        density = q.mixture_density_kg_m3
        if density < min(a,b) or density > max(a,b):
            return TwoLiquidMixtureResult(status="out_of_scope", mixture_density_kg_m3=density,
                density_contrast_kg_m3=contrast,
                issues=("measured density lies outside the additive-volume component interval",))
        if contrast <= q.minimum_identifiable_density_contrast_kg_m3:
            return TwoLiquidMixtureResult(status="non_identifiable", mixture_density_kg_m3=density,
                density_contrast_kg_m3=contrast,
                issues=("component density contrast does not resolve volume fractions under supplied policy",))
        first = (density-b)/(a-b)
    return TwoLiquidMixtureResult(status="computed", mixture_density_kg_m3=density,
        first_volume_fraction=first, second_volume_fraction=1-first,
        density_contrast_kg_m3=contrast)
