"""Darcy-equilibrated liquid line with an instantaneous downstream closure wave.

The fixture is authored and synthetic. The oracle checks the independently
derived Darcy grade loss and the Joukowsky pressure-head jump at the instant
the boundary flow changes; it does not reuse internal MOC arrays.
"""

import math

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient, simulate_liquid_transient


RHO = 998.0
C = 1000.0
G = 9.80665
D = 0.2
LENGTH = 1000.0
FLOW = 0.01
DT = 0.1
BASE_HEAD = 100.0


def _case(friction_factor):
    area = math.pi * D**2 / 4.0
    steady_friction_head = friction_factor * LENGTH / D * (FLOW / area) ** 2 / (2.0 * G)
    return LiquidTransient(
        nodes=(
            LiquidNode(
                id="upstream_reservoir",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD + steady_friction_head,
                boundary="head",
                history=History(time_s=(0.0,), values=(BASE_HEAD + steady_friction_head,)),
            ),
            LiquidNode(
                id="closing_outlet",
                elevation_m=0.0,
                initial_head_m=BASE_HEAD,
                boundary="withdrawal",
                history=History(time_s=(0.0, DT, 2.0 * DT), values=(FLOW, FLOW, 0.0)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="line",
                start_node="upstream_reservoir",
                end_node="closing_outlet",
                length_m=LENGTH,
                diameter_m=D,
                wave_speed_m_s=C,
                darcy_friction_factor=friction_factor,
                initial_volume_flow_m3_s=FLOW,
            ),
        ),
        duration_s=2.0 * DT,
        requested_timestep_s=DT,
        density_kg_m3=RHO,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-8,
    )


@pytest.mark.parametrize("friction_factor", (0.005, 0.02, 0.04))
def test_friction_equilibrated_line_retains_exact_first_closure_joukowsky_jump(friction_factor):
    context = RunContext(
        asset_id="synthetic_frictional_waterline",
        scenario_id=f"closure-f-{friction_factor}",
        clock_id="elapsed",
        origin="synthetic",
    )
    spec = _case(friction_factor)
    result = simulate_liquid_transient(spec, context)
    assert result["status"] == "computed", result["notes"]

    area = math.pi * D**2 / 4.0
    expected_steady_drop = friction_factor * LENGTH / D * (FLOW / area) ** 2 / (2.0 * G)
    expected_closure_jump_m = C * FLOW / (G * area)
    trace = result["pipes"]["line"]["end_head_m"]

    assert trace[1] == pytest.approx(BASE_HEAD, abs=1e-12)
    assert spec.nodes[0].initial_head_m - spec.nodes[1].initial_head_m == pytest.approx(
        expected_steady_drop, rel=1e-12, abs=1e-12
    )
    assert trace[2] - trace[1] == pytest.approx(expected_closure_jump_m, rel=2e-12, abs=1e-11)
    assert result["maximum_junction_residual_m3_s"] < 1e-12
    assert result["all_volume_balances_within_tolerance"] is True
