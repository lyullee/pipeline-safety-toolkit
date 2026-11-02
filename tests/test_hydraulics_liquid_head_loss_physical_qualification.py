"""Bounded Darcy-Weisbach head-loss qualification through the catalog API."""

import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


G = 9.80665
CASES = (
    (5.0e-6, 0.04, 8.0, 1.0e-6, 1.0e-5, 0.0, 0.0),
    (1.0e-3, 0.10, 120.0, 1.0e-6, 2.0e-5, 0.0, 0.0),
    (2.0e-2, 0.05, 45.0, 1.1e-6, 8.0e-5, 1.2, 2.5),
    (-8.0e-4, 0.08, 30.0, 1.0e-6, 1.0e-4, 0.5, -1.1),
    (5.0e-4, 0.06, 70.0, 1.2e-6, 3.0e-5, 0.2, 0.0),
)


def _context():
    return RunContext(asset_id="straight-pipe-reference", scenario_id="single-phase-darcy-head", clock_id="steady-state", origin="synthetic")


def _colebrook(reynolds: float, relative_roughness: float) -> float:
    def residual(factor):
        return 1.0 / math.sqrt(factor) + 2.0 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(factor))
        )

    low, high = 0.005, 0.15
    assert residual(low) > 0.0 > residual(high)
    for _ in range(100):
        mid = (low + high) / 2.0
        if residual(mid) > 0.0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


@pytest.mark.parametrize("flow,d,length,nu,roughness,k,dz", CASES)
def test_catalog_head_loss_matches_independent_darcy_and_colebrook(flow,d,length,nu,roughness,k,dz):
    result = evaluate(
        "hydraulics.liquid_head_loss",
        {
            "diameter_m": d,
            "length_m": length,
            "roughness_m": roughness,
            "volume_flow_m3_s": flow,
            "kinematic_viscosity_m2_s": nu,
            "minor_loss_coefficient": k,
            "outlet_minus_inlet_elevation_m": dz,
            "gravity_m_s2": G,
            "friction_method": "Colebrook",
        },
        _context(),
    )
    area = math.pi * d**2 / 4.0
    velocity = flow / area
    reynolds = abs(velocity) * d / nu
    assert reynolds < 2300.0 or reynolds >= 4000.0
    factor = 64.0 / reynolds if reynolds < 2300.0 else _colebrook(reynolds, roughness / d)
    dissipative_head = (factor * length / d + k) * velocity**2 / (2.0 * G)
    expected = math.copysign(dissipative_head, velocity) + dz
    assert result.status == "computed"
    assert result.model_id == "hydraulics.liquid_head_loss"
    assert result.values["reynolds"] == pytest.approx(reynolds, rel=2e-15)
    assert result.values["darcy_friction_factor"] == pytest.approx(factor, rel=2e-10, abs=1e-14)
    assert result.values["required_inlet_minus_outlet_head_m"] == pytest.approx(
        expected, rel=2e-10, abs=1e-12
    )


def test_catalog_head_loss_withholds_unreviewed_transition_regime():
    diameter = 0.05
    nu = 1.0e-6
    target_reynolds = 3000.0
    flow = target_reynolds * nu * math.pi * diameter / 4.0
    result = evaluate(
        "hydraulics.liquid_head_loss",
        {
            "diameter_m": diameter,
            "length_m": 10.0,
            "roughness_m": 1.0e-5,
            "volume_flow_m3_s": flow,
            "kinematic_viscosity_m2_s": nu,
            "gravity_m_s2": G,
            "friction_method": "Colebrook",
        },
        _context(),
    )
    assert result.status == "out_of_scope"
    assert result.values == {}


def _catalog_head(flow, diameter, length, viscosity, roughness, minor=0.0):
    return evaluate(
        "hydraulics.liquid_head_loss",
        {
            "diameter_m": diameter,
            "length_m": length,
            "roughness_m": roughness,
            "volume_flow_m3_s": flow,
            "kinematic_viscosity_m2_s": viscosity,
            "minor_loss_coefficient": minor,
            "outlet_minus_inlet_elevation_m": 0.0,
            "gravity_m_s2": G,
            "friction_method": "Colebrook",
        },
        _context(),
    )


def test_laminar_darcy_head_is_linear_in_flow_and_minor_loss_is_quadratic():
    diameter, length, viscosity, roughness = 0.05, 10.0, 1.0e-6, 1.0e-5
    flow = 1.0e-5
    base = _catalog_head(flow, diameter, length, viscosity, roughness, minor=0.0)
    half = _catalog_head(flow / 2.0, diameter, length, viscosity, roughness, minor=0.0)
    base_with_minor = _catalog_head(flow, diameter, length, viscosity, roughness, minor=1.5)
    half_with_minor = _catalog_head(flow / 2.0, diameter, length, viscosity, roughness, minor=1.5)
    assert base.status == half.status == base_with_minor.status == half_with_minor.status == "computed"
    assert base.values["reynolds"] < 2300.0 and half.values["reynolds"] < 2300.0
    assert half.values["dissipative_head_loss_m"] == pytest.approx(
        0.5 * base.values["dissipative_head_loss_m"], rel=2e-14
    )
    minor_base = base_with_minor.values["dissipative_head_loss_m"] - base.values["dissipative_head_loss_m"]
    minor_half = half_with_minor.values["dissipative_head_loss_m"] - half.values["dissipative_head_loss_m"]
    assert minor_half == pytest.approx(0.25 * minor_base, rel=2e-14)


def test_coherent_geometric_and_viscosity_scaling_preserves_head_loss():
    base = _catalog_head(1.0e-3, 0.08, 20.0, 1.0e-6, 2.0e-5, minor=0.6)
    scaled = _catalog_head(9.0e-3, 0.24, 60.0, 3.0e-6, 6.0e-5, minor=0.6)
    assert base.status == scaled.status == "computed"
    assert scaled.values["velocity_m_s"] == pytest.approx(base.values["velocity_m_s"], rel=2e-15)
    assert scaled.values["reynolds"] == pytest.approx(base.values["reynolds"], rel=2e-15)
    assert scaled.values["darcy_friction_factor"] == pytest.approx(
        base.values["darcy_friction_factor"], rel=2e-14
    )
    assert scaled.values["dissipative_head_loss_m"] == pytest.approx(
        base.values["dissipative_head_loss_m"], rel=2e-14
    )
