"""Original artificial assay and impact series for reproducible software checks."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_quality import (
    CHEMISTRY,
    IMPACT,
    INPUT_UNITS,
    PITTING,
    REVIEWS,
    MaterialQualityRequest,
)


def make_cases():
    evidence = EvidenceRef(
        id="authored",
        citation="Original synthetic material quality reference inputs",
        origin="synthetic",
        license_id="MIT",
    )
    context = RunContext(
        asset_id="authored-sheet",
        scenario_id="material-quality-software-reference",
        clock_id="authored-test-event",
        origin="synthetic",
        evidence=(evidence,),
    )
    native_evidence = dict(
        source_id=evidence.id,
        origin="synthetic",
        citation=evidence.citation,
        license_id=evidence.license_id,
        sha256=None,
    )
    designation = dict(
        namespace="user",
        specification="authored-intervals",
        edition="1",
        grade="fixture",
        product_form="sheet",
        condition="authored-condition",
    )
    assay = dict(
        physical_scope_id="authored-sheet",
        heat_id="authored-heat",
        designation=designation,
        method_id="authored-assay-method",
        interval_interpretation="declared bounded intervals",
        evidence=[native_evidence],
    )
    chemistry = dict(
        certificate=dict(
            assay, record_id="certificate", element_intervals={"Ni": dict(lower=0.19, upper=0.21)}
        ),
        verification=dict(
            assay, record_id="verification", element_intervals={"Ni": dict(lower=0.195, upper=0.205)}
        ),
        requirements=dict(
            requirements_id="authored-requirements",
            designation=designation,
            element_limits={"Ni": dict(lower=0.1, upper=0.3)},
            evidence=[native_evidence],
        ),
        require_matching_heat_ids=True,
        require_verification_designation=True,
        require_certificate_verification_interval_overlap=True,
    )
    pitting = dict(
        chromium_mass_percent=20.0,
        molybdenum_mass_percent=2.0,
        nitrogen_mass_percent=0.1,
        molybdenum_coefficient=3.0,
        nitrogen_coefficient=10.0,
    )
    # Known odds and energies define a standalone analytic fixture. No fitting
    # algorithm or material coefficients are implemented here.
    odds = [1 / 99, 1 / 19, 1 / 9, 1 / 3, 1.0, 3.0, 9.0, 19.0, 99.0]
    impact = dict(
        series_id="authored-series",
        specimen_geometry_id="authored-geometry",
        material_condition_id="authored-condition",
        orientation_id="authored-orientation",
        observational_unit_description="one uncensored synthetic specimen per observation",
        observation_ids=[f"authored-specimen-{i}" for i in range(9)],
        temperature_K=[300.0 + 10.0 * math.log(value) for value in odds],
        absorbed_energy_J=[6.0, 10.0, 15.0, 30.0, 55.0, 80.0, 95.0, 100.0, 104.0],
        target_energy_J=55.0,
        minimum_relative_shelf_coverage=0.95,
        loss="linear",
        residual_scale_J=1.0,
    )
    requests = {}
    for method, parameters in ((CHEMISTRY, chemistry), (PITTING, pitting), (IMPACT, impact)):
        requests[method] = MaterialQualityRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="authored-sheet-location",
            clock_id=context.clock_id,
            material_condition_id="authored-condition",
            population_id="one-authored-material",
            physical_scope_id="authored-sheet",
            series_or_assay_id="authored-series" if method == IMPACT else "authored-assay",
            composition_basis={CHEMISTRY: "mass_fraction", PITTING: "mass_percent", IMPACT: "not_applicable"}[
                method
            ],
            parameters=parameters,
            input_units=INPUT_UNITS[method],
            evidence_ids=(evidence.id,),
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], evidence.id),
            element_assay_ids=dict.fromkeys(("Cr", "Mo", "N"), "authored-assay") if method == PITTING else {},
            impact_observations_complete=True if method == IMPACT else None,
            declared_temperature_support_K=(250.0, 350.0) if method == IMPACT else None,
        )
    return requests, context
