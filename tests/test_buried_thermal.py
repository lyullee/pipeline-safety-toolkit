"""Temperature support, signed steady energy and source-bound library results."""

import math
import runpy

import pytest

from pipeline_integrity.buried_thermal import (
    BuriedThermalRequest,
    evaluate_buried_thermal,
    verify_buried_thermal_record,
)
from pipeline_integrity.contracts import digest


def cases():
    return runpy.run_path("examples/buried_thermal.py")["make_cases"]()


@pytest.mark.parametrize("name", ["uniform", "flow"])
@pytest.mark.parametrize(
    "update",
    [
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"soil_conductivity_W_m_K": None},
        {"soil_contact_resistance_K_m_W": None},
        {"inner_film_coefficient_W_m2_K": None},
        {"soil_temperature_support_K": None},
    ],
)
def test_unknown_properties_and_reviews_are_withheld_before_optional_library(name, update, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(module, *args, **kwargs):
        if module == "ht" or module.startswith("ht."):
            raise AssertionError("unreviewed soil model imported its optional library")
        return original(module, *args, **kwargs)

    requests, context = cases()
    monkeypatch.setattr(builtins, "__import__", guarded)
    result = evaluate_buried_thermal(requests[name].model_copy(update=update), context)
    assert result["status"] == "needs_data" and result["values"] == {} and result["calculation"] is None
    verify_buried_thermal_record(result)


def test_authored_equal_resistances_and_declared_contacts_have_explicit_meanings():
    requests, context = cases()
    result = evaluate_buried_thermal(requests["uniform"], context)
    values = result["values"]
    assert result["status"] == "computed"
    assert values["total_resistance_K_W"] == pytest.approx(3 / (20 * math.pi))
    assert values["heat_from_fluid_to_soil_W"] == pytest.approx(400 * math.pi / 3)
    assert values["layer_results"][0]["inner_temperature_K"] == pytest.approx(320 - 20 / 3)
    assert values["layer_results"][0]["outer_temperature_K"] == pytest.approx(320 - 40 / 3)
    assert values["fluid_outlet_temperature_K"] is None and values["fluid_energy_balance_residual_W"] is None
    verify_buried_thermal_record(result)


def test_coupled_flow_reuses_single_phase_model_and_preserves_steady_energy():
    requests, context = cases()
    result = evaluate_buried_thermal(requests["flow"], context)
    values = result["values"]
    expected = 300 + 20 * math.exp(-math.pi / 15)
    assert values["fluid_outlet_temperature_K"] == pytest.approx(expected)
    assert values["heat_from_fluid_to_soil_W"] == pytest.approx(100 * (320 - expected))
    assert abs(values["fluid_energy_balance_residual_W"]) < 1e-10
    assert abs(values["temperature_drop_residual_K"]) < 1e-10
    assert values["fluid_outlet_temperature_K"] < values["fluid_mean_temperature_K"] < 320
    assert not result["transient_soil_or_moisture_coupling_modelled"]
    verify_buried_thermal_record(result)


@pytest.mark.parametrize("mode", ["uniform", "flow"])
def test_heat_gain_is_negative_and_is_not_clipped(mode):
    requests, context = cases()
    result = evaluate_buried_thermal(
        requests[mode].model_copy(update={"fluid_inlet_temperature_K": 280.0}), context
    )
    assert result["status"] == "computed" and result["values"]["heat_from_fluid_to_soil_W"] < 0
    if mode == "flow":
        assert 280 < result["values"]["fluid_outlet_temperature_K"] < 300
    verify_buried_thermal_record(result)


@pytest.mark.parametrize("update", [{"depth_to_pipe_center_m": 0.2}, {"length_m": 1.0}])
def test_intersecting_surface_and_unsupported_slenderness_are_out_of_scope(update):
    requests, context = cases()
    result = evaluate_buried_thermal(requests["uniform"].model_copy(update=update), context)
    assert result["status"] == "out_of_scope" and result["calculation"] is None
    verify_buried_thermal_record(result)


def test_zero_advection_is_retained_as_zero_without_using_a_stagnation_formula():
    requests, context = cases()
    result = evaluate_buried_thermal(requests["flow"].model_copy(update={"mass_flow_kg_s": 0.0}), context)
    assert result["status"] == "out_of_scope" and result["request"]["mass_flow_kg_s"] == 0
    assert result["values"] == {}


@pytest.mark.parametrize(
    "update",
    [
        {"soil_temperature_support_K": (250.0, 300.5)},
        {"fluid_temperature_support_K": (250.0, 310.0)},
        {"maximum_supported_axial_NTU": 0.1},
    ],
)
def test_solved_temperature_or_axial_support_violations_retain_diagnostics(update):
    requests, context = cases()
    result = evaluate_buried_thermal(requests["flow"].model_copy(update=update), context)
    assert result["status"] == "out_of_scope" and result["calculation"] is not None
    assert result["values"]["heat_from_fluid_to_soil_W"] > 0
    verify_buried_thermal_record(result)


def test_layer_missing_data_and_thermal_support_are_not_default_material_properties():
    requests, context = cases()
    request = requests["uniform"]
    for key in ("conductivity_W_m_K", "upstream_contact_resistance_K_m_W", "temperature_support_K"):
        layer = request.layers[0].model_copy(update={key: None})
        result = evaluate_buried_thermal(request.model_copy(update={"layers": (layer,)}), context)
        assert result["status"] == "needs_data"
    layer = request.layers[0].model_copy(update={"temperature_support_K": (250.0, 300.0)})
    assert (
        evaluate_buried_thermal(request.model_copy(update={"layers": (layer,)}), context)["status"]
        == "out_of_scope"
    )


def test_contact_resistance_per_length_is_divided_by_length_once():
    requests, context = cases()
    request = requests["uniform"]
    contact = 10 / (20 * math.pi)
    result = evaluate_buried_thermal(
        request.model_copy(update={"soil_contact_resistance_K_m_W": contact}), context
    )
    assert result["values"]["soil_contact_resistance_K_W"] == pytest.approx(1 / (20 * math.pi))
    assert result["values"]["total_resistance_K_W"] == pytest.approx(4 / (20 * math.pi))
    assert result["values"]["heat_from_fluid_to_soil_W"] == pytest.approx(100 * math.pi)


@pytest.mark.parametrize("which", ["warm_layer", "warm_soil", "cold_layer"])
def test_axial_endpoints_cannot_escape_property_support_behind_a_valid_mean(which):
    requests, context = cases()
    request = requests["flow"]
    if which == "warm_soil":
        request = request.model_copy(update={"soil_temperature_support_K": (250.0, 306.2)})
    else:
        bounds = (250.0, 313.0) if which == "warm_layer" else (287.0, 400.0)
        layer = request.layers[0].model_copy(update={"temperature_support_K": bounds})
        changes = {"layers": (layer,)}
        if which == "cold_layer":
            changes["fluid_inlet_temperature_K"] = 280.0
        request = request.model_copy(update=changes)
    result = evaluate_buried_thermal(request, context)
    assert result["status"] == "out_of_scope"
    values = result["values"]
    assert [p["endpoint"] for p in values["axial_endpoint_temperatures"]] == ["inlet", "outlet"]
    if which == "warm_soil":
        assert (
            values["soil_inner_temperature_K"]
            < 306.2
            < values["axial_endpoint_temperatures"][0]["soil_inner_temperature_K"]
        )
    elif which == "warm_layer":
        assert (
            values["layer_results"][0]["inner_temperature_K"]
            < 313
            < values["axial_endpoint_temperatures"][0]["layer_results"][0]["inner_temperature_K"]
        )
    else:
        assert (
            values["axial_endpoint_temperatures"][0]["layer_results"][0]["inner_temperature_K"]
            < 287
            < values["layer_results"][0]["inner_temperature_K"]
        )
    verify_buried_thermal_record(result)


@pytest.mark.parametrize(
    "update",
    [
        {"asset_id": "other"},
        {"clock_id": "other"},
        {"evidence_ids": ("missing",)},
        {"input_units": {}},
        {"expected_ht_version": "unreviewed-version"},
    ],
)
def test_context_evidence_units_and_library_pin_are_explicit(update):
    requests, context = cases()
    with pytest.raises(ValueError):
        evaluate_buried_thermal(requests["uniform"].model_copy(update=update), context)


def test_invalid_geometry_and_nonfinite_bypassed_copy_are_not_filled():
    requests, context = cases()
    request = requests["uniform"]
    for update in (
        {"inner_radius_m": 0.2},
        {"soil_conductivity_W_m_K": float("nan")},
        {"surface_plane_temperature_K": float("inf")},
        {"soil_temperature_support_K": (400.0, 300.0)},
    ):
        with pytest.raises(ValueError):
            evaluate_buried_thermal(request.model_copy(update=update), context)
    with pytest.raises(ValueError):
        BuriedThermalRequest.model_validate(
            request.model_dump() | {"layers": (request.layers[0], request.layers[0])}
        )


@pytest.mark.parametrize("field", ["version", "shape_function", "shape_implementation_sha256", "adapter"])
def test_resealing_cannot_turn_an_unreviewed_backend_into_the_recorded_implementation(field):
    requests, context = cases()
    record = evaluate_buried_thermal(requests["uniform"], context)
    if field == "adapter":
        record["implementation_sha256"] = "a" * 64
    else:
        record["calculation"]["backend"][field] = "9.9.9" if field == "version" else "a" * 64
        record["calculation"]["calculation_digest"] = digest(
            {k: v for k, v in record["calculation"].items() if k != "calculation_digest"}
        )
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="pin|implementation"):
        verify_buried_thermal_record(record)


@pytest.mark.parametrize("mutation", ["source", "context", "projection", "qualification", "withheld"])
def test_record_projections_cannot_be_relabelled_by_only_resealing_outer_digest(mutation):
    requests, context = cases()
    request = requests["uniform"]
    if mutation == "withheld":
        record = evaluate_buried_thermal(request.model_copy(update={"applicability_reviews": {}}), context)
        record["values"] = {"heat_from_fluid_to_soil_W": 0.0}
    else:
        record = evaluate_buried_thermal(request, context)
        if mutation == "source":
            record["request"]["length_m"] = 20.0
        elif mutation == "context":
            record["context"]["scenario_id"] = "changed"
        elif mutation == "projection":
            record["values"]["heat_from_fluid_to_soil_W"] = 999.0
        else:
            record["field_or_standard_qualification"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_buried_thermal_record(record)
