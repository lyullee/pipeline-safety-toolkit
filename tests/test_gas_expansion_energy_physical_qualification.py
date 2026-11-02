"""Bounded polytropic ideal-gas expansion work through the pressure-test adapter."""

import math
import runpy

import pytest

from pipeline_integrity.pressure_tests import evaluate_pressure_test


CASES, _, _, CONTEXT = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
BASE = CASES["inspection.gas_expansion_energy"]


def evaluate(p1=2.0e5, p2=1.0e5, v1=0.1, gamma=1.4):
    parameters = dict(BASE.parameters)
    parameters.update(
        initial_absolute_pressure_Pa=p1,
        final_absolute_pressure_Pa=p2,
        initial_volume_m3=v1,
        heat_capacity_ratio=gamma,
    )
    return evaluate_pressure_test(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


def analytic(p1, p2, v1, gamma):
    v2 = v1 * (p1 / p2) ** (1.0 / gamma)
    work = (p1 * v1 - p2 * v2) / (gamma - 1.0)
    return v2, work


@pytest.mark.parametrize(
    "p1,p2,v1,gamma",
    [
        (2.0e5, 1.0e5, 0.1, 1.4),
        (9.0e5, 3.0e5, 0.04, 1.3),
        (5.0e5, 4.0e5, 0.25, 1.67),
        (7.0e5, 7.0e5, 0.08, 1.4),
    ],
)
def test_polytropic_final_volume_and_boundary_work_match_closed_form(p1, p2, v1, gamma):
    result = evaluate(p1, p2, v1, gamma)
    expected_volume, expected_work = analytic(p1, p2, v1, gamma)
    assert result["status"] == "computed"
    assert result["values"]["final_volume_m3"] == pytest.approx(expected_volume, rel=2e-12)
    assert result["values"]["ideal_expansion_work_J"] == pytest.approx(expected_work, rel=2e-12, abs=1e-10)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "inspection.gas_expansion_energy"


def test_pressure_scaling_changes_work_but_preserves_volume_ratio():
    base = evaluate(2.0e5, 1.0e5, 0.1, 1.4)
    scaled = evaluate(6.0e5, 3.0e5, 0.1, 1.4)
    assert scaled["values"]["final_volume_m3"] == pytest.approx(base["values"]["final_volume_m3"])
    assert scaled["values"]["ideal_expansion_work_J"] == pytest.approx(3.0 * base["values"]["ideal_expansion_work_J"])


def test_initial_volume_scaling_is_linear_in_final_volume_and_work():
    base = evaluate(2.0e5, 1.0e5, 0.1, 1.4)
    scaled = evaluate(2.0e5, 1.0e5, 0.35, 1.4)
    assert scaled["values"]["final_volume_m3"] == pytest.approx(3.5 * base["values"]["final_volume_m3"])
    assert scaled["values"]["ideal_expansion_work_J"] == pytest.approx(3.5 * base["values"]["ideal_expansion_work_J"])


def test_work_matches_integrated_polytropic_boundary_expression():
    p1, p2, v1, gamma = 3.0e5, 1.2e5, 0.15, 1.35
    v2 = v1 * (p1 / p2) ** (1.0 / gamma)
    result = evaluate(p1, p2, v1, gamma)
    # Check the independent boundary-work identity in addition to the closed form.
    assert math.isclose(
        result["values"]["ideal_expansion_work_J"],
        (p1 * v1 - p2 * v2) / (gamma - 1.0),
        rel_tol=2e-12,
    )


def test_compression_is_not_reported_as_expansion_work():
    result = evaluate(1.0e5, 1.2e5, 0.1, 1.4)
    assert result["status"] == "out_of_scope"
    assert result["values"] == {}
