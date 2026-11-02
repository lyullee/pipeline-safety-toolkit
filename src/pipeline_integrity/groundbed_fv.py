"""Voxel-electrode conduction in a heterogeneous finite earth domain.

Cell conductivities represent supplied soil layers or backfill. Electrode cell
groups share one potential; the exterior faces selected by the caller form the
zero-potential return. Unselected exterior faces are insulating. The finite
boundary and voxel geometry require domain/mesh refinement for accurate earth
resistance; no infinite-domain or smooth-metal geometry accuracy is inferred.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu

from .contracts import Contract, Name, Positive, json_value

Face = Literal['x_min', 'x_max', 'y_min', 'y_max', 'z_min', 'z_max']


class VoxelEarthElectrode(Contract):
    id: Name
    cells_xyz: tuple[tuple[int, int, int], ...] = Field(min_length=1)


class ConductiveEarthRequest(Contract):
    x_edges_m: tuple[float, ...] = Field(min_length=2)
    y_edges_m: tuple[float, ...] = Field(min_length=2)
    z_depth_edges_m: tuple[float, ...] = Field(min_length=2)
    conductivity_S_m_by_cell: tuple[Positive, ...] = Field(min_length=1)
    electrodes: tuple[VoxelEarthElectrode, ...] = Field(min_length=1, max_length=32)
    grounded_faces: tuple[Face, ...] = ('x_min', 'x_max', 'y_min', 'y_max', 'z_max')
    prescribed_electrode_currents_A: dict[Name, float] | None = None
    prescribed_electrode_potentials_V: dict[Name, float] | None = None
    coordinate_frame_id: Name
    material_assignment_id: Name
    potential_reference_id: Name
    heterogeneous_ohmic_mesh_and_finite_exterior_boundary_reviewed: Literal[True]
    relative_balance_tolerance: Positive = 1e-8
    absolute_current_tolerance_A: Positive = 1e-10
    absolute_power_tolerance_W: Positive = 1e-10

    @model_validator(mode='after')
    def mesh_and_excitation(self):
        for edges in (self.x_edges_m, self.y_edges_m, self.z_depth_edges_m):
            if any(b <= a or not math.isfinite(b-a) for a, b in zip(edges, edges[1:])):
                raise ValueError('orthogonal mesh edges must strictly increase')
        if self.z_depth_edges_m[0] < 0:
            raise ValueError('z is nonnegative depth below the top reference plane')
        nx, ny, nz = (len(e)-1 for e in (self.x_edges_m, self.y_edges_m, self.z_depth_edges_m))
        count = nx*ny*nz
        if count > 200000 or len(self.conductivity_S_m_by_cell) != count:
            raise ValueError('one conductivity per cell required; maximum 200000 cells')
        if not self.grounded_faces or len(set(self.grounded_faces)) != len(self.grounded_faces):
            raise ValueError('at least one distinct grounded exterior face is required')
        ids = [e.id for e in self.electrodes]
        if len(set(ids)) != len(ids):
            raise ValueError('electrode IDs must be distinct')
        occupied = {}
        for electrode in self.electrodes:
            for cell in electrode.cells_xyz:
                if cell in occupied:
                    raise ValueError('electrode cell groups must be disjoint, without repeated cells')
                if any(i < 0 or i >= n for i, n in zip(cell, (nx, ny, nz))):
                    raise ValueError('electrode cell outside supplied mesh')
                ix, iy, iz = cell
                touched_faces = (('x_min', ix == 0), ('x_max', ix == nx-1),
                                 ('y_min', iy == 0), ('y_max', iy == ny-1),
                                 ('z_min', iz == 0), ('z_max', iz == nz-1))
                if any(touches and face in self.grounded_faces for face, touches in touched_faces):
                    raise ValueError('electrode metal cannot contact an exterior grounded face')
                occupied[cell] = electrode.id
        for cell, electrode_id in occupied.items():
            for axis in range(3):
                neighbor = list(cell)
                neighbor[axis] += 1
                other = occupied.get(tuple(neighbor))
                if other is not None and other != electrode_id:
                    raise ValueError('distinct electrode groups cannot share a metal face; merge electrically connected groups')
        if (self.prescribed_electrode_currents_A is None) == (self.prescribed_electrode_potentials_V is None):
            raise ValueError('select electrode currents or electrode potentials, not both')
        excitation = self.prescribed_electrode_currents_A if self.prescribed_electrode_currents_A is not None else self.prescribed_electrode_potentials_V
        if set(excitation) != set(ids):
            raise ValueError('every electrode needs exactly one excitation coordinate')
        return self


def evaluate_conductive_earth(q: ConductiveEarthRequest) -> dict:
    """Solve -div(sigma grad V)=0 and extract a coupled electrode admittance.

    The flattened cell order is ((iz*ny)+iy)*nx+ix. Series half-cell
    resistances give a harmonic face conductance across soil interfaces.
    An electrode occupies its whole voxel: its face is at the imposed metal
    potential, so only the neighboring soil half-cell contributes resistance.
    Conductivity entries inside electrode voxels are ignored.
    Eliminating ordinary cells for unit electrode voltages yields the complete
    symmetric electrode conductance matrix, including mutual interactions.
    """
    q = ConductiveEarthRequest.model_validate(q.model_dump(mode='python'))
    dx, dy, dz = (np.diff(edges) for edges in (q.x_edges_m, q.y_edges_m, q.z_depth_edges_m))
    nx, ny, nz = len(dx), len(dy), len(dz)
    n = nx*ny*nz
    ne = len(q.electrodes)

    def cell_index(ix, iy, iz):
        return (iz*ny+iy)*nx+ix

    electrode_by_cell = {}
    for e, electrode in enumerate(q.electrodes):
        for xyz in electrode.cells_xyz:
            electrode_by_cell[cell_index(*xyz)] = e
    nf = n-len(electrode_by_cell)
    labels = np.empty(n, dtype=int)
    ordinary = 0
    for c in range(n):
        if c in electrode_by_cell:
            labels[c] = nf+electrode_by_cell[c]
        else:
            labels[c] = ordinary
            ordinary += 1
    sigma = np.asarray(q.conductivity_S_m_by_cell, dtype=float)
    ri, ci, data = [], [], []
    interior_faces = []
    ground_links = []

    def connect(a, b, area, width_a, width_b):
        i, j = labels[a], labels[b]
        if i == j:
            return
        resistance_a = 0. if a in electrode_by_cell else width_a/(2*sigma[a])
        resistance_b = 0. if b in electrode_by_cell else width_b/(2*sigma[b])
        conductance = area/(resistance_a+resistance_b)
        if not math.isfinite(conductance) or conductance <= 0:
            raise ValueError('positive finite face conductance is outside the numerical scale support')
        interior_faces.append((a, b, conductance))
        ri.extend((i, i, j, j))
        ci.extend((i, j, i, j))
        data.extend((conductance, -conductance, -conductance, conductance))

    def ground(a, face, area, width):
        if face not in q.grounded_faces:
            return
        conductance = 2*sigma[a]*area/width
        if not math.isfinite(conductance) or conductance <= 0:
            raise ValueError('positive finite ground-face conductance is outside the numerical scale support')
        ground_links.append((a, face, conductance))
        ri.append(labels[a])
        ci.append(labels[a])
        data.append(conductance)

    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                a = cell_index(ix, iy, iz)
                if ix+1 < nx:
                    b = a+1
                    connect(a, b, dy[iy]*dz[iz], dx[ix], dx[ix+1])
                if iy+1 < ny:
                    b = a+nx
                    connect(a, b, dx[ix]*dz[iz], dy[iy], dy[iy+1])
                if iz+1 < nz:
                    b = a+nx*ny
                    connect(a, b, dx[ix]*dy[iy], dz[iz], dz[iz+1])
                if ix == 0:
                    ground(a, 'x_min', dy[iy]*dz[iz], dx[ix])
                if ix == nx-1:
                    ground(a, 'x_max', dy[iy]*dz[iz], dx[ix])
                if iy == 0:
                    ground(a, 'y_min', dx[ix]*dz[iz], dy[iy])
                if iy == ny-1:
                    ground(a, 'y_max', dx[ix]*dz[iz], dy[iy])
                if iz == 0:
                    ground(a, 'z_min', dx[ix]*dy[iy], dz[iz])
                if iz == nz-1:
                    ground(a, 'z_max', dx[ix]*dy[iy], dz[iz])
    matrix = coo_matrix((data, (ri, ci)), shape=(nf+ne, nf+ne)).tocsc()
    if not np.all(np.isfinite(matrix.data)):
        raise ValueError('summed matrix conductance exceeds finite numerical support')
    if nf:
        response = splu(matrix[:nf, :nf]).solve(-matrix[:nf, nf:].toarray())
        admittance = matrix[nf:, nf:].toarray()+matrix[nf:, :nf]@response
    else:
        response = np.empty((0, ne))
        admittance = matrix.toarray()
    ids = [e.id for e in q.electrodes]
    if not np.all(np.isfinite(admittance)):
        raise ValueError('conductance assembly exceeded finite numerical support')
    resistance = np.linalg.solve(admittance, np.eye(ne))
    if q.prescribed_electrode_currents_A is not None:
        currents = np.array([q.prescribed_electrode_currents_A[e] for e in ids])
        voltage = np.linalg.solve(admittance, currents)
    else:
        voltage = np.array([q.prescribed_electrode_potentials_V[e] for e in ids])
        currents = admittance@voltage
    reduced = np.concatenate((response@voltage, voltage))
    field = reduced[labels]
    residual = matrix@reduced
    ordinary_residual = float(np.max(np.abs(residual[:nf]))) if nf else 0.
    reconstructed_currents = residual[nf:]
    flux_by_face = {face: 0. for face in q.grounded_faces}
    for c, face, g in ground_links:
        flux_by_face[face] += g*field[c]
    ground_current = math.fsum(flux_by_face.values())
    power = float(voltage@currents)
    dissipation = math.fsum(g*(field[a]-field[b])**2 for a, b, g in interior_faces)
    dissipation += math.fsum(g*field[c]**2 for c, _, g in ground_links)
    charge_residual = float(math.fsum(currents)-ground_current)
    energy_residual = power-dissipation
    electrode_residual = float(np.max(np.abs(reconstructed_currents-currents)))
    current_scale = float(np.sum(np.abs(currents)))
    current_tolerance = q.absolute_current_tolerance_A+q.relative_balance_tolerance*current_scale
    power_tolerance = q.absolute_power_tolerance_W+q.relative_balance_tolerance*abs(power)
    symmetric_error = float(np.max(np.abs(admittance-admittance.T)))
    conductance_scale = float(np.max(np.abs(admittance)))
    if not (np.all(np.isfinite(field)) and np.all(np.isfinite(resistance)) and math.isfinite(power) and math.isfinite(dissipation)):
        raise ValueError('earth solution exceeded finite numerical support')
    converged = (max(ordinary_residual, electrode_residual, abs(charge_residual)) <= current_tolerance
                 and abs(energy_residual) <= power_tolerance
                 and symmetric_error <= q.relative_balance_tolerance*conductance_scale)
    return json_value(dict(status='computed' if converged else 'not_converged', electrode_ids=ids,
                electrode_conductance_matrix_S=admittance.tolist(),
                electrode_resistance_matrix_ohm=resistance.tolist(),
                electrode_potentials_V=dict(zip(ids, voltage.tolist())),
                electrode_currents_into_earth_A=dict(zip(ids, currents.tolist())),
                cell_potentials_V=field.tolist(), cell_shape_xyz=(nx, ny, nz),
                cell_flattening='((iz*ny)+iy)*nx+ix',
                outward_ground_current_by_face_A=flux_by_face,
                charge_balance_residual_A=charge_residual,
                maximum_ordinary_cell_current_residual_A=ordinary_residual,
                maximum_electrode_current_residual_A=electrode_residual,
                electrode_input_power_W=power, conductive_dissipation_W=dissipation,
                energy_balance_residual_W=energy_residual,
                conductance_reciprocity_absolute_error_S=symmetric_error,
                coordinate_frame_id=q.coordinate_frame_id,
                potential_reference_id=q.potential_reference_id,
                material_assignment_id=q.material_assignment_id,
                domain_truncation_and_voxel_geometry_accuracy_qualified=False))
