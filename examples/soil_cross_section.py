"""Original Cartesian meshes with two square cavities, never inferred circular pipes."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.soil_cross_section import (
    INPUT_UNITS,
    REVIEWS,
    SoilCrossSectionBoundary,
    SoilCrossSectionMesh,
    SoilCrossSectionRequest,
    boundary_metadata,
    evaluate_soil_cross_section,
)


def polygon_mesh(refinement=1, *, length_m=2.0):
    if type(refinement) is not int or not 1 <= refinement <= 8:
        raise ValueError("authored mesh refinement must be 1..8")
    points = []
    point_ids = {}
    faces = []
    face_ids = {}
    cells = []

    def vertex(point):
        if point not in point_ids:
            point_ids[point] = len(points)
            points.append(point)
        return point_ids[point]

    def edge(a, b):
        pair = tuple(sorted((vertex(a), vertex(b))))
        if pair not in face_ids:
            face_ids[pair] = len(faces)
            faces.append(pair)
        return face_ids[pair]

    for j in range(4 * refinement):
        for i in range(6 * refinement):
            x0, x1 = i / refinement, (i + 1) / refinement
            y0, y1 = -4 + j / refinement, -4 + (j + 1) / refinement
            if -2 <= y0 and y1 <= -1 and ((1 <= x0 and x1 <= 2) or (4 <= x0 and x1 <= 5)):
                continue
            p = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            cells.append(tuple(edge(a, b) for a, b in zip(p, (*p[1:], p[0]), strict=True)))
    counts = {i: 0 for i in range(len(faces))}
    for cell in cells:
        for face in cell:
            counts[face] += 1
    groups = {"ground": [], "far": [], "pipe_A": [], "pipe_B": []}
    for f, count in counts.items():
        if count != 1:
            continue
        a, b = (points[v] for v in faces[f])
        if a[1] == b[1] == 0:
            name = "ground"
        elif a[0] == b[0] and a[0] in {0, 6} or a[1] == b[1] == -4:
            name = "far"
        elif all(1 <= p[0] <= 2 and -2 <= p[1] <= -1 for p in (a, b)):
            name = "pipe_A"
        elif all(4 <= p[0] <= 5 and -2 <= p[1] <= -1 for p in (a, b)):
            name = "pipe_B"
        else:
            raise ValueError("authored cavity boundary classification failed")
        groups[name].append(f)
    return SoilCrossSectionMesh(
        vertices_m=tuple(points),
        face_vertex_ids=tuple(faces),
        cell_face_ids=tuple(cells),
        boundary_face_groups={k: tuple(v) for k, v in groups.items()},
        axial_extrusion_length_m=length_m,
        ground_surface_y_m=0.0,
        maximum_reviewed_nonorthogonality_deg=1e-5,
    )


def _face_average_cos(mesh, face):
    a, b = (mesh.vertices_m[v] for v in mesh.face_vertex_ids[face])
    wave = math.pi / 6
    return (
        math.cos(wave * a[0])
        if a[0] == b[0]
        else (math.sin(wave * b[0]) - math.sin(wave * a[0])) / (wave * (b[0] - a[0]))
    )


def make_case(*, refinement=1, reviewed=False, mode="uniform", length_m=2.0, maximum_step_s=None):
    mesh = polygon_mesh(refinement, length_m=length_m)
    times = (0.0, 0.1, 0.2)
    capacity = 10.0
    initial = []
    if mode == "heat_wave":
        times = tuple(i / 20 for i in range(21))
        capacity = 1.0
    if mode.startswith("interaction"):
        times = (0.0, 1.0, 2.0)
        capacity = 0.01
    for cell in mesh.cell_face_ids:
        coordinates = [mesh.vertices_m[v] for f in cell for v in mesh.face_vertex_ids[f]]
        xmin, xmax = min(x for x, y in coordinates), max(x for x, y in coordinates)
        y = (min(y for x, y in coordinates) + max(y for x, y in coordinates)) / 2
        if mode == "linear":
            value = 300 + 2 * y
        elif mode == "heat_wave":
            value = 300 + 10 * (math.sin(math.pi * xmax / 6) - math.sin(math.pi * xmin / 6)) / (
                math.pi * (xmax - xmin) / 6
            )
        elif mode == "convection":
            value = 310.0
        else:
            value = 300.0
        initial.append(value)
    boundaries = []
    for name, faces in mesh.boundary_face_groups.items():
        role = "pipe" if name.startswith("pipe_") else "ground" if name == "ground" else "far_field"
        rows = []
        for time in times:
            values = []
            for face in faces:
                if mode == "linear":
                    a, b = (mesh.vertices_m[v] for v in mesh.face_vertex_ids[face])
                    value = 300 + (a[1] + b[1])
                elif mode == "heat_wave":
                    value = 300 + 10 * math.exp(-((math.pi / 6) ** 2) * time) * _face_average_cos(mesh, face)
                elif mode in {"interaction_A", "interaction_B", "interaction_both"} and name in (
                    {"pipe_A", "pipe_B"} if mode == "interaction_both" else {"pipe_" + mode[-1]}
                ):
                    value = 301.0
                else:
                    value = 300.0
                values.append(value)
            rows.append(tuple(values))
        kwargs = {"kind": "temperature", "temperature_K": tuple(rows)}
        if mode == "flux_sign":
            kwargs = (
                {
                    "kind": "heat_flux",
                    "heat_flux_into_soil_W_m2": tuple(tuple(10.0 for _ in faces) for _ in times),
                }
                if name == "pipe_A"
                else {"kind": "adiabatic"}
            )
        elif mode == "convection":
            kwargs = {
                "kind": "convection",
                "convection_W_m2_K": tuple(tuple(1.0 for _ in faces) for _ in times),
                "ambient_temperature_K": tuple(rows),
            }
        boundaries.append(
            SoilCrossSectionBoundary(
                id=name,
                role=role,
                component_asset_id=name if role == "pipe" else None,
                evidence_id="authored",
                **kwargs,
            )
        )
    request = SoilCrossSectionRequest(
        asset_id="authored-soil",
        location_id="polygon-cross-section",
        clock_id="authored-elapsed",
        event_id=mode,
        clock_offset_s=20.0,
        mesh=mesh,
        time_s=times,
        maximum_step_s=maximum_step_s if maximum_step_s is not None else 0.05,
        history_interpolation="linear",
        conductivity_W_m_K=1.0,
        volumetric_heat_capacity_J_m3_K=capacity,
        temperature_support_K=(280.0, 340.0),
        initial_temperature_K=tuple(initial),
        boundaries=tuple(boundaries),
        geometry_evidence_id="authored",
        property_evidence_id="authored",
        initial_evidence_id="authored",
        evidence_ids=("authored",),
        applicability_reviews={key: True for key in REVIEWS} if reviewed else {},
        review_evidence_ids={key: "authored" for key in REVIEWS} if reviewed else {},
        input_units=INPUT_UNITS,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored-polygon-soil",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original two square cavities, artificial soil properties and verification boundaries",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"soil_cross_section_boundary": boundary_metadata(request)},
    )
    return request, context


if __name__ == "__main__":
    request, context = make_case()
    print(evaluate_soil_cross_section(request, context)["status"])
