"""A selected diameter root around the existing fixed-flow gas path.

Heat loads and wall conductances in the template stay fixed while diameter
changes. A caller needing a diameter-dependent external heat model must first
provide that model; this API does not silently reinterpret total conductance.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest
from .datasets import file_digest
from .gas_potential_network import GasPotentialForwardRequest, evaluate_gas_potential_path


class GasPathDiameterRequest(Contract):
    id: Name
    transport: GasPotentialForwardRequest
    target_outlet_pressure_Pa_abs: Positive
    diameter_bracket_m: tuple[Positive, Positive]
    absolute_diameter_tolerance_m: Positive = 1e-9
    absolute_pressure_tolerance_Pa: Positive = 0.01
    maximum_evaluations: int = Field(default=60, ge=5, le=1000)
    maximum_segment_evaluations: int = Field(default=100000, ge=1, le=1000000)
    selected_bracket_and_fixed_total_heat_boundary_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        lo, hi = self.diameter_bracket_m
        if lo >= hi:
            raise ValueError("diameter bracket must increase")
        if not lo <= self.transport.diameter_m <= hi:
            raise ValueError("template diameter is outside the selected bracket")
        if any(s.roughness_m / lo > 0.1 for s in self.transport.segments):
            raise ValueError("diameter bracket exceeds reviewed relative roughness")
        pmin, pmax = self.transport.pressure_support_Pa_abs
        if not pmin <= self.target_outlet_pressure_Pa_abs <= pmax:
            raise ValueError("target pressure is outside the transport support")
        if len(self.transport.segments) * self.maximum_evaluations > self.maximum_segment_evaluations:
            raise ValueError("diameter search exceeds the declared segment-evaluation budget")
        return self


class _WithheldTrial(Exception):
    pass


def evaluate_gas_path_diameter(request: GasPathDiameterRequest, context: RunContext, *, property_provider=None):
    """Find one supported pressure root; do not infer global uniqueness."""
    from scipy.optimize import brentq

    q = GasPathDiameterRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if not q.selected_bracket_and_fixed_total_heat_boundary_reviewed:
        raise ValueError("diameter bracket and fixed total heat boundary require review")
    if (q.transport.asset_id, q.transport.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("diameter search asset/clock differs from context")
    trials = []
    final = None
    root = None
    issue = None

    def trial(diameter, *, retain=False):
        nonlocal final
        if len(trials) >= q.maximum_evaluations:
            raise _WithheldTrial("diameter-search evaluation budget exhausted")
        payload = q.transport.model_dump(mode="json")
        payload["diameter_m"] = float(diameter)
        transport = GasPotentialForwardRequest.model_validate_json(json.dumps(payload))
        result = evaluate_gas_potential_path(transport, c, property_provider=property_provider)
        entry = {"diameter_m": float(diameter), "status": result["status"],
                 "transport_record_digest": result["record_digest"]}
        trials.append(entry)
        if result["status"] != "computed_diagnostic":
            raise _WithheldTrial("gas path withheld a diameter trial: " + str(result.get("failure")))
        residual = result["states"][-1]["pressure_pa_abs"] - q.target_outlet_pressure_Pa_abs
        if not math.isfinite(residual):
            raise _WithheldTrial("diameter trial returned a nonfinite pressure residual")
        entry["outlet_pressure_residual_Pa"] = residual
        if retain:
            final = result
        return residual

    try:
        lo, hi = q.diameter_bracket_m
        flo, fhi = trial(lo), trial(hi)
        if abs(flo) <= q.absolute_pressure_tolerance_Pa:
            root = lo
        elif abs(fhi) <= q.absolute_pressure_tolerance_Pa:
            root = hi
        elif (flo > 0) == (fhi > 0):
            raise _WithheldTrial("selected diameter bracket does not enclose a pressure root")
        else:
            root = float(brentq(trial, lo, hi, xtol=q.absolute_diameter_tolerance_m,
                                rtol=1e-12, maxiter=q.maximum_evaluations - 3))
        residual = trial(root, retain=True)
        if abs(residual) > q.absolute_pressure_tolerance_Pa:
            raise _WithheldTrial("diameter root fails the declared pressure residual tolerance")
    except (_WithheldTrial, ValueError, RuntimeError) as error:
        issue = str(error)
        final = None
    body = dict(schema="pipeline_integrity.gas_path_diameter.v1",
                request=q.model_dump(mode="json"), context=c.model_dump(mode="json"),
                status="computed_diagnostic" if final is not None else "withheld",
                diameter_m=root if final is not None else None, transport_result=final,
                trials=trials, issue=issue, global_uniqueness_proved=False,
                heat_boundary_meaning="template total watts and watts per kelvin remain fixed",
                implementation_sha256=file_digest(Path(__file__)),
                gas_path_sha256=file_digest(Path(__file__).with_name("gas_potential_network.py")))
    body["record_digest"] = digest(body)
    return body


def verify_gas_path_diameter_record(record, *, request=None, context=None, property_provider=None):
    if digest({k: v for k, v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("gas diameter receipt changed")
    q = GasPathDiameterRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("gas diameter caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("gas diameter caller context differs")
    if evaluate_gas_path_diameter(q, c, property_provider=property_provider) != record:
        raise ValueError("gas diameter replay differs")
    return True
