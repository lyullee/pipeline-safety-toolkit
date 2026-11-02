"""Simultaneous liquid MOC boundary-wave superposition at a shared junction.

This is an authored synthetic test of characteristic-admittance weighting.
"""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient, simulate_liquid_transient


BASE_HEAD = 100.0
STEP_HEAD = 0.05
RHO = 998.0
WAVE_SPEED = 1000.0
SAMPLE_INDEX = 22  # Simultaneous fronts have reached the shared junction by 0.22 s.


def _case(left_diameter_m, right_diameter_m, left_step, right_step):
    def source_history(step):
        return History(
            time_s=(0.0, 0.10, 0.11),
            values=(BASE_HEAD, BASE_HEAD, BASE_HEAD + step),
        ) if step else History(time_s=(0.0,), values=(BASE_HEAD,))

    return LiquidTransient(
        nodes=(
            LiquidNode(
                id="left_source",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=source_history(left_step),
            ),
            LiquidNode(
                id="right_source",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=source_history(right_step),
            ),
            LiquidNode(
                id="shared_junction",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0,), values=(0.0,)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="left_reach",
                start_node="left_source",
                end_node="shared_junction",
                length_m=100.0,
                diameter_m=left_diameter_m,
                wave_speed_m_s=WAVE_SPEED,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
            LiquidPipe(
                id="right_reach",
                start_node="right_source",
                end_node="shared_junction",
                length_m=100.0,
                diameter_m=right_diameter_m,
                wave_speed_m_s=WAVE_SPEED,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=0.23,
        requested_timestep_s=0.01,
        density_kg_m3=RHO,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-8,
    )


def _junction_head(left_diameter_m, right_diameter_m, left_step, right_step):
    context = RunContext(
        asset_id="synthetic_two_source_waterline",
        scenario_id=f"wave-left-{left_step}-right-{right_step}",
        clock_id="elapsed",
        origin="synthetic",
    )
    result = simulate_liquid_transient(
        _case(left_diameter_m, right_diameter_m, left_step, right_step), context
    )
    assert result["status"] == "computed", result["notes"]
    assert result["time_s"][SAMPLE_INDEX] == pytest.approx(0.22, abs=1e-12)
    left = result["pipes"]["left_reach"]["end_head_m"][SAMPLE_INDEX]
    right = result["pipes"]["right_reach"]["end_head_m"][SAMPLE_INDEX]
    assert left == pytest.approx(right, abs=1e-12)
    assert result["maximum_junction_residual_m3_s"] < 1e-12
    assert result["all_volume_balances_within_tolerance"] is True
    return left


@pytest.mark.parametrize("left_diameter_m,right_diameter_m", ((0.1, 0.2), (0.2, 0.1)))
def test_simultaneous_head_waves_superpose_by_characteristic_admittance(
    left_diameter_m, right_diameter_m
):
    left_area = math.pi * left_diameter_m**2 / 4.0
    right_area = math.pi * right_diameter_m**2 / 4.0
    left_admittance = left_area / (RHO * WAVE_SPEED)
    right_admittance = right_area / (RHO * WAVE_SPEED)
    total_admittance = left_admittance + right_admittance

    left_only = _junction_head(left_diameter_m, right_diameter_m, STEP_HEAD, 0.0) - BASE_HEAD
    right_only = _junction_head(left_diameter_m, right_diameter_m, 0.0, STEP_HEAD) - BASE_HEAD
    both = _junction_head(left_diameter_m, right_diameter_m, STEP_HEAD, STEP_HEAD) - BASE_HEAD

    # A traveling head increment A has a characteristic increment 2*A,
    # because its accompanying flow increment is A/B. Continuity at the node
    # gives H_node=2*sum(Y_i*A_i)/sum(Y_i), before a boundary echo returns.
    # With identical reaches a one-sided wave therefore passes unchanged;
    # two equal counterpropagating waves double the head and cancel the flow.
    assert left_only == pytest.approx(2*STEP_HEAD * left_admittance / total_admittance, rel=2e-10, abs=1e-12)
    assert right_only == pytest.approx(2*STEP_HEAD * right_admittance / total_admittance, rel=2e-10, abs=1e-12)
    assert both == pytest.approx(left_only + right_only, rel=2e-10, abs=1e-12)
    assert both == pytest.approx(2*STEP_HEAD, rel=2e-10, abs=1e-12)
