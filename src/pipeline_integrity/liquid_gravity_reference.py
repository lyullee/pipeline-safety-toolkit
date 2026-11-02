"""API-gravity/reference-specific-gravity conversion at a declared 60 F basis."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Positive


class LiquidGravityConversionRequest(Contract):
    mode: Literal["specific_gravity_to_api", "api_to_specific_gravity"]
    specific_gravity_60F: Positive | None = None
    api_gravity_60F: float | None = None
    common_60F_reference_reviewed: bool = False


class LiquidGravityConversionResult(Contract):
    status: Literal["computed", "needs_data"]
    specific_gravity_60F: Positive | None = None
    api_gravity_60F: float | None = None
    reference_temperature_K: float = 288.7055555555555
    issues: tuple[str,...] = ()


def evaluate_liquid_gravity_conversion(request: LiquidGravityConversionRequest) -> LiquidGravityConversionResult:
    """Convert the selected known reference coordinate; do not temperature-correct.

    Specific gravity is relative density at the same declared reference basis,
    rather than an absolute density with an implicit water-property shortcut.
    """
    if not isinstance(request,LiquidGravityConversionRequest):
        raise TypeError("LiquidGravityConversionRequest required")
    q=LiquidGravityConversionRequest.model_validate(request.model_dump(mode="python"))
    field="specific_gravity_60F" if q.mode=="specific_gravity_to_api" else "api_gravity_60F"
    issues=[]
    if getattr(q,field) is None: issues.append("missing "+field)
    if not q.common_60F_reference_reviewed: issues.append("common 60 F reference basis requires review")
    if issues: return LiquidGravityConversionResult(status="needs_data",issues=tuple(issues))
    if q.mode=="specific_gravity_to_api":
        sg=q.specific_gravity_60F
        api=141.5/sg-131.5
    else:
        api=q.api_gravity_60F
        if api <= -131.5: raise ValueError("API gravity must imply a finite positive specific gravity")
        sg=141.5/(api+131.5)
    if not math.isfinite(sg) or not math.isfinite(api) or sg<=0 or api<=-131.5:
        raise ValueError("reference conversion exceeds finite positive numerical support")
    return LiquidGravityConversionResult(status="computed",specific_gravity_60F=sg,api_gravity_60F=api)
