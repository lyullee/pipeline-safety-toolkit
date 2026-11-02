"""Hangang candidate: liquid control-valve low-drop coefficient and unit oracle."""
import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="liquid-valve-oracle", scenario_id="subcritical-low-drop", clock_id="steady", origin="synthetic")


def _run(q_m3_s, dp_pa):
    return create_registry().evaluate(
        "equipment.control_valve_liquid",
        {
            "density_kg_m3": 1000.0,
            "vapour_pressure_pa_abs": 2000.0,
            "critical_pressure_pa_abs": 22.06e6,
            "viscosity_pa_s": 1.0e-3,
            "inlet_pressure_pa_abs": 500000.0,
            "outlet_pressure_pa_abs": 500000.0 - dp_pa,
            "volume_flow_m3_s": q_m3_s,
            "inlet_diameter_m": 0.1,
            "outlet_diameter_m": 0.1,
            "valve_diameter_m": 0.1,
            "pressure_recovery_factor": 0.9,
            "valve_style_factor": 0.46,
        },
        CTX,
    )


def test_low_drop_equal_bore_liquid_valve_kv_matches_independent_unit_oracle():
    # Equal inlet/outlet/valve bores eliminate reducer/piping geometry factors.
    # 1 kPa = 0.01 bar and Q = 36 m3/h; the flow is well below the liquid choking boundary.
    result = _run(0.01, 1000.0)
    assert result.status == "computed"
    # IEC Kv uses the reference density of water at 288.15 K (999.103290757 kg/m3).
    reference_water_density_kg_m3 = 999.10329075702327
    expected_kv = 36.0 * ((1000.0 / reference_water_density_kg_m3) / 0.01) ** 0.5
    diagnostics = result.values["diagnostics"]
    # Equal inlet, outlet, and trim diameters take the library's no-piping-correction
    # branch; the pinned owner reports FR and FP as None for this branch.
    assert diagnostics["FR"] is None
    assert diagnostics["FP"] is None
    assert result.values["kv"] == pytest.approx(expected_kv, rel=2e-8)


def test_coefficient_is_invariant_under_low_drop_flow_pressure_scaling():
    # Q multiplied by two and pressure drop by four preserves Q/sqrt(DeltaP).
    baseline = _run(0.01, 1000.0)
    scaled = _run(0.02, 4000.0)
    assert scaled.values["kv"] == pytest.approx(baseline.values["kv"], rel=2e-8)