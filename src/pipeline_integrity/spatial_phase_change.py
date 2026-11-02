"""Conservative fixed-volume spatial melting/freezing on reviewed FV cells.

The analytic material enthalpy is reused from phase_material. Unknowns are cell
temperatures; latent storage enters through the exact enthalpy increment rather
than a time-lagged apparent heat capacity. Interfaces conduct equal and opposite
heat on every accepted implicit step. Liquid fraction is a diffuse local phase
state, not an explicitly tracked sharp interface or a prediction of liquid flow.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .phase_material import PhaseMaterialRequest, phase_material_state


class PhaseVolumeCell(Contract):
    id: Name
    material_id: Name
    volume_m3: Positive
    initial_temperature_K: Positive
    evidence_id: Name


class PhaseConductionFace(Contract):
    id: Name
    left_cell_id: Name
    right_cell_id: Name
    left_half_length_over_area_m_inv: Positive
    right_half_length_over_area_m_inv: Positive
    evidence_id: Name


class PhaseThermalBoundary(Contract):
    id: Name
    cell_id: Name
    effective_conductance_W_K: Nonnegative
    evidence_id: Name


class SpatialPhaseWindow(Contract):
    id: Name
    duration_s: Positive
    maximum_step_s: Positive
    boundary_temperature_K: tuple[Positive, ...] = ()
    cell_heat_W: tuple[float, ...]
    evidence_id: Name


class SpatialPhaseRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    materials: tuple[PhaseMaterialRequest, ...] = Field(min_length=1, max_length=200)
    cells: tuple[PhaseVolumeCell, ...] = Field(min_length=1, max_length=2000)
    faces: tuple[PhaseConductionFace, ...] = Field(default=(), max_length=10000)
    boundaries: tuple[PhaseThermalBoundary, ...] = Field(default=(), max_length=2000)
    windows: tuple[SpatialPhaseWindow, ...] = Field(min_length=1, max_length=10000)
    absolute_cell_energy_tolerance_J: Positive
    relative_cell_energy_tolerance: Positive = Field(default=1e-8, le=1e-3)
    maximum_residual_evaluations: int = Field(default=100000, ge=1, le=10000000)
    maximum_steps: int = Field(default=20000, ge=1, le=100000)
    maximum_solver_evaluations_per_step: int = Field(default=100, ge=1, le=10000)
    maximum_result_bytes: int = Field(default=32000000, ge=1000, le=256000000)
    orthogonal_face_paths_and_fixed_volumes_reviewed: bool = False
    conduction_only_no_advection_or_volume_change_reviewed: bool = False
    boundary_conductances_and_heat_sources_reviewed: bool = False

    @model_validator(mode="after")
    def layout(self):
        for values, key in ((self.cells, "id"), (self.faces, "id"),
                            (self.boundaries, "id"), (self.windows, "id"),
                            (self.materials, "material_id")):
            if len({getattr(item, key) for item in values}) != len(values):
                raise ValueError("spatial phase identifiers must be unique within each collection")
        materials = {m.material_id: m for m in self.materials}
        cell_ids = {cell.id for cell in self.cells}
        pairs = set()
        for cell in self.cells:
            if cell.material_id not in materials:
                raise ValueError("phase cell material is absent")
            phase_material_state(materials[cell.material_id], cell.initial_temperature_K)
        for face in self.faces:
            if (face.left_cell_id not in cell_ids or face.right_cell_id not in cell_ids
                    or face.left_cell_id == face.right_cell_id):
                raise ValueError("phase conduction face must join distinct existing cells")
            pair = frozenset((face.left_cell_id, face.right_cell_id))
            if pair in pairs:
                raise ValueError("aggregate parallel face paths explicitly before evaluation")
            pairs.add(pair)
        if any(b.cell_id not in cell_ids for b in self.boundaries):
            raise ValueError("phase thermal boundary cell is absent")
        for window in self.windows:
            if len(window.cell_heat_W) != len(self.cells):
                raise ValueError("heat-source array must follow every request cell exactly")
            if len(window.boundary_temperature_K) != len(self.boundaries):
                raise ValueError("ambient-temperature array must follow every request boundary exactly")
        if sum(math.ceil(w.duration_s / w.maximum_step_s) for w in self.windows) > self.maximum_steps:
            raise ValueError("requested phase integration exceeds step budget")
        return self


def spatial_phase_metadata():
    return {"spatial_phase_change_sha256": file_digest(Path(__file__)),
            "phase_material_sha256": file_digest(Path(__file__).with_name("phase_material.py"))}


def evaluate_spatial_phase_change(request: SpatialPhaseRequest, context: RunContext):
    import numpy as np
    import scipy
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix

    q = SpatialPhaseRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("spatial phase asset/clock differs from context")
    if not all((q.orthogonal_face_paths_and_fixed_volumes_reviewed,
                q.conduction_only_no_advection_or_volume_change_reviewed,
                q.boundary_conductances_and_heat_sources_reviewed)):
        raise ValueError("fixed-volume conduction, face geometry and thermal-boundary reviews are required")
    evidence_ids = [e.id for e in c.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("spatial phase evidence IDs must be unambiguous")
    required = {x.evidence_id for values in (q.materials, q.cells, q.faces, q.boundaries, q.windows)
                for x in values}
    if not required <= set(evidence_ids):
        raise ValueError("spatial phase material/geometry/boundary source evidence is absent")
    if any(not m.apparent_capacity_and_transition_interval_reviewed for m in q.materials):
        raise ValueError("each spatial phase material requires a transition-interval review")
    pins = spatial_phase_metadata()
    request_pin, context_pin = digest(q), digest(c)
    materials = {m.material_id: m for m in q.materials}
    cell_material = [materials[cell.material_id] for cell in q.cells]
    n = len(q.cells)
    index = {cell.id: i for i, cell in enumerate(q.cells)}
    volume = np.array([cell.volume_m3 for cell in q.cells])
    lower = np.array([m.temperature_support_K[0] for m in cell_material])
    upper = np.array([m.temperature_support_K[1] for m in cell_material])
    temperature = np.array([cell.initial_temperature_K for cell in q.cells])
    face_indices = [(index[f.left_cell_id], index[f.right_cell_id], f) for f in q.faces]
    boundary_indices = [(index[b.cell_id], b) for b in q.boundaries]
    sparsity = lil_matrix((n, n), dtype=int)
    sparsity.setdiag(1)
    for i, j, _ in face_indices:
        sparsity[i, j] = sparsity[j, i] = 1
    sparsity = sparsity.tocsr()
    evaluations = 0

    def properties(values):
        rows = [phase_material_state(m, float(t)) for m, t in zip(cell_material, values)]
        return (np.array([r["volumetric_enthalpy_J_m3"] for r in rows]),
                np.array([r["conductivity_W_m_K"] for r in rows]),
                [r["liquid_fraction"] for r in rows])

    def fluxes(values, conductivity, window):
        net = np.array(window.cell_heat_W, dtype=float)
        face_power, boundary_power = [], []
        for i, j, face in face_indices:
            resistance = (face.left_half_length_over_area_m_inv / conductivity[i]
                          + face.right_half_length_over_area_m_inv / conductivity[j])
            power = (values[j] - values[i]) / resistance
            net[i] += power
            net[j] -= power
            face_power.append(float(power))
        for (i, boundary), ambient in zip(boundary_indices, window.boundary_temperature_K):
            power = boundary.effective_conductance_W_K * (ambient - values[i])
            net[i] += power
            boundary_power.append(float(power))
        if not np.all(np.isfinite(net)):
            raise ValueError("spatial phase thermal powers exceeded finite arithmetic")
        return net, face_power, boundary_power

    enthalpy, _, fraction = properties(temperature)
    initial_energy = float(volume @ enthalpy)
    if not math.isfinite(initial_energy):
        raise ValueError("spatial phase initial storage exceeds finite arithmetic")
    time = q.start_time_s
    total_source_heat = total_boundary_heat = 0.0
    cumulative_cell_residual = np.zeros(n)
    states = [dict(time_s=time, temperature_K=temperature.tolist(), liquid_fraction=fraction,
                   total_enthalpy_J=initial_energy)]
    steps = []
    retained_bytes = len(json.dumps(states).encode("utf-8"))
    failure = None
    try:
        for window in q.windows:
            count = math.ceil(window.duration_s / window.maximum_step_s)
            dt = window.duration_s / count
            for local_step in range(count):
                if not math.isfinite(dt) or dt <= 0 or time + dt <= time:
                    raise ValueError("spatial phase step is not representable on the declared clock")
                previous_enthalpy = enthalpy.copy()
                previous_temperature = temperature.copy()
                initial_net, _, _ = fluxes(temperature, properties(temperature)[1], window)
                scale = (q.absolute_cell_energy_tolerance_J
                         + q.relative_cell_energy_tolerance * np.abs(dt * initial_net))
                if not np.all(np.isfinite(scale)) or np.any(scale <= 0):
                    raise ValueError("spatial phase step normalization exceeds finite positive arithmetic")

                def residual(trial):
                    nonlocal evaluations
                    evaluations += 1
                    if evaluations > q.maximum_residual_evaluations:
                        raise ValueError("spatial phase total residual-evaluation budget exhausted")
                    h, conductivity, _ = properties(trial)
                    power, _, _ = fluxes(trial, conductivity, window)
                    result = (volume * (h - previous_enthalpy) - dt * power) / scale
                    if not np.all(np.isfinite(result)):
                        raise ValueError("spatial phase residual exceeded finite arithmetic")
                    return result

                fit = least_squares(residual, previous_temperature, bounds=(lower, upper),
                                    jac_sparsity=sparsity if n > 1 else None, method="trf", x_scale="jac",
                                    ftol=1e-11, xtol=1e-11, gtol=1e-11,
                                    max_nfev=q.maximum_solver_evaluations_per_step)
                candidate = fit.x
                h, conductivity, fractions = properties(candidate)
                power, face_power, boundary_power = fluxes(candidate, conductivity, window)
                cell_residual = volume * (h - previous_enthalpy) - dt * power
                # Standing enthalpy relative to a material's arbitrary reference
                # must not loosen convergence. Gate against actual step storage
                # and thermal work, independently of the optimizer scaling.
                gate = q.absolute_cell_energy_tolerance_J+q.relative_cell_energy_tolerance*np.maximum(
                    np.abs(volume*(h-previous_enthalpy)),np.abs(dt*power))
                if (not fit.success or not np.all(np.isfinite(gate))
                        or not np.all(np.isfinite(cell_residual)) or np.any(np.abs(cell_residual) > gate)):
                    raise ValueError("spatial phase nonlinear step failed the cell-energy convergence gate")
                source_heat = dt * math.fsum(window.cell_heat_W)
                boundary_heat = dt * math.fsum(boundary_power)
                global_residual = float(volume @ (h - previous_enthalpy)) - source_heat - boundary_heat
                if not math.isfinite(global_residual) or abs(global_residual) > float(np.sum(gate)):
                    raise ValueError("spatial phase step failed the global-energy convergence gate")
                state_row = dict(time_s=time + dt, temperature_K=candidate.tolist(),
                                 liquid_fraction=fractions, total_enthalpy_J=float(volume @ h))
                step_row = dict(window_id=window.id, local_step=local_step, duration_s=dt,
                                  face_heat_into_left_J=[dt * v for v in face_power],
                                  boundary_heat_into_cell_J=[dt * v for v in boundary_power],
                                  source_heat_J=source_heat, boundary_heat_J=boundary_heat,
                                  cell_energy_residual_J=cell_residual.tolist(),
                                  energy_residual_J=global_residual,
                                  solver_status=int(fit.status), solver_evaluations=int(fit.nfev))
                retained_bytes += len(json.dumps([state_row, step_row]).encode("utf-8"))
                if retained_bytes > q.maximum_result_bytes:
                    raise ValueError("spatial phase retained trajectory exceeded its result-byte budget")
                # Only a converged, bounded state is promoted. Failed trials do
                # not add heat to the accepted trajectory or its energy ledger.
                temperature, enthalpy = candidate.copy(), h
                time += dt
                total_source_heat += source_heat
                total_boundary_heat += boundary_heat
                cumulative_cell_residual += cell_residual
                states.append(state_row)
                steps.append(step_row)
    except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as error:
        failure = str(error)
    if spatial_phase_metadata() != pins or digest(q) != request_pin or digest(c) != context_pin:
        raise ValueError("spatial phase implementation/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.spatial_phase_change.v1",
                request=q.model_dump(mode="json"), context=c.model_dump(mode="json"),
                status="withheld" if failure else "computed_diagnostic", failure=failure,
                cell_order=[cell.id for cell in q.cells], face_order=[f.id for f in q.faces],
                boundary_order=[b.id for b in q.boundaries], states=states, steps=steps,
                source_heat_J=total_source_heat, boundary_heat_J=total_boundary_heat,
                cell_cumulative_energy_residual_J=cumulative_cell_residual.tolist(),
                energy_residual_J=float(volume @ enthalpy)-initial_energy-total_source_heat-total_boundary_heat,
                residual_evaluations=evaluations, implementation=pins,
                numerical_backend={"numpy": np.__version__, "scipy": scipy.__version__},
                enthalpy_reference="per_material_solid_at_solidus_fixed_material_volumes",
                sharp_interface_tracking=False, convection_or_mass_transport_solved=False,
                numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("spatial phase complete receipt exceeds its result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_spatial_phase_change_record(record, *, request=None, context=None):
    if digest({k: v for k, v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("spatial phase receipt changed")
    q = SpatialPhaseRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("spatial phase caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("spatial phase caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("a withheld partial spatial phase trajectory is not a completed replay")
    if evaluate_spatial_phase_change(q, c) != record:
        raise ValueError("spatial phase replay differs")
    return True


def spatial_phase_signal_frames(record, *, cell_id: str, stream_id: str):
    """Send one replayed cell's thermal state to existing monitoring APIs."""
    verify_spatial_phase_change_record(record)
    q = SpatialPhaseRequest.model_validate_json(json.dumps(record["request"]))
    if cell_id not in record["cell_order"]:
        raise ValueError("spatial phase signal cell is absent")
    i = record["cell_order"].index(cell_id)
    units = {"temperature_K": "K", "liquid_fraction": "1"}
    frames = tuple(SignalFrame(stream_id=stream_id, clock_id=q.clock_id,
                              time_s=row["time_s"], origin="simulation",
                              values={key: row[key][i] for key in units}, units=units,
                              valid={key: True for key in units},
                              operating_mode="fixed_volume_spatial_phase") for row in record["states"])
    receipt = dict(schema="pipeline_integrity.spatial_phase_frames.v1",
                   source_record_digest=record["record_digest"], frames_digest=digest(frames),
                   cell_id=cell_id, stream_id=stream_id, asset_id=q.asset_id, clock_id=q.clock_id,
                   origin="simulation", time_interpolation_added=False)
    receipt["record_digest"] = digest(receipt)
    return frames, receipt
