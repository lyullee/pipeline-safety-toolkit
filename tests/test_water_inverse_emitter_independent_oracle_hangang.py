"""Independent H-W plus emitter oracle through the water inverse provider.

The synthetic observations are generated from a separate scalar balance, not
from the WaterForward/EPANET provider being calibrated. This checks an emitter
parameter binding and a held-out hydraulic response; it is not field data.
"""

from __future__ import annotations

import hashlib
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.inverse import (
    FitParameter,
    FitRequest,
    ResidualScale,
    fit_parameters,
    observations_from_frames,
)
from pipeline_integrity.water import WaterRun
from pipeline_integrity.water_inverse import WaterForward, WaterParameter, WaterSensor


def _independent_emitter_solution(
    reservoir_head_m: float,
    elevation_m: float,
    length_m: float,
    diameter_m: float,
    hazen_williams_c: float,
    emitter_coefficient: float,
) -> tuple[float, float]:
    available_head_m = reservoir_head_m - elevation_m

    def friction_head(flow_m3_s: float) -> float:
        return (
            10.67
            * length_m
            * flow_m3_s**1.852
            / (hazen_williams_c**1.852 * diameter_m**4.871)
        )

    def residual(flow_m3_s: float) -> float:
        pressure_head_m = available_head_m - friction_head(flow_m3_s)
        if pressure_head_m < 0.0:
            return -flow_m3_s
        return emitter_coefficient * math.sqrt(pressure_head_m) - flow_m3_s

    lower = 0.0
    upper = emitter_coefficient * math.sqrt(available_head_m)
    assert residual(lower) > 0.0 and residual(upper) <= 0.0
    for _ in range(120):
        midpoint = (lower + upper) / 2.0
        if residual(midpoint) > 0.0:
            lower = midpoint
        else:
            upper = midpoint
    flow_m3_s = (lower + upper) / 2.0
    pressure_head_m = available_head_m - friction_head(flow_m3_s)
    return flow_m3_s, pressure_head_m


def test_water_inverse_emitter_fit_matches_independent_hazen_williams_balance(tmp_path):
    wntr = pytest.importorskip("wntr")

    reservoir_head_m = 90.0
    junction_elevation_m = 10.0
    length_m = 120.0
    diameter_m = 0.25
    hazen_williams_c = 120.0
    true_emitter_coefficient = 0.006
    expected_flow_m3_s, expected_pressure_head_m = _independent_emitter_solution(
        reservoir_head_m,
        junction_elevation_m,
        length_m,
        diameter_m,
        hazen_williams_c,
        true_emitter_coefficient,
    )

    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("supply", base_head=reservoir_head_m)
    model.add_junction("leak", base_demand=0.0, elevation=junction_elevation_m)
    model.add_pipe(
        "service",
        "supply",
        "leak",
        length=length_m,
        diameter=diameter_m,
        roughness=hazen_williams_c,
        minor_loss=0.0,
    )
    model.options.hydraulic.headloss = "H-W"
    network_path = tmp_path / "authored_single_emitter.inp"
    wntr.network.write_inpfile(model, str(network_path))
    network_sha256 = file_digest(network_path)

    forward = WaterForward(
        network_path,
        specification=WaterRun(
            duration_s=300,
            hydraulic_timestep_s=100,
            report_timestep_s=100,
        ),
        sensors=(
            WaterSensor(channel_id="flow", element_id="service", quantity="volume_flow"),
            WaterSensor(channel_id="head", element_id="leak", quantity="pressure_head"),
        ),
        parameters=(
            WaterParameter(
                parameter_id="emitter",
                kind="emitter_coefficient",
                element_ids=("leak",),
            ),
        ),
    )
    source = EvidenceRef(
        id="network",
        citation="Authored synthetic single-reservoir emitter network",
        origin="synthetic",
        sha256=network_sha256,
    )
    context = RunContext(
        asset_id="synthetic_emitter_network",
        scenario_id="independent_emitter_fit",
        clock_id="elapsed_seconds",
        origin="synthetic",
        evidence=(source,),
    )
    times = (0.0, 100.0, 200.0, 300.0)
    frames = tuple(
        SignalFrame(
            stream_id="independent_hydraulic_oracle",
            clock_id="elapsed_seconds",
            time_s=time_s,
            values={"flow": expected_flow_m3_s, "head": expected_pressure_head_m},
            units={"flow": "m3/s", "head": "m"},
            valid={"flow": True, "head": True},
            origin="synthetic",
        )
        for time_s in times
    )
    observations = observations_from_frames(
        frames,
        observation_id="independent_emitter_truth",
        asset_id=context.asset_id,
        source_digests={"network": network_sha256},
        scales={
            "flow": ResidualScale(value=1e-5, unit="m3/s", source_id="declared_flow_scale"),
            "head": ResidualScale(value=0.01, unit="m", source_id="declared_head_scale"),
        },
    )
    fit_request = FitRequest(
        parameters=(
            FitParameter(
                id="emitter",
                unit="m3/s/m^0.5",
                lower=0.002,
                upper=0.012,
                initial=0.003,
            ),
        ),
        provider_id="water_forward",
        provider_version="1",
        fit_end_s=100.0,
        validation_start_s=200.0,
        validation_end_s=300.0,
    )

    result = fit_parameters(observations, fit_request, context, forward)
    assert result["status"] == "fitted"
    assert result["fitted_parameters"]["emitter"] == pytest.approx(
        true_emitter_coefficient, rel=2e-3
    )
    assert result["validation"]["used_for_parameter_selection"] is False
    # Compare channels in their own units. The independently derived head is
    # an exact Hazen-Williams balance, while the forward solver has sub-mm
    # numerical tolerance; a combined scaled score obscures that distinction.
    fitted_channels = result["validation"]["fitted"]["channels"]
    assert fitted_channels["flow"]["rmse"] < 1e-7
    assert fitted_channels["head"]["rmse"] < 1e-3
    assert forward(
        {"emitter": true_emitter_coefficient}, times, context
    ).values["flow"] == pytest.approx((expected_flow_m3_s,) * len(times), abs=2e-7)
    assert forward(
        {"emitter": true_emitter_coefficient}, times, context
    ).values["head"] == pytest.approx((expected_pressure_head_m,) * len(times), abs=2e-3)
