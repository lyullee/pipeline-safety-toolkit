"""Authored scalar Mode-I and ordered-cycle cases; no material or standard defaults are implied."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.fracture_exchange import (
    CrackGrowthRequest,
    CrackLoadBlock,
    FractureResistance,
    FractureReviews,
    GrowthCalibration,
    ModeIRequest,
)


def context():
    return RunContext(
        asset_id="reference-wall",
        scenario_id="authored-reference",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Authored crack, constant factor and artificial calibration",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )


def resistance():
    return FractureResistance(
        material_condition_id="authored-material",
        value_pa_sqrt_m=5e6,
        kind="synthetic_reference",
        definition="Artificial Mode-I resistance for code references",
        minimum_temperature_k=280.0,
        maximum_temperature_k=320.0,
        evidence_id="authored",
        applicability_reviewed=True,
    )


def reviews():
    return FractureReviews(
        geometry_and_stress_solution_reviewed=True,
        linear_elastic_regime_reviewed=True,
        loading_and_environment_reviewed=True,
    )


def make_mode_i():
    return ModeIRequest(
        asset_id="reference-wall",
        location_id="annulus",
        clock_id="elapsed",
        material_condition_id="authored-material",
        flaw_id="authored-crack",
        evidence_ids=("authored",),
        time_s=0.0,
        crack_depth_m=0.001,
        wall_thickness_m=0.01,
        effective_opening_stress_pa=4e7,
        opening_stress_definition="Authored uniform effective opening traction",
        geometry_factor=1.0,
        minimum_supported_depth_m=0.0005,
        maximum_supported_depth_m=0.004,
        temperature_k=293.15,
        resistance=resistance(),
        reviews=reviews(),
    )


def make_growth():
    return CrackGrowthRequest(
        asset_id="reference-wall",
        location_id="annulus",
        clock_id="elapsed",
        material_condition_id="authored-material",
        flaw_id="authored-crack",
        evidence_ids=("authored",),
        initial_depth_m=0.001,
        terminal_depth_m=0.004,
        wall_thickness_m=0.01,
        geometry_depth_m=(0.0005, 0.004),
        geometry_factor=(1.0, 1.0),
        blocks=(
            CrackLoadBlock(
                id="first",
                minimum_opening_stress_pa=2e7,
                maximum_opening_stress_pa=4e7,
                cycles=100.0,
                evidence_id="authored",
            ),
        ),
        counting_basis="Declared ordered cycles; no conversion from elapsed time or machine cycle indices",
        cycle_order_reviewed=True,
        temperature_k=293.15,
        resistance=resistance(),
        reviews=reviews(),
        calibration=GrowthCalibration(
            material_condition_id="authored-material",
            coefficient_m_cycle_per_pa_sqrt_m_power=1e-24,
            exponent=3.0,
            threshold_delta_k_pa_sqrt_m=0.0,
            minimum_delta_k_pa_sqrt_m=1e4,
            maximum_delta_k_pa_sqrt_m=1e9,
            minimum_stress_ratio=0.0,
            maximum_stress_ratio=0.9,
            minimum_temperature_k=280.0,
            maximum_temperature_k=320.0,
            evidence_id="authored",
            applicability_reviewed=True,
        ),
    )


def failure_points(*, kr=0.2, lr=0.5):
    return dict(
        material_condition_id="authored-material",
        curve=dict(
            curve_id="authored-polyline",
            assessment_basis_id="authored-example-only",
            load_ratio=[0.0, 1.0, 2.0],
            allowable_toughness_ratio=[1.0, 0.8, 0.0],
            interpolation="linear",
            validity_description="Authored polygon for interface references; no standard acceptance claim",
            evidence=[
                dict(
                    source_id="authored",
                    origin="synthetic",
                    citation="Original illustrative boundary",
                    license_id="MIT",
                )
            ],
        ),
        points=[
            dict(
                point_id="point",
                load_case_id="authored-reference",
                location_id="annulus",
                material_condition_id="authored-material",
                geometry_solution_id="authored",
                reference_stress_solution_id="authored",
                toughness_source_id="authored",
                effective_stress_intensity_Pa_sqrt_m=kr * 5e6,
                fracture_resistance_Pa_sqrt_m=5e6,
                reference_stress_Pa=lr * 2e8,
                normalizing_strength_Pa=2e8,
            )
        ],
        linear_elastic_k_solution_reviewed=True,
        reference_stress_solution_reviewed=True,
        toughness_applicability_reviewed=True,
        load_combination_reviewed=True,
        curve_applicability_reviewed=True,
    )


if __name__ == "__main__":
    import json

    from pipeline_integrity.fracture_exchange import assess_mode_i

    print(json.dumps(assess_mode_i(make_mode_i(), context()), indent=2))
