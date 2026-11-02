"""Supplied-mean-pressure elevation correction around existing Panhandle kernels."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Positive
from .models.hydraulics import GasCorrelation, gas_correlation


class ElevationCorrectedPanhandleRequest(Contract):
    flat_correlation: GasCorrelation
    outlet_minus_inlet_elevation_m: float
    mean_pressure_pa_abs: Positive | None = None
    elevation_coefficient_K_per_m: Positive = .0375/(.3048*1.8)
    steady_mean_state_height_approximation_reviewed: bool = False

    @model_validator(mode="after")
    def panhandle_only(self):
        if self.flat_correlation.method not in ("Panhandle_A","Panhandle_B"):
            raise ValueError("this height correction applies only to Panhandle A/B")
        return self


class ElevationCorrectedPanhandleResult(Contract):
    status: Literal["computed","needs_data","no_forward_solution"]
    raw_pressure_squared_drop_Pa2: Positive
    elevation_pressure_squared_cost_Pa2: float | None = None
    corrected_pressure_squared_drop_Pa2: float | None = None
    standard_volume_flow_m3_s: float | None = None
    issues: tuple[str,...] = ()


def evaluate_elevation_corrected_panhandle(request: ElevationCorrectedPanhandleRequest) -> ElevationCorrectedPanhandleResult:
    """Correct the driving pressure-square budget using supplied segment mean state.

    Positive height means the outlet is higher. The dimensional coefficient
    defaults to a common rounded engineering-unit approximation; callers may
    supply their reviewed value. Existing flat-segment flow kernels are reused.
    Their supplied pressure/reference-state and positive-pressure-drop domain
    are retained: this is not a reverse-pressure gravity-flow or network solver.
    """
    if not isinstance(request,ElevationCorrectedPanhandleRequest):
        raise TypeError("ElevationCorrectedPanhandleRequest required")
    q=ElevationCorrectedPanhandleRequest.model_validate(request.model_dump(mode="python"))
    p=q.flat_correlation
    raw=(p.inlet_pressure_pa_abs-p.outlet_pressure_pa_abs)*(p.inlet_pressure_pa_abs+p.outlet_pressure_pa_abs)
    if not math.isfinite(raw) or raw<=0:raise ValueError("pressure-square budget exceeds finite positive support")
    issues=[]
    if q.outlet_minus_inlet_elevation_m!=0 and q.mean_pressure_pa_abs is None:
        issues.append("missing supplied mean absolute pressure for nonzero elevation")
    if not q.steady_mean_state_height_approximation_reviewed:
        issues.append("steady mean-state height approximation requires review")
    if issues:return ElevationCorrectedPanhandleResult(status="needs_data",raw_pressure_squared_drop_Pa2=raw,issues=tuple(issues))
    cost=0. if q.outlet_minus_inlet_elevation_m==0 else (
        q.elevation_coefficient_K_per_m*p.relative_density_to_air*q.outlet_minus_inlet_elevation_m
        *q.mean_pressure_pa_abs*q.mean_pressure_pa_abs/(p.mean_temperature_k*p.mean_compressibility))
    corrected=raw-cost
    if not math.isfinite(cost) or not math.isfinite(corrected):raise ValueError("height-corrected pressure budget exceeds finite support")
    budget=dict(raw_pressure_squared_drop_Pa2=raw,elevation_pressure_squared_cost_Pa2=cost,
                corrected_pressure_squared_drop_Pa2=corrected)
    if corrected<0:
        return ElevationCorrectedPanhandleResult(status="no_forward_solution",**budget,
            issues=("height cost exceeds the declared forward pressure-square budget",))
    flat=gas_correlation(p).values["standard_volume_flow_m3_s"]
    exponent=.5394 if p.method=="Panhandle_A" else .51
    flow=flat*(corrected/raw)**exponent
    if not math.isfinite(flow) or flow<0:raise ValueError("corrected flow exceeds finite nonnegative support")
    return ElevationCorrectedPanhandleResult(status="computed",standard_volume_flow_m3_s=flow,**budget)
