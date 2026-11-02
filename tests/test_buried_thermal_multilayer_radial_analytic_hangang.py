"""Independent steady radial-resistance checks for a multilayer buried pipe."""

import math
import runpy

import pytest

from pipeline_integrity.buried_thermal import ThermalPipeLayer, evaluate_buried_thermal


def _case(length_m=10.0):
    requests, context = runpy.run_path("examples/buried_thermal.py")["make_cases"]()
    base = requests["uniform"]
    request = base.model_copy(
        update={
            "length_m": length_m,
            "layers": (
                ThermalPipeLayer(
                    id="inner_shell",
                    material_condition_id="constant_k_inner",
                    outer_radius_m=0.12,
                    conductivity_W_m_K=2.4,
                    upstream_contact_resistance_K_m_W=0.3,
                    temperature_support_K=(250.0, 400.0),
                    evidence_id="authored",
                ),
                ThermalPipeLayer(
                    id="outer_jacket",
                    material_condition_id="constant_k_outer",
                    outer_radius_m=0.15,
                    conductivity_W_m_K=0.8,
                    upstream_contact_resistance_K_m_W=0.7,
                    temperature_support_K=(250.0, 400.0),
                    evidence_id="authored",
                ),
            ),
        }
    )
    return request, context


def test_two_layer_cylinder_matches_log_resistance_and_temperature_drop_oracle():
    request, context = _case()
    record = evaluate_buried_thermal(request, context)
    assert record["status"] == "computed"
    values = record["values"]

    expected_layers = (
        (0.10, 0.12, 2.4, 0.3 / request.length_m),
        (0.12, 0.15, 0.8, 0.7 / request.length_m),
    )
    expected_radial = tuple(
        math.log(outer / inner) / (2 * math.pi * conductivity * request.length_m)
        for inner, outer, conductivity, _ in expected_layers
    )
    layer_rows = values["layer_results"]
    assert [row["layer_id"] for row in layer_rows] == ["inner_shell", "outer_jacket"]
    for row, (_, _, _, contact), radial in zip(layer_rows, expected_layers, expected_radial):
        assert row["contact_resistance_K_W"] == pytest.approx(contact, rel=1e-12, abs=1e-14)
        assert row["conduction_resistance_K_W"] == pytest.approx(radial, rel=1e-12, abs=1e-14)

    independently_summed = math.fsum(
        [
            values["inner_film_resistance_K_W"],
            values["soil_contact_resistance_K_W"],
            values["soil_resistance_K_W"],
            *[contact + radial for (_, _, _, contact), radial in zip(expected_layers, expected_radial)],
        ]
    )
    assert values["total_resistance_K_W"] == pytest.approx(independently_summed, rel=1e-12)
    heat = (request.fluid_inlet_temperature_K - request.surface_plane_temperature_K) / independently_summed
    assert values["heat_from_fluid_to_soil_W"] == pytest.approx(heat, rel=1e-12)

    previous_temperature = request.fluid_inlet_temperature_K - heat * values["inner_film_resistance_K_W"]
    for row, (_, _, _, contact), radial in zip(layer_rows, expected_layers, expected_radial):
        assert row["upstream_temperature_K"] == pytest.approx(previous_temperature, abs=1e-10)
        previous_temperature -= heat * contact
        assert row["inner_temperature_K"] == pytest.approx(previous_temperature, abs=1e-10)
        previous_temperature -= heat * radial
        assert row["outer_temperature_K"] == pytest.approx(previous_temperature, abs=1e-10)
    previous_temperature -= heat * values["soil_contact_resistance_K_W"]
    assert values["soil_inner_temperature_K"] == pytest.approx(previous_temperature, abs=1e-10)
    assert previous_temperature - heat * values["soil_resistance_K_W"] == pytest.approx(
        request.surface_plane_temperature_K, abs=1e-10
    )
    assert abs(values["temperature_drop_residual_K"]) < 1e-10


def test_multilayer_resistance_and_heat_scale_with_pipe_length():
    short_request, context = _case(10.0)
    long_request, _ = _case(20.0)
    short = evaluate_buried_thermal(short_request, context)["values"]
    long = evaluate_buried_thermal(long_request, context)["values"]
    assert long["total_resistance_K_W"] == pytest.approx(short["total_resistance_K_W"] / 2, rel=1e-12)
    assert long["heat_from_fluid_to_soil_W"] == pytest.approx(2 * short["heat_from_fluid_to_soil_W"], rel=1e-12)
    assert long["heat_per_length_W_m"] == pytest.approx(short["heat_per_length_W_m"], rel=1e-12)
    for short_layer, long_layer in zip(short["layer_results"], long["layer_results"]):
        assert long_layer["conduction_resistance_K_W"] == pytest.approx(
            short_layer["conduction_resistance_K_W"] / 2, rel=1e-12
        )
        assert long_layer["contact_resistance_K_W"] == pytest.approx(
            short_layer["contact_resistance_K_W"] / 2, rel=1e-12
        )
