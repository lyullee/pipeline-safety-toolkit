"""Independent ideal-gas and ledger references for coupled tank drainage.

All inputs are authored synthetic boundary conditions. These tests verify the
implemented equations and numerical event handling; they do not qualify a tank,
discharge coefficient, liquid property set, or installed pressure system.

Adaptive heated transport and serial gas-potential paths are intentionally not
represented by owner-only replay or mock assertions here. The former requires
reviewed, source-pinned refinement mapping plus a qualified heated-transport
property provider; the latter requires the installed ``lh2dt`` friction owner
and a thermodynamic provider with viscosity and sound speed. A useful analytic
oracle for either path needs those explicit dependencies and reviewed domain
inputs. The branching gas-plenum API likewise depends on the native network
owner and a common-reference property closure; no placeholder test is used as
evidence for those calculations.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_headspace import HeadspaceRequest, HeadspaceWindow
from pipeline_integrity.headspace_drainage import (
    HeadspaceDrainageRequest,
    evaluate_headspace_drainage,
)
from pipeline_integrity.deposition_inventory import DepositInventoryRequest, DepositWindow
from pipeline_integrity.hydrate_kinetics import (
    HydrateEquilibriumFugacity,
    HydrateKineticWindow,
    HydrateKineticsRequest,
    bind_hydrate_deposit_inventory,
    evaluate_hydrate_kinetics,
)
from pipeline_integrity.tank_drainage import DrainageRequest
from pipeline_integrity.tanks import AxisymmetricTank


R_GAS = 287.0
CV = 718.0
T0 = 300.0
P0 = 150_000.0
INITIAL_LIQUID_VOLUME = 1.0


def coupled_case(*, maximum_step_s, duration_s, sample_step_s, stop_delta_m3=None):
    evidence = EvidenceRef(
        id="authored_case",
        citation="Authored constant-property ideal-gas coupling reference",
        origin="synthetic",
        sha256="a" * 64,
    )
    geometry = AxisymmetricTank(
        inner_diameter_m=1.0,
        straight_length_m=2.0,
        horizontal=False,
        evidence_id=evidence.id,
    )
    capacity = math.pi / 4.0 * 2.0
    initial_gas_volume = capacity - INITIAL_LIQUID_VOLUME
    initial_gas_mass = P0 * initial_gas_volume / (R_GAS * T0)
    drainage = DrainageRequest(
        id="liquid_outflow",
        asset_id="tank_1",
        clock_id="elapsed_s",
        geometry=geometry,
        initial_volume_m3=INITIAL_LIQUID_VOLUME,
        stop_volume_m3=(
            INITIAL_LIQUID_VOLUME - stop_delta_m3 if stop_delta_m3 is not None else 0.8
        ),
        duration_s=duration_s,
        maximum_step_s=maximum_step_s,
        tank_gas_pressure_pa_abs=P0,
        downstream_pressure_pa_abs=100_000.0,
        tank_bottom_minus_outlet_elevation_m=0.0,
        pipe_length_m=5.0,
        pipe_diameter_m=0.02,
        roughness_m=4.5e-5,
        minor_loss_coefficient=0.0,
        density_kg_m3=1000.0,
        viscosity_pa_s=1.0e-3,
        maximum_volume_flow_m3_s=0.01,
        transition_policy="use_declared_correlation",
        quasi_steady_nonflashing_single_phase_reviewed=True,
        evidence_ids=(evidence.id,),
    )
    headspace = HeadspaceRequest(
        id="fixed_composition_gas",
        asset_id=drainage.asset_id,
        clock_id=drainage.clock_id,
        volume_m3=initial_gas_volume,
        gas_constant_J_kg_K=R_GAS,
        cv_J_kg_K=CV,
        initial_mass_kg=initial_gas_mass,
        initial_temperature_K=T0,
        maximum_step_s=maximum_step_s,
        windows=(
            HeadspaceWindow(
                id="closed_adiabatic_gas",
                duration_s=duration_s,
                inlet_temperature_K=T0,
                ambient_temperature_K=T0,
            ),
        ),
        evidence_ids=(evidence.id,),
    )
    request = HeadspaceDrainageRequest(
        id="coupled_adiabatic_drain",
        drainage=drainage,
        headspace=headspace,
        coupling_evidence_id=evidence.id,
        rigid_shell_and_interface_volume_displacement_reviewed=True,
        ideal_gas_fixed_composition_and_constant_liquid_properties_reviewed=True,
        minimum_forward_driving_pressure_pa=1000.0,
        sample_step_s=sample_step_s,
        relative_tolerance=1.0e-10,
        absolute_volume_tolerance_m3=1.0e-11,
        absolute_gas_mass_tolerance_kg=1.0e-11,
        absolute_gas_energy_tolerance_J=1.0e-6,
        absolute_energy_balance_tolerance_J=1.0e-5,
        relative_energy_balance_tolerance=1.0e-9,
    )
    context = RunContext(
        asset_id=drainage.asset_id,
        scenario_id="synthetic_reference",
        clock_id=drainage.clock_id,
        origin="simulation",
        evidence=(evidence,),
    )
    return request, context, initial_gas_volume, initial_gas_mass


def assert_poisson_state(row, *, initial_volume, initial_mass):
    gamma = 1.0 + R_GAS / CV
    volume = row["gas_volume_m3"]
    expected_temperature = T0 * (initial_volume / volume) ** (gamma - 1.0)
    expected_pressure = P0 * (initial_volume / volume) ** gamma
    assert row["gas_mass_kg"] == pytest.approx(initial_mass, abs=1.0e-11)
    assert row["temperature_K"] == pytest.approx(expected_temperature, rel=2.0e-8)
    assert row["pressure_pa_abs"] == pytest.approx(expected_pressure, rel=2.0e-8)
    expected_energy = initial_mass * CV * expected_temperature
    assert row["gas_internal_energy_J"] == pytest.approx(expected_energy, rel=2.0e-8)
    assert row["gas_internal_energy_J"] - initial_mass * CV * T0 == pytest.approx(
        row["gas_boundary_energy_J"]["volume_work_into_gas_J"], abs=3.0e-5
    )


@pytest.mark.parametrize("maximum_step_s", [1.0, 0.5, 0.25])
def test_coupled_closed_adiabatic_headspace_matches_poisson_law_and_conserved_ledgers(maximum_step_s):
    request, context, gas_volume_0, gas_mass_0 = coupled_case(
        maximum_step_s=maximum_step_s,
        duration_s=20.0,
        sample_step_s=1.0,
    )
    result = evaluate_headspace_drainage(request, context)

    assert result["status"] == "computed_diagnostic"
    assert result["completed_requested_duration"] is True
    assert result["reverse_flow_implemented"] is False
    for row in result["states"]:
        assert_poisson_state(row, initial_volume=gas_volume_0, initial_mass=gas_mass_0)
        assert row["liquid_volume_m3"] + row["discharged_volume_m3"] == pytest.approx(
            INITIAL_LIQUID_VOLUME, abs=2.0e-10
        )
        assert abs(row["gas_energy_residual_J"]) <= 3.0e-5
        assert abs(row["gas_mass_residual_kg"]) <= 2.0e-10
        assert abs(row["liquid_volume_residual_m3"]) <= 2.0e-10
    assert result["states"][-1]["gas_volume_m3"] > gas_volume_0
    assert result["events"] == []


def test_coupled_drainage_stop_volume_event_preserves_exact_threshold_and_gas_reference():
    stop_delta = 5.0e-4
    request, context, gas_volume_0, gas_mass_0 = coupled_case(
        maximum_step_s=0.1,
        duration_s=10.0,
        sample_step_s=0.25,
        stop_delta_m3=stop_delta,
    )
    result = evaluate_headspace_drainage(request, context)

    assert result["status"] == "computed_diagnostic"
    assert result["completed_requested_duration"] is False
    assert [event["kind"] for event in result["events"]] == ["stop_liquid_volume"]
    final = result["states"][-1]
    assert final["liquid_volume_m3"] == pytest.approx(
        INITIAL_LIQUID_VOLUME - stop_delta, abs=2.0e-10
    )
    assert_poisson_state(final, initial_volume=gas_volume_0, initial_mass=gas_mass_0)
    assert abs(final["liquid_volume_residual_m3"]) <= 2.0e-10


def test_hydrate_deposition_receipt_maps_actual_phase_mass_to_existing_bore_inventory():
    review = EvidenceRef(
        id="kinetic_review",
        citation="Authored first-order hydrate phase-transfer case",
        origin="synthetic",
        sha256="d" * 64,
    )
    request = HydrateKineticsRequest(
        id="hydrate_phase_transfer",
        asset_id="reactor_pipe",
        clock_id="elapsed_s",
        guest_fluid="Methane",
        energy_reference_id="common_reference",
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
        reference_guest_pressure_Pa_abs=1.0e5,
        water_reference_internal_energy_J_mol=0.0,
        formation_internal_energy_release_at_reference_J_mol=50_000.0,
        formation_coefficient_at_reference_mol_m2_s_Pa=0.0,
        formation_activation_energy_J_mol=0.0,
        dissociation_coefficient_at_reference_mol_m2_s_Pa=0.0,
        dissociation_activation_energy_J_mol=0.0,
        initial_accessible_water_interface_area_m2=1.0,
        suspended_specific_reactive_area_m2_mol=1.0,
        deposited_specific_reactive_area_m2_mol=1.0,
        equilibrium=HydrateEquilibriumFugacity(
            temperature_K=(270.0, 290.0), guest_fugacity_Pa=(1.0e5, 1.0e5), evidence_id=review.id
        ),
        windows=(
            HydrateKineticWindow(
                id="kinetic_window",
                duration_s=2.0,
                bath_temperature_K=280.0,
                deposition_rate_constant_s_inv=0.7,
                erosion_rate_constant_s_inv=0.2,
                evidence_id=review.id,
            ),
        ),
        maximum_step_s=0.5,
        absolute_extent_residual_tolerance_mol=1.0e-10,
        relative_extent_residual_tolerance=1.0e-9,
        pressure_support_Pa_abs=(1.0e4, 1.0e7),
        material_and_kinetic_evidence_ids=(review.id,),
        pure_guest_common_reference_and_fixed_condensed_densities_reviewed=True,
        interfacial_fugacity_and_calibrated_effective_kinetics_reviewed=True,
        shrinking_water_domains_and_reactive_hydrate_area_reviewed=True,
        bath_and_first_order_deposition_erosion_reviewed=True,
    )
    source_context = RunContext(
        asset_id=request.asset_id,
        scenario_id="synthetic_phase_transfer",
        clock_id=request.clock_id,
        origin="simulation",
        evidence=(review,),
    )
    source = evaluate_hydrate_kinetics(request, source_context)
    assert source["status"] == "computed_diagnostic", source.get("failure")

    diameter = math.sqrt(4.0 * request.total_volume_m3 / math.pi)
    initial_deposit_mass_kg = source["states"][0]["deposited_mass_kg"]
    initial_deposit_thickness_m = diameter / 2.0 - math.sqrt(
        (diameter / 2.0) ** 2
        - initial_deposit_mass_kg
        / (math.pi * request.hydrate_density_kg_m3)
    )
    template = DepositInventoryRequest(
        id="existing_bore_inventory",
        asset_id=request.asset_id,
        clock_id=request.clock_id,
        clean_inner_diameter_m=diameter,
        length_m=1.0,
        deposit_density_kg_m3=request.hydrate_density_kg_m3,
        initial_thickness_m=initial_deposit_thickness_m,
        minimum_open_diameter_m=0.5 * diameter,
        windows=(
            DepositWindow(
                id="placeholder_only",
                duration_s=2.0,
                deposition_kg_s=0.0,
                removal_kg_s=0.0,
                evidence_id=review.id,
            ),
        ),
        evidence_ids=(review.id,),
    )
    downstream_context = source_context.model_copy(
        update={
            "evidence": (
                review,
                EvidenceRef(
                    id="kinetic_source_receipt",
                    citation="Hash pin for the calculated source trajectory",
                    origin="simulation",
                    sha256=source["record_digest"],
                ),
                EvidenceRef(
                    id="reactor_to_bore_mapping",
                    citation="Authored reactor and bore coordinate mapping review",
                    origin="synthetic",
                    sha256="e" * 64,
                ),
            )
        }
    )
    derived, retained_context, receipt = bind_hydrate_deposit_inventory(
        source,
        template,
        downstream_context,
        source_evidence_id="kinetic_source_receipt",
        mapping_evidence_id="reactor_to_bore_mapping",
        cylindrical_reactor_and_deposit_geometry_reviewed=True,
    )

    projected = receipt["projection"]
    kinetic_masses = [row["deposited_mass_kg"] for row in source["states"]]
    bore_masses = [row["deposit_mass_kg"] for row in projected["states"]]
    assert len(bore_masses) == len(kinetic_masses)
    mass_tolerance = (
        source["hydrate_molar_mass_kg_mol"]
        * request.absolute_extent_residual_tolerance_mol
        + receipt["cumulative_actual_deposition_equation_residual_allowance_kg"]
        + request.relative_extent_residual_tolerance
        * max(1.0, max(kinetic_masses))
    )
    assert bore_masses == pytest.approx(kinetic_masses, abs=mass_tolerance)
    assert receipt["maximum_endpoint_mass_difference_kg"] <= mass_tolerance
    assert receipt["projection_mass_adjustment_applied"] is False
    assert receipt["within_step_rate_shape_inferred"] is False
    assert retained_context.asset_id == derived.asset_id == request.asset_id
