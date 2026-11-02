"""Hangang candidate: independent incompressible differential-pressure meter oracle."""
import math

import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="dp-meter-oracle", scenario_id="steady-flow", clock_id="steady", origin="synthetic")


def _run(dp, rho=998.2, d_pipe=0.08, d_open=0.04, cd=0.62):
    return create_registry().evaluate(
        "instrument.liquid_dp_meter",
        {
            "pipe_diameter_m": d_pipe,
            "opening_diameter_m": d_open,
            "pressure_difference_pa": dp,
            "density_kg_m3": rho,
            "discharge_coefficient": cd,
        },
        CTX,
    )


def test_incompressible_orifice_discharge_and_mass_rate_close_independently():
    for dp, rho, d_pipe, d_open, cd in [
        (12500.0, 998.2, 0.08, 0.04, 0.62),
        (85000.0, 850.0, 0.15, 0.09, 0.71),
        (2400.0, 1025.0, 0.05, 0.025, 0.58),
    ]:
        result = _run(dp, rho, d_pipe, d_open, cd)
        assert result.status == "computed"
        beta = d_open / d_pipe
        area = math.pi * d_open**2 / 4.0
        expected_q = cd * area * math.sqrt(2.0 * dp / (rho * (1.0 - beta**4)))
        assert result.values["diameter_ratio"] == pytest.approx(beta, rel=1e-15)
        assert result.values["volume_flow_m3_s"] == pytest.approx(expected_q, rel=2e-14)
        assert result.values["mass_flow_kg_s"] == pytest.approx(rho * expected_q, rel=2e-14)


def test_meter_square_root_pressure_scaling_and_zero_dp_limit():
    baseline = _run(9000.0).values
    scaled = _run(36000.0).values
    zero = _run(0.0).values
    assert scaled["volume_flow_m3_s"] == pytest.approx(2.0 * baseline["volume_flow_m3_s"], rel=2e-14)
    assert scaled["mass_flow_kg_s"] == pytest.approx(2.0 * baseline["mass_flow_kg_s"], rel=2e-14)
    assert zero["volume_flow_m3_s"] == 0.0
    assert zero["mass_flow_kg_s"] == 0.0
