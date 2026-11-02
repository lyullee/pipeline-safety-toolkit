"""Independent two-segment first-order water-quality reaction oracle.

This verifies per-pipe decay overrides and fallback composition against the
closed-form characteristic solution; it does not claim field chemistry fit.
"""

import math
from pathlib import Path

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.water import WaterRun
from pipeline_integrity.water_quality import WaterQualityRun, WaterQualitySource, simulate_water_quality


@pytest.mark.parametrize(
    "bulk_rate_per_s,pipe_rates_per_s",
    [
        (0.004, {"P1": 0.001, "P2": 0.002}),
        (0.003, {"P1": 0.001}),
    ],
)
def test_two_pipe_first_order_decay_matches_piecewise_characteristic_exponential(
    tmp_path, bulk_rate_per_s, pipe_rates_per_s
):
    wntr = pytest.importorskip("wntr")
    flow_m3_s = 0.01
    diameter_1_m, residence_1_s = 0.10, 100.0
    diameter_2_m, residence_2_s = 0.20, 200.0
    area_1_m2 = math.pi * diameter_1_m**2 / 4.0
    area_2_m2 = math.pi * diameter_2_m**2 / 4.0
    length_1_m = flow_m3_s * residence_1_s / area_1_m2
    length_2_m = flow_m3_s * residence_2_s / area_2_m2

    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("R", base_head=100.0)
    model.add_junction("J1", base_demand=0.0, elevation=0.0)
    model.add_junction("J2", base_demand=flow_m3_s, elevation=0.0)
    model.add_pipe("P1", "R", "J1", length=length_1_m, diameter=diameter_1_m, roughness=120.0)
    model.add_pipe("P2", "J1", "J2", length=length_2_m, diameter=diameter_2_m, roughness=120.0)
    input_path = tmp_path / "two_segment_decay.inp"
    wntr.network.write_inpfile(model, str(input_path))

    run = WaterQualityRun(
        hydraulics=WaterRun(duration_s=450.0, hydraulic_timestep_s=60.0, report_timestep_s=5.0),
        mode="concentration",
        parameter_source_id="analytic_piecewise_decay",
        quality_timestep_s=1.0,
        quality_tolerance=1e-8,
        initial_default_value=0.0,
        species_id="passive_marker",
        sources=(
            WaterQualitySource(
                source_id="constant_inlet",
                node_id="R",
                source_type="CONCEN",
                strength=0.001,
                unit="kg/m3",
            ),
        ),
        bulk_decay_per_s=bulk_rate_per_s,
        pipe_bulk_decay_per_s=pipe_rates_per_s,
    )
    context = RunContext(
        asset_id="two_segment_decay_network",
        scenario_id="piecewise_first_order_reference",
        clock_id="elapsed",
        origin="synthetic",
    )
    result = simulate_water_quality(input_path, run, context)

    assert result["status"] == "computed"
    assert result["checks"]["native_balance_within_tolerance"]
    assert result["native_diagnostics"]["mass_balance_ratio"] == pytest.approx(1.0, abs=1e-7)
    assert result["hydraulics"]["flow_m3_s"]["P1"][-1] == pytest.approx(flow_m3_s, abs=1e-8)
    assert result["hydraulics"]["flow_m3_s"]["P2"][-1] == pytest.approx(flow_m3_s, abs=1e-8)

    rate_1 = pipe_rates_per_s.get("P1", bulk_rate_per_s)
    rate_2 = pipe_rates_per_s.get("P2", bulk_rate_per_s)
    expected = 0.001 * math.exp(-rate_1 * residence_1_s - rate_2 * residence_2_s)
    actual = result["node_quality"]["J2"][-1]
    assert result["elapsed_time_s"][-1] > residence_1_s + residence_2_s
    assert actual == pytest.approx(expected, rel=5e-3, abs=1e-9)