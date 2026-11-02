"""Independent Hazen-Williams head-loss checks for the EPANET water adapter.

The inputs describe authored one-reservoir/one-junction systems. The expected
flow follows continuity and the expected pressure head uses the SI
Hazen-Williams relation, independently of the native EPANET result.
"""

import math

import pytest

from pipeline_integrity.water import WaterRun, simulate_water_network


@pytest.mark.parametrize(
    "demand_m3_s, length_m, diameter_m, coefficient",
    [
        (0.01, 100.0, 0.20, 120.0),
        (0.03, 250.0, 0.30, 110.0),
        (0.005, 50.0, 0.15, 130.0),
    ],
)
def test_single_pipe_epanet_matches_independent_hazen_williams_head_reference(
    tmp_path, demand_m3_s, length_m, diameter_m, coefficient
):
    wntr = pytest.importorskip("wntr")
    model = wntr.network.WaterNetworkModel()
    reservoir_head_m = 100.0
    junction_elevation_m = 10.0
    model.add_reservoir("supply", base_head=reservoir_head_m)
    model.add_junction(
        "consumer", base_demand=demand_m3_s, elevation=junction_elevation_m
    )
    model.add_pipe(
        "pipe",
        "supply",
        "consumer",
        length=length_m,
        diameter=diameter_m,
        roughness=coefficient,
        minor_loss=0.0,
    )
    model.options.hydraulic.headloss = "H-W"
    source = tmp_path / "authored_single_pipe.inp"
    wntr.network.write_inpfile(model, str(source))

    result = simulate_water_network(source, WaterRun())

    # SI Hazen-Williams, with Q in m^3/s, D in m and L,h in m.
    expected_friction_head_m = (
        10.67
        * length_m
        * demand_m3_s**1.852
        / (coefficient**1.852 * diameter_m**4.871)
    )
    expected_pressure_head_m = (
        reservoir_head_m - junction_elevation_m - expected_friction_head_m
    )
    assert result["status"] == "computed"
    assert result["flow_m3_s"]["pipe"][0] == pytest.approx(
        demand_m3_s, abs=1e-8
    )
    assert result["pressure_head_m"]["consumer"][0] == pytest.approx(
        expected_pressure_head_m, abs=2e-3
    )
    assert result["maximum_junction_continuity_residual_m3_s"] < 1e-8
    assert result["hydraulic_settings"]["headloss"] == "H-W"
