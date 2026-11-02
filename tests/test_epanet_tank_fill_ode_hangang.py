"""Independent Hazen-Williams tank-filling storage trajectory.

Synthetic reservoir, pipe and tank dimensions only. This compares the native
extended-period model against a closed-form head ODE and an inventory balance.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from pipeline_integrity.water import WaterRun, simulate_water_network


# EPANET 2.2 computes H-W resistance with 4.727 in its US customary internal
# units. Convert that source coefficient to SI rather than using the rounded
# 10.67 textbook display constant.
HW_SI = 4.727 * 0.3048**4.871 / 0.028316846592**1.852


def _hazen_williams_loss(length_m, diameter_m, coefficient, flow_m3_s):
    return (
        HW_SI * length_m * abs(flow_m3_s) ** 1.852
        / (coefficient**1.852 * diameter_m**4.871)
    )


def _closed_form_level(time_s, reservoir_head_m, initial_level_m, tank_area_m2,
                       length_m, diameter_m, coefficient):
    exponent = 1.0 / 1.852
    flow_scale = (
        coefficient**1.852 * diameter_m**4.871 / (HW_SI * length_m)
    ) ** exponent
    initial_difference = reservoir_head_m - initial_level_m
    remaining = initial_difference**(1.0 - exponent) - (
        1.0 - exponent
    ) * flow_scale / tank_area_m2 * time_s
    assert remaining > 0.0
    return reservoir_head_m - remaining ** (1.0 / (1.0 - exponent))


@pytest.mark.parametrize("hydraulic_timestep_s", [60, 10])
def test_epanet_hw_tank_fill_matches_storage_ode_and_inflow_conservation(
    tmp_path, hydraulic_timestep_s
):
    wntr = pytest.importorskip("wntr")
    duration_s = 3600
    reservoir_head_m, initial_level_m = 50.0, 10.0
    tank_diameter_m = 25.0
    tank_area_m2 = math.pi * tank_diameter_m**2 / 4.0
    upstream_length_m, downstream_length_m = 400.0, 600.0
    length_m = upstream_length_m + downstream_length_m
    diameter_m, coefficient = 0.25, 120.0
    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("supply", base_head=reservoir_head_m)
    model.add_junction("midpoint", base_demand=0.0, elevation=0.0)
    model.add_tank(
        "storage", elevation=0.0, init_level=initial_level_m,
        min_level=0.0, max_level=30.0, diameter=tank_diameter_m, min_vol=0.0,
    )
    model.add_pipe(
        "fill_upstream", "supply", "midpoint", length=upstream_length_m,
        diameter=diameter_m, roughness=coefficient, minor_loss=0.0,
    )
    model.add_pipe(
        "fill_downstream", "midpoint", "storage", length=downstream_length_m,
        diameter=diameter_m, roughness=coefficient, minor_loss=0.0,
    )
    model.options.hydraulic.headloss = "H-W"
    source = tmp_path / f"authored_tank_fill_dt_{hydraulic_timestep_s}.inp"
    wntr.network.write_inpfile(model, str(source))

    result = simulate_water_network(
        source,
        WaterRun(
            duration_s=float(duration_s),
            hydraulic_timestep_s=float(hydraulic_timestep_s),
            report_timestep_s=float(hydraulic_timestep_s),
            continuity_tolerance_m3_s=1e-7,
        ),
    )
    assert result["status"] == "computed"
    times = np.asarray(result["time_s"], dtype=float)
    levels = np.asarray(result["pressure_head_m"]["storage"], dtype=float)
    upstream_flows = np.asarray(result["flow_m3_s"]["fill_upstream"], dtype=float)
    downstream_flows = np.asarray(result["flow_m3_s"]["fill_downstream"], dtype=float)
    exact_levels = np.asarray([
        _closed_form_level(
            time_s, reservoir_head_m, initial_level_m, tank_area_m2,
            length_m, diameter_m, coefficient,
        )
        for time_s in times
    ])
    # EPANET extended-period hydraulics advances tank volume with the net flow
    # from the just-solved hydraulic state, then solves hydraulics again at the
    # updated level. This left-rectangle recurrence is the independent discrete
    # oracle for reported states; the closed-form ODE remains a timestep
    # refinement reference, not an exact match at finite hydraulic intervals.
    discrete_levels = [initial_level_m]
    for interval_s, flow_m3_s in zip(np.diff(times), upstream_flows[:-1], strict=True):
        discrete_levels.append(discrete_levels[-1] + flow_m3_s * interval_s / tank_area_m2)
    discrete_levels = np.asarray(discrete_levels)
    assert times[0] == 0.0 and times[-1] == duration_s
    assert levels[0] == pytest.approx(initial_level_m, abs=1e-9)
    assert levels == pytest.approx(discrete_levels, abs=2e-5)
    continuous_error = float(np.max(np.abs(levels - exact_levels)))
    assert continuous_error < 0.08
    assert np.all(upstream_flows >= 0.0)
    assert upstream_flows == pytest.approx(downstream_flows, abs=1e-7)
    midpoint_heads = np.asarray(result["pressure_head_m"]["midpoint"], dtype=float)
    expected_midpoint_heads = reservoir_head_m - np.asarray([
        _hazen_williams_loss(upstream_length_m, diameter_m, coefficient, flow)
        for flow in upstream_flows
    ])
    # EPANET 2.2's iterative hydraulic solve and serialized SI/US conversion
    # leave a sub-millimetre endpoint residual against the independently
    # converted H-W law; keep this much smaller than the model head losses.
    assert midpoint_heads == pytest.approx(expected_midpoint_heads, abs=3e-4)
    integrated_inflow = float(np.sum(upstream_flows[:-1] * np.diff(times)))
    storage_increase = tank_area_m2 * (levels[-1] - levels[0])
    assert integrated_inflow == pytest.approx(storage_increase, rel=0.01, abs=0.02)
    assert result["maximum_junction_continuity_residual_m3_s"] < 1e-7
