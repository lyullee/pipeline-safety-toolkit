"""Finite-volume transport of reviewed additive-volume liquid products.

The prescribed common volume flux transports component volumes and sensible
energy. Composition changes mixture density, heat capacity and reported
viscosity. Pressure is a supplied boundary field, not a momentum solution.
The axial dispersion coefficient acts on component-volume and energy density;
it is a reviewed effective mixing model, not a molecular diffusion closure.
"""
from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, model_validator

from .batch_transport import BatchTransportRequest, evaluate_batch_transport
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest


class BatchLiquidComponent(Contract):
    id: Name
    density_kg_m3: Positive
    heat_capacity_J_kg_K: Positive
    dynamic_viscosity_Pa_s: Positive
    evidence_id: Name


class ConservativeBatchConfiguration(Contract):
    id: Name
    property_basis_id: Name
    dispersion_model_id: Name
    thermal_model_id: Name | None = None
    components: tuple[BatchLiquidComponent, ...] = Field(min_length=2, max_length=64)
    axial_dispersion_m2_s: Nonnegative
    initial_temperature_K: Positive
    initial_pressure_Pa_abs: Positive
    sensible_energy_reference_temperature_K: Positive = 273.15
    minimum_temperature_K: Positive
    maximum_temperature_K: Positive
    cells: int = Field(default=100, ge=2, le=5000)
    maximum_step_s: Positive
    ambient_temperature_K: Positive
    ambient_conductance_per_length_W_m_K: Nonnegative = 0.0
    imposed_heat_per_length_W_m: float = 0.0
    maximum_cell_steps: int = Field(default=2000000, ge=1, le=10000000)
    maximum_scalar_field_values: int = Field(default=4000000, ge=100, le=20000000)
    maximum_result_bytes: int = Field(default=32000000, ge=4096, le=256000000)
    maximum_roundoff_volume_fraction_change: Nonnegative = 1e-12
    minimum_accepted_volume_fraction: float = Field(default=-1e-13, le=0, ge=-1e-8)
    absolute_linear_residual_tolerance: Nonnegative = 1e-12
    relative_linear_residual_tolerance: Nonnegative = 1e-10
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    additive_incompressible_volumes_and_mass_fraction_basis_reviewed: bool = False
    constant_pure_component_properties_and_log_viscosity_mixing_reviewed: bool = False
    prescribed_flow_pressure_and_dispersion_scope_reviewed: bool = False
    mechanical_energy_absent_or_in_imposed_heat_reviewed: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if len({component.id for component in self.components}) != len(self.components):
            raise ValueError("batch component property IDs must be unique")
        if self.minimum_temperature_K >= self.maximum_temperature_K:
            raise ValueError("batch property temperature support must have positive width")
        if not self.minimum_temperature_K <= self.initial_temperature_K <= self.maximum_temperature_K:
            raise ValueError("initial batch temperature is outside reviewed property support")
        return self


class ConservativeBatchRequest(Contract):
    transport: BatchTransportRequest
    solver: ConservativeBatchConfiguration


def conservative_batch_metadata():
    source = {name: file_digest(Path(__file__).with_name(name + ".py"))
              for name in ("batch_transport_solver", "batch_transport", "contracts", "datasets")}
    return dict(id="pipeline_integrity.additive_volume_batch_fv", version="1",
                source_digest=digest(source), source_sha256=source,
                numerical_dependencies={name: version(name) for name in ("numpy", "scipy")})


def _validated_pair(request, config, context):
    q = BatchTransportRequest.model_validate(request.model_dump(mode="python"))
    s = ConservativeBatchConfiguration.model_validate(config.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("batch solver asset/clock differs from context")
    if not all((s.additive_incompressible_volumes_and_mass_fraction_basis_reviewed,
                s.constant_pure_component_properties_and_log_viscosity_mixing_reviewed,
                s.prescribed_flow_pressure_and_dispersion_scope_reviewed,
                s.mechanical_energy_absent_or_in_imposed_heat_reviewed)):
        raise ValueError("batch composition, property, dispersion and mechanical-energy reviews are required")
    if set(q.component_ids) != {item.id for item in s.components}:
        raise ValueError("batch property set differs from requested component set")
    if (q.property_basis_id, q.dispersion_model_id) != (s.property_basis_id, s.dispersion_model_id):
        raise ValueError("batch property/dispersion identity differs from the reviewed solver")
    if q.thermal_mode == "energy_coupled":
        if q.thermal_model_id != s.thermal_model_id or s.thermal_model_id is None:
            raise ValueError("coupled batch thermal model identity is missing or inconsistent")
    elif (s.thermal_model_id is not None or s.ambient_conductance_per_length_W_m_K != 0
          or s.imposed_heat_per_length_W_m != 0
          or any(step.temperature_K != s.initial_temperature_K for step in q.inlet_steps)):
        raise ValueError("prescribed-temperature solver requires one fixed temperature and no heat settings")
    if any(not s.minimum_temperature_K <= step.temperature_K <= s.maximum_temperature_K
           for step in q.inlet_steps):
        raise ValueError("batch inlet temperature lies outside property support")
    required = set(s.evidence_ids) | {item.evidence_id for item in s.components}
    required |= {q.geometry_evidence_id, q.boundary_evidence_id, q.property_evidence_id}
    if q.thermal_evidence_id is not None:
        required.add(q.thermal_evidence_id)
    evidence = {item.id: item for item in c.evidence}
    if len(evidence) != len(c.evidence) or not required <= set(evidence):
        raise ValueError("batch solver properties/closure/boundaries require actual context evidence")
    if any(evidence[key].sha256 is None for key in required):
        raise ValueError("batch solver review evidence must be pinned by source SHA-256")
    if s.cells + 2 > q.maximum_position_points:
        raise ValueError("batch solver cell centres and boundary positions exceed output budget")
    return q, s, c


class ConservativeBatchTransportProvider:
    """Existing batch adapter provider with explicit bounded constitutive scope."""

    def __init__(self, configuration: ConservativeBatchConfiguration):
        self.configuration = ConservativeBatchConfiguration.model_validate(configuration.model_dump(mode="python"))
        self._configuration_digest = digest(self.configuration)
        self._source_identity = conservative_batch_metadata()

    @property
    def identity(self):
        self._check_identity()
        return {**self._source_identity, "configuration": self.configuration.model_dump(mode="json"),
                "configuration_digest": self._configuration_digest}

    def _check_identity(self):
        if (digest(self.configuration) != self._configuration_digest
                or conservative_batch_metadata() != self._source_identity):
            raise ValueError("batch provider source, dependencies or configuration changed")

    def evaluate(self, request: BatchTransportRequest, context: RunContext):
        self._check_identity()
        q, s, c = _validated_pair(request, self.configuration, context)
        request_digest, context_digest = digest(q), digest(c)
        result = _solve(q, s)
        self._check_identity()
        if digest(request) != request_digest or digest(context) != context_digest:
            raise ValueError("batch provider input/context changed during transport solve")
        return result


def _solve(q, s):
    import numpy as np
    from scipy.linalg import solve_banded

    order = list(q.component_ids)
    properties = {item.id: item for item in s.components}
    rho = np.asarray([properties[key].density_kg_m3 for key in order], dtype=float)
    cp = np.asarray([properties[key].heat_capacity_J_kg_K for key in order], dtype=float)
    log_mu = np.log([properties[key].dynamic_viscosity_Pa_s for key in order])
    n, count = s.cells, len(order)
    dx, area = q.route_length_m / n, q.cross_section_area_m2
    volume = area * dx
    diffusion_volume_flow = s.axial_dispersion_m2_s * area / dx
    ua = s.ambient_conductance_per_length_W_m_K * dx
    heat = s.imposed_heat_per_length_W_m * dx
    if not all(math.isfinite(value) for value in (dx, volume, diffusion_volume_flow, ua, heat)) or volume <= 0:
        raise ValueError("batch mesh/storage/dispersion exceeds finite arithmetic")

    def volume_fractions(composition):
        specific_volumes = np.asarray([composition.fraction_by_component[key] for key in order]) / rho
        return specific_volumes / float(np.sum(specific_volumes))

    initial_phi = volume_fractions(q.initial_composition)
    phi = np.tile(initial_phi, (n, 1))
    energy_density = np.full(n, float(initial_phi @ (rho * cp))
                             * (s.initial_temperature_K - s.sensible_energy_reference_temperature_K))
    initial_component_mass = np.sum(phi, axis=0) * volume * rho
    inlet_component_mass = np.zeros(count)
    outlet_component_mass = np.zeros(count)
    component_adjustment = np.zeros(count)
    initial_energy = float(np.sum(energy_density) * volume)
    energy_inlet = energy_outlet = heat_transferred = 0.0
    maximum_projection = 0.0
    linear_residuals = {}
    times, fractions = [], {key: [] for key in order}
    fields = {key: [] for key in ("density_kg_m3", "dynamic_viscosity_Pa_s", "temperature_K", "pressure_Pa_abs")}
    positions = [0.0, *(dx * (index + 0.5) for index in range(n)), q.route_length_m]
    schedules = []
    total_steps = 0
    for index, step in enumerate(q.inlet_steps):
        end = q.inlet_steps[index + 1].time_s if index + 1 < len(q.inlet_steps) else q.duration_s
        duration = end - step.time_s
        steps = math.ceil(duration / s.maximum_step_s)
        total_steps += steps
        schedules.append((step, end, steps))
    if total_steps * n * (count + (q.thermal_mode == "energy_coupled")) > s.maximum_cell_steps:
        raise ValueError("batch solve exceeds its scalar cell-step work budget")
    time_count = total_steps + 1
    if time_count > q.maximum_time_points:
        raise ValueError("batch accepted-step output exceeds the requested time-point budget")
    if time_count * (n + 2) * (count + 4) > s.maximum_scalar_field_values:
        raise ValueError("batch fields exceed the scalar retention budget")

    def linear_residual(matrix, solution, rhs, quantity):
        column = solution.ndim == 2
        diagonal = matrix[1, :, None] if column else matrix[1]
        lower = matrix[2, :-1, None] if column else matrix[2, :-1]
        upper = matrix[0, 1:, None] if column else matrix[0, 1:]
        diagonal_product = diagonal * solution
        residual = diagonal_product - rhs
        residual[1:] += lower * solution[:-1]
        residual[:-1] += upper * solution[1:]
        scale = max(float(np.max(np.abs(rhs))), float(np.max(np.abs(diagonal_product))), 1.0)
        error = float(np.max(np.abs(residual)))
        previous = linear_residuals.get(quantity, {"maximum_absolute": 0.0, "maximum_scaled": 0.0})
        linear_residuals[quantity] = dict(maximum_absolute=max(previous["maximum_absolute"], error),
                                         maximum_scaled=max(previous["maximum_scaled"], error / scale))
        if (not math.isfinite(error) or error > s.absolute_linear_residual_tolerance
                + s.relative_linear_residual_tolerance * scale):
            raise ValueError("batch implicit local balance equations failed their residual gate")

    def retain(time, boundary, boundary_phi):
        density = phi @ rho
        capacity = phi @ (rho * cp)
        temperature = (s.sensible_energy_reference_temperature_K + energy_density / capacity
                       if q.thermal_mode == "energy_coupled" else np.full(n, s.initial_temperature_K))
        if (not np.isfinite(temperature).all() or np.any(temperature < s.minimum_temperature_K)
                or np.any(temperature > s.maximum_temperature_K)):
            raise ValueError("batch cell temperature leaves reviewed single-phase property support")
        mass_fraction = phi * rho / density[:, None]
        boundary_w = [boundary.composition.fraction_by_component[key] for key in order]
        for component, key in enumerate(order):
            values = [boundary_w[component], *mass_fraction[:, component].tolist(), float(mass_fraction[-1, component])]
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
                raise ValueError("batch mass-fraction field exceeds the accepted convex domain")
            fractions[key].extend(values)
        boundary_rho = float(boundary_phi @ rho)
        mu = np.exp(mass_fraction @ log_mu)
        boundary_mu = math.exp(math.fsum(w * float(log_mu[i]) for i, w in enumerate(boundary_w)))
        fields["density_kg_m3"].extend([boundary_rho, *density.tolist(), float(density[-1])])
        fields["dynamic_viscosity_Pa_s"].extend([boundary_mu, *mu.tolist(), float(mu[-1])])
        fields["temperature_K"].extend([boundary.temperature_K, *temperature.tolist(), float(temperature[-1])])
        pressure = s.initial_pressure_Pa_abs if time == 0 else boundary.pressure_Pa_abs
        fields["pressure_Pa_abs"].extend([pressure] * (n + 2))
        times.append(time)

    first = q.inlet_steps[0]
    retain(0.0, first, volume_fractions(first.composition))
    for schedule_index, (boundary, end, steps) in enumerate(schedules):
        inlet_phi = volume_fractions(boundary.composition)
        inlet_energy_density = (float(inlet_phi @ (rho * cp))
                                * (boundary.temperature_K - s.sensible_energy_reference_temperature_K))
        flow = boundary.volume_flow_m3_s
        start = boundary.time_s
        dt = (end - start) / steps
        if dt <= 0 or not math.isfinite(dt) or start + dt <= start:
            raise ValueError("batch time step is below elapsed-clock resolution")
        ratio = dt / volume
        diagonal = np.full(n, 1 + ratio * (flow + 2 * diffusion_volume_flow))
        diagonal[0] = diagonal[-1] = 1 + ratio * (flow + diffusion_volume_flow)
        lower = np.full(n - 1, -ratio * (flow + diffusion_volume_flow))
        upper = np.full(n - 1, -ratio * diffusion_volume_flow)
        matrix = np.zeros((3, n))
        matrix[1] = diagonal
        matrix[0, 1:] = upper
        matrix[2, :-1] = lower
        if not np.isfinite(matrix).all():
            raise ValueError("batch time-step transport matrix is nonfinite")
        for index in range(steps):
            previous_phi = phi
            rhs = previous_phi.copy()
            rhs[0] += ratio * flow * inlet_phi
            raw_phi = solve_banded((1, 1), matrix, rhs, check_finite=True)
            linear_residual(matrix, raw_phi, rhs, "component_volume_fraction")
            if not np.isfinite(raw_phi).all() or float(np.min(raw_phi)) < s.minimum_accepted_volume_fraction:
                raise ValueError("batch transport solve violates component positivity")
            projected = np.maximum(raw_phi, 0.0)
            sums = np.sum(projected, axis=1)
            if np.any(sums <= 0) or not np.isfinite(sums).all():
                raise ValueError("batch transport solve lost cell-volume closure")
            projected /= sums[:, None]
            change = projected - raw_phi
            maximum_projection = max(maximum_projection, float(np.max(np.abs(change))))
            if maximum_projection > s.maximum_roundoff_volume_fraction_change:
                raise ValueError("batch composition requires more than the reviewed roundoff projection")
            # Projection changes both stored mass and the same-step outlet flux.
            component_adjustment += rho * (np.sum(change, axis=0) * volume + dt * flow * change[-1])
            phi = projected
            inlet_component_mass += dt * flow * inlet_phi * rho
            outlet_component_mass += dt * flow * phi[-1] * rho
            if q.thermal_mode == "energy_coupled":
                capacity = phi @ (rho * cp)
                energy_matrix = matrix.copy()
                energy_matrix[1] += dt * ua / (volume * capacity)
                energy_rhs = energy_density.copy()
                energy_rhs[0] += ratio * flow * inlet_energy_density
                energy_rhs += dt * (heat + ua * (s.ambient_temperature_K
                                                - s.sensible_energy_reference_temperature_K)) / volume
                energy_density = solve_banded((1, 1), energy_matrix, energy_rhs, check_finite=True)
                linear_residual(energy_matrix, energy_density, energy_rhs, "sensible_energy_density_J_m3")
                if not np.isfinite(energy_density).all():
                    raise ValueError("batch sensible-energy solve returned a nonfinite state")
                temperature = s.sensible_energy_reference_temperature_K + energy_density / capacity
                energy_inlet += dt * flow * inlet_energy_density
                energy_outlet += dt * flow * float(energy_density[-1])
                heat_transferred += dt * float(np.sum(heat + ua * (s.ambient_temperature_K - temperature)))
            else:
                energy_density = ((phi @ (rho * cp))
                                  * (s.initial_temperature_K - s.sensible_energy_reference_temperature_K))
            time = end if index + 1 == steps else start + (index + 1) * dt
            # A discontinuity row uses the next inlet value while inventories stay continuous.
            if index + 1 == steps and schedule_index + 1 < len(schedules):
                next_boundary = schedules[schedule_index + 1][0]
                retain(time, next_boundary, volume_fractions(next_boundary.composition))
            else:
                retain(time, boundary, inlet_phi)

    final_component_mass = np.sum(phi, axis=0) * volume * rho
    component_ledgers = {key: dict(initial=float(initial_component_mass[i]), inlet=float(inlet_component_mass[i]),
                                    outlet=float(outlet_component_mass[i]), final=float(final_component_mass[i]),
                                    numerical_adjustment=float(component_adjustment[i]))
                         for i, key in enumerate(order)}
    total_ledger = {name: math.fsum(component_ledgers[key][name] for key in order)
                    for name in ("initial", "inlet", "outlet", "final", "numerical_adjustment")}
    body = dict(request_digest=digest(q), field_layout="time_major_position_minor",
                fraction_basis="component_mass_fraction", position_meaning="inlet_boundary_cell_centres_outlet_boundary",
                density_closure="additive_incompressible_component_volumes",
                sensible_energy_reference_temperature_K=s.sensible_energy_reference_temperature_K,
                viscosity_closure="mass_fraction_logarithmic_pure_component_mixing",
                pressure_meaning="uniform_prescribed_inlet_schedule_not_momentum_solution",
                dispersion_boundary="Danckwerts_total_inlet_flux_zero_dispersive_outlet_flux",
                time_s=times, position_m=positions, fraction_by_component=fractions, **fields,
                mass_ledger_sign_convention="initial + inlet - outlet - final + numerical_adjustment = residual",
                mass_ledger_kg=total_ledger, component_mass_ledger_kg=component_ledgers,
                initial_cell_component_volume_fractions=initial_phi.tolist(), final_cell_component_volume_fractions=phi.tolist(),
                cell_volume_m3=volume, cell_centres_m=positions[1:-1],
                maximum_roundoff_volume_fraction_projection=maximum_projection,
                implicit_equation_residuals=linear_residuals,
                accepted_steps=total_steps, implicit_first_order_upwind_and_dispersion=True,
                resolved_interface_width_or_independent_validation_claim=False,
                limitations=["No pressure/flow momentum feedback, compressibility, reactions or phase transitions.",
                             "Effective axial dispersion is caller reviewed; numerical diffusion remains mesh dependent.",
                             "Pure-component properties are fixed within the supplied temperature support.",
                             "All components share zero sensible enthalpy at the supplied reference temperature; no mixing enthalpy model."])
    if q.thermal_mode == "energy_coupled":
        body.update(energy_ledger_J=dict(initial=initial_energy, inlet=energy_inlet, outlet=energy_outlet,
                                        final=float(np.sum(energy_density) * volume), heat_transfer=heat_transferred,
                                        shaft_work=0.0, numerical_adjustment=0.0),
                    energy_ledger_sign_convention="initial + inlet - outlet - final + heat_transfer + shaft_work + numerical_adjustment = residual")
    result = json_value(body)
    if len(json.dumps(result, allow_nan=False).encode("utf-8")) > s.maximum_result_bytes:
        raise ValueError("batch transport fields exceed retained-result byte budget")
    return result


def evaluate_conservative_batch_transport(request: ConservativeBatchRequest, context: RunContext):
    q = ConservativeBatchRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    provider = ConservativeBatchTransportProvider(q.solver)
    result = evaluate_batch_transport(q.transport, c, provider=provider)
    if digest(request) != digest(q) or digest(context) != digest(c):
        raise ValueError("conservative batch caller request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.conservative_batch_transport.v1", status=result["status"],
                request=q.model_dump(mode="json"), context=c.model_dump(mode="json"), result=result,
                provider=provider.identity, field_validation_claim=False)
    record = dict(body, record_digest=digest(body))
    if len(json.dumps(record, allow_nan=False).encode("utf-8")) > q.solver.maximum_result_bytes:
        raise ValueError("conservative batch receipt exceeds retained-result byte budget")
    return record


def verify_conservative_batch_transport_record(record, *, request=None, context=None):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
            {key: value for key, value in record.items() if key != "record_digest"}):
        raise ValueError("conservative batch receipt changed")
    q = ConservativeBatchRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request) != digest(q):
        raise ValueError("conservative batch caller request differs")
    if context is not None and digest(context) != digest(c):
        raise ValueError("conservative batch caller context differs")
    if evaluate_conservative_batch_transport(q, c) != record:
        raise ValueError("conservative batch source/provider/state replay differs")
    return True
