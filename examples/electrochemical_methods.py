"""Original declared corrosion/protection inputs; no prescribed design or field coefficients."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.electrochemical_exchange import METHOD_REVIEWS, ElectrochemicalRequest


def make_cases():
    context = RunContext(
        asset_id="authored_pipe",
        scenario_id="electrochemical_references",
        clock_id="declared_exposure_seconds",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original mathematical corrosion/protection reference cases; material/design values are declared assumptions",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    inputs = {
        "corrosion.coupon_loss": (
            "not_applicable",
            dict(mass_loss_kg=0.001, exposed_area_m2=0.01, density_kg_m3=7850.0, duration_s=1000.0),
        ),
        "corrosion.faraday": (
            "anodic_dissolution",
            dict(
                current_density_A_m2=0.2,
                molar_mass_kg_mol=0.055845,
                electrons_per_atom=2.0,
                density_kg_m3=7850.0,
                current_efficiency=0.9,
                duration_s=100000.0,
            ),
        ),
        "corrosion.charge_to_loss": (
            "anodic_dissolution",
            dict(
                anodic_charge_C=9.648533212331001,
                equivalent_weight_kg_mol_electron=0.0279225,
                density_kg_m3=7850.0,
                current_efficiency=1.0,
            ),
        ),
        "corrosion.stern_geary": (
            "conditional_corrosion_current_estimate",
            dict(
                anodic_tafel_slope_V_decade=0.12,
                cathodic_tafel_slope_V_decade=0.12,
                area_normalized_resistance_ohm_m2=0.02,
            ),
        ),
        "corrosion.galvanic_linear": (
            "net_external_electrode_current",
            dict(
                anodic_free_potential_V=-0.7,
                cathodic_free_potential_V=-0.69,
                anodic_area_m2=0.1,
                cathodic_area_m2=0.2,
                anodic_polarization_resistance_ohm_m2=0.2,
                cathodic_polarization_resistance_ohm_m2=0.1,
                electrolyte_resistance_ohm=0.5,
                maximum_linear_overpotential_V=0.02,
            ),
        ),
        "protection.coating_coverage": (
            "not_applicable",
            dict(area_m2=100.0, dry_thickness_m=0.0002, volume_solids_fraction=0.5, transfer_efficiency=0.7),
        ),
        "protection.cathodic_budget": (
            "protective_demand",
            dict(
                time_s=[0.0, 4.0],
                protected_area_m2=10.0,
                bare_surface_fraction=[0.0, 1.0],
                bare_surface_current_density_A_m2=[1.0, 0.0],
                additional_current_drain_A=[0.0, 0.0],
                anode_electrochemical_capacity_C_kg=100000.0,
                anode_utilization_fraction=0.8,
                single_anode_mass_kg=0.1,
                single_anode_minimum_available_current_A=1.0,
                installed_anodes=3,
            ),
        ),
    }
    cases = {}
    for method, (meaning, parameters) in inputs.items():
        cases[method] = ElectrochemicalRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="declared_surface",
            clock_id=context.clock_id,
            condition_id="authored_condition",
            material_id="declared_material_or_coating",
            environment_id="declared_laboratory_or_application_basis",
            current_meaning=meaning,
            potential_reference_id="same_declared_reference"
            if method == "corrosion.galvanic_linear"
            else None,
            parameters=parameters,
            applicability_reviews=dict.fromkeys(METHOD_REVIEWS[method], True),
            evidence_ids=("reference",),
        )
    return cases, context
