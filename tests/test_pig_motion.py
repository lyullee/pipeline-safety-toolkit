"""Meaningful analytic/transition tests authored for later NURION execution."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import pig_motion as pm

_spec = importlib.util.spec_from_file_location(
    "pig_example", Path(__file__).parents[1] / "examples" / "pig_motion.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def window(id, duration, pressure=110000.0, external=0.0):
    return pm.PigBoundaryWindow(
        id=id,
        duration_s=duration,
        upstream_pressure_pa_abs=pressure,
        downstream_pressure_pa_abs=100000.0,
        external_axial_force_N=external,
        provider_id="analytic_constant",
        provider_version="1",
        evidence_id="authored_pig_boundary",
    )


def result(**changes):
    q, c = _example.make_case(reviewed=True, **changes)
    r = pm.evaluate_pig_motion(q, c)
    assert r["status"] == "computed_diagnostic", r["notes"]
    return q, c, r


def test_unreviewed_has_no_solver_dependency(monkeypatch):
    q, c = _example.make_case()
    monkeypatch.setattr(pm, "runtime_metadata", lambda: pytest.fail("unreviewed solver access"))
    r = pm.evaluate_pig_motion(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]
    assert pm.verify_pig_motion_record(r, request=q, context=c)["valid"]


def test_context_and_clone_finiteness():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        pm.evaluate_pig_motion(q, c.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError):
        pm.evaluate_pig_motion(q.model_copy(update={"initial_velocity_m_s": float("nan")}), c)


@pytest.mark.parametrize(
    "changes",
    [
        dict(static_friction_N=1.0, kinetic_friction_N=2.0),
        dict(initial_position_m=101.0),
        dict(upstream_port_id="ahead_pig"),
        dict(initial_position_m=0.0, initial_velocity_m_s=-1.0),
    ],
)
def test_invalid_mechanical_domain(changes):
    with pytest.raises(ValidationError):
        _example.make_case(**changes)


def test_constant_acceleration_and_work_energy_analytic():
    q, c, r = result()
    acceleration = (100.0 - 2.0) / 10.0
    assert r["values"]["final_position_m"] == pytest.approx(1 + acceleration / 2, abs=1e-7)
    assert r["values"]["final_velocity_m_s"] == pytest.approx(acceleration, abs=1e-7)
    assert all(row["within_budget"] for row in r["history"])
    assert pm.verify_pig_motion_record(r, request=q, context=c)["valid"]
    assert pm.to_signal_frames(r, c)[-1].values["velocity_m_s"] == r["values"]["final_velocity_m_s"]


def test_static_threshold_then_breakaway():
    _, _, r = result(windows=(window("stick", 1.0, 100500.0), window("release", 1.0)))
    stuck = [row for row in r["history"] if row["window_id"] == "stick"]
    assert all(row["position_m"] == 1.0 and row["velocity_m_s"] == 0.0 for row in stuck)
    assert r["values"]["final_velocity_m_s"] == pytest.approx(9.8, abs=1e-7)
    assert {t["event"] for t in r["transitions"]} >= {"stuck", "breakaway"}


def test_stop_then_reverse_with_independent_piecewise_solution():
    _, _, r = result(
        initial_velocity_m_s=2.0, initial_position_m=10.0, windows=(window("reverse", 1.0, 90000.0),)
    )
    stop_time = 2.0 / 10.2
    reverse_time = 1.0 - stop_time
    expected_x = 10.0 + 2 * stop_time - 10.2 * stop_time**2 / 2 - 9.8 * reverse_time**2 / 2
    assert r["values"]["final_velocity_m_s"] == pytest.approx(-9.8 * reverse_time, abs=1e-7)
    assert r["values"]["final_position_m"] == pytest.approx(expected_x, abs=1e-7)
    assert any(t["event"] == "velocity_zero" for t in r["transitions"])


def test_arrival_is_terminal_without_impact_model():
    _, _, r = result(length_m=2.0)
    time = (2 / 9.8) ** 0.5
    assert r["values"]["termination"] == "downstream_boundary"
    assert r["values"]["actual_duration_s"] == pytest.approx(time, abs=1e-7)
    assert r["values"]["endpoint_impact_energy_J"] == pytest.approx(98.0, abs=1e-6)


def test_receiver_arrival_uses_absolute_clock_and_speed_limits():
    q, c, r = result(length_m=2.0, start_time_s=20.0)
    actual = 20 + (2 / 9.8) ** 0.5
    limits = dict(
        request=q,
        context=c,
        earliest_time_s=actual - 0.01,
        latest_time_s=actual + 0.01,
        maximum_arrival_speed_m_s=5.0,
        receiver_evidence_id="authored_pig_boundary",
    )
    accepted = pm.assess_receiver_arrival(r, **limits)
    assert accepted["status"] == "within_declared_limits"
    assert accepted["arrival_time_s"] == pytest.approx(actual)
    assert not accepted["receiver_capture_or_impact_qualified"]
    assert pm.verify_receiver_arrival_assessment(accepted, r, request=q, context=c)["valid"]
    rejected = pm.assess_receiver_arrival(r, **{**limits, "maximum_arrival_speed_m_s": 1.0})
    assert rejected["status"] == "outside_declared_limits"
    changed = {**accepted, "status": "outside_declared_limits"}
    assert not pm.verify_receiver_arrival_assessment(changed, r, request=q, context=c)["valid"]


def test_receiver_nonarrival_and_unreviewed_are_distinct():
    q, c, r = result()
    limits = dict(
        request=q,
        context=c,
        earliest_time_s=0.0,
        latest_time_s=10.0,
        maximum_arrival_speed_m_s=10.0,
        receiver_evidence_id="authored_pig_boundary",
    )
    assert pm.assess_receiver_arrival(r, **limits)["status"] == "not_arrived_within_simulated_horizon"
    assert pm.assess_receiver_arrival(r, **limits)["arrival_time_s"] is None
    with pytest.raises(ValueError):
        pm.assess_receiver_arrival(r, **{**limits, "receiver_evidence_id": "absent"})
    with pytest.raises(ValueError):
        pm.assess_receiver_arrival(r, **{**limits, "earliest_time_s": True})


def test_gravity_external_force_and_drag_terminal_solution():
    # With pressure/external drive 100 N, k=1, m=10, v0=0:
    # v=sqrt(F/k)*tanh(sqrt(F*k)*t/m).
    import math

    _, _, r = result(kinetic_friction_N=0.0, static_friction_N=0.0, quadratic_drag_N_s2_m2=1.0)
    assert r["values"]["final_velocity_m_s"] == pytest.approx(10 * math.tanh(1), abs=1e-6)
    assert r["history"][-1]["friction_work_J"] < 0


def test_tamper_and_source_mismatch():
    q, c = _example.make_case()
    r = pm.evaluate_pig_motion(q, c)
    r["physical_validation_claim"] = True
    assert not pm.verify_pig_motion_record(r)["valid"]
    q, c = _example.make_case(reviewed=True, runtime_pins={"invalid": True})
    with pytest.raises(ValueError):
        pm.evaluate_pig_motion(q, c)


def test_work_budget_fail_is_explicit_not_partial_success():
    q, c = _example.make_case(reviewed=True, maximum_rhs_calls=1)
    r = pm.evaluate_pig_motion(q, c)
    assert r["status"] == "out_of_scope"
    assert r["history"] is None and r["values"] is None


def test_transition_budget_includes_static_window_transitions():
    q, c = _example.make_case(
        reviewed=True, maximum_events=1, windows=(window("hold1", 1, 100000), window("hold2", 1, 100000))
    )
    r = pm.evaluate_pig_motion(q, c)
    assert r["status"] == "out_of_scope"
    assert "transition budget" in r["notes"][0]


def test_terminal_velocity_reset_is_in_physical_energy_and_impulse_budget(monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    # Constant -9.99 N over one second is consistent with v: 1 -> .001 m/s.
    # A deliberately coarse root tolerance accepts that residual velocity;
    # resetting it to zero must still expose the energy/impulse discrepancy.
    def coarse_root(*args, **kwargs):
        return SimpleNamespace(
            success=True,
            message="authored root residual",
            t=np.array([0.0, 1.0]),
            y=np.array(
                [
                    [1.0, 1.5005],
                    [1.0, 0.001],
                    [0.0, -4.999995],
                    [0.0, 0.0],
                    [0.0, 0.0],
                    [0.0, 0.0],
                    [0.0, -9.99],
                ]
            ),
            t_events=(np.array([1.0]), np.array([]), np.array([])),
        )

    monkeypatch.setattr("scipy.integrate.solve_ivp", coarse_root)
    q, c = _example.make_case(
        reviewed=True,
        initial_velocity_m_s=1,
        kinetic_friction_N=0,
        static_friction_N=0,
        velocity_atol_m_s=0.002,
        energy_budget_absolute_J=1e-8,
        momentum_budget_absolute_N_s=1e-8,
        balance_relative_tolerance=0,
        windows=(window("coarse_stop", 1, 99001),),
    )
    r = pm.evaluate_pig_motion(q, c)
    assert r["status"] == "diagnostic_rejected"
    assert r["history"][-1]["velocity_m_s"] == r["values"]["final_velocity_m_s"] == 0
    assert not r["history"][-1]["within_budget"]
    assert r["transitions"][-1]["numerical_reset_momentum_change_N_s"] == pytest.approx(-0.01)


def test_transition_count_matches_retained_transition_records():
    _, _, r = result(windows=(window("hold", 1, 100000), window("release", 1)))
    assert r["values"]["transition_count"] == len(r["transitions"])
