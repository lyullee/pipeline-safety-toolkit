import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

CONTEXT = RunContext(
    asset_id="pump_reference", scenario_id="intersection", clock_id="elapsed", origin="synthetic"
)


def parameters():
    return {
        "reference_curve": {
            "volume_flow_m3_s": (0.0, 0.01, 0.02),
            "head_m": (60.0, 40.0, 20.0),
            "source_id": "synthetic_linear_reference",
        },
        "static_head_m": 20.0,
        "resistance_head_s2_m5": 200000.0,
        "parallel_pumps": 1,
        "series_pumps": 1,
        "density_kg_m3": 1000.0,
        "pump_efficiency": 0.8,
        "motor_efficiency": 0.9,
    }


def test_analytic_linear_curve_quadratic_system_and_power_budget():
    result = create_registry().evaluate("equipment.pump_system", parameters(), CONTEXT)
    assert result.status == "computed"
    assert result.values["volume_flow_m3_s"] == pytest.approx(0.01, abs=1e-12)
    assert result.values["total_head_m"] == pytest.approx(40.0)
    assert abs(result.values["head_intersection_residual_m"]) < 1e-8
    assert result.values["electrical_power_w"] == pytest.approx(1000 * 9.80665 * 0.01 * 40 / (0.8 * 0.9))


def test_identical_parallel_series_scaling_and_extrapolation_rejection():
    base = parameters()
    # Quadrupling two parallel branches and two series stages uses total system flow.
    parallel = create_registry().evaluate(
        "equipment.pump_system", {**base, "parallel_pumps": 2, "resistance_head_s2_m5": 50000.0}, CONTEXT
    )
    assert parallel.values["volume_flow_m3_s"] == pytest.approx(0.02)
    series = create_registry().evaluate(
        "equipment.pump_system",
        {**base, "series_pumps": 2, "static_head_m": 40.0, "resistance_head_s2_m5": 400000.0},
        CONTEXT,
    )
    assert series.values["total_head_m"] == pytest.approx(80.0)
    assert series.values["volume_flow_m3_s"] == pytest.approx(0.01)
    assert (
        create_registry().evaluate("equipment.pump_system", {**base, "static_head_m": 100.0}, CONTEXT).status
        == "out_of_scope"
    )


def test_nonmonotone_branch_is_not_silently_smoothed():
    p = parameters()
    p["reference_curve"]["head_m"] = (20.0, 60.0, 40.0)
    with pytest.raises(ValueError, match="decreasing"):
        create_registry().evaluate("equipment.pump_system", p, CONTEXT)
