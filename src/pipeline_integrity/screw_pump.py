"""Positive-displacement screw delivery with reviewed leakage and drag closures.

Coefficients describe the selected pump and liquid regime. No equipment table,
universal clearance coefficient or manufacturer performance claim is supplied.
"""
from __future__ import annotations

import math

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative, Positive


class ScrewPumpRequest(Contract):
    displacement_m3_rev: Positive
    speed_rev_s: Nonnegative
    pressure_rise_pa: Nonnegative
    dynamic_viscosity_pa_s: Positive
    leakage_conductance_m3: Nonnegative
    viscous_torque_geometry_m3: Nonnegative
    calibration_id: Name
    operating_regime_id: Name
    newtonian_linear_leakage_and_viscous_drag_reviewed: bool = False
    effective_shear_radius_m: Positive | None = None
    effective_shear_gap_m: Positive | None = None

    @model_validator(mode='after')
    def paired_shear_geometry(self):
        if (self.effective_shear_radius_m is None) != (self.effective_shear_gap_m is None):
            raise ValueError('shear radius and gap must be supplied together')
        return self


class ScrewPumpResult(Contract):
    theoretical_delivery_m3_s: Nonnegative
    pressure_leakage_m3_s: Nonnegative
    net_delivery_m3_s: float
    volumetric_efficiency: float | None
    pumping_delivery_positive: bool
    pressure_torque_N_m: Nonnegative
    viscous_torque_N_m: Nonnegative
    required_shaft_torque_N_m: Nonnegative
    shaft_power_W: Nonnegative
    delivered_pressure_power_W: float
    leakage_dissipation_W: Nonnegative
    viscous_dissipation_W: Nonnegative
    power_balance_residual_W: float
    effective_shear_rate_s_inv: Nonnegative | None
    calibration_id: Name
    operating_regime_id: Name
    calibration_or_vendor_acceptance_qualified: bool = False


def evaluate_screw_pump(request: ScrewPumpRequest) -> ScrewPumpResult:
    """Evaluate a declared linear leakage/drag operating point.

    Leakage conductance has dimensions m3: a reviewed laminar rectangular gap
    contributes width*gap**3/(12*length). Viscous torque geometry is the area
    integral of radius**2/gap. Caller coefficients may instead be fitted on the
    same dimensions and must preserve the selected regime and calibration ID.
    Negative net delivery is retained as reverse leakage, never clipped.
    """
    q = ScrewPumpRequest.model_validate(request.model_dump())
    if not q.newtonian_linear_leakage_and_viscous_drag_reviewed:
        raise ValueError('reviewed Newtonian linear leakage and drag closure required')
    omega = 2 * math.pi * q.speed_rev_s
    ideal = q.displacement_m3_rev * q.speed_rev_s
    leak = q.leakage_conductance_m3 * q.pressure_rise_pa / q.dynamic_viscosity_pa_s
    delivery = ideal - leak
    pressure_torque = q.pressure_rise_pa * q.displacement_m3_rev / (2 * math.pi)
    viscous_torque = q.dynamic_viscosity_pa_s * omega * q.viscous_torque_geometry_m3
    shaft = omega * (pressure_torque + viscous_torque)
    output = q.pressure_rise_pa * delivery
    leak_loss = q.pressure_rise_pa * leak
    drag_loss = omega * viscous_torque
    values = (omega, ideal, leak, delivery, pressure_torque, viscous_torque,
              shaft, output, leak_loss, drag_loss)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('operating point exceeds finite numerical support')
    shear = (None if q.effective_shear_radius_m is None else
             omega * q.effective_shear_radius_m / q.effective_shear_gap_m)
    return ScrewPumpResult(
        theoretical_delivery_m3_s=ideal, pressure_leakage_m3_s=leak,
        net_delivery_m3_s=delivery,
        volumetric_efficiency=None if ideal == 0 else delivery / ideal,
        pumping_delivery_positive=delivery > 0,
        pressure_torque_N_m=pressure_torque, viscous_torque_N_m=viscous_torque,
        required_shaft_torque_N_m=pressure_torque + viscous_torque,
        shaft_power_W=shaft, delivered_pressure_power_W=output,
        leakage_dissipation_W=leak_loss, viscous_dissipation_W=drag_loss,
        power_balance_residual_W=math.fsum((shaft, -output, -leak_loss, -drag_loss)),
        effective_shear_rate_s_inv=shear, calibration_id=q.calibration_id,
        operating_regime_id=q.operating_regime_id)
