"""Native finite-inventory gas/wall network with stage pressure/enthalpy solves.

The owner integrates each pipe cell; this adapter supplies topology, boundaries,
qualification gates and replay. Acoustic momentum PDEs are outside this scope.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from pathlib import Path

from pydantic import Field, model_validator

from . import gas_network, phase_change_exchange, thermo
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .thermo import StateRequest

REVIEWS = (
    "pure_gas_eos_reference_and_transport",
    "finite_inventory_geometry_and_wall_boundaries",
    "quasisteady_low_mach_half_link_domain",
    "time_step_mesh_and_conservation_budget",
)


class GasThermalCell(Contract):
    id: Name
    inlet_port_id: Name
    outlet_port_id: Name
    length_m: Positive
    inner_diameter_m: Positive
    roughness_m: Nonnegative
    local_loss_coefficient: Nonnegative
    wall_heat_capacity_J_K: Positive
    fluid_wall_UA_W_K: Nonnegative | None
    ambient_UA_W_K: Nonnegative | None
    ambient_temperature_K: Positive | None
    initial_wall_temperature_K: Positive | None
    initial_fluid_state: StateRequest
    effective_fluid_volume_m3: Positive | None = None
    minimum_inventory_to_geometry_ratio: Positive = 0.5
    maximum_inventory_to_geometry_ratio: Positive = 1.5
    fluid_volume_override_evidence_id: Name | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def volume_override_evidence(self):
        if (self.effective_fluid_volume_m3 is None) != (self.fluid_volume_override_evidence_id is None):
            raise ValueError("effective fluid volume and its separate geometry evidence must be supplied together")
        if self.minimum_inventory_to_geometry_ratio >= self.maximum_inventory_to_geometry_ratio:
            raise ValueError("inventory-to-geometry ratio limits must be ordered")
        if self.effective_fluid_volume_m3 is not None:
            geometric = math.pi * self.inner_diameter_m**2 * self.length_m / 4
            ratio = self.effective_fluid_volume_m3 / geometric
            if not self.minimum_inventory_to_geometry_ratio <= ratio <= self.maximum_inventory_to_geometry_ratio:
                raise ValueError("effective fluid volume is outside the reviewed geometry-ratio limits")
        return self


class GasThermalWindow(Contract):
    id: Name
    duration_s: Positive
    timestep_s: Positive
    maximum_reviewed_timestep_s: Positive
    left_state: StateRequest
    right_state: StateRequest
    evidence_id: Name


class GasThermalTransientRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    start_time_s: Nonnegative
    fluid: Name
    property_basis: Name
    left_boundary_id: Name
    right_boundary_id: Name
    cells: tuple[GasThermalCell, ...] = Field(min_length=1, max_length=128)
    windows: tuple[GasThermalWindow, ...] = Field(min_length=1, max_length=256)
    pressure_support_pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    maximum_mach: Positive = Field(default=0.1, le=0.1)
    maximum_link_relative_pressure_change: Positive = Field(default=0.05, le=0.1)
    maximum_link_relative_density_change: Positive = Field(default=0.05, le=0.1)
    maximum_omitted_kinetic_energy_transport_W: Nonnegative
    maximum_link_momentum_residual_Pa: Nonnegative
    mass_balance_absolute_kg: Nonnegative = 1e-8
    energy_balance_absolute_J: Nonnegative = 1e-4
    balance_relative_tolerance: Nonnegative = Field(default=1e-7, le=1e-3)
    junction_mass_tolerance_kg_s: Positive = 1e-9
    junction_energy_tolerance_W: Positive = 1e-4
    solver_relative_tolerance: Positive = Field(default=1e-9, le=1e-3)
    maximum_pressure_evaluations: int = Field(default=300, ge=1, le=10000)
    maximum_enthalpy_iterations: int = Field(default=50, ge=1, le=1000)
    maximum_state_conversions: int = Field(default=200000, ge=1, le=10000000)
    maximum_network_solves: int = Field(default=50000, ge=1, le=1000000)
    maximum_steps: int = Field(default=10000, ge=1, le=100000)
    maximum_duration_s: Positive = 86400.0
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def boundaries(self):
        if any(token in self.fluid for token in ("&", "[", "]", "::")):
            raise ValueError(
                "explicit pure HEOS fluid identifier required; mixtures need a separate owner contract"
            )
        ids = [self.left_boundary_id, self.right_boundary_id, *[c.id for c in self.cells]]
        ports = [port for c in self.cells for port in (c.inlet_port_id, c.outlet_port_id)]
        if len(set(ids)) != len(ids) or len(set(ports)) != len(ports) or set(ids) & set(ports):
            raise ValueError("boundary/cell nodes and cell material ports require distinct IDs")
        if any(name.startswith("__gas_thermal:") for name in ids + ports):
            raise ValueError("reserved internal face namespace")
        if len({w.id for w in self.windows}) != len(self.windows) or len(set(self.evidence_ids)) != len(
            self.evidence_ids
        ):
            raise ValueError("window/evidence IDs must be unique")
        for cell in self.cells:
            if (
                cell.fluid_volume_override_evidence_id is not None
                and cell.fluid_volume_override_evidence_id == cell.evidence_id
            ):
                raise ValueError("fluid-volume override needs evidence distinct from the cell geometry evidence")
            if (
                cell.fluid_volume_override_evidence_id is not None
                and cell.fluid_volume_override_evidence_id not in self.evidence_ids
            ):
                raise ValueError("fluid-volume override evidence must be selected in request evidence_ids")
        if set(self.reviews) != set(REVIEWS):
            raise ValueError("all named reviews are required")
        for state in [c.initial_fluid_state for c in self.cells] + [
            s for w in self.windows for s in (w.left_state, w.right_state)
        ]:
            if (
                state.fluid != self.fluid
                or state.backend != "HEOS"
                or not state.single_phase_required
                or set(state.inputs) != {"P", "T"}
            ):
                raise ValueError("all initial/reservoir states require the common pure HEOS P/T gas basis")
        for lo, hi in (self.pressure_support_pa_abs, self.temperature_support_K):
            if lo >= hi:
                raise ValueError("property support must be ordered")
        for w in self.windows:
            if w.timestep_s > w.duration_s or w.timestep_s > w.maximum_reviewed_timestep_s:
                raise ValueError("timestep exceeds its physical/reviewed interval")
        return self


def boundary_metadata(q):
    return {
        "asset_id": q.asset_id,
        "event_id": q.event_id,
        "clock_id": q.clock_id,
        "start_time_s": q.start_time_s,
        "property_basis": q.property_basis,
        "request_digest": digest(q),
        "topology": "ordered finite-volume cell chain with zero-storage internal faces",
    }


def native_backend_metadata(q):
    return {
        "owner": phase_change_exchange.native_backend_metadata(),
        "gas_property_adapter_sha256": file_digest(Path(gas_network.__file__)),
        "shared_property_sha256": file_digest(Path(thermo.__file__)),
        "pure_fluid_reference_markers": thermo.coolprop_reference_anchors((q.fluid,)),
        "implementation_sha256": file_digest(Path(__file__)),
        "property_basis": q.property_basis,
        "fluid": q.fluid,
        "backend": "HEOS",
        "property_cache_scope": "new native provider and run-local P/T,P/H caches each evaluation; rho/u uncached",
    }


def _policy(q, c):
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id) or c.metadata.get(
        "gas_thermal_transient_boundary"
    ) != boundary_metadata(q):
        raise ValueError("actual asset/event/clock/ports/EOS/topology context differs")
    available = [e.id for e in c.evidence]
    selected = set(q.evidence_ids)
    used = {
        *[cell.evidence_id for cell in q.cells],
        *[cell.fluid_volume_override_evidence_id for cell in q.cells if cell.fluid_volume_override_evidence_id],
        *[w.evidence_id for w in q.windows],
        *q.review_evidence_ids.values(),
    }
    if len(set(available)) != len(available) or selected - set(available) or used - selected:
        raise ValueError("geometry/boundary/review evidence must select actual unique context evidence")
    notes = ["review:" + k for k in REVIEWS if not q.reviews[k] or k not in q.review_evidence_ids]
    if not q.owner_pins:
        notes.append("reviewed native source/runtime/reference pins")
    notes += [
        f"{cell.id}:missing:{key}"
        for cell in q.cells
        for key in (
            "fluid_wall_UA_W_K",
            "ambient_UA_W_K",
            "ambient_temperature_K",
            "initial_wall_temperature_K",
        )
        if getattr(cell, key) is None
    ]
    return notes


class _TransientGasProperties(gas_network._GasProperties):
    """Extend the existing single-gas wrapper with uncached conserved-state flash."""

    def __init__(self, native, q):
        self.request = q
        super().__init__(native, q.maximum_state_conversions)

    def _convert(self, method, a, b):
        state = super()._convert(method, a, b)
        q = self.request
        if (
            not q.pressure_support_pa_abs[0] <= state.pressure_Pa <= q.pressure_support_pa_abs[1]
            or not q.temperature_support_K[0] <= state.temperature_K <= q.temperature_support_K[1]
        ):
            raise ValueError("native RK/pressure state exceeds reviewed EOS domain")
        return state

    def from_rho_u(self, rho, u):
        return self._convert(self.native.from_rho_u, rho, u)

    def viscosity_Pa_s(self, state):
        value = super().viscosity_Pa_s(state)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("native gas viscosity is unavailable")
        return value


class _EquilibriumPreservingGasLink:
    """Reuse the owner's conserved-state roundoff rule on network half-links.

    DynamicHEMPipe already excludes EOS-roundtrip flow between numerically
    identical P/h states in its direct endpoint derivative. Its raw half-pipe
    network components otherwise bypass that rule. The original pressure
    mismatch remains an explicit momentum residual, rather than being erased.
    """

    def __init__(self, component, same_thermo):
        self.component = component
        self.same_thermo = same_thermo
        self.equilibrium_roundoff_evaluations = 0

    def __getattr__(self, name):
        return getattr(self.component, name)

    def evaluate(self, left, right):
        if not self.same_thermo(left, right):
            return self.component.evaluate(left, right)
        self.equilibrium_roundoff_evaluations += 1
        native_zero = self.component.evaluate(left, left)
        return replace(native_zero, momentum_residual_Pa=left.pressure_Pa - right.pressure_Pa)

    def evaluate_mass_flow(self, left, right):
        # Pressure optimization and the complete energy/momentum evaluation must
        # share the same owner roundoff branch, including on owner versions
        # exposing a separate fast mass-only method.
        return self.evaluate(left, right).mass_flow_kg_s


def _run(q, body):
    from CoolProp.CoolProp import PropsSI
    from lh2dt.dynamic_network import DynamicNetworkSimulator, dynamic_hem_pipe_node
    from lh2dt.dynamic_pipe import DynamicHEMPipe
    from lh2dt.network import SteadyNetwork
    from lh2dt.properties import HydrogenProperties

    props = _TransientGasProperties(HydrogenProperties("HEOS::" + q.fluid), q)
    stage_records = []

    class QualifiedNetwork(SteadyNetwork):
        def solve(self, **kwargs):
            if len(stage_records) >= q.maximum_network_solves:
                raise ValueError("native network solve budget exceeded")
            kwargs.update(
                relative_tolerance=q.solver_relative_tolerance,
                max_enthalpy_iterations=q.maximum_enthalpy_iterations,
                mass_closure_absolute_tolerance_kg_s=q.junction_mass_tolerance_kg_s,
                energy_closure_absolute_tolerance_W=q.junction_energy_tolerance_W,
                closure_relative_tolerance=q.balance_relative_tolerance,
            )
            solution = super().solve(**kwargs)
            solver_gates = {
                "pressure_solver_success": solution.pressure_solver_success,
                "pressure_optimizer_success": solution.pressure_optimizer_success,
                "enthalpy_solver_success": solution.enthalpy_solver_success,
                "junction_conservation_success": solution.junction_conservation_success,
            }
            if not all(solver_gates.values()):
                failed_gates = ", ".join(
                    f"{name}={value}" for name, value in solver_gates.items() if not value
                )
                raise ValueError(
                    "native pressure/enthalpy/junction solver did not qualify; "
                    f"failed gates: {failed_gates}"
                )
            if set(solution.link_momentum_residuals_Pa) != {link.name for link in self.links}:
                raise ValueError("all hydraulic links must expose native momentum residuals")
            momentum = max(abs(r) for r in solution.link_momentum_residuals_Pa.values())
            if momentum > q.maximum_link_momentum_residual_Pa:
                raise ValueError("native hydraulic momentum residual exceeds reviewed limit")
            max_mach = max_kinetic = max_density = max_pressure = 0.0
            for link in self.links:
                left, right = (solution.node_states[n] for n in (link.left, link.right))
                flow = solution.link_flows[link.name].mass_flow_kg_s
                density_change = abs(left.density_kg_m3 - right.density_kg_m3) / max(
                    left.density_kg_m3, right.density_kg_m3
                )
                pressure_change = abs(left.pressure_Pa - right.pressure_Pa) / max(
                    left.pressure_Pa, right.pressure_Pa
                )
                area = math.pi * link.component.diameter**2 / 4
                for state in (left, right):
                    speed = float(
                        PropsSI(
                            "A",
                            "P",
                            state.pressure_Pa,
                            "Hmass",
                            state.specific_enthalpy_J_kg,
                            "HEOS::" + q.fluid,
                        )
                    )
                    if not math.isfinite(speed) or speed <= 0:
                        raise ValueError("gas acoustic property is unavailable for Mach qualification")
                    velocity = abs(flow) / state.density_kg_m3 / area
                    max_mach = max(max_mach, velocity / speed)
                    max_kinetic = max(max_kinetic, abs(flow) * velocity**2 / 2)
                max_density = max(max_density, density_change)
                max_pressure = max(max_pressure, pressure_change)
            if (
                max_mach > q.maximum_mach
                or max_kinetic > q.maximum_omitted_kinetic_energy_transport_W
                or max_density > q.maximum_link_relative_density_change
                or max_pressure > q.maximum_link_relative_pressure_change
            ):
                raise ValueError(
                    "native hydraulic stage leaves low-Mach/density/pressure/omitted-kinetic service domain: "
                    f"Mach={max_mach:.17g} (limit {q.maximum_mach:.17g}); "
                    f"kinetic_transport_W={max_kinetic:.17g} "
                    f"(limit {q.maximum_omitted_kinetic_energy_transport_W:.17g}); "
                    f"relative_density_change={max_density:.17g} "
                    f"(limit {q.maximum_link_relative_density_change:.17g}); "
                    f"relative_pressure_change={max_pressure:.17g} "
                    f"(limit {q.maximum_link_relative_pressure_change:.17g})"
                )
            stage_records.append(
                {
                    "index": len(stage_records),
                    "window_id": self.window_id,
                    "max_momentum_residual_Pa": momentum,
                    "maximum_mach": max_mach,
                    "maximum_kinetic_energy_transport_W": max_kinetic,
                    "maximum_relative_density_change": max_density,
                    "maximum_relative_pressure_change": max_pressure,
                    "native_pressure_solver_success": solution.pressure_solver_success,
                    "native_pressure_optimizer_success": solution.pressure_optimizer_success,
                    "native_enthalpy_solver_success": solution.enthalpy_solver_success,
                    "native_junction_conservation_success": solution.junction_conservation_success,
                    "junction_mass_residual_kg_s": solution.node_mass_residual_kg_s,
                    "junction_energy_residual_W": solution.node_energy_residual_W,
                    "junction_mass_closure_tolerance_kg_s": solution.node_mass_closure_tolerance_kg_s,
                    "junction_energy_closure_tolerance_W": solution.node_energy_closure_tolerance_W,
                    "native_equilibrium_roundoff_link_evaluations": {
                        link.name: link.component.equilibrium_roundoff_evaluations for link in self.links
                    },
                }
            )
            return solution

    pipes = {
        cell.id: DynamicHEMPipe(
            cell.length_m,
            cell.inner_diameter_m,
            cell.roughness_m,
            cell.wall_heat_capacity_J_K,
            local_loss_coefficient=cell.local_loss_coefficient,
            fluid_wall_UA_W_K=cell.fluid_wall_UA_W_K,
            ambient_temperature_K=cell.ambient_temperature_K,
            ambient_UA_W_K=cell.ambient_UA_W_K,
            fluid_volume_m3=cell.effective_fluid_volume_m3,
            properties=props,
        )
        for cell in q.cells
    }
    initial = {
        cell.id: pipes[cell.id].initialize(
            props.from_pT(cell.initial_fluid_state.inputs["P"], cell.initial_fluid_state.inputs["T"]),
            wall_temperature_K=cell.initial_wall_temperature_K,
        )
        for cell in q.cells
    }
    current = dict(initial)
    mass_prefix = dict.fromkeys(initial, 0.0)
    energy_prefix = dict.fromkeys(initial, 0.0)
    ambient_prefix = dict.fromkeys(initial, 0.0)
    transport_prefix = dict.fromkeys(initial, 0.0)
    elapsed = 0.0
    history = []
    for window in q.windows:
        network = QualifiedNetwork(props)
        network.window_id = window.id
        network.add_boundary(
            q.left_boundary_id, props.from_pT(window.left_state.inputs["P"], window.left_state.inputs["T"])
        )
        network.add_boundary(
            q.right_boundary_id, props.from_pT(window.right_state.inputs["P"], window.right_state.inputs["T"])
        )
        for cell in q.cells:
            network.add_boundary(cell.id, pipes[cell.id].thermo(current[cell.id]))
        for i in range(len(q.cells) - 1):
            left = pipes[q.cells[i].id].thermo(current[q.cells[i].id])
            right = pipes[q.cells[i + 1].id].thermo(current[q.cells[i + 1].id])
            network.add_junction(
                f"__gas_thermal:face:{i}",
                (left.pressure_Pa + right.pressure_Pa) / 2,
                (left.specific_enthalpy_J_kg + right.specific_enthalpy_J_kg) / 2,
            )
        for i, cell in enumerate(q.cells):
            left = q.left_boundary_id if i == 0 else f"__gas_thermal:face:{i - 1}"
            right = q.right_boundary_id if i == len(q.cells) - 1 else f"__gas_thermal:face:{i}"
            network.add_link(
                cell.id + ":in",
                left,
                cell.id,
                _EquilibriumPreservingGasLink(
                    pipes[cell.id].inlet_hydraulic_component, pipes[cell.id]._same_thermo
                ),
                source_port_id=f"__gas_thermal:port:{i}:in_face",
                target_port_id=cell.inlet_port_id,
            )
            network.add_link(
                cell.id + ":out",
                cell.id,
                right,
                _EquilibriumPreservingGasLink(
                    pipes[cell.id].outlet_hydraulic_component, pipes[cell.id]._same_thermo
                ),
                source_port_id=cell.outlet_port_id,
                target_port_id=f"__gas_thermal:port:{i}:out_face",
            )
        nodes = {
            cell.id: dynamic_hem_pipe_node(cell.id, pipes[cell.id], current[cell.id]) for cell in q.cells
        }
        results = DynamicNetworkSimulator(
            network, nodes, pressure_solver_max_nfev=q.maximum_pressure_evaluations
        ).simulate(window.duration_s, window.timestep_s)
        if not results:
            raise ValueError("owner did not integrate this short interval")
        for result in results:
            audits = {}
            for cell in q.cells:
                name = cell.id
                mass = mass_prefix[name] + result.cumulative_boundary_mass_kg[name]
                energy = energy_prefix[name] + result.cumulative_boundary_energy_J[name]
                transport = transport_prefix[name] + result.cumulative_boundary_transport_energy_J[name]
                ambient = ambient_prefix[name] + result.cumulative_boundary_ambient_heat_J[name]
                state = result.states[name]
                mass_change = state.mass_kg - initial[name].mass_kg
                energy_change = (
                    state.internal_energy_J
                    - initial[name].internal_energy_J
                    + cell.wall_heat_capacity_J_K
                    * (state.wall_temperature_K - initial[name].wall_temperature_K)
                )
                mass_limit = q.mass_balance_absolute_kg + q.balance_relative_tolerance * max(
                    abs(mass), abs(mass_change)
                )
                energy_limit = q.energy_balance_absolute_J + q.balance_relative_tolerance * max(
                    abs(energy), abs(energy_change), abs(transport), abs(ambient)
                )
                audits[name] = {
                    "cumulative_boundary_mass_kg": mass,
                    "cumulative_boundary_energy_J": energy,
                    "cumulative_transport_energy_J": transport,
                    "cumulative_ambient_heat_J": ambient,
                    "mass_inventory_change_kg": mass_change,
                    "fluid_plus_wall_energy_change_J": energy_change,
                    "mass_residual_kg": mass_change - mass,
                    "energy_residual_J": energy_change - energy,
                    "transport_plus_ambient_residual_J": transport + ambient - energy,
                    "mass_limit_kg": mass_limit,
                    "energy_limit_J": energy_limit,
                    "within_budget": abs(mass_change - mass) <= mass_limit
                    and abs(energy_change - energy) <= energy_limit
                    and abs(transport + ambient - energy) <= energy_limit,
                }
            time = q.start_time_s + elapsed + result.time_s
            if history and time <= history[-1]["time_s"]:
                raise ValueError("accepted native history is not representable on the actual clock")
            global_mass = math.fsum(a["mass_residual_kg"] for a in audits.values())
            global_energy = math.fsum(a["energy_residual_J"] for a in audits.values())
            global_mass_limit = q.mass_balance_absolute_kg + q.balance_relative_tolerance * max(
                abs(math.fsum(a["cumulative_boundary_mass_kg"] for a in audits.values())),
                abs(math.fsum(a["mass_inventory_change_kg"] for a in audits.values())),
            )
            global_energy_limit = q.energy_balance_absolute_J + q.balance_relative_tolerance * max(
                abs(math.fsum(a["cumulative_boundary_energy_J"] for a in audits.values())),
                abs(math.fsum(a["fluid_plus_wall_energy_change_J"] for a in audits.values())),
            )
            history.append(
                {
                    "time_s": time,
                    "window_id": window.id,
                    "native": asdict(result),
                    "conservation": audits,
                    "global_mass_residual_kg": global_mass,
                    "global_energy_residual_J": global_energy,
                    "global_mass_limit_kg": global_mass_limit,
                    "global_energy_limit_J": global_energy_limit,
                    "within_budget": all(a["within_budget"] for a in audits.values())
                    and abs(global_mass) <= global_mass_limit
                    and abs(global_energy) <= global_energy_limit,
                }
            )
        last = results[-1]
        if not math.isclose(last.time_s, window.duration_s, rel_tol=0, abs_tol=1e-12):
            raise ValueError("owner history did not cover requested physical window")
        elapsed += last.time_s
        current = dict(last.states)
        for name in initial:
            mass_prefix[name] += last.cumulative_boundary_mass_kg[name]
            energy_prefix[name] += last.cumulative_boundary_energy_J[name]
            ambient_prefix[name] += last.cumulative_boundary_ambient_heat_J[name]
            transport_prefix[name] += last.cumulative_boundary_transport_energy_J[name]
    valid = all(r["within_budget"] for r in history)
    body.update(
        status="computed_diagnostic" if valid else "diagnostic_rejected",
        notes=[] if valid else ["native finite-volume integrated inventory/wall-energy budget exceeded"],
        initial_states={name: asdict(state) for name, state in initial.items()},
        cell_fluid_volumes={
            cell.id: {
                "geometric_cylinder_m3": math.pi * cell.inner_diameter_m**2 * cell.length_m / 4,
                "effective_inventory_m3": pipes[cell.id].fluid_volume,
                "override_applied": cell.effective_fluid_volume_m3 is not None,
                "override_evidence_id": cell.fluid_volume_override_evidence_id,
                "ratio_to_geometric": pipes[cell.id].fluid_volume
                / (math.pi * cell.inner_diameter_m**2 * cell.length_m / 4),
            }
            for cell in q.cells
        },
        history=history,
        stage_service_records=stage_records,
        values={
            "actual_duration_s": elapsed,
            "requested_duration_s": math.fsum(w.duration_s for w in q.windows),
            "unintegrated_tail_s": math.fsum(w.duration_s for w in q.windows) - elapsed,
            "final_states": {name: asdict(state) for name, state in current.items()},
            "final_thermo": {name: asdict(pipes[name].thermo(state)) for name, state in current.items()},
            "cumulative_network_boundary_mass_kg": math.fsum(mass_prefix.values()),
            "cumulative_network_boundary_energy_J": math.fsum(energy_prefix.values()),
            "cumulative_network_boundary_transport_energy_J": math.fsum(transport_prefix.values()),
            "cumulative_network_ambient_heat_J": math.fsum(ambient_prefix.values()),
            "property_state_conversions": props.conversions,
            "network_solves": len(stage_records),
        },
    )


def evaluate_gas_thermal_transient(request, context):
    q = GasThermalTransientRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": "pipeline_integrity.gas_thermal_transient.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "notes": _policy(q, c),
        "native_called": False,
        "provider": None,
        "history": None,
        "stage_service_records": None,
        "values": None,
        "physical_validation_claim": False,
        "acoustic_momentum_PDE": False,
        "whole_nonisothermal_gas_transient_complete": False,
        "qualification": "native finite-inventory gas/wall cell network; quasi-steady low-Mach hydraulic faces; numerical verification pending",
    }
    if not body["notes"]:
        cursor = 0.0
        steps = 0
        for w in q.windows:
            ratio = w.duration_s / w.timestep_s
            if not math.isfinite(ratio):
                raise ValueError("native step count is not finite")
            steps += math.ceil(ratio)
            next_cursor = cursor + w.duration_s
            if next_cursor <= cursor or q.start_time_s + next_cursor <= q.start_time_s + cursor:
                raise ValueError("window boundary cannot be represented on actual clock")
            cursor = next_cursor
        if (
            cursor > q.maximum_duration_s
            or steps > q.maximum_steps
            or not math.isfinite(cursor + q.start_time_s)
        ):
            raise ValueError("requested native work/duration exceeds reviewed budget")
        before = native_backend_metadata(q)
        if before != q.owner_pins:
            raise ValueError("reviewed native source/runtime/reference pins differ")
        body["provider"] = before
        try:
            body["native_called"] = True
            _run(q, body)
            json_value(body)
        except (ValueError, RuntimeError, ArithmeticError, TypeError, KeyError) as exc:
            body.update(
                status="out_of_scope",
                notes=[f"{type(exc).__name__}: {exc}"],
                history=None,
                stage_service_records=None,
                values=None,
            )
        if native_backend_metadata(q) != before:
            raise ValueError("native source/runtime/EOS reference changed during simulation")
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def verify_gas_thermal_transient_record(record, *, request=None, context=None):
    try:
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("gas thermal receipt changed")
        q = GasThermalTransientRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and request != q) or (context is not None and context != c):
            raise ValueError("caller request/context differs")
        if evaluate_gas_thermal_transient(q, c) != record:
            raise ValueError("actual native pressure/thermal/inventory replay differs")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frames(record, context):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_gas_thermal_transient_record(record, context=context)["valid"]
    ):
        raise ValueError("verified native history and actual matching context required")
    frames = []
    for row in record["history"]:
        for name, state in row["native"]["thermo_states"].items():
            frames.append(
                SignalFrame(
                    stream_id=f"{context.asset_id}/{record['request']['event_id']}/{name}/gas_thermal",
                    clock_id=context.clock_id,
                    time_s=row["time_s"],
                    values={
                        "pressure_pa_abs": state["pressure_Pa"],
                        "temperature_K": state["temperature_K"],
                        "mass_kg": row["native"]["states"][name]["mass_kg"],
                    },
                    units={"pressure_pa_abs": "Pa", "temperature_K": "K", "mass_kg": "kg"},
                    valid=dict.fromkeys(("pressure_pa_abs", "temperature_K", "mass_kg"), True),
                    origin="synthetic" if context.origin == "synthetic" else "simulation",
                    operating_mode="native_finite_inventory_quasisteady_gas_network",
                )
            )
    return tuple(frames)
