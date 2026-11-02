"""Authored nodal fields on the shared two-cavity polygon mesh."""

import runpy
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.soil_fem import (
    INPUT_UNITS,
    REVIEWS,
    SoilFEMBoundary,
    SoilFEMRequest,
    boundary_metadata,
    evaluate_soil_fem,
)


def make_case(
    *,
    mode="linear",
    shear=0.0,
    refinement=1,
    reviewed=False,
    triangulation="local_delaunay",
    mass_scheme="vertex_quadrature_lumped",
    robin_quadrature="vertex_lumped",
    maximum_step_s=0.05,
):
    mesh = runpy.run_path(str(Path(__file__).with_name("soil_cross_section.py")))["polygon_mesh"](refinement)
    mesh = mesh.model_copy(
        update={
            "vertices_m": tuple((x + shear * y, y) for x, y in mesh.vertices_m),
            "maximum_reviewed_nonorthogonality_deg": 60.0,
        }
    )
    steady = mode == "linear"
    times = (0.0,) if steady else (0.0, 0.1, 0.2)
    initial = tuple(None if steady else 310.0 if mode == "robin" else 300.0 for _ in mesh.vertices_m)
    boundaries = []
    for name, faces in mesh.boundary_face_groups.items():
        role = "pipe" if name.startswith("pipe_") else "ground" if name == "ground" else "far_field"
        fields = {"kind": "adiabatic"}
        if mode in {"linear", "uniform"}:
            nodes = tuple(sorted({v for f in faces for v in mesh.face_vertex_ids[f]}))
            fields = {
                "kind": "temperature",
                "node_ids": nodes,
                "nodal_temperature_K": tuple(
                    tuple(300.0 + (2.0 * mesh.vertices_m[v][1] if steady else 0.0) for v in nodes)
                    for _ in times
                ),
            }
        elif mode == "robin":
            fields = {
                "kind": "convection",
                "convection_W_m2_K": tuple(tuple(1.0 for _ in faces) for _ in times),
                "ambient_temperature_K": tuple(tuple(300.0 for _ in faces) for _ in times),
            }
        elif mode == "flux" and name == "pipe_A":
            fields = {
                "kind": "heat_flux",
                "heat_flux_into_soil_W_m2": tuple(tuple(10.0 for _ in faces) for _ in times),
            }
        boundaries.append(
            SoilFEMBoundary(
                id=name,
                role=role,
                component_asset_id=name if role == "pipe" else None,
                evidence_id="authored",
                **fields,
            )
        )
    request = SoilFEMRequest(
        asset_id="authored-P1-soil",
        location_id="two-polygon-cavities",
        clock_id="authored-elapsed",
        event_id=mode,
        clock_offset_s=20.0,
        mode="steady" if steady else "transient",
        mesh=mesh,
        time_s=times,
        maximum_step_s=maximum_step_s,
        history_interpolation="linear",
        initial_node_temperature_K=initial,
        conductivity_W_m_K=1.0,
        volumetric_heat_capacity_J_m3_K=10.0,
        temperature_support_K=(280.0, 340.0),
        boundaries=tuple(boundaries),
        triangulation=triangulation,
        mass_scheme=mass_scheme,
        robin_quadrature=robin_quadrature,
        geometry_evidence_id="authored",
        property_evidence_id="authored",
        nodal_field_evidence_id="authored",
        evidence_ids=("authored",),
        applicability_reviews=dict.fromkeys(REVIEWS, True) if reviewed else {},
        review_evidence_ids=dict.fromkeys(REVIEWS, "authored") if reviewed else {},
        input_units=INPUT_UNITS,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored-FEM-check",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original polygon mesh and explicit synthetic nodal fields",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"soil_fem_boundary": boundary_metadata(request)},
    )
    return request, context


if __name__ == "__main__":
    request, context = make_case()
    print(evaluate_soil_fem(request, context)["status"])
