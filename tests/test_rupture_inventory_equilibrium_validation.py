"""Independent equal-pressure equilibrium and inventory conservation reference.

All inputs are synthetic, authored states. The check targets native shared HEM
storage with no imposed pressure, heat, or actuator disturbance; it is not a
rupture-transient or field qualification.
"""

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.rupture_inventory import (
    RuptureConnection,
    RuptureInventoryCell,
    RuptureInventoryRequest,
    bind_rupture_inventory_context,
    evaluate_rupture_inventory,
    rupture_inventory_metadata,
)
from pipeline_integrity.thermo import StateRequest


def _equilibrium_case():
    evidence_ids = ("geometry", "initial_state", "connection", "review")
    evidence = tuple(
        EvidenceRef(
            id=item,
            citation="Authored equal-pressure equilibrium fixture for conservation verification",
            origin="synthetic",
            sha256=(str(index + 1) * 64),
        )
        for index, item in enumerate(evidence_ids)
    )
    state = StateRequest(fluid="Hydrogen", backend="HEOS", inputs={"P": 1_000_000.0, "T": 300.0})
    cells = tuple(
        RuptureInventoryCell(
            id=name,
            length_m=0.5,
            inner_diameter_m=0.02,
            wall_heat_capacity_J_K=1200.0,
            fluid_wall_UA_W_K=0.0,
            ambient_UA_W_K=0.0,
            ambient_temperature_K=300.0,
            initial_fluid_state=state,
            initial_wall_temperature_K=300.0,
            evidence_id="geometry",
        )
        for name in ("inventory_left", "inventory_right")
    )
    request = RuptureInventoryRequest(
        id="equal_pressure_static_network",
        asset_id="synthetic_asset",
        event_id="equilibrium_reference",
        clock_id="elapsed_seconds",
        duration_s=0.01,
        maximum_step_s=0.005,
        fluid="Hydrogen",
        property_basis_id="native_heos_reference",
        inventories=cells,
        connections=(
            RuptureConnection(
                id="equalization_link",
                left_node_id="inventory_left",
                right_node_id="inventory_right",
                left_port_id="left_material_port",
                right_port_id="right_material_port",
                full_open_area_m2=1e-5,
                discharge_coefficient=0.7,
                fixed_opening=1.0,
                allow_reverse=True,
                throat_pressure_samples=12,
                evidence_id="connection",
            ),
        ),
        pressure_support_Pa_abs=(500_000.0, 1_500_000.0),
        temperature_support_K=(290.0, 310.0),
        minimum_retained_mass_kg=1e-8,
        absolute_mass_balance_tolerance_kg=1e-10,
        absolute_energy_balance_tolerance_J=1e-5,
        relative_balance_tolerance=1e-10,
        evidence_ids=evidence_ids,
        review_evidence_id="review",
        homogeneous_equilibrium_storage_and_restriction_network_reviewed=True,
        pure_fluid_energy_reference_and_phase_domain_reviewed=True,
        actuator_conductance_and_closed_seat_leakage_reviewed=True,
        negligible_inventory_kinetic_potential_energy_reviewed=True,
        owner_pins=rupture_inventory_metadata(),
    )
    context = RunContext(
        asset_id="synthetic_asset",
        scenario_id="equilibrium_reference",
        clock_id="elapsed_seconds",
        origin="synthetic",
        evidence=evidence,
    )
    return request, bind_rupture_inventory_context(request, context)


def test_equal_pressure_native_shared_inventories_remain_at_exact_static_equilibrium():
    request, context = _equilibrium_case()
    record = evaluate_rupture_inventory(request, context)
    assert record["status"] == "computed", record.get("issues")
    assert record["accepted_steps"] == 2
    assert len(record["states"]) == 3

    initial = record["states"][0]
    for row in record["states"]:
        for inventory_id in ("inventory_left", "inventory_right"):
            state = row["inventories"][inventory_id]
            initial_state = initial["inventories"][inventory_id]
            # Equal pressure/temperature at both ends is a stationary solution
            # of the restriction network. No boundary source or wall heat exists.
            assert state["thermo_state"]["pressure_Pa"] == pytest.approx(1_000_000.0, rel=1e-10)
            assert state["thermo_state"]["temperature_K"] == pytest.approx(300.0, abs=1e-9)
            assert state["conserved_state"]["mass_kg"] == pytest.approx(
                initial_state["conserved_state"]["mass_kg"], abs=request.absolute_mass_balance_tolerance_kg
            )
            assert state["stored_fluid_and_wall_energy_J"] == pytest.approx(
                initial_state["stored_fluid_and_wall_energy_J"], abs=request.absolute_energy_balance_tolerance_J
            )
            assert state["conserved_state"]["wall_temperature_K"] == pytest.approx(300.0, abs=1e-9)

    for step in record["states"][1:]:
        for inventory_id in ("inventory_left", "inventory_right"):
            ledger = step["conservation"][inventory_id]
            assert abs(ledger["mass_residual_kg"]) <= request.absolute_mass_balance_tolerance_kg
            assert abs(ledger["energy_residual_J"]) <= request.absolute_energy_balance_tolerance_J
        link = step["cumulative_connection_exchange"]["equalization_link"]
        assert link["forward_mass_kg"] == pytest.approx(0.0, abs=1e-12)
        assert link["reverse_mass_kg"] == pytest.approx(0.0, abs=1e-12)
