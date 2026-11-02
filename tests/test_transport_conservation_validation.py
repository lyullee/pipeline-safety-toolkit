"""Independent shaft ODE and multicomponent transport conservation references."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.inventory_transfers import (
    ConservedInventory,
    InventoryStream,
    InventoryTransferRequest,
    InventoryTransferWindow,
    evaluate_inventory_transfers,
    inventory_transfer_signal_frames,
)
from pipeline_integrity.rotating_dynamics import (
    RotatingShaftRequest,
    ShaftWindow,
    SignedRotatingMap,
    evaluate_rotating_shaft,
)


def context():
    return RunContext(asset_id="unit", scenario_id="transport", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored shaft and species-balance reference",
            origin="synthetic", sha256="e" * 64),))


def shaft_case(step, *, initial_speed, drive, map_drag=0.0, viscous_drag=0.0):
    return RotatingShaftRequest(
        id="shaft", asset_id="unit", clock_id="seconds", shaft_id="rotor", inlet_port_id="inlet",
        outlet_port_id="outlet", inertia_kg_m2=4.0, initial_speed_rad_s=initial_speed,
        performance_map=SignedRotatingMap(
            id="linear_map", speed_rad_s=(-20.0, 20.0), volume_flow_m3_s=(-1.0, 1.0),
            resisting_torque_N_m=((-20.0 * map_drag, -20.0 * map_drag), (20.0 * map_drag, 20.0 * map_drag)),
            pressure_rise_Pa=((0.0, 0.0), (0.0, 0.0)), evidence_id="review",
            signed_axes_and_torque_pressure_conventions_reviewed=True),
        windows=(ShaftWindow(id="forcing", duration_s=2.0, volume_flow_m3_s=0.0,
            drive_torque_N_m=drive, viscous_drag_N_m_s=viscous_drag, evidence_id="review"),),
        maximum_step_s=step, map_power_loss_nonnegative_reviewed=True,
        imposed_flow_and_no_network_feedback_reviewed=True, rotor_and_drive_evidence_id="review",
    )


def test_shaft_constant_torque_has_exact_angular_momentum_and_kinetic_work():
    request = shaft_case(0.25, initial_speed=1.0, drive=2.0)
    result = evaluate_rotating_shaft(request, context())
    assert result["status"] == "computed_diagnostic"
    for row in result["states"]:
        omega = 1.0 + 2.0 * row["time_s"] / 4.0
        assert row["speed_rad_s"] == pytest.approx(omega, abs=1e-10)
        assert row["rotor_energy_J"] == pytest.approx(0.5 * 4.0 * omega**2, abs=1e-9)
    ledger = result["energy_ledger_J"]
    assert ledger["initial_rotor"] == pytest.approx(2.0)
    assert ledger["final_rotor"] == pytest.approx(8.0)
    assert ledger["drive"] == pytest.approx(6.0, abs=1e-9)
    assert ledger["load"] == ledger["drag"] == ledger["hydraulic"] == 0.0


@pytest.mark.parametrize("sign", (-1.0, 1.0))
def test_signed_shaft_linear_drag_matches_analytic_decay_and_second_order_time_convergence(sign):
    errors = []
    initial = 8.0 * sign
    decay = 2.0 / 4.0  # One unit of mapped drag plus one of explicit viscous drag.
    continuous = initial * math.exp(-decay * 2.0)
    for step in (0.5, 0.25, 0.125):
        request = shaft_case(step, initial_speed=initial, drive=0.0, map_drag=1.0, viscous_drag=1.0)
        result = evaluate_rotating_shaft(request, context())
        assert result["status"] == "computed_diagnostic"
        exact_midpoint = initial * ((1 - decay * step / 2) / (1 + decay * step / 2)) ** round(2.0 / step)
        actual = result["states"][-1]["speed_rad_s"]
        assert actual == pytest.approx(exact_midpoint, abs=1e-8)
        energies = [row["rotor_energy_J"] for row in result["states"]]
        assert all(b < a for a, b in zip(energies, energies[1:]))
        ledger = result["energy_ledger_J"]
        assert ledger["load"] == pytest.approx(ledger["drag"], abs=1e-8)
        assert ledger["initial_rotor"] - ledger["final_rotor"] == pytest.approx(
            ledger["load"] + ledger["drag"], abs=1e-7)
        errors.append(abs(actual - continuous))
    assert errors[0] > errors[1] > errors[2] > 0.0
    assert 3.5 < errors[0] / errors[1] < 4.5
    assert 3.5 < errors[1] / errors[2] < 4.5


def inventory_case(parts=1, energy_shift=0.0, reverse_stream_order=False):
    streams = (
        InventoryStream(id="internal", source_id="left", destination_id="right",
            species_mass_flow_kg_s={"A": 0.2, "B": 0.3}, specific_total_flow_energy_J_kg=100.0 + energy_shift,
            evidence_id="review"),
        InventoryStream(id="feed", destination_id="left", species_mass_flow_kg_s={"A": 0.1, "B": 0.0},
            specific_total_flow_energy_J_kg=200.0 + energy_shift, evidence_id="review"),
        InventoryStream(id="delivery", source_id="right", species_mass_flow_kg_s={"A": 0.0, "B": 0.1},
            specific_total_flow_energy_J_kg=50.0 + energy_shift, evidence_id="review"),
    )
    return InventoryTransferRequest(
        id="inventory", asset_id="unit", clock_id="seconds", species_ids=("A", "B"),
        total_energy_reference_id="shifted" if energy_shift else "base",
        inventories=(ConservedInventory(id="left", species_mass_kg={"A": 10.0, "B": 20.0},
            stored_total_energy_J=1000.0 + energy_shift * 30.0, evidence_id="review"),
            ConservedInventory(id="right", species_mass_kg={"A": 1.0, "B": 3.0},
                stored_total_energy_J=200.0 + energy_shift * 4.0, evidence_id="review")),
        windows=tuple(InventoryTransferWindow(id=f"window_{index}", duration_s=2.0 / parts,
            streams=tuple(reversed(streams)) if reverse_stream_order else streams,
            heat_and_work_into_inventory_W={"left": 10.0, "right": -2.0}, evidence_id="review")
            for index in range(parts)),
        prescribed_streams_common_energy_reference_reviewed=True,
    )


def inventory_rows(record):
    return {value["id"]: value for value in record["states"][-1]["inventories"]}


def test_multicomponent_inventory_exact_open_boundary_balance_is_partition_and_stream_order_invariant():
    for parts, reverse in ((1, False), (4, False), (4, True)):
        request = inventory_case(parts, reverse_stream_order=reverse)
        result = evaluate_inventory_transfers(request, context())
        assert result["status"] == "computed"
        final = inventory_rows(result)
        assert final["left"]["species_mass_kg"] == pytest.approx({"A": 9.8, "B": 19.4}, abs=1e-12)
        assert final["right"]["species_mass_kg"] == pytest.approx({"A": 1.4, "B": 3.4}, abs=1e-12)
        assert final["left"]["stored_total_energy_J"] == pytest.approx(960.0, abs=1e-10)
        assert final["right"]["stored_total_energy_J"] == pytest.approx(286.0, abs=1e-10)
        ledger = result["ledgers"][-1]
        assert ledger["cumulative_external_species_mass_kg"] == pytest.approx({"A": 0.2, "B": -0.2})
        assert ledger["cumulative_external_flow_energy_J"] == pytest.approx(30.0)
        assert ledger["cumulative_heat_work_J"] == pytest.approx(16.0)
        frames, receipt = inventory_transfer_signal_frames(result, inventory_id="right", stream_id="downstream")
        assert frames[-1].values["total_mass_kg"] == pytest.approx(4.8)
        assert frames[-1].values["stored_total_energy_J"] == pytest.approx(286.0)
        assert receipt["source_record_digest"] == result["record_digest"]


@pytest.mark.parametrize("energy_shift", (-10000.0, 10000.0))
def test_inventory_mass_transport_and_energy_change_transform_consistently_with_common_reference_shift(energy_shift):
    base = inventory_rows(evaluate_inventory_transfers(inventory_case(), context()))
    shifted = inventory_rows(evaluate_inventory_transfers(inventory_case(energy_shift=energy_shift), context()))
    for name in ("left", "right"):
        assert shifted[name]["species_mass_kg"] == pytest.approx(base[name]["species_mass_kg"])
        final_mass = math.fsum(base[name]["species_mass_kg"].values())
        assert shifted[name]["stored_total_energy_J"] == pytest.approx(
            base[name]["stored_total_energy_J"] + energy_shift * final_mass, abs=1e-8)
