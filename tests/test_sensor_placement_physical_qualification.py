"""Bounded information-matrix and observability checks for sensor placement."""

import math

import pytest

from examples.sensor_placement import make_case
from pipeline_integrity.sensor_placement import plan_sensor_placement
from test_sensor_placement import FiniteModeOracle, amend


def _scalar_information(gradient, covariance):
    """Direct 1D inverse-covariance quadratic form (2x2 only)."""
    a, b = covariance[0]
    c, d = covariance[1]
    determinant = a * d - b * c
    return (gradient[0] ** 2 * d - gradient[0] * gradient[1] * (b + c) + gradient[1] ** 2 * a) / determinant


def test_selected_design_matches_direct_scalar_gls_information_oracle():
    request, context = make_case(reviewed=True)
    result = plan_sensor_placement(request, context, backend=FiniteModeOracle())
    assert result["status"] == "optimal"
    assert set(result["selected"]["sensor_ids"]) == {"a", "c"}

    gradient = [100000.0 * 0.001, 100000.0 * 0.001]
    expected_ac = _scalar_information(gradient, [[10000.0, 0.0], [0.0, 40000.0]])
    selected = result["selected"]
    assert selected["diagnostic"]["normalized_information"][0][0] == pytest.approx(expected_ac)
    assert selected["loss"] == pytest.approx(-math.log(expected_ac))

    ab = next(row for row in result["subsets"] if set(row["sensor_ids"]) == {"a", "b"})
    expected_ab = _scalar_information(gradient, [[10000.0, 9000.0], [9000.0, 10000.0]])
    assert expected_ab == pytest.approx(20.0 / 19.0)
    assert ab["diagnostic"]["normalized_information"][0][0] == pytest.approx(expected_ab)


def test_parameter_scale_changes_information_quadratically_and_loss_logarithmically():
    request, context = make_case(reviewed=True)
    before = plan_sensor_placement(request, context, backend=FiniteModeOracle())
    request, context = amend(
        request, context, parameters=(request.parameters[0].model_copy(update={"reference_scale": 0.002}),)
    )
    after = plan_sensor_placement(request, context, backend=FiniteModeOracle())
    base_info = before["selected"]["diagnostic"]["normalized_information"][0][0]
    scaled_info = after["selected"]["diagnostic"]["normalized_information"][0][0]
    assert scaled_info == pytest.approx(4.0 * base_info)
    assert after["selected"]["loss"] == pytest.approx(before["selected"]["loss"] - 2.0 * math.log(2.0))


def test_missing_cross_covariance_is_not_replaced_by_independence():
    request, context = make_case(reviewed=True)
    covariance = ((10000.0, None, 0.0), (None, 10000.0, 0.0), (0.0, 0.0, 40000.0))
    request, context = amend(request, context, noise=request.noise.model_copy(update={"covariance": covariance}))
    result = plan_sensor_placement(request, context, backend=FiniteModeOracle())
    ab = next(row for row in result["subsets"] if set(row["sensor_ids"]) == {"a", "b"})
    assert result["status"] == "needs_data"
    assert not result["joint_noise_completion_qualified"]
    assert not result["global_design_proven_optimal"]
    assert ab["status"] == "needs_data"


def test_rank_deficient_linearization_is_withheld_without_regularization():
    request, context = make_case(reviewed=True)
    extra_parameter = request.parameters[0].model_copy(update={"id": "second_parameter"})
    linearization = request.linearization.model_copy(
        update={
            "parameter_ids": ("leak_rate", "second_parameter"),
            "jacobian": ((100000.0, 0.0),) * len(request.measurements),
        }
    )
    request, context = amend(
        request,
        context,
        parameters=(*request.parameters, extra_parameter),
        linearization=linearization,
    )
    result = plan_sensor_placement(request, context, backend=FiniteModeOracle())
    assert result["status"] == "infeasible"
    assert result["selected"] is None
    assert all(row["status"] == "rank_deficient" for row in result["subsets"])
