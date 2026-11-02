"""Authored atom-molar inputs; material and boundary values are hypothetical."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.hydrogen_transport import (
    EQUILIBRIUM,
    FLUX_SIGNS,
    INPUT_UNITS,
    KINETIC,
    MEASURES,
    REVIEWS,
    SEMI,
    HydrogenTransportRequest,
)


def make_cases():
    context = RunContext(
        asset_id="authored_hydrogen_slab",
        scenario_id="transport_numerical_reference",
        clock_id="physical_elapsed_seconds",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical atom-molar transport inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    evidence = {
        "source_id": "authored",
        "citation": context.evidence[0].citation,
        "origin": "synthetic",
        "license_id": "MIT",
        "sha256": None,
    }
    population = {
        "trap_id": "authored_zero_binding_sites",
        "material_condition_id": "authored_material",
        "reference_lattice_id": "authored_lattice",
        "site_density_mol_m3": 100.0,
        "binding_energy_J_mol": 0.0,
        "maximum_supported_occupancy": 0.5,
        "evidence": [evidence],
    }
    parameters = {
        SEMI: {
            "diffusivity_m2_s": 1e-8,
            "exposure_s": 4.0,
            "depth_m": [0.0, 0.0002, 0.0006, 0.0002],
            "initial_concentration_mol_m3": 10.0,
            "surface_concentration_mol_m3": 110.0,
        },
        EQUILIBRIUM: {
            "material_condition_id": "authored_material",
            "reference_lattice_id": "authored_lattice",
            "total_hydrogen_atoms_mol_m3": 110.0,
            "lattice_site_density_mol_m3": 1000.0,
            "lattice_diffusivity_m2_s": 1e-8,
            "temperature_K": 300.0,
            "maximum_supported_lattice_occupancy": 0.5,
            "traps": [population],
            "local_equilibrium_confirmed": True,
            "independent_single_occupancy_sites_confirmed": True,
            "fixed_trap_population_and_uniform_temperature_confirmed": True,
        },
        KINETIC: {
            "material_condition_id": "authored_material",
            "reference_lattice_id": "authored_lattice",
            "elapsed_time_origin_id": "authored_exposure_zero",
            "thickness_m": 0.001,
            "cell_count": 8,
            "lattice_site_density_mol_m3": 1000.0,
            "initial_lattice_hydrogen_atoms_mol_m3": [100.0] * 8,
            "elapsed_time_s": [0.0, 1.0, 10.0],
            "uniform_temperature_K": [300.0] * 3,
            "lattice_diffusivity_m2_s": [1e-8] * 3,
            "left_boundary": {"boundary_id": "left_surface", "kind": "no_flux", "evidence": [evidence]},
            "right_boundary": {"boundary_id": "right_surface", "kind": "no_flux", "evidence": [evidence]},
            "traps": [
                {
                    "population": population,
                    "rate_model": "binding_energy_ratio",
                    "capture_frequency_per_s": [0.02] * 3,
                    "release_frequency_per_s": None,
                    "initial_occupancy": [0.1] * 8,
                    "independent_rate_histories_reviewed": False,
                    "rate_evidence": [evidence],
                }
            ],
            "maximum_supported_lattice_occupancy": 0.5,
            "maximum_supported_total_trap_to_lattice_site_ratio": 0.2,
            "planar_geometry_reviewed": True,
            "fixed_populations_and_uniform_temperature_reviewed": True,
            "independent_single_occupancy_and_dilute_trap_regime_reviewed": True,
            "negligible_stress_gradient_reviewed": True,
            "detailed_balance_rate_ratio_reviewed": True,
            "relative_tolerance": 0.001,
            "absolute_occupancy_tolerance": 1e-8,
            "maximum_step_s": 1.0,
            "maximum_step_attempts": 20000,
            "maximum_relative_mass_balance_error": 1e-6,
            "store_profiles": True,
        },
    }
    requests = {}
    for method, p in parameters.items():
        requests[method] = HydrogenTransportRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="authored_material_point_or_slab",
            clock_id=context.clock_id,
            material_condition_id="authored_material",
            reference_lattice_id="authored_lattice",
            population_id="authored_population",
            parameter_support_id="authored_parameter_support",
            geometry_id="authored_half_space" if method == SEMI else "authored_planar_slab",
            elapsed_time_origin_id="authored_exposure_zero",
            concentration_basis="hydrogen_atoms_mol_m3",
            concentration_measure=MEASURES[method],
            binding_energy_reference="not_used" if method == SEMI else "positive_relative_to_lattice_site",
            flux_sign_convention=FLUX_SIGNS[method],
            declared_temperature_K=300.0,
            temperature_support_K=(290.0, 310.0),
            depth_point_ids=tuple(f"depth-row-{i}" for i in range(4)) if method == SEMI else (),
            cell_ids=tuple(f"cell-{i}" for i in range(8)) if method == KINETIC else (),
            time_knot_ids=("time-0", "time-1", "time-2") if method == KINETIC else (),
            parameters=p,
            input_units=INPUT_UNITS[method],
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "authored"),
            evidence_ids=("authored",),
        )
    return requests, context
