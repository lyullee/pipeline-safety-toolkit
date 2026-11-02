"""Independent reactive-inventory and time-refinement checks for Nurion execution."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.hydrate_kinetics import (
    HydrateEquilibriumFugacity,
    HydrateKineticWindow,
    HydrateKineticsRequest,
    evaluate_hydrate_kinetics,
)


def hydrate_case(step_s, *, formation=0.0, deposition=0.0, erosion=0.0):
    request = HydrateKineticsRequest(
        id="reactive_inventory", asset_id="reactor", clock_id="seconds", guest_fluid="Methane",
        energy_reference_id="common-reference", total_volume_m3=0.01, minimum_gas_volume_m3=0.001,
        initial_temperature_K=280.0, initial_guest_gas_mol=1.0, initial_liquid_water_mol=10.0,
        initial_suspended_hydrate_mol=0.1, initial_deposited_hydrate_mol=0.1,
        water_per_guest_mol=5.75, water_molar_mass_kg_mol=0.01801528,
        water_density_kg_m3=1000.0, hydrate_density_kg_m3=900.0,
        water_molar_heat_capacity_J_mol_K=75.0, hydrate_molar_heat_capacity_J_mol_K=300.0,
        reference_temperature_K=280.0, reference_guest_pressure_Pa_abs=1e5,
        water_reference_internal_energy_J_mol=0.0,
        formation_internal_energy_release_at_reference_J_mol=50000.0,
        formation_coefficient_at_reference_mol_m2_s_Pa=formation,
        formation_activation_energy_J_mol=0.0,
        dissociation_coefficient_at_reference_mol_m2_s_Pa=0.0,
        dissociation_activation_energy_J_mol=0.0,
        initial_accessible_water_interface_area_m2=1.0,
        suspended_specific_reactive_area_m2_mol=1.0, deposited_specific_reactive_area_m2_mol=1.0,
        equilibrium=HydrateEquilibriumFugacity(
            temperature_K=(270.0, 290.0), guest_fugacity_Pa=(1e5, 1e5), evidence_id="review"),
        windows=(HydrateKineticWindow(id="bath", duration_s=2.0, bath_temperature_K=280.0,
            deposition_rate_constant_s_inv=deposition, erosion_rate_constant_s_inv=erosion,
            evidence_id="review"),),
        maximum_step_s=step_s, absolute_extent_residual_tolerance_mol=1e-10,
        relative_extent_residual_tolerance=1e-9,
        pressure_support_Pa_abs=(1e4, 1e7), material_and_kinetic_evidence_ids=("review",),
        pure_guest_common_reference_and_fixed_condensed_densities_reviewed=True,
        interfacial_fugacity_and_calibrated_effective_kinetics_reviewed=True,
        shrinking_water_domains_and_reactive_hydrate_area_reviewed=True,
        bath_and_first_order_deposition_erosion_reviewed=True,
    )
    context = RunContext(asset_id="reactor", scenario_id="refinement", clock_id="seconds",
        origin="simulation", evidence=(EvidenceRef(id="review", citation="Authored reactive-inventory reference",
            origin="synthetic", sha256="d" * 64),))
    return request, context


def computed(request, context):
    result = evaluate_hydrate_kinetics(request, context)
    assert result["status"] == "computed_diagnostic", result.get("failure")
    assert result["states"][-1]["time_s"] == pytest.approx(2.0, abs=1e-13)
    return result


def test_hydrate_deposition_erosion_matches_independent_linear_solution_and_first_order_limit():
    errors = []
    total, initial_deposit, kd, ke, duration = 0.2, 0.1, 0.7, 0.2, 2.0
    equilibrium = kd * total / (kd + ke)
    continuous = equilibrium + (initial_deposit - equilibrium) * math.exp(-(kd + ke) * duration)
    for step in (0.5, 0.25, 0.125):
        request, context = hydrate_case(step, deposition=kd, erosion=ke)
        result = computed(request, context)
        final = result["states"][-1]
        # Independently solve D' = kd*(H-D)-ke*D and its backward-Euler recurrence.
        discrete = equilibrium + (initial_deposit - equilibrium) / (1 + (kd + ke) * step) ** round(duration / step)
        assert final["deposited_hydrate_mol"] == pytest.approx(discrete, abs=1e-8)
        assert final["suspended_hydrate_mol"] + final["deposited_hydrate_mol"] == pytest.approx(total, abs=1e-9)
        assert final["guest_gas_mol"] == pytest.approx(1.0, abs=1e-9)
        assert final["liquid_water_mol"] == pytest.approx(10.0, abs=1e-9)
        assert result["total_bath_heat_into_reactor_J"] == pytest.approx(0.0, abs=1e-4)
        errors.append(abs(final["deposited_hydrate_mol"] - continuous))
    assert errors[0] > errors[1] > errors[2] > 0.0
    assert 1.6 < errors[0] / errors[1] < 2.2
    assert 1.6 < errors[1] / errors[2] < 2.2


def test_real_gas_hydrate_formation_conserves_stoichiometry_energy_and_refines_in_time():
    import CoolProp.CoolProp as CP

    endpoints = []
    for step in (0.2, 0.1, 0.05):
        request, context = hydrate_case(step, formation=1e-7)
        result = computed(request, context)
        initial, final = result["states"][0], result["states"][-1]
        previous = initial["total_hydrate_mol"]
        guest_total = 1.2
        water_total = 10.0 + 5.75 * 0.2
        for row in result["states"]:
            h = row["total_hydrate_mol"]
            assert h >= previous - 1e-10
            assert row["guest_gas_mol"] + h == pytest.approx(guest_total, abs=1e-9)
            assert row["liquid_water_mol"] + 5.75 * h == pytest.approx(water_total, abs=1e-9)
            previous = h
        assert final["total_hydrate_mol"] > initial["total_hydrate_mol"] + 1e-3
        # Reconstruct actual phase internal energy from an independent EOS object.
        gas = CP.AbstractState("HEOS", "Methane")
        gas.update(CP.PT_INPUTS, 1e5, 280.0)
        hydrate_u = gas.umolar() - 50000.0
        reconstructed = []
        for row in (initial, final):
            gas.update(CP.DmolarT_INPUTS, row["guest_gas_mol"] / row["gas_volume_m3"], 280.0)
            reconstructed.append(row["guest_gas_mol"] * gas.umolar() + row["total_hydrate_mol"] * hydrate_u)
        assert final["internal_energy_J"] == pytest.approx(reconstructed[1], abs=1e-5)
        assert result["total_bath_heat_into_reactor_J"] == pytest.approx(reconstructed[1] - reconstructed[0], abs=1e-5)
        assert result["total_bath_heat_into_reactor_J"] < 0.0
        endpoints.append(final["total_hydrate_mol"])
    coarse_change = abs(endpoints[0] - endpoints[1])
    fine_change = abs(endpoints[1] - endpoints[2])
    assert coarse_change > fine_change > 1e-9
    assert 1.5 < coarse_change / fine_change < 2.5
