"""Conservative radial pipe-wall heating with an inner gas film boundary.

The supplied outer heat-flux history is an effective circumferentially uniform
local loading. A caller-supplied thermal ceiling is observed, not calibrated.
"""
from __future__ import annotations

import math

import numpy as np
from pydantic import Field

from .contracts import Contract, Name, Nonnegative, Positive, digest


class WeldHeatStage(Contract):
    duration_s: Positive
    effective_outer_heat_flux_w_m2: Nonnegative
    gas_temperature_k: Positive
    gas_film_w_m2_k: Nonnegative


class WeldWallThermalLimitRequest(Contract):
    basis_id: Name
    inside_radius_m: Positive
    wall_thickness_m: Positive
    conductivity_w_m_k: Positive
    density_kg_m3: Positive
    specific_heat_j_kg_k: Positive
    initial_temperature_k: Positive
    supplied_inner_surface_ceiling_k: Positive
    stages: tuple[WeldHeatStage, ...] = Field(min_length=1)
    radial_cells: int = Field(default=24, ge=3, le=1000)
    maximum_step_s: Positive
    maximum_steps: int = Field(default=100000, ge=1)
    radial_constant_property_heat_flux_and_film_basis_reviewed: bool = False


def evaluate_weld_wall_thermal_limit(request: WeldWallThermalLimitRequest):
    """Integrate backward-Euler finite-volume heat storage per unit pipe length.

    Inner film and the half-cell cylindrical conduction resistance are combined
    in series. Each step reports the same gas heat transfer used by the solver,
    allowing stored energy and applied/net heat to be reconciled. Refinement is
    caller controlled; no unexecuted convergence or burn-through claim is made.
    """
    q = WeldWallThermalLimitRequest.model_validate(request.model_dump())
    base = dict(request_digest=digest(q), basis_id=q.basis_id,
                burnthrough_or_weld_acceptance_qualified=False,
                melting_or_material_failure_predicted=False)
    if not q.radial_constant_property_heat_flux_and_film_basis_reviewed:
        return dict(status="needs_data", **base)
    steps = sum(math.ceil(s.duration_s / q.maximum_step_s) for s in q.stages)
    if steps > q.maximum_steps:
        raise ValueError("requested integration exceeds maximum_steps")
    from scipy.linalg import solve_banded

    n = q.radial_cells
    faces = np.linspace(q.inside_radius_m, q.inside_radius_m + q.wall_thickness_m, n + 1)
    centers = np.sqrt((faces[1:] ** 2 + faces[:-1] ** 2) / 2)
    capacities = q.density_kg_m3 * q.specific_heat_j_kg_k * math.pi * np.diff(faces ** 2)
    links = 2 * math.pi * q.conductivity_w_m_k / np.log(centers[1:] / centers[:-1])
    temperature = np.full(n, q.initial_temperature_k, dtype=float)
    elapsed = applied = removed = 0.0
    peak_inner = q.initial_temperature_k
    peak_wall = q.initial_temperature_k
    first_ceiling_step_end_s = 0.0 if peak_inner >= q.supplied_inner_surface_ceiling_k else None
    history = []
    for stage_index, stage in enumerate(q.stages):
        count = math.ceil(stage.duration_s / q.maximum_step_s)
        dt = stage.duration_s / count
        film_conductance = (0.0 if stage.gas_film_w_m2_k == 0 else
            1 / (math.log(centers[0] / faces[0]) / (2 * math.pi * q.conductivity_w_m_k)
                 + 1 / (2 * math.pi * faces[0] * stage.gas_film_w_m2_k)))
        outer_power = 2 * math.pi * faces[-1] * stage.effective_outer_heat_flux_w_m2
        matrix = np.zeros((3, n))
        matrix[1] = capacities / dt
        matrix[1, 0] += film_conductance
        for i, conductance in enumerate(links):
            matrix[1, i] += conductance
            matrix[1, i + 1] += conductance
            matrix[0, i + 1] = -conductance
            matrix[2, i] = -conductance
        for _ in range(count):
            rhs = capacities / dt * temperature
            rhs[0] += film_conductance * stage.gas_temperature_k
            rhs[-1] += outer_power
            temperature = solve_banded((1, 1), matrix, rhs)
            if not np.all(np.isfinite(temperature)) or np.any(temperature <= 0):
                raise ValueError("thermal solution leaves finite positive-temperature support")
            gas_power = film_conductance * (temperature[0] - stage.gas_temperature_k)
            inner_face = (temperature[0] if stage.gas_film_w_m2_k == 0 else
                stage.gas_temperature_k + gas_power / (2 * math.pi * faces[0] * stage.gas_film_w_m2_k))
            outer_face = temperature[-1] + outer_power * math.log(faces[-1] / centers[-1]) / (2 * math.pi * q.conductivity_w_m_k)
            elapsed += dt
            applied += outer_power * dt
            removed += gas_power * dt
            peak_inner = max(peak_inner, float(inner_face))
            peak_wall = max(peak_wall, float(np.max(temperature)), float(outer_face))
            if first_ceiling_step_end_s is None and inner_face >= q.supplied_inner_surface_ceiling_k:
                first_ceiling_step_end_s = elapsed
        history.append(dict(stage_index=stage_index, time_s=elapsed,
                            inner_surface_temperature_k=float(inner_face),
                            outer_surface_temperature_k=float(outer_face)))
    storage = float(np.dot(capacities, temperature - q.initial_temperature_k))
    return dict(status="computed", **base, duration_s=elapsed,
                radii_m=centers.tolist(), final_cell_temperature_k=temperature.tolist(),
                stage_end_observations=history, peak_sampled_inner_surface_temperature_k=peak_inner,
                peak_sampled_wall_temperature_k=peak_wall,
                supplied_inner_ceiling_margin_k=q.supplied_inner_surface_ceiling_k - peak_inner,
                first_ceiling_step_end_s=first_ceiling_step_end_s,
                boundary_event_resolution_s=q.maximum_step_s,
                applied_heat_j_per_m=applied, gas_removed_heat_j_per_m=removed,
                stored_heat_j_per_m=storage, energy_residual_j_per_m=applied - removed - storage)
