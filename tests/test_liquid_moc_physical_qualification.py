"""Bounded single-phase liquid MOC checks against the Joukowsky/characteristic solution."""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient, simulate_liquid_transient


RHO = 998.0
G = 9.80665
BASE_HEAD = 100.0
LENGTH = 100.0
DIAMETER = 0.1
STEP_TIME = 0.1

CONTEXT = RunContext(
    asset_id="synthetic_single_pipe",
    scenario_id="closed_end_head_step",
    clock_id="elapsed_seconds",
    origin="synthetic",
)


def case(*, wave_speed=1000.0, timestep=0.01, head_step=0.05, density=RHO, friction=0.0):
    # Choose a CFL=1 grid: length/(wave speed * timestep) is an integer.
    arrival = LENGTH / wave_speed
    # Match the final requested time to the CFL grid so ceil(duration/dt)
    # cannot add an unintended step through binary floating-point roundoff.
    end_time = math.nextafter(STEP_TIME + arrival, -math.inf)
    return LiquidTransient(
        nodes=(
            LiquidNode(
                id="reservoir",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="head",
                history=History(
                    time_s=(0.0, STEP_TIME - timestep, STEP_TIME),
                    values=(BASE_HEAD, BASE_HEAD, BASE_HEAD + head_step),
                ),
            ),
            LiquidNode(
                id="closed_end",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0,), values=(0.0,)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="line",
                start_node="reservoir",
                end_node="closed_end",
                length_m=LENGTH,
                diameter_m=DIAMETER,
                wave_speed_m_s=wave_speed,
                darcy_friction_factor=friction,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=end_time,
        requested_timestep_s=timestep,
        density_kg_m3=density,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-8,
    )


def simulate(specification):
    return simulate_liquid_transient(specification, CONTEXT)


@pytest.mark.parametrize(
    "wave_speed,timestep",
    [(1000.0, 0.01), (500.0, 0.02), (2000.0, 0.005)],
)
def test_closed_end_first_arrival_matches_joukowsky_head_and_distance_over_speed(wave_speed, timestep):
    spec = case(wave_speed=wave_speed, timestep=timestep)
    result = simulate(spec)
    assert result["status"] == "computed", result["notes"]

    expected_arrival = STEP_TIME + LENGTH / wave_speed
    index = round(expected_arrival / result["timestep_s"])
    assert result["time_s"][index] == pytest.approx(expected_arrival, abs=1e-12)
    terminal_head = result["pipes"]["line"]["end_head_m"][index]
    # A fixed-head step is doubled at the zero-flow (closed) termination.
    assert terminal_head - BASE_HEAD == pytest.approx(2.0 * 0.05, abs=1e-12)
    assert result["pipes"]["line"]["end_flow_m3_s"][index] == pytest.approx(0.0, abs=1e-12)
    assert result["all_volume_balances_within_tolerance"] is True
    assert result["maximum_pipe_volume_residual_m3"] <= 1e-8


def test_pressure_increment_matches_density_gravity_times_joukowsky_head():
    step = 0.08
    result = simulate(case(head_step=step))
    assert result["status"] == "computed", result["notes"]
    arrival = STEP_TIME + LENGTH / 1000.0
    index = round(arrival / result["timestep_s"])
    head_rise = result["pipes"]["line"]["end_head_m"][index] - BASE_HEAD
    expected_pa = RHO * G * (2.0 * step)
    terminal_absolute_pressure = 101325.0 + RHO * G * result["pipes"]["line"]["end_head_m"][index]
    initial_absolute_pressure = 101325.0 + RHO * G * BASE_HEAD
    assert head_rise == pytest.approx(2.0 * step, abs=1e-12)
    assert terminal_absolute_pressure - initial_absolute_pressure == pytest.approx(expected_pa, rel=1e-12, abs=1e-9)


def test_waveform_domain_guard_reports_vapour_pressure_boundary():
    result = simulate(case(head_step=-120.0))
    assert result["status"] == "out_of_scope"
    assert result["first_vapour_pressure_boundary"] is not None
    assert result["vapour_pressure_margin_pa"] <= 0.0


def test_inconsistent_initial_darcy_gradient_is_rejected_before_transient_update():
    spec = case(friction=0.02).model_copy(
        update={
            "pipes": (
                case(friction=0.02).pipes[0].model_copy(update={"initial_volume_flow_m3_s": 0.01}),
            )
        }
    )
    with pytest.raises(ValueError, match="initial flow/head gradient"):
        simulate(spec)
