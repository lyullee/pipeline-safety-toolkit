"""Absolute pressure integral mean for a declared linear-squared-pressure profile."""
from __future__ import annotations

from typing import Literal

from .contracts import Contract, Positive


class SquaredPressureProfileMeanRequest(Contract):
    first_pressure_pa_abs: Positive | None = None
    second_pressure_pa_abs: Positive | None = None
    squared_pressure_linear_in_distance_reviewed: bool = False


class SquaredPressureProfileMeanResult(Contract):
    status: Literal["computed","needs_data"]
    mean_pressure_pa_abs: Positive | None = None
    issues: tuple[str,...] = ()


def evaluate_squared_pressure_profile_mean(request: SquaredPressureProfileMeanRequest) -> SquaredPressureProfileMeanResult:
    """Average absolute pressure along a uniform distance coordinate.

    The assumed profile is linear in squared pressure, not linear in pressure.
    Endpoint labels do not infer flow direction or establish a gas-flow/EOS
    solution. The caller must review the profile assumption separately.
    """
    if not isinstance(request,SquaredPressureProfileMeanRequest):
        raise TypeError("SquaredPressureProfileMeanRequest required")
    q=SquaredPressureProfileMeanRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    if q.first_pressure_pa_abs is None:issues.append("missing first absolute pressure")
    if q.second_pressure_pa_abs is None:issues.append("missing second absolute pressure")
    if not q.squared_pressure_linear_in_distance_reviewed:
        issues.append("linear squared-pressure profile assumption requires review")
    if issues:return SquaredPressureProfileMeanResult(status="needs_data",issues=tuple(issues))
    hi=max(q.first_pressure_pa_abs,q.second_pressure_pa_abs)
    lo=min(q.first_pressure_pa_abs,q.second_pressure_pa_abs)
    ratio=lo/hi
    mean=hi if lo==hi else (hi*(2/3))*((1+ratio+ratio*ratio)/(1+ratio))
    return SquaredPressureProfileMeanResult(status="computed",mean_pressure_pa_abs=mean)
