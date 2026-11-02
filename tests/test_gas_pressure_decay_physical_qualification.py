"""Bounded rigid-vessel gas inventory balance through the pressure-test adapter."""

import math
import runpy

import pytest

from pipeline_integrity.pressure_tests import evaluate_pressure_test


CASES, _, _, CONTEXT = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
BASE = CASES["inspection.gas_pressure_decay"]
R = 8.31446261815324


def evaluate(**updates):
    parameters = dict(BASE.parameters)
    parameters.update(updates)
    return evaluate_pressure_test(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


def reference(values):
    n_initial = (
        values["initial_absolute_pressure_Pa"]
        * values["rigid_volume_m3"]
        / (values["initial_compressibility_factor"] * R * values["initial_temperature_K"])
    )
    n_final = (
        values["final_absolute_pressure_Pa"]
        * values["rigid_volume_m3"]
        / (values["final_compressibility_factor"] * R * values["final_temperature_K"])
    )
    rate = (n_initial - n_final) / values["duration_s"]
    reference_volume_rate = (
        rate
        * values["reference_compressibility_factor"]
        * R
        * values["reference_temperature_K"]
        / values["reference_pressure_Pa"]
    )
    return rate, reference_volume_rate


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"initial_absolute_pressure_Pa": 220000.0, "final_absolute_pressure_Pa": 180000.0,
         "initial_temperature_K": 310.0, "final_temperature_K": 295.0,
         "initial_compressibility_factor": 0.98, "final_compressibility_factor": 1.02},
        {"rigid_volume_m3": 0.35, "duration_s": 120.0,
         "reference_pressure_Pa": 125000.0, "reference_temperature_K": 285.0,
         "reference_compressibility_factor": 0.97},
    ],
)
def test_endpoint_molar_and_reference_volume_rates_match_ideal_inventory_balance(updates):
    params = dict(BASE.parameters) | updates
    result = evaluate(**updates)
    expected_molar, expected_reference = reference(params)
    assert result["status"] == "computed"
    assert result["values"]["net_molar_outflow_mol_s"] == pytest.approx(expected_molar, rel=2e-13)
    assert result["values"]["reference_volume_outflow_m3_s"] == pytest.approx(
        expected_reference, rel=2e-13
    )
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "inspection.gas_pressure_decay"


def test_endpoint_molar_flow_scales_linearly_with_rigid_volume():
    base = evaluate()
    doubled = evaluate(rigid_volume_m3=0.2)
    assert doubled["values"]["net_molar_outflow_mol_s"] == pytest.approx(
        2.0 * base["values"]["net_molar_outflow_mol_s"], rel=2e-13
    )
    assert doubled["values"]["reference_volume_outflow_m3_s"] == pytest.approx(
        2.0 * base["values"]["reference_volume_outflow_m3_s"], rel=2e-13
    )


def test_equal_endpoint_inventory_gives_zero_net_outflow():
    # Equal P/(Z*T) means equal moles at fixed volume, even with changed states.
    result = evaluate(
        initial_absolute_pressure_Pa=100000.0,
        initial_temperature_K=300.0,
        initial_compressibility_factor=1.0,
        final_absolute_pressure_Pa=120000.0,
        final_temperature_K=300.0,
        final_compressibility_factor=1.2,
    )
    assert result["status"] == "computed"
    assert result["values"]["net_molar_outflow_mol_s"] == pytest.approx(0.0, abs=1e-16)
    assert result["values"]["reference_volume_outflow_m3_s"] == pytest.approx(0.0, abs=1e-16)


def test_inventory_gain_is_reported_as_negative_net_outflow():
    params = dict(BASE.parameters)
    params.update(final_absolute_pressure_Pa=110000.0)
    result = evaluate(final_absolute_pressure_Pa=110000.0)
    expected, _ = reference(params)
    assert result["status"] == "computed"
    assert expected < 0.0
    assert result["values"]["net_molar_outflow_mol_s"] == pytest.approx(expected, rel=2e-13)
    assert result["values"]["net_molar_outflow_mol_s"] < 0.0


def test_reference_volume_conversion_uses_reference_state_not_endpoint_state():
    result = evaluate(
        reference_pressure_Pa=200000.0,
        reference_temperature_K=360.0,
        reference_compressibility_factor=0.9,
    )
    expected_molar, expected_volume = reference(dict(BASE.parameters) | {
        "reference_pressure_Pa": 200000.0,
        "reference_temperature_K": 360.0,
        "reference_compressibility_factor": 0.9,
    })
    assert result["values"]["net_molar_outflow_mol_s"] == pytest.approx(expected_molar, rel=2e-13)
    assert result["values"]["reference_volume_outflow_m3_s"] == pytest.approx(expected_volume, rel=2e-13)
    assert not math.isclose(expected_volume, expected_molar * R * 300.0 / 100000.0, rel_tol=1e-3)
