"""P1 soil heat exchange assembled by scikit-fem on reviewed polygon cavities.

Nodal values are explicit inputs.  No conversion from cell averages, mesh
clipping, temperature clipping, wall model or fluid inventory is inferred.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .soil_cross_section import (
    SoilCrossSectionBoundary,
    SoilCrossSectionMesh,
    _expected_clock,
    _loop,
    _value,
    validate_mesh,
)

SCHEMA = "pipeline_integrity.soil_fem.v1"
REVIEWS = (
    "polygon_cavities_and_axially_invariant_geometry",
    "explicit_P1_nodal_initial_and_Dirichlet_fields",
    "constant_soil_properties_and_temperature_support",
    "boundary_histories_clocks_and_face_order",
    "triangulation_quadrature_and_operator_diagnostics",
    "no_curved_geometry_moisture_wall_or_fluid_coupling",
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
    "power_tolerance": "W",
}
History = tuple[tuple[float | None, ...], ...]
QUALIFICATION = "P1 polygon soil research; no curved geometry, moisture, wall, fluid or field qualification"
GENERAL_GALERKIN_REVIEW = "general_polygon_Galerkin_energy_residual_without_discrete_maximum_principle"
POLYLINE_GROUND_REVIEW = "declared_piecewise_linear_ground_surface_and_outer_domain"
NATURAL_HISTORY_KEYS = ("heat_flux_into_soil_W_m2","convection_W_m2_K","ambient_temperature_K",
                        "global_heat_flux_x_W_m2","global_heat_flux_y_W_m2",
                        "emissivity","radiation_environment_temperature_K")


class SoilFEMBoundary(Contract):
    """Natural fields use declared face order or the listed boundary vertex IDs.

    Scalar heat flux is positive into soil. Global x/y flux components are the
    physical heat-flow vector, integrated as minus its native outward-normal
    dot product; no cell-center projection or averaged boundary normal is used.
    """
    id: Name
    role: Literal["ground", "pipe", "far_field"]
    component_asset_id: Name | None = None
    kind: Literal["temperature", "heat_flux", "heat_flux_vector", "convection", "radiation", "convection_radiation", "adiabatic"]
    node_ids: tuple[int, ...] = ()
    nodal_temperature_K: History | None = None
    heat_flux_into_soil_W_m2: History | None = None
    convection_W_m2_K: History | None = None
    ambient_temperature_K: History | None = None
    natural_field_representation: Literal["face_constant","boundary_vertex_linear"] = "face_constant"
    global_heat_flux_x_W_m2: History | None = None
    global_heat_flux_y_W_m2: History | None = None
    emissivity: History | None = None
    radiation_environment_temperature_K: History | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def histories(self):
        allowed = {
            "temperature": {"nodal_temperature_K"},
            "heat_flux": {"heat_flux_into_soil_W_m2"},
            "heat_flux_vector": {"global_heat_flux_x_W_m2","global_heat_flux_y_W_m2"},
            "convection": {"convection_W_m2_K", "ambient_temperature_K"},
            "radiation": {"emissivity","radiation_environment_temperature_K"},
            "convection_radiation": {"convection_W_m2_K","ambient_temperature_K","emissivity","radiation_environment_temperature_K"},
            "adiabatic": set(),
        }[self.kind]
        if (self.role == "pipe") != (self.component_asset_id is not None):
            raise ValueError("only pipe boundaries require a component asset")
        if self.kind != "temperature" and self.natural_field_representation=="face_constant" and self.node_ids:
            raise ValueError("face-constant natural boundaries cannot carry node identities")
        if self.kind=="temperature" and self.natural_field_representation!="face_constant":
            raise ValueError("Dirichlet nodal fields do not use the natural-field representation selector")
        if self.kind=="adiabatic" and self.natural_field_representation!="face_constant":
            raise ValueError("an adiabatic boundary carries no interpolated natural field")
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("Dirichlet node identifiers must be unique")
        for key in ("nodal_temperature_K",*NATURAL_HISTORY_KEYS):
            rows = getattr(self, key)
            if rows is not None:
                if key not in allowed:
                    raise ValueError("unrelated boundary history")
                if any(
                    v is not None
                    and (("temperature" in key and v <= 0) or (key == "convection_W_m2_K" and v < 0))
                    for row in rows
                    for v in row
                ):
                    raise ValueError("Kelvin temperatures must be positive and h nonnegative")
                if key=="emissivity" and any(v is not None and not 0<=v<=1 for row in rows for v in row):
                    raise ValueError("surface emissivity must lie between zero and one")
        return self


class SoilFEMRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    event_id: Name
    clock_offset_s: Nonnegative
    mode: Literal["steady", "transient"]
    mesh: SoilCrossSectionMesh
    time_s: tuple[Nonnegative, ...] = Field(min_length=1, max_length=501)
    maximum_step_s: Positive
    history_interpolation: Literal["linear", "previous_knot_hold"]
    initial_node_temperature_K: tuple[Positive | None, ...]
    conductivity_W_m_K: Positive | None
    volumetric_heat_capacity_J_m3_K: Positive | None
    temperature_support_K: tuple[Positive, Positive] | None
    boundaries: tuple[SoilFEMBoundary, ...] = Field(min_length=2, max_length=100)
    triangulation: Literal["local_delaunay", "convex_fan"]
    mass_scheme: Literal["vertex_quadrature_lumped", "consistent"]
    robin_quadrature: Literal["vertex_lumped", "consistent"]
    operator_acceptance: Literal["monotone_comparison","Galerkin_energy_residual"] = "monotone_comparison"
    boundary_quadrature_order: int = Field(default=4,ge=4,le=12)
    ground_surface_representation: Literal["horizontal","boundary_polyline"] = "horizontal"
    maximum_radiation_newton_iterations: int = Field(default=50,ge=1,le=1000)
    maximum_radiation_line_search_steps: int = Field(default=30,ge=1,le=100)
    maximum_radiation_boundary_assemblies: int = Field(default=50000,ge=1,le=1000000)
    stefan_boltzmann_W_m2_K4: Positive = 5.670374419e-8
    geometry_evidence_id: Name
    property_evidence_id: Name
    nodal_field_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    input_units: dict[str, str]
    expected_skfem_version: Name = "12.0.2"
    absolute_energy_tolerance_J: Positive = 1e-7
    absolute_power_tolerance_W: Positive = 1e-8
    relative_residual_tolerance: Positive = 1e-9
    temperature_comparison_tolerance_K: Positive = Field(default=1e-8, le=1e-5)

    @model_validator(mode="after")
    def aligned(self):
        if self.input_units != INPUT_UNITS:
            raise ValueError("explicit SI FEM input units required")
        if self.time_s[0] != 0 or any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("local clock must start at zero and increase")
        if (self.mode == "steady") != (len(self.time_s) == 1):
            raise ValueError("steady has one time; transient has at least two")
        if len(self.initial_node_temperature_K) != len(self.mesh.vertices_m):
            raise ValueError("initial P1 field must identify every original mesh vertex")
        if (
            self.temperature_support_K is not None
            and self.temperature_support_K[0] > self.temperature_support_K[1]
        ):
            raise ValueError("ordered temperature support required")
        names = [b.id for b in self.boundaries]
        if len(set(names)) != len(names) or set(names) != set(self.mesh.boundary_face_groups):
            raise ValueError("boundaries must identify each common mesh group exactly once")
        if not any(b.role == "pipe" for b in self.boundaries) or not any(
            b.role == "ground" for b in self.boundaries
        ):
            raise ValueError("ground and genuine pipe cavity boundaries required")
        dirichlet = {}
        for boundary in self.boundaries:
            faces = self.mesh.boundary_face_groups[boundary.id]
            if any(f < 0 or f >= len(self.mesh.face_vertex_ids) for f in faces):
                raise ValueError("boundary face index outside mesh")
            nodes = {v for f in faces for v in self.mesh.face_vertex_ids[f]}
            nodal=boundary.kind=="temperature" or boundary.natural_field_representation=="boundary_vertex_linear"
            if nodal and set(boundary.node_ids) != nodes:
                raise ValueError("boundary node IDs must exactly cover this group's declared vertices")
            count = len(boundary.node_ids) if nodal else len(faces)
            for key in ("nodal_temperature_K",*NATURAL_HISTORY_KEYS):
                rows = getattr(boundary, key)
                if rows is not None and (
                    len(rows) != len(self.time_s) or any(len(row) != count for row in rows)
                ):
                    raise ValueError("boundary rows must preserve time knots and declared node/face order")
            if boundary.kind == "temperature" and boundary.nodal_temperature_K is not None:
                for j, node in enumerate(boundary.node_ids):
                    values = tuple(row[j] for row in boundary.nodal_temperature_K)
                    if node in dirichlet and values != dirichlet[node]:
                        raise ValueError("conflicting Dirichlet corner histories; no averaging is permitted")
                    dirichlet[node] = values
        if self.mode == "transient" and any(
            values[0] is not None
            and self.initial_node_temperature_K[node] is not None
            and values[0] != self.initial_node_temperature_K[node]
            for node, values in dirichlet.items()
        ):
            raise ValueError("initial P1 Dirichlet nodes must match the initial boundary trace")
        steps = sum(
            math.ceil((b - a) / self.maximum_step_s)
            for a, b in zip(self.time_s, self.time_s[1:], strict=False)
        )
        if (
            steps > 2000
            or (steps + 1)
            * (len(self.mesh.vertices_m) + len(self.mesh.cell_face_ids) + len(self.boundaries) * 4)
            > 300000
        ):
            raise ValueError("native step/field history budget exceeded")
        if any(b.kind in {"radiation","convection_radiation"} for b in self.boundaries):
            if self.robin_quadrature!="consistent" or self.operator_acceptance!="Galerkin_energy_residual":
                raise ValueError("nonlinear radiation requires consistent facet quadrature and explicit Galerkin energy/residual acceptance")
            if self.boundary_quadrature_order<6:
                raise ValueError("P1 emissivity/temperature radiation forms require at least degree-six facet quadrature")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - {*REVIEWS,GENERAL_GALERKIN_REVIEW,POLYLINE_GROUND_REVIEW}:
            raise ValueError("unknown FEM applicability review")
        return self


def boundary_metadata(request):
    result = {
        "location_id": request.location_id,
        "event_id": request.event_id,
        "clock_offset_s": request.clock_offset_s,
        "extrusion_length_m": request.mesh.axial_extrusion_length_m,
        "coordinate_plane": "x_y_metres",
        "temperature_field": "explicit_P1_vertex_nodes",
        "pipe_geometry": "declared_polygon_cavities",
    }
    if request.operator_acceptance!="monotone_comparison" or request.ground_surface_representation!="horizontal" or any(
        b.kind in {"heat_flux_vector","radiation","convection_radiation"}
        or b.natural_field_representation!="face_constant" for b in request.boundaries):
        result.update(operator_acceptance=request.operator_acceptance,
                      boundary_quadrature_order=request.boundary_quadrature_order,
                      natural_boundary_topology_digest=digest([dict(id=b.id,kind=b.kind,node_ids=b.node_ids,
                          field=b.natural_field_representation) for b in request.boundaries]),
                      geometry_representation="piecewise_straight_polygon_facets")
        result.update(ground_surface_representation=request.ground_surface_representation,
                      ground_surface_y_m_role="actual_horizontal_surface" if request.ground_surface_representation=="horizontal" else "declared_upper_envelope_only")
    return result


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("FEM request/context asset or clock differs")
    ids = [e.id for e in context.evidence]
    needed = {
        request.geometry_evidence_id,
        request.property_evidence_id,
        request.nodal_field_evidence_id,
        *request.review_evidence_ids.values(),
        *(b.evidence_id for b in request.boundaries),
    }
    if (
        len(set(ids)) != len(ids)
        or len(set(request.evidence_ids)) != len(request.evidence_ids)
        or not set(request.evidence_ids) <= set(ids)
        or not needed <= set(request.evidence_ids)
    ):
        raise ValueError("geometry/property/nodal/boundary/review evidence must be selected and present")
    if context.metadata.get("soil_fem_boundary") != boundary_metadata(request):
        raise ValueError("RunContext must bind the P1 field, soil event/location/offset/extrusion")
    missing = [
        f"review/evidence required: {key}"
        for key in REVIEWS
        if request.applicability_reviews.get(key) is not True or key not in request.review_evidence_ids
    ]
    if request.operator_acceptance=="Galerkin_energy_residual" and (
        request.applicability_reviews.get(GENERAL_GALERKIN_REVIEW) is not True
        or GENERAL_GALERKIN_REVIEW not in request.review_evidence_ids):
        missing.append("review/evidence required: "+GENERAL_GALERKIN_REVIEW)
    if request.ground_surface_representation=="boundary_polyline" and (
        request.applicability_reviews.get(POLYLINE_GROUND_REVIEW) is not True
        or POLYLINE_GROUND_REVIEW not in request.review_evidence_ids):
        missing.append("review/evidence required: "+POLYLINE_GROUND_REVIEW)
    if request.conductivity_W_m_K is None or request.temperature_support_K is None:
        missing.append("constant conductivity and temperature support required")
    if request.mode == "transient" and (
        request.volumetric_heat_capacity_J_m3_K is None
        or any(v is None for v in request.initial_node_temperature_K)
    ):
        missing.append("constant capacity and explicit complete nodal initial temperature required")
    for b in request.boundaries:
        for key in {
            "temperature": ("nodal_temperature_K",),
            "heat_flux": ("heat_flux_into_soil_W_m2",),
            "heat_flux_vector": ("global_heat_flux_x_W_m2","global_heat_flux_y_W_m2"),
            "convection": ("convection_W_m2_K", "ambient_temperature_K"),
            "radiation": ("emissivity","radiation_environment_temperature_K"),
            "convection_radiation": ("convection_W_m2_K","ambient_temperature_K","emissivity","radiation_environment_temperature_K"),
            "adiabatic": (),
        }[b.kind]:
            rows = getattr(b, key)
            if rows is None or any(v is None for row in rows for v in row):
                missing.append("resolved boundary history required: " + b.id + "/" + key)
    if (
        request.mode == "steady"
        and not any(b.kind == "temperature" for b in request.boundaries)
        and not any(
            b.kind in {"convection","convection_radiation"}
            and b.convection_W_m2_K is not None
            and any(v is not None and v > 0 for row in b.convection_W_m2_K for v in row)
            for b in request.boundaries
        )
        and not any(b.kind in {"radiation","convection_radiation"} and b.emissivity is not None
                    and any(v is not None and v>0 for row in b.emissivity for v in row)
                    for b in request.boundaries)
    ):
        missing.append("steady thermal reference requires a Dirichlet trace or positive Robin conductance")
    return missing


def _geometry(request):
    descriptors = tuple(
        SoilCrossSectionBoundary(
            id=b.id,
            role=b.role,
            component_asset_id=b.component_asset_id,
            kind="adiabatic",
            evidence_id=b.evidence_id,
        )
        for b in request.boundaries
    )
    return validate_mesh(request.mesh, descriptors,ground_surface_representation=request.ground_surface_representation)


def _backend():
    import numpy as np
    import scipy
    import scipy.sparse.linalg._dsolve._superlu as superlu
    import scipy.spatial._qhull as qhull
    import skfem as fem
    from scipy.spatial import Delaunay
    from skfem.models.poisson import laplace, mass

    installed = importlib.metadata.distribution("scikit-fem")
    tree = hashlib.sha256()
    paths = sorted(
        p
        for p in installed.files
        if str(p).replace("\\", "/").startswith("skfem/") and str(p).endswith(".py")
    )
    for path in paths:
        tree.update(str(path).replace("\\", "/").encode())
        tree.update(installed.locate_file(path).read_bytes())
    license_path = next(p for p in installed.files if str(p).replace("\\", "/").endswith("licenses/LICENSE"))
    receipt = {
        "name": "scikit-fem",
        "version": fem.__version__,
        "source_tree_files": len(paths),
        "source_tree_sha256": tree.hexdigest(),
        "license": "BSD-3-Clause",
        "license_sha256": file_digest(installed.locate_file(license_path)),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "qhull_binary_sha256": file_digest(Path(qhull.__file__)),
        "superlu_binary_sha256": file_digest(Path(superlu.__file__)),
        "common_polygon_contract_sha256": file_digest(Path(__file__).with_name("soil_cross_section.py")),
    }
    return np, fem, Delaunay, laplace, mass, receipt


def _matrix_digest(matrix):
    matrix = matrix.tocsr(copy=True)
    matrix.sort_indices()
    return digest(
        {
            "shape": list(matrix.shape),
            "data": hashlib.sha256(matrix.data.tobytes()).hexdigest(),
            "indices": hashlib.sha256(matrix.indices.tobytes()).hexdigest(),
            "indptr": hashlib.sha256(matrix.indptr.tobytes()).hexdigest(),
        }
    )


def _setup(request, geometry):
    np, fem, Delaunay, laplace, mass, backend = _backend()
    if backend["version"] != request.expected_skfem_version:
        raise ValueError("scikit-fem differs from the reviewed version")
    points = np.asarray(request.mesh.vertices_m)
    triangles, parents = [], []
    for parent, faces in enumerate(request.mesh.cell_face_ids):
        polygon = _loop(faces, request.mesh.face_vertex_ids)
        local = (
            Delaunay(points[polygon]).simplices
            if request.triangulation == "local_delaunay" and len(polygon) > 3
            else [(0, j, j + 1) for j in range(1, len(polygon) - 1)]
        )
        for row in local:
            triangles.append(tuple(polygon[int(i)] for i in row))
            parents.append(parent)
    mesh = fem.MeshTri(
        np.ascontiguousarray(points.T), np.ascontiguousarray(np.asarray(triangles).T)
    ).oriented()
    if not np.array_equal(mesh.p.T, points) or set(mesh.t.ravel()) != set(range(len(points))):
        raise ValueError("native triangulation changed or dropped original vertex/node identities")
    lookup = {tuple(sorted(pair)): i for i, pair in enumerate(mesh.facets.T)}
    groups = {
        key: np.asarray([lookup[tuple(sorted(request.mesh.face_vertex_ids[f]))] for f in faces], dtype=int)
        for key, faces in request.mesh.boundary_face_groups.items()
    }
    all_faces = np.concatenate(list(groups.values()))
    if len(set(all_faces)) != len(all_faces) or set(all_faces) != set(mesh.boundary_facets()):
        raise ValueError("native triangulation failed to preserve exact cavity/outer boundary facets")
    basis = fem.Basis(mesh, fem.ElementTriP1(), intorder=4)
    triangle_areas = np.sum(basis.dx, axis=1)
    parent_areas = np.bincount(parents, weights=triangle_areas, minlength=len(geometry["cell_areas_m2"]))
    if not np.allclose(parent_areas, geometry["cell_areas_m2"], rtol=1e-11, atol=1e-12):
        raise ValueError("native triangles do not exactly partition the supplied soil polygons")
    length = request.mesh.axial_extrusion_length_m
    local_stiffness = laplace.elemental(basis)
    stiffness_scale=length*request.conductivity_W_m_K
    K = stiffness_scale*local_stiffness.tocsr()
    element_stiffness=stiffness_scale*local_stiffness.tolocal()
    M = None
    element_mass=None
    if request.mode == "transient":
        mass_basis = basis
        if request.mass_scheme == "vertex_quadrature_lumped":
            mass_basis = fem.Basis(
                mesh,
                fem.ElementTriP1(),
                quadrature=(np.asarray([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]), np.full(3, 1 / 6)),
            )
        local_mass=mass.elemental(mass_basis)
        mass_scale=length*request.volumetric_heat_capacity_J_m3_K
        M = mass_scale*local_mass.tocsr()
        element_mass=mass_scale*local_mass.tolocal()
    facet_geometry={}
    for boundary in request.boundaries:
        facets=groups[boundary.id]
        fb=fem.FacetBasis(mesh,fem.ElementTriP1(),facets=facets,intorder=request.boundary_quadrature_order)
        if not np.array_equal(np.asarray(fb.find),facets):
            raise ValueError("native facet integration changed the declared boundary face order")
        source_lengths=np.array([math.dist(request.mesh.vertices_m[request.mesh.face_vertex_ids[f][0]],
                                          request.mesh.vertices_m[request.mesh.face_vertex_ids[f][1]])
                                 for f in request.mesh.boundary_face_groups[boundary.id]])
        if not np.allclose(np.sum(fb.dx,axis=1),source_lengths,rtol=1e-12,atol=1e-14):
            raise ValueError("native facet quadrature does not reproduce the actual polygon edge lengths")
        normals=np.asarray(fb.normals)
        centroid=np.mean(mesh.p[:,mesh.t[:,fb.tind]],axis=1)
        midpoint=np.mean(mesh.p[:,mesh.facets[:,facets]],axis=1)
        if (not np.allclose(np.sum(normals*normals,axis=0),1.0,rtol=1e-12,atol=1e-12)
            or np.any(np.sum(normals*(midpoint-centroid)[:,:,None],axis=0)<=0)):
            raise ValueError("native boundary normals are not outward unit normals of the supplied soil mesh")
        facet_geometry[boundary.id]=dict(source_face_ids=list(request.mesh.boundary_face_groups[boundary.id]),
            native_facet_ids=facets.tolist(),quadrature_coordinates_m=np.asarray(fb.global_coordinates()).tolist(),
            outward_unit_normals=normals.tolist(),quadrature_extruded_areas_m2=(length*fb.dx).tolist(),
            source_edge_lengths_m=source_lengths.tolist(),quadrature_role="physical_gradient_and_exact_polygon_geometry")
        natural_quadrature=(np.asarray([[0.0,1.0]]),np.asarray([0.5,0.5])) if (
            boundary.kind=="convection" and request.robin_quadrature=="vertex_lumped") else None
        natural_fb=fem.FacetBasis(mesh,fem.ElementTriP1(),facets=facets,
                                 intorder=request.boundary_quadrature_order,quadrature=natural_quadrature)
        if not np.array_equal(np.asarray(natural_fb.find),facets):
            raise ValueError("native natural facet quadrature reordered the declared boundary history")
        facet_geometry[boundary.id]["natural_operator_quadrature"]=(None if boundary.kind=="temperature" else dict(
            coordinates_m=np.asarray(natural_fb.global_coordinates()).tolist(),
            outward_unit_normals=np.asarray(natural_fb.normals).tolist(),
            extruded_areas_m2=(length*natural_fb.dx).tolist(),
            scheme="vertex_lumped" if natural_quadrature is not None else "native_gauss"))
    native_geometry = {
        "vertices_m": points.tolist(),
        "triangles": mesh.t.T.tolist(),
        "triangle_parent_cell_ids": parents,
        "triangle_areas_m2": triangle_areas.tolist(),
        "cell_areas_m2": parent_areas.tolist(),
        "boundary_facet_ids": {key: value.tolist() for key, value in groups.items()},
        "cavity_count": geometry["cavity_count"],
        "representation": "straight_P1_polygon_facets_not_curved_edges",
        "ground_surface_representation":request.ground_surface_representation,
        "cell_face_nonorthogonality_deg":geometry["maximum_nonorthogonality_deg"],
        "native_element_gradients_not_two_point_flux_projection":True,
        "boundary_facet_quadrature": facet_geometry,
        "element_dof_order": basis.element_dofs.T.tolist(),
        "element_stiffness_W_K": element_stiffness.tolist(),
        "element_capacity_J_K": None if element_mass is None else element_mass.tolist(),
        "native_elemental_to_global_assembly": True,
        "maximum_element_stiffness_row_sum_W_K":float(np.max(np.abs(np.sum(element_stiffness,axis=2)))),
    }
    return np, fem, mesh, basis, K, M, groups, parents, native_geometry, backend


def _boundary_assembly(request,np,fem,mesh,basis,groups,interval,fraction,K):
    length=request.mesh.axial_extrusion_length_m
    H=K*0.0
    flux=np.zeros(basis.N)
    environment=np.zeros(basis.N)
    loads,operators,dir_values,terms,radiation={},{},{},{},{}
    temperatures=[]
    flux_form=fem.LinearForm(lambda v,w:w.q*v)
    robin_form=fem.BilinearForm(lambda u,v,w:w.h*u*v)
    environment_form=fem.LinearForm(lambda v,w:w.h*w.env*v)
    for b in request.boundaries:
        quadrature=None
        if b.kind=="convection" and request.robin_quadrature=="vertex_lumped":
            quadrature=(np.asarray([[0.0,1.0]]),np.asarray([0.5,0.5]))
        fb=fem.FacetBasis(mesh,fem.ElementTriP1(),facets=groups[b.id],
                          intorder=request.boundary_quadrature_order,quadrature=quadrature)
        if not np.array_equal(np.asarray(fb.find),groups[b.id]):
            raise ValueError("native natural-boundary facet order differs from the source history")
        def values(rows):
            return _value(rows,interval,fraction) if len(request.time_s)>1 else list(rows[0])
        def field(rows):
            data=np.asarray(values(rows),dtype=float)
            if b.natural_field_representation=="face_constant":
                return np.broadcast_to(data[:,None],fb.dx.shape)
            nodal=np.zeros(basis.N)
            nodal[list(b.node_ids)]=data
            return np.asarray(fb.interpolate(nodal))
        loads[b.id]=np.zeros(basis.N)
        operators[b.id]=K*0.0
        term=dict(basis=fb,flux=np.zeros(fb.dx.shape),h=np.zeros(fb.dx.shape),ambient=np.zeros(fb.dx.shape))
        if b.kind=="temperature":
            prescribed=values(b.nodal_temperature_K)
            dir_values.update(zip(b.node_ids,prescribed,strict=True))
            temperatures.extend(prescribed)
        elif b.kind in {"heat_flux","heat_flux_vector"}:
            if b.kind=="heat_flux":
                q=field(b.heat_flux_into_soil_W_m2)
            else:
                normal=np.asarray(fb.normals)
                q=-(field(b.global_heat_flux_x_W_m2)*normal[0]+field(b.global_heat_flux_y_W_m2)*normal[1])
            loads[b.id]=length*fem.asm(flux_form,fb,q=q)
            flux+=loads[b.id]
            term["flux"]=q
        if b.kind in {"convection","convection_radiation"}:
            h=field(b.convection_W_m2_K)
            ambient=field(b.ambient_temperature_K)
            if np.any(h<0) or np.any(ambient<=0):
                raise ValueError("native interpolated convection field left its nonnegative/positive domain")
            operators[b.id]=length*fem.asm(robin_form,fb,h=h)
            loads[b.id]+=length*fem.asm(environment_form,fb,h=h,env=ambient)
            H+=operators[b.id]
            environment+=loads[b.id]
            term.update(h=h,ambient=ambient)
            temperatures.extend(ambient.ravel().tolist())
        if b.kind in {"radiation","convection_radiation"}:
            epsilon=field(b.emissivity)
            radiant=field(b.radiation_environment_temperature_K)
            if np.any(epsilon<0) or np.any(epsilon>1) or np.any(radiant<=0):
                raise ValueError("native interpolated radiation field left its physical support")
            radiation[b.id]=dict(basis=fb,coefficient=epsilon*request.stefan_boltzmann_W_m2_K4,environment=radiant)
            temperatures.extend(radiant.ravel().tolist())
        terms[b.id]=term
    return H,environment,flux,loads,operators,dir_values,temperatures,terms,radiation


def _radiation_assembly(request,np,fem,terms,current,counter,*,with_jacobian=True):
    from scipy.sparse import csr_matrix
    residual=np.zeros(len(current))
    jacobian=csr_matrix((len(current),len(current)))
    groups={}
    length=request.mesh.axial_extrusion_length_m
    residual_form=fem.LinearForm(lambda v,w:w.r*v)
    tangent_form=fem.BilinearForm(lambda u,v,w:w.j*u*v)
    for key,term in terms.items():
        counter[0]+=2 if with_jacobian else 1
        if counter[0]>request.maximum_radiation_boundary_assemblies:
            raise ValueError("radiation facet assembly exceeded its declared work budget")
        fb=term["basis"]
        temperature=np.asarray(fb.interpolate(current))
        environment=term["environment"]
        outward=term["coefficient"]*(temperature-environment)*(temperature+environment)*(temperature**2+environment**2)
        derivative=4*term["coefficient"]*temperature**3
        if (np.any(temperature<=0) or not np.isfinite(outward).all() or not np.isfinite(derivative).all()):
            raise ValueError("radiation residual or tangent left finite positive-temperature arithmetic")
        group=length*fem.asm(residual_form,fb,r=outward)
        residual+=group
        if with_jacobian:
            jacobian+=length*fem.asm(tangent_form,fb,j=derivative)
        groups[key]=dict(load=group,outward_flux_W_m2=outward)
    return residual,jacobian,groups


def _solve_radiation(request,np,fem,A,rhs,D,dir_values,initial,terms,counter,physical_scale,energy_power_limit):
    count=A.shape[0]
    if initial is None:
        supplied=request.initial_node_temperature_K
        initial_guess_source="supplied_nodal_initial_guess" if all(v is not None for v in supplied) else "material_support_midpoint_numerical_seed"
        current=(np.asarray(supplied,dtype=float) if all(v is not None for v in supplied)
                 else np.full(count,request.temperature_support_K[0]
                              +(request.temperature_support_K[1]-request.temperature_support_K[0])/2))
    else:
        current=initial.copy()
        initial_guess_source="previous_retained_nodal_field"
    for node,value in dir_values.items():
        current[node]=value
    low,high=request.temperature_support_K
    if not np.isfinite(current).all() or np.any(current<low) or np.any(current>high):
        raise ValueError("nonlinear radiation initial iterate/Dirichlet field exceeds material temperature support")
    free=np.setdiff1d(np.arange(count),D)
    history=[]
    for iteration in range(request.maximum_radiation_newton_iterations+1):
        radiation,J,_=_radiation_assembly(request,np,fem,terms,current,counter)
        residual=A@current+radiation-rhs
        if not np.isfinite(residual).all():
            raise ValueError("radiation assembled residual exceeds finite arithmetic")
        norm=float(np.max(np.abs(residual[free]),initial=0.0))
        limit=request.absolute_power_tolerance_W+request.relative_residual_tolerance*physical_scale(current,radiation)
        global_error=abs(float(np.sum(residual[free])))
        global_limit=energy_power_limit(current,radiation,residual)
        if not math.isfinite(limit) or not math.isfinite(global_limit) or min(limit,global_limit)<=0:
            raise ValueError("radiation residual/energy tolerances exceed finite positive arithmetic")
        merit=max(norm/limit,global_error/global_limit)
        entry=dict(iteration=iteration,maximum_free_residual_W=norm,residual_limit_W=limit,
                   global_energy_residual_W=global_error,global_energy_limit_W=global_limit)
        history.append(entry)
        if norm<=limit and global_error<=global_limit:
            return current,dict(converged=True,iterations=iteration,history=history,initial_guess_source=initial_guess_source)
        if iteration==request.maximum_radiation_newton_iterations:
            break
        zero=np.zeros(count)
        delta=(fem.solve(*fem.condense(A+J,-residual,x=zero,D=D)) if len(D)
               else fem.solve(A+J,-residual))
        if not np.isfinite(delta).all():
            raise ValueError("native radiation Newton correction contains nonfinite values")
        accepted=False
        alpha=1.0
        for line_search in range(request.maximum_radiation_line_search_steps):
            candidate=current+alpha*delta
            if np.all(candidate>=low) and np.all(candidate<=high) and np.isfinite(candidate).all():
                candidate_radiation,_,_=_radiation_assembly(request,np,fem,terms,candidate,counter,with_jacobian=False)
                candidate_residual=A@candidate+candidate_radiation-rhs
                if not np.isfinite(candidate_residual).all():
                    alpha/=2
                    continue
                candidate_norm=float(np.max(np.abs(candidate_residual[free]),initial=0.0))
                candidate_limit=request.absolute_power_tolerance_W+request.relative_residual_tolerance*physical_scale(candidate,candidate_radiation)
                candidate_global=abs(float(np.sum(candidate_residual[free])))
                candidate_global_limit=energy_power_limit(candidate,candidate_radiation,candidate_residual)
                if not math.isfinite(candidate_limit) or not math.isfinite(candidate_global_limit) or min(candidate_limit,candidate_global_limit)<=0:
                    alpha/=2
                    continue
                candidate_merit=max(candidate_norm/candidate_limit,candidate_global/candidate_global_limit)
                if candidate_merit<=1 or candidate_merit<(1-1e-4*alpha)*merit:
                    entry.update(line_search_scale=alpha,line_search_trials=line_search+1)
                    current=candidate
                    accepted=True
                    break
            alpha/=2
        if not accepted:
            raise ValueError("radiation Newton line search could not remain in material support and reduce the physical residual")
    raise ValueError("radiation boundary solve exceeded its Newton iteration limit")


def _run(request, geometry, replay=None):
    np, fem, mesh, basis, K, M, groups, parents, native_geometry, backend = _setup(request, geometry)
    length = request.mesh.axial_extrusion_length_m
    gradient_form = fem.Functional(lambda w: np.sum(w["uh"].grad * w.n, axis=0))
    times, intervals, fractions = _expected_clock(request)
    if replay is not None:
        times, intervals, fractions = times[: len(replay)], intervals[: len(replay)], fractions[: len(replay)]
    group_nodes = {
        b.id: {v for f in request.mesh.boundary_face_groups[b.id] for v in request.mesh.face_vertex_ids[f]}
        for b in request.boundaries
    }
    dir_groups = [b.id for b in request.boundaries if b.kind == "temperature"]
    membership = {node: [key for key in dir_groups if node in group_nodes[key]] for node in range(basis.N)}
    unique_reactions = {
        key: all(len(membership[node]) == 1 for node in group_nodes[key]) for key in dir_groups
    }
    current = (
        np.asarray(request.initial_node_temperature_K, dtype=float) if request.mode == "transient" else None
    )
    initial = current.copy() if current is not None else None
    rows = []
    cumulative = {b.id: 0.0 for b in request.boundaries}
    cumulative_total = 0.0
    status, reason = "computed", None
    radiation_assemblies=[0]
    for index, (time, interval, fraction) in enumerate(zip(times, intervals, fractions, strict=True)):
        try:
            H,environment,flux,loads,operators,dir_values,boundary_temperatures,natural_terms,radiation_terms=(
                _boundary_assembly(request,np,fem,mesh,basis,groups,interval,fraction,K))
            D = np.asarray(sorted(dir_values), dtype=int)
            free = np.setdiff1d(np.arange(basis.N), D)
            old = current.copy() if current is not None else None
            dt = time - times[index - 1] if index else None
            if index and (not math.isfinite(dt) or dt<=0):
                raise ValueError("native FEM timestep is not positively representable on the declared clock")
            initial_row = request.mode == "transient" and index == 0
            A = K + H
            rhs = flux + environment
            if request.mode == "transient" and index:
                A = A + M / dt
                rhs = rhs + M @ old / dt
            diagonal_mass = bool(request.mode == "steady" or M.nnz == basis.N)
            sparse = A.tocoo()
            offdiag = sparse.data[sparse.row != sparse.col]
            maximum_positive = max(0.0, float(offdiag.max(initial=0.0)))
            zero = 1e-12 * max(1.0, float(np.max(np.abs(K.data))))
            operator_passed = diagonal_mass and maximum_positive <= zero and bool(np.all(A.diagonal() > 0))
            nonlinear_trace=dict(required=False,converged=True,iterations=0,history=[])
            if radiation_terms and not initial_row:
                def physical_scale(candidate,radiation):
                    storage=(np.zeros(basis.N) if M is None or not index else M@(candidate-old)/dt)
                    return max(1.0,float(np.sum(np.abs(K@candidate))),
                               float(np.sum(np.abs(flux+environment-H@candidate))),
                               float(np.sum(np.abs(radiation))),float(np.sum(np.abs(storage))))
                def energy_power_limit(candidate,radiation,residual):
                    if M is None:
                        return request.absolute_power_tolerance_W+request.relative_residual_tolerance*physical_scale(candidate,radiation)
                    increment=float(np.sum(M@(candidate-old)))
                    natural=float(np.sum(flux+environment-H@candidate-radiation))
                    total=natural+float(np.sum(residual[D]))
                    return request.absolute_energy_tolerance_J/dt+request.relative_residual_tolerance*max(abs(increment/dt),abs(total))
                current,nonlinear_trace=_solve_radiation(request,np,fem,A,rhs,D,dir_values,
                                                        old,radiation_terms,radiation_assemblies,physical_scale,energy_power_limit)
                nonlinear_trace["required"]=True
                if replay is not None:
                    retained=np.asarray(replay[index],dtype=float)
                    if retained.shape!=(basis.N,) or not np.allclose(current,retained,rtol=1e-12,atol=request.temperature_comparison_tolerance_K):
                        raise ValueError("stored radiation temperature field differs from the native nonlinear solve")
            elif replay is not None:
                current = np.asarray(replay[index], dtype=float)
                if current.shape != (basis.N,):
                    raise ValueError("stored P1 field has wrong node count")
            elif not initial_row:
                prescribed = np.zeros(basis.N)
                for node, value in dir_values.items():
                    prescribed[node] = value
                current = fem.solve(*fem.condense(A, rhs, x=prescribed, D=D)) if len(D) else fem.solve(A, rhs)
            if not np.isfinite(current).all():
                raise ValueError("native FEM solve produced a nonfinite nodal field")
            if initial_row and not np.array_equal(current, initial):
                raise ValueError("stored initial P1 field changed")
            if any(
                abs(current[node] - value) > request.temperature_comparison_tolerance_K
                for node, value in dir_values.items()
            ):
                raise ValueError("P1 field differs from the exact declared Dirichlet trace")
            radiation_residual,radiation_tangent,radiation_groups=_radiation_assembly(
                request,np,fem,radiation_terms,current,radiation_assemblies)
            if radiation_terms:
                tangent=(A+radiation_tangent).tocoo()
                offdiag=tangent.data[tangent.row!=tangent.col]
                maximum_positive=max(0.0,float(offdiag.max(initial=0.0)))
                # A nonlinear consistent radiation form is accepted through
                # its assembled energy/residual contract, never advertised as
                # a proved discrete comparison/maximum-principle operator.
                operator_passed=False
            residual = A @ current + radiation_residual - rhs if not initial_row else np.zeros(basis.N)
            if not np.isfinite(residual).all():
                raise ValueError("assembled FEM equation residual exceeds finite arithmetic")
            reactions = {str(node): float(residual[node]) for node in D} if not initial_row else {}
            weak, physical, quadrature_power, natural_consistency = {}, {}, {}, {}
            for b in request.boundaries:
                physical_basis = fem.FacetBasis(mesh, fem.ElementTriP1(), facets=groups[b.id], intorder=request.boundary_quadrature_order)
                physical[b.id] = float(
                    length
                    * request.conductivity_W_m_K
                    * gradient_form.assemble(physical_basis, uh=physical_basis.interpolate(current))
                )
                if initial_row:
                    weak[b.id] = None
                elif b.kind == "temperature":
                    weak[b.id] = (
                        float(sum(residual[node] for node in group_nodes[b.id]))
                        if unique_reactions[b.id]
                        else None
                    )
                else:
                    radiant_load=radiation_groups[b.id]["load"] if b.id in radiation_groups else np.zeros(basis.N)
                    weak[b.id] = float(np.sum(loads[b.id] - operators[b.id] @ current-radiant_load))
                if b.kind=="temperature":
                    quadrature_power[b.id]=None
                    natural_consistency[b.id]=True
                else:
                    term=natural_terms[b.id]
                    trace=np.asarray(term["basis"].interpolate(current))
                    into=term["flux"]+term["h"]*(term["ambient"]-trace)
                    if b.id in radiation_groups:
                        into=into-radiation_groups[b.id]["outward_flux_W_m2"]
                    integrated=float(length*np.sum(into*term["basis"].dx))
                    quadrature_power[b.id]=integrated
                    natural_consistency[b.id]=(True if initial_row else abs(integrated-weak[b.id])<=
                        request.absolute_power_tolerance_W+request.relative_residual_tolerance*max(abs(integrated),abs(weak[b.id])))
            dir_total = None if initial_row else float(np.sum(residual[D]))
            total = (
                None
                if initial_row
                else dir_total + sum(value for key, value in weak.items() if key not in dir_groups)
            )
            stored = None if M is None else float(np.sum(M @ (current - initial)))
            quadrature_stored = (
                None
                if M is None
                else float(
                    request.volumetric_heat_capacity_J_m3_K
                    * length
                    * np.sum(np.asarray(basis.interpolate(current - initial)) * basis.dx)
                )
            )
            quadrature_passed = M is None or abs(stored - quadrature_stored) <= (
                request.absolute_energy_tolerance_J
                + request.relative_residual_tolerance * max(abs(stored), abs(quadrature_stored))
            )
            increment = None if not index or M is None else float(np.sum(M @ (current - old)))
            energy_residual = None if increment is None else increment - total * dt
            if index and request.mode == "transient":
                cumulative_total += total * dt
                for key in cumulative:
                    cumulative[key] = (
                        None
                        if cumulative[key] is None or weak[key] is None
                        else cumulative[key] + weak[key] * dt
                    )
            minimum, maximum = None, None
            bound_passed = False
            if boundary_temperatures or old is not None:
                all_temperatures = boundary_temperatures + ([] if old is None else old.tolist())
                low, high = min(all_temperatures), max(all_temperatures)
                if request.mode == "transient":
                    weights = np.asarray(M.sum(axis=1)).ravel()
                    rates = flux / weights
                    minimum = low + (dt or 0.0) * min(0.0, float(rates.min()))
                    maximum = high + (dt or 0.0) * max(0.0, float(rates.max()))
                    bound_passed = bool(
                        np.min(current) >= minimum - request.temperature_comparison_tolerance_K
                        and np.max(current) <= maximum + request.temperature_comparison_tolerance_K
                    )
                elif radiation_terms:
                    if not np.any(flux):
                        minimum,maximum=low,high
                        bound_passed=bool(np.min(current)>=low-request.temperature_comparison_tolerance_K
                                          and np.max(current)<=high+request.temperature_comparison_tolerance_K)
                elif operator_passed:
                    lower_rhs = np.asarray(H.sum(axis=1)).ravel() * low + np.minimum(flux, 0.0)
                    upper_rhs = np.asarray(H.sum(axis=1)).ravel() * high + np.maximum(flux, 0.0)
                    lower_values, upper_values = np.full(basis.N, low), np.full(basis.N, high)
                    lower = (
                        fem.solve(*fem.condense(A, lower_rhs, x=lower_values, D=D))
                        if len(D)
                        else fem.solve(A, lower_rhs)
                    )
                    upper = (
                        fem.solve(*fem.condense(A, upper_rhs, x=upper_values, D=D))
                        if len(D)
                        else fem.solve(A, upper_rhs)
                    )
                    minimum, maximum = float(lower.min()), float(upper.max())
                    bound_passed = bool(
                        np.all(current >= lower - request.temperature_comparison_tolerance_K)
                        and np.all(current <= upper + request.temperature_comparison_tolerance_K)
                    )
            field = basis.interpolate(current)
            triangle_integrals = np.sum(np.asarray(field) * basis.dx, axis=1)
            means = np.bincount(
                parents, weights=triangle_integrals, minlength=len(geometry["cell_areas_m2"])
            ) / np.asarray(geometry["cell_areas_m2"])
            support = bool(
                np.all(current >= request.temperature_support_K[0])
                and np.all(current <= request.temperature_support_K[1])
            )
            local_error = float(np.max(np.abs(residual[free]), initial=0.0))
            # A Kelvin reference offset must not inflate the residual tolerance.
            # Use actual conduction, natural heat and storage-rate magnitudes.
            storage_rate = np.zeros(basis.N) if not index or M is None else M @ (current - old) / dt
            gross = max(
                1.0,
                float(np.sum(np.abs(K @ current))),
                float(np.sum(np.abs(flux + environment - H @ current-radiation_residual))),
                float(np.sum(np.abs(radiation_residual))),
                float(np.sum(np.abs(storage_rate))),
                abs(total or 0.0),
            )
            if not math.isfinite(gross):
                raise ValueError("FEM physical power scale exceeds finite arithmetic")
            local_pass = (
                local_error
                <= request.absolute_power_tolerance_W + request.relative_residual_tolerance * gross
            )
            energy_pass = (
                True
                if initial_row
                else (
                    abs(total)
                    <= request.absolute_power_tolerance_W + request.relative_residual_tolerance * gross
                    if request.mode == "steady"
                    else abs(energy_residual)
                    <= request.absolute_energy_tolerance_J
                    + request.relative_residual_tolerance * max(abs(increment), abs(total * dt))
                )
            )
            rows.append(
                {
                    "time_s": time,
                    "boundary_interval": interval,
                    "boundary_fraction": fraction,
                    "node_temperature_K": current.tolist(),
                    "cell_mean_from_P1_K": means.tolist(),
                    "boundary_physical_gradient_power_into_soil_W": physical,
                    "boundary_weak_power_into_soil_W": weak,
                    "boundary_natural_quadrature_power_into_soil_W":quadrature_power,
                    "natural_boundary_quadrature_consistency_passed":all(natural_consistency.values()),
                    "boundary_natural_quadrature_consistency":natural_consistency,
                    "radiation_nonlinear_solve":nonlinear_trace,
                    "nonlinear_boundary_converged":nonlinear_trace["converged"],
                    "cumulative_radiation_boundary_assemblies":radiation_assemblies[0],
                    "boundary_linear_operator_digests":{key:_matrix_digest(value) for key,value in operators.items()},
                    "radiation_tangent_digest":None if not radiation_terms else _matrix_digest(radiation_tangent),
                    "dirichlet_node_reaction_W": reactions,
                    "total_dirichlet_weak_power_W": dir_total,
                    "total_boundary_weak_power_W": total,
                    "stored_energy_change_J": stored,
                    "independent_P1_quadrature_energy_change_J": quadrature_stored,
                    "quadrature_energy_consistency_passed": bool(quadrature_passed),
                    "step_storage_increment_J": increment,
                    "step_energy_residual_J": energy_residual,
                    "cumulative_total_boundary_energy_J": cumulative_total
                    if request.mode == "transient"
                    else None,
                    "boundary_cumulative_weak_energy_J": dict(cumulative)
                    if request.mode == "transient"
                    else {key: None for key in cumulative},
                    "maximum_free_equation_residual_W": local_error,
                    "source_aware_lower_bound_K": minimum,
                    "source_aware_upper_bound_K": maximum,
                    "temperature_bound_passed": bound_passed,
                    "temperature_support_passed": support,
                    "mass_diagonal": diagonal_mass,
                    "maximum_positive_operator_offdiagonal_W_K": maximum_positive,
                    "operator_comparison_passed": operator_passed,
                    "energy_budget_passed": bool(energy_pass),
                    "local_equations_passed": bool(local_pass),
                }
            )
        except Exception as error:
            status, reason = "partial", type(error).__name__ + ": " + str(error)
            break
    return {
        "status": status,
        "reason": reason,
        "backend": backend,
        "geometry": native_geometry,
        "stiffness_digest": _matrix_digest(K),
        "capacity_digest": None if M is None else _matrix_digest(M),
        "operator_acceptance":request.operator_acceptance,
        "discrete_maximum_principle_required":request.operator_acceptance=="monotone_comparison",
        "maximum_principle_qualification_claim":False,
        "radiation_boundary_assemblies":0 if not rows else rows[-1]["cumulative_radiation_boundary_assemblies"],
        "dirichlet_group_reaction_allocatable": unique_reactions,
        "weak_power_semantics": "steady variational constraint/natural reaction"
        if request.mode == "steady"
        else "backward-Euler endpoint reaction/load over the preceding interval",
        "physical_gradient_power_is_conservative_weak_reaction": False,
        "rows": rows,
    }


def _status(native):
    rows = native["rows"]
    if any(not row["temperature_support_passed"] for row in rows):
        return "out_of_scope"
    required=["energy_budget_passed","local_equations_passed","quadrature_energy_consistency_passed",
              "natural_boundary_quadrature_consistency_passed","nonlinear_boundary_converged"]
    if native["operator_acceptance"]=="monotone_comparison":
        required.extend(("operator_comparison_passed","temperature_bound_passed"))
    if any(
        not all(
            row[key]
            for key in required
        )
        for row in rows
    ):
        return "diagnostic_rejected"
    return native["status"]


def evaluate_soil_fem(request: SoilFEMRequest, context: RunContext):
    request = SoilFEMRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": SCHEMA,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "native_called": False,
        "native": None,
        "qualification": QUALIFICATION,
        "producer_sha256": file_digest(Path(__file__)),
    }
    body["input_digest"] = digest({"request": body["request"], "context": body["context"]})
    missing = _policy(request, context)
    if missing:
        body["notes"] = missing
    else:
        geometry = _geometry(request)
        native = _run(request, geometry)
        body.update(native_called=True, native=native, native_digest=digest(native), status=_status(native))
    if body["producer_sha256"] != file_digest(Path(__file__)) or body["input_digest"] != digest({
            "request": request.model_dump(mode="json"), "context": context.model_dump(mode="json")}):
        raise ValueError("FEM source or input/context changed during evaluation")
    body["record_digest"] = digest(body)
    return body


def verify_soil_fem_record(record, request: SoilFEMRequest | None = None, context: RunContext | None = None):
    """Reassemble native physics; an unsigned digest does not prove execution."""
    try:
        if (
            record.get("schema") != SCHEMA
            or record.get("qualification") != QUALIFICATION
            or record.get("producer_sha256") != file_digest(Path(__file__))
            or record.get("record_digest")
            != digest({k: v for k, v in record.items() if k != "record_digest"})
        ):
            raise ValueError("FEM schema, source, scope or record digest differs")
        supplied = SoilFEMRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        ctx = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and supplied != request) or (context is not None and ctx != context):
            raise ValueError("FEM expected request/context differs")
        if record["input_digest"] != digest({"request": record["request"], "context": record["context"]}):
            raise ValueError("FEM input/context binding changed")
        missing = _policy(supplied, ctx)
        if record["native"] is None:
            if (
                not missing
                or record["status"] != "needs_data"
                or record["native_called"] is not False
                or record.get("notes") != missing
            ):
                raise ValueError("withheld FEM record has unsupported outputs")
        else:
            native = record["native"]
            if missing or record["native_called"] is not True or record["native_digest"] != digest(native):
                raise ValueError("FEM native/source/review binding changed")
            geometry = _geometry(supplied)
            times, intervals, fractions = _expected_clock(supplied)
            rows = native["rows"]
            if (
                native["status"] not in {"computed", "partial"}
                or len(rows) > len(times)
                or (native["status"] == "computed" and len(rows) != len(times))
            ):
                raise ValueError("FEM native completion/prefix differs")
            if [(r["time_s"], r["boundary_interval"], r["boundary_fraction"]) for r in rows] != list(
                zip(times[: len(rows)], intervals[: len(rows)], fractions[: len(rows)], strict=True)
            ):
                raise ValueError("FEM native clock is not the requested prefix")
            replayed = _run(supplied, geometry, replay=[row["node_temperature_K"] for row in rows])
            if replayed["status"] != "computed":
                raise ValueError("FEM physical reconstruction failed")

            def close(a, b):
                if isinstance(a, dict):
                    return isinstance(b, dict) and set(a) == set(b) and all(close(a[k], b[k]) for k in a)
                if isinstance(a, list):
                    return (
                        isinstance(b, list)
                        and len(a) == len(b)
                        and all(close(x, y) for x, y in zip(a, b, strict=True))
                    )
                if type(a) is float:
                    return (
                        type(b) in (int, float)
                        and math.isfinite(a)
                        and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)
                    )
                return type(a) is type(b) and a == b

            if not close(
                {k: v for k, v in native.items() if k not in {"status", "reason"}},
                {k: v for k, v in replayed.items() if k not in {"status", "reason"}},
            ):
                raise ValueError(
                    "FEM fields, geometry, operators or native source differ from reconstruction"
                )
            if record["status"] != _status(native):
                raise ValueError("FEM diagnostic/completion status changed")
        return {
            "valid": True,
            "meaning": "unsigned current-source, assembled-physics and context consistency; not independent execution or field qualification",
        }
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ImportError, OverflowError) as error:
        return {"valid": False, "reason": str(error)}


def to_signal_frames(record, context: RunContext, *, allow_partial=False):
    if record.get("status") != "computed" and not (
        allow_partial is True and record.get("status") == "partial"
    ):
        raise ValueError("computed FEM record required; partial prefix requires explicit opt-in")
    if not verify_soil_fem_record(record, context=context)["valid"] or not record["native"]["rows"]:
        raise ValueError("FEM record/context/physics verification failed")
    request = SoilFEMRequest.model_validate_json(json.dumps(record["request"]))
    frames = []
    for row in record["native"]["rows"]:
        values = {f"node.{i}.P1_temperature_K": v for i, v in enumerate(row["node_temperature_K"])}
        values.update({f"cell.{i}.mean_from_P1_K": v for i, v in enumerate(row["cell_mean_from_P1_K"])})
        units = {key: "K" for key in values}
        for b in request.boundaries:
            for label, field in (
                ("physical_gradient_endpoint_W", "boundary_physical_gradient_power_into_soil_W"),
                ("natural_quadrature_endpoint_W","boundary_natural_quadrature_power_into_soil_W"),
                (
                    "steady_weak_W" if request.mode == "steady" else "backward_euler_weak_W",
                    "boundary_weak_power_into_soil_W",
                ),
                ("cumulative_weak_J", "boundary_cumulative_weak_energy_J"),
            ):
                key = f"boundary.{b.id}.{label}"
                values[key] = row[field][b.id]
                units[key] = "J" if label.endswith("_J") else "W"
        values["total.cumulative_boundary_J"] = row["cumulative_total_boundary_energy_J"]
        units["total.cumulative_boundary_J"] = "J"
        for key, field in (
            ("total.dirichlet_weak_W", "total_dirichlet_weak_power_W"),
            ("total.boundary_weak_W", "total_boundary_weak_power_W"),
            ("total.stored_energy_change_J", "stored_energy_change_J"),
        ):
            values[key] = row[field]
            units[key] = "J" if key.endswith("_J") else "W"
        frames.append(
            SignalFrame(
                stream_id=f"{request.asset_id}:{request.event_id}:P1",
                clock_id=context.clock_id,
                time_s=row["time_s"] + request.clock_offset_s,
                values=values,
                units=units,
                valid={key: v is not None for key, v in values.items()},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode=(("soil-FEM-complete" if record["status"]=="computed" else "soil-FEM-partial-prefix")
                    if request.operator_acceptance=="monotone_comparison" else
                    ("soil-FEM-Galerkin-energy-complete" if record["status"]=="computed" else "soil-FEM-Galerkin-energy-partial-prefix")),
            )
        )
    return tuple(frames)
