"""Batch finite-volume and dilute-settling independent reference verification."""

import math

import pytest

from pipeline_integrity.batch_transport import BatchComposition, BatchInletStep, BatchTransportRequest
from pipeline_integrity.batch_transport_solver import (
    BatchLiquidComponent,
    ConservativeBatchConfiguration,
    ConservativeBatchRequest,
    evaluate_conservative_batch_transport,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.particle_settling import (
    ParticleSettlingRequest,
    SettlingParticleClass,
    evaluate_particle_settling,
)


def context():
    return RunContext(asset_id="unit", scenario_id="reference", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored conservative transport reference",
            origin="synthetic", sha256="a" * 64),))


def batch_case(cells, step, *, uniform=False):
    initial = {"A": 0.25, "B": 0.75} if uniform else {"A": 0.0, "B": 1.0}
    inlet = initial if uniform else {"A": 1.0, "B": 0.0}
    transport = BatchTransportRequest(
        id="batch", asset_id="unit", location_id="pipe", clock_id="seconds", route_length_m=1.0,
        cross_section_area_m2=1.0, duration_s=0.5, component_ids=("A", "B"),
        initial_composition=BatchComposition(fraction_by_component=initial),
        inlet_steps=(BatchInletStep(time_s=0.0, volume_flow_m3_s=0.2,
            composition=BatchComposition(fraction_by_component=inlet), temperature_K=300.0, pressure_Pa_abs=1e5),),
        property_basis_id="constant_components", dispersion_model_id="zero_dispersion",
        geometry_evidence_id="review", boundary_evidence_id="review", property_evidence_id="review",
        absolute_mass_balance_tolerance_kg=1e-7, relative_mass_balance_tolerance=1e-10,
        maximum_mass_numerical_adjustment_kg=1e-8,
        thermal_mode="energy_coupled" if uniform else "prescribed_temperature",
        thermal_model_id="sensible_energy" if uniform else None, thermal_evidence_id="review" if uniform else None,
        absolute_energy_balance_tolerance_J=1e-5 if uniform else None,
        relative_energy_balance_tolerance=1e-10 if uniform else None,
        maximum_energy_numerical_adjustment_J=1e-6 if uniform else None,
    )
    solver = ConservativeBatchConfiguration(
        id="fv", property_basis_id="constant_components", dispersion_model_id="zero_dispersion",
        thermal_model_id="sensible_energy" if uniform else None,
        components=(BatchLiquidComponent(id="A", density_kg_m3=800.0 if uniform else 1000.0,
            heat_capacity_J_kg_K=2000.0, dynamic_viscosity_Pa_s=0.001, evidence_id="review"),
            BatchLiquidComponent(id="B", density_kg_m3=1000.0, heat_capacity_J_kg_K=4000.0,
                dynamic_viscosity_Pa_s=0.004, evidence_id="review")),
        axial_dispersion_m2_s=0.0, initial_temperature_K=300.0, initial_pressure_Pa_abs=1e5,
        minimum_temperature_K=290.0, maximum_temperature_K=310.0, cells=cells,
        maximum_step_s=step, ambient_temperature_K=300.0, evidence_ids=("review",),
        additive_incompressible_volumes_and_mass_fraction_basis_reviewed=True,
        constant_pure_component_properties_and_log_viscosity_mixing_reviewed=True,
        prescribed_flow_pressure_and_dispersion_scope_reviewed=True,
        mechanical_energy_absent_or_in_imposed_heat_reviewed=True,
    )
    return ConservativeBatchRequest(transport=transport, solver=solver)


def batch_native(request):
    record = evaluate_conservative_batch_transport(request, context())
    assert record["status"] == "computed", record["result"]
    native = record["result"]["native"]
    for component in ("A", "B"):
        ledger = native["component_mass_ledger_kg"][component]
        residual = ledger["initial"] + ledger["inlet"] - ledger["outlet"] - ledger["final"] + ledger["numerical_adjustment"]
        assert abs(residual) < 1e-7
    return native


def test_batch_uniform_unequal_density_composition_and_sensible_energy_remain_stationary():
    native = batch_native(batch_case(8, 0.125, uniform=True))
    density = 1.0 / (0.25 / 800.0 + 0.75 / 1000.0)
    viscosity = math.exp(0.25 * math.log(0.001) + 0.75 * math.log(0.004))
    assert native["fraction_by_component"]["A"] == pytest.approx([0.25] * len(native["density_kg_m3"]), abs=1e-12)
    assert native["density_kg_m3"] == pytest.approx([density] * len(native["density_kg_m3"]), rel=1e-12)
    assert native["dynamic_viscosity_Pa_s"] == pytest.approx([viscosity] * len(native["density_kg_m3"]), rel=1e-12)
    assert native["temperature_K"] == pytest.approx([300.0] * len(native["density_kg_m3"]), abs=1e-10)
    ledger = native["energy_ledger_J"]
    expected = density * 3500.0 * (300.0 - 273.15)
    assert ledger["initial"] == pytest.approx(expected, rel=1e-12)
    assert ledger["final"] == pytest.approx(expected, rel=1e-12)
    assert ledger["inlet"] == pytest.approx(ledger["outlet"], abs=1e-6)
    assert ledger["heat_transfer"] == ledger["shaft_work"] == 0.0


def test_batch_step_boundary_first_cell_matches_semidiscrete_exponential_and_first_order_time_limit():
    # With zero axial dispersion, the first cell independently obeys
    # dphi/dt = (Q/Vcell)*(1-phi), initially zero.
    rate, duration = 0.2 * 8, 0.5
    continuum = 1.0 - math.exp(-rate * duration)
    errors = []
    for step in (0.125, 0.0625, 0.03125):
        native = batch_native(batch_case(8, step))
        actual = native["final_cell_component_volume_fractions"][0][0]
        discrete = 1.0 - (1.0 + rate * step) ** (-round(duration / step))
        assert actual == pytest.approx(discrete, abs=1e-12)
        errors.append(abs(actual - continuum))
    assert errors[0] > errors[1] > errors[2] > 0.0
    assert 1.7 < errors[0] / errors[1] < 2.2
    assert 1.7 < errors[1] / errors[2] < 2.2


def test_batch_advected_front_spatial_refinement_approaches_exact_translation_without_losing_component_mass():
    errors = []
    for cells in (20, 40, 80):
        dx = 1.0 / cells
        native = batch_native(batch_case(cells, dx / (4.0 * 0.2)))
        fractions = native["final_cell_component_volume_fractions"]
        # Exact advection translates the initial discontinuity to x=u*t=0.1m.
        exact = [max(0.0, min((index + 1) * dx, 0.1) - index * dx) / dx for index in range(cells)]
        errors.append(math.fsum(abs(row[0] - value) * dx for row, value in zip(fractions, exact)))
        reconstructed_A = math.fsum(row[0] for row in fractions) * dx * 1000.0
        assert native["component_mass_ledger_kg"]["A"]["final"] == pytest.approx(reconstructed_A, abs=1e-8)
        assert native["mass_ledger_kg"]["final"] == pytest.approx(1000.0, abs=1e-8)
    # An upwind approximation of a jump has numerical diffusion; its L1 error
    # must shrink on refinement, without asserting a smooth-field order here.
    assert errors[0] > errors[1] > errors[2] > 0.0


@pytest.mark.parametrize("diameter", (1e-5, 2e-5))
def test_dilute_stokes_settling_exact_collector_mass_and_profile_translation_are_grid_invariant(diameter):
    viscosity, fluid_density, particle_density = 0.001, 1000.0, 2500.0
    speed = (particle_density - fluid_density) * 9.80665 * diameter**2 / (18.0 * viscosity)
    captured = []
    profiles = []
    for cells in (2, 4):
        request = ParticleSettlingRequest(
            id="settling", asset_id="unit", clock_id="seconds", column_area_m2=2.0,
            height_edges_m=tuple(i / cells for i in range(cells + 1)),
            carrier_density_kg_m3=fluid_density, carrier_viscosity_Pa_s=viscosity,
            particles=(SettlingParticleClass(id="solid", diameter_m=diameter, density_kg_m3=particle_density,
                initial_concentration_kg_m3=(1.0, 3.0) if cells == 2 else (1.0, 1.0, 3.0, 3.0), evidence_id="review"),),
            elapsed_sample_time_s=(0.0, 0.25 / speed, 0.75 / speed, 1.1 / speed), drag_method="Stokes",
            maximum_reviewed_solids_volume_fraction=0.01,
            dilute_noninteracting_spheres_quiescent_newtonian_carrier_reviewed=True,
            terminal_regime_and_external_absorbing_collector_reviewed=True,
            geometry_and_carrier_evidence_ids=("review",), absolute_terminal_force_tolerance_N=1e-16,
        )
        record = evaluate_particle_settling(request, context())
        assert record["status"] == "computed_diagnostic"
        particle = record["particle_classes"][0]
        assert particle["terminal_velocity_m_s"] == pytest.approx(speed, rel=1e-10)
        assert particle["drag_coefficient"] == pytest.approx(24.0 / particle["particle_Reynolds_number"], rel=1e-10)
        assert particle["initial_mass_kg"] == pytest.approx(4.0)
        captured.append([row["captured_mass_kg"] for row in particle["states"]])
        profiles.append(particle["states"])
        for row in particle["states"]:
            assert row["captured_mass_kg"] + row["suspended_mass_kg"] == pytest.approx(4.0, abs=1e-12)
            assert all(value >= 0.0 for value in row["concentration_kg_m3"])
    assert captured[0] == pytest.approx([0.0, 0.5, 2.5, 4.0], abs=1e-10)
    assert captured[1] == pytest.approx(captured[0], abs=1e-10)
    assert profiles[0][1]["concentration_kg_m3"] == pytest.approx([2.0, 1.5], abs=1e-10)
    for coarse, fine in zip(profiles[0], profiles[1]):
        concentration = fine["concentration_kg_m3"]
        assert coarse["concentration_kg_m3"] == pytest.approx(
            [(concentration[0] + concentration[1]) / 2, (concentration[2] + concentration[3]) / 2], abs=1e-10)
