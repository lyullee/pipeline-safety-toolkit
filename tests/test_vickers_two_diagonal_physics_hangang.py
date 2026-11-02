"""Vickers pyramidal-indentation geometry and force scaling references.

Synthetic inputs test the method-specific two-diagonal average and owner
conversion; no hardness-to-strength conversion or acceptance is inferred.
"""

import math
import runpy

import pytest

from pipeline_integrity.hardness_exchange import evaluate_hardness


CASES = (
    (98.0665, 0.00019, 0.00021),
    (196.133, 0.00019, 0.00021),
    (500.0, 0.00030, 0.00034),
    (25.0, 0.00008, 0.00010),
    (1200.0, 0.00050, 0.00046),
)


def _base():
    return runpy.run_path("examples/hardness_methods.py")["make_cases"]()


def _run(force, d1, d2):
    requests, context = _base()
    request = requests["testing.vickers"].model_copy(update={
        "parameters": {"test_force_N": force, "first_diagonal_m": d1, "second_diagonal_m": d2}
    })
    return evaluate_hardness(request, context)


@pytest.mark.parametrize("force,d1,d2", CASES)
def test_vickers_two_diagonal_mean_and_pyramid_area_conversion(force, d1, d2):
    record = _run(force, d1, d2)
    mean_diagonal = (d1 + d2) / 2.0
    expected_hardness = 2.0 * math.sin(math.radians(68.0)) * force / mean_diagonal**2
    values = record["values"]
    assert record["status"] == "computed"
    assert values["indentation_hardness_Pa"] == pytest.approx(expected_hardness, rel=2e-12, abs=1e-6)
    assert values["conventional_HV"] == pytest.approx(expected_hardness / (9.80665e6), rel=2e-12, abs=1e-12)
    assert record["field_or_standard_qualification"] is False


def test_vickers_diagonal_order_is_symmetric_and_force_scales_linearly():
    force, d1, d2 = CASES[0]
    first = _run(force, d1, d2)
    swapped = _run(force, d2, d1)
    doubled = _run(2.0 * force, d1, d2)
    base = first["values"]["indentation_hardness_Pa"]
    assert swapped["values"]["indentation_hardness_Pa"] == pytest.approx(base, rel=2e-12)
    assert doubled["values"]["indentation_hardness_Pa"] == pytest.approx(2.0 * base, rel=2e-12)
