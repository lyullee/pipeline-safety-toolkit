"""Independent native-H2 references for shared finite-volume HEM equalization.

The tests recompute stored fluid mass and internal energy from final CoolProp
HEOS P/T states and authored cylinder volumes, independently of the model's
integrated directional-flow ledger. They check closed-network conservation and
time-step convergence for a pressure-driven transient. This is not an acoustic
rupture-wave or discharge-to-atmosphere qualification.
"""

import math

import CoolProp.CoolProp as CP
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


def _case(maximum_step_s):
    evidence_ids = ("geometry", "initial_state", "connection", "review")
    evidence = tuple(
        EvidenceRef(
            id=item,
            citation="Authored closed-volume hydrogen equalization reference case",
            origin="synthetic",
            sha256=str(index + 1) * 64,
        )
        for index, item in enumerate(evidence_ids)
    )
    cells = tuple(
        RuptureInventoryCell(
            id=name,
            length_m=0.5,
            inner_diameter_m=0.02,
            wall_heat_capacity_J_K=1200.0,
            fluid_wall_UA_W_K=0.0,
            ambient_UA_W_K=0.0,
            ambient_temperature_K=300.0,
            initial_fluid_state=StateRequest(
                fluid="Hydrogen", backend="HEOS", inputs={"P": pressure, "T": 300.0}
            ),
            initial_wall_temperature_K=300.0,
            evidence_id="geometry",
        )
        for name, pressure in (("high", 1_200_000.0), ("low", 800_000.0))
    )
    request = RuptureInventoryRequest(
        id="two_closed_hydrogen_inventories",
        asset_id="synthetic_asset",
        event_id="pressure_equalization",
        clock_id="elapsed_seconds",
        duration_s=0.005,
        maximum_step_s=maximum_step_s,
        fluid="Hydrogen",
        property_basis_id="coolprop-heos-native-reference",
        inventories=cells,
        connections=(
            RuptureConnection(
                id="bidirectional_restriction",
                left_node_id="high",
                right_node_id="low",
                left_port_id="high_port",
                right_port_id="low_port",
                full_open_area_m2=1e-7,
                discharge_coefficient=0.7,
                fixed_opening=1.0,
                allow_reverse=True,
                throat_pressure_samples=12,
                evidence_id="connection",
            ),
        ),
        # The HEM restriction's isentropic throat can be cooler than the
        # inventory bulk state even for a single-phase gas. Cover that valid
        # Hydrogen HEOS path without opening the model to cryogenic inventories.
        pressure_support_Pa_abs=(100_000.0, 1_500_000.0),
        temperature_support_K=(200.0, 400.0),
        minimum_retained_mass_kg=1e-10,
        absolute_mass_balance_tolerance_kg=1e-10,
        absolute_energy_balance_tolerance_J=1e-4,
        relative_balance_tolerance=1e-10,
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


def _independent_totals(request, states):
    by_id = {cell.id: cell for cell in request.inventories}
    totals = {"mass_kg": 0.0, "fluid_internal_energy_J": 0.0}
    for name, row in states.items():
        cell = by_id[name]
        volume = math.pi * cell.inner_diameter_m**2 * cell.length_m / 4.0
        pressure = row["thermo_state"]["pressure_Pa"]
        temperature = row["thermo_state"]["temperature_K"]
        mass = CP.PropsSI("Dmass", "P", pressure, "T", temperature, request.fluid) * volume
        internal_energy = CP.PropsSI("Umass", "P", pressure, "T", temperature, request.fluid) * mass
        totals["mass_kg"] += mass
        totals["fluid_internal_energy_J"] += internal_energy
        assert row["conserved_state"]["wall_temperature_K"] == pytest.approx(
            cell.initial_wall_temperature_K, abs=1e-8
        )
    return totals


@pytest.mark.parametrize("maximum_step_s", [0.00125, 0.000625, 0.0003125])
def test_closed_network_transient_matches_independent_heos_mass_energy_and_relaxes_pressure(
    maximum_step_s,
):
    request, context = _case(maximum_step_s)
    record = evaluate_rupture_inventory(request, context)
    assert record["status"] == "computed", record.get("issues")
    assert record["accepted_steps"] == math.ceil(request.duration_s / maximum_step_s)
    assert len(record["states"]) == record["accepted_steps"] + 1

    initial = _independent_totals(request, record["states"][0]["inventories"])
    final = _independent_totals(request, record["states"][-1]["inventories"])
    assert final["mass_kg"] == pytest.approx(initial["mass_kg"], abs=2e-9)
    assert final["fluid_internal_energy_J"] == pytest.approx(
        initial["fluid_internal_energy_J"], abs=2e-2
    )

    p_high = record["states"][-1]["inventories"]["high"]["thermo_state"]["pressure_Pa"]
    p_low = record["states"][-1]["inventories"]["low"]["thermo_state"]["pressure_Pa"]
    initial_gap = 1_200_000.0 - 800_000.0
    assert 0.0 < p_high - p_low < initial_gap

    first = record["states"][1]["cumulative_connection_exchange"]["bidirectional_restriction"]
    assert first["forward_mass_kg"] > 0.0
    assert first["reverse_mass_kg"] == 0.0


def test_fixed_step_refinement_reduces_pressure_gap_change():
    records = []
    for step in (0.00125, 0.000625, 0.0003125):
        request, context = _case(step)
        record = evaluate_rupture_inventory(request, context)
        assert record["status"] == "computed", record.get("issues")
        states = record["states"][-1]["inventories"]
        records.append(
            states["high"]["thermo_state"]["pressure_Pa"]
            - states["low"]["thermo_state"]["pressure_Pa"]
        )
    coarse_change = abs(records[1] - records[0])
    fine_change = abs(records[2] - records[1])
    assert fine_change < coarse_change
