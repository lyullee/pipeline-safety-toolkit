"""Hangang candidate: heterogeneous segmented inventory balance and split invariance.

Synthetic ideal-gas property oracle; no external property data or CoolProp required.
"""
from types import SimpleNamespace

import pytest

from pipeline_integrity.models.thermal import SegmentedInventory, segmented_inventory

R = 287.0
CP = 1004.0
CV = CP - R


class IdealGasOracle:
    def resolve(self, request):
        pressure = float(request.inputs["P"])
        temperature = float(request.inputs["T"])
        return SimpleNamespace(
            density_kg_m3=pressure / (R * temperature),
            enthalpy_j_kg=CP * temperature,
            pressure_pa_abs=pressure,
            phase="gas",
        )


def _evaluate(segments):
    request = SegmentedInventory(
        fluid="synthetic-ideal-gas",
        segments=tuple(segments),
        reference_pressure_pa_abs=101325.0,
        reference_temperature_k=288.15,
    )
    return segmented_inventory(request, property_provider=IdealGasOracle()).values


def test_heterogeneous_segment_mass_energy_and_reference_volume_close():
    segments = [
        {"id": "s1", "pressure_pa_abs": 2.4e6, "temperature_k": 285.0, "internal_volume_m3": 1.7},
        {"id": "s2", "pressure_pa_abs": 1.3e6, "temperature_k": 315.0, "internal_volume_m3": 2.2},
        {"id": "s3", "pressure_pa_abs": 0.8e6, "temperature_k": 340.0, "internal_volume_m3": 0.9},
    ]
    values = _evaluate(segments)
    expected_masses = [s["pressure_pa_abs"] * s["internal_volume_m3"] / (R * s["temperature_k"]) for s in segments]
    expected_mass = sum(expected_masses)
    expected_energy = sum(m * CV * s["temperature_k"] for m, s in zip(expected_masses, segments))
    reference_density = 101325.0 / (R * 288.15)

    assert [row["mass_kg"] for row in values["segment_results"]] == pytest.approx(expected_masses, rel=2e-14)
    assert values["total_mass_kg"] == pytest.approx(expected_mass, rel=2e-14)
    assert values["total_internal_volume_m3"] == pytest.approx(sum(s["internal_volume_m3"] for s in segments))
    assert values["reference_volume_m3"] == pytest.approx(expected_mass / reference_density, rel=2e-14)
    assert values["total_internal_energy_j"] == pytest.approx(expected_energy, rel=2e-14)
    expected_h = sum(m * CP * s["temperature_k"] for m, s in zip(expected_masses, segments)) / expected_mass
    assert values["mass_weighted_enthalpy_j_kg"] == pytest.approx(expected_h, rel=2e-14)


def test_splitting_one_nonuniform_segment_preserves_inventory_extensives():
    original = [
        {"id": "hot", "pressure_pa_abs": 1.8e6, "temperature_k": 330.0, "internal_volume_m3": 3.6},
        {"id": "cold", "pressure_pa_abs": 0.9e6, "temperature_k": 290.0, "internal_volume_m3": 1.1},
    ]
    split = [original[0] | {"id": "hot-a", "internal_volume_m3": 1.4},
             original[0] | {"id": "hot-b", "internal_volume_m3": 2.2}, original[1]]
    a, b = _evaluate(original), _evaluate(split)
    for key in ("total_mass_kg", "total_internal_volume_m3", "reference_volume_m3", "total_internal_energy_j"):
        assert b[key] == pytest.approx(a[key], rel=2e-14)
    assert b["mass_weighted_enthalpy_j_kg"] == pytest.approx(a["mass_weighted_enthalpy_j_kg"], rel=2e-14)
