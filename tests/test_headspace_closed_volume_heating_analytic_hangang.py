"""Exact first-law and ideal-gas checks for closed, fixed-volume headspace heating."""

import pytest

from pipeline_integrity.gas_headspace import HeadspaceRequest, HeadspaceWindow, evaluate_headspace


HEAT_WINDOWS = (
    ((500.0, 10.0),),
    ((-250.0, 10.0),),
    ((300.0, 20.0), (-100.0, 15.0)),
    ((0.0, 5.0),),
)


@pytest.mark.parametrize("heat_windows", HEAT_WINDOWS)
def test_fixed_volume_headspace_matches_exact_temperature_pressure_and_energy_budget(heat_windows):
    mass_kg = 2.0
    cv_J_kg_K = 718.0
    gas_constant_J_kg_K = 287.0
    volume_m3 = 0.5
    initial_temperature_K = 300.0
    windows = tuple(
        HeadspaceWindow(
            id=f"heat-{index}",
            duration_s=duration_s,
            inlet_temperature_K=initial_temperature_K,
            ambient_temperature_K=initial_temperature_K,
            heat_input_W=heat_W,
        )
        for index, (heat_W, duration_s) in enumerate(heat_windows)
    )
    request = HeadspaceRequest(
        id="closed-volume-heat-reference",
        asset_id="synthetic-headspace",
        clock_id="elapsed-seconds",
        volume_m3=volume_m3,
        gas_constant_J_kg_K=gas_constant_J_kg_K,
        cv_J_kg_K=cv_J_kg_K,
        initial_mass_kg=mass_kg,
        initial_temperature_K=initial_temperature_K,
        maximum_step_s=1.0,
        windows=windows,
        evidence_ids=("authored-reference",),
    )
    record = evaluate_headspace(request)

    assert record["status"] == "computed"
    cumulative_heat_J = 0.0
    previous_time_s = request.start_time_s
    for row in record["states"]:
        if row["time_s"] > previous_time_s:
            active = next(w for w in windows if w.id == row["window_id"])
            cumulative_heat_J += active.heat_input_W * (row["time_s"] - previous_time_s)
        expected_temperature_K = initial_temperature_K + cumulative_heat_J / (mass_kg * cv_J_kg_K)
        expected_pressure_Pa = mass_kg * gas_constant_J_kg_K * expected_temperature_K / volume_m3
        assert row["mass_kg"] == pytest.approx(mass_kg, abs=1e-13)
        assert row["volume_m3"] == pytest.approx(volume_m3, abs=1e-13)
        assert row["temperature_K"] == pytest.approx(expected_temperature_K, abs=2e-10)
        assert row["pressure_pa_abs"] == pytest.approx(expected_pressure_Pa, rel=2e-12)
        previous_time_s = row["time_s"]

    total_heat_J = sum(heat_W * duration_s for heat_W, duration_s in heat_windows)
    assert record["boundary_energy_ledger"]["direct_heat_J"] == pytest.approx(total_heat_J, abs=1e-9)
    assert record["mass_residual_kg"] == pytest.approx(0.0, abs=1e-13)
    assert record["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)
    assert record["ledger_energy_residual_J"] == pytest.approx(0.0, abs=1e-8)
