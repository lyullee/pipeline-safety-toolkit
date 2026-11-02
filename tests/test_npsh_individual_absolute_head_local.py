"""Independent absolute-head budgets for a stationary suction boundary."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CTX = RunContext(asset_id="suction-reference", scenario_id="individual-head-budget", clock_id="steady", origin="synthetic")


def run(*, surface_head=12.0, vapour_head=1.0, elevation=2.0, loss=3.0,
        required=4.0, density=800.0, gravity=9.81):
    return evaluate("equipment.pump_npsh", {
        "surface_pressure_pa_abs": surface_head * density * gravity,
        "vapour_pressure_pa_abs": vapour_head * density * gravity,
        "density_kg_m3": density,
        "surface_above_pump_m": elevation,
        "suction_head_loss_m": loss,
        "vendor_required_npsh_m": required,
        "gravity_m_s2": gravity,
    }, CTX).values


@pytest.mark.parametrize("heads,expected", [
    ((12.0, 1.0, 2.0, 3.0, 4.0), (10.0, 6.0)),
    ((4.0, 1.0, -2.0, 1.5, 1.0), (-0.5, -1.5)),
    ((6.0, 2.0, -1.0, 3.0, 0.0), (0.0, 0.0)),
])
def test_constructed_absolute_head_energy_budgets(heads, expected):
    result = run(surface_head=heads[0], vapour_head=heads[1], elevation=heads[2],
                 loss=heads[3], required=heads[4])
    assert result["available_npsh_m"] == pytest.approx(expected[0], abs=2e-14)
    assert result["declared_margin_m"] == pytest.approx(expected[1], abs=2e-14)


def test_same_head_budget_is_invariant_to_density_and_gravity_basis():
    # Pressures, rather than heads, change with the declared liquid/gravity basis.
    for density, gravity in ((450.0, 3.71), (1200.0, 9.80665), (2200.0, 15.0)):
        assert run(density=density, gravity=gravity) == pytest.approx(run(), abs=2e-14)


def test_customary_pressure_head_conversion_with_explicit_rounding_interval():
    # Unit definitions are exact; the familiar two-decimal water-head factor is rounded.
    psi_pa, foot_m, rho, gravity = 6894.757293168, 0.3048, 998.2, 9.80665
    factor = psi_pa / (rho * gravity * foot_m)
    assert 2.305 <= factor < 2.315
    ps_psi, pv_psi, elevation_ft, loss_ft = 22.0, 0.6, -7.0, 1.5
    result = evaluate("equipment.pump_npsh", {
        "surface_pressure_pa_abs": ps_psi * psi_pa,
        "vapour_pressure_pa_abs": pv_psi * psi_pa,
        "density_kg_m3": rho, "gravity_m_s2": gravity,
        "surface_above_pump_m": elevation_ft * foot_m,
        "suction_head_loss_m": loss_ft * foot_m,
        "vendor_required_npsh_m": 0.0,
    }, CTX).values["available_npsh_m"] / foot_m
    lower = (ps_psi - pv_psi) * 2.305 + elevation_ft - loss_ft
    upper = (ps_psi - pv_psi) * 2.315 + elevation_ft - loss_ft
    assert lower <= result < upper


def test_declared_vendor_requirement_changes_margin_only():
    first, second = run(required=2.0), run(required=7.0)
    assert second["available_npsh_m"] == first["available_npsh_m"]
    assert second["declared_margin_m"] - first["declared_margin_m"] == pytest.approx(-5.0)
