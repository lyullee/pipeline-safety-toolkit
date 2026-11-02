"""Small independent physics references for Nurion, not qualification evidence."""

import math

import numpy as np
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.dynamic_fracture import CrackGeometryEnergy, _geometry
from pipeline_integrity.elastic_shell_ring import ElasticShellRingRequest, _ring_discretization
from pipeline_integrity.hydrate_kinetics import (
    HydrateEquilibriumFugacity, HydrateKineticWindow, HydrateKineticsRequest,
    evaluate_hydrate_kinetics, hydrate_kinetics_signal_frames,
)
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient
from pipeline_integrity.vapor_cavity_moc import (
    VaporCavityMOCRequest, evaluate_vapor_cavity_moc, vapor_cavity_moc_signal_frames,
)


def test_crack_volume_and_elastic_work_gradients_use_physical_coordinate():
    table = CrackGeometryEnergy(id="geometry", advance_m=(0.0, 2.0), fluid_volume_m3=(1.0, 1.4),
        new_crack_area_m2=(0.0, 0.8), opening_area_m2=(0.0, 0.02),
        recoverable_elastic_energy_J=(1000.0, 600.0), new_crack_area_basis_id="two-surfaces", evidence_id="review")
    result = _geometry(table, 0.5)
    assert result["fluid_volume_m3"] == pytest.approx(1.1)
    assert result["recoverable_elastic_energy_J"] == pytest.approx(900.0)
    # A constant 100 Pa pressure supplies 20 J/m; stored elasticity releases 200 J/m.
    driving = (100.0 * result["fluid_volume_m3_per_m"] - result["recoverable_elastic_energy_J_per_m"]) / result["new_crack_area_m2_per_m"]
    assert driving == pytest.approx(550.0)
    with pytest.raises(ValueError, match="support"):
        _geometry(table, 2.1)


def test_hydrate_equilibrium_requires_aligned_strict_temperature_support():
    with pytest.raises(ValueError, match="increasing temperature"):
        HydrateEquilibriumFugacity(temperature_K=(280.0, 280.0), guest_fugacity_Pa=(1e6, 2e6), evidence_id="review")
    with pytest.raises(ValueError, match="aligned"):
        HydrateEquilibriumFugacity(temperature_K=(280.0, 290.0), guest_fugacity_Pa=(1e6, 2e6, 3e6), evidence_id="review")


def test_hydrate_zero_rates_isothermal_inventory_and_energy_are_stationary():
    request = HydrateKineticsRequest(
        id="stationary", asset_id="unit", clock_id="seconds", guest_fluid="Methane", energy_reference_id="reviewed-reference",
        total_volume_m3=0.01, minimum_gas_volume_m3=0.001, initial_temperature_K=280.0,
        initial_guest_gas_mol=1.0, initial_liquid_water_mol=10.0,
        initial_suspended_hydrate_mol=0.1, initial_deposited_hydrate_mol=0.1,
        water_per_guest_mol=5.75, water_molar_mass_kg_mol=0.01801528, water_density_kg_m3=1000.0,
        hydrate_density_kg_m3=900.0, water_molar_heat_capacity_J_mol_K=75.0,
        hydrate_molar_heat_capacity_J_mol_K=300.0, reference_temperature_K=280.0,
        reference_guest_pressure_Pa_abs=1e5, water_reference_internal_energy_J_mol=0.0,
        formation_internal_energy_release_at_reference_J_mol=50000.0,
        formation_coefficient_at_reference_mol_m2_s_Pa=0.0, formation_activation_energy_J_mol=0.0,
        dissociation_coefficient_at_reference_mol_m2_s_Pa=0.0, dissociation_activation_energy_J_mol=0.0,
        initial_accessible_water_interface_area_m2=1.0, suspended_specific_reactive_area_m2_mol=1.0,
        deposited_specific_reactive_area_m2_mol=1.0,
        equilibrium=HydrateEquilibriumFugacity(temperature_K=(270.0, 290.0), guest_fugacity_Pa=(1e6, 2e6), evidence_id="review"),
        windows=(HydrateKineticWindow(id="bath", duration_s=0.2, bath_temperature_K=280.0,
            deposition_rate_constant_s_inv=0.0, erosion_rate_constant_s_inv=0.0, evidence_id="review"),),
        maximum_step_s=0.1, pressure_support_Pa_abs=(1e4, 1e7), material_and_kinetic_evidence_ids=("review",),
        pure_guest_common_reference_and_fixed_condensed_densities_reviewed=True,
        interfacial_fugacity_and_calibrated_effective_kinetics_reviewed=True,
        shrinking_water_domains_and_reactive_hydrate_area_reviewed=True,
        bath_and_first_order_deposition_erosion_reviewed=True,
    )
    context = RunContext(asset_id="unit", scenario_id="stationary", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored zero-rate limiting fixture", origin="synthetic", sha256="c" * 64),))
    record = evaluate_hydrate_kinetics(request, context)
    assert record["status"] == "computed_diagnostic", record.get("failure")
    first = record["states"][0]
    for row in record["states"]:
        for name in ("guest_gas_mol", "liquid_water_mol", "total_hydrate_mol", "suspended_hydrate_mol", "deposited_hydrate_mol"):
            assert row[name] == pytest.approx(first[name], abs=1e-8)
        assert row["internal_energy_J"] == pytest.approx(first["internal_energy_J"], abs=1e-5)
    assert record["total_bath_heat_into_reactor_J"] == pytest.approx(0.0, abs=1e-5)
    frames, receipt = hydrate_kinetics_signal_frames(record, stream_id="hydrate")
    assert len(frames) == len(record["states"])
    assert receipt["source_record_digest"] == record["record_digest"]


def test_ring_uniform_radial_contraction_has_exact_area_and_membrane_energy():
    request = ElasticShellRingRequest(id="ring", asset_id="unit", clock_id="seconds", cross_section_id="section",
        coordinate_frame_id="xy", mean_reference_centerline_radius_m=1.0, wall_thickness_m=0.01,
        axial_strip_width_m=0.2, young_modulus_Pa=2e11, poisson_ratio=0.3,
        axial_constraint="plane_stress_ring", external_minus_internal_pressure_Pa=(0.0, 100.0),
        minimum_pressure_step_Pa=0.01, pressure_event_tolerance_Pa=0.01, native_backend_pins={},
        geometry_evidence_id="review", material_evidence_id="review", pressure_evidence_id="review", scope_review_evidence_id="review")
    ring = _ring_discretization(request, 256)
    contraction = 0.001
    coefficients = np.zeros(len(ring["labels"]))
    coefficients[0] = coefficients[2] = -contraction
    fields = ring["fields"](coefficients)
    assert fields[8] == pytest.approx(math.pi * (1 - contraction) ** 2, rel=1e-12)
    assert fields[9] == pytest.approx(math.pi * ring["ea"] * contraction ** 2, rel=1e-10)
    assert fields[10] == pytest.approx(0.0, abs=1e-15)
    assert np.asarray(fields[6]) == pytest.approx(np.full(256, -contraction), abs=1e-14)


def vapor_case():
    liquid = LiquidTransient(nodes=(
        LiquidNode(id="reservoir", elevation_m=0.0, initial_head_m=10.0, boundary="head", history=History(time_s=(0.0,), values=(10.0,))),
        LiquidNode(id="outlet", elevation_m=0.0, initial_head_m=10.0, boundary="withdrawal", history=History(time_s=(0.0,), values=(0.0,)))),
        pipes=(LiquidPipe(id="pipe", start_node="reservoir", end_node="outlet", length_m=100.0,
                         diameter_m=0.1, wave_speed_m_s=1000.0, darcy_friction_factor=0.0, initial_volume_flow_m3_s=0.0),),
        duration_s=0.1, requested_timestep_s=0.01, density_kg_m3=1000.0,
        ambient_pressure_pa_abs=101325.0, vapour_pressure_pa_abs=2000.0)
    request = VaporCavityMOCRequest(id="steady", asset_id="unit", clock_id="seconds", liquid=liquid,
        maximum_absolute_pressure_Pa=1e6, event_time_tolerance_s=1e-10, event_pressure_tolerance_Pa=1e-5,
        event_volume_tolerance_m3=1e-12, global_liquid_volume_tolerance_m3=1e-10,
        parameter_evidence_id="review", saturation_and_initial_void_evidence_id="review", discretization_review_evidence_id="review",
        pure_vapor_negligible_vapor_mass_and_constant_saturation_pressure_reviewed=True,
        characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed=True,
        small_void_fraction_acoustic_liquid_and_omitted_thermal_energy_reviewed=True)
    context = RunContext(asset_id="unit", scenario_id="steady", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored hydrostatic fixture", origin="synthetic", sha256="b" * 64),))
    return request, context


def test_vapor_moc_preserves_hydrostatic_equilibrium_and_signal_lineage():
    request, context = vapor_case()
    record = evaluate_vapor_cavity_moc(request, context)
    assert record["status"] == "computed_diagnostic", record.get("failure")
    for row in record["states"]:
        assert row["head_m"] == pytest.approx([10.0] * 11, abs=1e-10)
        assert row["absolute_pressure_Pa"] == pytest.approx([199391.5] * 11, abs=1e-5)
        assert row["cavity_volume_m3"] == pytest.approx([0.0] * 10, abs=1e-14)
    frames, receipt = vapor_cavity_moc_signal_frames(record, section_index=1, stream_id="pressure")
    assert [i for group in receipt["source_row_groups"] for i in group] == list(range(len(record["states"])))
    assert len(frames) == receipt["published_frame_count"]
    assert all(frame.values["pressure_pa_abs"] == pytest.approx(199391.5, abs=1e-5) for frame in frames)


def test_vapor_initial_void_grid_mismatch_and_unreviewed_request_are_rejected():
    request, context = vapor_case()
    with pytest.raises(ValueError, match="initial vapor volumes"):
        VaporCavityMOCRequest.model_validate(request.model_copy(update={"initial_cavity_volume_m3": (1e-8,)}).model_dump(mode="python"))
    with pytest.raises(ValueError, match="reviews"):
        evaluate_vapor_cavity_moc(request.model_copy(update={"characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed": False}), context)
