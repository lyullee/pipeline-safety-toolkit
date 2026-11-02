"""Pressure/volume-rate adapter to the existing two-efficiency pump energy ledger."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Nonnegative,Positive
from .models.equipment import PumpPower,pump_power


class PumpPressureDutyRequest(Contract):
    volume_flow_m3_s: Nonnegative
    differential_pressure_pa: Nonnegative
    pump_efficiency: Positive = Field(le=1)
    upstream_drive_efficiency: Positive = Field(le=1)
    upstream_drive_boundary: Literal["electrical_input","mechanical_transmission_input"]
    incompressible_liquid_duty_and_efficiency_basis_reviewed: bool = False


class PumpPressureDutyResult(Contract):
    status: Literal["computed","needs_data"]
    upstream_drive_boundary: Literal["electrical_input","mechanical_transmission_input"]
    hydraulic_power_w: Nonnegative | None = None
    pump_shaft_power_w: Nonnegative | None = None
    upstream_drive_input_power_w: Nonnegative | None = None
    pump_loss_w: Nonnegative | None = None
    upstream_drive_loss_w: Nonnegative | None = None
    issues: tuple[str,...] = ()


def evaluate_pump_pressure_duty(request: PumpPressureDutyRequest) -> PumpPressureDutyResult:
    """Reuse the head-based energy ledger for supplied liquid differential pressure.

    A fiducial water column is an internal pressure-unit coordinate only; it is
    neither the working-fluid density nor a predicted actual pump head. The
    existing second efficiency stage is used numerically, while this adapter
    explicitly labels its actual motor/mechanical-transmission power boundary.
    No efficiencies, pressure rise or operating point are inferred.
    """
    if not isinstance(request,PumpPressureDutyRequest):raise TypeError("PumpPressureDutyRequest required")
    q=PumpPressureDutyRequest.model_validate(request.model_dump(mode="python"))
    if not q.incompressible_liquid_duty_and_efficiency_basis_reviewed:
        return PumpPressureDutyResult(status="needs_data",upstream_drive_boundary=q.upstream_drive_boundary,
            issues=("incompressible differential-pressure duty and efficiency boundary require review",))
    rho_reference,g_reference=1000.,9.80665
    pressure_coordinate=q.differential_pressure_pa/(rho_reference*g_reference)
    if q.differential_pressure_pa>0 and pressure_coordinate==0:
        raise ValueError("nonzero pressure duty is below finite numerical support")
    values=pump_power(PumpPower(volume_flow_m3_s=q.volume_flow_m3_s,density_kg_m3=rho_reference,
        head_m=pressure_coordinate,pump_efficiency=q.pump_efficiency,motor_efficiency=q.upstream_drive_efficiency,
        gravity_m_s2=g_reference)).values
    hydraulic,shaft,upstream=(values[k] for k in ("hydraulic_power_w","shaft_power_w","electrical_power_w"))
    if not all(math.isfinite(v) for v in (hydraulic,shaft,upstream)):
        raise ValueError("power duty exceeds finite numerical support")
    if q.volume_flow_m3_s>0 and q.differential_pressure_pa>0 and hydraulic==0:
        raise ValueError("nonzero hydraulic duty is below finite numerical support")
    return PumpPressureDutyResult(status="computed",upstream_drive_boundary=q.upstream_drive_boundary,
        hydraulic_power_w=hydraulic,pump_shaft_power_w=shaft,upstream_drive_input_power_w=upstream,
        pump_loss_w=shaft-hydraulic,upstream_drive_loss_w=upstream-shaft)
