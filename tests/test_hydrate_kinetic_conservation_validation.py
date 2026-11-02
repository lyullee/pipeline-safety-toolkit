"""Independent inventory and energy checks for nonzero hydrate kinetics.

Inputs are synthetic authored fixtures. These are model-consistency checks,
not kinetic-parameter calibration or field qualification.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.hydrate_kinetics import (
    HydrateEquilibriumFugacity,
    HydrateKineticWindow,
    HydrateKineticsRequest,
    evaluate_hydrate_kinetics,
)


def _case(maximum_step_s: float):
    request = HydrateKineticsRequest(
        id="nonzero_reaction_inventory",
        asset_id="synthetic_reactor",
        clock_id="elapsed_seconds",
        guest_fluid="Methane",
        energy_reference_id="authored_reference",
        total_volume_m3=0.01,
        minimum_gas_volume_m3=0.001,
        initial_temperature_K=280.0,
        initial_guest_gas_mol=1.0,
        initial_liquid_water_mol=10.0,
        initial_suspended_hydrate_mol=0.1,
        initial_deposited_hydrate_mol=0.1,
        water_per_guest_mol=5.75,
        water_molar_mass_kg_mol=0.01801528,
        water_density_kg_m3=1000.0,
        hydrate_density_kg_m3=900.0,
        water_molar_heat_capacity_J_mol_K=75.0,
        hydrate_molar_heat_capacity_J_mol_K=300.0,
        reference_temperature_K=280.0,
        reference_guest_pressure_Pa_abs=100000.0,
        water_reference_internal_energy_J_mol=0.0,
        formation_internal_energy_release_at_reference_J_mol=50000.0,
        formation_coefficient_at_reference_mol_m2_s_Pa=1e-9,
        formation_activation_energy_J_mol=0.0,
        dissociation_coefficient_at_reference_mol_m2_s_Pa=2e-10,
        dissociation_activation_energy_J_mol=0.0,
        initial_accessible_water_interface_area_m2=0.02,
        suspended_specific_reactive_area_m2_mol=0.2,
        deposited_specific_reactive_area_m2_mol=0.1,
        equilibrium=HydrateEquilibriumFugacity(
            temperature_K=(270.0, 290.0),
            guest_fugacity_Pa=(10000.0, 40000.0),
            evidence_id="synthetic_law",
        ),
        windows=(
            HydrateKineticWindow(
                id="isothermal_bath",
                duration_s=0.4,
                bath_temperature_K=280.0,
                deposition_rate_constant_s_inv=0.7,
                erosion_rate_constant_s_inv=0.1,
                evidence_id="synthetic_law",
            ),
        ),
        maximum_step_s=maximum_step_s,
        pressure_support_Pa_abs=(1000.0, 1e7),
        material_and_kinetic_evidence_ids=("synthetic_law",),
        pure_guest_common_reference_and_fixed_condensed_densities_reviewed=True,
        interfacial_fugacity_and_calibrated_effective_kinetics_reviewed=True,
        shrinking_water_domains_and_reactive_hydrate_area_reviewed=True,
        bath_and_first_order_deposition_erosion_reviewed=True,
    )
    context = RunContext(
        asset_id="synthetic_reactor",
        scenario_id="nonzero_reaction_inventory",
        clock_id="elapsed_seconds",
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="synthetic_law",
                citation="Authored synthetic constitutive inputs for conservation checks",
                origin="synthetic",
                sha256="d" * 64,
            ),
        ),
    )
    return request, context


@pytest.mark.parametrize("step_s", (0.2, 0.1))
def test_nonzero_hydrate_reaction_and_deposition_preserve_independent_inventories_and_energy(step_s):
    request, context = _case(step_s)
    record = evaluate_hydrate_kinetics(request, context)
    assert record["status"] == "computed_diagnostic", record["failure"]
    assert len(record["ledgers"]) == math.ceil(0.4 / step_s)
    assert any(row["formed_mol"] > 0.0 for row in record["ledgers"])
    assert any(row["deposited_transfer_mol"] > 0.0 for row in record["ledgers"])

    guest_total = request.initial_guest_gas_mol + request.initial_suspended_hydrate_mol + request.initial_deposited_hydrate_mol
    water_total = request.initial_liquid_water_mol + request.water_per_guest_mol * (
        request.initial_suspended_hydrate_mol + request.initial_deposited_hydrate_mol
    )
    hydrate_mass = record["hydrate_molar_mass_kg_mol"]
    guest_mass = hydrate_mass - request.water_per_guest_mol * request.water_molar_mass_kg_mol
    initial_energy = record["states"][0]["internal_energy_J"]
    cumulative_formed = cumulative_dissociated = 0.0
    cumulative_bath_heat = 0.0
    previous_deposited = request.initial_deposited_hydrate_mol

    # These are direct stoichiometric and first-law balances; they do not
    # reuse the solver's residual fields as the expected values.
    for state, ledger in zip(record["states"][1:], record["ledgers"], strict=True):
        cumulative_formed += ledger["formed_mol"]
        cumulative_dissociated += ledger["dissociated_suspended_mol"] + ledger["dissociated_deposit_mol"]
        cumulative_bath_heat += ledger["sensible_bath_heat_into_reactor_J"] + ledger["reaction_bath_heat_into_reactor_J"]

        hydrate = state["total_hydrate_mol"]
        deposited = state["deposited_hydrate_mol"]
        assert state["guest_gas_mol"] + hydrate == pytest.approx(guest_total, abs=2e-8)
        assert state["liquid_water_mol"] + request.water_per_guest_mol * hydrate == pytest.approx(water_total, abs=2e-8)
        assert state["guest_gas_mol"] * guest_mass + state["liquid_water_mol"] * request.water_molar_mass_kg_mol + hydrate * hydrate_mass == pytest.approx(
            guest_total * guest_mass + water_total * request.water_molar_mass_kg_mol,
            abs=2e-10,
        )
        assert hydrate == pytest.approx(
            request.initial_suspended_hydrate_mol + request.initial_deposited_hydrate_mol + cumulative_formed - cumulative_dissociated,
            abs=2e-8,
        )
        assert deposited == pytest.approx(
            previous_deposited + ledger["deposited_transfer_mol"] - ledger["eroded_transfer_mol"]
            - ledger["dissociated_deposit_mol"],
            abs=2e-8,
        )
        assert state["internal_energy_J"] - initial_energy == pytest.approx(cumulative_bath_heat, abs=2e-5)
        assert abs(ledger["energy_residual_J"]) <= request.absolute_energy_tolerance_J + request.relative_energy_tolerance * max(
            abs(state["internal_energy_J"] - initial_energy), abs(cumulative_bath_heat)
        )
        assert ledger["equilibrium_fugacity_Pa"] == pytest.approx(math.sqrt(10000.0 * 40000.0), rel=1e-12)
        previous_deposited = deposited


def test_deposition_endpoint_converges_under_two_successive_step_refinements():
    endpoints = []
    for step_s in (0.2, 0.1, 0.05):
        request, context = _case(step_s)
        record = evaluate_hydrate_kinetics(request, context)
        assert record["status"] == "computed_diagnostic", record["failure"]
        endpoints.append(record["states"][-1])

    coarse_error = abs(endpoints[0]["deposited_hydrate_mol"] - endpoints[1]["deposited_hydrate_mol"])
    fine_error = abs(endpoints[1]["deposited_hydrate_mol"] - endpoints[2]["deposited_hydrate_mol"])
    assert fine_error < coarse_error
    assert 0.4 < fine_error / coarse_error < 0.7
    for state in endpoints[1:]:
        assert state["total_hydrate_mol"] == pytest.approx(endpoints[0]["total_hydrate_mol"], abs=1e-9)
        assert state["internal_energy_J"] == pytest.approx(endpoints[0]["internal_energy_J"], abs=1e-4)
