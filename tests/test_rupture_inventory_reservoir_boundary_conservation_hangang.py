"""Independent open-boundary HEM inventory references with fixed reservoirs.

Two synthetic pressure directions check that a finite hydrogen inventory
exchanges mass and native enthalpy with an effectively infinite reservoir.
Stored endpoint properties are recomputed independently from CoolProp HEOS.
This covers boundary coupling and conservation, not a plant blowdown rate or
atmospheric release model.
"""

import math

import CoolProp.CoolProp as CP
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.rupture_inventory import (
    RuptureConnection,
    RuptureInventoryCell,
    RuptureInventoryRequest,
    RuptureReservoir,
    bind_rupture_inventory_context,
    evaluate_rupture_inventory,
    rupture_inventory_metadata,
    verify_rupture_inventory_record,
)
from pipeline_integrity.thermo import StateRequest


def _scenario(finite_pressure_pa, reservoir_pressure_pa):
    evidence_ids = ("geometry", "finite_state", "reservoir_state", "restriction", "review")
    evidence = tuple(
        EvidenceRef(
            id=item,
            citation="Authored synthetic finite-volume hydrogen reservoir-boundary reference",
            origin="synthetic",
            sha256=str(index + 1) * 64,
        )
        for index, item in enumerate(evidence_ids)
    )
    cell = RuptureInventoryCell(
        id="finite_cell",
        length_m=0.5,
        inner_diameter_m=0.02,
        wall_heat_capacity_J_K=1200.0,
        fluid_wall_UA_W_K=0.0,
        ambient_UA_W_K=0.0,
        ambient_temperature_K=300.0,
        initial_fluid_state=StateRequest(
            fluid="Hydrogen", backend="HEOS", inputs={"P": finite_pressure_pa, "T": 300.0}
        ),
        initial_wall_temperature_K=300.0,
        evidence_id="geometry",
    )
    reservoir = RuptureReservoir(
        id="fixed_reservoir",
        state=StateRequest(
            fluid="Hydrogen", backend="HEOS", inputs={"P": reservoir_pressure_pa, "T": 300.0}
        ),
        evidence_id="reservoir_state",
    )
    request = RuptureInventoryRequest(
        id="finite_cell_to_fixed_reservoir",
        asset_id="synthetic_asset",
        event_id="reservoir_exchange",
        clock_id="elapsed_seconds",
        duration_s=0.003,
        maximum_step_s=0.001,
        fluid="Hydrogen",
        property_basis_id="coolprop-heos-native-reference",
        inventories=(cell,),
        reservoirs=(reservoir,),
        connections=(
            RuptureConnection(
                id="reservoir_restriction",
                left_node_id="finite_cell",
                right_node_id="fixed_reservoir",
                left_port_id="finite_port",
                right_port_id="reservoir_port",
                full_open_area_m2=1.0e-6,
                discharge_coefficient=0.7,
                fixed_opening=1.0,
                allow_reverse=True,
                throat_pressure_samples=12,
                evidence_id="restriction",
            ),
        ),
        pressure_support_Pa_abs=(100_000.0, 1_500_000.0),
        temperature_support_K=(200.0, 400.0),
        minimum_retained_mass_kg=1.0e-10,
        absolute_mass_balance_tolerance_kg=1.0e-10,
        absolute_energy_balance_tolerance_J=1.0e-4,
        relative_balance_tolerance=1.0e-8,
        maximum_steps=100,
        evidence_ids=evidence_ids,
        review_evidence_id="review",
        homogeneous_equilibrium_storage_and_restriction_network_reviewed=True,
        pure_fluid_energy_reference_and_phase_domain_reviewed=True,
        actuator_conductance_and_closed_seat_leakage_reviewed=True,
        negligible_inventory_kinetic_potential_energy_reviewed=True,
        owner_pins=rupture_inventory_metadata(),
    )
    context = bind_rupture_inventory_context(
        request,
        RunContext(
            asset_id=request.asset_id,
            scenario_id=request.event_id,
            clock_id=request.clock_id,
            origin="synthetic",
            evidence=evidence,
        ),
    )
    return request, context


def _independent_stored_state(request, row):
    cell = request.inventories[0]
    volume_m3 = math.pi * cell.inner_diameter_m**2 * cell.length_m / 4.0
    state = row["thermo_state"]
    mass_kg = CP.PropsSI("Dmass", "P", state["pressure_Pa"], "T", state["temperature_K"], "HEOS::Hydrogen") * volume_m3
    internal_energy_j = CP.PropsSI(
        "Umass", "P", state["pressure_Pa"], "T", state["temperature_K"], "HEOS::Hydrogen"
    ) * mass_kg
    wall_energy_j = cell.wall_heat_capacity_J_K * row["conserved_state"]["wall_temperature_K"]
    return mass_kg, internal_energy_j + wall_energy_j


@pytest.mark.parametrize(
    ("finite_pressure_pa", "reservoir_pressure_pa", "direction"),
    [(1_200_000.0, 1_000_000.0, "forward"), (1_000_000.0, 1_200_000.0, "reverse")],
    ids=("finite-inventory-outflow", "finite-inventory-inflow"),
)
def test_fixed_reservoir_exchange_matches_independent_heos_mass_energy_and_direction(
    finite_pressure_pa, reservoir_pressure_pa, direction
):
    request, context = _scenario(finite_pressure_pa, reservoir_pressure_pa)
    record = evaluate_rupture_inventory(request, context)
    assert record["status"] == "computed", record.get("issues")
    assert record["accepted_steps"] == math.ceil(request.duration_s / request.maximum_step_s)
    assert record["native_EOS_sample_failures"] == 0
    assert len(record["states"]) == record["accepted_steps"] + 1

    initial_mass_kg, initial_energy_j = _independent_stored_state(
        request, record["states"][0]["inventories"]["finite_cell"]
    )
    final_row = record["states"][-1]
    final_mass_kg, final_energy_j = _independent_stored_state(
        request, final_row["inventories"]["finite_cell"]
    )
    exchange = final_row["cumulative_connection_exchange"]["reservoir_restriction"]
    if direction == "forward":
        assert exchange["forward_mass_kg"] > 0.0
        assert exchange["reverse_mass_kg"] == 0.0
    else:
        assert exchange["reverse_mass_kg"] > 0.0
        assert exchange["forward_mass_kg"] == 0.0

    independent_mass_into_cell_kg = exchange["reverse_mass_kg"] - exchange["forward_mass_kg"]
    independent_energy_into_cell_j = exchange["reverse_enthalpy_J"] - exchange["forward_enthalpy_J"]
    assert final_mass_kg - initial_mass_kg == pytest.approx(
        independent_mass_into_cell_kg, rel=2e-7, abs=2e-10
    )
    assert final_energy_j - initial_energy_j == pytest.approx(
        independent_energy_into_cell_j, rel=2e-7, abs=0.05
    )

    initial_pressure_pa = record["states"][0]["inventories"]["finite_cell"]["thermo_state"]["pressure_Pa"]
    final_pressure_pa = final_row["inventories"]["finite_cell"]["thermo_state"]["pressure_Pa"]
    assert abs(final_pressure_pa - reservoir_pressure_pa) < abs(initial_pressure_pa - reservoir_pressure_pa)
    assert verify_rupture_inventory_record(record, request=request, context=context)
