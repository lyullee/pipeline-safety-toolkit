"""Independent authored mean-stress correction ladder for fatigue screening.

The adapter/provider remains the calculation owner; the test checks the
closed-form Goodman relation on synthetic inputs only.
"""

import runpy

import pytest

from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue


CASES = (
    (120e6, 0.0, 800e6),
    (120e6, 200e6, 800e6),
    (120e6, 400e6, 800e6),
    (60e6, 400e6, 800e6),
    (120e6, 200e6, 400e6),
)


@pytest.mark.parametrize("alternating,mean,ultimate", CASES)
def test_goodman_mean_stress_ladder_matches_independent_closed_form(alternating, mean, ultimate):
    requests, context = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
    request = requests["fatigue.goodman"].model_copy(
        update={
            "parameters": {
                "alternating_stress_Pa": alternating,
                "mean_stress_Pa": mean,
                "ultimate_tensile_strength_Pa": ultimate,
            }
        }
    )

    record = evaluate_cyclic_fatigue(request, context)

    expected = alternating / (1.0 - mean / ultimate)
    assert record["status"] == "computed"
    assert record["values"]["equivalent_fully_reversed_amplitude_Pa"] == pytest.approx(
        expected, rel=2e-12, abs=1e-6
    )
    assert record["request"]["parameters"] == request.model_dump(mode="json")["parameters"]
    assert record["failure_probability"] is None
    assert record["material_or_code_acceptance"] is False
    assert record["field_or_population_validation"] is False
