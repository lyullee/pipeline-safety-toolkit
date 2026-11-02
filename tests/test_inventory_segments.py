import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

CONTEXT = RunContext(
    asset_id="linepack_reference", scenario_id="segments", clock_id="elapsed", origin="synthetic"
)


def test_segment_partition_invariance_and_common_reference_mass():
    engine = create_registry()
    base = {
        "fluid": "Methane",
        "pressure_pa_abs": 5000000.0,
        "temperature_k": 290.0,
        "internal_volume_m3": 100.0,
        "reference_pressure_pa_abs": 101325.0,
        "reference_temperature_k": 288.15,
    }
    uniform = engine.evaluate("inventory.uniform", base, CONTEXT)
    result = engine.evaluate(
        "inventory.segmented",
        {
            "fluid": "Methane",
            "segments": (
                {"id": "a", "pressure_pa_abs": 5000000.0, "temperature_k": 290.0, "internal_volume_m3": 40.0},
                {"id": "b", "pressure_pa_abs": 5000000.0, "temperature_k": 290.0, "internal_volume_m3": 60.0},
            ),
            "reference_pressure_pa_abs": 101325.0,
            "reference_temperature_k": 288.15,
        },
        CONTEXT,
    )
    assert result.values["total_mass_kg"] == pytest.approx(uniform.values["mass_kg"])
    assert result.values["reference_volume_m3"] == pytest.approx(uniform.values["reference_volume_m3"])
    assert result.values["mass_weighted_enthalpy_j_kg"] == pytest.approx(
        uniform.values["specific_enthalpy_j_kg"]
    )
    expected = result.values["total_mass_kg"] * uniform.values["specific_enthalpy_j_kg"] - 5000000 * 100
    assert result.values["total_internal_energy_j"] == pytest.approx(expected)


def test_duplicate_spatial_identity_is_rejected():
    segment = {"id": "same", "pressure_pa_abs": 101325.0, "temperature_k": 300.0, "internal_volume_m3": 1.0}
    with pytest.raises(ValueError, match="unique"):
        create_registry().evaluate(
            "inventory.segmented",
            {
                "fluid": "Water",
                "segments": (segment, segment),
                "reference_pressure_pa_abs": 101325.0,
                "reference_temperature_k": 300.0,
            },
            CONTEXT,
        )
