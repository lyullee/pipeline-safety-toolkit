"""Brinell spherical-indenter contact-area mechanics references.

Inputs are hypothetical test measurements. Owner-computed indentation
hardness is checked against spherical-cap area geometry and force scaling.
"""

import math
import runpy

import pytest

from pipeline_integrity.hardness_exchange import evaluate_hardness


CASES = (
    (1000.0, 0.005, 0.003),
    (2000.0, 0.005, 0.003),
    (1500.0, 0.010, 0.006),
    (500.0, 0.005, 0.001),
    (250.0, 0.002, 0.0016),
)


def _base():
    return runpy.run_path("examples/hardness_methods.py")["make_cases"]()


@pytest.mark.parametrize("force,ball_diameter,indentation_diameter", CASES)
def test_brinell_spherical_cap_area_hardness_and_force_scaling(force, ball_diameter, indentation_diameter):
    requests, context = _base()
    request = requests["testing.brinell"].model_copy(update={
        "parameters": {
            "test_force_N": force,
            "ball_diameter_m": ball_diameter,
            "mean_indentation_diameter_m": indentation_diameter,
        }
    })

    record = evaluate_hardness(request, context)

    root = math.sqrt((ball_diameter - indentation_diameter) * (ball_diameter + indentation_diameter))
    spherical_cap_depth = indentation_diameter**2 / (ball_diameter + root)
    projected_contact_area = math.pi * ball_diameter * spherical_cap_depth
    expected_hardness = 2.0 * force / projected_contact_area
    values = record["values"]
    assert record["status"] == "computed"
    assert values["indentation_hardness_Pa"] == pytest.approx(expected_hardness, rel=2e-12, abs=1e-6)
    assert values["conventional_HBW"] == pytest.approx(expected_hardness / (9.80665e6), rel=2e-12, abs=1e-12)
    assert record["field_or_standard_qualification"] is False


def test_brinell_hardness_scales_linearly_with_force_at_fixed_geometry():
    requests, context = _base()
    base = requests["testing.brinell"]
    parameters = base.parameters
    first = evaluate_hardness(base, context)
    doubled = evaluate_hardness(base.model_copy(update={
        "parameters": parameters | {"test_force_N": 2.0 * parameters["test_force_N"]}
    }), context)
    assert doubled["values"]["indentation_hardness_Pa"] == pytest.approx(
        2.0 * first["values"]["indentation_hardness_Pa"], rel=2e-12
    )
