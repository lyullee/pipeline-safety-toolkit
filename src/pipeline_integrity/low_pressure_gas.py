"""Typed near-ambient Spitzglass-low flow using the existing external kernel."""
from __future__ import annotations

import math
from typing import Literal
from .contracts import Contract,Positive
from .models.hydraulics import GasCorrelation

PSI_PA=.45359237*9.80665/.0254**2


class LowPressureSpitzglassRequest(GasCorrelation):
    method: Literal["Spitzglass_low"] = "Spitzglass_low"
    ambient_pressure_pa_abs: Positive
    maximum_inlet_gauge_pressure_pa: Positive = PSI_PA
    low_pressure_uniform_state_domain_reviewed: bool = False


class LowPressureSpitzglassResult(Contract):
    status: Literal["computed","needs_data","out_of_scope"]
    inlet_gauge_pressure_pa: float
    standard_temperature_k: Positive
    standard_pressure_pa_abs: Positive
    standard_volume_flow_m3_s: Positive | None = None
    issues: tuple[str,...] = ()


def evaluate_low_pressure_spitzglass(request: LowPressureSpitzglassRequest) -> LowPressureSpitzglassResult:
    """Keep absolute, ambient and standard-state pressure bases distinct.

    The default near-ambient inlet gauge-pressure support is below one psi.
    A different caller limit requires its own applicability evidence. The
    existing library reference-corrected low-pressure formulation is used;
    no exact identity with every rounded historical prefactor is asserted.
    """
    from fluids.compressible import Spitzglass_low
    if not isinstance(request,LowPressureSpitzglassRequest):raise TypeError("LowPressureSpitzglassRequest required")
    q=LowPressureSpitzglassRequest.model_validate(request.model_dump(mode="python"))
    gauge=q.inlet_pressure_pa_abs-q.ambient_pressure_pa_abs
    base=dict(inlet_gauge_pressure_pa=gauge,standard_temperature_k=q.standard_temperature_k,
              standard_pressure_pa_abs=q.standard_pressure_pa_abs)
    if gauge>=q.maximum_inlet_gauge_pressure_pa or q.outlet_pressure_pa_abs<q.ambient_pressure_pa_abs:
        return LowPressureSpitzglassResult(status="out_of_scope",**base,
            issues=("pressure is outside the declared non-vacuum near-ambient gauge-pressure support",))
    if not q.low_pressure_uniform_state_domain_reviewed:
        return LowPressureSpitzglassResult(status="needs_data",**base,
            issues=("low-pressure uniform-state empirical applicability requires review",))
    flow=Spitzglass_low(SG=q.relative_density_to_air,Tavg=q.mean_temperature_k,Zavg=q.mean_compressibility,
        L=q.length_m,D=q.diameter_m,P1=q.inlet_pressure_pa_abs,P2=q.outlet_pressure_pa_abs,
        Ts=q.standard_temperature_k,Ps=q.standard_pressure_pa_abs,E=q.empirical_efficiency)
    if not math.isfinite(flow) or flow<=0:raise ValueError("directed low-pressure flow exceeds finite positive support")
    return LowPressureSpitzglassResult(status="computed",standard_volume_flow_m3_s=flow,**base)
