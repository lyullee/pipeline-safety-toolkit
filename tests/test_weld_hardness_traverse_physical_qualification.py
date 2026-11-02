"""Bounded uncertainty-band arithmetic for declared weld hardness points."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.hardness_traverse"]


def evaluate(points, limits=None):
    parameters = dict(BASE.parameters)
    parameters["points"] = list(points)
    if limits is not None:
        parameters["declared_region_upper_limits_Pa"] = limits
    return evaluate_welding(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


def point(pid, x, region, hardness, uncertainty):
    return dict(
        point_id=pid,
        position_m=x,
        region_id=region,
        hardness_Pa=hardness,
        expanded_uncertainty_Pa=uncertainty,
    )


def regions_by_id(result):
    return {row["region_id"]: row for row in result["values"]["regions"]}


def test_uncertainty_interval_entirely_below_limit_is_satisfied():
    result = evaluate([point("p1", 0.0, "weld", 390.0, 10.0)], {"weld": 400.0})
    assert result["status"] == "computed"
    assert result["values"]["outcome"] == "satisfied"
    assert regions_by_id(result)["weld"]["outcome"] == "satisfied"


def test_uncertainty_interval_entirely_above_limit_is_not_satisfied():
    result = evaluate([point("p1", 0.0, "weld", 420.0, 10.0)], {"weld": 400.0})
    assert regions_by_id(result)["weld"]["outcome"] == "not_satisfied"
    assert result["values"]["outcome"] == "not_satisfied"


@pytest.mark.parametrize("hardness,uncertainty", [(395.0, 10.0), (405.0, 10.0), (400.0, 1.0)])
def test_uncertainty_interval_touching_or_straddling_limit_is_unknown(hardness, uncertainty):
    result = evaluate([point("p1", 0.0, "weld", hardness, uncertainty)], {"weld": 400.0})
    assert regions_by_id(result)["weld"]["outcome"] == "unknown"
    assert result["values"]["outcome"] == "unknown"


def test_peak_point_and_measured_position_are_preserved():
    points = [point("low", -0.01, "HAZ", 2.1e9, 0.1e9), point("peak", 0.01, "HAZ", 2.8e9, 0.2e9)]
    row = regions_by_id(evaluate(points, {"HAZ": 3.0e9}))["HAZ"]
    assert row["peak_hardness_Pa"] == pytest.approx(2.8e9)
    assert row["peak_point_id"] == "peak"
    assert row["peak_position_m"] == pytest.approx(0.01)
    assert row["observations"] == 2


def test_region_without_any_measured_point_is_unknown():
    result = evaluate([point("p1", 0.0, "weld", 3.5e9, 0.1e9)], {"weld": 4.0e9, "HAZ": 3.0e9})
    assert regions_by_id(result)["HAZ"] == {"region_id": "HAZ", "outcome": "unknown", "observations": 0}
    assert result["values"]["outcome"] == "unknown"
