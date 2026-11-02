import math

import pytest

from pipeline_integrity.screw_pump import ScrewPumpRequest, evaluate_screw_pump


def request(**updates):
    values = dict(displacement_m3_rev=.002, speed_rev_s=5., pressure_rise_pa=200000.,
        dynamic_viscosity_pa_s=.1, leakage_conductance_m3=1e-10,
        viscous_torque_geometry_m3=.02, calibration_id='reviewed-clearance',
        operating_regime_id='newtonian-steady',
        newtonian_linear_leakage_and_viscous_drag_reviewed=True)
    values.update(updates)
    return ScrewPumpRequest(**values)


def test_supplied_rectangular_clearance_matches_poiseuille_and_power():
    width, gap, length = .1, .0001, .02
    q = request(leakage_conductance_m3=width*gap**3/(12*length))
    result = evaluate_screw_pump(q)
    expected = width*gap**3*q.pressure_rise_pa/(12*q.dynamic_viscosity_pa_s*length)
    assert result.pressure_leakage_m3_s == pytest.approx(expected)
    assert result.shaft_power_W == pytest.approx(result.delivered_pressure_power_W
        + result.leakage_dissipation_W + result.viscous_dissipation_W)


def test_stopped_pump_preserves_reverse_leak_and_undefined_efficiency():
    result = evaluate_screw_pump(request(speed_rev_s=0.))
    assert result.net_delivery_m3_s < 0
    assert result.volumetric_efficiency is None
    assert result.shaft_power_W == 0
    assert result.delivered_pressure_power_W == -result.leakage_dissipation_W


def test_viscous_drag_doubles_with_viscosity_and_leakage_halves():
    first = evaluate_screw_pump(request())
    second = evaluate_screw_pump(request(dynamic_viscosity_pa_s=.2))
    assert second.viscous_torque_N_m == pytest.approx(2*first.viscous_torque_N_m)
    assert second.pressure_leakage_m3_s == pytest.approx(.5*first.pressure_leakage_m3_s)
    assert math.isfinite(second.power_balance_residual_W)


def test_unreviewed_closure_rejected():
    with pytest.raises(ValueError, match='reviewed'):
        evaluate_screw_pump(request(newtonian_linear_leakage_and_viscous_drag_reviewed=False))
