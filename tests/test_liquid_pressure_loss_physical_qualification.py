"""Catalog-path laminar signed pressure and dissipated-work checks."""

import math
import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="liquid-loss-reference", scenario_id="laminar-pipe", clock_id="steady", origin="synthetic")
CASES = (
    (998.2, 1.002e-3, 1e-5, 0.050, 12.0, 0.0, 3.0, 9.80665),
    (850.0, 2.10e-3, 2.5e-5, 0.060, 40.0, 1.7, -5.0, 9.81),
    (1025.0, 1.08e-3, -1.4e-5, 0.045, 8.0, 0.8, 1.25, 9.80665),
    (920.0, 3e-3, -3e-5, 0.075, 25.0, 2.4, -2.0, 1.62),
)


@pytest.mark.parametrize("rho,mu,q,d,l,k,dz,g", CASES)
def test_catalog_laminar_pressure_work_matches_stokes_darcy_balance(rho, mu, q, d, l, k, dz, g):
    a = math.pi * d**2 / 4
    v = q / a
    re = rho * abs(v) * d / mu
    assert re < 2000
    viscous = 32 * mu * l * abs(v) / d**2
    minor = k * rho * v**2 / 2
    signed_loss = math.copysign(viscous + minor, q)
    static = rho * g * dz
    result = evaluate(
        "hydraulics.liquid_loss",
        {
            "density_kg_m3": rho,
            "viscosity_pa_s": mu,
            "volume_flow_m3_s": q,
            "diameter_m": d,
            "length_m": l,
            "roughness_m": 0.0,
            "minor_loss_coefficient": k,
            "outlet_minus_inlet_elevation_m": dz,
            "gravity_m_s2": g,
        },
        CTX,
    )
    assert result.status == "computed"
    assert result.values["reynolds"] == pytest.approx(re, rel=2e-14)
    assert result.values["darcy_friction_factor"] == pytest.approx(64 / re, rel=2e-12)
    assert result.values["dissipative_pressure_loss_pa"] == pytest.approx(signed_loss, rel=2e-12)
    assert result.values["static_pressure_change_pa"] == pytest.approx(static, rel=2e-14)
    assert result.values["required_inlet_minus_outlet_pa"] == pytest.approx(signed_loss + static, rel=2e-12, abs=2e-12)
    assert result.values["dissipated_power_w"] == pytest.approx((viscous + minor) * abs(q), rel=2e-12)
