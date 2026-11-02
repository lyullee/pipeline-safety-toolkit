"""Optional EPANET network execution with explicit water-only scope and continuity audit."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from pydantic import Field

from .contracts import Contract, Nonnegative, Positive, digest
from .datasets import file_digest
from .epanet import solve_epanet


class WaterRun(Contract):
    duration_s: Nonnegative = 0.0
    hydraulic_timestep_s: Positive = 3600.0
    report_timestep_s: Positive = 3600.0
    continuity_tolerance_m3_s: Positive = 1e-6
    solver_relative_accuracy: float = Field(default=1e-8, ge=1e-8, le=0.01)
    solver_head_error_m: Positive = 1e-7
    solver_flow_change_m3_s: Positive = 1e-9
    solver_maximum_trials: int = Field(default=200, ge=1, le=10000)
    maximum_hydraulic_periods: int = Field(default=1000000, ge=1)


def _configure_hydraulics(model, specification: WaterRun):
    for value in [
        specification.duration_s,
        specification.hydraulic_timestep_s,
        specification.report_timestep_s,
    ]:
        if value != int(value):
            raise ValueError("this EPANET time adapter requires whole seconds")
    model.options.time.duration = int(specification.duration_s)
    model.options.time.hydraulic_timestep = int(specification.hydraulic_timestep_s)
    model.options.time.report_timestep = int(specification.report_timestep_s)
    model.options.time.report_start = 0
    model.options.time.statistic = "NONE"
    model.options.hydraulic.accuracy = specification.solver_relative_accuracy
    model.options.hydraulic.headerror = specification.solver_head_error_m
    model.options.hydraulic.flowchange = specification.solver_flow_change_m3_s
    model.options.hydraulic.trials = specification.solver_maximum_trials


def simulate_water_network(input_path, specification: WaterRun):
    import wntr

    source = Path(input_path).resolve()
    if source.suffix.lower() != ".inp" or not source.is_file():
        raise ValueError("a saved EPANET input file is required")
    model = wntr.network.WaterNetworkModel(str(source))
    if model.options.hydraulic.hydraulics is not None:
        raise ValueError(
            "external hydraulic caches need an explicit source binding; use a self-contained INP"
        )
    _configure_hydraulics(model, specification)
    model.options.quality.parameter = "NONE"
    result, diagnostics = solve_epanet(
        model, maximum_hydraulic_periods=specification.maximum_hydraulic_periods
    )
    return _water_results(source, model, result, specification, diagnostics)


def _water_results(source, model, result, specification: WaterRun, diagnostics=None):
    import numpy as np

    flows, demands = result.link["flowrate"], result.node["demand"]
    residual = np.zeros((len(flows), len(model.junction_name_list)))
    positions = {name: index for index, name in enumerate(model.junction_name_list)}
    for link_name, link in model.links():
        values = flows[link_name].to_numpy(float)
        if link.start_node_name in positions:
            residual[:, positions[link.start_node_name]] -= values
        if link.end_node_name in positions:
            residual[:, positions[link.end_node_name]] += values
    for name, index in positions.items():
        residual[:, index] -= demands[name].to_numpy(float)
    maximum = (
        (float(np.max(np.abs(residual))) if residual.size else 0.0) if np.isfinite(residual).all() else None
    )
    pressures = result.node["pressure"]
    finite = bool(
        np.isfinite(flows.to_numpy()).all()
        and np.isfinite(pressures.to_numpy()).all()
        and np.isfinite(demands.to_numpy()).all()
    )
    converged = result.error_code is None
    if diagnostics is not None:
        stats = diagnostics["maximum_native_hydraulic_statistics"]
        converged = (
            converged
            and not diagnostics["warning_count"]
            and diagnostics["hydraulic_options_verified"]
            and stats is not None
            and stats["iterations"] <= specification.solver_maximum_trials
            and stats["relative_error"] <= specification.solver_relative_accuracy
            and stats["head_error_m"] <= specification.solver_head_error_m
            and stats["flow_change_m3_s"] <= specification.solver_flow_change_m3_s
            and diagnostics["hydraulic_elapsed_s"] == int(specification.duration_s)
            and not diagnostics["effective_time_parameters"]["hydraulic_halt_flag"]
        )
    status = (
        "computed"
        if converged and finite and maximum is not None and maximum <= specification.continuity_tolerance_m3_s
        else "withheld"
    )
    return {
        "schema": "pipeline_integrity.water_network.v1",
        "status": status,
        "source_sha256": file_digest(source),
        "run_digest": digest(specification),
        "backend": {"wntr": version("wntr"), "epanet_version": "2.2"},
        "solver_error_code": result.error_code,
        "native_diagnostics": diagnostics,
        "specification": specification.model_dump(mode="json"),
        "time_s": [float(value) for value in flows.index],
        "pressure_head_m": {
            name: [float(v) if np.isfinite(v) else None for v in pressures[name]] for name in pressures
        },
        "flow_m3_s": {name: [float(v) if np.isfinite(v) else None for v in flows[name]] for name in flows},
        "maximum_junction_continuity_residual_m3_s": maximum,
        "topology": {
            "junctions": model.num_junctions,
            "pipes": model.num_pipes,
            "pumps": model.num_pumps,
            "valves": model.num_valves,
            "tanks": model.num_tanks,
        },
        "hydraulic_settings": {
            "headloss": model.options.hydraulic.headloss,
            "demand_model": model.options.hydraulic.demand_model,
        },
        "scope": "water-network hydraulic heads; pressure heads are not absolute pressure",
        "field_validation_claim": False,
    }
