"""Rigid-wall versus compliant-wall celerity shifts MOC event timing.

Both authored synthetic runs have identical geometry, step time and mesh
timestep. Only pipe-wall restraint changes, so the travel-time shift has an
independent fluid/pipe-compliance reference.
"""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.models.hydraulics import WaveSpeed, wave_speed
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient, simulate_liquid_transient


RHO = 1000.0
K = 1.0e9
E = 17_777_777_777.77778
D = 0.2
WALL = 0.02
LENGTH = 80.0
DT = 0.01
BASE_HEAD = 100.0
STEP_HEAD = 0.01


def _celerity(restraint):
    q = WaveSpeed(
        density_kg_m3=RHO,
        fluid_bulk_modulus_pa=K,
        pipe_elastic_modulus_pa=E,
        inner_diameter_m=D,
        wall_thickness_m=WALL,
        restraint_coefficient=restraint,
    )
    independent = math.sqrt(
        1.0
        / (
            RHO
            * (
                1.0 / K
                + restraint * D / (E * WALL)
            )
        )
    )
    owner_value = wave_speed(q).values["wave_speed_m_s"]
    assert owner_value == pytest.approx(independent, rel=2e-14)
    return independent


def _run(speed, scenario):
    spec = LiquidTransient(
        nodes=(
            LiquidNode(
                id="fixed-head-source",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=History(
                    time_s=(0.0, DT, 2.0 * DT),
                    values=(BASE_HEAD, BASE_HEAD, BASE_HEAD + STEP_HEAD),
                ),
            ),
            LiquidNode(
                id="zero-flow-end",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0,), values=(0.0,)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="same-geometry-line",
                start_node="fixed-head-source",
                end_node="zero-flow-end",
                length_m=LENGTH,
                diameter_m=D,
                wave_speed_m_s=speed,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=0.15,
        requested_timestep_s=DT,
        density_kg_m3=RHO,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-9,
    )
    context = RunContext(
        asset_id="synthetic-rigid-flexible-line",
        scenario_id=scenario,
        clock_id="elapsed",
        origin="synthetic",
    )
    record = simulate_liquid_transient(spec, context)
    assert record["status"] == "computed", record["notes"]
    return record


def test_compliant_pipe_wall_delays_the_same_closed_end_wave_event():
    rigid_speed = _celerity(restraint=0.0)
    compliant_speed = _celerity(restraint=1.0)
    assert rigid_speed == pytest.approx(1000.0, rel=2e-14)
    assert compliant_speed == pytest.approx(800.0, rel=2e-14)

    rigid = _run(rigid_speed, "rigid-wall")
    compliant = _run(compliant_speed, "compliant-wall")
    expected_rigid_segments = round(LENGTH / (rigid_speed * DT))
    expected_compliant_segments = round(LENGTH / (compliant_speed * DT))
    assert expected_rigid_segments == 8
    assert expected_compliant_segments == 10
    assert rigid["grid"]["same-geometry-line"]["segments"] == expected_rigid_segments
    assert compliant["grid"]["same-geometry-line"]["segments"] == expected_compliant_segments

    def first_event(record):
        trace = record["pipes"]["same-geometry-line"]["end_head_m"]
        index = next(i for i, head in enumerate(trace) if head > BASE_HEAD + STEP_HEAD)
        return index, record["time_s"][index], trace

    rigid_index, rigid_time, rigid_trace = first_event(rigid)
    compliant_index, compliant_time, compliant_trace = first_event(compliant)
    expected_rigid_time = 2.0 * DT + LENGTH / rigid_speed
    expected_compliant_time = 2.0 * DT + LENGTH / compliant_speed
    assert rigid_time == pytest.approx(expected_rigid_time, abs=1e-12)
    assert compliant_time == pytest.approx(expected_compliant_time, abs=1e-12)
    assert compliant_time - rigid_time == pytest.approx(
        LENGTH * (1.0 / compliant_speed - 1.0 / rigid_speed), abs=1e-12
    )
    assert rigid_index == expected_rigid_segments + 2
    assert compliant_index == expected_compliant_segments + 2
    assert rigid_trace[rigid_index] - BASE_HEAD == pytest.approx(2.0 * STEP_HEAD, rel=2e-10)
    assert compliant_trace[compliant_index] - BASE_HEAD == pytest.approx(2.0 * STEP_HEAD, rel=2e-10)
    assert rigid["all_volume_balances_within_tolerance"] is True
    assert compliant["all_volume_balances_within_tolerance"] is True
