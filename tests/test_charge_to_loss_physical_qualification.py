"""Bounded Faraday charge-to-dissolution balance through the typed adapter."""

import runpy

import pytest
from pydantic import ValidationError

from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical


CASES, CONTEXT = runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()
BASE = CASES["corrosion.charge_to_loss"]
FARADAY_C_PER_MOL = 96485.33212331001


def evaluate(charge, equivalent_weight, density, efficiency=1.0):
    request = BASE.model_copy(
        update={
            "parameters": {
                "anodic_charge_C": charge,
                "equivalent_weight_kg_mol_electron": equivalent_weight,
                "density_kg_m3": density,
                "current_efficiency": efficiency,
            }
        }
    )
    return evaluate_electrochemical(request, CONTEXT)


@pytest.mark.parametrize(
    "charge,equivalent_weight,density,efficiency",
    [
        (9.648533212331001, 0.0279225, 7850.0, 1.0),
        (100.0, 0.020, 8000.0, 0.75),
        (2.5e5, 0.055845, 7874.0, 0.90),
        (0.25, 0.010, 2700.0, 0.20),
    ],
)
def test_faraday_mass_and_volume_match_independent_balance(
    charge, equivalent_weight, density, efficiency
):
    result = evaluate(charge, equivalent_weight, density, efficiency)
    expected_mass = charge * equivalent_weight * efficiency / FARADAY_C_PER_MOL
    expected_volume = expected_mass / density
    assert result["status"] == "computed"
    assert result["values"]["dissolved_mass_kg"] == pytest.approx(expected_mass, rel=2e-14)
    assert result["values"]["dissolved_volume_m3"] == pytest.approx(expected_volume, rel=2e-14)
    assert result["output_units"] == {"dissolved_mass_kg": "kg", "dissolved_volume_m3": "m^3"}
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "corrosion.charge_to_loss"


def test_zero_anodic_charge_produces_zero_dissolved_mass_and_volume():
    result = evaluate(0.0, 0.0279225, 7850.0)
    assert result["status"] == "computed"
    assert result["values"]["dissolved_mass_kg"] == 0.0
    assert result["values"]["dissolved_volume_m3"] == 0.0


def test_charge_and_efficiency_are_linear_and_density_only_scales_volume():
    base = evaluate(100.0, 0.020, 8000.0, 0.5)["values"]
    doubled_charge = evaluate(200.0, 0.020, 8000.0, 0.5)["values"]
    doubled_efficiency = evaluate(100.0, 0.020, 8000.0, 1.0)["values"]
    half_density = evaluate(100.0, 0.020, 4000.0, 0.5)["values"]
    assert doubled_charge["dissolved_mass_kg"] == pytest.approx(2 * base["dissolved_mass_kg"])
    assert doubled_charge["dissolved_volume_m3"] == pytest.approx(2 * base["dissolved_volume_m3"])
    assert doubled_efficiency["dissolved_mass_kg"] == pytest.approx(2 * base["dissolved_mass_kg"])
    assert half_density["dissolved_mass_kg"] == pytest.approx(base["dissolved_mass_kg"])
    assert half_density["dissolved_volume_m3"] == pytest.approx(2 * base["dissolved_volume_m3"])


@pytest.mark.parametrize("efficiency", [0.0, -0.1, 1.0001])
def test_current_efficiency_outside_open_zero_to_closed_one_is_rejected(efficiency):
    with pytest.raises(ValidationError):
        evaluate(100.0, 0.02, 8000.0, efficiency)


def test_non_dissolution_current_meaning_is_withheld():
    request = BASE.model_copy(update={"current_meaning": "net_external_electrode_current"})
    result = evaluate_electrochemical(request, CONTEXT)
    assert result["status"] == "out_of_scope"
    assert result["values"] == {}
    assert result["native_receipt"] is None
