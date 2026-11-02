"""Independent thermal references for Nurion; no observation-derived parameters.

These fixtures test ideal-gas work, two thermal storage nodes, prescribed frost
growth and fixed-volume latent storage. Constitutive input values are authored
synthetic cases rather than fitted material or field validation claims.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.frost_growth import (
    FrostFluxWindow,
    FrostGrowthRequest,
    FrostMaterial,
    evaluate_frost_growth,
)
from pipeline_integrity.gas_headspace import HeadspaceRequest, HeadspaceWindow, evaluate_headspace
from pipeline_integrity.phase_material import PhaseMaterialRequest
from pipeline_integrity.spatial_phase_change import (
    PhaseVolumeCell,
    SpatialPhaseRequest,
    SpatialPhaseWindow,
    evaluate_spatial_phase_change,
)


def _context():
    return RunContext(
        asset_id="thermal_test", scenario_id="analytic_reference", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored independent conservation references",
                              origin="synthetic", sha256="d" * 64),),
    )


def _headspace(dt, *, heat=0.0, displacement=0.0):
    return HeadspaceRequest(
        id="ideal_gas", asset_id="thermal_test", clock_id="seconds", volume_m3=1.0,
        gas_constant_J_kg_K=287.0, cv_J_kg_K=718.0,
        initial_mass_kg=1.0, initial_temperature_K=300.0, maximum_step_s=dt,
        evidence_ids=("review",), windows=(HeadspaceWindow(
            id="constant_boundary", duration_s=20.0, inlet_temperature_K=300.0,
            heat_input_W=heat, ambient_temperature_K=300.0, volume_change_m3_s=displacement,
        ),),
    )


@pytest.mark.parametrize("heat", [-100.0, 100.0])
def test_closed_rigid_headspace_heat_matches_first_law_and_ideal_gas_at_every_step(heat):
    request = _headspace(1.0, heat=heat)
    record = evaluate_headspace(request, _context())
    assert record["status"] == "computed"
    for state in record["states"]:
        energy = 718.0 * 300.0 + heat * state["time_s"]
        assert state["internal_energy_J"] == pytest.approx(energy, abs=1e-7)
        assert state["mass_kg"] == 1.0
        assert state["temperature_K"] == pytest.approx(energy / 718.0, abs=1e-10)
        assert state["pressure_pa_abs"] == pytest.approx(287.0 * energy / 718.0, abs=1e-7)
    assert record["boundary_energy_ledger"]["direct_heat_J"] == pytest.approx(heat * 20.0, abs=1e-8)
    assert abs(record["ledger_energy_residual_J"]) < 1e-7
    assert abs(record["mass_residual_kg"]) < 1e-14


def test_headspace_adiabatic_compression_reproduces_poisson_law_and_fourth_order_refinement():
    errors = []
    gamma = 1 + 287.0 / 718.0
    final_volume = 0.6
    exact_temperature = 300.0 * final_volume ** (1 - gamma)
    exact_energy_change = 718.0 * (exact_temperature - 300.0)
    for dt in (2.0, 1.0, 0.5):
        record = evaluate_headspace(_headspace(dt, displacement=-0.02), _context())
        assert record["status"] == "computed"
        last = record["states"][-1]
        assert last["volume_m3"] == pytest.approx(final_volume, abs=1e-13)
        assert last["mass_kg"] == 1.0
        error = abs(last["temperature_K"] - exact_temperature)
        errors.append(error)
        assert record["boundary_energy_ledger"]["volume_work_into_gas_J"] == pytest.approx(
            last["internal_energy_J"] - 718.0 * 300.0, abs=1e-7,
        )
        assert abs(record["ledger_energy_residual_J"]) < 1e-7
        assert last["pressure_pa_abs"] == pytest.approx(
            287.0 * last["temperature_K"] / final_volume, abs=1e-7,
        )
    assert errors[1] <= errors[0] / 8 + 1e-9
    assert errors[2] <= errors[1] / 8 + 1e-9
    assert errors[-1] < 1e-5
    assert record["boundary_energy_ledger"]["volume_work_into_gas_J"] == pytest.approx(
        exact_energy_change, abs=0.01,
    )


def _frost(dt, *, deposition=0.0, initial_wall_temperature=270.0, balancing_heat=0.0):
    return FrostGrowthRequest(
        id="frost_reference", asset_id="thermal_test", clock_id="seconds", area_m2=1.0,
        material=FrostMaterial(
            density_kg_m3=100.0, solid_heat_capacity_J_kg_K=1000.0,
            liquid_heat_capacity_J_kg_K=4200.0, vapor_heat_capacity_J_kg_K=2000.0,
            conductivity_W_m_K=0.1, phase_reference_temperature_K=273.15,
            latent_sublimation_at_reference_J_kg=2_800_000.0,
            latent_fusion_at_reference_J_kg=334_000.0, evidence_id="review",
        ),
        initial_thickness_m=0.01, minimum_supported_thickness_m=0.001,
        maximum_supported_thickness_m=0.1, initial_frost_temperature_K=250.0,
        initial_substrate_temperature_K=initial_wall_temperature, substrate_heat_capacity_J_K=2000.0,
        minimum_supported_temperature_K=200.0, maximum_substrate_temperature_K=300.0,
        absolute_energy_tolerance_J=1e-6, relative_energy_tolerance=1e-10,
        geometry_and_midpoint_thermal_storage_reviewed=True,
        constant_properties_and_solid_only_storage_reviewed=True,
        fluxes_and_phase_change_energy_boundary_reviewed=True, evidence_ids=("review",),
        windows=(FrostFluxWindow(
            id="isolated", duration_s=20.0, maximum_step_s=dt,
            vapor_deposition_kg_m2_s=deposition,
            incoming_vapor_temperature_K=273.15, outgoing_vapor_temperature_K=273.15,
            outgoing_liquid_temperature_K=273.15, ambient_temperature_K=250.0,
            ambient_film_W_m2_K=0.0, substrate_reservoir_temperature_K=250.0,
            substrate_reservoir_conductance_W_K=0.0, frost_direct_heat_W=balancing_heat,
            evidence_id="review",
        ),),
    )


def test_isolated_frost_substrate_exchange_conserves_energy_and_converges_to_exact_exponential():
    # m=1 kg, Cf=1000 J/K, Cw=2000 J/K, half-layer R=.05 K/W.
    conductance = 20.0
    decay_rate = conductance * (1 / 1000.0 + 1 / 2000.0)
    equilibrium = (1000 * 250 + 2000 * 270) / 3000
    exact_difference = -20 * math.exp(-decay_rate * 20)
    errors = []
    for dt in (1.0, 0.5, 0.25):
        record = evaluate_frost_growth(_frost(dt), _context())
        assert record["status"] == "computed_diagnostic", record["failure"]
        energy0 = record["states"][0]["total_stored_energy_J"]
        for state in record["states"]:
            assert state["total_stored_energy_J"] == pytest.approx(energy0, abs=1e-6)
            assert state["frost_mass_kg"] == pytest.approx(1.0, abs=1e-14)
            weighted = (1000 * state["frost_temperature_K"] + 2000 * state["substrate_temperature_K"]) / 3000
            assert weighted == pytest.approx(equilibrium, abs=1e-10)
        final = record["states"][-1]
        difference = final["frost_temperature_K"] - final["substrate_temperature_K"]
        expected_discrete = -20 * (1 + decay_rate * dt) ** (-round(20 / dt))
        assert difference == pytest.approx(expected_discrete, abs=1e-9)
        errors.append(abs(difference - exact_difference))
        assert abs(record["energy_residual_J"]) <= 1e-6
    assert errors[1] < 0.6 * errors[0]
    assert errors[2] < 0.6 * errors[1]


@pytest.mark.parametrize("dt", [1.0, 0.25])
def test_isothermal_frost_growth_accounts_for_latent_input_and_added_mass_sensible_energy(dt):
    rate = 1e-4
    # Maintaining 250K requires removing latent input AND the sensible energy
    # difference between the incoming phase reference and newly stored solid.
    solid_enthalpy = 1000 * (250.0 - 273.15)
    direct_heat = rate * (solid_enthalpy - 2_800_000.0)
    record = evaluate_frost_growth(
        _frost(dt, deposition=rate, initial_wall_temperature=250.0, balancing_heat=direct_heat), _context(),
    )
    assert record["status"] == "computed_diagnostic", record["failure"]
    for state in record["states"]:
        mass = 1.0 + rate * state["time_s"]
        assert state["frost_mass_kg"] == pytest.approx(mass, abs=1e-13)
        assert state["thickness_m"] == pytest.approx(mass / 100, abs=1e-14)
        assert state["frost_temperature_K"] == pytest.approx(250.0, abs=1e-10)
        assert state["substrate_temperature_K"] == pytest.approx(250.0, abs=1e-10)
    expected_increment = rate * 20 * solid_enthalpy
    actual_increment = record["states"][-1]["total_stored_energy_J"] - record["states"][0]["total_stored_energy_J"]
    assert actual_increment == pytest.approx(expected_increment, abs=1e-6)
    ledger = record["energy_ledger"]
    assert ledger["vapor_enthalpy_in_J"] == pytest.approx(rate * 20 * 2_800_000, abs=1e-8)
    assert ledger["direct_heat_J"] == pytest.approx(20 * direct_heat, abs=1e-8)
    assert abs(record["mass_residual_kg"]) < 1e-12
    assert abs(record["energy_residual_J"]) < 1e-6


@pytest.mark.parametrize("dt", [2.0, 1.0])
def test_spatial_single_cell_heat_crosses_latent_interval_with_exact_enthalpy(dt):
    material = PhaseMaterialRequest(
        id="phase", material_id="material", temperature_support_K=(250.0, 300.0),
        solidus_temperature_K=270.0, liquidus_temperature_K=280.0,
        solid_volumetric_heat_capacity_J_m3_K=1000.0,
        liquid_volumetric_heat_capacity_J_m3_K=1000.0, volumetric_latent_heat_J_m3=100_000.0,
        solid_conductivity_W_m_K=1.0, liquid_conductivity_W_m_K=1.0,
        maximum_enthalpy_table_error_J_m3=100.0,
        maximum_conductivity_interpolation_error_W_m_K=0.01,
        evidence_id="review", apparent_capacity_and_transition_interval_reviewed=True,
    )
    request = SpatialPhaseRequest(
        id="latent_heat", asset_id="thermal_test", clock_id="seconds", materials=(material,),
        cells=(PhaseVolumeCell(id="cell", material_id="material", volume_m3=1.0,
                              initial_temperature_K=260.0, evidence_id="review"),),
        windows=(SpatialPhaseWindow(id="heat", duration_s=20.0, maximum_step_s=dt,
                                    cell_heat_W=(4770.0,), evidence_id="review"),),
        absolute_cell_energy_tolerance_J=1e-5, relative_cell_energy_tolerance=1e-10,
        orthogonal_face_paths_and_fixed_volumes_reviewed=True,
        conduction_only_no_advection_or_volume_change_reviewed=True,
        boundary_conductances_and_heat_sources_reviewed=True,
    )
    record = evaluate_spatial_phase_change(request, _context())
    assert record["status"] == "computed_diagnostic", record["failure"]
    for row in record["states"]:
        temperature = row["temperature_K"][0]
        x = min(1.0, max(0.0, (temperature - 270.0) / 10.0))
        fraction = 3 * x**2 - 2 * x**3
        enthalpy = 1000 * (temperature - 270.0) + 100_000 * fraction
        expected_enthalpy = -10_000 + 4770 * row["time_s"]
        assert enthalpy == pytest.approx(expected_enthalpy, abs=0.001)
        assert row["total_enthalpy_J"] == pytest.approx(enthalpy, abs=1e-7)
        assert row["liquid_fraction"][0] == pytest.approx(fraction, abs=1e-10)
    assert record["states"][-1]["temperature_K"][0] == pytest.approx(277.0, abs=1e-6)
    assert record["states"][-1]["liquid_fraction"][0] == pytest.approx(0.784, abs=1e-7)
    assert record["boundary_heat_J"] == 0.0
    assert record["source_heat_J"] == pytest.approx(95_400.0, abs=1e-7)
    assert abs(record["energy_residual_J"]) < 0.001
