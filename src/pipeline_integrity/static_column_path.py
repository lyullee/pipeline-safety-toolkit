"""Conditional pressure budgets on explicitly connected constant-density columns.

Each oriented segment is supplied. The operator never replaces a declared
fluid topology by a sum of absolute elevation changes. Optional reviewed
resistance is a prescribed pressure budget, not a friction or pig-motion law.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative, Positive


class StaticColumnSegment(Contract):
    start_node_id: Name
    end_node_id: Name
    start_upward_elevation_m: float
    end_upward_elevation_m: float
    density_kg_m3: Positive | None = None
    fluid_state_basis_id: Name | None = None
    phase: Literal["single_liquid", "single_gas", "unknown"] = "unknown"
    constant_density_column_reviewed: bool = False
    prescribed_resisting_drop_pa: Nonnegative = 0.
    prescribed_resistance_basis_id: Name | None = None


class StaticNodePressureBounds(Contract):
    node_id: Name
    lower_pressure_pa: float | None = None
    upper_pressure_pa: float | None = None
    bounds_basis_id: Name
    external_limit_applicability_reviewed: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.lower_pressure_pa is not None and self.upper_pressure_pa is not None and self.lower_pressure_pa > self.upper_pressure_pa:
            raise ValueError("pressure bounds must be ordered")
        return self


class StaticColumnPathRequest(Contract):
    segments: tuple[StaticColumnSegment, ...]
    inlet_pressure_pa: float | None = None
    pressure_reference: Literal["absolute", "common_gauge"]
    pressure_datum_id: Name | None = None
    vertical_frame_id: Name | None = None
    gravity_m_s2: Positive = 9.80665
    topology_and_interface_pressure_continuity_reviewed: bool = False
    prescribed_resistance_applicability_reviewed: bool = False
    required_terminal_pressure_pa: float | None = None
    node_pressure_bounds: tuple[StaticNodePressureBounds, ...] = ()


class StaticColumnNodeResult(Contract):
    node_id: Name
    upward_elevation_m: float
    pressure_pa: float
    lower_pressure_margin_pa: float | None = None
    upper_pressure_margin_pa: float | None = None
    supplied_pressure_bounds_satisfied: bool | None = None


class StaticColumnPathResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    nodes: tuple[StaticColumnNodeResult, ...] = ()
    signed_gravity_requirement_pa: float | None = None
    supplied_resistance_requirement_pa: Nonnegative | None = None
    required_inlet_minus_outlet_pressure_pa: float | None = None
    available_inlet_minus_required_terminal_pressure_pa: float | None = None
    available_pressure_margin_pa: float | None = None
    required_inlet_pressure_for_terminal_pa: float | None = None
    pressure_reference: Literal["absolute", "common_gauge"] | None = None
    pressure_datum_id: Name | None = None
    vertical_frame_id: Name | None = None
    supplied_node_pressure_bounds_all_satisfied: bool | None = None
    pressure_budget_includes_prescribed_resistance: bool = False
    pig_motion_contact_bypass_or_air_interface_evolution_solved: bool = False
    fluid_density_or_operating_limits_inferred: bool = False
    field_or_standard_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_static_column_path(request: StaticColumnPathRequest) -> StaticColumnPathResult:
    """Replay hydrostatics on an oriented path, with explicit datum and topology.

    All caller pressure bounds and the terminal pressure use the request's
    pressure reference/datum. Gas density is accepted only as a reviewed
    constant over that segment; no gas EOS or compressibility is inferred.
    Surface-tension/interface jumps and motion must be negligible or excluded
    by the declared interface-pressure continuity review.
    """
    if not isinstance(request, StaticColumnPathRequest):
        raise TypeError("StaticColumnPathRequest required")
    q = StaticColumnPathRequest.model_validate(request.model_dump(mode="python"))
    missing = ["missing " + name for name in ("inlet_pressure_pa", "pressure_datum_id", "vertical_frame_id") if getattr(q, name) is None]
    if not q.segments:
        missing.append("explicit ordered fluid columns are required")
    if not q.topology_and_interface_pressure_continuity_reviewed:
        missing.append("column topology and pressure-continuous interfaces must be reviewed")
    for i, s in enumerate(q.segments):
        if s.density_kg_m3 is None or s.fluid_state_basis_id is None or s.phase == "unknown" or not s.constant_density_column_reviewed:
            missing.append(f"segment {i} needs a reviewed constant-density single-phase state")
        if s.prescribed_resisting_drop_pa and (s.prescribed_resistance_basis_id is None or not q.prescribed_resistance_applicability_reviewed):
            missing.append(f"segment {i} prescribed resistance needs an explicit reviewed basis")
    if any(not b.external_limit_applicability_reviewed for b in q.node_pressure_bounds):
        missing.append("caller node pressure bounds need applicability review")
    if missing:
        return StaticColumnPathResult(status="needs_data", issues=tuple(missing))
    for left, right in zip(q.segments, q.segments[1:]):
        if left.end_node_id != right.start_node_id or left.end_upward_elevation_m != right.start_upward_elevation_m:
            return StaticColumnPathResult(status="out_of_scope", issues=("segments must share exactly matching interface nodes and elevations",))
    node_ids = [q.segments[0].start_node_id] + [s.end_node_id for s in q.segments]
    if len(set(node_ids)) != len(node_ids):
        return StaticColumnPathResult(status="out_of_scope", issues=("repeated nodes require a network consistency solve outside this ordered path",))
    bounds = {b.node_id: b for b in q.node_pressure_bounds}
    if len(bounds) != len(q.node_pressure_bounds) or not set(bounds) <= set(node_ids):
        return StaticColumnPathResult(status="out_of_scope", issues=("pressure bounds require unique nodes on this path",))
    if q.pressure_reference == "absolute" and (q.inlet_pressure_pa <= 0 or
            (q.required_terminal_pressure_pa is not None and q.required_terminal_pressure_pa <= 0)):
        return StaticColumnPathResult(status="out_of_scope", issues=("absolute boundary pressures must be positive",))
    try:
        pressure = q.inlet_pressure_pa
        raw_nodes = [(node_ids[0], q.segments[0].start_upward_elevation_m, pressure)]
        gravity_drops, resistance_drops = [], []
        for s in q.segments:
            dz = s.end_upward_elevation_m - s.start_upward_elevation_m
            if q.pressure_reference == "absolute":
                from .models.hydraulics import Hydrostatic, hydrostatic
                value = hydrostatic(Hydrostatic(inlet_pressure_pa_abs=pressure, density_kg_m3=s.density_kg_m3,
                    outlet_minus_inlet_elevation_m=dz, gravity_m_s2=q.gravity_m_s2))
                if value.status != "computed":
                    raise ArithmeticError
                hydrostatic_pressure = value.values["outlet_pressure_pa_abs"]
                gravity_drop = s.density_kg_m3 * q.gravity_m_s2 * dz
            else:
                from fluids.core import P_from_head
                gravity_drop = P_from_head(dz, rho=s.density_kg_m3, g=q.gravity_m_s2)
                hydrostatic_pressure = pressure - gravity_drop
            gravity_drops.append(gravity_drop)
            resistance_drops.append(s.prescribed_resisting_drop_pa)
            # Keep the native local-domain check above, but do not repeatedly
            # round a large common pressure datum at every interface. The
            # signed pressure budget is accumulated before applying that datum.
            pressure = math.fsum((q.inlet_pressure_pa, -math.fsum(gravity_drops), -math.fsum(resistance_drops)))
            if not math.isfinite(hydrostatic_pressure) or not math.isfinite(pressure) or not math.isfinite(gravity_drop) or (q.pressure_reference == "absolute" and pressure <= 0):
                raise ArithmeticError
            raw_nodes.append((s.end_node_id, s.end_upward_elevation_m, pressure))
        gravity, resistance = math.fsum(gravity_drops), math.fsum(resistance_drops)
        requirement = math.fsum((gravity, resistance))
        if not all(math.isfinite(v) for v in (gravity, resistance, requirement)):
            raise ArithmeticError
        values = dict(status="computed", signed_gravity_requirement_pa=gravity,
            supplied_resistance_requirement_pa=resistance, required_inlet_minus_outlet_pressure_pa=requirement,
            pressure_reference=q.pressure_reference, pressure_datum_id=q.pressure_datum_id,
            vertical_frame_id=q.vertical_frame_id, pressure_budget_includes_prescribed_resistance=resistance > 0)
        if q.required_terminal_pressure_pa is not None:
            available = q.inlet_pressure_pa - q.required_terminal_pressure_pa
            margin = available - requirement
            required_inlet = q.required_terminal_pressure_pa + requirement
            if not all(math.isfinite(v) for v in (available, margin, required_inlet)):
                raise ArithmeticError
            values.update(available_inlet_minus_required_terminal_pressure_pa=available,
                available_pressure_margin_pa=margin, required_inlet_pressure_for_terminal_pa=required_inlet)
        nodes = []
        for node, elevation, pressure in raw_nodes:
            n = dict(node_id=node, upward_elevation_m=elevation, pressure_pa=pressure)
            if node in bounds:
                b = bounds[node]
                lower = None if b.lower_pressure_pa is None else pressure - b.lower_pressure_pa
                upper = None if b.upper_pressure_pa is None else b.upper_pressure_pa - pressure
                if any(v is not None and not math.isfinite(v) for v in (lower, upper)):
                    raise ArithmeticError
                n.update(lower_pressure_margin_pa=lower, upper_pressure_margin_pa=upper,
                    supplied_pressure_bounds_satisfied=all(v is None or v >= 0 for v in (lower, upper)))
            nodes.append(StaticColumnNodeResult(**n))
        values.update(nodes=tuple(nodes), supplied_node_pressure_bounds_all_satisfied=
            all(n.supplied_pressure_bounds_satisfied for n in nodes if n.node_id in bounds) if bounds else None)
        return StaticColumnPathResult(**values)
    except (ArithmeticError, ValueError):
        return StaticColumnPathResult(status="out_of_scope", issues=("column path leaves positive absolute or finite pressure arithmetic support",))
