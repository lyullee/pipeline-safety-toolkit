"""Independent prescribed-flux frost deposition and two-node thermal oracle.

All inputs are authored synthetic values. This checks the reduced model's
one-step implicit storage/conduction balance, not vapor-transfer kinetics.
"""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.frost_growth import (
    FrostFluxWindow,
    FrostGrowthRequest,
    FrostMaterial,
    evaluate_frost_growth,
    verify_frost_growth_record,
)


def _evidence(identity):
    return EvidenceRef(
        id=identity,
        citation="Authored synthetic frost deposition and thermal reference",
        origin="synthetic",
        sha256=digest(("frost-two-node-oracle", identity)),
        limitations=("Synthetic prescribed flux; no field or phase-equilibrium qualification.",),
    )


def test_single_step_deposition_matches_independent_two_node_solve_and_ledgers():
    area = 0.2
    density = 900.0
    solid_cp = 2100.0
    vapor_cp = 1800.0
    conductivity = 2.0
    reference = 273.15
    initial_thickness = 0.002
    frost_initial = 268.0
    wall_initial = 270.0
    wall_capacity = 10.0
    contact_resistance = 0.02
    deposition_flux = 1.0e-6
    duration = 0.25
    incoming_temperature = 250.0
    latent_sublimation = 2.8e6
    ambient_temperature = 260.0
    ambient_film = 5.0
    reservoir_temperature = 272.0
    reservoir_conductance = 1.0
    frost_direct_heat = 0.05
    substrate_direct_heat = 0.1

    material = FrostMaterial(
        density_kg_m3=density,
        solid_heat_capacity_J_kg_K=solid_cp,
        liquid_heat_capacity_J_kg_K=4200.0,
        vapor_heat_capacity_J_kg_K=vapor_cp,
        conductivity_W_m_K=conductivity,
        phase_reference_temperature_K=reference,
        latent_sublimation_at_reference_J_kg=latent_sublimation,
        latent_fusion_at_reference_J_kg=334000.0,
        evidence_id="material",
    )
    window = FrostFluxWindow(
        id="deposit-one-window",
        duration_s=duration,
        maximum_step_s=duration,
        vapor_deposition_kg_m2_s=deposition_flux,
        incoming_vapor_temperature_K=incoming_temperature,
        outgoing_vapor_temperature_K=incoming_temperature,
        outgoing_liquid_temperature_K=reference,
        ambient_temperature_K=ambient_temperature,
        ambient_film_W_m2_K=ambient_film,
        substrate_reservoir_temperature_K=reservoir_temperature,
        substrate_reservoir_conductance_W_K=reservoir_conductance,
        frost_direct_heat_W=frost_direct_heat,
        substrate_direct_heat_W=substrate_direct_heat,
        evidence_id="flux",
    )
    request = FrostGrowthRequest(
        id="frost-one-step-oracle",
        asset_id="synthetic-frost-panel",
        clock_id="elapsed",
        area_m2=area,
        material=material,
        initial_thickness_m=initial_thickness,
        minimum_supported_thickness_m=0.001,
        maximum_supported_thickness_m=0.01,
        initial_frost_temperature_K=frost_initial,
        initial_substrate_temperature_K=wall_initial,
        substrate_heat_capacity_J_K=wall_capacity,
        substrate_contact_resistance_K_W=contact_resistance,
        minimum_supported_temperature_K=200.0,
        maximum_substrate_temperature_K=330.0,
        windows=(window,),
        absolute_energy_tolerance_J=1e-8,
        relative_energy_tolerance=1e-10,
        geometry_and_midpoint_thermal_storage_reviewed=True,
        constant_properties_and_solid_only_storage_reviewed=True,
        fluxes_and_phase_change_energy_boundary_reviewed=True,
        evidence_ids=("geometry", "thermal", "flux"),
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="single-step-deposition",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=tuple(_evidence(key) for key in ("geometry", "thermal", "flux", "material")),
    )

    record = evaluate_frost_growth(request, context)
    assert record["status"] == "computed_diagnostic", record["failure"]
    assert len(record["states"]) == 2
    assert len(record["steps"]) == 1

    initial_mass = density * area * initial_thickness
    deposited_mass = deposition_flux * area * duration
    final_mass = initial_mass + deposited_mass
    final_thickness = final_mass / (density * area)
    dt = duration
    half_resistance = final_thickness / (2.0 * conductivity * area)
    base_conductance = 1.0 / (half_resistance + contact_resistance)
    ambient_conductance = 1.0 / (half_resistance + 1.0 / (ambient_film * area))
    vapor_enthalpy = latent_sublimation + vapor_cp * (incoming_temperature - reference)
    deposition_power = deposition_flux * area * vapor_enthalpy

    # Independent 2x2 linear solve for the implicit frost/substrate nodes.
    diagonal_frost = final_mass * solid_cp / dt + base_conductance + ambient_conductance
    diagonal_wall = wall_capacity / dt + base_conductance + reservoir_conductance
    rhs_frost = (
        initial_mass * solid_cp * (frost_initial - reference) / dt
        + deposition_power
        + frost_direct_heat
        + ambient_conductance * (ambient_temperature - reference)
    )
    rhs_wall = (
        wall_capacity * (wall_initial - reference) / dt
        + substrate_direct_heat
        + reservoir_conductance * (reservoir_temperature - reference)
    )
    determinant = diagonal_frost * diagonal_wall - base_conductance**2
    expected_frost = reference + (
        rhs_frost * diagonal_wall + base_conductance * rhs_wall
    ) / determinant
    expected_wall = reference + (
        diagonal_frost * rhs_wall + base_conductance * rhs_frost
    ) / determinant

    final_state = record["states"][-1]
    assert final_state["frost_mass_kg"] == pytest.approx(final_mass, rel=1e-12)
    assert final_state["thickness_m"] == pytest.approx(final_thickness, rel=1e-12)
    assert final_state["frost_layer_resistance_K_W"] == pytest.approx(
        final_thickness / (conductivity * area), rel=1e-12
    )
    assert final_state["frost_temperature_K"] == pytest.approx(expected_frost, abs=1e-10)
    assert final_state["substrate_temperature_K"] == pytest.approx(expected_wall, abs=1e-10)
    assert record["deposited_mass_kg"] == pytest.approx(deposited_mass, rel=1e-12)
    assert record["mass_residual_kg"] == pytest.approx(0.0, abs=1e-14)
    assert record["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)
    assert abs(record["steps"][0]["frost_energy_residual_J"]) <= 1e-8
    assert abs(record["steps"][0]["substrate_energy_residual_J"]) <= 1e-8
    assert verify_frost_growth_record(record, request=request, context=context)

