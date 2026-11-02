"""Catalog-path physical checks for pump power and affinity laws."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX = RunContext(
    asset_id="pump-law-reference",
    scenario_id="synthetic-steady-pump",
    clock_id="steady-state",
    origin="synthetic",
)


@pytest.mark.parametrize(
    "flow,density,head,eta_p,eta_m,g",
    [
        (0.01, 1000.0, 40.0, 0.8, 0.9, 9.80665),
        (0.2, 850.0, 120.0, 0.72, 0.94, 9.81),
        (0.0, 998.2, 55.0, 0.65, 0.88, 9.80665),
    ],
)
def test_catalog_pump_power_matches_hydraulic_energy_rate(flow, density, head, eta_p, eta_m, g):
    result = evaluate(
        "equipment.pump_power",
        {
            "volume_flow_m3_s": flow,
            "density_kg_m3": density,
            "head_m": head,
            "pump_efficiency": eta_p,
            "motor_efficiency": eta_m,
            "gravity_m_s2": g,
        },
        CTX,
    )
    hydraulic = density * g * flow * head
    shaft = hydraulic / eta_p
    assert result.status == "computed"
    assert result.values["hydraulic_power_w"] == pytest.approx(hydraulic, rel=1e-14, abs=1e-13)
    assert result.values["shaft_power_w"] == pytest.approx(shaft, rel=1e-14, abs=1e-13)
    assert result.values["electrical_power_w"] == pytest.approx(shaft / eta_m, rel=1e-14, abs=1e-13)
    assert result.values["electrical_power_w"] >= result.values["shaft_power_w"] >= hydraulic


@pytest.mark.parametrize(
    "q0,h0,p0,n,d",
    [
        (0.01, 40.0, 6000.0, 1.5, 1.0),
        (0.08, 90.0, 75000.0, 0.72, 0.85),
        (0.0, 0.0, 0.0, 1.0, 1.0),
        (0.005, 30.0, 1500.0, 2.0, 1.1),
    ],
)
def test_catalog_pump_affinity_matches_similarity_scaling(q0, h0, p0, n, d):
    result = evaluate(
        "equipment.pump_affinity",
        {
            "reference_flow_m3_s": q0,
            "reference_head_m": h0,
            "reference_power_w": p0,
            "speed_ratio": n,
            "geometric_diameter_ratio": d,
        },
        CTX,
    )
    assert result.status == "computed"
    assert result.values["flow_m3_s"] == pytest.approx(q0 * n * d**3, rel=1e-14, abs=1e-14)
    assert result.values["head_m"] == pytest.approx(h0 * n**2 * d**2, rel=1e-14, abs=1e-14)
    assert result.values["power_w"] == pytest.approx(p0 * n**3 * d**5, rel=1e-14, abs=1e-13)
