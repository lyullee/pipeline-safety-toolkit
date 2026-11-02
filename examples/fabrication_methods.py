"""Authored SI scenarios; dimensions, factors and requirements are not standards defaults."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.fabrication_exchange import (
    REVIEWS,
    SI_INPUT_UNITS,
    FabricationRequest,
    FabricationRequirement,
)


def make_cases():
    context = RunContext(
        asset_id="authored_spool",
        scenario_id="fabrication_contract_references",
        clock_id="one_declared_fabrication_snapshot",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="source", citation="Original scalar geometry, torque and wall inputs", origin="synthetic"
            ),
            EvidenceRef(
                id="basis",
                citation="Authored applicability reviews for mathematical references only",
                origin="synthetic",
            ),
            EvidenceRef(
                id="limits",
                citation="Original illustrative quantity limits; no standard entitlement or design approval",
                origin="synthetic",
            ),
        ),
    )
    parameters = {
        "fabrication.bend_strain": dict(outside_diameter_m=0.12, centerline_bend_radius_m=0.6),
        "fabrication.bolt_preload": dict(
            torque_N_m=100.0, nominal_diameter_m=0.02, nut_factor=0.2, tensile_area_m2=0.00025
        ),
        "fabrication.thickness_budget": dict(
            nominal_thickness_m=0.01,
            manufacturing_minus_tolerance_m=0.001,
            forming_loss_fraction=0.1,
            corrosion_allowance_m=0.001,
            required_structural_thickness_m=0.007,
            measured_postfabrication_thickness_m=0.0085,
        ),
    }
    rules = {
        "fabrication.bend_strain": (("strain_limit", "outer_fiber_strain", "1", "le", 0.08),),
        "fabrication.bolt_preload": (
            ("minimum_preload", "preload_N", "N", "ge", 20000.0),
            ("maximum_nominal_stress", "nominal_tensile_stress_Pa", "Pa", "le", 120000000.0),
        ),
        "fabrication.thickness_budget": (
            ("nominal_margin", "nominal_budget_margin_m", "m", "ge", 0.0),
            ("measured_margin", "measured_margin_m", "m", "ge", 0.0),
        ),
    }
    cases = {}
    for method, values in parameters.items():
        cases[method] = FabricationRequest(
            method=method,
            asset_id=context.asset_id,
            location_id=method.split(".")[1],
            clock_id=context.clock_id,
            material_condition_id="authored_condition_without_strength_or_pressure_qualification",
            parameters=values,
            input_units=SI_INPUT_UNITS[method].copy(),
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "basis"),
            parameter_evidence_ids=dict.fromkeys(values, "source"),
            evidence_ids=("source", "basis", "limits"),
            requirements=tuple(
                FabricationRequirement(
                    id=id,
                    output_key=output,
                    unit=unit,
                    relation=relation,
                    threshold=threshold,
                    evidence_id="limits",
                    interpretation_reviewed=True,
                )
                for id, output, unit, relation, threshold in rules[method]
            ),
        )
    return cases, context
