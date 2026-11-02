"""Geometric two-base-surface fit references outside the declared weld zone."""

import math
import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.joint_profile"]


def evaluate(position, height, half_width=0.005, method="ols"):
    parameters = dict(BASE.parameters)
    parameters.update(
        transverse_position_m=list(position),
        measured_surface_height_m=list(height),
        excluded_joint_half_width_m=half_width,
        fit_method=method,
    )
    return evaluate_welding(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


POSITION = (-0.03, -0.02, -0.01, 0.01, 0.02, 0.03)
HEIGHT = tuple(0.001 + 0.01 * x if x < 0 else 0.003 + 0.02 * x for x in POSITION)


def test_two_exact_base_planes_match_intercept_and_signed_angle_reference():
    result = evaluate(POSITION, HEIGHT)
    values = result["values"]
    assert result["status"] == "computed"
    assert values["left_slope"] == pytest.approx(0.01, abs=1e-12)
    assert values["right_slope"] == pytest.approx(0.02, abs=1e-12)
    assert values["signed_surface_offset_at_joint_m"] == pytest.approx(0.002, abs=1e-12)
    assert values["signed_angular_mismatch_rad"] == pytest.approx(math.atan(0.02) - math.atan(0.01), abs=1e-12)
    assert values["left_profile_rmse_m"] < 1e-12
    assert values["right_profile_rmse_m"] < 1e-12
    assert values["left_fit_observations"] == 3
    assert values["right_fit_observations"] == 3
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "welding.joint_profile"


def test_common_vertical_translation_preserves_offset_mismatch_and_rmse():
    base = evaluate(POSITION, HEIGHT)["values"]
    shifted = evaluate(POSITION, tuple(y + 0.005 for y in HEIGHT))["values"]
    assert shifted["signed_surface_offset_at_joint_m"] == pytest.approx(base["signed_surface_offset_at_joint_m"])
    assert shifted["signed_angular_mismatch_rad"] == pytest.approx(base["signed_angular_mismatch_rad"])
    assert shifted["left_slope"] == pytest.approx(base["left_slope"])
    assert shifted["right_slope"] == pytest.approx(base["right_slope"])


def test_right_side_translation_changes_only_signed_joint_offset():
    changed = tuple(y + (0.004 if x > 0 else 0.0) for x, y in zip(POSITION, HEIGHT, strict=True))
    result = evaluate(POSITION, changed)["values"]
    assert result["signed_surface_offset_at_joint_m"] == pytest.approx(0.006, abs=1e-12)
    assert result["signed_angular_mismatch_rad"] == pytest.approx(math.atan(0.02) - math.atan(0.01), abs=1e-12)


def test_fewer_than_three_points_on_either_side_is_withheld():
    result = evaluate((-0.02, -0.01, 0.01, 0.02, 0.03, 0.04), (0.001, 0.002, 0.003, 0.004, 0.005, 0.006), half_width=0.015)
    assert result["status"] == "needs_data"
    assert result["values"] == {}


def test_increasing_position_order_is_required_and_not_silently_sorted():
    with pytest.raises(ValueError, match="strictly increasing transverse profile positions"):
        evaluate(tuple(reversed(POSITION)), tuple(reversed(HEIGHT)))
