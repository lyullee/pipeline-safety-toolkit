"""Analytic pressure-wave reflection at an abrupt area/impedance change.

The authored step is deliberately synthetic and checks the liquid MOC
junction against one-dimensional characteristic impedance relations.
"""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import (
    History,
    LiquidNode,
    LiquidPipe,
    LiquidTransient,
    simulate_liquid_transient,
)


RHO = 998.0
C = 1000.0
G = 9.80665
BASE_HEAD = 100.0
STEP_HEAD = 0.05
DT = 0.01
SAMPLE_INDEX = 22  # 0.22 s: step has reached the interface, before boundary return.


def _case(diameter_left_m, diameter_right_m):
    return LiquidTransient(
        nodes=(
            LiquidNode(
                id="upstream_reservoir",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=History(
                    time_s=(0.0, 0.10, 0.11),
                    values=(BASE_HEAD, BASE_HEAD, BASE_HEAD + STEP_HEAD),
                ),
            ),
            LiquidNode(
                id="area_change",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0,), values=(0.0,)),
            ),
            LiquidNode(
                id="downstream_reservoir",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=History(time_s=(0.0,), values=(BASE_HEAD,)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="left_pipe",
                start_node="upstream_reservoir",
                end_node="area_change",
                length_m=100.0,
                diameter_m=diameter_left_m,
                wave_speed_m_s=C,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
            LiquidPipe(
                id="right_pipe",
                start_node="area_change",
                end_node="downstream_reservoir",
                length_m=100.0,
                diameter_m=diameter_right_m,
                wave_speed_m_s=C,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=0.23,
        requested_timestep_s=DT,
        density_kg_m3=RHO,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-8,
    )


@pytest.mark.parametrize(
    "diameter_left_m,diameter_right_m",
    ((0.1, 0.2), (0.2, 0.1)),
)
def test_moc_interface_matches_independent_pressure_reflection_and_transmission(
    diameter_left_m, diameter_right_m
):
    context = RunContext(
        asset_id="synthetic_two_area_waterline",
        scenario_id="synthetic_head_step",
        clock_id="elapsed",
        origin="synthetic",
    )
    result = simulate_liquid_transient(_case(diameter_left_m, diameter_right_m), context)
    assert result["status"] == "computed", result["notes"]

    area_left = math.pi * diameter_left_m**2 / 4.0
    area_right = math.pi * diameter_right_m**2 / 4.0
    impedance_left = RHO * C / area_left
    impedance_right = RHO * C / area_right
    pressure_reflection = (impedance_right - impedance_left) / (impedance_right + impedance_left)
    pressure_transmission = 2.0 * impedance_right / (impedance_right + impedance_left)

    time = result["time_s"][SAMPLE_INDEX]
    assert time == pytest.approx(0.22, abs=1e-12)
    upstream_interface_head = result["pipes"]["left_pipe"]["end_head_m"][SAMPLE_INDEX]
    downstream_interface_head = result["pipes"]["right_pipe"]["start_head_m"][SAMPLE_INDEX]
    measured_transmitted = upstream_interface_head - BASE_HEAD
    measured_reflected = measured_transmitted - STEP_HEAD

    assert upstream_interface_head == pytest.approx(downstream_interface_head, abs=1e-12)
    assert measured_transmitted == pytest.approx(STEP_HEAD * pressure_transmission, rel=2e-10, abs=1e-12)
    assert measured_reflected / STEP_HEAD == pytest.approx(pressure_reflection, rel=2e-10, abs=1e-12)
    assert result["maximum_junction_residual_m3_s"] < 1e-12
    assert result["all_volume_balances_within_tolerance"] is True
