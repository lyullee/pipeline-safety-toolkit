"""Closed-form checks for the imposed-flow liquid thermal chain.

The cases validate its single-cell semidiscrete thermal equations under the
declared constant-property, single-phase assumptions. They do not represent
hydraulic pressure solving or field qualification.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.liquid_thermal_chain import (
    LiquidThermalCell,
    LiquidThermalRequest,
    LiquidThermalWindow,
    evaluate_liquid_thermal_chain,
    verify_liquid_thermal_chain_record,
)


EVIDENCE_ID = "synthetic-thermal-inputs"


def _context(scenario_id):
    evidence = EvidenceRef(
        id=EVIDENCE_ID,
        citation="Authored synthetic single-cell thermal oracle inputs",
        origin="synthetic",
        sha256=digest(("liquid-thermal-chain-oracle", scenario_id)),
    )
    return RunContext(
        asset_id="synthetic-liquid-line",
        scenario_id=scenario_id,
        clock_id="elapsed-seconds",
        origin="synthetic",
        evidence=(evidence,),
    )


def _request(*, scenario_id, dt, duration, flow, tf0, tw0, g_fw, g_amb,
             supply_left=300.0, supply_right=300.0, ambient=300.0,
             fluid_heat=(), wall_heat=()):
    cell = LiquidThermalCell(
        id="cell-0",
        fluid_volume_m3=0.001,
        wall_heat_capacity_J_K=1200.0,
        fluid_wall_conductance_W_K=g_fw,
        wall_ambient_conductance_W_K=g_amb,
        initial_fluid_temperature_K=tf0,
        initial_wall_temperature_K=tw0,
        evidence_id=EVIDENCE_ID,
    )
    window = LiquidThermalWindow(
        id="window-0",
        duration_s=duration,
        mass_flow_left_to_right_kg_s=flow,
        left_supply_temperature_K=supply_left,
        right_supply_temperature_K=supply_right,
        ambient_temperature_K=ambient,
        fluid_heat_W=fluid_heat,
        wall_heat_W=wall_heat,
        evidence_id=EVIDENCE_ID,
    )
    return LiquidThermalRequest(
        id="analytic-single-cell",
        asset_id="synthetic-liquid-line",
        clock_id="elapsed-seconds",
        density_kg_m3=1000.0,
        heat_capacity_J_kg_K=4000.0,
        cells=(cell,),
        windows=(window,),
        maximum_step_s=dt,
        constant_properties_and_single_phase_reviewed=True,
        mechanical_energy_absent_or_in_supplied_heat_reviewed=True,
        evidence_ids=(EVIDENCE_ID,),
    )


def test_single_cell_fluid_wall_exchange_matches_exact_backward_euler_and_conserves_energy():
    fluid_capacity_J_K = 0.001 * 1000.0 * 4000.0
    wall_capacity_J_K = 1200.0
    conductance_W_K = 20.0
    initial_fluid_K, initial_wall_K = 360.0, 280.0
    duration_s = 2.0
    context = _context("fluid-wall-exchange")
    errors = []

    for dt in (0.1, 0.05, 0.025):
        request = _request(
            scenario_id="fluid-wall-exchange",
            dt=dt,
            duration=duration_s,
            flow=0.0,
            tf0=initial_fluid_K,
            tw0=initial_wall_K,
            g_fw=conductance_W_K,
            g_amb=0.0,
        )
        record = evaluate_liquid_thermal_chain(request, context)
        assert record["status"] == "computed"
        steps = math.ceil(duration_s / dt)
        actual_dt = duration_s / steps
        decay = 1.0 + actual_dt * conductance_W_K * (
            1.0 / fluid_capacity_J_K + 1.0 / wall_capacity_J_K
        )
        difference = initial_fluid_K - initial_wall_K
        total_capacity = fluid_capacity_J_K + wall_capacity_J_K
        equilibrium_K = (
            fluid_capacity_J_K * initial_fluid_K
            + wall_capacity_J_K * initial_wall_K
        ) / total_capacity
        expected_discrete_fluid_K = equilibrium_K + (
            wall_capacity_J_K / total_capacity
        ) * difference / decay**steps
        expected_discrete_wall_K = equilibrium_K - (
            fluid_capacity_J_K / total_capacity
        ) * difference / decay**steps
        final = record["states"][-1]
        assert final["fluid_temperature_K"][0] == pytest.approx(
            expected_discrete_fluid_K, abs=2e-10
        )
        assert final["wall_temperature_K"][0] == pytest.approx(
            expected_discrete_wall_K, abs=2e-10
        )

        exact_decay = math.exp(
            -duration_s * conductance_W_K
            * (1.0 / fluid_capacity_J_K + 1.0 / wall_capacity_J_K)
        )
        exact_fluid_K = equilibrium_K + (
            wall_capacity_J_K / total_capacity
        ) * difference * exact_decay
        errors.append(abs(final["fluid_temperature_K"][0] - exact_fluid_K))
        assert all(
            abs(row["step_energy_residual_J"]) < 2e-8
            for row in record["states"][1:]
        )
        assert abs(record["energy_residual_J"]) < 2e-8
        assert verify_liquid_thermal_chain_record(
            record, request=request, context=context
        )

    assert errors[2] < errors[1] < errors[0]
    assert errors[0] / errors[1] == pytest.approx(2.0, rel=0.08)
    assert errors[1] / errors[2] == pytest.approx(2.0, rel=0.08)


@pytest.mark.parametrize(
    ("flow_kg_s", "left_supply_K", "right_supply_K", "expected_supply_K"),
    ((0.2, 360.0, 280.0, 360.0), (-0.2, 360.0, 280.0, 280.0)),
)
def test_single_cell_advection_matches_exponential_response_in_both_flow_directions(
    flow_kg_s, left_supply_K, right_supply_K, expected_supply_K
):
    fluid_capacity_J_K = 0.001 * 1000.0 * 4000.0
    advective_capacity_rate_W_K = abs(flow_kg_s) * 4000.0
    initial_fluid_K = 300.0
    duration_s = 5.0
    context = _context(f"advection-{flow_kg_s:+g}")
    errors = []

    for dt in (0.25, 0.125, 0.0625):
        request = _request(
            scenario_id=f"advection-{flow_kg_s:+g}",
            dt=dt,
            duration=duration_s,
            flow=flow_kg_s,
            tf0=initial_fluid_K,
            tw0=300.0,
            g_fw=0.0,
            g_amb=0.0,
            supply_left=left_supply_K,
            supply_right=right_supply_K,
        )
        record = evaluate_liquid_thermal_chain(request, context)
        assert record["status"] == "computed"
        steps = math.ceil(duration_s / dt)
        actual_dt = duration_s / steps
        ratio = actual_dt * advective_capacity_rate_W_K / fluid_capacity_J_K
        expected_backward_euler_K = expected_supply_K + (
            initial_fluid_K - expected_supply_K
        ) / (1.0 + ratio) ** steps
        final_fluid_K = record["states"][-1]["fluid_temperature_K"][0]
        assert final_fluid_K == pytest.approx(expected_backward_euler_K, abs=2e-10)
        exact_K = expected_supply_K + (
            initial_fluid_K - expected_supply_K
        ) * math.exp(-duration_s * advective_capacity_rate_W_K / fluid_capacity_J_K)
        errors.append(abs(final_fluid_K - exact_K))
        assert abs(record["energy_residual_J"]) < 2e-8
        assert verify_liquid_thermal_chain_record(
            record, request=request, context=context
        )

    assert errors[2] < errors[1] < errors[0]
    assert errors[0] / errors[1] == pytest.approx(2.0, rel=0.08)
    assert errors[1] / errors[2] == pytest.approx(2.0, rel=0.08)
