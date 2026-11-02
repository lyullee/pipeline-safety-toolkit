"""Scope, complete histories, unknown chemistry and signed electrical meaning before native imports."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import (
    REVIEWS,
    evaluate_weld_thermal,
    evaluate_weld_waveform,
    evaluate_welding,
)


def base():
    return runpy.run_path("examples/welding_methods.py")["make_cases"]()


@pytest.mark.parametrize("method", list(REVIEWS))
def test_all_methods_require_review_without_importing_native_owner(method):
    cases, *_, context = base()
    assert (
        evaluate_welding(cases[method].model_copy(update={"applicability_reviews": {}}), context)["status"]
        == "needs_data"
    )


@pytest.mark.parametrize(
    "method,basis", [("welding.carbon_equivalents", "mass_fraction"), ("welding.dilution", "mass_percent")]
)
def test_chemistry_basis_is_never_guessed_or_silently_rescaled(method, basis):
    cases, *_, context = base()
    assert (
        evaluate_welding(cases[method].model_copy(update={"chemistry_basis": basis}), context)["status"]
        == "needs_data"
    )


def test_missing_assay_is_not_zero_and_missing_history_is_not_interpolated():
    cases, *_, context = base()
    r = cases["welding.carbon_equivalents"]
    assert (
        evaluate_welding(r.model_copy(update={"parameters": r.parameters | dict(B=None)}), context)["status"]
        == "needs_data"
    )
    r = cases["welding.waveform_energy"]
    assert (
        evaluate_welding(r.model_copy(update={"maximum_sample_interval_s": None}), context)["status"]
        == "needs_data"
    )
    assert (
        evaluate_welding(r.model_copy(update={"maximum_sample_interval_s": 0.5}), context)["status"]
        == "needs_data"
    )
    assert (
        evaluate_welding(
            r.model_copy(update={"parameters": r.parameters | dict(current_A=[30.0, None, 30.0])}), context
        )["status"]
        == "needs_data"
    )


def test_history_reordering_and_excess_samples_are_rejected():
    cases, *_, context = base()
    r = cases["welding.waveform_energy"]
    with pytest.raises(ValueError):
        evaluate_welding(
            r.model_copy(update={"parameters": r.parameters | dict(time_s=[0.0, 2.0, 1.0])}), context
        )
    with pytest.raises(ValueError, match="budget"):
        evaluate_welding(r.model_copy(update={"maximum_samples": 2}), context)


def test_warming_branch_cannot_be_renamed_as_cooling():
    cases, *_, context = base()
    r = cases["welding.cooling_interval"]
    assert (
        evaluate_welding(
            r.model_copy(update={"parameters": r.parameters | dict(temperature_K=[700.0, 1000.0, 1200.0])}),
            context,
        )["status"]
        == "out_of_scope"
    )


def test_asset_clock_and_evidence_are_strict():
    cases, *_, context = base()
    r = cases["welding.arc_energy"]
    with pytest.raises(ValueError, match="asset/clock"):
        evaluate_welding(r, context.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError, match="evidence"):
        evaluate_welding(r.model_copy(update={"evidence_ids": ("missing",)}), context)


def test_wave_polarity_and_unknown_efficiency_distance_withhold_heat_but_keep_electrical_integral():
    _, frames, spec, _, _, context = base()
    assert (
        evaluate_weld_waveform(
            frames,
            spec.model_copy(update={"electrical_synchronization_and_polarity_reviewed": False}),
            context,
        )["electrical_diagnostic"]
        is None
    )
    for changes in (
        {"travel_distance_m": None},
        {"efficiency": None},
        {"distance_efficiency_and_window_reviewed": False},
    ):
        result = evaluate_weld_waveform(frames, spec.model_copy(update=changes), context)
        assert result["status"] == "needs_data" and result["native_result"] is None
        assert result["electrical_diagnostic"]["values"]["import_energy_j"] == 600.0


def test_negative_power_keeps_import_export_and_is_not_clipped_or_made_positive():
    _, frames, spec, _, _, context = base()
    frames = tuple(
        f.model_copy(update={"values": dict(U=u, I=30.0)})
        for f, u in zip(frames, (10.0, -10.0, 10.0), strict=True)
    )
    result = evaluate_weld_waveform(frames, spec, context)
    assert result["status"] == "out_of_scope" and result["native_result"] is None
    assert result["electrical_diagnostic"]["values"]["import_energy_j"] == 150.0
    assert result["electrical_diagnostic"]["values"]["export_energy_j"] == 150.0


def test_gaps_and_invalid_samples_never_become_full_weld_heat():
    _, frames, spec, _, _, context = base()
    result = evaluate_weld_waveform((frames[0], frames[-1]), spec, context)
    assert result["status"] == "needs_data" and result["electrical_diagnostic"][
        "skipped_interval_indices"
    ] == [0]
    changed = frames[1].model_copy(update={"values": dict(U=None, I=10.0), "valid": dict(U=False, I=True)})
    assert evaluate_weld_waveform((frames[0], changed, frames[-1]), spec, context)["status"] == "needs_data"


def test_electrical_units_are_converted_per_sample_without_multiplying_mean_channels():
    _, frames, spec, _, _, context = base()
    frames = tuple(
        f.model_copy(
            update={
                "values": dict(U=f.values["U"] / 1000, I=f.values["I"] / 1000),
                "units": dict(U="kV", I="kA"),
            }
        )
        for f in frames
    )
    result = evaluate_weld_waveform(frames, spec.model_copy(update={"efficiency": None}), context)
    assert result["electrical_diagnostic"]["values"]["import_energy_j"] == 600.0


def test_thermal_review_missing_values_and_sensor_selection_are_preserved():
    _, _, _, frames, spec, context = base()
    assert (
        evaluate_weld_thermal(
            frames, spec.model_copy(update={"sensor_locations_temperature_and_rule_reviewed": False}), context
        )["status"]
        == "needs_data"
    )
    changed = frames[1].model_copy(update={"values": dict(T=None), "valid": dict(T=False)})
    assert evaluate_weld_thermal((frames[0], changed, frames[-1]), spec, context)["status"] == "needs_data"
    with pytest.raises(ValueError, match="distinct|one explicitly selected"):
        evaluate_weld_thermal(frames, spec.model_copy(update={"temperature_channels": ("T", "T")}), context)


@pytest.mark.parametrize(
    "changes", [{"efficiency": 0.0}, {"electrical_evidence_id": "unlisted"}, {"current_channel": "U"}]
)
def test_invalid_efficiency_or_ambiguous_electrical_channels_and_sources_are_errors(changes):
    _, frames, spec, _, _, context = base()
    with pytest.raises(ValueError):
        evaluate_weld_waveform(frames, spec.model_copy(update=changes), context)


def test_thermal_budget_is_checked_before_native_import():
    _, _, _, frames, spec, context = base()
    with pytest.raises(ValueError, match="budget"):
        evaluate_weld_thermal(frames, spec.model_copy(update={"maximum_samples": 2}), context)
