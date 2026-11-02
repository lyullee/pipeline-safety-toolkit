"""Hangang physical oracle for pump suction NPSH head and declared margin."""
import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="pump_npsh_oracle", scenario_id="suction-boundary", clock_id="steady", origin="synthetic")
G = 9.80665


def _evaluate(**changes):
    p = {
        "surface_pressure_pa_abs": 180000.0,
        "vapour_pressure_pa_abs": 3200.0,
        "density_kg_m3": 998.2,
        "surface_above_pump_m": 4.7,
        "suction_head_loss_m": 1.35,
        "vendor_required_npsh_m": 3.2,
        "gravity_m_s2": G,
    }
    p.update(changes)
    return create_registry().evaluate("equipment.pump_npsh", p, CTX).values


def test_available_npsh_and_margin_match_independent_pressure_head_balance():
    cases = [
        (180000.0, 3200.0, 998.2, 4.7, 1.35, 3.2),
        (101325.0, 2339.0, 997.0, 0.8, 0.25, 2.1),
        (450000.0, 95000.0, 870.0, -2.0, 7.5, 5.0),
    ]
    for ps, pv, rho, elevation, loss, required in cases:
        out = _evaluate(
            surface_pressure_pa_abs=ps,
            vapour_pressure_pa_abs=pv,
            density_kg_m3=rho,
            surface_above_pump_m=elevation,
            suction_head_loss_m=loss,
            vendor_required_npsh_m=required,
        )
        expected = (ps - pv) / (rho * G) + elevation - loss
        assert out["available_npsh_m"] == pytest.approx(expected, rel=2e-14, abs=2e-14)
        assert out["declared_margin_m"] == pytest.approx(expected - required, rel=2e-14, abs=2e-14)


def test_npsh_head_response_has_expected_linear_boundary_sensitivities():
    base = _evaluate()
    dh = 0.37
    dp = 998.2 * G * dh
    higher_surface_pressure = _evaluate(surface_pressure_pa_abs=180000.0 + dp)
    higher_surface_elevation = _evaluate(surface_above_pump_m=4.7 + dh)
    higher_suction_loss = _evaluate(suction_head_loss_m=1.35 + dh)
    assert higher_surface_pressure["available_npsh_m"] - base["available_npsh_m"] == pytest.approx(dh, abs=2e-13)
    assert higher_surface_elevation["available_npsh_m"] - base["available_npsh_m"] == pytest.approx(dh, abs=2e-14)
    assert higher_suction_loss["available_npsh_m"] - base["available_npsh_m"] == pytest.approx(-dh, abs=2e-14)
    assert higher_surface_pressure["declared_margin_m"] - base["declared_margin_m"] == pytest.approx(dh, abs=2e-13)
