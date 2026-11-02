"""Shared finite HEM inventories connected by native two-way restriction valves.

Native EOS, throat-flow, finite-volume derivatives and RK4 integration stay in
lh2dt. This layer declares shared storage and ports, samples original actuator
kinematics at actual RK stages, and reconciles integrated directional streams.
It does not resolve acoustic rupture waves, phase slip or atmospheric jet fate.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .inventory_transfers import (ConservedInventory, InventoryStream, InventoryTransferRequest,
                                  InventoryTransferWindow, evaluate_inventory_transfers)
from .phase_change_exchange import native_backend_metadata
from .thermo import StateRequest
from .valve_motion import ValveMotionRequest, simulate_valve_motion


class RuptureInventoryCell(Contract):
    id: Name
    length_m: Positive
    inner_diameter_m: Positive
    wall_heat_capacity_J_K: Positive
    fluid_wall_UA_W_K: Nonnegative
    ambient_UA_W_K: Nonnegative
    ambient_temperature_K: Positive
    initial_fluid_state: StateRequest
    initial_wall_temperature_K: Positive
    evidence_id: Name


class RuptureReservoir(Contract):
    id: Name
    state: StateRequest
    evidence_id: Name


class RuptureConnection(Contract):
    id: Name
    left_node_id: Name
    right_node_id: Name
    left_port_id: Name
    right_port_id: Name
    full_open_area_m2: Positive
    discharge_coefficient: Positive = Field(le=1)
    closed_leakage_CdA_m2: Nonnegative = 0.0
    fixed_opening: Fraction = 1.0
    motion: ValveMotionRequest | None = None
    allow_reverse: bool = True
    throat_pressure_samples: int = Field(default=60, ge=12, le=500)
    evidence_id: Name

    @model_validator(mode="after")
    def conductance(self):
        full = self.full_open_area_m2 * self.discharge_coefficient
        if not math.isfinite(full) or full <= 0 or self.closed_leakage_CdA_m2 > full:
            raise ValueError("rupture restriction leakage must lie inside the finite full-open CdA")
        if self.left_node_id == self.right_node_id or self.left_port_id == self.right_port_id:
            raise ValueError("rupture link endpoints and material ports must differ")
        if self.motion is not None:
            if self.motion.component_id != self.id or self.fixed_opening != 1.0:
                raise ValueError("motion identifies the actual connection and replaces fixed opening")
            curve = self.motion.conductance
            # The open endpoint is the product area*Cd. Compare at floating
            # precision, since an authored decimal CdA and that product can
            # differ by one representable ULP while denoting the same contract.
            endpoint_tolerance = 1e-30
            if not math.isclose(
                curve.effective_area_m2[0], self.closed_leakage_CdA_m2,
                rel_tol=1e-12, abs_tol=endpoint_tolerance,
            ) or not math.isclose(
                curve.effective_area_m2[-1], full,
                rel_tol=1e-12, abs_tol=endpoint_tolerance,
            ):
                raise ValueError("motion curve endpoints must equal native closed and full-open effective CdA")
        return self


class RuptureInventoryRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    duration_s: Positive
    maximum_step_s: Positive
    fluid: Literal["Hydrogen", "ParaHydrogen"]
    property_basis_id: Name
    inventories: tuple[RuptureInventoryCell, ...] = Field(min_length=1, max_length=64)
    reservoirs: tuple[RuptureReservoir, ...] = Field(default=(), max_length=128)
    connections: tuple[RuptureConnection, ...] = Field(min_length=1, max_length=256)
    pressure_support_Pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    minimum_retained_mass_kg: Positive
    absolute_mass_balance_tolerance_kg: Nonnegative = 1e-8
    absolute_energy_balance_tolerance_J: Nonnegative = 1e-4
    relative_balance_tolerance: Nonnegative = Field(default=1e-8, le=1e-3)
    maximum_steps: int = Field(default=10000, ge=1, le=10000)
    maximum_property_calls: int = Field(default=1000000, ge=1, le=10000000)
    maximum_throat_sample_evaluations: int = Field(default=10000000, ge=1, le=100000000)
    maximum_retained_values: int = Field(default=1000000, ge=100, le=10000000)
    maximum_result_bytes: int = Field(default=32000000, ge=4096, le=256000000)
    absolute_replay_clock_tolerance_s: Nonnegative = 1e-9
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    review_evidence_id: Name
    homogeneous_equilibrium_storage_and_restriction_network_reviewed: bool = False
    pure_fluid_energy_reference_and_phase_domain_reviewed: bool = False
    actuator_conductance_and_closed_seat_leakage_reviewed: bool = False
    negligible_inventory_kinetic_potential_energy_reviewed: bool = False
    owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def layout(self):
        nodes = [cell.id for cell in self.inventories] + [boundary.id for boundary in self.reservoirs]
        ports = [port for link in self.connections for port in (link.left_port_id, link.right_port_id)]
        if len(nodes) != len(set(nodes)) or len(ports) != len(set(ports)) or set(nodes) & set(ports):
            raise ValueError("inventory/reservoir nodes and actual material ports must be unique")
        if len({link.id for link in self.connections}) != len(self.connections):
            raise ValueError("rupture connection IDs must be unique")
        storage = {cell.id for cell in self.inventories}
        for link in self.connections:
            if not {link.left_node_id, link.right_node_id} <= set(nodes):
                raise ValueError("rupture connection references an unknown node")
            if not {link.left_node_id, link.right_node_id} & storage:
                raise ValueError("each restriction must connect at least one finite inventory")
            if link.motion is not None and link.motion.sample_time_s[-1] != self.duration_s:
                raise ValueError("actuator trajectory must span the exact rupture analysis horizon")
        for limits in (self.pressure_support_Pa_abs, self.temperature_support_K):
            if limits[0] >= limits[1]:
                raise ValueError("rupture EOS support bounds must be ordered")
        for state in [cell.initial_fluid_state for cell in self.inventories] + [r.state for r in self.reservoirs]:
            if (state.fluid != self.fluid or state.backend != "HEOS"
                    or set(state.inputs) not in ({"P", "T"}, {"P", "Hmass"}, {"P", "Smass"})):
                raise ValueError("rupture states use the common pure-fluid HEOS P/T, P/H or P/S basis")
        return self


def rupture_inventory_metadata():
    return dict(owner=native_backend_metadata(), source_sha256={
        name: file_digest(Path(__file__).with_name(name + ".py"))
        for name in ("rupture_inventory", "valve_motion", "inventory_transfers", "contracts", "datasets")})


def rupture_inventory_boundary(request):
    return dict(asset_id=request.asset_id, event_id=request.event_id, clock_id=request.clock_id,
                fluid=request.fluid, property_basis_id=request.property_basis_id,
                request_digest_without_pins=digest(request.model_dump(mode="json", exclude={"owner_pins"})))


def bind_rupture_inventory_context(request: RuptureInventoryRequest, context: RunContext):
    q = RuptureInventoryRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id):
        raise ValueError("rupture context asset/event/clock differs")
    payload = c.model_dump(mode="python")
    payload["metadata"] = {**c.metadata, "rupture_inventory_boundary": rupture_inventory_boundary(q)}
    return RunContext.model_validate(payload)


def _policy(q, c):
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id):
        raise ValueError("rupture inventory asset/event/clock differs")
    if c.metadata.get("rupture_inventory_boundary") != rupture_inventory_boundary(q):
        raise ValueError("rupture context does not bind the actual shared inventories and material ports")
    if not all((q.homogeneous_equilibrium_storage_and_restriction_network_reviewed,
                q.pure_fluid_energy_reference_and_phase_domain_reviewed,
                q.actuator_conductance_and_closed_seat_leakage_reviewed,
                q.negligible_inventory_kinetic_potential_energy_reviewed)):
        raise ValueError("rupture HEM, energy, actuator and omitted inventory-motion reviews are required")
    required = {q.review_evidence_id, *[cell.evidence_id for cell in q.inventories],
                *[boundary.evidence_id for boundary in q.reservoirs], *[link.evidence_id for link in q.connections]}
    for link in q.connections:
        if link.motion is not None:
            required.add(link.motion.conductance.parameter_source_id)
            required.update(command.evidence_id for command in link.motion.commands)
    evidence = {entry.id: entry for entry in c.evidence}
    if (len(evidence) != len(c.evidence) or len(set(q.evidence_ids)) != len(q.evidence_ids)
            or not required <= set(q.evidence_ids) <= set(evidence)):
        raise ValueError("rupture geometry, boundaries, actuator and reviews must select actual context evidence")
    if any(evidence[key].sha256 is None for key in required):
        raise ValueError("rupture review and parameter evidence requires retained source SHA-256 pins")
    if not q.owner_pins:
        raise ValueError("rupture owner/runtime/reference/source pins must be reviewed before calculation")


class RuptureDomainError(ArithmeticError):
    """A domain rejection must not become a zero native throat sample."""


def _run(q, c):
    from lh2dt.dynamic_network import DynamicNetworkSimulator, dynamic_hem_pipe_node
    from lh2dt.dynamic_pipe import DynamicHEMPipe
    from lh2dt.network import SteadyNetwork
    from lh2dt.properties import HydrogenProperties
    from lh2dt.valve import Valve

    class Properties:
        def __init__(self):
            self.native = HydrogenProperties(q.fluid)
            self.calls = 0
            self.failures = 0

        def __getattr__(self, name):
            method = getattr(self.native, name)
            if not callable(method) or not (name.startswith("from_") or name.startswith("saturated_")):
                return method

            def resolve(*args, **kwargs):
                self.calls += 1
                if self.calls > q.maximum_property_calls:
                    raise RuptureDomainError("rupture native EOS evaluation budget exceeded")
                try:
                    state = method(*args, **kwargs)
                except Exception:
                    self.failures += 1
                    raise
                if (not q.pressure_support_Pa_abs[0] <= state.pressure_Pa <= q.pressure_support_Pa_abs[1]
                        or not q.temperature_support_K[0] <= state.temperature_K <= q.temperature_support_K[1]):
                    raise RuptureDomainError("rupture native state/throat leaves reviewed pure-fluid EOS support")
                if state.quality is not None and not 0 <= state.quality <= 1:
                    raise RuptureDomainError("rupture native quality is outside its homogeneous phase domain")
                return state
            return resolve

    properties = Properties()

    def state_from_request(specification):
        if set(specification.inputs) == {"P", "T"}:
            result = properties.from_pT(specification.inputs["P"], specification.inputs["T"])
        elif set(specification.inputs) == {"P", "Hmass"}:
            result = properties.from_ph(specification.inputs["P"], specification.inputs["Hmass"])
        else:
            result = properties.from_ps(specification.inputs["P"], specification.inputs["Smass"])
        if specification.single_phase_required and result.quality is not None:
            raise ValueError("a rupture initial/reservoir state requested single-phase but resolved in equilibrium coexistence")
        return result

    pipes = {cell.id: DynamicHEMPipe(cell.length_m, cell.inner_diameter_m, 0.0,
                                   cell.wall_heat_capacity_J_K, fluid_wall_UA_W_K=cell.fluid_wall_UA_W_K,
                                   ambient_UA_W_K=cell.ambient_UA_W_K,
                                   ambient_temperature_K=cell.ambient_temperature_K, properties=properties)
             for cell in q.inventories}
    current = {cell.id: pipes[cell.id].initialize(state_from_request(cell.initial_fluid_state),
                                               wall_temperature_K=cell.initial_wall_temperature_K)
               for cell in q.inventories}
    initial = dict(current)
    capacities = {cell.id: cell.wall_heat_capacity_J_K for cell in q.inventories}
    reservoirs = {boundary.id: state_from_request(boundary.state) for boundary in q.reservoirs}
    cuts = {0.0, q.duration_s}
    for link in q.connections:
        if link.motion is not None:
            cuts.update(command.time_s for command in link.motion.commands)
    cuts = sorted(cuts)
    intervals = []
    for left, right in zip(cuts, cuts[1:], strict=False):
        count = math.ceil((right - left) / q.maximum_step_s)
        if len(intervals) + count > q.maximum_steps:
            raise ValueError("rupture accepted RK-step budget exceeded before integration")
        for index in range(count):
            begin = left + (right - left) * index / count
            end = right if index + 1 == count else left + (right - left) * (index + 1) / count
            if end - begin <= 1e-12 or q.start_time_s + begin >= q.start_time_s + end:
                raise ValueError("rupture step is below original integrator/absolute-clock resolution")
            intervals.append((begin, end))
    stage_times = sorted({time for begin, end in intervals for time in (begin, begin + (end - begin) / 2, end)})
    expected_samples = len(intervals) * 5 * sum(link.throat_pressure_samples for link in q.connections)
    if expected_samples > q.maximum_throat_sample_evaluations:
        raise ValueError("rupture original throat search upper work bound exceeds request budget")
    if ((len(intervals) + 1) * (len(q.inventories) * 40 + len(q.connections) * 50)
            + len(stage_times) * sum(link.motion is not None for link in q.connections) * 8
            > q.maximum_retained_values):
        raise ValueError("rupture trajectory exceeds its retained scalar budget")
    motions = {}
    trajectories = {}
    for link in q.connections:
        if link.motion is None:
            continue
        payload = link.motion.model_dump(mode="python")
        payload["sample_time_s"] = tuple(sorted(set(payload["sample_time_s"]) | set(stage_times)))
        expanded = ValveMotionRequest.model_validate(payload)
        valve_context = c.model_dump(mode="python")
        valve_context["asset_id"] = link.id
        valve_context["metadata"] = {**c.metadata, "shared_inventory_parent_asset_id": q.asset_id,
                                     "connection_mapping_evidence_id": link.evidence_id}
        motion = simulate_valve_motion(expanded, RunContext.model_validate(valve_context))
        motions[link.id] = motion.model_dump(mode="json")
        trajectories[link.id] = dict(zip(motion.time_s, motion.effective_area_m2, strict=True))

    class ControlledRestriction:
        def __init__(self, specification):
            self.specification = specification
            self.valve = Valve(specification.full_open_area_m2, specification.discharge_coefficient,
                               properties=properties, pressure_samples=specification.throat_pressure_samples,
                               allow_reverse=specification.allow_reverse,
                               closed_leakage_CdA_m2=specification.closed_leakage_CdA_m2)
            self.opening = specification.fixed_opening

        def set_time(self, time):
            link = self.specification
            if link.motion is None:
                return
            effective = trajectories[link.id][time]
            full = link.full_open_area_m2 * link.discharge_coefficient
            width = full - link.closed_leakage_CdA_m2
            # The motion owner interpolates effective CdA independently from
            # the native area*Cd product. Snap only representational endpoint
            # differences back to the exact closed/open states; otherwise the
            # normalized opening can become -epsilon or 1+epsilon and reject a
            # physically identical endpoint.
            endpoint_tol = 1e-30
            if math.isclose(
                effective, link.closed_leakage_CdA_m2,
                rel_tol=1e-12, abs_tol=endpoint_tol,
            ):
                self.opening = 0.0
            elif math.isclose(effective, full, rel_tol=1e-12, abs_tol=endpoint_tol):
                self.opening = 1.0
            else:
                self.opening = (effective - link.closed_leakage_CdA_m2) / width if width else 0.0
            if not math.isfinite(self.opening) or not 0 <= self.opening <= 1:
                raise ValueError("native actuator/curve CdA cannot map to the actual restriction")

        def evaluate(self, left, right):
            failures = properties.failures
            result = self.valve.evaluate(left, right, opening=self.opening)
            if properties.failures != failures:
                raise RuptureDomainError("native throat search encountered an unresolved EOS sample; no silent zero replacement accepted")
            return result

    restrictions = {link.id: ControlledRestriction(link) for link in q.connections}
    link_cumulative = {link.id: dict(forward_mass_kg=0.0, reverse_mass_kg=0.0,
                                     forward_enthalpy_J=0.0, reverse_enthalpy_J=0.0) for link in q.connections}
    cumulative_mass = dict.fromkeys(current, 0.0)
    cumulative_energy = dict.fromkeys(current, 0.0)
    cumulative_ambient = dict.fromkeys(current, 0.0)
    rows = []
    transfer_windows = []

    def total_energy(name, state):
        return state.internal_energy_J + capacities[name] * state.wall_temperature_K

    def snapshot(time, ledgers):
        values = {}
        for name, state in current.items():
            if state.mass_kg < q.minimum_retained_mass_kg:
                raise ValueError("rupture native inventory fell below reviewed strictly positive EOS mass floor")
            values[name] = dict(conserved_state=asdict(state), thermo_state=asdict(pipes[name].thermo(state)),
                                stored_fluid_and_wall_energy_J=total_energy(name, state))
        rows.append(dict(time_s=q.start_time_s + time, inventories=values, conservation=ledgers,
                         cumulative_connection_exchange=json_value(link_cumulative)))

    snapshot(0.0, {})
    for number, (begin, end) in enumerate(intervals):
        captured = []

        class CapturedNetwork(SteadyNetwork):
            def solve(self, **kwargs):
                solution = super().solve(**kwargs)
                if not all((solution.pressure_solver_success, solution.pressure_optimizer_success,
                            solution.enthalpy_solver_success, solution.junction_conservation_success)):
                    raise ValueError("rupture native hydraulic network did not qualify")
                captured.append({name: dict(mass_flow_kg_s=flow.mass_flow_kg_s,
                                           specific_enthalpy_J_kg=flow.transmitted_specific_enthalpy_J_kg)
                                 for name, flow in solution.link_flows.items()})
                return solution

        network = CapturedNetwork(properties)
        for name, state in reservoirs.items():
            network.add_boundary(name, state)
        for name, state in current.items():
            network.add_boundary(name, pipes[name].thermo(state))
        for link in q.connections:
            network.add_link(link.id, link.left_node_id, link.right_node_id, restrictions[link.id],
                             source_port_id=link.left_port_id, target_port_id=link.right_port_id)
        dt = end - begin
        mapped_times = {0.0: begin, dt / 2: begin + dt / 2, dt: end}

        def commands(local_time, _network):
            if local_time not in mapped_times:
                raise ValueError("original integrator called an unreviewed stage time")
            for restriction in restrictions.values():
                restriction.set_time(mapped_times[local_time])

        models = {name: dynamic_hem_pipe_node(name, pipes[name], state) for name, state in current.items()}
        results = DynamicNetworkSimulator(network, models, command_callback=commands).simulate(dt, dt)
        if len(results) != 1 or len(captured) != 5 or results[0].time_s != dt:
            raise ValueError("native one-step RK4 stage/clock contract changed")
        result = results[0]
        current = dict(result.states)
        stream_rows = []
        for link in q.connections:
            amounts = dict(forward_mass_kg=0.0, reverse_mass_kg=0.0,
                           forward_enthalpy_J=0.0, reverse_enthalpy_J=0.0)
            for weight, stage in zip((1, 2, 2, 1), captured[:4], strict=True):
                rate, h = stage[link.id]["mass_flow_kg_s"], stage[link.id]["specific_enthalpy_J_kg"]
                direction = "forward" if rate >= 0 else "reverse"
                mass = dt * weight * abs(rate) / 6
                amounts[direction + "_mass_kg"] += mass
                amounts[direction + "_enthalpy_J"] += mass * h
            for key, amount in amounts.items():
                link_cumulative[link.id][key] += amount
            for direction, source, destination in (("forward", link.left_node_id, link.right_node_id),
                                                    ("reverse", link.right_node_id, link.left_node_id)):
                amount = amounts[direction + "_mass_kg"]
                if amount > 0:
                    stream_rows.append(InventoryStream(id=link.id + "/" + direction,
                        source_id=source if source in pipes else None,
                        destination_id=destination if destination in pipes else None,
                        species_mass_flow_kg_s={q.fluid: amount / dt},
                        specific_total_flow_energy_J_kg=amounts[direction + "_enthalpy_J"] / amount,
                        evidence_id=link.evidence_id))
        ambient = result.cumulative_boundary_ambient_heat_J
        transfer_windows.append(InventoryTransferWindow(id=q.id + "/step/" + str(number), duration_s=dt,
            streams=tuple(stream_rows), heat_and_work_into_inventory_W={name: ambient[name] / dt for name in pipes},
            evidence_id=q.review_evidence_id))
        residuals = {}
        for name in pipes:
            cumulative_mass[name] += result.cumulative_boundary_mass_kg[name]
            cumulative_energy[name] += result.cumulative_boundary_energy_J[name]
            cumulative_ambient[name] += ambient[name]
            mass_residual = current[name].mass_kg - initial[name].mass_kg - cumulative_mass[name]
            energy_residual = total_energy(name, current[name]) - total_energy(name, initial[name]) - cumulative_energy[name]
            mass_limit = q.absolute_mass_balance_tolerance_kg + q.relative_balance_tolerance * max(initial[name].mass_kg, abs(cumulative_mass[name]))
            energy_limit = q.absolute_energy_balance_tolerance_J + q.relative_balance_tolerance * max(abs(total_energy(name, initial[name])), abs(cumulative_energy[name]))
            if abs(mass_residual) > mass_limit or abs(energy_residual) > energy_limit:
                raise ValueError("rupture native integrated mass/fluid+wall energy ledger exceeds caller tolerance")
            residuals[name] = dict(mass_residual_kg=mass_residual, energy_residual_J=energy_residual,
                                   cumulative_net_mass_into_inventory_kg=cumulative_mass[name],
                                   cumulative_net_energy_into_inventory_J=cumulative_energy[name],
                                   cumulative_ambient_heat_into_inventory_J=cumulative_ambient[name])
        snapshot(end, residuals)

    maximum_initial_mass = max(state.mass_kg for state in initial.values())
    maximum_initial_energy = max(abs(total_energy(name, state)) for name, state in initial.items())
    transfer = InventoryTransferRequest(id=q.id + "/integrated_native_streams", asset_id=q.asset_id,
        clock_id=q.clock_id, start_time_s=q.start_time_s, species_ids=(q.fluid,),
        total_energy_reference_id=q.property_basis_id,
        inventories=tuple(ConservedInventory(id=cell.id, species_mass_kg={q.fluid: initial[cell.id].mass_kg},
                                           stored_total_energy_J=total_energy(cell.id, initial[cell.id]), evidence_id=cell.evidence_id)
                          for cell in q.inventories), windows=tuple(transfer_windows),
        prescribed_streams_common_energy_reference_reviewed=True,
        absolute_species_balance_tolerance_kg=max(1e-15, q.absolute_mass_balance_tolerance_kg
                                                + q.relative_balance_tolerance * maximum_initial_mass * len(pipes)),
        absolute_energy_balance_tolerance_J=max(1e-12, q.absolute_energy_balance_tolerance_J
                                               + q.relative_balance_tolerance * maximum_initial_energy * len(pipes)),
        maximum_retained_values=q.maximum_retained_values)
    reconciliation = evaluate_inventory_transfers(transfer, c)
    maximum_clock_residual = 0.0
    for native_row, transfer_row in zip(rows, reconciliation["states"], strict=True):
        clock_residual = abs(native_row["time_s"] - transfer_row["time_s"])
        maximum_clock_residual = max(maximum_clock_residual, clock_residual)
        if clock_residual > q.absolute_replay_clock_tolerance_s:
            raise ValueError("integrated stream replay clock differs beyond caller tolerance")
        reconstructed = {item["id"]: item for item in transfer_row["inventories"]}
        for name, value in native_row["inventories"].items():
            native_mass = value["conserved_state"]["mass_kg"]
            native_energy = value["stored_fluid_and_wall_energy_J"]
            if (abs(reconstructed[name]["species_mass_kg"][q.fluid] - native_mass)
                    > q.absolute_mass_balance_tolerance_kg + q.relative_balance_tolerance * max(native_mass, initial[name].mass_kg)
                    or abs(reconstructed[name]["stored_total_energy_J"] - native_energy)
                    > q.absolute_energy_balance_tolerance_J + q.relative_balance_tolerance * max(abs(native_energy), maximum_initial_energy)):
                raise ValueError("directional shared-inventory stream replay disagrees with original RK inventories")
    return dict(states=rows, actuator_motion=motions, integrated_native_stream_inventory=reconciliation,
                accepted_steps=len(intervals), property_calls=properties.calls,
                property_call_budget_scope="native_state_conversions; internal_CoolProp_scalar_queries_not_individually_counted",
                native_network_solves=5*len(intervals),
                native_throat_search_sample_upper_bound=expected_samples,
                native_EOS_sample_failures=properties.failures,
                maximum_inventory_replay_clock_residual_s=maximum_clock_residual,
                output_origin="simulation",
                shared_inventory_consumed_once=True,
                initial_thermodynamic_state_by_inventory={name: value["thermo_state"]
                                                         for name, value in rows[0]["inventories"].items()},
                geometry_role="cylindrical_storage_volume_only; hydraulic_losses_only_in_explicit_restrictions",
                property_reference_definition="native_CoolProp_HEOS_pure_fluid_H_S_U_without_reference_shifts",
                directional_exchange_method="positive_and_negative_parts_of_actual_RK4_stage_fluxes; reversal_events_not_localized",
                energy_scope="fluid_internal_energy_plus_constant_capacity_wall_energy; transported_native_enthalpy",
                phase_slip_column_separation_or_acoustic_PDE=False,
                atmospheric_phase_partition_or_droplet_fate=False)


def evaluate_rupture_inventory(request: RuptureInventoryRequest, context: RunContext):
    q = RuptureInventoryRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    _policy(q, c)
    before = rupture_inventory_metadata()
    if before != q.owner_pins:
        raise ValueError("rupture source/runtime/reference identity differs from reviewed owner pins")
    body = dict(schema="pipeline_integrity.rupture_inventory.v1", request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"), provider=before, status="withheld",
                field_validation_claim=False, limitations=[
                    "Finite homogeneous inventories with native quasi-steady HEM restrictions; no distributed momentum or acoustic rupture waves.",
                    "All connected reservoirs contain the same pure fluid; atmospheric air ingestion is not represented.",
                    "Inventory kinetic/potential energy, slip, separated columns and downstream jet/droplet fate are outside scope."])
    try:
        result = _run(q, c)
        body.update(status="computed", **result)
    except (ValueError, RuntimeError, ArithmeticError, KeyError) as error:
        body.update(issues=[str(error)], error_type=type(error).__name__)
    if rupture_inventory_metadata() != before or digest(request) != digest(q) or digest(context) != digest(c):
        raise ValueError("rupture source/runtime/reference/request/context changed during evaluation")
    result = json_value(body)
    result["record_digest"] = digest(result)
    if len(json.dumps(result, allow_nan=False).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("rupture inventory receipt exceeds retained-result byte budget")
    return result


def verify_rupture_inventory_record(record, *, request=None, context=None):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
            {key: value for key, value in record.items() if key != "record_digest"}):
        raise ValueError("rupture inventory receipt changed")
    q = RuptureInventoryRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request) != digest(q):
        raise ValueError("rupture caller request differs")
    if context is not None and digest(context) != digest(c):
        raise ValueError("rupture caller context differs")
    if evaluate_rupture_inventory(q, c) != record:
        raise ValueError("rupture native source/actuator/shared-inventory replay differs")
    return True


def rupture_inventory_signal_frames(record, *, inventory_id, stream_id=None):
    """Replay native shared-storage history and retain unknown single-phase quality."""
    verify_rupture_inventory_record(record)
    if record["status"] != "computed":
        raise ValueError("only a completed native shared-inventory calculation can produce process frames")
    q = RuptureInventoryRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if inventory_id not in {cell.id for cell in q.inventories}:
        raise ValueError("process frame selector must identify an actual finite inventory")
    identity = stream_id or q.asset_id + "/" + q.event_id + "/" + inventory_id + "/rupture_inventory"
    units = dict(pressure_pa_abs="Pa", temperature_K="K", mass_kg="kg", internal_energy_J="J",
                 wall_temperature_K="K", vapor_mass_fraction="dimensionless",
                 stored_fluid_and_wall_energy_J="J")
    frames = []
    for row in record["states"]:
        selected = row["inventories"][inventory_id]
        state, conserved = selected["thermo_state"], selected["conserved_state"]
        values = dict(pressure_pa_abs=state["pressure_Pa"], temperature_K=state["temperature_K"],
                      mass_kg=conserved["mass_kg"], internal_energy_J=conserved["internal_energy_J"],
                      wall_temperature_K=conserved["wall_temperature_K"], vapor_mass_fraction=state["quality"],
                      stored_fluid_and_wall_energy_J=selected["stored_fluid_and_wall_energy_J"])
        frames.append(SignalFrame(stream_id=identity, clock_id=q.clock_id, time_s=row["time_s"],
            values=values, units=units, valid={key: value is not None for key, value in values.items()},
            origin="synthetic" if c.origin == "synthetic" else "simulation",
            operating_mode="native_shared_HEM_inventory_" + state["phase"]))
    body = dict(schema="pipeline_integrity.rupture_inventory_frames.v1",
                source_record_digest=record["record_digest"], source_context_digest=digest(c),
                inventory_id=inventory_id, stream_id=identity, clock_id=q.clock_id,
                provider_digest=digest(record["provider"]), frame_count=len(frames), frames_digest=digest(frames),
                quantity_basis="absolute_pressure_and_native_pure_fluid_energy_reference",
                single_phase_quality_not_replaced_by_zero_or_one=True,
                source_evidence_ids=list(q.evidence_ids))
    body["record_digest"] = digest(body)
    return tuple(frames), body
