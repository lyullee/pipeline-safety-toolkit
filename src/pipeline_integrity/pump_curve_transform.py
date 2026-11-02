"""Declared same-casing pump-curve approximation under speed/impeller changes."""
import math

from pydantic import Field, model_validator

from .contracts import Calculation, Contract, Nonnegative, Positive


class SameCasingPumpCurveTransform(Contract):
    reference_flow_m3_s: tuple[Nonnegative, ...] = Field(min_length=2, max_length=100000)
    reference_head_m: tuple[Nonnegative, ...] = Field(min_length=2, max_length=100000)
    speed_ratio: Positive = 1.0
    impeller_diameter_ratio: Positive = 1.0

    @model_validator(mode="after")
    def paired_curve(self):
        if len(self.reference_flow_m3_s) != len(self.reference_head_m):
            raise ValueError("each reference flow requires one head ordinate")
        if any(b <= a for a,b in zip(self.reference_flow_m3_s,self.reference_flow_m3_s[1:])):
            raise ValueError("reference flow coordinates must increase strictly")
        return self


def scale_same_casing_pump_curve(request: SameCasingPumpCurveTransform) -> Calculation:
    """Transform supplied curve coordinates without fitting a pump characteristic.

    The diameter ratio changes an impeller in the same casing; it does not scale
    all dimensions of a family of geometrically similar machines.
    """
    q=SameCasingPumpCurveTransform.model_validate(request.model_dump(mode="python"))
    scale=q.speed_ratio*q.impeller_diameter_ratio
    head_scale=scale*scale
    if not all(math.isfinite(x) and x > 0 for x in (scale,head_scale)):
        raise ValueError("curve scaling exceeds finite positive numerical support")
    flow=[scale*x for x in q.reference_flow_m3_s]
    head=[head_scale*x for x in q.reference_head_m]
    if not all(math.isfinite(x) for x in (*flow,*head)):
        raise ValueError("transformed curve exceeds finite numerical support")
    if any(a > 0 and b == 0 for originals,scaled in ((q.reference_flow_m3_s,flow),(q.reference_head_m,head)) for a,b in zip(originals,scaled)):
        raise ValueError("nonzero curve ordinate underflowed during scaling")
    if any(b <= a for a,b in zip(flow,flow[1:])):
        raise ValueError("scaled flow coordinates lost their distinct ordering")
    return Calculation(values={"flow_m3_s":flow,"head_m":head,
        "flow_coordinate_ratio":scale,"head_coordinate_ratio":head_scale},notes=(
        "Approximate same-casing impeller/speed transformation at corresponding curve points.",
        "Supplied reference data and applicability of trimming/viscosity assumptions require review.",
        "No power/efficiency, NPSH, operating point, interpolation, extrapolation or vendor validation is inferred.",
    ))
