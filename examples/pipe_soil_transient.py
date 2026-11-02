"""Authored material curves and radial boundaries, not ground property advice."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.pipe_soil_transient import (
    REVIEWS,
    UNITS,
    PipeSoilBoundary,
    PipeSoilMaterial,
    PipeSoilShell,
    PipeSoilTransientRequest,
    evaluate_pipe_soil_transient,
    pipe_soil_boundary,
)


def make_case(*, source_sha256=None, model_version=None, schema_digest=None, count=4):
    wall_edges = (0.1, *(0.1 + 0.02 * i / count for i in range(1, count)), 0.12)
    soil_edges = (0.12, *(0.12 + 0.08 * i / count for i in range(1, count)), 0.2)
    request = PipeSoilTransientRequest(
        asset_id="authored-pipe-soil",
        location_id="one-radial-assembly",
        clock_id="authored-elapsed",
        event_id="authored-heating",
        cylinder_length_m=1.0,
        materials=(
            PipeSoilMaterial(
                id="wall",
                temperature_K=(250.0, 400.0),
                conductivity_W_m_K=(10.0, 10.0),
                volumetric_heat_capacity_J_m3_K=(2e6, 2e6),
                evidence_id="authored",
            ),
            PipeSoilMaterial(
                id="soil",
                temperature_K=(250.0, 400.0),
                conductivity_W_m_K=(1.0, 1.0),
                volumetric_heat_capacity_J_m3_K=(1e6, 1e6),
                evidence_id="authored",
            ),
        ),
        shells=(
            PipeSoilShell(
                id="wall",
                role="pipe_wall",
                material_id="wall",
                radial_edges_m=wall_edges,
                initial_cell_temperature_K=(300.0,) * count,
                evidence_id="authored",
            ),
            PipeSoilShell(
                id="soil",
                role="soil",
                material_id="soil",
                radial_edges_m=soil_edges,
                initial_cell_temperature_K=(300.0,) * count,
                evidence_id="authored",
            ),
        ),
        time_s=(0.0, 10.0, 20.0, 40.0),
        clock_offset_s=100.0,
        inner_boundary=PipeSoilBoundary(
            id="fluid-side",
            kind="convection_radiation",
            convection_W_m2_K=(50.0,) * 4,
            ambient_temperature_K=(320.0, 325.0, 315.0, 310.0),
            emissivity=0.0,
            radiative_environment_temperature_K=(300.0,) * 4,
            evidence_id="authored",
        ),
        outer_boundary=PipeSoilBoundary(
            id="far-soil-cylinder",
            kind="prescribed_temperature",
            temperature_K=(300.0, 301.0, 300.0, 299.0),
            evidence_id="authored",
        ),
        perfect_contacts=True,
        maximum_step_s=1.0,
        owner_source_tree_sha256=source_sha256,
        expected_model_version=model_version,
        expected_input_schema_digest=schema_digest,
        applicability_reviews=dict.fromkeys(REVIEWS, True),
        review_evidence_ids=dict.fromkeys(REVIEWS, "authored"),
        evidence_ids=("authored",),
        input_units=UNITS,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="original-reference",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical geometry, material curves and boundary histories",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"pipe_soil_boundary": pipe_soil_boundary(request)},
    )
    return request, context


if __name__ == "__main__":
    request, context = make_case()
    record = evaluate_pipe_soil_transient(request, context)
    assert record["status"] == "needs_data" and record["native_receipt"] is None
    print(record["record_digest"])
