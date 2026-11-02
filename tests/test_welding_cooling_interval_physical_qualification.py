"""Independent piecewise-linear temperature crossing reference cases."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.cooling_interval"]


def evaluate(time, temperature, upper=1100.0, lower=800.0, interval_limit=10.0):
    parameters = dict(BASE.parameters)
    parameters.update(
        time_s=list(time),
        temperature_K=list(temperature),
        upper_temperature_K=upper,
        lower_temperature_K=lower,
    )
    request = BASE.model_copy(
        update={"parameters": parameters, "maximum_sample_interval_s": interval_limit}
    )
    return evaluate_welding(request, CONTEXT)


def test_linear_cooling_crossings_match_exact_interpolation():
    result = evaluate((0.0, 10.0, 20.0), (1200.0, 900.0, 600.0), upper=1000.0, lower=800.0)
    assert result["status"] == "computed"
    assert result["values"]["upper_crossing_s"] == pytest.approx(20.0 / 3.0)
    assert result["values"]["lower_crossing_s"] == pytest.approx(40.0 / 3.0)
    assert result["values"]["cooling_interval_s"] == pytest.approx(20.0 / 3.0)


def test_piecewise_cooling_crossings_use_their_own_brackets():
    result = evaluate((0.0, 5.0, 15.0, 20.0), (1200.0, 1050.0, 900.0, 700.0), upper=1000.0, lower=800.0)
    assert result["status"] == "computed"
    assert result["values"]["upper_crossing_s"] == pytest.approx(5.0 + 50.0 / 150.0 * 10.0)
    assert result["values"]["lower_crossing_s"] == pytest.approx(15.0 + 100.0 / 200.0 * 5.0)
    assert result["values"]["upper_crossing_bracket_sample_indices"] == [1, 2]
    assert result["values"]["lower_crossing_bracket_sample_indices"] == [2, 3]


def test_time_unit_scaling_scales_crossings_and_interval():
    base = evaluate((0.0, 10.0, 20.0), (1200.0, 1000.0, 700.0), upper=1100.0, lower=800.0)
    scaled = evaluate((0.0, 40.0, 80.0), (1200.0, 1000.0, 700.0), upper=1100.0, lower=800.0, interval_limit=40.0)
    for key in ("upper_crossing_s", "lower_crossing_s", "cooling_interval_s", "maximum_selected_sample_spacing_s"):
        assert scaled["values"][key] == pytest.approx(4.0 * base["values"][key])


def test_crossing_exactly_at_a_recorded_sample_is_preserved():
    result = evaluate((0.0, 7.0, 17.0), (1200.0, 1000.0, 700.0), upper=1000.0, lower=800.0)
    assert result["status"] == "computed"
    assert result["values"]["upper_crossing_s"] == pytest.approx(7.0)
    assert result["values"]["lower_crossing_s"] == pytest.approx(7.0 + 200.0 / 300.0 * 10.0)


def test_target_outside_recorded_branch_is_not_extrapolated():
    result = evaluate((0.0, 10.0, 20.0), (1200.0, 1000.0, 900.0), upper=1100.0, lower=800.0)
    assert result["status"] == "needs_data"
    assert result["values"]["upper_crossing_available"] is True
    assert result["values"]["lower_crossing_available"] is False


def test_increasing_temperature_path_is_withheld_instead_of_auto_reordered():
    result = evaluate((0.0, 10.0, 20.0), (700.0, 1000.0, 1200.0), upper=1100.0, lower=800.0)
    assert result["status"] == "out_of_scope"
