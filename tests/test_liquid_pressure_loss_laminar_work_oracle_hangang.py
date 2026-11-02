"""Independent laminar pressure-work reference for the liquid pipe kernel."""

from __future__ import annotations

import math

import pytest

from pipeline_integrity.models.hydraulics import LiquidPressureLoss, liquid_pressure_loss


CASES = (
    # density, viscosity, flow, diameter, length, minor K, elevation rise, gravity
    (998.2, 1.002e-3, 1.0e-5, 0.050, 12.0, 0.0, 3.0, 9.80665),
    (850.0, 2.10e-3, 2.5e-5, 0.060, 40.0, 1.7, -5.0, 9.81),
    (1025.0, 1.08e-3, -1.4e-5, 0.045, 8.0, 0.8, 1.25, 9.80665),
    (920.0, 3.00e-3, -3.0e-5, 0.075, 25.0, 2.4, -2.0, 1.62),
)


@pytest.mark.parametrize(
    "density,viscosity,flow,diameter,length,minor_k,elevation,g", CASES
)
def test_laminar_pressure_and_work_match_closed_form_stokes_darcy_balance(
    density, viscosity, flow, diameter, length, minor_k, elevation, g
):
    """Use Δp=32μL|v|/D²+Kρv²/2 and Δpelev=ρgΔz independently."""
    area = math.pi * diameter**2 / 4
    velocity = flow / area
    reynolds = density * abs(velocity) * diameter / viscosity
    assert reynolds < 2_000

    viscous_drop = 32 * viscosity * length * abs(velocity) / diameter**2
    minor_drop = minor_k * density * velocity**2 / 2
    dissipative_signed = math.copysign(viscous_drop + minor_drop, flow)
    static_change = density * g * elevation
    expected_required = dissipative_signed + static_change
    expected_power = (viscous_drop + minor_drop) * abs(flow)

    result = liquid_pressure_loss(
        LiquidPressureLoss(
            density_kg_m3=density,
            viscosity_pa_s=viscosity,
            volume_flow_m3_s=flow,
            diameter_m=diameter,
            length_m=length,
            roughness_m=0.0,
            minor_loss_coefficient=minor_k,
            outlet_minus_inlet_elevation_m=elevation,
            gravity_m_s2=g,
        )
    )

    assert result.status == "computed"
    assert result.values["reynolds"] == pytest.approx(reynolds, rel=2e-14)
    assert result.values["velocity_m_s"] == pytest.approx(velocity, rel=2e-14)
    assert result.values["darcy_friction_factor"] == pytest.approx(64 / reynolds, rel=2e-12)
    assert result.values["dissipative_pressure_loss_pa"] == pytest.approx(
        dissipative_signed, rel=2e-12
    )
    assert result.values["static_pressure_change_pa"] == pytest.approx(static_change, rel=2e-14)
    assert result.values["required_inlet_minus_outlet_pa"] == pytest.approx(
        expected_required, rel=2e-12, abs=2e-12
    )
    # Reversible elevation head must not enter irreversible hydraulic power.
    assert result.values["dissipated_power_w"] == pytest.approx(expected_power, rel=2e-12)

