"""Implicit valve/junction closure for the liquid characteristic solver."""

import math

import numpy as np


def solve_valve_nodes(spec, nodes, free_heads, coefficients, forcings, time_s):
    """Qualify raw SI closure residuals independently of the optimizer's stop flag."""
    flows = dict.fromkeys((v.id for v in spec.valves), 0.0)
    active = [(v, v.effective_area_m2.at(time_s)) for v in spec.valves if v.effective_area_m2.at(time_s) > 0]
    if not active:
        return (
            free_heads.copy(),
            flows,
            {
                "time_s": time_s,
                "root_qualified": True,
                "method": "all_valves_closed",
                "maximum_flow_residual_m3_s": 0.0,
                "maximum_head_residual_m": 0.0,
                "function_evaluations": 0,
            },
        )
    from scipy.optimize import least_squares

    names = sorted(
        {key for v, _ in active for key in (v.start_node, v.end_node) if nodes[key].boundary == "withdrawal"}
    )
    indices = {name: i for i, name in enumerate(names)}
    size = len(names) + len(active)
    if size > spec.maximum_valve_unknowns:
        raise ValueError("the simultaneous valve/junction unknown budget was exceeded")
    head_scale = max(1.0, *[abs(h) for h in free_heads.values()])
    flow_scale = max(
        1e-12, *[abs(q) for q in forcings.values()], *[abs(p.initial_volume_flow_m3_s) for p in spec.pipes]
    )
    capacities = np.array([area * math.sqrt(2 * spec.gravity_m_s2 * head_scale) for _, area in active])
    if not np.isfinite(capacities).all() or np.any(capacities <= 0):
        raise ValueError("the valve conductance scale cannot be represented")
    x0 = [free_heads[name] / head_scale for name in names]
    for (valve, _), capacity in zip(active, capacities, strict=True):
        drop = (free_heads[valve.start_node] - free_heads[valve.end_node]) / head_scale
        impedance = sum(1 / coefficients[key] for key in (valve.start_node, valve.end_node) if key in indices)
        linear = impedance * capacity / head_scale
        denominator = linear + math.hypot(linear, 2 * math.sqrt(abs(drop)))
        x0.append(0.0 if drop == 0 else 2 * drop / denominator)

    def unpack(values):
        heads = free_heads.copy()
        heads.update({name: float(values[i] * head_scale) for name, i in indices.items()})
        return heads, capacities * values[len(names) :]

    def evaluate(values, *, jacobian=False):
        heads, q = unpack(values)
        residual = np.zeros(size)
        jac = np.zeros((size, size)) if jacobian else None
        for name, i in indices.items():
            node, head = nodes[name], heads[name]
            k = node.atmospheric_emitter.at(time_s)
            above = head - node.elevation_m
            emission = k * math.sqrt(max(above, 0.0))
            residual[i] = (
                coefficients[name] * head - forcings[name] + node.history.at(time_s) + emission
            ) / flow_scale
            if jacobian:
                derivative = k / (2 * math.sqrt(above)) if above > 0 else 0.0
                jac[i, i] = (coefficients[name] + derivative) * head_scale / flow_scale
        for j, (valve, _) in enumerate(active):
            column = len(names) + j
            for key, sign in ((valve.start_node, 1), (valve.end_node, -1)):
                if key in indices:
                    i = indices[key]
                    residual[i] += sign * q[j] / flow_scale
                    if jacobian:
                        jac[i, column] += sign * capacities[j] / flow_scale
            residual[column] = (heads[valve.start_node] - heads[valve.end_node]) / head_scale - values[
                column
            ] * abs(values[column])
            if jacobian:
                if valve.start_node in indices:
                    jac[column, indices[valve.start_node]] = 1.0
                if valve.end_node in indices:
                    jac[column, indices[valve.end_node]] = -1.0
                jac[column, column] = -2 * abs(values[column])
        return jac if jacobian else residual

    result = least_squares(
        evaluate,
        np.asarray(x0),
        jac=lambda x: evaluate(x, jacobian=True),
        method="trf",
        tr_solver="exact",
        x_scale="jac",
        loss="linear",
        ftol=1e-12,
        xtol=1e-12,
        gtol=1e-12,
        max_nfev=spec.maximum_valve_solver_evaluations,
    )
    heads, values = unpack(result.x)
    residual = evaluate(result.x)
    flow_error = float(np.max(np.abs(residual[: len(names)]), initial=0.0) * flow_scale)
    head_error = float(np.max(np.abs(residual[len(names) :]), initial=0.0) * head_scale)
    qualified = (
        np.isfinite(result.x).all()
        and math.isfinite(flow_error)
        and math.isfinite(head_error)
        and flow_error <= spec.valve_flow_tolerance_m3_s
        and head_error <= spec.valve_head_tolerance_m
    )
    flows.update({valve.id: float(q) for (valve, _), q in zip(active, values, strict=True)})
    return (
        heads,
        flows,
        {
            "time_s": time_s,
            "root_qualified": bool(qualified),
            "method": "scipy_trf_analytic_jacobian",
            "function_evaluations": int(result.nfev),
            "jacobian_evaluations": int(result.njev or 0),
            "optimizer_success": bool(result.success),
            "optimizer_status": int(result.status),
            "maximum_flow_residual_m3_s": flow_error,
            "maximum_head_residual_m": head_error,
            "unknowns": size,
            "head_scale_m": head_scale,
            "flow_scale_m3_s": flow_scale,
        },
    )
