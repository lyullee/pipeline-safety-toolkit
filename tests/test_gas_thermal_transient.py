"""Deferred numerical/conservation scenarios for NURION; no local execution."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import gas_thermal_transient as gt

_spec = importlib.util.spec_from_file_location(
    "gas_thermal_example", Path(__file__).parents[1] / "examples" / "gas_thermal_transient.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def test_unreviewed_does_not_load_owner(monkeypatch):
    q, c = _example.make_case()
    monkeypatch.setattr(gt, "native_backend_metadata", lambda *a: pytest.fail("unreviewed owner access"))
    r = gt.evaluate_gas_thermal_transient(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]
    assert gt.verify_gas_thermal_transient_record(r, request=q, context=c)["valid"]


def test_context_and_nonfinite_clone():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        gt.evaluate_gas_thermal_transient(q, c.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError):
        gt.evaluate_gas_thermal_transient(q.model_copy(update={"start_time_s": float("nan")}), c)


def test_missing_wall_boundary_not_silent_adiabatic():
    q, _ = _example.make_case()
    cells = (q.cells[0].model_copy(update={"ambient_UA_W_K": None}),)
    q, c = _example.make_case(cells=cells)
    r = gt.evaluate_gas_thermal_transient(q, c)
    assert any("missing:ambient_UA_W_K" in note for note in r["notes"])


def test_effective_fluid_volume_requires_distinct_evidence():
    q, _ = _example.make_case()
    cell = q.cells[0]
    with pytest.raises(ValidationError, match="effective fluid volume"):
        _example.make_case(cells=(cell.model_copy(update={"effective_fluid_volume_m3": 0.02}), q.cells[1]))
    with pytest.raises(ValidationError, match="distinct from the cell geometry"):
        _example.make_case(
            cells=(
                cell.model_copy(
                    update={
                        "effective_fluid_volume_m3": 0.02,
                        "fluid_volume_override_evidence_id": cell.evidence_id,
                    }
                ),
                q.cells[1],
            )
        )


def test_effective_fluid_volume_override_evidence_must_be_bound_to_context():
    q, c = _example.make_case()
    override_id = "reviewed_inventory_volume"
    cell = q.cells[0].model_copy(
        update={"effective_fluid_volume_m3": 0.02, "fluid_volume_override_evidence_id": override_id}
    )
    q, c = _example.make_case(cells=(cell, q.cells[1]), evidence_ids=(q.evidence_ids[0], override_id))
    with pytest.raises(ValueError, match="actual unique context evidence"):
        gt.evaluate_gas_thermal_transient(q, c)


def test_effective_fluid_volume_override_evidence_must_be_selected_by_request():
    q, _ = _example.make_case()
    override_id = "reviewed_inventory_volume"
    cell = q.cells[0].model_copy(
        update={"effective_fluid_volume_m3": 0.02, "fluid_volume_override_evidence_id": override_id}
    )
    with pytest.raises(ValidationError, match="selected in request evidence_ids"):
        _example.make_case(cells=(cell, q.cells[1]))


@pytest.mark.parametrize("ratio", [0.1, 2.0])
def test_effective_fluid_volume_outside_geometry_ratio_is_rejected(ratio):
    q, _ = _example.make_case()
    cell = q.cells[0]
    geometric = 3.141592653589793 * cell.inner_diameter_m**2 * cell.length_m / 4
    override = cell.model_copy(
        update={
            "effective_fluid_volume_m3": geometric * ratio,
            "fluid_volume_override_evidence_id": "reviewed_inventory_volume",
        }
    )
    with pytest.raises(ValidationError, match="outside the reviewed geometry-ratio limits"):
        _example.make_case(cells=(override, q.cells[1]))


@pytest.mark.parametrize(
    "changes",
    [
        {"fluid": "Nitrogen&Oxygen"},
        {"left_boundary_id": "delivery"},
        {"pressure_support_pa_abs": (2000000.0, 100000.0)},
    ],
)
def test_unsupported_basis_and_topology(changes):
    with pytest.raises(ValidationError):
        _example.make_case(**changes)


def computed(**changes):
    pytest.importorskip("lh2dt")
    q, c = _example.make_case(reviewed=True, **changes)
    r = gt.evaluate_gas_thermal_transient(q, c)
    assert r["status"] == "computed_diagnostic", r["notes"]
    return q, c, r


def test_native_multicell_mass_and_total_wall_energy_ledgers():
    q, c, r = computed()
    for row in r["history"]:
        assert row["within_budget"]
        for cell in q.cells:
            name = cell.id
            audit = row["conservation"][name]
            initial = r["initial_states"][name]
            current = row["native"]["states"][name]
            independently = (
                current["internal_energy_J"]
                - initial["internal_energy_J"]
                + cell.wall_heat_capacity_J_K
                * (current["wall_temperature_K"] - initial["wall_temperature_K"])
            )
            assert independently == pytest.approx(
                audit["cumulative_boundary_energy_J"], abs=audit["energy_limit_J"]
            )
    assert len(r["values"]["final_states"]) == 2
    assert r["values"]["cumulative_network_ambient_heat_J"] > 0
    assert gt.verify_gas_thermal_transient_record(r, request=q, context=c)["valid"]
    assert len(gt.to_signal_frames(r, c)) == len(q.cells) * len(r["history"])


def test_native_adiabatic_equilibrium_has_no_thermal_source():
    q, _ = _example.make_case()
    cells = tuple(
        cell.model_copy(update={"ambient_UA_W_K": 0.0, "fluid_wall_UA_W_K": 0.0}) for cell in q.cells
    )
    q, _, r = computed(cells=cells)
    assert r["values"]["cumulative_network_ambient_heat_J"] == 0.0
    for state in r["values"]["final_thermo"].values():
        assert state["temperature_K"] == pytest.approx(300.0, abs=1e-3)


def test_two_windows_preserve_state_and_clock():
    q, _ = _example.make_case()
    windows = (q.windows[0], q.windows[0].model_copy(update={"id": "second"}))
    q, _, r = computed(windows=windows)
    assert r["values"]["actual_duration_s"] == pytest.approx(0.004)
    assert all(b["time_s"] > a["time_s"] for a, b in zip(r["history"], r["history"][1:], strict=False))
    assert {row["window_id"] for row in r["history"]} == {"heat", "second"}


def test_stage_budget_and_source_tampering_are_explicit():
    pytest.importorskip("lh2dt")
    q, c = _example.make_case(reviewed=True, maximum_network_solves=1)
    r = gt.evaluate_gas_thermal_transient(q, c)
    assert r["status"] == "out_of_scope" and r["history"] is None
    q, c = _example.make_case(reviewed=True, owner_pins={"wrong": True})
    with pytest.raises(ValueError):
        gt.evaluate_gas_thermal_transient(q, c)


def test_record_tampering_and_unreviewed_frame_rejected():
    q, c = _example.make_case()
    r = gt.evaluate_gas_thermal_transient(q, c)
    r["acoustic_momentum_PDE"] = True
    assert not gt.verify_gas_thermal_transient_record(r)["valid"]
    with pytest.raises(ValueError):
        gt.to_signal_frames(r, c)


def test_native_stage_receipt_retains_optimizer_enthalpy_and_junction_closure_separately():
    _, _, r = computed()
    assert r["stage_service_records"]
    for stage in r["stage_service_records"]:
        assert stage["native_pressure_solver_success"]
        assert stage["native_pressure_optimizer_success"]
        assert stage["native_enthalpy_solver_success"]
        assert stage["native_junction_conservation_success"]
        assert set(stage["junction_mass_residual_kg_s"]) == set(stage["junction_mass_closure_tolerance_kg_s"])
        assert set(stage["junction_energy_residual_W"]) == set(stage["junction_energy_closure_tolerance_W"])


def test_native_conserved_state_roundoff_guard_is_retained_on_network_links_without_erasing_pressure_residual():
    pytest.importorskip("lh2dt")
    from lh2dt.dynamic_pipe import DynamicHEMPipe
    from lh2dt.pipe import HEMPipe
    from lh2dt.properties import HydrogenProperties

    properties = HydrogenProperties("HEOS::Nitrogen")
    original = properties.from_pT(1e6, 300.0)
    reconstructed = properties.from_rho_u(original.density_kg_m3, original.specific_internal_energy_J_kg)
    assert DynamicHEMPipe._same_thermo(original, reconstructed)
    native = HEMPipe(5.0, 0.05, 1e-5, properties=properties)
    wrapped = gt._EquilibriumPreservingGasLink(native, DynamicHEMPipe._same_thermo)
    result = wrapped.evaluate(original, reconstructed)
    assert result.mass_flow_kg_s == 0.0
    assert result.momentum_residual_Pa == original.pressure_Pa - reconstructed.pressure_Pa
    assert wrapped.equilibrium_roundoff_evaluations == 1

    distinct = properties.from_pT(999900.0, 300.0)
    assert not DynamicHEMPipe._same_thermo(original, distinct)
    assert wrapped.evaluate(original, distinct) == native.evaluate(original, distinct)
    assert wrapped.equilibrium_roundoff_evaluations == 1


def test_native_adiabatic_exact_equilibrium_does_not_manufacture_transport_from_eos_roundoff():
    q, _ = _example.make_case()
    cells = tuple(cell.model_copy(update={"ambient_UA_W_K": 0.0, "fluid_wall_UA_W_K": 0.0}) for cell in q.cells)
    _, _, record = computed(cells=cells)
    for stage in record["stage_service_records"]:
        assert stage["maximum_kinetic_energy_transport_W"] == 0.0
        assert stage["maximum_mach"] == 0.0
    for row in record["history"]:
        for name, state in row["native"]["states"].items():
            initial = record["initial_states"][name]
            assert state["mass_kg"] == initial["mass_kg"]
            assert state["internal_energy_J"] == initial["internal_energy_J"]


def test_small_resolved_reservoir_pressure_gradient_still_drives_actual_native_transport():
    q, _ = _example.make_case()
    left = q.windows[0].left_state.model_copy(update={"inputs": {"P": 1000001.0, "T": 300.0}})
    window = q.windows[0].model_copy(update={"left_state": left, "duration_s": 1e-6,
        "timestep_s": 5e-7, "maximum_reviewed_timestep_s": 5e-7})
    cells = tuple(cell.model_copy(update={"ambient_UA_W_K": 0.0, "fluid_wall_UA_W_K": 0.0}) for cell in q.cells)
    _, _, record = computed(cells=cells, windows=(window,))
    assert max(stage["maximum_mach"] for stage in record["stage_service_records"]) > 0.0
    assert max(stage["maximum_kinetic_energy_transport_W"] for stage in record["stage_service_records"]) > 0.0
    first = q.cells[0].id
    final = record["values"]["final_states"][first]
    assert final["mass_kg"] > record["initial_states"][first]["mass_kg"]
    assert all(row["within_budget"] for row in record["history"])
