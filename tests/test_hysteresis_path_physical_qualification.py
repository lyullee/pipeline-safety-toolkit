"""Independent loop-work and closure checks through the cyclic material bridge."""

import runpy

import pytest

from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue


REQUESTS, CONTEXT = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
BASE = REQUESTS["fatigue.hysteresis_path"]


def evaluate(stress, strain, *, boundary=True, linear_closure=False, stress_tol=0.0, strain_tol=0.0):
    n = len(stress)
    request = BASE.model_copy(
        update={
            "parameters": BASE.parameters
            | {
                "stress_Pa": list(stress),
                "strain_fraction": list(strain),
                "cycle_boundary_declared": boundary,
                "linear_closure_declared": linear_closure,
                "maximum_endpoint_stress_difference_Pa": stress_tol,
                "maximum_endpoint_strain_difference": strain_tol,
            },
            "path_sample_ids": tuple(f"hysteresis-{i}" for i in range(n)),
            "sample_time_s": tuple(float(i) for i in range(n)),
            "observation_period_s": (0.0, float(n - 1)),
        }
    )
    return evaluate_cyclic_fatigue(request, CONTEXT)


@pytest.mark.parametrize(
    "stress,strain,expected",
    [
        ([0.0, 100e6, 100e6, 0.0, 0.0], [0.0, 0.0, 0.002, 0.002, 0.0], 200_000.0),
        ([0.0, 0.0, 100e6, 100e6, 0.0], [0.0, 0.002, 0.002, 0.0, 0.0], -200_000.0),
        ([10e6, 100e6, 120e6, 80e6, 10e6], [0.0, 0.001, 0.003, 0.002, 0.0], 85_000.0),
    ],
)
def test_signed_closed_loop_integral_matches_trapezoid_and_polygon_reference(stress, strain, expected):
    result = evaluate(stress, strain)
    assert result["status"] == "computed"
    values = result["values"]
    assert values["signed_recorded_path_work_J_m3"] == pytest.approx(expected, abs=1e-8)
    assert values["signed_closed_cycle_work_J_m3"] == pytest.approx(expected, abs=1e-8)
    assert values["endpoints_within_declared_tolerances"] is True
    assert result["cumulative_damage"] is None
    assert result["failure_probability"] is None


def test_linear_closing_segment_uses_endpoint_average_stress():
    stress = [10e6, 30e6, 20e6]
    strain = [0.0, 0.001, 0.002]
    result = evaluate(stress, strain, linear_closure=True, stress_tol=10e6, strain_tol=0.002)
    values = result["values"]
    recorded = (10e6 + 30e6) / 2 * 0.001 + (30e6 + 20e6) / 2 * 0.001
    closing = (20e6 + 10e6) / 2 * (0.0 - 0.002)
    assert values["signed_recorded_path_work_J_m3"] == pytest.approx(recorded, abs=1e-8)
    assert values["signed_closed_cycle_work_J_m3"] == pytest.approx(recorded + closing, abs=1e-8)


@pytest.mark.parametrize(
    "boundary,linear,stress_tol,strain_tol,expected_closed",
    [
        (False, True, 20e6, 0.002, None),
        (True, False, 20e6, 0.002, None),
        (True, True, 1e6, 0.002, None),
        (True, True, 20e6, 0.0005, None),
    ],
)
def test_incomplete_or_out_of_tolerance_paths_never_claim_closed_cycle_work(
    boundary, linear, stress_tol, strain_tol, expected_closed
):
    result = evaluate(
        [10e6, 30e6, 20e6],
        [0.0, 0.001, 0.002],
        boundary=boundary,
        linear_closure=linear,
        stress_tol=stress_tol,
        strain_tol=strain_tol,
    )
    assert result["values"]["signed_closed_cycle_work_J_m3"] is expected_closed


def test_closed_loop_work_is_invariant_to_stress_offset_and_path_refinement():
    coarse = evaluate([0.0, 80e6, 80e6, 0.0], [0.0, 0.0, 0.003, 0.0])
    refined = evaluate(
        [15e6, 15e6, 95e6, 95e6, 55e6, 15e6],
        [0.004, 0.004, 0.004, 0.007, 0.0055, 0.004],
    )
    expected = 0.5 * 80e6 * 0.003
    assert coarse["values"]["signed_closed_cycle_work_J_m3"] == pytest.approx(expected, abs=1e-8)
    assert refined["values"]["signed_closed_cycle_work_J_m3"] == pytest.approx(expected, abs=1e-8)


def test_zero_loop_has_zero_work_even_when_stress_and_strain_are_nonzero():
    result = evaluate([30e6, 30e6, 30e6, 30e6], [0.002, 0.002, 0.002, 0.002])
    assert result["values"]["signed_recorded_path_work_J_m3"] == pytest.approx(0.0, abs=1e-12)
    assert result["values"]["signed_closed_cycle_work_J_m3"] == pytest.approx(0.0, abs=1e-12)
