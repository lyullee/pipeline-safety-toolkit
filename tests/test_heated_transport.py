"""NURION verification scenarios; numerical execution deliberately deferred locally."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import heated_transport as ht

_spec = importlib.util.spec_from_file_location(
    "heated_example", Path(__file__).parents[1] / "examples" / "heated_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def test_unreviewed_no_property_or_native_calls(monkeypatch):
    q, c = _example.make_case()
    monkeypatch.setattr(ht, "native_backend_metadata", lambda *a, **k: pytest.fail("unreviewed owner access"))
    r = ht.evaluate_heated_transport(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]
    assert ht.verify_heated_transport_record(r, request=q, context=c)["valid"]


def test_missing_heat_boundary_is_not_zero():
    q, c = _example.make_case()
    cells = (q.cells[0].model_copy(update={"heater_into_fluid_W": None}),)
    q, c = _example.make_case(cells=cells)
    r = ht.evaluate_heated_transport(q, c)
    assert any("missing:heater_into_fluid_W" in note for note in r["notes"])


def test_context_and_clone_finiteness():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        ht.evaluate_heated_transport(q, c.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError):
        ht.evaluate_heated_transport(q.model_copy(update={"mass_flow_kg_s": float("nan")}), c)


@pytest.mark.parametrize(
    "changes",
    [
        dict(inlet_port_id="out"),
        dict(mass_flow_kg_s=0.0),
        dict(pressure_support_pa_abs=(2000000.0, 100000.0)),
    ],
)
def test_invalid_domains(changes):
    with pytest.raises(ValidationError):
        _example.make_case(**changes)


def computed(**changes):
    q, c = _example.make_case(reviewed=True, **changes)
    r = ht.evaluate_heated_transport(q, c)
    assert r["status"] == "computed_diagnostic", r["notes"]
    return q, c, r


def test_native_water_heating_updates_viscosity_and_conserves_energy():
    q, c, r = computed()
    assert r["values"]["outlet"]["temperature_k"] > q.inlet.inputs["T"]
    assert r["values"]["final_viscosity_Pa_s"] < r["values"]["initial_viscosity_Pa_s"]
    for row in r["cells"]:
        assert row["mass_flux_residual_kg_s"] == 0
        assert abs(row["reduced_energy_residual_W"]) <= row["energy_balance_limit_W"]
        assert row["internal_energy_flux_change_W"] + row[
            "pressure_flow_work_flux_change_W"
        ] == pytest.approx(row["enthalpy_flux_change_W"], abs=1e-7)
    assert ht.verify_heated_transport_record(r, request=q, context=c)["valid"]


def test_upstream_heat_changes_downstream_hydraulic_owner_inputs():
    q, _ = _example.make_case()
    hot = tuple(cell.model_copy(update={"heater_into_fluid_W": 500.0}) for cell in q.cells)
    cold = tuple(
        cell.model_copy(update={"heater_into_fluid_W": 0.0, "conductance_W_K": 0.0}) for cell in q.cells
    )
    _, _, heated = computed(cells=hot)
    _, _, unheated = computed(cells=cold)
    assert heated["cells"][0]["hydraulic_owner"] == unheated["cells"][0]["hydraulic_owner"]
    assert (
        heated["cells"][1]["hydraulic_owner"]["values"]["reynolds"]
        > unheated["cells"][1]["hydraulic_owner"]["values"]["reynolds"]
    )


def test_heater_energy_not_augmented_by_pressure_dissipation():
    q, _ = _example.make_case()
    cells = tuple(
        cell.model_copy(update={"conductance_W_K": 0.0, "heater_into_fluid_W": 0.0}) for cell in q.cells
    )
    q, _, r = computed(cells=cells)
    assert r["values"]["summed_native_dissipated_power_W"] > 0
    assert r["values"]["total_heat_into_fluid_W"] == 0
    assert r["cells"][-1]["outlet"]["enthalpy_j_kg"] == pytest.approx(
        r["cells"][0]["inlet"]["enthalpy_j_kg"], abs=1e-7
    )


def test_internal_plus_pressure_flow_work_is_an_audited_global_ledger():
    _, _, r = computed()
    values = r["values"]
    assert values["global_internal_energy_flux_change_W"] + values[
        "pressure_flow_work_flux_change_W"
    ] == pytest.approx(values["global_enthalpy_flux_change_W"], abs=1e-6)
    assert abs(values["global_internal_plus_flow_work_residual_W"]) < 1e-6
    for cell in r["cells"]:
        assert abs(cell["internal_plus_flow_work_residual_W"]) <= cell["energy_balance_limit_W"]


def test_area_changes_not_silently_treated_as_same_pipe():
    q, _ = _example.make_case()
    cells = (q.cells[0], q.cells[1].model_copy(update={"inner_diameter_m": 0.06}))
    q, c = _example.make_case(reviewed=True, cells=cells)
    r = ht.evaluate_heated_transport(q, c)
    assert r["status"] == "out_of_scope" and r["values"] is None


def test_source_pin_and_record_tampering():
    q, c = _example.make_case(reviewed=True, owner_pins={"wrong": True})
    with pytest.raises(ValueError):
        ht.evaluate_heated_transport(q, c)
    q, c = _example.make_case()
    r = ht.evaluate_heated_transport(q, c)
    r["whole_heated_oil_complete"] = True
    assert not ht.verify_heated_transport_record(r)["valid"]


class SyntheticConstantLiquid:
    """Authored constant-property test double; no oil correlation or field applicability."""

    def runtime_identity(self):
        return {
            "provider": "synthetic_constant_liquid",
            "version": "1",
            "reference_basis": "h=4000*T J/kg for this authored test only",
        }

    def resolve(self, request):
        temperature = request.inputs.get("T")
        if temperature is None:
            temperature = request.inputs["Hmass"] / 4000.0
        return ht.ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=request.inputs["P"],
            temperature_k=temperature,
            density_kg_m3=1000.0,
            enthalpy_j_kg=4000.0 * temperature,
            entropy_j_kg_k=0.0,
            phase="liquid",
            viscosity_pa_s=0.001,
            cp_j_kg_k=4000.0,
            cv_j_kg_k=4000.0,
            conductivity_w_m_k=None,
            speed_of_sound_m_s=None,
            provider_version="1",
            provider_revision="authored_test",
        )


def test_custom_provider_matches_constant_cp_thermal_analytic_solution():
    import math

    provider = SyntheticConstantLiquid()
    q, c = _example.make_case(property_provider_kind="custom")
    q = q.model_copy(
        update={
            "cells": (q.cells[0],),
            "reviews": dict.fromkeys(ht.REVIEWS, True),
            "review_evidence_ids": dict.fromkeys(ht.REVIEWS, q.evidence_ids[0]),
        }
    )
    q = q.model_copy(update={"owner_pins": ht.native_backend_metadata(q, property_provider=provider)})
    c = c.model_copy(update={"metadata": {"heated_transport_boundary": ht.boundary_metadata(q)}})
    r = ht.evaluate_heated_transport(q, c, property_provider=provider)
    assert r["status"] == "computed_diagnostic", r["notes"]
    expected = 320.0 + (300.0 - 320.0) * math.exp(-5.0 / 4000.0) + 100.0 / 4000.0
    assert r["values"]["outlet"]["temperature_k"] == pytest.approx(expected, abs=1e-9)
    assert ht.verify_heated_transport_record(r, request=q, context=c, property_provider=provider)["valid"]
