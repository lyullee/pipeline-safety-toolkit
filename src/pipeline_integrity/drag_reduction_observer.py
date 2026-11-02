"""Conditional resistance-change observations under explicit flow/head/power bounds.

Measured friction drops or a supplied reduction establish the multiplier at a
reviewed state. They predict no dose chemistry or persistence after pumping.
Changing throughput requires separate reviewed frozen-multiplier/exponent
support. Hydraulic friction power is not motor/shaft power.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive

Reduction = Annotated[float, Field(le=1)]


class DragReductionRequest(Contract):
    baseline_friction_drop_pa: Positive | None = None
    observed_treated_friction_drop_pa: Nonnegative | None = None
    supplied_fractional_drag_reduction: Reduction | None = None
    baseline_volume_flow_magnitude_m3_s: Positive | None = None
    operating_boundary: Literal["same_flow", "fixed_friction_head", "fixed_hydraulic_friction_power"]
    supplied_baseline_friction_flow_exponent: Positive | None = None
    measured_state_and_calibration_basis_id: Name | None = None
    static_head_and_other_losses_separated_reviewed: bool = False
    same_turbulent_state_and_resistance_calibration_reviewed: bool = False
    frozen_exponent_and_multiplier_over_projected_flow_reviewed: bool = False
    reviewed_flow_support_m3_s: tuple[Positive, Positive] | None = None
    supplied_pump_flow_capacity_m3_s: Positive | None = None
    supplied_hydraulic_friction_power_capacity_W: Nonnegative | None = None

    @model_validator(mode="after")
    def unique_calibration(self):
        if self.observed_treated_friction_drop_pa is not None and self.supplied_fractional_drag_reduction is not None:
            raise ValueError("choose observed treated drop or supplied reduction")
        if self.reviewed_flow_support_m3_s is not None and self.reviewed_flow_support_m3_s[0] > self.reviewed_flow_support_m3_s[1]:
            raise ValueError("flow support must be ordered")
        return self


class DragReductionResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    treated_over_baseline_friction_multiplier: Nonnegative | None = None
    fractional_drag_reduction: float | None = None
    treated_friction_drop_at_baseline_flow_pa: Nonnegative | None = None
    projection_status: Literal["computed", "needs_data", "out_of_scope"] = "needs_data"
    projected_flow_magnitude_m3_s: Positive | None = None
    projected_over_baseline_flow_ratio: Positive | None = None
    projected_friction_drop_pa: Nonnegative | None = None
    projected_hydraulic_friction_power_W: Nonnegative | None = None
    pump_envelope_status: Literal["not_supplied", "within_supplied_limits", "outside_supplied_limits"] = "not_supplied"
    pump_flow_capacity_margin_m3_s: float | None = None
    hydraulic_friction_power_capacity_margin_W: float | None = None
    dose_chemistry_or_persistence_inferred: bool = False
    actual_pump_operating_point_or_shaft_power_solved: bool = False
    field_calibration_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_drag_reduction(request: DragReductionRequest) -> DragReductionResult:
    """Report conditional friction change and its declared operating-bound limit.

    Negative observed/supplied reduction means increased resistance and is not
    clipped to zero. A measured same-flow reduction does not establish its
    persistence over another flow, Reynolds state or downstream pump passage.
    """
    if not isinstance(request, DragReductionRequest):raise TypeError("DragReductionRequest required")
    q = DragReductionRequest.model_validate(request.model_dump(mode="python"))
    missing = []
    if q.baseline_friction_drop_pa is None:missing.append("baseline friction drop is required")
    if q.observed_treated_friction_drop_pa is None and q.supplied_fractional_drag_reduction is None:
        missing.append("observed treated drop or supplied reduction is required")
    if q.measured_state_and_calibration_basis_id is None or not q.static_head_and_other_losses_separated_reviewed or not q.same_turbulent_state_and_resistance_calibration_reviewed:
        missing.append("reviewed same-state turbulent friction-only calibration basis is required")
    if missing:return DragReductionResult(status="needs_data", issues=tuple(missing))
    try:
        multiplier = (q.observed_treated_friction_drop_pa / q.baseline_friction_drop_pa if q.observed_treated_friction_drop_pa is not None
                      else 1-q.supplied_fractional_drag_reduction)
        treated = multiplier*q.baseline_friction_drop_pa
        if not math.isfinite(multiplier) or not math.isfinite(treated):raise ArithmeticError
        if (q.observed_treated_friction_drop_pa and multiplier <= 0) or (multiplier > 0 and treated <= 0):raise ArithmeticError
        values = dict(status="computed", treated_over_baseline_friction_multiplier=multiplier,
            fractional_drag_reduction=1-multiplier, treated_friction_drop_at_baseline_flow_pa=treated)
        if q.baseline_volume_flow_magnitude_m3_s is None:
            return DragReductionResult(**values, issues=("flow projection needs baseline volume-flow magnitude",))
        if q.operating_boundary == "same_flow":
            ratio, pressure = 1., treated
        else:
            if q.supplied_baseline_friction_flow_exponent is None or not q.frozen_exponent_and_multiplier_over_projected_flow_reviewed:
                return DragReductionResult(**values, issues=("throughput projection needs reviewed frozen exponent and multiplier support",))
            if multiplier == 0:
                return DragReductionResult(**values, projection_status="out_of_scope", issues=("zero resistance has no finite positive flow for this fixed head/power boundary",))
            power_exponent = q.supplied_baseline_friction_flow_exponent + (q.operating_boundary == "fixed_hydraulic_friction_power")
            ratio = math.exp(-math.log(multiplier)/power_exponent)
            pressure = q.baseline_friction_drop_pa if q.operating_boundary == "fixed_friction_head" else q.baseline_friction_drop_pa/ratio
        flow = q.baseline_volume_flow_magnitude_m3_s*ratio
        power = flow*pressure
        if not all(math.isfinite(v) and v > 0 for v in (flow,ratio)) or not all(math.isfinite(v) and v >= 0 for v in (pressure,power)):
            raise ArithmeticError
        if pressure > 0 and power <= 0:raise ArithmeticError
        if q.reviewed_flow_support_m3_s is not None and not q.reviewed_flow_support_m3_s[0] <= flow <= q.reviewed_flow_support_m3_s[1]:
            return DragReductionResult(**values, projection_status="out_of_scope", issues=("projected flow is outside supplied calibration support; no clipping or extrapolation",))
        values.update(projection_status="computed", projected_flow_magnitude_m3_s=flow,
            projected_over_baseline_flow_ratio=ratio, projected_friction_drop_pa=pressure, projected_hydraulic_friction_power_W=power)
        margins = []
        if q.supplied_pump_flow_capacity_m3_s is not None:
            margin=q.supplied_pump_flow_capacity_m3_s-flow
            values['pump_flow_capacity_margin_m3_s']=margin;margins.append(margin)
        if q.supplied_hydraulic_friction_power_capacity_W is not None:
            margin=q.supplied_hydraulic_friction_power_capacity_W-power
            values['hydraulic_friction_power_capacity_margin_W']=margin;margins.append(margin)
        if margins:
            if not all(math.isfinite(v) for v in margins):raise ArithmeticError
            values['pump_envelope_status']='within_supplied_limits' if min(margins)>=0 else 'outside_supplied_limits'
        return DragReductionResult(**values)
    except (ArithmeticError, ValueError):
        return DragReductionResult(status="out_of_scope", issues=("resistance/head/power observation exceeds finite supported arithmetic",))
