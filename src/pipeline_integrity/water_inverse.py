"""Bind the common inverse API to fresh, explicitly parameterized EPANET network runs."""

import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative, digest
from .datasets import file_digest
from .epanet import solve_epanet
from .inverse import Prediction
from .water import WaterRun, _configure_hydraulics, _water_results


class WaterSensor(Contract):
    channel_id: Name
    element_id: Name
    quantity: Literal["pressure_head", "volume_flow", "water_level"]
    flow_sign: Literal[-1, 1] = 1

    @model_validator(mode="after")
    def direction(self):
        if self.quantity != "volume_flow" and self.flow_sign != 1:
            raise ValueError("only a flow sensor can reverse the declared link direction")
        return self

    @property
    def unit(self):
        return "m3/s" if self.quantity == "volume_flow" else "m"


class WaterParameter(Contract):
    parameter_id: Name
    kind: Literal[
        "demand_multiplier",
        "junction_demand_multiplier",
        "pipe_roughness_multiplier",
        "emitter_coefficient",
        "reservoir_head_offset",
        "initial_tank_level_offset",
    ]
    element_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def selection(self):
        if len(set(self.element_ids)) != len(self.element_ids):
            raise ValueError("parameter element identifiers must be distinct")
        if (self.kind == "demand_multiplier") != (not self.element_ids):
            raise ValueError(
                "a global demand multiplier needs no targets; other bindings need explicit targets"
            )
        return self

    @property
    def unit(self):
        if self.kind.endswith("offset"):
            return "m"
        return "m3/s/m^0.5" if self.kind == "emitter_coefficient" else "1"


class WaterForward:
    def __init__(
        self,
        network_path,
        *,
        specification: WaterRun,
        sensors: tuple[WaterSensor, ...],
        parameters: tuple[WaterParameter, ...],
        start_time_s=0.0,
    ):
        import wntr

        self.path = Path(network_path).resolve()
        self.specification = specification
        self.sensors, self.parameters = sensors, parameters
        self.start_time_s = NonnegativeStart(value=start_time_s).value
        if self.path.suffix.lower() != ".inp" or not self.path.is_file():
            raise ValueError("a saved EPANET network is required")
        if not sensors or len({s.channel_id for s in sensors}) != len(sensors):
            raise ValueError("distinct water sensor channels are required")
        if not parameters or len({p.parameter_id for p in parameters}) != len(parameters):
            raise ValueError("distinct water parameter bindings are required")
        model = wntr.network.WaterNetworkModel(str(self.path))
        _configure_hydraulics(model, specification)
        if model.options.hydraulic.hydraulics is not None:
            raise ValueError("external hydraulic caches are not supported by this inverse provider")
        for s in sensors:
            allowed = (
                model.link_name_list
                if s.quantity == "volume_flow"
                else model.tank_name_list
                if s.quantity == "water_level"
                else model.junction_name_list
            )
            if s.element_id not in allowed:
                raise ValueError("sensor refers to an absent or incompatible network element")
        occupied = set()
        for p in parameters:
            allowed = {
                "junction_demand_multiplier": model.junction_name_list,
                "pipe_roughness_multiplier": model.pipe_name_list,
                "emitter_coefficient": model.junction_name_list,
                "reservoir_head_offset": model.reservoir_name_list,
                "initial_tank_level_offset": model.tank_name_list,
            }.get(p.kind, ())
            if any(name not in allowed for name in p.element_ids):
                raise ValueError("parameter binding refers to an absent or incompatible element")
            if p.kind == "emitter_coefficient" and model.options.hydraulic.emitter_exponent != 0.5:
                raise ValueError(
                    "emitter bindings currently require the explicit square-root pressure-head law"
                )
            for name in p.element_ids or ("__global__",):
                if (p.kind, name) in occupied:
                    raise ValueError("two parameters cannot modify the same network property")
                occupied.add((p.kind, name))
        self.network_sha256 = file_digest(self.path)
        self.provider_source_digest = digest(
            {
                "network": self.network_sha256,
                "specification": specification,
                "sensors": sensors,
                "bindings": parameters,
                "start_time_s": self.start_time_s,
                "wntr": version("wntr"),
                "code": {
                    name: file_digest(Path(__file__).with_name(name))
                    for name in ("water_inverse.py", "water.py", "epanet.py")
                },
            }
        )

    @property
    def parameter_units(self):
        return {p.parameter_id: p.unit for p in self.parameters}

    def __call__(self, values, time_s, context):
        import wntr

        if set(values) != set(self.parameter_units):
            raise ValueError("forward values must identify every declared water parameter exactly")
        if any(isinstance(v, bool) or not math.isfinite(v) for v in values.values()):
            raise ValueError("forward parameter values must be finite numbers")
        if file_digest(self.path) != self.network_sha256:
            raise ValueError("the network source changed after provider construction")
        elapsed = tuple(t - self.start_time_s for t in time_s)
        if any(b <= a for a, b in zip(elapsed, elapsed[1:], strict=False)):
            raise ValueError("forward report times must strictly increase")
        if not elapsed or any(
            t < 0 or t != int(t) or t % self.specification.report_timestep_s for t in elapsed
        ):
            raise ValueError("inverse times must lie exactly on the declared network report grid")
        if max(elapsed) > self.specification.duration_s:
            raise ValueError("requested inverse times exceed the declared forward horizon")
        model = wntr.network.WaterNetworkModel(str(self.path))
        run = self.specification.model_copy(update={"duration_s": max(elapsed)})
        _configure_hydraulics(model, run)
        for p in self.parameters:
            value = values[p.parameter_id]
            if (
                p.kind in {"demand_multiplier", "junction_demand_multiplier", "pipe_roughness_multiplier"}
                and value <= 0
            ):
                raise ValueError("water parameter multipliers must remain positive")
            if p.kind == "demand_multiplier":
                model.options.hydraulic.demand_multiplier *= value
            for name in p.element_ids:
                if p.kind == "pipe_roughness_multiplier":
                    model.get_link(name).roughness *= value
                else:
                    node = model.get_node(name)
                    if p.kind == "junction_demand_multiplier":
                        for demand in node.demand_timeseries_list:
                            demand.base_value *= value
                    elif p.kind == "emitter_coefficient":
                        if value < 0:
                            raise ValueError("an emitter coefficient cannot be negative")
                        node.emitter_coefficient = value
                    elif p.kind == "reservoir_head_offset":
                        node.base_head += value
                    elif p.kind == "initial_tank_level_offset":
                        node.init_level += value
                        if not node.min_level <= node.init_level <= node.max_level:
                            raise ValueError("the adjusted initial tank level lies outside its limits")
        model.options.quality.parameter = "NONE"
        native, diagnostics = solve_epanet(model, maximum_hydraulic_periods=run.maximum_hydraulic_periods)
        result = _water_results(self.path, model, native, run, diagnostics)
        position = {t: i for i, t in enumerate(result["time_s"])}
        if any(t not in position for t in elapsed):
            raise ValueError("the native solver omitted requested observation times")
        output = {
            s.channel_id: tuple(
                (
                    None
                    if (
                        v := result["flow_m3_s" if s.quantity == "volume_flow" else "pressure_head_m"][
                            s.element_id
                        ][position[t]]
                    )
                    is None
                    else v * s.flow_sign
                )
                for t in elapsed
            )
            for s in self.sensors
        }
        detail = {
            "warning_count": diagnostics["warning_count"],
            "maximum_native_hydraulic_statistics": diagnostics["maximum_native_hydraulic_statistics"],
            "effective_hydraulic_options_si": diagnostics["effective_hydraulic_options_si"],
            "continuity_residual_m3_s": result["maximum_junction_continuity_residual_m3_s"],
            "native_library_sha256": diagnostics["native_library_sha256"],
        }
        return Prediction(
            status=result["status"],
            asset_id=context.asset_id,
            clock_id=context.clock_id,
            time_s=time_s,
            values=output,
            units={s.channel_id: s.unit for s in self.sensors},
            provider_source_digest=self.provider_source_digest,
            evaluation_digest=digest(
                {
                    "context": context,
                    "values": values,
                    "time_s": time_s,
                    "prediction": output,
                    "native": diagnostics,
                    "source": self.provider_source_digest,
                }
            ),
            diagnostics=detail,
        )


class NonnegativeStart(Contract):
    value: Nonnegative
