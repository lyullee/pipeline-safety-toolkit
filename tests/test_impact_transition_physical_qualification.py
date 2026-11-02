"""Bounded verification of the caller-declared impact energy transition fit."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


TEMPERATURES = [250.0 + 10.0 * i for i in range(11)]
LOWER, UPPER, MIDPOINT, WIDTH = 10.0, 100.0, 300.0, 12.0
ENERGIES = [LOWER + (UPPER - LOWER) / (1.0 + math.exp(-(t - MIDPOINT) / WIDTH)) for t in TEMPERATURES]
CONTEXT = RunContext(
    asset_id="synthetic_impact_specimen_series",
    scenario_id="logistic_transition_oracle",
    clock_id="temperature_order",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently generated synthetic reference series", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(temperatures=TEMPERATURES, energies=ENERGIES, target=55.0, **updates):
    values = {
        "series_id": "series-a",
        "specimen_geometry_id": "geometry-a",
        "material_condition_id": "condition-a",
        "orientation_id": "orientation-a",
        "observational_unit_description": "One independently authored synthetic specimen series",
        "observation_ids": [f"impact-{i}" for i in range(len(temperatures))],
        "temperature_K": list(temperatures),
        "absorbed_energy_J": list(energies),
        "target_energy_J": target,
        "minimum_relative_shelf_coverage": 0.9,
        "loss": "linear",
        "residual_scale_J": 1.0,
    }
    values.update(updates)
    return CLIENT.evaluate("materials.impact_transition", values, CONTEXT, location_id="charpy-series")


def test_exact_synthetic_logistic_series_recovers_known_parameters():
    result = evaluate()
    assert result.status == "computed"
    fit = result.payload["values"]
    assert fit["lower_shelf_energy_J"] == pytest.approx(LOWER, abs=1e-5)
    assert fit["upper_shelf_energy_J"] == pytest.approx(UPPER, abs=1e-5)
    assert fit["mid_shelf_temperature_K"] == pytest.approx(MIDPOINT, abs=1e-5)
    assert fit["logistic_transition_width_K"] == pytest.approx(WIDTH, abs=1e-5)
    assert fit["target_energy_crossing_temperature_K"] == pytest.approx(MIDPOINT, abs=1e-5)
    assert fit["residual_rmse_J"] < 1e-6


def test_temperature_translation_moves_transition_and_crossing_by_same_offset():
    result = evaluate([t + 25.0 for t in TEMPERATURES])
    fit = result.payload["values"]
    assert result.status == "computed"
    assert fit["mid_shelf_temperature_K"] == pytest.approx(MIDPOINT + 25.0, abs=1e-5)
    assert fit["target_energy_crossing_temperature_K"] == pytest.approx(MIDPOINT + 25.0, abs=1e-5)
    assert fit["logistic_transition_width_K"] == pytest.approx(WIDTH, abs=1e-5)


def test_energy_scaling_scales_shelves_and_preserves_temperature():
    result = evaluate(energies=[2.0 * e for e in ENERGIES], target=110.0)
    fit = result.payload["values"]
    assert result.status == "computed"
    assert fit["lower_shelf_energy_J"] == pytest.approx(2.0 * LOWER, abs=1e-5)
    assert fit["upper_shelf_energy_J"] == pytest.approx(2.0 * UPPER, abs=1e-5)
    assert fit["mid_shelf_temperature_K"] == pytest.approx(MIDPOINT, abs=1e-5)
    assert fit["target_energy_crossing_temperature_K"] == pytest.approx(MIDPOINT, abs=1e-5)


def test_target_outside_observed_energy_bracket_is_not_extrapolated():
    fit = evaluate(target=120.0).payload["values"]
    assert fit["target_energy_crossing_temperature_K"] is None
    assert fit["observed_temperature_domain_K"] == [min(TEMPERATURES), max(TEMPERATURES)]


def test_duplicate_observation_witnesses_and_short_series_are_rejected():
    with pytest.raises(ValidationError):
        evaluate(updates={"observation_ids": ["duplicate"] * len(TEMPERATURES)})
    with pytest.raises(ValidationError):
        evaluate(temperatures=TEMPERATURES[:-1])
