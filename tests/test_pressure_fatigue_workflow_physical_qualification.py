"""Independent end-to-end checks for the bounded pressure-to-fatigue workflow."""

import runpy

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.fatigue_exchange import assess_pressure_fatigue


def case():
    return runpy.run_path("examples/pressure_fatigue.py")["make_case"]()


def replace_gauge_pressures(request, values):
    history = request.history
    values = tuple(values)
    replacement = history.model_copy(
        update={
            "pressure_pa": values,
            "source_sha256": digest({"time_s": history.time_s, "gauge_pressure_pa": values}),
            "binding_digest": None,
        }
    )
    return request.model_copy(update={"history": replacement})


def test_hand_calculated_pressure_to_hoop_range_basquin_life_and_miner_damage():
    request, context = case()
    result = assess_pressure_fatigue(request, context)

    # Δp=2 MPa and r/t=0.1/0.005=20, so the thin-wall hoop range is 40 MPa.
    expected_range = (4.0e6 - 2.0e6) * (0.1 / 0.005)
    values = result.values
    assert result.status == "computed"
    assert len(values["cycles"]) == 4
    assert [row["stress_range_Pa"] for row in values["cycles"]] == [expected_range] * 4
    assert [row["count"] for row in values["cycles"]] == [0.5] * 4
    assert [row["stress_ratio"] for row in values["cycles"]] == pytest.approx([0.5] * 4)
    assert values["counted_cycles"] == pytest.approx(2.0)

    # Reference range equals the operating range; every half-cycle life is 1000.
    expected_life = 1000.0 * (40.0e6 / expected_range) ** 3.0
    expected_increment = sum(row["count"] / expected_life for row in values["cycles"])
    assert [row["cycles_to_failure"] for row in values["cycles"]] == pytest.approx([1000.0] * 4)
    assert values["damage_increment"] == pytest.approx(expected_increment)
    assert values["total_damage"] == pytest.approx(0.1 + expected_increment)
    assert values["damage_margin"] == pytest.approx(1.0 - values["total_damage"])
    assert result.failure_probability_calculated is False


def test_halving_pressure_range_changes_basquin_life_by_cubed_factor():
    request, context = case()
    request = replace_gauge_pressures(request, [2e6, 3e6, 2e6, 3e6, 2e6])
    result = assess_pressure_fatigue(request, context)
    assert result.status == "computed"
    values = result.values
    expected_range = (3.0e6 - 2.0e6) * 20.0
    expected_life = 1000.0 * (40.0e6 / expected_range) ** 3.0
    assert values["counted_cycles"] == pytest.approx(2.0)
    assert all(row["stress_range_Pa"] == pytest.approx(expected_range) for row in values["cycles"])
    assert all(row["cycles_to_failure"] == pytest.approx(expected_life) for row in values["cycles"])
    assert values["damage_increment"] == pytest.approx(2.0 / expected_life)


def test_equivalent_absolute_history_matches_gauge_history_after_ambient_conversion():
    request, context = case()
    gauge_result = assess_pressure_fatigue(request, context)
    history = request.history
    absolute = tuple(p + history.ambient_pressure_pa_abs for p in history.pressure_pa)
    replacement = history.model_copy(
        update={
            "pressure_pa": absolute,
            "pressure_reference": "absolute",
            "ambient_pressure_pa_abs": None,
            "source_sha256": digest({"time_s": history.time_s, "absolute_pressure_pa": absolute}),
            "binding_digest": None,
        }
    )
    absolute_result = assess_pressure_fatigue(request.model_copy(update={"history": replacement}), context)
    assert absolute_result.status == gauge_result.status == "computed"
    assert absolute_result.values["cycles"] == gauge_result.values["cycles"]
    assert absolute_result.values["damage_increment"] == pytest.approx(gauge_result.values["damage_increment"])


def test_initial_damage_is_preserved_and_added_without_repeating_residual_cycles():
    request, context = case()
    initial = 0.375
    result = assess_pressure_fatigue(request.model_copy(update={"initial_damage": initial}), context)
    assert result.values["initial_damage"] == initial
    assert result.values["damage_increment"] == pytest.approx(0.002)
    assert result.values["total_damage"] == pytest.approx(initial + result.values["damage_increment"])
    assert result.values["counted_cycles"] == pytest.approx(2.0)


def test_invalid_pressure_history_withholds_cycle_and_damage_calculation():
    request, context = case()
    valid = list(request.history.valid)
    valid[2] = False
    history = request.history.model_copy(update={"valid": tuple(valid), "binding_digest": None})
    result = assess_pressure_fatigue(request.model_copy(update={"history": history}), context)
    assert result.status == "needs_data"
    assert result.values == {}
