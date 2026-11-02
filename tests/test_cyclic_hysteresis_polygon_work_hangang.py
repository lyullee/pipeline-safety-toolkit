"""Closed stress-strain cycle work from exact polygon area references.

Synthetic stress/strain loops exercise signed path integration through the
cyclic material owner. Work is energy density, not automatic fatigue damage.
"""

import runpy

import pytest

from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue


PATHS = (
    ((0.0, 120e6, 120e6, 0.0, 0.0), (0.0, 0.0, 0.002, 0.002, 0.0)),
    ((0.0, 0.0, 120e6, 120e6, 0.0), (0.0, 0.002, 0.002, 0.0, 0.0)),
    ((0.0, 0.0, 80e6, 0.0), (0.0, 0.003, 0.0015, 0.0)),
    ((0.0, 80e6, 0.0, 0.0), (0.0, 0.0015, 0.003, 0.0)),
)


def _base():
    return runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()


@pytest.mark.parametrize("stress_path,strain_path", PATHS)
def test_signed_closed_cycle_work_matches_independent_polygon_area(stress_path, strain_path):
    requests, context = _base()
    base = requests["fatigue.hysteresis_path"]
    count = len(stress_path)
    request = base.model_copy(update={
        "parameters": base.parameters | {
            "stress_Pa": list(stress_path),
            "strain_fraction": list(strain_path),
            "cycle_boundary_declared": True,
            "linear_closure_declared": False,
            "maximum_endpoint_stress_difference_Pa": 0.0,
            "maximum_endpoint_strain_difference": 0.0,
        },
        "path_sample_ids": tuple(f"sample-{i}" for i in range(count)),
        "sample_time_s": tuple(0.1 * i for i in range(count)),
        "observation_period_s": (0.0, 0.1 * (count - 1)),
    })

    record = evaluate_cyclic_fatigue(request, context)

    signed_polygon_area = 0.5 * sum(
        strain_path[i] * stress_path[(i + 1) % count]
        - strain_path[(i + 1) % count] * stress_path[i]
        for i in range(count)
    )
    expected_work = -signed_polygon_area
    values = record["values"]
    assert record["status"] == "computed"
    assert values["signed_closed_cycle_work_J_m3"] == pytest.approx(expected_work, rel=2e-12, abs=1e-8)
    assert values["signed_recorded_path_work_J_m3"] == pytest.approx(expected_work, rel=2e-12, abs=1e-8)
    assert values["endpoints_within_declared_tolerances"] is True
    assert record["cumulative_damage"] is None
    assert record["remaining_service_life_s"] is None
