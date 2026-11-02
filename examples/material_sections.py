"""Authored pressure and heat-conduction inputs for separately installed material-engine models."""

from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext
from pipeline_integrity.material_exchange import PressureSection


def context():
    return RunContext(
        asset_id="reference-wall",
        scenario_id="authored-reference",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Explicit geometry, loading and artificial properties",
                origin="synthetic",
            ),
        ),
    )


def pressure_case(*, count=33, closed=True, pi=2e6, pe=1e5):
    radii = tuple(0.05 + 0.01 * i / (count - 1) for i in range(count))
    return PressureSection(
        asset_id="reference-wall",
        location_id="annulus",
        clock_id="elapsed",
        time_s=0.0,
        inner_radius_m=radii[0],
        outer_radius_m=radii[-1],
        evaluation_radii_m=radii,
        internal_pressure=Pressure(value=pi, unit="Pa", reference="absolute"),
        external_pressure=Pressure(value=pe, unit="Pa", reference="absolute"),
        pressure_loaded_closed_ends=closed,
        geometry_evidence_id="authored",
        pressure_evidence_id="authored",
    )


def heat_case(*, count=10, radial=False):
    a, b = (0.05, 0.07) if radial else (0.0, 0.02)
    edges = [a + (b - a) * i / count for i in range(count + 1)]
    return dict(
        geometry="cylindrical" if radial else "planar",
        cell_edges_m=edges,
        **({"cylinder_length_m": 1.0} if radial else {"planar_area_m2": 1.0}),
        materials=[
            dict(
                material_id="authored-solid",
                temperature_K=[280.0, 400.0],
                conductivity_W_m_K=[10.0, 10.0],
                volumetric_heat_capacity_J_m3_K=[2e6, 2e6],
            )
        ],
        cell_material_ids=["authored-solid"] * count,
        initial_temperature_K=[300.0] * count,
        time_s=[0.0, 1.0, 2.0],
        left_boundary=dict(boundary_id="left", kind="adiabatic"),
        right_boundary=dict(boundary_id="right", kind="adiabatic"),
        maximum_step_s=0.1,
        one_dimensional_geometry_reviewed=True,
        material_property_support_reviewed=True,
        boundary_conditions_reviewed=True,
        phase_change_absent_or_included_in_capacity_reviewed=True,
    )


def thermal_stress_case():
    return dict(
        profile_id="authored-planar-points",
        position_m=[0.0, 0.005, 0.01, 0.015, 0.02],
        temperature_K=[320.0] * 5,
        reference_temperature_K=300.0,
        secant_thermal_expansion_1_K=[1e-5] * 5,
        elastic_modulus_Pa=[2e11] * 5,
        poisson_ratio=[0.3] * 5,
        elastic_limit_Pa=[2.5e8] * 5,
        plane_condition="uniaxial_plane_stress",
        control="resultant_controlled",
        membrane_force_N_m=0.0,
        bending_moment_N=0.0,
        plane_sections_reviewed=True,
        linear_elastic_response_reviewed=True,
        property_profiles_reviewed=True,
        applied_resultants_or_kinematics_reviewed=True,
    )


if __name__ == "__main__":
    import json

    from pipeline_integrity.material_exchange import evaluate_pressure_section

    print(json.dumps(evaluate_pressure_section(pressure_case(), context()), indent=2))
