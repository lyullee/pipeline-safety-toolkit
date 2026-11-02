"""Rigid isothermal chamber pressure-volume throughput references."""

import runpy

import pytest

from pipeline_integrity.pressure_tests import evaluate_pressure_test


REQUESTS, _, _, CONTEXT = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
BASE = REQUESTS["inspection.vacuum_rise"]


def evaluate(volume=0.01, p0=1.0, p1=3.0, duration=100.0, background=0.0003, isothermal=True):
    parameters = dict(BASE.parameters)
    parameters.update(
        rigid_volume_m3=volume,
        initial_absolute_pressure_Pa=p0,
        final_absolute_pressure_Pa=p1,
        duration_s=duration,
        background_throughput_Pa_m3_s=background,
        constant_temperature_declared=isothermal,
    )
    return evaluate_pressure_test(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


@pytest.mark.parametrize(
    "volume,p0,p1,duration,background",
    [
        (0.01, 1.0, 3.0, 100.0, 0.0003),
        (0.2, 1.0e5, 1.1e5, 10.0, 100.0),
        (0.04, 5.0e4, 7.5e4, 20.0, 10.0),
    ],
)
def test_pressure_volume_throughput_matches_endpoint_inventory_balance(volume, p0, p1, duration, background):
    result = evaluate(volume, p0, p1, duration, background)
    total = volume * (p1 - p0) / duration
    assert result["status"] == "computed"
    assert result["values"]["total_gas_load_Pa_m3_s"] == pytest.approx(total)
    assert result["values"]["background_corrected_throughput_Pa_m3_s"] == pytest.approx(total - background)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "inspection.vacuum_rise"


def test_throughput_scales_with_volume_and_pressure_change_and_inverse_time():
    base = evaluate(volume=0.1, p0=1.0e5, p1=1.2e5, duration=10.0, background=0.0)
    scaled = evaluate(volume=0.3, p0=2.0e5, p1=2.6e5, duration=30.0, background=0.0)
    assert scaled["values"]["total_gas_load_Pa_m3_s"] == pytest.approx(3.0 * base["values"]["total_gas_load_Pa_m3_s"])


def test_zero_pressure_change_preserves_negative_background_corrected_result():
    result = evaluate(volume=0.5, p0=2.0e5, p1=2.0e5, duration=25.0, background=12.0)
    assert result["values"]["total_gas_load_Pa_m3_s"] == pytest.approx(0.0)
    assert result["values"]["background_corrected_throughput_Pa_m3_s"] == pytest.approx(-12.0)


def test_pressure_decrease_retains_negative_net_throughput():
    result = evaluate(volume=0.2, p0=1.0e5, p1=9.0e4, duration=5.0, background=0.0)
    assert result["values"]["total_gas_load_Pa_m3_s"] == pytest.approx(-400.0)


def test_nonisothermal_assumption_is_withheld():
    result = evaluate(isothermal=False)
    assert result["status"] == "out_of_scope"
    assert result["values"] == {}
