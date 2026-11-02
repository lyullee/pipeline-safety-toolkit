"""Conservation-qualified gas-network exchange using the owner's junction and pipe solvers."""

import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .thermo import CoolPropProvider, StateRequest, ThermodynamicState


class GasBoundary(Contract):
    id: Name
    pressure_pa_abs: Positive
    temperature_k: Positive
    elevation_m: float = 0.0


class GasJunction(Contract):
    id: Name
    initial_pressure_pa_abs: Positive
    initial_temperature_k: Positive
    withdrawal_kg_s: Nonnegative = 0.0
    elevation_m: float = 0.0


class GasPipe(Contract):
    id: Name
    start_node: Name
    end_node: Name
    length_m: Positive
    diameter_m: Positive
    roughness_m: Nonnegative
    local_loss_coefficient: Nonnegative = 0.0
    heat_into_fluid_w: float = 0.0
    segments: int = Field(default=4, ge=1, le=128)


class GasNetwork(Contract):
    fluid: Name
    backend: Literal["HEOS", "PR", "SRK"] = "HEOS"
    property_basis: Name
    parameter_source_id: Name
    boundaries: tuple[GasBoundary, ...] = Field(min_length=1)
    junctions: tuple[GasJunction, ...] = ()
    pipes: tuple[GasPipe, ...] = Field(min_length=1)
    maximum_nodes: int = Field(default=128, ge=2, le=1000)
    maximum_segments: int = Field(default=256, ge=1, le=20000)
    maximum_state_conversions: int = Field(default=20000, ge=1, le=1000000)
    maximum_enthalpy_iterations: int = Field(default=50, ge=1, le=1000)
    maximum_pressure_evaluations: int = Field(default=200, ge=1, le=10000)
    relative_solver_tolerance: Positive = Field(default=1e-9, le=1e-3)
    mass_tolerance_kg_s: Positive = 1e-8
    static_energy_tolerance_w: Positive = 0.01
    relative_closure_tolerance: Nonnegative = Field(default=1e-7, le=1e-3)
    momentum_tolerance_pa: Positive = 0.01
    maximum_mach: Positive = Field(default=0.1, le=0.1)
    maximum_segment_density_change: Positive = Field(default=0.05, le=0.1)
    maximum_segment_pressure_change: Positive = Field(default=0.05, le=0.1)

    @model_validator(mode="after")
    def graph(self):
        ids = [n.id for n in self.boundaries + self.junctions]
        if len(set(ids)) != len(ids) or any(n.startswith("__gas_internal:") for n in ids):
            raise ValueError("gas nodes need unique identifiers outside the internal namespace")
        if len({p.id for p in self.pipes}) != len(self.pipes):
            raise ValueError("pipe identifiers must be unique")
        neighbors = {n: set() for n in ids}
        for p in self.pipes:
            if p.start_node == p.end_node or {p.start_node, p.end_node} - neighbors.keys():
                raise ValueError("gas pipes require distinct existing endpoints")
            neighbors[p.start_node].add(p.end_node)
            neighbors[p.end_node].add(p.start_node)
        reached = set()
        pending = [b.id for b in self.boundaries]
        while pending:
            n = pending.pop()
            if n not in reached:
                reached.add(n)
                pending.extend(neighbors[n] - reached)
        if set(ids) != reached or any(not neighbors[n] for n in ids):
            raise ValueError("each gas node needs a pipe path to a pressure boundary")
        count = (
            len(ids)
            + sum(p.segments - 1 for p in self.pipes)
            + sum(j.withdrawal_kg_s > 0 for j in self.junctions)
        )
        if count > self.maximum_nodes:
            raise ValueError("segmented gas network exceeds its node budget")
        if sum(p.segments for p in self.pipes) > self.maximum_segments:
            raise ValueError("gas pipe links exceed the declared segment budget")
        return self


class GasNetworkResult(Contract):
    schema_version: Literal["pipeline_integrity.gas_network.v1"] = "pipeline_integrity.gas_network.v1"
    status: Literal["computed", "withheld", "out_of_scope"]
    context: RunContext
    specification_digest: Name
    adapter_sha256: Name
    fluid: Name
    backend: Name
    property_basis: Name
    native: dict | None
    checks: dict
    notes: tuple[str, ...]
    result_origin: Literal["simulation"] = "simulation"
    energy_meaning: Literal["advected_static_enthalpy_and_prescribed_heat"] = (
        "advected_static_enthalpy_and_prescribed_heat"
    )
    total_energy_or_field_validation: Literal[False] = False
    result_digest: Name

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the gas-network result has been changed")


@dataclass(frozen=True)
class NativeGasRun:
    specification_digest: str
    solution: dict
    states: dict
    transmitted_states: dict
    pipe_links: dict
    withdrawal_links: dict
    provenance: dict


class GasDomainError(ValueError):
    pass


class _GasProperties:
    def __init__(self, native, maximum):
        self.native = native
        self.maximum = maximum
        self.conversions = 0
        self._pt = lru_cache(maxsize=32768)(lambda p, t: self._convert(self.native.from_pT, p, t))
        self._ph = lru_cache(maxsize=32768)(lambda p, h: self._convert(self.native.from_ph, p, h))

    def _convert(self, method, a, b):
        if self.conversions >= self.maximum:
            raise RuntimeError("gas state-conversion budget exceeded")
        self.conversions += 1
        state = method(a, b)
        if state.phase not in {"gas", "supercritical_gas", "supercritical"} or state.quality is not None:
            raise GasDomainError("the owner property state is outside the declared single-gas-phase domain")
        return state

    def from_pT(self, p, t):
        return self._pt(p, t)

    def from_ph(self, p, h):
        return self._ph(p, h)

    def viscosity_Pa_s(self, state):
        return self.native.viscosity_Pa_s(state)


class _Withdrawal:
    """Declared mass flux at a control surface, with no implied device pressure/flow law."""

    def __init__(self, mass):
        self.mass = mass

    def evaluate(self, left, _right):
        return SimpleNamespace(
            mass_flow_kg_s=self.mass, outlet_specific_enthalpy_J_kg=left.specific_enthalpy_J_kg
        )

    def evaluate_mass_flow(self, _left, _right):
        return self.mass


class LH2GasNetworkBackend:
    """Reuse general P/h junctions and native pipes; retain the owner's original solution."""

    def _pipe_component(self, pipe_module, properties, specification, pipe_spec, segment_index):
        """Keep native assembly reusable by explicit physical component adapters."""
        return pipe_module.Pipe(
            pipe_spec.length_m / pipe_spec.segments,
            pipe_spec.diameter_m,
            pipe_spec.roughness_m,
            local_loss_coefficient=pipe_spec.local_loss_coefficient / pipe_spec.segments,
            heat_into_fluid_W=pipe_spec.heat_into_fluid_w / pipe_spec.segments,
            properties=properties,
        )

    def solve(self, spec: GasNetwork) -> NativeGasRun:
        from lh2dt import network, pipe, properties

        props = _GasProperties(
            properties.HydrogenProperties(f"{spec.backend}::{spec.fluid}"), spec.maximum_state_conversions
        )
        engine = network.SteadyNetwork(props)
        initial = {}
        for b in spec.boundaries:
            initial[b.id] = props.from_pT(b.pressure_pa_abs, b.temperature_k)
            engine.add_boundary(b.id, initial[b.id])
        for j in spec.junctions:
            initial[j.id] = props.from_pT(j.initial_pressure_pa_abs, j.initial_temperature_k)
            engine.add_junction(j.id, j.initial_pressure_pa_abs, initial[j.id].specific_enthalpy_J_kg)
        withdrawal_links = {}
        for j in spec.junctions:
            if j.withdrawal_kg_s > 0:
                endpoint = f"__gas_internal:load:{j.id}"
                link = f"__gas_internal:withdrawal:{j.id}"
                engine.add_boundary(endpoint, initial[j.id])
                engine.add_link(link, j.id, endpoint, _Withdrawal(j.withdrawal_kg_s))
                withdrawal_links[j.id] = link
        pipe_links = {}
        for p in spec.pipes:
            chain = [p.start_node]
            a, b = initial[p.start_node], initial[p.end_node]
            for k in range(1, p.segments):
                node = f"__gas_internal:pipe:{p.id}:{k}"
                fraction = k / p.segments
                pressure = a.pressure_Pa + fraction * (b.pressure_Pa - a.pressure_Pa)
                enthalpy = a.specific_enthalpy_J_kg + fraction * (
                    b.specific_enthalpy_J_kg - a.specific_enthalpy_J_kg
                )
                engine.add_junction(node, pressure, enthalpy)
                chain.append(node)
            chain.append(p.end_node)
            names = []
            for k, (left, right) in enumerate(zip(chain, chain[1:], strict=False)):
                link = f"__gas_internal:link:{p.id}:{k}"
                component = self._pipe_component(pipe, props, spec, p, k)
                engine.add_link(
                    link, left, right, component, source_port_id=link + ":start", target_port_id=link + ":end"
                )
                names.append(link)
            pipe_links[p.id] = names
        solution = engine.solve(
            relative_tolerance=spec.relative_solver_tolerance,
            max_enthalpy_iterations=spec.maximum_enthalpy_iterations,
            max_pressure_nfev=spec.maximum_pressure_evaluations,
            mass_closure_absolute_tolerance_kg_s=spec.mass_tolerance_kg_s,
            energy_closure_absolute_tolerance_W=spec.static_energy_tolerance_w,
            closure_relative_tolerance=spec.relative_closure_tolerance,
        )
        inspected = {}
        provider = CoolPropProvider()
        load_nodes = {f"__gas_internal:load:{j}" for j in withdrawal_links}
        for name, state in solution.node_states.items():
            if name not in load_nodes:
                inspected[name] = provider.resolve(
                    StateRequest(
                        fluid=spec.fluid,
                        backend=spec.backend,
                        inputs={"P": state.pressure_Pa, "Hmass": state.specific_enthalpy_J_kg},
                    )
                ).model_dump(mode="json")
        transmitted = {}
        for names in pipe_links.values():
            for name in names:
                flow = solution.link_flows[name]
                receiver = flow.right if flow.mass_flow_kg_s >= 0 else flow.left
                transmitted[name] = provider.resolve(
                    StateRequest(
                        fluid=spec.fluid,
                        backend=spec.backend,
                        inputs={
                            "P": solution.node_states[receiver].pressure_Pa,
                            "Hmass": flow.transmitted_specific_enthalpy_J_kg,
                        },
                    )
                ).model_dump(mode="json")
        return NativeGasRun(
            specification_digest=digest(spec),
            solution=json_value(asdict(solution)),
            states=inspected,
            transmitted_states=transmitted,
            pipe_links=pipe_links,
            withdrawal_links=withdrawal_links,
            provenance={
                "owner": "lh2dt_models",
                "modules_sha256": {
                    name: file_digest(Path(module.__file__))
                    for name, module in (("network", network), ("pipe", pipe), ("properties", properties))
                },
                "state_conversions": props.conversions,
            },
        )


def _receipt(spec, context, status, checks, notes, native=None):
    data = dict(
        status=status,
        context=context.model_dump(mode="json"),
        specification_digest=digest(spec),
        adapter_sha256=file_digest(Path(__file__)),
        fluid=spec.fluid,
        backend=spec.backend,
        property_basis=spec.property_basis,
        native=native,
        checks=checks,
        notes=notes,
        result_digest="pending",
    )
    initial = GasNetworkResult.model_validate_json(json.dumps(data))
    data = initial.model_dump(mode="json", exclude={"result_digest"})
    return GasNetworkResult.model_validate_json(json.dumps(data | {"result_digest": digest(data)}))


def solve_gas_network(specification: GasNetwork, context: RunContext, *, backend=None):
    spec = GasNetwork.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if len({r.id for r in context.evidence}) != len(context.evidence) or spec.parameter_source_id not in {
        r.id for r in context.evidence
    }:
        raise ValueError("gas geometry, states and load assumptions require context evidence")
    elevations = {n.id: n.elevation_m for n in spec.boundaries + spec.junctions}
    if any(elevations[p.start_node] != elevations[p.end_node] for p in spec.pipes):
        return _receipt(
            spec,
            context,
            "out_of_scope",
            {},
            (
                "Unequal endpoint elevations need a qualified potential-energy and momentum coupling in the owner engine.",
            ),
        )
    try:
        run = (LH2GasNetworkBackend() if backend is None else backend).solve(spec)
    except GasDomainError as error:
        return _receipt(spec, context, "out_of_scope", {}, (str(error),))
    except (ValueError, RuntimeError, ArithmeticError) as error:
        return _receipt(
            spec, context, "withheld", {}, (f"Native gas solve failed: {type(error).__name__}: {error}",)
        )
    if not isinstance(run, NativeGasRun) or run.specification_digest != digest(spec):
        raise ValueError("the gas backend changed the requested specification")
    return _qualify(spec, context, run)


def _qualify(spec, context, run):
    solution = json_value(run.solution)
    flows = solution["link_flows"]
    states = {
        n: ThermodynamicState.model_validate_json(json.dumps(s)).model_dump(mode="json")
        for n, s in run.states.items()
    }
    transmitted = {
        n: ThermodynamicState.model_validate_json(json.dumps(s)).model_dump(mode="json")
        for n, s in run.transmitted_states.items()
    }
    expected_pipes = {p.id for p in spec.pipes}
    if set(run.pipe_links) != expected_pipes:
        raise ValueError("the native solution omitted or added a physical pipe")
    pipe_links = {link for names in run.pipe_links.values() for link in names}
    load_links = set(run.withdrawal_links.values())
    if len(pipe_links) != sum(p.segments for p in spec.pipes) or set(flows) != pipe_links | load_links:
        raise ValueError("native segment/load links do not match the declared topology")
    if set(run.withdrawal_links) != {j.id for j in spec.junctions if j.withdrawal_kg_s > 0}:
        raise ValueError("native withdrawals differ from the declared load ports")
    expected_states = {end for name in pipe_links for end in (flows[name]["left"], flows[name]["right"])}
    if set(states) != expected_states or set(transmitted) != pipe_links:
        raise ValueError("native physical states or transmitted endpoint states are incomplete")
    snapshot = dict(
        solution=solution,
        inspected_states=states,
        transmitted_states=transmitted,
        pipe_links=run.pipe_links,
        withdrawal_links=run.withdrawal_links,
        provenance=run.provenance,
    )
    if any(s["speed_of_sound_m_s"] is None for s in tuple(states.values()) + tuple(transmitted.values())):
        return _receipt(
            spec, context, "withheld", {}, ("Gas endpoint speed of sound is unavailable.",), native=snapshot
        )
    for state in transmitted.values():
        if state["fluid"] != spec.fluid or state["backend"] != spec.backend:
            raise ValueError("a transmitted endpoint changed the fluid or backend")
    if any(
        s["phase"] not in {"gas", "supercritical_gas", "supercritical"}
        for s in tuple(states.values()) + tuple(transmitted.values())
    ):
        return _receipt(
            spec,
            context,
            "out_of_scope",
            {},
            ("An endpoint is outside the declared gas phase.",),
            native=snapshot,
        )
    for b in spec.boundaries:
        if not math.isclose(
            states[b.id]["pressure_pa_abs"], b.pressure_pa_abs, rel_tol=1e-9
        ) or not math.isclose(states[b.id]["temperature_k"], b.temperature_k, rel_tol=1e-9):
            raise ValueError("the native gas pressure/temperature boundary changed")
    for name, state in states.items():
        if state["fluid"] != spec.fluid or state["backend"] != spec.backend:
            raise ValueError("the native property inspector changed the fluid or backend")
        original = solution["node_states"][name]
        if not math.isclose(
            state["pressure_pa_abs"], original["pressure_Pa"], rel_tol=1e-9
        ) or not math.isclose(
            state["enthalpy_j_kg"], original["specific_enthalpy_J_kg"], rel_tol=1e-9, abs_tol=1e-6
        ):
            raise ValueError("native node state and property inspector disagree")
    h_ref = states[spec.boundaries[0].id]["enthalpy_j_kg"]
    mass = {n: 0.0 for n in states}
    energy = {n: 0.0 for n in states}
    mass_scale = {n: 0.0 for n in states}
    energy_scale = {n: 0.0 for n in states}
    incoming_mass = {n: 0.0 for n in states}
    diagnostics = []
    domain_reasons = []
    for p in spec.pipes:
        links = run.pipe_links[p.id]
        if len(links) != p.segments:
            raise ValueError("native pipe subdivision changed")
        left = p.start_node
        for k, name in enumerate(links):
            flow = flows[name]
            if flow["left"] != left or (k == len(links) - 1 and flow["right"] != p.end_node):
                raise ValueError("native pipe segment order or endpoints changed")
            left = flow["right"]
            a = states[flow["left"] if flow["mass_flow_kg_s"] >= 0 else flow["right"]]
            b = transmitted[name]
            receiver = flow["right"] if flow["mass_flow_kg_s"] >= 0 else flow["left"]
            if not math.isclose(
                b["pressure_pa_abs"], states[receiver]["pressure_pa_abs"], rel_tol=1e-9
            ) or not math.isclose(
                b["enthalpy_j_kg"], flow["transmitted_specific_enthalpy_J_kg"], rel_tol=1e-9, abs_tol=1e-6
            ):
                raise ValueError("the transmitted property state changed its pressure or enthalpy")
            area = math.pi * p.diameter_m**2 / 4
            q = flow["mass_flow_kg_s"]
            mach = max(abs(q) / (s["density_kg_m3"] * area * s["speed_of_sound_m_s"]) for s in (a, b))
            density_change = abs(a["density_kg_m3"] - b["density_kg_m3"]) / min(
                a["density_kg_m3"], b["density_kg_m3"]
            )
            pressure_change = abs(a["pressure_pa_abs"] - b["pressure_pa_abs"]) / min(
                a["pressure_pa_abs"], b["pressure_pa_abs"]
            )
            momentum = flow.get("momentum_residual_Pa")
            diagnostics.append(
                dict(
                    pipe_id=p.id,
                    native_link=name,
                    mach=mach,
                    relative_density_change=density_change,
                    relative_pressure_change=pressure_change,
                    momentum_residual_pa=momentum,
                )
            )
            if (
                mach > spec.maximum_mach
                or density_change > spec.maximum_segment_density_change
                or pressure_change > spec.maximum_segment_pressure_change
            ):
                domain_reasons.append(f"Pipe {p.id} exceeds the declared local Mach/density/pressure domain.")
            if abs(q) <= 1e-12 and p.heat_into_fluid_w != 0:
                domain_reasons.append(
                    f"Pipe {p.id} has prescribed heat without resolved advective transport."
                )
    for j in spec.junctions:
        if j.withdrawal_kg_s > 0:
            flow = flows[run.withdrawal_links[j.id]]
            if (
                flow["left"] != j.id
                or flow["right"] in states
                or not math.isclose(
                    flow["mass_flow_kg_s"], j.withdrawal_kg_s, rel_tol=0, abs_tol=spec.mass_tolerance_kg_s
                )
                or not math.isclose(
                    flow["transmitted_specific_enthalpy_J_kg"],
                    states[j.id]["enthalpy_j_kg"],
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
            ):
                raise ValueError("the native load flux or computational terminal changed")
    for flow in flows.values():
        q = flow["mass_flow_kg_s"]
        upstream = flow["left"] if q >= 0 else flow["right"]
        if upstream not in states:
            raise ValueError("a computational withdrawal terminal cannot supply material")
        upstream_h = states[upstream]["enthalpy_j_kg"]
        for name, signed, h in (
            (flow["left"], -q, upstream_h if q >= 0 else flow["transmitted_specific_enthalpy_J_kg"]),
            (flow["right"], q, flow["transmitted_specific_enthalpy_J_kg"] if q >= 0 else upstream_h),
        ):
            if name in states:
                mass[name] += signed
                energy[name] += signed * (h - h_ref)
                mass_scale[name] += abs(q)
                energy_scale[name] += abs(signed * (h - h_ref))
                incoming_mass[name] += max(signed, 0.0)
    boundaries = {b.id for b in spec.boundaries}
    internal = set(states) - boundaries
    unresolved_enthalpy = sorted(n for n in internal if incoming_mass[n] <= spec.mass_tolerance_kg_s)
    mass_ok = all(
        abs(mass[n]) <= max(spec.mass_tolerance_kg_s, spec.relative_closure_tolerance * mass_scale[n])
        for n in internal
    )
    energy_ok = all(
        abs(energy[n])
        <= max(spec.static_energy_tolerance_w, spec.relative_closure_tolerance * energy_scale[n])
        for n in internal
    )
    source_mass = -sum(mass[n] for n in boundaries)
    load_mass = sum(j.withdrawal_kg_s for j in spec.junctions)
    source_energy = -sum(energy[n] for n in boundaries)
    load_energy = sum(j.withdrawal_kg_s * (states[j.id]["enthalpy_j_kg"] - h_ref) for j in spec.junctions)
    global_mass = source_mass - load_mass
    global_energy = source_energy + sum(p.heat_into_fluid_w for p in spec.pipes) - load_energy
    momentum_ok = all(
        d["momentum_residual_pa"] is not None and abs(d["momentum_residual_pa"]) <= spec.momentum_tolerance_pa
        for d in diagnostics
    )
    native_ok = all(
        solution.get(flag) is True
        for flag in (
            "pressure_solver_success",
            "pressure_optimizer_success",
            "enthalpy_solver_success",
            "junction_conservation_success",
        )
    )
    global_ok = abs(global_mass) <= max(
        spec.mass_tolerance_kg_s, spec.relative_closure_tolerance * sum(mass_scale[n] for n in boundaries)
    ) and abs(global_energy) <= max(
        spec.static_energy_tolerance_w,
        spec.relative_closure_tolerance * (sum(energy_scale[n] for n in boundaries) + abs(load_energy)),
    )
    pipe_flow_summary = {}
    pipe_segment_mass_continuity = True
    for p in spec.pipes:
        names = run.pipe_links[p.id]
        # Native link signs follow their own left/right endpoints. Normalize
        # every segment to the caller's declared start→end direction before
        # comparing continuity or publishing an exchange rate.
        rates = []
        for k, name in enumerate(names):
            flow = flows[name]
            expected_left = p.start_node if k == 0 else flows[names[k - 1]]["right"]
            expected_right = p.end_node if k == len(names) - 1 else flows[names[k + 1]]["left"]
            if flow["left"] == expected_left and flow["right"] == expected_right:
                direction = 1.0
            elif flow["left"] == expected_right and flow["right"] == expected_left:
                direction = -1.0
            else:
                raise ValueError("native pipe segment endpoints are not contiguous in declared topology")
            rates.append(direction * flows[name]["mass_flow_kg_s"])
        adjacent_steps = [abs(right - left) for left, right in zip(rates, rates[1:], strict=False)]
        max_step = max(adjacent_steps, default=0.0)
        segment_ok = all(
            step <= max(spec.mass_tolerance_kg_s, spec.relative_closure_tolerance * (abs(left) + abs(right)))
            for step, left, right in zip(adjacent_steps, rates, rates[1:], strict=False)
        )
        pipe_segment_mass_continuity = pipe_segment_mass_continuity and segment_ok
        signed_rate = rates[0] if segment_ok else None
        pipe_flow_summary[p.id] = dict(
            start_node=p.start_node,
            end_node=p.end_node,
            mass_flow_signed_kg_s=signed_rate,
            flow_direction=(
                "start_to_end" if signed_rate > 0 else "end_to_start" if signed_rate < 0 else "stagnant"
            ) if signed_rate is not None else "unresolved",
            segment_link_ids=list(names),
            max_adjacent_segment_flow_step_kg_s=max_step,
            segment_mass_continuity=segment_ok,
        )
    checks = dict(
        native_solver_and_closure=native_ok,
        pipe_segment_mass_continuity=pipe_segment_mass_continuity,
        pipe_flow_summary=pipe_flow_summary,
        junction_mass_closed=mass_ok,
        junction_static_energy_closed=energy_ok,
        momentum_closed=momentum_ok,
        global_closed=global_ok,
        junction_enthalpy_resolved=not unresolved_enthalpy,
        nodes_without_resolved_inflow=unresolved_enthalpy,
        global_mass_residual_kg_s=global_mass,
        global_static_energy_residual_w=global_energy,
        energy_reference_j_kg=h_ref,
        node_mass_residual_kg_s={n: mass[n] for n in sorted(internal)},
        node_static_energy_residual_w={n: energy[n] for n in sorted(internal)},
        segments=diagnostics,
    )
    status = (
        "out_of_scope"
        if domain_reasons
        else (
            "computed"
            if (
                native_ok
                and pipe_segment_mass_continuity
                and mass_ok
                and energy_ok
                and momentum_ok
                and global_ok
                and not unresolved_enthalpy
            )
            else "withheld"
        )
    )
    return _receipt(
        spec,
        context,
        status,
        checks,
        tuple(dict.fromkeys(domain_reasons))
        + (
            ("Mass flow is not continuous between segments of one or more declared pipes.",)
            if not pipe_segment_mass_continuity
            else ()
        )
        + (
            (
                "A zero-storage junction without resolved incoming mass has no determined advective enthalpy; its initial guess is retained only as a diagnostic.",
            )
            if unresolved_enthalpy
            else ()
        )
        + (
            "Native P/h mixing and segmented upstream-density pipe laws are retained; kinetic and potential energy are not qualified.",
            "Declared withdrawal ports are mass/enthalpy boundary conditions, not predictions of a consuming device.",
            "A computed result is a bounded steady gas screening, not a full accident or independent field validation.",
        ),
        native=snapshot,
    )
