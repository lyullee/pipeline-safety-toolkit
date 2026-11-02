"""Piecewise-linear simultaneous temperature-band and rate references."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.heat_treatment_history"]


def evaluate(time, sensors, low=800.0, high=900.0, spacing=10.0):
    parameters = dict(BASE.parameters)
    parameters.update(
        time_s=list(time),
        sensor_temperature_K=[list(row) for row in sensors],
        minimum_hold_temperature_K=low,
        maximum_hold_temperature_K=high,
    )
    request = BASE.model_copy(
        update={"parameters": parameters, "maximum_sample_interval_s": spacing}
    )
    return evaluate_welding(request, CONTEXT)


def test_simultaneous_band_overlap_matches_independent_ramp_intersections():
    result = evaluate((0.0, 10.0, 20.0), ((700.0, 850.0, 1000.0), (750.0, 850.0, 950.0)))
    assert result["status"] == "computed"
    # Sensor 1 enters at 100/15 s and leaves at 200/15 s;
    # sensor 2 occupies the band from 2.5 s to 12.5 s.
    start, end = 100.0 / 15.0, 200.0 / 15.0
    intervals = result["values"]["simultaneous_hold_intervals_s"]
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx([start, end])
    assert result["values"]["total_simultaneous_hold_s"] == pytest.approx(end - start)
    assert result["values"]["longest_continuous_hold_s"] == pytest.approx(end - start)


def test_flat_temperatures_inside_band_hold_for_full_recorded_duration():
    result = evaluate((0.0, 4.0, 10.0), ((820.0, 820.0, 820.0), (850.0, 850.0, 850.0)))
    intervals = result["values"]["simultaneous_hold_intervals_s"]
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx([0.0, 10.0])
    assert result["values"]["total_simultaneous_hold_s"] == pytest.approx(10.0)


def test_sensor_band_crossings_with_no_shared_time_have_zero_simultaneous_hold():
    result = evaluate((0.0, 10.0, 20.0), ((700.0, 850.0, 1000.0), (900.0, 700.0, 700.0)))
    assert result["values"]["simultaneous_hold_intervals_s"] == []
    assert result["values"]["total_simultaneous_hold_s"] == pytest.approx(0.0)


def test_heating_cooling_rates_and_sensor_spread_match_finite_differences():
    result = evaluate((0.0, 5.0, 10.0), ((700.0, 800.0, 900.0), (710.0, 790.0, 850.0)))
    values = result["values"]
    assert values["maximum_heating_rate_K_s"] == pytest.approx(20.0)
    assert values["maximum_cooling_rate_K_s"] == pytest.approx(0.0)
    assert values["maximum_sensor_spread_K"] == pytest.approx(50.0)


def test_time_scaling_preserves_temperatures_and_scales_hold_and_rate():
    base = evaluate((0.0, 5.0, 10.0), ((700.0, 850.0, 1000.0), (750.0, 850.0, 950.0)))
    scaled = evaluate((0.0, 20.0, 40.0), ((700.0, 850.0, 1000.0), (750.0, 850.0, 950.0)), spacing=20.0)
    assert scaled["values"]["total_simultaneous_hold_s"] == pytest.approx(
        4.0 * base["values"]["total_simultaneous_hold_s"]
    )
    assert scaled["values"]["maximum_heating_rate_K_s"] == pytest.approx(
        base["values"]["maximum_heating_rate_K_s"] / 4.0
    )


def test_mismatched_sensor_history_is_rejected():
    with pytest.raises(ValueError, match="complete common-clock temperature vector"):
        evaluate((0.0, 5.0, 10.0), ((700.0, 850.0), (750.0, 850.0, 950.0)))
