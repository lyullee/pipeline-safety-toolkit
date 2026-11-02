"""Independent piecewise-linear area and nodal-window oracle for profile capacity."""

import runpy
from copy import deepcopy
from pathlib import Path
import pytest

from pipeline_integrity.adapters import MaterialClient


EXAMPLE = runpy.run_path("examples/profile_capacity.py")
MAKE_CASE = EXAMPLE["make_case"]
CLIENT = MaterialClient()


def run_profile(position, thickness, *, reference_wall=0.01, ref_pressure=10e6, knots=None, factors=None,
                maximum_depth=0.7):
    request, context = MAKE_CASE()
    p = deepcopy(request.parameters)
    p.update(
        reference_wall_thickness_m=reference_wall,
        axial_position_m=list(position),
        measured_remaining_thickness_m=list(thickness),
        maximum_supported_depth_fraction=maximum_depth,
    )
    p["reference_capacity"]["reference_pressure_difference_Pa"] = ref_pressure
    for provider_key in ("reference_capacity", "bulging_table"):
        p[provider_key]["evidence"] = tuple(p[provider_key]["evidence"])
    if knots is not None:
        p["bulging_table"]["window_length_m"] = list(knots)
        p["bulging_table"]["factor"] = list(factors)
    return CLIENT.evaluate("structural.profile_capacity", p, context, location_id=request.location_id)


def direct_nodal_oracle(position, thickness, reference_wall, reference_pressure, knots, factors):
    relative_loss = [1.0 - t / reference_wall for t in thickness]
    segment_areas = [
        0.5 * (relative_loss[i] + relative_loss[i + 1]) * (position[i + 1] - position[i])
        for i in range(len(position) - 1)
    ]
    candidates = []
    for i in range(len(position) - 1):
        for j in range(i + 1, len(position)):
            length = position[j] - position[i]
            area = sum(segment_areas[i:j])
            alpha = area / length
            factor = knots[0]  # overwritten below by direct linear interpolation
            for k in range(len(knots) - 1):
                if knots[k] <= length <= knots[k + 1]:
                    fraction = (length - knots[k]) / (knots[k + 1] - knots[k])
                    factor = factors[k] + fraction * (factors[k + 1] - factors[k])
                    break
            reduction = (1.0 - alpha) / ((factor - 1.0 + 1.0 - alpha) / factor)
            candidates.append((reduction, i, j, alpha, factor, area * reference_wall, length))
    reduction, i, j, alpha, factor, area_m2, length = min(candidates, key=lambda row: row[0])
    return {
        "pressure": reference_pressure * reduction,
        "reduction": reduction,
        "start": i,
        "end": j,
        "alpha": alpha,
        "factor": factor,
        "area_m2": area_m2,
        "length": length,
        "window_count": len(candidates),
    }


def test_nonuniform_profile_matches_independent_piecewise_area_and_all_nodal_windows():
    x = [0.0, 0.2, 0.6, 1.0]
    t = [0.010, 0.009, 0.0065, 0.008]
    knots, factors = [0.0, 0.4, 1.0], [1.2, 1.7, 2.6]
    reference_pressure = 12.3e6
    expected = direct_nodal_oracle(x, t, 0.01, reference_pressure, knots, factors)
    result = run_profile(x, t, ref_pressure=reference_pressure, knots=knots, factors=factors)
    assert result.status == "computed"
    values = result.payload["values"]
    assert values["declared_model_pressure_difference_capacity_Pa"] == pytest.approx(expected["pressure"], rel=2e-14)
    assert values["capacity_reduction_factor"] == pytest.approx(expected["reduction"], rel=2e-14)
    window = values["critical_window"]
    assert (window["start_index"], window["end_index"]) == (expected["start"], expected["end"])
    assert window["loss_area_fraction"] == pytest.approx(expected["alpha"], rel=2e-14)
    assert window["bulging_factor"] == pytest.approx(expected["factor"], rel=2e-14)
    assert window["projected_loss_area_m2"] == pytest.approx(expected["area_m2"], rel=2e-14)
    assert values["scanned_nodal_windows"] == expected["window_count"] == 6


def test_uniform_full_length_loss_reduces_to_closed_form_area_factor():
    result = run_profile([0.0, 0.5, 1.0], [0.008, 0.008, 0.008])
    assert result.status == "computed"
    assert result.payload["values"]["capacity_reduction_factor"] == pytest.approx(8.0 / 9.0, rel=1e-14)
    assert result.payload["values"]["declared_model_pressure_difference_capacity_Pa"] == pytest.approx(10e6 * 8.0 / 9.0)
    assert result.payload["values"]["critical_window"]["loss_area_fraction"] == pytest.approx(0.2)


def test_pressure_reference_scales_capacity_without_changing_spatial_critical_window():
    x, t = [0.0, 0.2, 0.6, 1.0], [0.01, 0.009, 0.0065, 0.008]
    base = run_profile(x, t)
    scaled = run_profile(x, t, ref_pressure=3.5 * 10e6)
    assert scaled.payload["values"]["declared_model_pressure_difference_capacity_Pa"] == pytest.approx(
        3.5 * base.payload["values"]["declared_model_pressure_difference_capacity_Pa"], rel=2e-14
    )
    assert scaled.payload["values"]["critical_window"] == base.payload["values"]["critical_window"]


def test_consistent_length_and_wall_scaling_preserves_dimensionless_capacity_reduction():
    x, t = [0.0, 0.2, 0.6, 1.0], [0.01, 0.009, 0.0065, 0.008]
    base = run_profile(x, t, knots=[0.0, 0.4, 1.0], factors=[1.2, 1.7, 2.6])
    scale = 1000.0
    scaled = run_profile(
        [scale * value for value in x], [scale * value for value in t], reference_wall=0.01 * scale,
        knots=[0.0, 400.0, 1000.0], factors=[1.2, 1.7, 2.6],
    )
    assert scaled.payload["values"]["capacity_reduction_factor"] == pytest.approx(
        base.payload["values"]["capacity_reduction_factor"], rel=2e-14
    )
    assert scaled.payload["values"]["declared_model_pressure_difference_capacity_Pa"] == pytest.approx(
        base.payload["values"]["declared_model_pressure_difference_capacity_Pa"], rel=2e-14
    )


@pytest.mark.parametrize("thickness", [[0.0, 0.008, 0.008], [0.002, 0.002, 0.002]])
def test_zero_ligament_or_declared_depth_limit_does_not_emit_capacity(thickness):
    result = run_profile([0.0, 0.5, 1.0], thickness, maximum_depth=0.7)
    assert result.status == "out_of_scope"
    assert result.payload["values"] == {}
