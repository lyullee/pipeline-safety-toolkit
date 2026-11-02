"""Elastic-wall celerity bridged to a liquid MOC boundary-event arrival.

This synthetic case independently computes the fluid/pipe compliance speed,
then verifies that the owner solver uses that speed for an exact-grid closed
end wave arrival and its pressure reflection.
"""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.models.hydraulics import WaveSpeed, wave_speed
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient, simulate_liquid_transient


RHO = 998.0
K = 2.2e9
E = 2.0e11
D = 0.1
WALL = 0.01
RESTRAINT = 0.8
G = 9.80665
DT = 0.001
SEGMENTS = 20
BASE_HEAD = 100.0
SOURCE_STEP_HEAD = 0.01


def test_pipe_compliance_celerity_sets_moc_arrival_and_closed_end_reflection():
    compliance = 1.0 / K + RESTRAINT * D / (E * WALL)
    independent_speed = math.sqrt(1.0 / (RHO * compliance))
    provider = wave_speed(
        WaveSpeed(
            density_kg_m3=RHO,
            fluid_bulk_modulus_pa=K,
            pipe_elastic_modulus_pa=E,
            inner_diameter_m=D,
            wall_thickness_m=WALL,
            restraint_coefficient=RESTRAINT,
        )
    )
    assert provider.values["wave_speed_m_s"] == pytest.approx(independent_speed, rel=2e-14)

    length = independent_speed * DT * SEGMENTS
    specification = LiquidTransient(
        nodes=(
            LiquidNode(
                id="elastic-reservoir",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=History(
                    time_s=(0.0, DT, 2.0 * DT),
                    values=(BASE_HEAD, BASE_HEAD, BASE_HEAD + SOURCE_STEP_HEAD),
                ),
            ),
            LiquidNode(
                id="closed-outlet",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0,), values=(0.0,)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="elastic-line",
                start_node="elastic-reservoir",
                end_node="closed-outlet",
                length_m=length,
                diameter_m=D,
                wave_speed_m_s=provider.values["wave_speed_m_s"],
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=(SEGMENTS + 3) * DT,
        requested_timestep_s=DT,
        density_kg_m3=RHO,
        gravity_m_s2=G,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-10,
        mass_balance_tolerance_m3=1e-10,
    )
    context = RunContext(
        asset_id="synthetic-elastic-celerity-line",
        scenario_id="elastic-speed-closed-end-arrival",
        clock_id="elapsed",
        origin="synthetic",
    )
    record = simulate_liquid_transient(specification, context)
    assert record["status"] == "computed", record["notes"]
    assert record["grid"]["elastic-line"]["segments"] == SEGMENTS
    assert record["grid"]["elastic-line"]["wave_speed_m_s"] == pytest.approx(independent_speed, rel=2e-14)

    arrival_index = SEGMENTS + 2
    expected_arrival_s = 2.0 * DT + length / independent_speed
    assert record["time_s"][arrival_index] == pytest.approx(expected_arrival_s, abs=2e-14)
    end_head = record["pipes"]["elastic-line"]["end_head_m"]
    assert end_head[arrival_index - 1] == pytest.approx(BASE_HEAD, abs=1e-12)
    assert end_head[arrival_index] - BASE_HEAD == pytest.approx(2.0 * SOURCE_STEP_HEAD, rel=2e-10, abs=2e-12)
    assert record["maximum_junction_residual_m3_s"] < 1e-12
    assert record["all_volume_balances_within_tolerance"] is True
    assert record["vapour_pressure_margin_pa"] > 0.0
