"""Reviewed polygonal pipe cavities and transient soil heat through optional FiPy.

The mesh, property values and boundary histories are supplied by the caller.
FiPy owns the finite-volume assembly and linear solve. This boundary does not
infer circular pipes, moving pore water, a pipe wall or a fluid energy inventory.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest

SCHEMA = "pipeline_integrity.soil_cross_section.v1"
FIPY_VERSION = "4.0.3"
MAX_SUPPORTED_NONORTHOGONALITY_DEG = 1e-6
REVIEWS = (
    "mesh_topology_and_polygon_pipe_geometry",
    "cross_section_invariant_along_extrusion",
    "constant_properties_and_temperature_support",
    "prescribed_soil_boundary_histories",
    "no_moisture_advection_freezing_or_pipe_fluid_coupling",
    "mesh_quality_and_far_boundary_extent",
)
INPUT_UNITS = {
    "coordinates": "m",
    "extrusion_length": "m",
    "time": "s",
    "temperature": "K",
    "conductivity": "W/(m*K)",
    "volumetric_heat_capacity": "J/(m3*K)",
    "heat_flux_into_soil": "W/m2",
    "convection": "W/(m2*K)",
    "energy_tolerance": "J",
}
History = tuple[tuple[float | None, ...], ...]


class SoilCrossSectionMesh(Contract):
    vertices_m: tuple[tuple[float, float], ...] = Field(min_length=4, max_length=4096)
    face_vertex_ids: tuple[tuple[int, int], ...] = Field(min_length=4, max_length=6000)
    cell_face_ids: tuple[tuple[int, ...], ...] = Field(min_length=1, max_length=2000)
    boundary_face_groups: dict[str, tuple[int, ...]]
    axial_extrusion_length_m: Positive
    ground_surface_y_m: float
    maximum_reviewed_nonorthogonality_deg: float = Field(ge=0, le=85)


class SoilCrossSectionBoundary(Contract):
    id: Name
    role: Literal["ground", "pipe", "far_field"]
    component_asset_id: Name | None = None
    kind: Literal["temperature", "heat_flux", "convection", "adiabatic"]
    temperature_K: History | None = None
    heat_flux_into_soil_W_m2: History | None = None
    convection_W_m2_K: History | None = None
    ambient_temperature_K: History | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def fields(self):
        allowed = {
            "temperature": {"temperature_K"},
            "heat_flux": {"heat_flux_into_soil_W_m2"},
            "convection": {"convection_W_m2_K", "ambient_temperature_K"},
            "adiabatic": set(),
        }[self.kind]
        for key in (
            "temperature_K",
            "heat_flux_into_soil_W_m2",
            "convection_W_m2_K",
            "ambient_temperature_K",
        ):
            rows = getattr(self, key)
            if rows is not None:
                if key not in allowed:
                    raise ValueError("boundary kind has an unrelated history")
                if any(
                    v is not None
                    and (("temperature" in key and v <= 0) or (key == "convection_W_m2_K" and v < 0))
                    for row in rows
                    for v in row
                ):
                    raise ValueError("absolute temperatures must be positive and convection nonnegative")
        if (self.role == "pipe") != (self.component_asset_id is not None):
            raise ValueError("pipe boundary requires its component asset identifier only")
        return self


class SoilCrossSectionRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    event_id: Name
    clock_offset_s: Nonnegative
    mesh: SoilCrossSectionMesh
    time_s: tuple[Nonnegative, ...] = Field(min_length=2, max_length=501)
    maximum_step_s: Positive
    history_interpolation: Literal["linear", "previous_knot_hold"]
    conductivity_W_m_K: Positive | None
    volumetric_heat_capacity_J_m3_K: Positive | None
    temperature_support_K: tuple[Positive, Positive] | None
    initial_temperature_K: tuple[Positive | None, ...]
    boundaries: tuple[SoilCrossSectionBoundary, ...] = Field(min_length=2, max_length=100)
    geometry_evidence_id: Name
    property_evidence_id: Name
    initial_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, str] = Field(default_factory=dict)
    absolute_energy_tolerance_J: Positive = 1e-6
    relative_energy_tolerance: Positive = 1e-8
    input_units: dict[str, str]
    expected_fipy_version: Name = FIPY_VERSION

    @model_validator(mode="after")
    def aligned(self):
        if self.input_units != INPUT_UNITS:
            raise ValueError("explicit SI soil cross-section input units required")
        if self.time_s[0] != 0 or any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("local thermal clock must start at zero and strictly increase")
        if (
            self.temperature_support_K is not None
            and self.temperature_support_K[0] > self.temperature_support_K[1]
        ):
            raise ValueError("temperature support must be ordered")
        if len(self.initial_temperature_K) != len(self.mesh.cell_face_ids):
            raise ValueError("initial temperature must identify every mesh cell")
        names = [b.id for b in self.boundaries]
        if len(set(names)) != len(names) or set(names) != set(self.mesh.boundary_face_groups):
            raise ValueError("boundaries must exactly identify all named mesh face groups")
        if not any(b.role == "ground" for b in self.boundaries) or not any(
            b.role == "pipe" for b in self.boundaries
        ):
            raise ValueError("horizontal ground and genuine pipe cavity groups required")
        for boundary in self.boundaries:
            count = len(self.mesh.boundary_face_groups[boundary.id])
            for key in (
                "temperature_K",
                "heat_flux_into_soil_W_m2",
                "convection_W_m2_K",
                "ambient_temperature_K",
            ):
                rows = getattr(boundary, key)
                if rows is not None and (
                    len(rows) != len(self.time_s) or any(len(row) != count for row in rows)
                ):
                    raise ValueError("boundary rows must align to common time knots and ordered group faces")
        steps = sum(
            math.ceil((b - a) / self.maximum_step_s)
            for a, b in zip(self.time_s, self.time_s[1:], strict=False)
        )
        if (
            steps > 2000
            or (steps + 1)
            * (len(self.initial_temperature_K) + sum(len(g) for g in self.mesh.boundary_face_groups.values()))
            > 200_000
        ):
            raise ValueError("thermal step/history value budget exceeded")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("unknown soil cross-section review key")
        return self


def boundary_metadata(request: SoilCrossSectionRequest):
    return {
        "location_id": request.location_id,
        "event_id": request.event_id,
        "clock_offset_s": request.clock_offset_s,
        "axial_extrusion_length_m": request.mesh.axial_extrusion_length_m,
        "coordinate_plane": "x_y_metres",
        "pipe_boundary_meaning": "declared_polygon_cavity_soil_surface",
    }


def _area(points):
    x0, y0 = points[0]
    return (
        sum(
            (a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
            for a, b in zip(points, (*points[1:], points[0]), strict=True)
        )
        / 2
    )


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _inside(point, polygon, eps):
    signs = [_cross(a, b, point) for a, b in zip(polygon, (*polygon[1:], polygon[0]), strict=True)]
    return all(s > eps for s in signs) or all(s < -eps for s in signs)


def _loop(edge_ids, faces):
    adjacency = {}
    for face in edge_ids:
        a, b = faces[face]
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if not adjacency or any(len(set(v)) != 2 or len(v) != 2 for v in adjacency.values()):
        raise ValueError("polygon edges must form a simple closed cycle")
    start = min(adjacency)
    result, previous, current = [], None, start
    while current not in result:
        result.append(current)
        nxt = next(v for v in adjacency[current] if v != previous)
        previous, current = current, nxt
    if current != start or len(result) != len(adjacency):
        raise ValueError("polygon is disconnected or self-repeating")
    return result


def validate_mesh(mesh: SoilCrossSectionMesh, boundaries: tuple[SoilCrossSectionBoundary, ...], *,
                  ground_surface_representation="horizontal"):
    """Check exact polygon topology; the default retains the horizontal-ground contract."""
    if ground_surface_representation not in {"horizontal","boundary_polyline"}:
        raise ValueError("unsupported ground-surface representation")
    vertices, faces, cells = mesh.vertices_m, mesh.face_vertex_ids, mesh.cell_face_ids
    scale = max(
        max(x for x, _ in vertices) - min(x for x, _ in vertices),
        max(y for _, y in vertices) - min(y for _, y in vertices),
    )
    if scale <= 0:
        raise ValueError("nonzero mesh extent required")
    eps = 1e-10 * scale * scale
    if len(set(vertices)) != len(vertices):
        raise ValueError("duplicate vertex coordinates")
    if any(a == b or min(a, b) < 0 or max(a, b) >= len(vertices) for a, b in faces) or len(
        {tuple(sorted(f)) for f in faces}
    ) != len(faces):
        raise ValueError("invalid or duplicate mesh edge")
    if set(v for f in faces for v in f) != set(range(len(vertices))):
        raise ValueError("unused mesh vertices")
    active = []
    for face in sorted(range(len(faces)), key=lambda i: min(vertices[v][0] for v in faces[i])):
        a, b = (vertices[v] for v in faces[face])
        xmin = min(a[0], b[0])
        ymin, ymax = min(a[1], b[1]), max(a[1], b[1])
        active = [
            other
            for other in active
            if max(vertices[v][0] for v in faces[other]) >= xmin - math.sqrt(eps) * 1e-5
        ]
        for other in active:
            c, d = (vertices[v] for v in faces[other])
            if min(ymax, max(c[1], d[1])) < max(ymin, min(c[1], d[1])) - math.sqrt(eps) * 1e-5:
                continue

            def interior(point, left, right):
                return (
                    abs(_cross(left, right, point)) <= eps
                    and (point[0] - left[0]) * (point[0] - right[0])
                    + (point[1] - left[1]) * (point[1] - right[1])
                    < -eps * 1e-10
                )

            if any(interior(p, c, d) for p in (a, b)) or any(interior(p, a, b) for p in (c, d)):
                raise ValueError("mesh has hanging vertices or partially overlapping edges")
            if (
                _cross(a, b, c) * _cross(a, b, d) < -eps * eps
                and _cross(c, d, a) * _cross(c, d, b) < -eps * eps
            ):
                raise ValueError("mesh faces cross")
        active.append(face)
    face_cells = [[] for _ in faces]
    polygons, areas, centers = [], [], []
    for index, cell in enumerate(cells):
        if not 3 <= len(cell) <= 8 or len(set(cell)) != len(cell) or min(cell) < 0 or max(cell) >= len(faces):
            raise ValueError("invalid convex cell faces")
        ids = _loop(cell, faces)
        polygon = tuple(vertices[i] for i in ids)
        area = _area(polygon)
        if area < 0:
            polygon = tuple(reversed(polygon))
            area = -area
        if area <= eps or any(
            _cross(polygon[i - 1], polygon[i], polygon[(i + 1) % len(polygon)]) <= eps
            for i in range(len(polygon))
        ):
            raise ValueError("cells must be strictly convex with positive area")
        cx = sum(
            (a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1])
            for a, b in zip(polygon, (*polygon[1:], polygon[0]), strict=True)
        ) / (6 * area)
        cy = sum(
            (a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1])
            for a, b in zip(polygon, (*polygon[1:], polygon[0]), strict=True)
        ) / (6 * area)
        polygons.append(polygon)
        areas.append(area)
        centers.append((cx, cy))
        for face in cell:
            face_cells[face].append(index)
    if any(len(row) not in {1, 2} for row in face_cells):
        raise ValueError("faces must belong to one or two cells")
    for i, a in enumerate(polygons):
        aminx, amaxx, aminy, amaxy = (
            min(x for x, y in a),
            max(x for x, y in a),
            min(y for x, y in a),
            max(y for x, y in a),
        )
        for j in range(i):
            b = polygons[j]
            if min(amaxx, max(x for x, y in b)) <= max(aminx, min(x for x, y in b)) or min(
                amaxy, max(y for x, y in b)
            ) <= max(aminy, min(y for x, y in b)):
                continue
            if (
                _inside(centers[i], b, eps)
                or _inside(centers[j], a, eps)
                or any(_inside(p, b, eps) for p in a)
                or any(_inside(p, a, eps) for p in b)
            ):
                raise ValueError("mesh cell interiors overlap")
            for a0, a1 in zip(a, (*a[1:], a[0]), strict=True):
                for b0, b1 in zip(b, (*b[1:], b[0]), strict=True):
                    if (
                        _cross(a0, a1, b0) * _cross(a0, a1, b1) < -eps * eps
                        and _cross(b0, b1, a0) * _cross(b0, b1, a1) < -eps * eps
                    ):
                        raise ValueError("mesh cell edges cross")
    neighbours = [set() for _ in cells]
    for row in face_cells:
        if len(row) == 2:
            neighbours[row[0]].add(row[1])
            neighbours[row[1]].add(row[0])
    reached, pending = set(), [0]
    while pending:
        i = pending.pop()
        if i not in reached:
            reached.add(i)
            pending.extend(neighbours[i] - reached)
    if len(reached) != len(cells):
        raise ValueError("soil control volumes must form one connected domain")
    exterior = {i for i, row in enumerate(face_cells) if len(row) == 1}
    supplied = [f for group in mesh.boundary_face_groups.values() for f in group]
    if (
        any(not group for group in mesh.boundary_face_groups.values())
        or len(set(supplied)) != len(supplied)
        or set(supplied) != exterior
    ):
        raise ValueError("nonempty boundary groups must partition all actual exterior faces")
    remaining, loops = exterior.copy(), []
    while remaining:
        component, frontier = set(), [min(remaining)]
        while frontier:
            f = frontier.pop()
            if f in component:
                continue
            component.add(f)
            frontier.extend(g for g in remaining - component if set(faces[g]) & set(faces[f]))
        ids = _loop(component, faces)
        loops.append((component, tuple(vertices[i] for i in ids)))
        remaining -= component
    outer = max(loops, key=lambda item: abs(_area(item[1])))
    holes = [item for item in loops if item is not outer]
    if any(len(polygon) < 3 or abs(_area(polygon)) <= eps for _, polygon in loops):
        raise ValueError("outer boundary and every pipe cavity require positive polygon area")
    pipes = [b for b in boundaries if b.role == "pipe"]
    if len(holes) != len(pipes) or not holes:
        raise ValueError("each pipe group must correspond to one genuine cavity loop")
    for pipe in pipes:
        if not any(set(mesh.boundary_face_groups[pipe.id]) == hole[0] for hole in holes):
            raise ValueError("pipe boundary is not a complete interior cavity")
    for b in boundaries:
        if b.role != "pipe" and not set(mesh.boundary_face_groups[b.id]).issubset(outer[0]):
            raise ValueError("ground/far field must belong to the outer domain boundary")
        if ground_surface_representation=="horizontal" and b.role == "ground" and any(
            abs(vertices[v][1] - mesh.ground_surface_y_m) > math.sqrt(eps)
            for f in mesh.boundary_face_groups[b.id]
            for v in faces[f]
        ):
            raise ValueError("ground faces must lie on the declared horizontal surface")
    if any(y > mesh.ground_surface_y_m + math.sqrt(eps) for x, y in vertices):
        raise ValueError("soil mesh extends above the ground surface" if ground_surface_representation=="horizontal"
                         else "soil mesh extends above the declared ground upper envelope")
    if not math.isclose(
        sum(areas), abs(_area(outer[1])) - sum(abs(_area(p)) for _, p in holes), rel_tol=1e-9, abs_tol=eps
    ):
        raise ValueError("mesh cell areas do not equal outer polygon minus genuine holes")
    maximum_angle = 0.0
    for i, (a, b) in enumerate(faces):
        p, q = vertices[a], vertices[b]
        nx, ny = -(q[1] - p[1]), q[0] - p[0]
        first = centers[face_cells[i][0]]
        target = (
            centers[face_cells[i][1]] if len(face_cells[i]) == 2 else ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        )
        dx, dy = target[0] - first[0], target[1] - first[1]
        denominator = math.hypot(nx, ny) * math.hypot(dx, dy)
        if denominator == 0:
            raise ValueError("degenerate face-to-cell geometry")
        angle = math.degrees(math.acos(min(1.0, abs(nx * dx + ny * dy) / denominator)))
        maximum_angle = max(maximum_angle, angle)
    if maximum_angle > mesh.maximum_reviewed_nonorthogonality_deg + 1e-6:
        raise ValueError("mesh nonorthogonality exceeds the caller-reviewed bound")
    return {
        "cell_areas_m2": areas,
        "cell_centers_m": centers,
        "face_cells": face_cells,
        "cavity_count": len(holes),
        "maximum_nonorthogonality_deg": maximum_angle,
        "ground_surface_representation":ground_surface_representation,
        "ground_surface_y_m_role":"actual_horizontal_surface" if ground_surface_representation=="horizontal" else "declared_upper_envelope_only",
    }


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("soil request and RunContext asset/clock differ")
    evidence = [e.id for e in context.evidence]
    selected = request.evidence_ids
    if (
        len(set(evidence)) != len(evidence)
        or len(set(selected)) != len(selected)
        or set(selected) - set(evidence)
    ):
        raise ValueError("selected evidence must be distinct and present in RunContext")
    needed = {
        request.geometry_evidence_id,
        request.property_evidence_id,
        request.initial_evidence_id,
        *request.review_evidence_ids.values(),
        *(b.evidence_id for b in request.boundaries),
    }
    if needed - set(selected):
        raise ValueError("boundary/property/geometry/initial/review evidence is not selected")
    if context.metadata.get("soil_cross_section_boundary") != boundary_metadata(request):
        raise ValueError(
            "RunContext must bind soil event, location, clock offset, extrusion and cavity meaning"
        )
    missing = [
        f"review/evidence required: {key}"
        for key in REVIEWS
        if request.applicability_reviews.get(key) is not True or key not in request.review_evidence_ids
    ]
    if any(
        v is None
        for v in (
            request.conductivity_W_m_K,
            request.volumetric_heat_capacity_J_m3_K,
            request.temperature_support_K,
            *request.initial_temperature_K,
        )
    ):
        missing.append(
            "resolved soil properties, temperature support and all initial cell temperatures required"
        )
    for b in request.boundaries:
        required = {
            "temperature": ("temperature_K",),
            "heat_flux": ("heat_flux_into_soil_W_m2",),
            "convection": ("convection_W_m2_K", "ambient_temperature_K"),
            "adiabatic": (),
        }[b.kind]
        for key in required:
            rows = getattr(b, key)
            if rows is None or any(v is None for row in rows for v in row):
                missing.append(f"resolved boundary history required: {b.id}/{key}")
    return missing


def _backend():
    from fipy import CellVariable, DiffusionTerm, FaceVariable, ImplicitSourceTerm, TransientTerm
    from fipy.meshes.mesh2D import Mesh2D
    from fipy.solvers.scipy import LinearLUSolver

    classes = (
        CellVariable,
        DiffusionTerm,
        FaceVariable,
        ImplicitSourceTerm,
        TransientTerm,
        Mesh2D,
        LinearLUSolver,
    )
    installed = distribution("FiPy")
    tree = hashlib.sha256()
    paths = sorted(
        path
        for path in installed.files
        if str(path).replace("\\", "/").startswith("fipy/") and str(path).endswith(".py")
    )
    for path in paths:
        tree.update(str(path).replace("\\", "/").encode("utf-8"))
        tree.update(installed.locate_file(path).read_bytes())
    receipt = {
        "name": "FiPy",
        "version": version("FiPy"),
        "implementation_files": {
            f"{c.__module__}.{c.__name__}": file_digest(Path(inspect.getsourcefile(c))) for c in classes
        },
        "solver": "fipy.solvers.scipy.LinearLUSolver",
        "source_tree_sha256": tree.hexdigest(),
        "source_tree_files": len(paths),
        "diffusion_geometry": "FiPy DiffusionTermNoCorrection; orthogonal meshes only",
        "maximum_supported_nonorthogonality_deg": MAX_SUPPORTED_NONORTHOGONALITY_DEG,
    }
    return classes, receipt


def _expected_clock(request):
    times = [0.0]
    intervals = [0]
    fractions = [0.0]
    for i, (a, b) in enumerate(zip(request.time_s, request.time_s[1:], strict=False)):
        count = math.ceil((b - a) / request.maximum_step_s)
        for j in range(1, count + 1):
            times.append(a + (b - a) * j / count)
            intervals.append(i)
            fractions.append(j / count if request.history_interpolation == "linear" else 0.0)
    return times, intervals, fractions


def _value(rows, interval, fraction):
    return [a + fraction * (b - a) for a, b in zip(rows[interval], rows[interval + 1], strict=True)]


def _native_mesh(request, geometry, Mesh2D):
    import numpy as np

    cell_ids = np.ma.masked_all(
        (max(map(len, request.mesh.cell_face_ids)), len(request.mesh.cell_face_ids)), dtype=int
    )
    for i, ids in enumerate(request.mesh.cell_face_ids):
        cell_ids[: len(ids), i] = ids
    mesh = Mesh2D(np.asarray(request.mesh.vertices_m).T, np.asarray(request.mesh.face_vertex_ids).T, cell_ids)
    if not np.allclose(mesh.cellVolumes, geometry["cell_areas_m2"], rtol=1e-10, atol=1e-12):
        raise ValueError("FiPy mesh areas differ from the validated control volumes")
    if set(np.flatnonzero(mesh.exteriorFaces.value)) != {
        f for group in request.mesh.boundary_face_groups.values() for f in group
    }:
        raise ValueError("FiPy exterior faces differ from the supplied boundary partition")
    maximum = 0.0
    for i, row in enumerate(geometry["face_cells"]):
        first = np.asarray(mesh.cellCenters)[:, row[0]]
        target = (
            np.asarray(mesh.cellCenters)[:, row[1]] if len(row) == 2 else np.asarray(mesh.faceCenters)[:, i]
        )
        delta = target - first
        normal = np.asarray(mesh.faceNormals)[:, i]
        maximum = max(
            maximum,
            math.degrees(
                math.acos(min(1.0, abs(float(np.dot(delta, normal))) / float(np.linalg.norm(delta))))
            ),
        )
    if maximum > min(
        request.mesh.maximum_reviewed_nonorthogonality_deg + 1e-6, MAX_SUPPORTED_NONORTHOGONALITY_DEG
    ):
        raise ValueError("actual FiPy cell/face nonorthogonality exceeds the reviewed bound")
    return mesh, {
        "cell_areas_m2": geometry["cell_areas_m2"],
        "cell_centers_m": np.asarray(mesh.cellCenters).T.tolist(),
        "cavity_count": geometry["cavity_count"],
        "maximum_nonorthogonality_deg": maximum,
    }


def _native(request, geometry, *, temperature_replay=None):
    import numpy as np

    classes, backend = _backend()
    if backend["version"] != request.expected_fipy_version:
        raise ValueError("FiPy version differs from the reviewed version")
    CellVariable, DiffusionTerm, FaceVariable, ImplicitSourceTerm, TransientTerm, Mesh2D, LinearLUSolver = (
        classes
    )
    mesh, native_geometry = _native_mesh(request, geometry, Mesh2D)
    temperature = CellVariable(mesh=mesh, value=request.initial_temperature_K, hasOld=True)
    k = float(request.conductivity_W_m_K)
    capacity = float(request.volumetric_heat_capacity_J_m3_K)
    gamma = FaceVariable(mesh=mesh, value=k)
    prescribed = FaceVariable(mesh=mesh, value=300.0)
    flux = FaceVariable(mesh=mesh, value=0.0)
    conductance = FaceVariable(mesh=mesh, value=0.0)
    environment = FaceVariable(mesh=mesh, value=0.0)
    masks = {}
    face_cells = np.asarray(mesh.faceCellIDs[0], dtype=int)
    normals = np.asarray(mesh.faceNormals)
    centers = np.asarray(mesh.cellCenters)
    face_centers = np.asarray(mesh.faceCenters)
    distances = np.abs(np.sum((face_centers - centers[:, face_cells]) * normals, axis=0))
    for boundary in request.boundaries:
        mask = np.zeros(len(request.mesh.face_vertex_ids), dtype=bool)
        mask[list(request.mesh.boundary_face_groups[boundary.id])] = True
        masks[boundary.id] = mask
        if boundary.kind == "temperature":
            temperature.constrain(prescribed, where=mask)
        elif boundary.kind == "heat_flux":
            temperature.faceGrad.constrain((flux / k) * mesh.faceNormals, where=mask)
        elif boundary.kind == "convection":
            gamma.setValue(0.0, where=mask)
    equation = TransientTerm(coeff=capacity) == DiffusionTerm(coeff=gamma) + (
        conductance * environment * mesh.faceNormals
    ).divergence - ImplicitSourceTerm(coeff=(conductance * mesh.faceNormals).divergence)
    solver = LinearLUSolver(tolerance=1e-12, iterations=1000)
    times, intervals, fractions = _expected_clock(request)
    if temperature_replay is not None:
        times, intervals, fractions = (
            times[: len(temperature_replay)],
            intervals[: len(temperature_replay)],
            fractions[: len(temperature_replay)],
        )
    rows = []
    equation_errors = []
    powers = {b.id: [] for b in request.boundaries}
    surfaces = {b.id: [] for b in request.boundaries}
    cumulative = {b.id: [0.0] for b in request.boundaries}
    status = "computed"
    reason = None
    for index, (time, interval, fraction) in enumerate(zip(times, intervals, fractions, strict=True)):
        for b in request.boundaries:
            mask = masks[b.id]
            ids = list(request.mesh.boundary_face_groups[b.id])
            if b.kind == "temperature":
                prescribed[ids] = _value(b.temperature_K, interval, fraction)
            elif b.kind == "heat_flux":
                flux[ids] = _value(b.heat_flux_into_soil_W_m2, interval, fraction)
            elif b.kind == "convection":
                h = np.asarray(_value(b.convection_W_m2_K, interval, fraction))
                ids = list(request.mesh.boundary_face_groups[b.id])
                local = np.divide(h, 1 + h * distances[ids] / k)
                conductance[ids] = local
                environment[ids] = _value(b.ambient_temperature_K, interval, fraction)
        try:
            if index:
                temperature.updateOld()
                if temperature_replay is None:
                    equation.solve(var=temperature, dt=time - times[index - 1], solver=solver)
                else:
                    temperature.setValue(temperature_replay[index])
                equation_error = (
                    float(
                        np.max(
                            np.abs(
                                equation.justResidualVector(
                                    var=temperature, dt=time - times[index - 1], solver=solver
                                )
                            )
                        )
                    )
                    * request.mesh.axial_extrusion_length_m
                    * (time - times[index - 1])
                )
            else:
                equation_error = 0.0
            values = np.asarray(temperature.value, dtype=float)
            if not np.isfinite(values).all() or np.any(values <= 0):
                raise ValueError("FiPy returned nonfinite/nonpositive cell temperatures")
            gradient = np.asarray(temperature.faceGrad.value)
            q = k * np.sum(gradient * normals, axis=0)
            for b in request.boundaries:
                ids = list(request.mesh.boundary_face_groups[b.id])
                mask = masks[b.id]
                if b.kind == "adiabatic":
                    q[ids] = 0.0
                elif b.kind == "heat_flux":
                    q[ids] = np.asarray(flux.value)[ids]
                elif b.kind == "convection":
                    q[ids] = np.asarray(conductance.value)[ids] * (
                        np.asarray(environment.value)[ids] - values[face_cells[ids]]
                    )
                surface = values[face_cells[ids]] + q[ids] * distances[ids] / k
                if b.kind == "temperature":
                    surface = np.asarray(prescribed.value)[ids]
                face_lengths = np.asarray(
                    [
                        math.dist(
                            request.mesh.vertices_m[request.mesh.face_vertex_ids[f][0]],
                            request.mesh.vertices_m[request.mesh.face_vertex_ids[f][1]],
                        )
                        for f in ids
                    ]
                )
                power = float(np.sum(q[ids] * face_lengths) * request.mesh.axial_extrusion_length_m)
                if not math.isfinite(power) or not np.isfinite(surface).all():
                    raise ValueError("FiPy returned invalid boundary heat/temperature")
                powers[b.id].append(power)
                surfaces[b.id].append(surface.tolist())
                if index:
                    cumulative[b.id].append(cumulative[b.id][-1] + power * (time - times[index - 1]))
            rows.append(values.tolist())
            equation_errors.append(equation_error)
        except Exception as exc:
            status = "partial"
            reason = f"{type(exc).__name__}: {exc}"
            break
    used = len(rows)
    return {
        "status": status,
        "reason": reason,
        "backend": backend,
        "trajectory": {
            "time_s": times[:used],
            "boundary_interval": intervals[:used],
            "boundary_fraction": fractions[:used],
            "temperature_K": rows,
            "maximum_cell_equation_residual_J": equation_errors,
            "boundary_heat_rate_into_soil_W": {key: row[:used] for key, row in powers.items()},
            "boundary_surface_temperature_K": {key: row[:used] for key, row in surfaces.items()},
            "cumulative_boundary_energy_into_soil_J": {key: row[:used] for key, row in cumulative.items()},
        },
        "geometry": native_geometry,
        "runtime": {name: version(name) for name in ("FiPy", "numpy", "scipy", "matplotlib")},
    }


def _audit(request, native):
    if native.get("status") not in {"computed", "partial"}:
        raise ValueError("unrecognized native soil status")
    trajectory = native["trajectory"]
    times = trajectory["time_s"]
    expected, intervals, fractions = _expected_clock(request)
    count = len(times)
    if (
        (native["status"] == "computed" and count != len(expected))
        or times != expected[:count]
        or trajectory["boundary_interval"] != intervals[:count]
        or trajectory["boundary_fraction"] != fractions[:count]
    ):
        raise ValueError("native clock/interpolation is not the requested clock or explicit partial prefix")
    rows = trajectory["temperature_K"]
    areas = native["geometry"]["cell_areas_m2"]
    if len(rows) != count or any(
        len(row) != len(areas) or any(not math.isfinite(v) or v <= 0 for v in row) for row in rows
    ):
        raise ValueError("native temperature rows do not align with the mesh")
    if count and rows[0] != list(request.initial_temperature_K):
        raise ValueError("native initial cell temperatures changed")
    capacity = request.volumetric_heat_capacity_J_m3_K
    length = request.mesh.axial_extrusion_length_m
    stored = [
        capacity
        * length
        * sum(
            area * (value - initial)
            for area, value, initial in zip(areas, row, request.initial_temperature_K, strict=True)
        )
        for row in rows
    ]
    power = trajectory["boundary_heat_rate_into_soil_W"]
    cumulative = trajectory["cumulative_boundary_energy_into_soil_J"]
    names = set(request.mesh.boundary_face_groups)
    if (
        set(power) != names
        or set(cumulative) != names
        or set(trajectory["boundary_surface_temperature_K"]) != names
    ):
        raise ValueError("boundary heat ledger identifiers differ")
    for b in request.boundaries:
        if (
            len(power[b.id]) != count
            or len(cumulative[b.id]) != count
            or len(trajectory["boundary_surface_temperature_K"][b.id]) != count
        ):
            raise ValueError("boundary heat ledger length mismatch")
        if count and cumulative[b.id][0] != 0:
            raise ValueError("initial cumulative boundary energy must be zero")
        for i in range(count):
            faces = request.mesh.boundary_face_groups[b.id]
            surface = trajectory["boundary_surface_temperature_K"][b.id][i]
            if (
                len(surface) != len(faces)
                or any(not math.isfinite(v) or v <= 0 for v in surface)
                or not math.isfinite(power[b.id][i])
            ):
                raise ValueError("boundary surface/heat fields are invalid")
            if b.kind == "temperature" and surface != _value(b.temperature_K, intervals[i], fractions[i]):
                raise ValueError("prescribed boundary temperatures changed")
            flux = None
            if b.kind == "adiabatic":
                flux = [0.0] * len(faces)
            elif b.kind == "heat_flux":
                flux = _value(b.heat_flux_into_soil_W_m2, intervals[i], fractions[i])
            elif b.kind == "convection":
                h = _value(b.convection_W_m2_K, intervals[i], fractions[i])
                ambient = _value(b.ambient_temperature_K, intervals[i], fractions[i])
                flux = [
                    coefficient * (environment - value)
                    for coefficient, environment, value in zip(h, ambient, surface, strict=True)
                ]
            if flux is not None:
                expected_power = length * sum(
                    q
                    * math.dist(
                        request.mesh.vertices_m[request.mesh.face_vertex_ids[f][0]],
                        request.mesh.vertices_m[request.mesh.face_vertex_ids[f][1]],
                    )
                    for q, f in zip(flux, faces, strict=True)
                )
                if not math.isclose(power[b.id][i], expected_power, rel_tol=1e-10, abs_tol=1e-8):
                    raise ValueError("boundary heat does not match the declared flux/convection condition")
        for i in range(1, count):
            expected_energy = cumulative[b.id][i - 1] + power[b.id][i] * (times[i] - times[i - 1])
            if not math.isclose(cumulative[b.id][i], expected_energy, rel_tol=1e-12, abs_tol=1e-10):
                raise ValueError("boundary energy is not backward-Euler endpoint quadrature")
    residuals = []
    accepted = []
    equation_accepted = []
    equation_errors = trajectory["maximum_cell_equation_residual_J"]
    if (
        len(equation_errors) != count
        or (count and equation_errors[0] != 0)
        or any(not math.isfinite(v) or v < 0 for v in equation_errors)
    ):
        raise ValueError("native cell-equation residual rows are invalid")
    for i in range(1, count):
        change = stored[i] - stored[i - 1]
        energies = [cumulative[b.id][i] - cumulative[b.id][i - 1] for b in request.boundaries]
        residual = change - sum(energies)
        scale = max(abs(change), sum(abs(e) for e in energies))
        residuals.append(residual)
        accepted.append(
            abs(residual) <= request.absolute_energy_tolerance_J + request.relative_energy_tolerance * scale
        )
        cell_storage_scale = (
            capacity
            * length
            * sum(area * abs(a - b) for area, a, b in zip(areas, rows[i], rows[i - 1], strict=True))
        )
        equation_accepted.append(
            equation_errors[i]
            <= request.absolute_energy_tolerance_J
            + request.relative_energy_tolerance * max(cell_storage_scale, sum(abs(e) for e in energies))
        )
    lower, upper = request.temperature_support_K
    support = all(lower <= v <= upper for row in rows for v in row) and all(
        lower <= v <= upper
        for b in request.boundaries
        for row in trajectory["boundary_surface_temperature_K"][b.id]
        for v in row
    )
    return {
        "stored_energy_change_J": stored,
        "step_energy_residual_J": residuals,
        "maximum_absolute_step_energy_residual_J": max(map(abs, residuals), default=0.0),
        "energy_budget_passed": all(accepted),
        "cell_equations_passed": all(equation_accepted),
        "maximum_cell_equation_residual_J": max(equation_errors, default=0.0),
        "temperature_support_passed": support,
        "final_total_boundary_energy_into_soil_J": sum(cumulative[b.id][-1] for b in request.boundaries)
        if count
        else None,
        "final_stored_energy_change_J": stored[-1] if count else None,
        "quadrature": "backward_euler_endpoint; two-dimensional face lengths multiplied by extrusion length once",
    }


def evaluate_soil_cross_section(request: SoilCrossSectionRequest, context: RunContext):
    request = SoilCrossSectionRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": SCHEMA,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "native_called": False,
        "native": None,
        "adapter_sha256": file_digest(Path(__file__)),
        "qualification": "research_screening; no field, moisture, pipe-wall or fluid-coupling qualification",
    }
    body["input_digest"] = digest({"request": body["request"], "context": body["context"]})
    missing = _policy(request, context)
    if missing:
        body["notes"] = missing
    else:
        geometry = validate_mesh(request.mesh, request.boundaries)
        if geometry["maximum_nonorthogonality_deg"] > MAX_SUPPORTED_NONORTHOGONALITY_DEG:
            body.update(
                status="out_of_scope",
                notes=[
                    "FiPy boundary/gradient closure is qualified only for orthogonal meshes; caller review cannot override the 1e-6 degree limit"
                ],
            )
        elif request.temperature_support_K and any(
            not request.temperature_support_K[0] <= v <= request.temperature_support_K[1]
            for v in request.initial_temperature_K
        ):
            body.update(
                status="out_of_scope",
                notes=["initial soil temperature lies outside reviewed property support"],
            )
        else:
            native = _native(request, geometry)
            audit = _audit(request, native)
            body.update(native_called=True, native=native, audit=audit, status=native["status"])
            if native["status"] == "computed" and not (
                audit["energy_budget_passed"]
                and audit["cell_equations_passed"]
                and audit["temperature_support_passed"]
            ):
                body["status"] = "out_of_scope"
            body["native_digest"] = digest(native)
    body["record_digest"] = digest(body)
    return body


def verify_soil_cross_section_record(
    record, request: SoilCrossSectionRequest | None = None, context: RunContext | None = None
):
    """Check an unsigned local consistency record; hashes do not prove execution."""
    try:
        body = {k: v for k, v in record.items() if k != "record_digest"}
        if (
            record["schema"] != SCHEMA
            or digest(body) != record["record_digest"]
            or record["adapter_sha256"] != file_digest(Path(__file__))
        ):
            raise ValueError("soil record/producer digest mismatch")
        supplied = SoilCrossSectionRequest.model_validate_json(json.dumps(record["request"]))
        ctx = RunContext.model_validate_json(json.dumps(record["context"]))
        if (request is not None and request != supplied) or (context is not None and context != ctx):
            raise ValueError("soil record differs from the caller's expected request/context")
        if record["input_digest"] != digest({"request": record["request"], "context": record["context"]}):
            raise ValueError("soil input/context digest mismatch")
        missing = _policy(supplied, ctx)
        if record["native"] is None:
            if (
                record["native_called"] is not False
                or record["status"] not in {"needs_data", "out_of_scope"}
                or (record["status"] == "needs_data" and not missing)
            ):
                raise ValueError("invalid withheld soil record")
        else:
            if (
                missing
                or record["native_called"] is not True
                or record["native_digest"] != digest(record["native"])
            ):
                raise ValueError("native soil source/review/digest mismatch")
            geometry = validate_mesh(supplied.mesh, supplied.boundaries)
            classes, backend = _backend()
            _, expected_geometry = _native_mesh(supplied, geometry, classes[5])
            if record["native"]["geometry"] != expected_geometry:
                raise ValueError("stored soil geometry differs")
            if (
                record["native"]["backend"] != backend
                or record["native"]["backend"]["version"] != supplied.expected_fipy_version
            ):
                raise ValueError("current FiPy backend differs from the reviewed record")
            audit = _audit(supplied, record["native"])
            reconstructed = _native(
                supplied, geometry, temperature_replay=record["native"]["trajectory"]["temperature_K"]
            )
            if reconstructed["status"] != "computed":
                raise ValueError("stored states could not be reconstructed through current FiPy")
            for field in ("boundary_heat_rate_into_soil_W", "boundary_surface_temperature_K"):
                original = record["native"]["trajectory"][field]
                expected = reconstructed["trajectory"][field]

                def aligned(a, b):
                    if isinstance(a, dict):
                        return set(a) == set(b) and all(aligned(a[k], b[k]) for k in a)
                    if isinstance(a, list):
                        return len(a) == len(b) and all(aligned(x, y) for x, y in zip(a, b, strict=True))
                    return math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-8)

                if not aligned(original, expected):
                    raise ValueError(
                        "stored boundary heat/surface is inconsistent with actual FiPy gradients and cell states"
                    )
            if not aligned(
                record["native"]["trajectory"]["maximum_cell_equation_residual_J"],
                reconstructed["trajectory"]["maximum_cell_equation_residual_J"],
            ):
                raise ValueError("stored cell-equation residual differs from actual FiPy reconstruction")
            status = record["native"]["status"]
            if status == "computed" and not (
                audit["energy_budget_passed"]
                and audit["cell_equations_passed"]
                and audit["temperature_support_passed"]
            ):
                status = "out_of_scope"
            if record["audit"] != audit or record["status"] != status:
                raise ValueError("stored soil energy/status semantics differ")
        return {
            "valid": True,
            "meaning": "unsigned local byte/contract/observable energy consistency; not independent execution or field validation",
        }
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frames(record, context: RunContext, *, allow_partial=False):
    if record.get("status") != "computed" and not (
        allow_partial is True and record.get("status") == "partial"
    ):
        raise ValueError("completed soil record required; partial prefix needs explicit opt-in")
    if (
        not verify_soil_cross_section_record(record, context=context)["valid"]
        or not record["audit"]["energy_budget_passed"]
        or not record["audit"]["cell_equations_passed"]
        or not record["audit"]["temperature_support_passed"]
    ):
        raise ValueError("soil record/context/budget/support verification failed")
    request = SoilCrossSectionRequest.model_validate_json(json.dumps(record["request"]))
    trajectory = record["native"]["trajectory"]
    frames = []
    for i, time in enumerate(trajectory["time_s"]):
        values = {f"cell.{j}.temperature_K": v for j, v in enumerate(trajectory["temperature_K"][i])}
        units = {key: "K" for key in values}
        for b in request.boundaries:
            key = f"boundary.{b.id}.backward_euler_power_into_soil_W"
            values[key] = trajectory["boundary_heat_rate_into_soil_W"][b.id][i] if i else None
            units[key] = "W"
            key = f"boundary.{b.id}.cumulative_energy_into_soil_J"
            values[key] = trajectory["cumulative_boundary_energy_into_soil_J"][b.id][i]
            units[key] = "J"
        frames.append(
            SignalFrame(
                stream_id=f"{request.asset_id}:{request.event_id}",
                clock_id=context.clock_id,
                time_s=time + request.clock_offset_s,
                values=values,
                units=units,
                valid={key: value is not None for key, value in values.items()},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode="soil-native-complete"
                if record["status"] == "computed"
                else "soil-native-partial-prefix",
            )
        )
    return tuple(frames)
