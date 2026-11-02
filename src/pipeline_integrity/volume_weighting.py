"""Shared normalization and weighted-sum primitives for declared volume bases."""
from __future__ import annotations

import math
from collections.abc import Mapping


def normalize_component_volumes(volumes: Mapping[str,float]) -> tuple[float,dict[str,float]]:
    """Return total volume and normalized weights; do not drop zero components."""
    if not volumes or any(not math.isfinite(v) or v<0 for v in volumes.values()):
        raise ValueError("nonempty finite nonnegative component volumes required")
    try:total=math.fsum(volumes.values())
    except OverflowError as exc:raise ValueError("total volume exceeds finite support") from exc
    if total<=0:raise ValueError("positive total component volume required")
    return total,{key:v/total for key,v in volumes.items()}


def normalized_weighted_sum(values: Mapping[str,float],weights: Mapping[str,float]) -> float:
    """Sum one finite value per explicit normalized nonnegative weight."""
    if not values or set(values)!=set(weights):
        raise ValueError("value and weight component identities must agree")
    if any(not math.isfinite(v) for v in values.values()):raise ValueError("finite component values required")
    if any(not math.isfinite(w) or w<0 for w in weights.values()):raise ValueError("finite nonnegative weights required")
    if not math.isclose(math.fsum(weights.values()),1.,rel_tol=0.,abs_tol=1e-12):
        raise ValueError("component weights must sum to one")
    try:value=math.fsum(weights[k]*v for k,v in values.items())
    except OverflowError as exc:raise ValueError("weighted sum exceeds finite support") from exc
    if not math.isfinite(value):raise ValueError("weighted sum exceeds finite support")
    return value
