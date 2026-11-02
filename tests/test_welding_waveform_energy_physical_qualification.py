"""Focused waveform energy checks using a separate nodal-power trapezoid oracle."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_weld_waveform


_, BASE_FRAMES, BASE_SPEC, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()


def case(times, voltage, current, *, distance=0.025, efficiency=0.8, maximum_gap=10.0, **spec_updates):
    frames = tuple(
        frame.model_copy(
            update={
                "time_s": t,
                "values": {"U": u, "I": i},
                "units": {"U": "V", "I": "A"},
                "valid": {"U": True, "I": True},
            }
        )
        for frame, t, u, i in zip(BASE_FRAMES, times, voltage, current, strict=True)
    )
    spec = BASE_SPEC.model_copy(
        update={
            "travel_distance_m": distance,
            "efficiency": efficiency,
            "maximum_sample_interval_s": maximum_gap,
            **spec_updates,
        }
    )
    return frames, spec


def evaluate(*args, **kwargs):
    frames, spec = case(*args, **kwargs)
    return evaluate_weld_waveform(frames, spec, CONTEXT)


def test_irregular_sampled_waveform_matches_independent_nodal_power_trapezoid():
    times = [0.0, 2.0, 5.0]
    voltage = [10.0, 20.0, 30.0]
    current = [2.0, 4.0, 2.0]
    # Independently form power at the measured knots, then integrate each interval.
    power = [u * i for u, i in zip(voltage, current, strict=True)]
    expected_energy = sum(
        (times[i + 1] - times[i]) * (power[i + 1] + power[i]) / 2 for i in range(len(times) - 1)
    )
    result = evaluate(times, voltage, current, maximum_gap=3.0)
    assert result["status"] == "computed"
    values = result["native_result"]["values"]
    assert values["arc_energy_J"] == pytest.approx(expected_energy)
    assert values["net_heat_input_J_m"] == pytest.approx(0.8 * expected_energy / 0.025)
    assert result["native_result"]["native_receipt"]["native"]["payload"]["model_id"] == "welding.waveform_energy"


def test_time_scaling_changes_energy_linearly_but_not_mean_power():
    base = evaluate([0.0, 1.0, 3.0], [20.0, 20.0, 20.0], [5.0, 5.0, 5.0])
    doubled = evaluate([0.0, 2.0, 6.0], [20.0, 20.0, 20.0], [5.0, 5.0, 5.0])
    e0 = base["native_result"]["values"]["arc_energy_J"]
    e1 = doubled["native_result"]["values"]["arc_energy_J"]
    assert e0 == pytest.approx(20.0 * 5.0 * 3.0)
    assert e1 == pytest.approx(2.0 * e0)


def test_distance_and_efficiency_scale_net_heat_per_length_independently():
    base = evaluate([0.0, 1.0, 2.0], [10.0, 10.0, 10.0], [10.0, 10.0, 10.0], distance=0.02, efficiency=0.5)
    changed = evaluate([0.0, 1.0, 2.0], [10.0, 10.0, 10.0], [10.0, 10.0, 10.0], distance=0.04, efficiency=0.25)
    b = base["native_result"]["values"]["net_heat_input_J_m"]
    c = changed["native_result"]["values"]["net_heat_input_J_m"]
    assert base["native_result"]["values"]["arc_energy_J"] == pytest.approx(200.0)
    assert c == pytest.approx(0.25 * b)


def test_voltage_and_current_units_are_converted_before_energy_integration():
    frames, spec = case([0.0, 1.0, 2.0], [0.01, 0.02, 0.01], [0.005, 0.0025, 0.005])
    converted = tuple(
        frame.model_copy(
            update={
                "values": {"U": frame.values["U"], "I": frame.values["I"]},
                "units": {"U": "kV", "I": "kA"},
            }
        )
        for frame in frames
    )
    result = evaluate_weld_waveform(converted, spec, CONTEXT)
    expected = evaluate([0.0, 1.0, 2.0], [10.0, 20.0, 10.0], [5.0, 2.5, 5.0])
    assert result["status"] == "computed"
    assert result["native_result"]["values"]["arc_energy_J"] == pytest.approx(
        expected["native_result"]["values"]["arc_energy_J"]
    )


def test_unreviewed_synchronization_or_distance_efficiency_withholds_native_heat():
    result = evaluate(
        [0.0, 1.0, 2.0],
        [10.0, 20.0, 10.0],
        [5.0, 2.5, 5.0],
        electrical_synchronization_and_polarity_reviewed=False,
    )
    assert result["status"] == "needs_data"
    assert result["native_result"] is None
    assert result["electrical_diagnostic"] is None
    result = evaluate(
        [0.0, 1.0, 2.0],
        [10.0, 20.0, 10.0],
        [5.0, 2.5, 5.0],
        distance_efficiency_and_window_reviewed=False,
    )
    assert result["status"] == "needs_data"
    assert result["native_result"] is None
    assert result["electrical_diagnostic"]["values"]["import_energy_j"] == pytest.approx(100.0)


def test_uncovered_sample_interval_withholds_full_energy_result():
    result = evaluate([0.0, 1.5, 2.0], [10.0, 20.0, 10.0], [5.0, 2.5, 5.0], maximum_gap=1.0)
    assert result["status"] == "needs_data"
    assert result["native_result"] is None
    assert result["electrical_diagnostic"]["skipped_interval_indices"] == [0]


def test_negative_signed_power_is_retained_and_native_nonnegative_heat_is_withheld():
    result = evaluate([0.0, 1.0, 2.0], [10.0, -10.0, 10.0], [5.0, 5.0, 5.0])
    assert result["status"] == "out_of_scope"
    assert result["native_result"] is None
    assert result["electrical_diagnostic"]["values"]["import_energy_j"] == pytest.approx(25.0)
    assert result["electrical_diagnostic"]["values"]["export_energy_j"] == pytest.approx(25.0)
