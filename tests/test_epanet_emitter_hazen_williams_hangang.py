"""Independent EPANET emitter-leak and Hazen-Williams junction reference.

All values are authored synthetic SI inputs; this checks the steady boundary
balance only, not leak calibration or a transient rupture process.
"""
from __future__ import annotations

import math

import pytest

from pipeline_integrity.water import WaterRun, simulate_water_network


def _reference_flow(reservoir_head_m, junction_elevation_m, length_m, diameter_m,
                    hazen_williams_C, emitter_coefficient):
    available_static_head = reservoir_head_m - junction_elevation_m
    def friction_head(flow_m3_s):
        return (10.67 * length_m * flow_m3_s**1.852 /
                (hazen_williams_C**1.852 * diameter_m**4.871))
    def residual(flow_m3_s):
        pressure_head = available_static_head - friction_head(flow_m3_s)
        if pressure_head < 0:
            return -flow_m3_s
        return emitter_coefficient * math.sqrt(pressure_head) - flow_m3_s
    lo, hi = 0.0, emitter_coefficient * math.sqrt(available_static_head)
    assert residual(lo) > 0.0 and residual(hi) <= 0.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if residual(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


@pytest.mark.parametrize(
    "length_m, diameter_m, hazen_williams_C, emitter_coefficient",
    [(100.0, 0.20, 120.0, 0.004),
     (250.0, 0.30, 110.0, 0.007),
     (50.0, 0.15, 130.0, 0.003)],
)
def test_epanet_emitter_flow_matches_independent_pipe_and_leak_boundary(
    tmp_path, length_m, diameter_m, hazen_williams_C, emitter_coefficient
):
    wntr = pytest.importorskip("wntr")
    reservoir_head_m = 100.0
    junction_elevation_m = 10.0
    expected_flow = _reference_flow(
        reservoir_head_m, junction_elevation_m, length_m, diameter_m,
        hazen_williams_C, emitter_coefficient,
    )
    expected_friction_head = (
        10.67 * length_m * expected_flow**1.852 /
        (hazen_williams_C**1.852 * diameter_m**4.871)
    )
    expected_pressure_head = reservoir_head_m - junction_elevation_m - expected_friction_head
    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("supply", base_head=reservoir_head_m)
    model.add_junction(
        "leak", base_demand=0.0, elevation=junction_elevation_m,
    )
    leak = model.get_node("leak")
    # WNTR exposes emitters as a mutable node property after node creation.
    leak.emitter_coefficient = emitter_coefficient
    model.add_pipe(
        "service", "supply", "leak", length=length_m, diameter=diameter_m,
        roughness=hazen_williams_C, minor_loss=0.0,
    )
    model.options.hydraulic.headloss = "H-W"
    source = tmp_path / "authored_emitter_leak.inp"
    wntr.network.write_inpfile(model, str(source))

    result = simulate_water_network(source, WaterRun())
    assert result["status"] == "computed"
    assert result["flow_m3_s"]["service"][0] == pytest.approx(expected_flow, abs=2e-7)
    assert result["pressure_head_m"]["leak"][0] == pytest.approx(expected_pressure_head, abs=2e-3)
    assert result["flow_m3_s"]["service"][0] == pytest.approx(
        emitter_coefficient * math.sqrt(result["pressure_head_m"]["leak"][0]), abs=2e-7
    )
    assert result["maximum_junction_continuity_residual_m3_s"] < 1e-7
    assert result["hydraulic_settings"]["headloss"] == "H-W"
