"""Pressure-boundary operating points around the existing heated-liquid march.

Only one caller-selected mass-flow bracket is searched. The original model's
diagnostic closure and property support remain visible in every accepted root.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .heated_transport import HeatedTransportRequest, evaluate_heated_transport, boundary_metadata, native_backend_metadata
from .models.pump_curves import ReferencePumpCurve


class HeatedOperatingPointRequest(Contract):
    id: Name
    transport: HeatedTransportRequest
    downstream_pressure_pa_abs: Positive
    mass_flow_bracket_kg_s: tuple[Positive,Positive]
    mass_flow_absolute_tolerance_kg_s: Positive = 1e-9
    pressure_absolute_tolerance_Pa: Positive = 0.1
    pressure_relative_tolerance: Nonnegative = 1e-8
    maximum_evaluations: int = Field(default=80,ge=5,le=1000)
    maximum_cell_evaluations: int = Field(default=100000,ge=1,le=1000000)
    mapping_review_evidence_id: Name
    diagnostic_model_and_selected_bracket_reviewed: bool = False

    @model_validator(mode="after")
    def bracket(self):
        if self.mass_flow_bracket_kg_s[0]>=self.mass_flow_bracket_kg_s[1]:
            raise ValueError("heated operating-point mass-flow bracket must increase")
        if len(self.transport.cells)*self.maximum_evaluations>self.maximum_cell_evaluations:
            raise ValueError("heated operating point exceeds its declared cell-evaluation budget")
        return self


class _WithheldTrial(Exception):
    pass


class HeatedPumpOperatingPointRequest(HeatedOperatingPointRequest):
    """Direct API for a pump curve and a prescribed pipe-inlet temperature.

    The transport template pins the reviewed provider and reference state.
    Each trial derives pipe-inlet pressure from the separate upstream pressure
    and the supplied pump curve; no pump thermal-efficiency model is inferred.
    """
    pump_curve: ReferencePumpCurve
    upstream_pressure_pa_abs: Positive
    curve_reference_density_kg_m3: Positive
    maximum_relative_density_deviation: Positive = Field(default=1e-6,le=0.01)
    constant_density_curve_mapping_reviewed: bool = False

    @model_validator(mode="after")
    def pump_mapping(self):
        if not self.constant_density_curve_mapping_reviewed:
            raise ValueError("constant-density pump/head mapping requires review")
        if set(self.transport.inlet.inputs)!={"P","T"}:
            raise ValueError("pump operating point requires a prescribed pipe-inlet temperature")
        flows=[m/self.curve_reference_density_kg_m3 for m in self.mass_flow_bracket_kg_s]
        if flows[0]<self.pump_curve.volume_flow_m3_s[0] or flows[1]>self.pump_curve.volume_flow_m3_s[-1]:
            raise ValueError("mass-flow bracket exceeds the supplied pump curve; no extrapolation")
        return self


def evaluate_heated_operating_point(request: HeatedOperatingPointRequest, context: RunContext, *, property_provider=None):
    from scipy.optimize import brentq
    request_type=HeatedPumpOperatingPointRequest if isinstance(request,HeatedPumpOperatingPointRequest) else HeatedOperatingPointRequest
    q=request_type.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.diagnostic_model_and_selected_bracket_reviewed:
        raise ValueError("diagnostic model applicability and flow bracket require review")
    evidence=[e for e in c.evidence if e.id==q.mapping_review_evidence_id]
    if len(evidence)!=1 or evidence[0].sha256 is None:
        raise ValueError("heated pressure/flow boundary mapping needs source-hash-pinned context evidence")
    if (q.transport.asset_id,q.transport.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("heated operating-point asset/clock differs")
    trials=[]
    final=None
    tolerance=q.pressure_absolute_tolerance_Pa+q.pressure_relative_tolerance*q.downstream_pressure_pa_abs
    curve=None
    reference_pins=None
    if isinstance(q,HeatedPumpOperatingPointRequest):
        from scipy.interpolate import PchipInterpolator
        reference_pins=native_backend_metadata(q.transport,property_provider=property_provider)
        if reference_pins!=q.transport.owner_pins:
            raise ValueError("reviewed pump transport template pins differ")
        curve=PchipInterpolator(q.pump_curve.volume_flow_m3_s,q.pump_curve.head_m,extrapolate=False)

    def trial(mass_flow, retain=False):
        nonlocal final
        if len(trials)>=q.maximum_evaluations:
            raise _WithheldTrial("heated operating-point trial budget exhausted")
        payload=q.transport.model_dump(mode="json")
        payload["mass_flow_kg_s"]=mass_flow
        pump_head=None
        if curve is not None:
            pump_head=float(curve(mass_flow/q.curve_reference_density_kg_m3))
            payload["inlet"]["inputs"]["P"]=q.upstream_pressure_pa_abs+q.curve_reference_density_kg_m3*q.transport.gravity_m_s2*pump_head
        transport=HeatedTransportRequest.model_validate_json(json.dumps(payload))
        if curve is not None:
            pins=native_backend_metadata(transport,property_provider=property_provider)
            if {k:v for k,v in pins.items() if k!="inlet_reference_marker"}!={k:v for k,v in reference_pins.items() if k!="inlet_reference_marker"}:
                raise _WithheldTrial("pump trial changed a reviewed property/model implementation")
            transport=transport.model_copy(update={"owner_pins":pins})
        context_payload=c.model_dump(mode="json")
        context_payload["metadata"]["heated_transport_boundary"]=boundary_metadata(transport)
        derived_context=RunContext.model_validate_json(json.dumps(context_payload))
        result=evaluate_heated_transport(transport,derived_context,property_provider=property_provider)
        entry=dict(mass_flow_kg_s=mass_flow,status=result["status"],receipt_digest=result["record_digest"])
        if curve is not None:
            entry.update(pump_head_m=pump_head,pipe_inlet_pressure_pa_abs=transport.inlet.inputs["P"])
        trials.append(entry)
        if result["status"]!="computed_diagnostic" or result.get("values") is None:
            raise _WithheldTrial("heated transport withheld a pressure/flow trial")
        if curve is not None:
            densities=[row[side]["density_kg_m3"] for row in result["cells"] for side in ("inlet","outlet")]
            if any(abs(rho/q.curve_reference_density_kg_m3-1)>q.maximum_relative_density_deviation for rho in densities):
                raise _WithheldTrial("resolved liquid density exceeds the reviewed constant-density pump mapping")
        residual=result["values"]["outlet"]["pressure_pa_abs"]-q.downstream_pressure_pa_abs
        if not math.isfinite(residual):
            raise _WithheldTrial("heated pressure residual is not finite")
        entry["pressure_residual_Pa"]=residual
        if retain:
            final=result
        return residual

    root=None
    reason=None
    try:
        lower,upper=q.mass_flow_bracket_kg_s
        fl,fu=trial(lower),trial(upper)
        if abs(fl)<=tolerance:
            root=lower
        elif abs(fu)<=tolerance:
            root=upper
        elif fl*fu>0:
            raise _WithheldTrial("selected heated mass-flow bracket does not enclose a pressure residual root")
        else:
            root=float(brentq(trial,lower,upper,xtol=q.mass_flow_absolute_tolerance_kg_s,rtol=1e-12,
                              maxiter=q.maximum_evaluations-3))
        residual=trial(root,retain=True)
        if abs(residual)>tolerance:
            raise _WithheldTrial("heated operating-point root fails the declared pressure residual tolerance")
    except (_WithheldTrial,ValueError,RuntimeError) as error:
        reason=str(error)
        final=None
    body=dict(schema="pipeline_integrity.heated_operating_point.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),trials=trials,
              status="computed_diagnostic" if final is not None else "withheld",
              mass_flow_kg_s=root if final is not None else None,transport_result=final,
              pressure_residual_tolerance_Pa=tolerance,issue=reason,
              global_uniqueness_proved=False,numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),
              heated_owner_sha256=file_digest(Path(__file__).with_name("heated_transport.py")))
    if curve is not None:
        body["pump_mapping"]={"upstream_pressure_pa_abs":q.upstream_pressure_pa_abs,
            "reference_density_kg_m3":q.curve_reference_density_kg_m3,
            "pipe_inlet_temperature_meaning":"prescribed independently of pump thermal efficiency",
            "hydraulic_power_W":None if final is None else (root/q.curve_reference_density_kg_m3)*(
                final["request"]["inlet"]["inputs"]["P"]-q.upstream_pressure_pa_abs)}
    body["record_digest"]=digest(body)
    return body


def verify_heated_operating_point_record(record, *, request=None, context=None, property_provider=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("heated operating-point receipt changed")
    request_type=HeatedPumpOperatingPointRequest if "pump_curve" in record["request"] else HeatedOperatingPointRequest
    q=request_type.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("heated operating-point caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("heated operating-point caller context differs")
    if evaluate_heated_operating_point(q,c,property_provider=property_provider)!=record:
        raise ValueError("heated operating-point replay differs")
    return True
