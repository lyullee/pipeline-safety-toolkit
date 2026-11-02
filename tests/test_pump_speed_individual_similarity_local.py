"""Same-geometry speed transformation with a mechanical energy cross-check."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="pump-speed", scenario_id="individual-similarity", clock_id="steady", origin="synthetic")


def transform(speed, q=0.02, head=25.0, power=8000.0):
    return evaluate("equipment.pump_affinity", {
        "reference_flow_m3_s": q, "reference_head_m": head,
        "reference_power_w": power, "speed_ratio": speed,
        "geometric_diameter_ratio": 1.0,
    }, CTX).values


@pytest.mark.parametrize("speed,expected", [
    (2.0, (0.04, 100.0, 64000.0)),
    (0.5, (0.01, 6.25, 1000.0)),
    (1.0, (0.02, 25.0, 8000.0)),
])
def test_authored_same_geometry_reference_points(speed, expected):
    result = transform(speed)
    assert tuple(result[k] for k in ("flow_m3_s", "head_m", "power_w")) == pytest.approx(expected)


def test_independent_hydraulic_energy_and_unchanged_efficiency_predict_shaft_power():
    density, gravity, efficiency = 875.0, 9.81, 0.76
    initial_power = density*gravity*0.02*25.0/efficiency
    for speed in (0.65, 1.25, 1.7):
        result = transform(speed, power=initial_power)
        independently_balanced_shaft_power = density*gravity*result["flow_m3_s"]*result["head_m"]/efficiency
        assert result["power_w"] == pytest.approx(independently_balanced_shaft_power, rel=2e-15)


def test_composed_speed_changes_equal_direct_speed_change():
    first = transform(1.4)
    composed = transform(0.8, first["flow_m3_s"], first["head_m"], first["power_w"])
    assert composed == pytest.approx(transform(1.12), rel=2e-15)


def test_zero_reference_state_remains_zero_without_creating_pump_performance():
    assert transform(1.8, 0.0, 0.0, 0.0) == {"flow_m3_s": 0.0, "head_m": 0.0, "power_w": 0.0}
