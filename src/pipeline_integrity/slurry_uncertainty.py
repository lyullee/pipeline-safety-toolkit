"""Connect reviewed slurry snapshots to the shared uncertainty ensemble engine."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from . import slurry_transport
from .contracts import RunContext, digest
from .datasets import file_digest
from .uncertainty import EnsembleMetric, EnsembleNode, EnsembleValues, UncertainVariable

_INPUT_UNITS = {
    "volume_flow_m3_s": "m^3/s", "length_m": "m", "inner_diameter_m": "m",
    "roughness_m": "m", "minor_loss_coefficient": "1",
    "outlet_minus_inlet_elevation_m": "m", "inlet_pressure_pa_abs": "Pa",
    "gravity_m_s2": "m/s^2", "properties.mixture_density_kg_m3": "kg/m^3",
    "properties.temperature_K": "K", "properties.yield_stress_Pa": "Pa",
    "properties.maximum_shear_rate_s_inv": "1/s", "properties.maximum_shear_stress_Pa": "Pa",
}

_OUTPUT_UNITS = {
    "outlet_pressure_pa_abs": "Pa", "required_inlet_minus_outlet_Pa": "Pa",
    "signed_friction_pressure_drop_Pa": "Pa", "static_pressure_change_Pa": "Pa",
    "wall_shear_stress_Pa": "Pa", "wall_shear_rate_s_inv": "1/s",
    "mass_flow_kg_s": "kg/s", "solid_volume_flow_m3_s": "m^3/s",
    "carrier_volume_flow_m3_s": "m^3/s", "velocity_m_s": "m/s",
    "pressure_work_rate_W": "W", "potential_energy_rate_W": "W",
    "dissipated_power_W": "W", "pressure_work_balance_residual_W": "W",
}


def slurry_transport_node(request, *, id, variables, bindings, metrics):
    """Prepare an EnsembleNode around the existing slurry evaluator.

    Bindings are exact-unit allowlisted paths. No uncertainty generator,
    correlation, or slurry solver is duplicated here.
    """
    from .slurry_transport import SlurryTransportRequest

    base = SlurryTransportRequest.model_validate_json(request.model_dump_json())
    variable_by_id = {v.id: v for v in variables}
    if not id.strip() or len(variable_by_id) != len(variables):
        raise ValueError("node ID and unique declared variables are required")
    if not bindings or len(set(bindings.values())) != len(bindings):
        raise ValueError("each ensemble variable must bind to a distinct request path")
    for variable_id, path in bindings.items():
        if variable_id not in variable_by_id or path not in _INPUT_UNITS:
            raise ValueError("binding must use a declared variable and an allowlisted slurry field")
        if variable_by_id[variable_id].unit != _INPUT_UNITS[path]:
            raise ValueError(f"unit mismatch for {variable_id} -> {path}")
    metric_ids = {m.id for m in metrics}
    if not metrics or len(metric_ids) != len(metrics):
        raise ValueError("unique declared output metrics are required")
    if any(m.id not in _OUTPUT_UNITS or m.unit != _OUTPUT_UNITS[m.id] for m in metrics):
        raise ValueError("metric ID and unit must match the slurry output contract")

    configuration = {
        "base_request": base.model_dump(mode="json"),
        "bindings": dict(bindings),
        "variables": [v.model_dump(mode="json") for v in variables],
        "metrics": [m.model_dump(mode="json") for m in metrics],
    }
    frozen = copy.deepcopy(configuration)

    def evaluate(values, context):
        if digest(configuration) != digest(frozen):
            raise ValueError("prepared slurry ensemble configuration was modified")
        raw = copy.deepcopy(frozen["base_request"])
        unresolved = []
        for variable_id, path in frozen["bindings"].items():
            value = values[variable_id]
            if value is None:
                unresolved.append(variable_id)
            elif path.startswith("properties."):
                raw["properties"][path.partition(".")[2]] = value
            else:
                raw[path] = value
        units = {m.id: m.unit for m in metrics}
        if unresolved:
            return EnsembleValues(
                context=context, values=dict.fromkeys(units), units=units,
                statuses={k: "needs_data" for k in units},
                native_payload={"issue": "unresolved bound draw value", "unresolved_variable_ids": unresolved,
                                "base_request_digest": digest(frozen["base_request"]), "draw_values": values},
            )

        draw_request = SlurryTransportRequest.model_validate_json(json.dumps(raw,allow_nan=False))
        draw_metadata = dict(context.metadata)
        draw_metadata["slurry_transport_boundary"] = slurry_transport.boundary_metadata(draw_request)
        evaluation_context = RunContext.model_validate(
            {**context.model_dump(mode="python"), "metadata": draw_metadata}
        )
        record = slurry_transport.evaluate_slurry_transport(draw_request, evaluation_context)
        computed = record["status"] == "computed_diagnostic"
        result_values = record.get("values") or {}
        output_values = {m.id: result_values.get(m.id) if computed else None for m in metrics}
        statuses = {
            m.id: "computed" if computed and output_values[m.id] is not None
            else "needs_data" if record["status"] == "needs_data" or (computed and output_values[m.id] is None)
            else "out_of_scope"
            for m in metrics
        }
        return EnsembleValues(
            context=context, values=output_values, units=units, statuses=statuses,
            native_payload={"slurry_record": record,
                            "evaluation_context": evaluation_context.model_dump(mode="json"),
                            "binding_values": values},
        )

    source_digest = digest({"adapter_sha256": file_digest(Path(__file__)),
                            "slurry_engine_sha256": file_digest(Path(slurry_transport.__file__))})
    return EnsembleNode(id=id, version="1", source_digest=source_digest,
                        configuration=configuration, metrics=metrics, evaluate=evaluate)
