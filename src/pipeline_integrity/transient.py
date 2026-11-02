"""Liquid acoustic characteristics on pipe graphs with explicit head/withdrawal boundaries."""

from __future__ import annotations

import hashlib
import math
import platform
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from . import __version__
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .pump_moc_coupling import LiquidPump
from .moc_characteristics import characteristic_invariants


class History(Contract):
    time_s: tuple[Nonnegative, ...] = Field(min_length=1)
    values: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def clock(self):
        if self.time_s[0] != 0 or len(self.time_s) != len(self.values):
            raise ValueError("a boundary history starts at zero with matching values")
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("boundary knots must strictly increase")
        return self

    def at(self, time_s):
        return float(np.interp(time_s, self.time_s, self.values))


class LiquidNode(Contract):
    id: Name
    elevation_m: float
    initial_head_m: float
    boundary: Literal["head", "withdrawal"]
    history: History
    atmospheric_emitter: History = Field(default_factory=lambda: History(time_s=(0.0,), values=(0.0,)))

    @model_validator(mode="after")
    def emitter(self):
        if any(value < 0 for value in self.atmospheric_emitter.values):
            raise ValueError("emitter coefficients must be nonnegative m3/s/sqrt(m)")
        if self.boundary == "head" and self.history.values[0] != self.initial_head_m:
            raise ValueError("a fixed-head history must start at the declared initial head")
        return self


class LiquidElevationPoint(Contract):
    """Surveyed centerline elevation at a pipe chainage measured from its start node."""

    chainage_m: Nonnegative
    elevation_m: float


class LiquidPipe(Contract):
    id: Name
    start_node: Name
    end_node: Name
    length_m: Positive
    diameter_m: Positive
    wave_speed_m_s: Positive
    darcy_friction_factor: Nonnegative
    initial_volume_flow_m3_s: float
    elevation_profile: tuple[LiquidElevationPoint, ...] = Field(default=(), max_length=10000)

    @model_validator(mode="after")
    def topology(self):
        if self.start_node == self.end_node:
            raise ValueError("a pipe needs distinct end nodes")
        return self


class LiquidValve(Contract):
    """A zero-volume, bidirectional liquid restriction with declared effective CdA history."""

    id: Name
    start_node: Name
    end_node: Name
    effective_area_m2: History
    initial_volume_flow_m3_s: float
    parameter_source_id: Name
    evidence: dict = Field(default_factory=dict)
    binding_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def physical_scope(self):
        if self.start_node == self.end_node or any(a < 0 for a in self.effective_area_m2.values):
            raise ValueError("a valve needs distinct nodes and nonnegative effective CdA in m2")
        json_value(self.evidence)
        expected = digest(self.model_dump(mode="json", exclude={"binding_digest"}))
        if self.binding_digest is None:
            object.__setattr__(self, "binding_digest", expected)
        elif self.binding_digest != expected:
            raise ValueError("valve conductance or its binding evidence changed")
        return self


class LiquidTransient(Contract):
    nodes: tuple[LiquidNode, ...] = Field(min_length=2)
    pipes: tuple[LiquidPipe, ...] = Field(min_length=1)
    valves: tuple[LiquidValve, ...] = ()
    pumps: tuple[LiquidPump, ...] = ()
    duration_s: Positive
    requested_timestep_s: Positive
    density_kg_m3: Positive
    ambient_pressure_pa_abs: Positive
    vapour_pressure_pa_abs: Positive
    gravity_m_s2: Positive = 9.80665
    maximum_wave_speed_adjustment: Nonnegative = Field(default=0.01, le=0.1)
    initial_head_tolerance_m: Positive = 1e-6
    initial_flow_tolerance_m3_s: Positive = 1e-9
    mass_balance_tolerance_m3: Positive = 1e-5
    maximum_acoustic_mach: Positive = Field(default=0.05, le=0.1)
    maximum_linearized_storage_strain: Positive = Field(default=0.01, le=0.05)
    maximum_steps: int = Field(default=20000, ge=2, le=1000000)
    maximum_total_segments: int = Field(default=100000, ge=2, le=1000000)
    maximum_history_values: int = Field(default=2000000, ge=10, le=200000000)
    maximum_elevation_profile_points: int = Field(default=100000, ge=0, le=1000000)
    valve_head_tolerance_m: Positive = 1e-7
    valve_flow_tolerance_m3_s: Positive = 1e-10
    maximum_valve_unknowns: int = Field(default=256, ge=1, le=1000)
    maximum_valve_solver_evaluations: int = Field(default=100, ge=1, le=1000)
    maximum_pump_residual_evaluations: int = Field(default=5000, ge=10, le=100000)

    @model_validator(mode="after")
    def graph(self):
        nodes = {node.id for node in self.nodes}
        node_lookup = {node.id: node for node in self.nodes}
        if sum(len(pipe.elevation_profile) for pipe in self.pipes) > self.maximum_elevation_profile_points:
            raise ValueError("surveyed elevation profile exceeds its declared point budget")
        if len(nodes) != len(self.nodes) or len({p.id for p in self.pipes}) != len(self.pipes):
            raise ValueError("node and pipe IDs must be unique within their types")
        if len({v.id for v in self.valves}) != len(self.valves) or {v.id for v in self.valves}.intersection(
            p.id for p in self.pipes
        ):
            raise ValueError("pipe and valve component identities must be distinct")
        component_ids=[p.id for p in (*self.pipes,*self.valves,*self.pumps)]
        if len(component_ids)!=len(set(component_ids)):
            raise ValueError("pipe, valve and pump IDs must be distinct")
        if len({p.shaft_id for p in self.pumps})!=len(self.pumps):
            raise ValueError("each pump requires an independent shaft; shared-shaft gearing is unresolved")
        used = {key for p in (*self.pipes, *self.valves,*self.pumps) for key in (p.start_node, p.end_node)}
        if used != nodes:
            raise ValueError("every declared node must connect and all pipe ends must exist")
        piped = {key for p in self.pipes for key in (p.start_node, p.end_node)}
        if any(n.id not in piped and n.boundary != "head" for n in self.nodes):
            raise ValueError(
                "a withdrawal node requires an incident pipe; valve-only nodes must be fixed-head boundaries"
            )
        if self.vapour_pressure_pa_abs >= self.ambient_pressure_pa_abs:
            raise ValueError("this liquid acoustic adapter requires sub-boiling ambient conditions")
        for pipe in self.pipes:
            if pipe.elevation_profile:
                points = pipe.elevation_profile
                if points[0].chainage_m != 0 or points[-1].chainage_m != pipe.length_m:
                    raise ValueError("a surveyed elevation profile must span both pipe endpoints")
                if any(b.chainage_m <= a.chainage_m for a, b in zip(points, points[1:])):
                    raise ValueError("surveyed elevation chainage must strictly increase")
                start_elevation = node_lookup[pipe.start_node].elevation_m
                end_elevation = node_lookup[pipe.end_node].elevation_m
                tolerance = 1e-9 * max(1.0, abs(start_elevation), abs(end_elevation))
                if (
                    abs(points[0].elevation_m - start_elevation) > tolerance
                    or abs(points[-1].elevation_m - end_elevation) > tolerance
                ):
                    raise ValueError("surveyed profile endpoint elevations must match connected nodes")
        return self


def simulate_liquid_transient(specification: LiquidTransient, context: RunContext, *, momentum_decay_rate_provider=None, momentum_decay_rate_evidence_id=None):
    """Use CFL=1 characteristics with reported wave-speed adjustment and audited initial equilibrium.

    Head boundaries supply unresolved reservoir flow. Withdrawal histories are actual
    fluid withdrawals, not actuator commands; valve mechanics belong to their owner model.
    """
    spec = specification
    if not isinstance(spec, LiquidTransient) or not isinstance(context, RunContext):
        raise TypeError("LiquidTransient and RunContext are required")
    spec = LiquidTransient.model_validate_json(spec.model_dump_json())
    if momentum_decay_rate_provider is not None:
        if not callable(momentum_decay_rate_provider):raise TypeError('momentum decay provider must be callable')
        if not isinstance(momentum_decay_rate_evidence_id,str) or not momentum_decay_rate_evidence_id.strip():raise ValueError('state-dependent wall drag requires explicit caller data/closure evidence identity')
        if any(p.darcy_friction_factor!=0 for p in spec.pipes):raise ValueError('state-dependent and fixed Darcy resistance cannot coexist')
    elif momentum_decay_rate_evidence_id is not None:raise ValueError('wall-drag evidence supplied without a provider')
    characteristic_source_pin = hashlib.sha256(Path(__file__).with_name("moc_characteristics.py").read_bytes()).hexdigest()
    count = math.ceil(spec.duration_s / spec.requested_timestep_s)
    if count > spec.maximum_steps:
        raise ValueError("the requested clock exceeds the step budget")
    if (count + 1) * (7 * len(spec.pipes) + 5 * len(spec.valves) + 20 * len(spec.pumps) + 5) > spec.maximum_history_values:
        raise ValueError("the requested output exceeds the history budget")
    dt = spec.duration_s / count
    nodes = {n.id: n for n in spec.nodes}
    connections = {key: [] for key in nodes}
    grid, initial_net = {}, dict.fromkeys(nodes, 0.0)
    total_segments = 0
    for pipe in spec.pipes:
        segments = round(pipe.length_m / (pipe.wave_speed_m_s * dt))
        if segments < 2:
            raise ValueError("reduce timestep to provide at least two segments per pipe")
        total_segments += segments
        if total_segments > spec.maximum_total_segments:
            raise ValueError("the requested mesh exceeds the segment budget")
        dx = pipe.length_m / segments
        wave = dx / dt
        change = abs(wave / pipe.wave_speed_m_s - 1)
        if change > spec.maximum_wave_speed_adjustment + 1e-14:
            raise ValueError("wave-speed adjustment exceeds its bound; refine the requested timestep")
        area = math.pi * pipe.diameter_m**2 / 4
        b = wave / (spec.gravity_m_s2 * area)
        resistance = pipe.darcy_friction_factor * dx / (2 * spec.gravity_m_s2 * pipe.diameter_m * area**2)
        h = np.linspace(
            nodes[pipe.start_node].initial_head_m, nodes[pipe.end_node].initial_head_m, segments + 1
        )
        q = np.full(segments + 1, pipe.initial_volume_flow_m3_s)
        expected_drop = resistance * segments * q[0] * abs(q[0])
        if momentum_decay_rate_provider is None and abs(h[0] - h[-1] - expected_drop) > spec.initial_head_tolerance_m:
            raise ValueError("initial flow/head gradient is not in equilibrium with the declared Darcy loss")
        chainage = np.linspace(0.0, pipe.length_m, segments + 1)
        if pipe.elevation_profile:
            elevation = np.interp(
                chainage,
                [point.chainage_m for point in pipe.elevation_profile],
                [point.elevation_m for point in pipe.elevation_profile],
            )
        else:
            elevation = np.linspace(
                nodes[pipe.start_node].elevation_m, nodes[pipe.end_node].elevation_m, segments + 1
            )
        grid[pipe.id] = {
            "pipe": pipe,
            "h": h,
            "q": q,
            "initial_h": h.copy(),
            "b": b,
            "r": resistance,
            "dx": dx,
            "wave": wave,
            "area": area,
            "elevation": elevation,
            "relative_wave_speed_adjustment": change,
            "chainage": chainage,
        }
        connections[pipe.start_node].append((pipe.id, True))
        connections[pipe.end_node].append((pipe.id, False))
        initial_net[pipe.start_node] += q[0]
        initial_net[pipe.end_node] -= q[-1]
    def current_friction_loss(item,time_s):
        from .liquid_moc_friction import LiquidMOCFrictionState
        pipe=item['pipe'];h=item['h'];q=item['q']
        absolute=spec.ambient_pressure_pa_abs+spec.density_kg_m3*spec.gravity_m_s2*(h-item['elevation'])
        if np.any(absolute<=0) or not np.all(np.isfinite(absolute)):raise ValueError('friction state requires positive finite absolute pressure')
        state=LiquidMOCFrictionState(pipe.id,float(time_s),pipe.diameter_m,spec.density_kg_m3,
            tuple(map(float,item['chainage'])),tuple(map(float,absolute)),tuple(map(float,q/item['area'])))
        rate=np.asarray(momentum_decay_rate_provider(state),dtype=float)
        if rate.shape!=q.shape or not np.all(np.isfinite(rate)) or np.any(rate<0):raise ValueError('friction provider must return aligned finite nonnegative rates in 1/s')
        if dt*float(np.max(rate))>=.5:raise ValueError('friction source is too stiff for this explicit step; reduce timestep')
        loss=dt*item['wave']/spec.gravity_m_s2*rate*q/item['area']
        if not np.all(np.isfinite(loss)):raise ValueError('friction head source exceeds finite support')
        return loss

    if momentum_decay_rate_provider is not None:
        for item in grid.values():
            initial_loss=current_friction_loss(item,0.)
            segment_drop=.5*(initial_loss[:-1]+initial_loss[1:])
            if np.max(np.abs(item['h'][:-1]-item['h'][1:]-segment_drop))>spec.initial_head_tolerance_m:
                raise ValueError('initial local head gradient is not in equilibrium with supplied state-dependent wall drag')

    valve_flows = {v.id: v.initial_volume_flow_m3_s for v in spec.valves}
    valve_connections = {name: [] for name in nodes}
    for valve in spec.valves:
        valve_connections[valve.start_node].append((valve.id, 1))
        valve_connections[valve.end_node].append((valve.id, -1))
        if valve.evidence.get("kind") == "valve_motion_binding":
            from .valve_motion import check_liquid_motion_binding

            check_liquid_motion_binding(valve, context, spec.duration_s)
        area = valve.effective_area_m2.at(0.0)
        flow = valve.initial_volume_flow_m3_s
        if area == 0:
            if flow != 0.0:
                raise ValueError("a closed valve cannot have a nonzero initial flow")
        else:
            scaled = flow / (area * math.sqrt(2 * spec.gravity_m_s2))
            drop = scaled * abs(scaled)
            if (
                not math.isfinite(drop)
                or abs(nodes[valve.start_node].initial_head_m - nodes[valve.end_node].initial_head_m - drop)
                > spec.initial_head_tolerance_m
            ):
                raise ValueError("initial valve flow/head loss is inconsistent with its effective CdA")
        initial_net[valve.start_node] += flow
        initial_net[valve.end_node] -= flow
    pump_states={}
    pump_connections={name:[] for name in nodes}
    if spec.pumps:
        from .pump_moc_coupling import pump_initial_state
        for pump in spec.pumps:
            pump_states[pump.id]=pump_initial_state(pump,spec,nodes,context)
            initial_net[pump.start_node]+=pump.initial_volume_flow_m3_s
            initial_net[pump.end_node]-=pump.initial_volume_flow_m3_s
            pump_connections[pump.start_node].append((pump.id,1))
            pump_connections[pump.end_node].append((pump.id,-1))
    for name, node in nodes.items():
        if node.boundary == "withdrawal":
            emitter = node.atmospheric_emitter.at(0) * math.sqrt(
                max(node.initial_head_m - node.elevation_m, 0)
            )
            if abs(initial_net[name] + node.history.at(0) + emitter) > spec.initial_flow_tolerance_m3_s:
                raise ValueError("initial junction flow is not balanced with its withdrawal and emitter")
    histories = {
        p.id: {key: [] for key in ("start_head_m", "end_head_m", "start_flow_m3_s", "end_flow_m3_s")}
        for p in spec.pipes
    }
    valve_histories = {
        v.id: {k: [] for k in ("volume_flow_m3_s", "head_drop_m", "effective_area_m2", "dissipated_power_w")}
        for v in spec.valves
    }
    valve_solver = []
    pump_solver=[]
    pump_histories={p.id:[] for p in spec.pumps}
    pipe_balance = {
        p.id: {"storage_change_m3": [], "net_inflow_m3_s": [], "residual_m3": [], "integrated_inflow_m3": 0.0}
        for p in spec.pipes
    }
    node_heads = {name: node.initial_head_m for name, node in nodes.items()}
    times, net_exchange, storage, balance_error, pressure_minima, pressure_locations, continuity = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    vapour_margin_history = []
    first_vapour_boundary = None
    integrated_exchange = 0.0
    maximum_mach, maximum_strain = 0.0, 0.0
    status, reasons = "computed", []

    def observe(time_s):
        nonlocal integrated_exchange, maximum_mach, maximum_strain, first_vapour_boundary
        inflow, residuals = 0.0, []
        for name, node in nodes.items():
            outward = sum(
                float(grid[key]["q"][0] if start else -grid[key]["q"][-1]) for key, start in connections[name]
            )
            outward += sum(sign * valve_flows[key] for key, sign in valve_connections[name])
            outward += sum(sign * pump_states[key]["volume_flow_m3_s"] for key,sign in pump_connections[name])
            emitter_coefficient = node.atmospheric_emitter.at(time_s)
            head_above_ambient = node_heads[name] - node.elevation_m
            emitter = emitter_coefficient * math.sqrt(max(head_above_ambient, 0))
            if emitter_coefficient and head_above_ambient < 0:
                reasons.append("An open atmospheric emitter requires unresolved air ingress.")
            if node.boundary == "head":
                inflow += outward
            else:
                inflow -= node.history.at(time_s) + emitter
                residuals.append(abs(outward + node.history.at(time_s) + emitter))
        for valve in spec.valves:
            row = valve_histories[valve.id]
            flow = valve_flows[valve.id]
            drop = node_heads[valve.start_node] - node_heads[valve.end_node]
            for field, value in (
                ("volume_flow_m3_s", flow),
                ("head_drop_m", drop),
                ("effective_area_m2", valve.effective_area_m2.at(time_s)),
                ("dissipated_power_w", spec.density_kg_m3 * spec.gravity_m_s2 * flow * drop),
            ):
                row[field].append(float(value))
        for pump in spec.pumps:
            state=pump_states[pump.id]
            pump_histories[pump.id].append(dict(time_s=float(time_s),**state,
                rotor_energy_J=0.5*pump.inertia_kg_m2*state["speed_rad_s"]**2))
        volume, minimum, minimum_location = 0.0, math.inf, None
        for key, item in grid.items():
            maximum_mach = max(maximum_mach, float(np.max(np.abs(item["q"])) / item["area"] / item["wave"]))
            maximum_strain = max(
                maximum_strain,
                float(spec.gravity_m_s2 * np.max(np.abs(item["h"] - item["initial_h"])) / item["wave"] ** 2),
            )
            integral = float(np.trapezoid(item["h"] - item["initial_h"], dx=item["dx"]))
            pipe_storage = spec.gravity_m_s2 * item["area"] / item["wave"] ** 2 * integral
            volume += pipe_storage
            budget = pipe_balance[key]
            net = float(item["q"][0] - item["q"][-1])
            if budget["net_inflow_m3_s"]:
                budget["integrated_inflow_m3"] += 0.5 * (budget["net_inflow_m3_s"][-1] + net) * dt
            budget["storage_change_m3"].append(pipe_storage)
            budget["net_inflow_m3_s"].append(net)
            budget["residual_m3"].append(pipe_storage - budget["integrated_inflow_m3"])
            absolute = spec.ambient_pressure_pa_abs + spec.density_kg_m3 * spec.gravity_m_s2 * (
                item["h"] - item["elevation"]
            )
            minimum_index = int(np.argmin(absolute))
            pipe_minimum = float(absolute[minimum_index])
            if pipe_minimum < minimum:
                minimum = pipe_minimum
                minimum_location = {
                    "pipe_id": key,
                    "segment_index": minimum_index,
                    "fraction_along_pipe": minimum_index / (len(absolute) - 1),
                }
            row = histories[key]
            for field, value in (
                ("start_head_m", item["h"][0]),
                ("end_head_m", item["h"][-1]),
                ("start_flow_m3_s", item["q"][0]),
                ("end_flow_m3_s", item["q"][-1]),
            ):
                row[field].append(float(value))
        if net_exchange:
            integrated_exchange += 0.5 * (net_exchange[-1] + inflow) * dt
        times.append(float(time_s))
        net_exchange.append(float(inflow))
        storage.append(volume)
        balance_error.append(volume - integrated_exchange)
        pressure_minima.append(minimum)
        pressure_locations.append({**minimum_location, "time_s": float(time_s)})
        vapour_margin_history.append(minimum - spec.vapour_pressure_pa_abs)
        continuity.append(max(residuals, default=0.0))
        if minimum <= spec.vapour_pressure_pa_abs:
            if first_vapour_boundary is None:
                first_vapour_boundary = {
                    **minimum_location,
                    "time_s": float(time_s),
                    "minimum_absolute_pressure_pa": minimum,
                    "vapour_pressure_margin_pa": minimum - spec.vapour_pressure_pa_abs,
                }
            reasons.append(
                "Pressure reached the vapour-pressure boundary; column separation is outside this solver."
            )

        if maximum_mach > spec.maximum_acoustic_mach:
            reasons.append("The declared small-Mach-number acoustic domain was exceeded.")
        if maximum_strain > spec.maximum_linearized_storage_strain:
            reasons.append("The declared linearized storage-strain domain was exceeded.")

    observe(0.0)
    for step in range(1, count + 1):
        if reasons:
            status = "out_of_scope"
            break
        time_s, incoming = step * dt, {}
        for key, item in grid.items():
            h, q, b, r = item["h"], item["q"], item["b"], item["r"]
            if momentum_decay_rate_provider is None:
                damping = r * np.max(np.abs(q)) / b
                if damping >= 0.5:
                    raise ValueError("friction source is too stiff for this explicit step; reduce timestep")
                plus, minus, incident_at_start, incident_at_end = characteristic_invariants(h, q, q, b, r)
            else:
                loss=current_friction_loss(item,(step-1)*dt)
                plus,minus,incident_at_start,incident_at_end=characteristic_invariants(h,q,q,b,0.)
                plus-=loss[:-2];minus+=loss[2:]
                incident_at_start+=loss[1];incident_at_end-=loss[-2]
            new_h, new_q = h.copy(), q.copy()
            new_h[1:-1], new_q[1:-1] = (plus + minus) / 2, (plus - minus) / (2 * b)
            item["new_h"], item["new_q"] = new_h, new_q
            incoming[(key, True)] = incident_at_start
            incoming[(key, False)] = incident_at_end
        coefficients, forcings, free_heads = {}, {}, {}
        for name, node in nodes.items():
            conductance = sum(1 / grid[key]["b"] for key, _ in connections[name])
            forcing = sum(incoming[(key, start)] / grid[key]["b"] for key, start in connections[name])
            coefficients[name], forcings[name] = conductance, forcing
            if node.boundary == "head":
                head = node.history.at(time_s)
            else:
                rhs = forcing - node.history.at(time_s)
                head = rhs / conductance
                k = node.atmospheric_emitter.at(time_s)
                available = rhs - conductance * node.elevation_m
                if k and available > 0:
                    root = 2 * available / (math.sqrt(k * k + 4 * conductance * available) + k)
                    head = node.elevation_m + root * root
            free_heads[name] = head
        if spec.pumps:
            from .pump_moc_coupling import solve_pump_nodes

            solved,flows,pump_next,audit=solve_pump_nodes(spec,nodes,free_heads,coefficients,forcings,time_s,dt,pump_states)
            pump_solver.append(audit)
            if not audit["root_qualified"]:
                status="withheld"
                reasons.append("Simultaneous pump/rotor/valve/junction closure exceeded its declared residual or work budget.")
                break
            node_heads.update(solved)
            valve_flows.update(flows)
            pump_states.update(pump_next)
        elif spec.valves:
            from .valve_boundary import solve_valve_nodes

            solved, flows, audit = solve_valve_nodes(spec, nodes, free_heads, coefficients, forcings, time_s)
            valve_solver.append(audit)
            if not audit["root_qualified"]:
                status = "withheld"
                reasons.append(
                    "Valve/junction closure exceeded its declared numerical tolerance or evaluation budget."
                )
                break
            node_heads.update(solved)
            valve_flows.update(flows)
        else:
            node_heads.update(free_heads)
        for name in nodes:
            head = node_heads[name]
            for key, start in connections[name]:
                item = grid[key]
                index = 0 if start else -1
                item["new_h"][index] = head
                sign = 1 if start else -1
                item["new_q"][index] = sign * (head - incoming[(key, start)]) / item["b"]
        for item in grid.values():
            item["h"], item["q"] = item.pop("new_h"), item.pop("new_q")
            if not (np.isfinite(item["h"]).all() and np.isfinite(item["q"]).all()):
                raise ArithmeticError("nonfinite transient state")
        observe(time_s)
    if reasons and status == "computed":
        status = "out_of_scope"
    maximum_balance = max(abs(value) for value in balance_error)
    maximum_pipe_balance = max(abs(value) for row in pipe_balance.values() for value in row["residual_m3"])
    for budget in pipe_balance.values():
        budget["maximum_absolute_residual_m3"] = max(abs(value) for value in budget["residual_m3"])
        budget["within_tolerance"] = budget["maximum_absolute_residual_m3"] <= spec.mass_balance_tolerance_m3
    if max(maximum_balance, maximum_pipe_balance) > spec.mass_balance_tolerance_m3 and status == "computed":
        status = "withheld"
        reasons.append("Linearized volume balance exceeded the declared numerical tolerance.")
    if (spec.valves or spec.pumps) and max(continuity) > spec.valve_flow_tolerance_m3_s and status == "computed":
        status = "withheld"
        reasons.append("Observed valve/junction continuity exceeded the declared flow tolerance.")
    runtime = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pipeline_integrity": __version__,
    }
    if spec.pumps or any(row["method"] == "scipy_trf_analytic_jacobian" for row in valve_solver):
        import scipy

        runtime["scipy"] = scipy.__version__
    if hashlib.sha256(Path(__file__).with_name("moc_characteristics.py").read_bytes()).hexdigest() != characteristic_source_pin:
        raise ValueError("shared liquid characteristic source changed during simulation")
    return {
        "schema": "pipeline_integrity.liquid_transient.v1",
        "model_version": "3" if spec.pumps else "2",
        "model_source_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "characteristic_source_digest": characteristic_source_pin,
        "valve_boundary_source_digest": None
        if not spec.valves
        else hashlib.sha256(Path(__file__).with_name("valve_boundary.py").read_bytes()).hexdigest(),
        "pump_boundary_source_digest": None if not spec.pumps else hashlib.sha256(Path(__file__).with_name("pump_moc_coupling.py").read_bytes()).hexdigest(),
        "pump_map_source_digest": None if not spec.pumps else hashlib.sha256(Path(__file__).with_name("rotating_dynamics.py").read_bytes()).hexdigest(),
        "runtime": runtime,
        "status": status,
        "context": context.model_dump(mode="json"),
        "specification_digest": digest(spec),
        "context_digest": digest(context),
        "state_dependent_friction": None if momentum_decay_rate_provider is None else {
            "provider_class":type(momentum_decay_rate_provider).__module__+'.'+type(momentum_decay_rate_provider).__qualname__,
            "rate_units":"1/s","applied_at":"previous-time actual characteristic feet",
            "fixed_Darcy_drag_replaced":True,"thermal_evolution_predicted":False,
            "evidence_id":momentum_decay_rate_evidence_id},
        "time_s": times,
        "pipes": histories,
        "valves": valve_histories,
        "valve_solver": valve_solver,
        "pumps": pump_histories,
        "pump_solver": pump_solver,
        "simultaneous_pump_rotor_coupling": bool(spec.pumps),
        "pipe_volume_balance": pipe_balance,
        "maximum_pipe_volume_residual_m3": maximum_pipe_balance,
        "all_volume_balances_within_tolerance": max(maximum_balance, maximum_pipe_balance)
        <= spec.mass_balance_tolerance_m3,
        "timestep_s": dt,
        "grid": {
            key: {
                "segments": len(item["h"]) - 1,
                "wave_speed_m_s": item["wave"],
                "relative_wave_speed_adjustment": item["relative_wave_speed_adjustment"],
            }
            for key, item in grid.items()
        },
        "volume_balance": {
            "linearized_storage_change_m3": storage,
            "net_external_inflow_m3_s": net_exchange,
            "residual_m3": balance_error,
            "maximum_absolute_residual_m3": maximum_balance,
            "tolerance_m3": spec.mass_balance_tolerance_m3,
            "within_tolerance": maximum_balance <= spec.mass_balance_tolerance_m3,
        },
        "maximum_junction_residual_m3_s": max(continuity),
        "minimum_absolute_pressure_pa": min(pressure_minima),
        "minimum_absolute_pressure_location": pressure_locations[int(np.argmin(pressure_minima))],
        "vapour_pressure_margin_pa": min(pressure_minima) - spec.vapour_pressure_pa_abs,
        "vapour_pressure_margin_history_pa": vapour_margin_history,
        "first_vapour_pressure_boundary": first_vapour_boundary,
        "maximum_acoustic_mach": maximum_mach,
        "maximum_linearized_storage_strain": maximum_strain,
        "notes": list(dict.fromkeys(reasons)),
        "assumptions": [
            "Small-Mach-number single-phase liquid acoustics; constant physical density. Wall drag uses the declared fixed coefficient or explicit current-state rate provider.",
            "Pipe elasticity is represented by the caller-supplied wave speed.",
            "Withdrawal histories describe actual fluid flow, not valve commands or actuator mechanics.",
            "Valves use declared effective CdA and quasi-steady signed orifice loss with no internal storage or inertia.",
            "Cavitation, air ingress and two-phase flow are unresolved.",
            "Pumps, when supplied, use signed reviewed maps, separate inertial shafts and simultaneous characteristic-node closure; shared-shaft gearing and check-valve mechanics are unresolved.",
        ],
        "independent_field_validation": False,
    }
