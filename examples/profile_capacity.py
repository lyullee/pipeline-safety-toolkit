"""Original hypothetical provider data, with no copied engineering standard factors."""

from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext
from pipeline_integrity.profile_capacity import INPUT_UNITS, REVIEWS, ProfileCapacityRequest


def make_case():
    context = RunContext(
        asset_id="authored_profile_asset",
        scenario_id="nodal_capacity_reference",
        clock_id="one_static_inspection",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical profile and provider inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    provider_evidence = {
        "source_id": "authored",
        "origin": "synthetic",
        "citation": context.evidence[0].citation,
        "license_id": "MIT",
        "sha256": None,
    }
    parameters = {
        "geometry_id": "authored_geometry",
        "material_condition_id": "authored_material",
        "reference_wall_thickness_m": 0.01,
        "axial_position_m": [0.0, 0.5, 1.0],
        "measured_remaining_thickness_m": [0.008, 0.008, 0.008],
        "reference_capacity": {
            "geometry_id": "authored_geometry",
            "material_condition_id": "authored_material",
            "reference_pressure_difference_Pa": 10e6,
            "capacity_basis": "authored hypothetical differential-pressure reference",
            "evidence": [provider_evidence],
        },
        "bulging_table": {
            "geometry_id": "authored_geometry",
            "material_condition_id": "authored_material",
            "window_length_m": [0.0, 1.0],
            "factor": [2.0, 2.0],
            "validity_description": "Original constant hypothetical factor for scalar verification only",
            "evidence": [provider_evidence],
        },
        "maximum_supported_depth_fraction": 0.7,
        "full_axial_extent_confirmed": True,
        "blunt_metal_loss_confirmed": True,
        "internal_pressure_dominant_confirmed": True,
        "provider_applicability_reviewed": True,
    }
    request = ProfileCapacityRequest(
        asset_id=context.asset_id,
        location_id="one_axial_track",
        clock_id=context.clock_id,
        geometry_id="authored_geometry",
        material_condition_id="authored_material",
        profile_id="authored_three_points",
        coordinate_basis_id="axial_positive_on_declared_track",
        source_observation_id="authored_inspection",
        source_point_ids=("point-0", "point-1", "point-2"),
        parameters=parameters,
        input_units=INPUT_UNITS,
        demand=Pressure(value=5.0, unit="MPa", reference="differential"),
        demand_evidence_id="authored",
        applicability_reviews=dict.fromkeys(REVIEWS, True),
        review_evidence_ids=dict.fromkeys(REVIEWS, "authored"),
        evidence_ids=("authored",),
    )
    return request, context


def make_cases():
    request, context = make_case()
    return {"structural.profile_capacity": request}, context
