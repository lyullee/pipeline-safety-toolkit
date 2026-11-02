"""EPANET constituent transport with explicit SI units, source clocks and native balance diagnostics."""

import bisect
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .epanet import solve_epanet
from .water import WaterRun, _configure_hydraulics, _water_results


class QualityStepPattern(Contract):
    time_s: tuple[Nonnegative, ...] = Field(min_length=1)
    factors: tuple[Nonnegative, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def clock(self):
        if (
            len(self.time_s) != len(self.factors)
            or self.time_s[0] != 0
            or any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False))
            or any(t != int(t) for t in self.time_s)
        ):
            raise ValueError("source factors require aligned increasing whole-second knots starting at zero")
        return self

    def at(self, time_s):
        return self.factors[max(0, bisect.bisect_right(self.time_s, time_s) - 1)]


class WaterQualitySource(Contract):
    source_id: Name
    node_id: Name
    source_type: Literal["CONCEN", "MASS", "FLOWPACED", "SETPOINT"]
    strength: Nonnegative
    unit: Literal["kg/m3", "kg/s"]
    pattern: QualityStepPattern | None = None

    @model_validator(mode="after")
    def strength_unit(self):
        expected = "kg/s" if self.source_type == "MASS" else "kg/m3"
        if self.unit != expected:
            raise ValueError("source strength must use the explicit SI unit of its EPANET source type")
        return self


class WaterQualityRun(Contract):
    hydraulics: WaterRun
    mode: Literal["concentration", "age", "source_fraction"]
    parameter_source_id: Name
    quality_timestep_s: Positive
    quality_tolerance: Positive
    initial_default_value: Nonnegative
    start_time_s: Nonnegative = 0.0
    initial_node_values: dict[str, Nonnegative] = Field(default_factory=dict)
    species_id: Name | None = None
    trace_node_id: Name | None = None
    sources: tuple[WaterQualitySource, ...] = ()
    bulk_decay_per_s: Nonnegative = 0.0
    pipe_bulk_decay_per_s: dict[str, Nonnegative] = Field(default_factory=dict)
    native_balance_ratio_tolerance: Positive = 1e-5
    admissible_value_error: Nonnegative = 1e-8
    maximum_pattern_values: int = Field(default=1000000, ge=1)
    maximum_returned_values: int = Field(default=5000000, ge=1)
    maximum_nominal_quality_steps: int = Field(default=2000000, ge=1)

    @model_validator(mode="after")
    def analysis_scope(self):
        h = self.hydraulics
        if (
            not math.isfinite(self.start_time_s + h.duration_s)
            or self.start_time_s + min(h.report_timestep_s, h.duration_s) == self.start_time_s
        ):
            raise ValueError("the declared context clock cannot represent the requested horizon")
        if h.duration_s <= 0 or self.quality_timestep_s != int(self.quality_timestep_s):
            raise ValueError("quality transport requires a positive horizon and whole-second quality steps")
        if self.mode == "concentration":
            if self.species_id is None or self.trace_node_id is not None:
                raise ValueError("concentration analysis requires a species and no trace-node interpretation")
        elif (
            self.species_id is not None or self.sources or self.bulk_decay_per_s or self.pipe_bulk_decay_per_s
        ):
            raise ValueError("age and source tracing cannot consume chemical sources or reaction parameters")
        if self.mode == "source_fraction":
            if (
                self.trace_node_id is None
                or self.initial_default_value
                or any(self.initial_node_values.values())
            ):
                raise ValueError(
                    "EPANET source tracing starts from zero and requires its explicit trace node"
                )
        elif self.trace_node_id is not None:
            raise ValueError("a trace node is only valid for source-fraction analysis")
        if len({s.node_id for s in self.sources}) != len(self.sources) or len(
            {s.source_id for s in self.sources}
        ) != len(self.sources):
            raise ValueError("source identifiers and injection nodes must be unique")
        return self


def _resample_patterns(model, sources, horizon_s, budget):
    old_step = int(model.options.time.pattern_timestep)
    old_start = int(model.options.time.pattern_start)
    if old_start < 0 or model.options.time.pattern_interpolation:
        raise ValueError("this EPANET adapter requires nonnegative, step-held input pattern clocks")
    knots = [int(t) for source in sources if source.pattern for t in source.pattern.time_s if t <= horizon_s]
    changing = any(t > 0 for t in knots)
    if not changing:
        return {"resampled": False, "pattern_timestep_s": old_step, "pattern_start_s": old_start}
    step = math.gcd(old_step, old_start, *knots)
    originals, total = [], 0
    for name, pattern in model.patterns():
        values = tuple(float(v) for v in pattern.multipliers)
        if not values or not pattern.wrap or any(not math.isfinite(v) for v in values):
            raise ValueError("source-file patterns must be finite nonempty periodic step sequences")
        count = len(values) * old_step // step
        total += count
        if total > budget:
            raise ValueError("preserving existing pattern periods exceeds the declared pattern budget")
        expanded = [values[((j * step + old_start) // old_step) % len(values)] for j in range(count)]
        originals.append((name, expanded, digest(values)))
    source_values = sum(
        (int(horizon_s) // step + 1) if any(0 < t <= horizon_s for t in source.pattern.time_s) else 1
        for source in sources
        if source.pattern is not None
    )
    if total + source_values > budget:
        raise ValueError("source event grid exceeds the declared pattern budget")
    for name, expanded, _ in originals:
        model.get_pattern(name).multipliers = expanded
    model.options.time.pattern_timestep = step
    model.options.time.pattern_start = 0
    return {
        "resampled": True,
        "old_pattern_timestep_s": old_step,
        "old_pattern_start_s": old_start,
        "pattern_timestep_s": step,
        "pattern_start_s": 0,
        "existing_pattern_values": total,
        "existing_pattern_digests": {name: sha for name, _, sha in originals},
        "hydraulic_event_grid_can_be_refined": True,
    }


def simulate_water_quality(input_path, specification: WaterQualityRun, context: RunContext):
    import numpy as np
    import wntr

    if not isinstance(specification, WaterQualityRun) or not isinstance(context, RunContext):
        raise TypeError("WaterQualityRun and RunContext are required")
    source = Path(input_path).resolve()
    if source.suffix.lower() != ".inp" or not source.is_file():
        raise ValueError("a saved water-network input file is required")
    model = wntr.network.WaterNetworkModel(str(source))
    if model.options.hydraulic.hydraulics is not None:
        raise ValueError(
            "an external hydraulic cache requires its own source binding; use a self-contained INP"
        )
    _configure_hydraulics(model, specification.hydraulics)
    ids = set(model.node_name_list)
    if set(specification.initial_node_values) - ids or any(
        s.node_id not in ids for s in specification.sources
    ):
        raise ValueError("quality initial states or sources refer to unknown network nodes")
    if specification.trace_node_id is not None and specification.trace_node_id not in ids:
        raise ValueError("the selected trace node is absent")
    if set(specification.pipe_bulk_decay_per_s) - set(model.pipe_name_list):
        raise ValueError("pipe decay parameters refer to an unknown pipe")
    reports = int(specification.hydraulics.duration_s // specification.hydraulics.report_timestep_s) + 1
    if reports * 3 * (model.num_nodes + model.num_links) > specification.maximum_returned_values:
        raise ValueError("the requested report arrays exceed their declared budget")
    original = {
        "quality_mode": model.options.quality.parameter,
        "removed_source_ids": list(model.source_name_list),
        "removed_source_nodes": {name: model.get_source(name).node_name for name in model.source_name_list},
    }
    for name in tuple(model.source_name_list):
        model.remove_source(name)
    mode = {"concentration": "CHEMICAL", "age": "AGE", "source_fraction": "TRACE"}[specification.mode]
    model.options.quality.parameter = mode
    model.options.quality.chemical_name = specification.species_id or "not_a_chemical"
    model.options.quality.inpfile_units = "mg/L"
    model.options.quality.trace_node = specification.trace_node_id
    conversion = {"concentration": 1000.0, "age": 1 / 3600.0, "source_fraction": 100.0}[specification.mode]
    model.options.quality.tolerance = specification.quality_tolerance * conversion
    for name, node in model.nodes():
        initial = specification.initial_node_values.get(name, specification.initial_default_value)
        node.initial_quality = initial * 100.0 if mode == "TRACE" else initial
    reaction = model.options.reaction
    reaction.bulk_order = reaction.wall_order = reaction.tank_order = 1
    reaction.bulk_coeff = -specification.bulk_decay_per_s
    reaction.wall_coeff, reaction.limiting_potential, reaction.roughness_correl = 0.0, None, None
    for name, pipe in model.pipes():
        pipe.bulk_coeff = -specification.pipe_bulk_decay_per_s.get(name, specification.bulk_decay_per_s)
        pipe.wall_coeff = 0.0
    for _, tank in model.tanks():
        tank.bulk_coeff = -specification.bulk_decay_per_s
    clock = _resample_patterns(
        model,
        specification.sources,
        specification.hydraulics.duration_s,
        specification.maximum_pattern_values,
    )
    remaining = specification.maximum_pattern_values
    if clock["resampled"]:
        remaining -= clock["existing_pattern_values"]
    source_bindings = []
    for i, injection in enumerate(specification.sources):
        pattern_name = None
        if injection.pattern is not None:
            step, start = int(model.options.time.pattern_timestep), int(model.options.time.pattern_start)
            active_changes = any(
                0 < t <= specification.hydraulics.duration_s for t in injection.pattern.time_s
            )
            if not active_changes:
                # Constant source profiles do not need a backend pattern or a change to existing demand clocks.
                factors = [injection.pattern.factors[0]]
            else:
                if start:
                    raise ValueError("source event clocks were not aligned")
                factors = [
                    injection.pattern.at(t)
                    for t in range(0, int(specification.hydraulics.duration_s) + 1, step)
                ]
            remaining -= len(factors)
            if remaining < 0:
                raise ValueError("source patterns exceed their declared budget")
            pattern_name = f"PIQ_{digest(specification)[:12]}_{i}"
            original_name, suffix = pattern_name, 0
            while pattern_name in model.pattern_name_list:
                suffix += 1
                pattern_name = f"{original_name}_{suffix}"
            model.add_pattern(pattern_name, factors)
        native_id = f"PIQS_{i}"
        model.add_source(
            native_id, injection.node_id, injection.source_type, injection.strength, pattern_name
        )
        source_bindings.append(
            {
                "source_id": injection.source_id,
                "backend_id": native_id,
                "node_id": injection.node_id,
                "pattern_id": pattern_name,
            }
        )
    model.options.time.quality_timestep = int(specification.quality_timestep_s)
    effective_upper_step = min(
        int(specification.quality_timestep_s),
        model.options.time.hydraulic_timestep,
        model.options.time.report_timestep,
        model.options.time.pattern_timestep,
    )
    if (
        math.ceil(specification.hydraulics.duration_s / effective_upper_step)
        > specification.maximum_nominal_quality_steps
    ):
        raise ValueError("the nominal quality-step estimate exceeds its declared budget")
    native, diagnostics = solve_epanet(
        model,
        quality_statistics=True,
        maximum_hydraulic_periods=specification.hydraulics.maximum_hydraulic_periods,
    )
    hydraulics = _water_results(source, model, native, specification.hydraulics, diagnostics)
    node_values = native.node["quality"].astype(float)
    link_values = native.link["quality"].astype(float)
    if mode == "TRACE":
        node_values, link_values = node_values / 100.0, link_values / 100.0
    all_values = np.concatenate((node_values.to_numpy().ravel(), link_values.to_numpy().ravel()))
    finite = bool(np.isfinite(all_values).all())
    lower_ok = finite and bool((all_values >= -specification.admissible_value_error).all())
    upper_ok = mode != "TRACE" or (
        finite and bool((all_values <= 1 + specification.admissible_value_error).all())
    )
    ratio = diagnostics["mass_balance_ratio"]
    balance_ok = ratio is not None and abs(ratio - 1.0) <= specification.native_balance_ratio_tolerance
    clock_ok = list(node_values.index) == hydraulics["time_s"] and diagnostics["effective_time_parameters"][
        "quality_elapsed_s"
    ] == int(specification.hydraulics.duration_s)
    clock_ok = clock_ok and not diagnostics["effective_time_parameters"]["hydraulic_halt_flag"]
    status = (
        "computed"
        if hydraulics["status"] == "computed"
        and finite
        and lower_ok
        and upper_ok
        and balance_ok
        and clock_ok
        and not diagnostics["warnings"]
        else "withheld"
    )

    def table(frame):
        return {name: [float(v) if math.isfinite(v) else None for v in frame[name]] for name in frame}

    unit = {"concentration": "kg/m3", "age": "s", "source_fraction": "1"}[specification.mode]
    implementation = {
        name: file_digest(Path(__file__).with_name(name))
        for name in ("water_quality.py", "epanet.py", "water.py")
    }
    body = {
        "schema": "pipeline_integrity.water_quality.v1",
        "status": status,
        "source_sha256": file_digest(source),
        "specification": specification.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "parameter_source_id": specification.parameter_source_id,
        "calculation_origin": "simulation",
        "source_bindings": source_bindings,
        "input_quality_reset": original,
        "mode": specification.mode,
        "quantity_unit": unit,
        "quantity_meaning": {
            "concentration": "constituent_mass_per_carrier_volume",
            "age": "mean_water_age",
            "source_fraction": "water_origin_fraction_not_event_probability",
        }[specification.mode],
        "time_s": [specification.start_time_s + t for t in hydraulics["time_s"]],
        "elapsed_time_s": hydraulics["time_s"],
        "hydraulic_history_time_basis": "elapsed_from_quality_start",
        "node_quality": table(node_values),
        "link_quality": table(link_values),
        "hydraulics": hydraulics,
        "native_diagnostics": diagnostics,
        "balance_quantity_meaning": {
            "concentration": "constituent_mass_with_reaction",
            "source_fraction": "water_origin_fraction_times_volume",
            "age": "water_age_times_volume_with_aging_production",
        }[specification.mode],
        "pattern_clock": clock,
        "checks": {
            "finite_quality": finite,
            "lower_bound": lower_ok,
            "upper_bound": upper_ok,
            "native_balance_within_tolerance": balance_ok,
            "clock_complete": clock_ok,
        },
        "backend": {"wntr": version("wntr"), "python": platform.python_version()},
        "implementation_files_sha256": implementation,
        "implementation_sha256": digest(implementation),
        "scope": "single dilute constituent, first-order bulk decay, water age or water-origin fraction; constant-density carrier",
        "thermal_or_product_density_feedback_calculated": False,
        "field_validation_claim": False,
    }
    return {**body, "result_digest": digest(body)}


def water_quality_frames(result, *, node_ids=None, stream_id: str):
    if result.get("schema") != "pipeline_integrity.water_quality.v1" or result.get("status") != "computed":
        raise ValueError("a computed water-quality result is required")
    if digest({k: v for k, v in result.items() if k != "result_digest"}) != result["result_digest"]:
        raise ValueError("the water-quality result changed after calculation")
    chosen = tuple(result["node_quality"] if node_ids is None else node_ids)
    if (
        not chosen
        or len(set(chosen)) != len(chosen)
        or any(name not in result["node_quality"] for name in chosen)
    ):
        raise ValueError("select distinct existing quality node identifiers")
    for index, time_s in enumerate(result["time_s"]):
        values = {f"{result['mode']}:{name}": result["node_quality"][name][index] for name in chosen}
        yield SignalFrame(
            stream_id=stream_id,
            clock_id=result["context"]["clock_id"],
            time_s=time_s,
            values=values,
            units={name: result["quantity_unit"] for name in values},
            valid={name: value is not None for name, value in values.items()},
            origin="simulation",
        )


def water_quality_batch(result, *, node_ids=None, stream_id=None, factory=None):
    from .adapters import monitoring_batch

    stream_id = (
        stream_id or f"{result['context']['asset_id']}:{result['mode']}:{result['context']['scenario_id']}"
    )
    return monitoring_batch(
        water_quality_frames(result, node_ids=node_ids, stream_id=stream_id),
        factory=factory,
        metadata={
            "source_result_digest": result["result_digest"],
            "asset_id": result["context"]["asset_id"],
            "scenario_id": result["context"]["scenario_id"],
            "quantity_meaning": result["quantity_meaning"],
            "input_context_origin": result["context"]["origin"],
            "parameter_source_id": result["parameter_source_id"],
        },
    )


def quality_threshold_arrival(result, node_id: str, threshold: float):
    """Return a sampled crossing bracket, without interpreting a source fraction as a probability."""
    frames = tuple(water_quality_frames(result, node_ids=(node_id,), stream_id="arrival_query"))
    if isinstance(threshold, bool) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("the threshold must be finite, nonnegative and in the result's declared unit")
    if result["mode"] == "source_fraction" and threshold > 1:
        raise ValueError("a source-fraction threshold must be between zero and one")
    values = [next(iter(frame.values.values())) for frame in frames]
    for i, value in enumerate(values):
        if value is not None and value >= threshold:
            return {
                "status": "reached",
                "threshold": threshold,
                "unit": result["quantity_unit"],
                "sample_bracket_s": [frames[max(0, i - 1)].time_s, frames[i].time_s],
                "already_at_or_above_at_start": i == 0,
                "probability_calculated": False,
            }
    return {
        "status": "not_reached_in_reported_horizon",
        "threshold": threshold,
        "unit": result["quantity_unit"],
        "last_report_time_s": frames[-1].time_s,
        "probability_calculated": False,
    }
