"""Uniform mesh refinement around the original steady heated-liquid march.

This wrapper compares successive original-owner results. It introduces no new
friction, fluid-property or heat-transfer formula. Agreement between meshes is
a diagnostic, not a rigorous discretization bound or independent validation.
Automatic refinement is limited to cells without unlocated minor losses.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from . import heated_transport as owner
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .heated_transport import HeatedTransportCell, HeatedTransportRequest

REVIEWS = (
    "uniform_geometry_roughness_and_linear_elevation_within_each_original_cell",
    "uniform_distributed_conductance_and_heater_power",
    "constant_ambient_within_each_original_cell",
    "unlocated_minor_losses_absent",
    "single_phase_and_property_support_preserved_under_refinement",
    "successive_mesh_difference_is_diagnostic_not_error_bound",
    "source_clock_ports_and_refinement_mapping",
)


class AdaptiveHeatedTransportRequest(Contract):
    id: Name
    base: HeatedTransportRequest
    initial_subdivisions_per_cell: int = Field(default=1, ge=1, le=4096)
    refinement_factor: int = Field(default=2, ge=2, le=16)
    maximum_levels: int = Field(default=8, ge=2, le=12)
    maximum_refined_cells: int = Field(default=4096, ge=2, le=4096)
    maximum_total_cell_evaluations: int = Field(default=32768, ge=3, le=1000000)
    maximum_total_owner_api_work_units: int = Field(default=100000, ge=1, le=4000000)
    maximum_retained_result_bytes: int = Field(default=256000000, ge=1000, le=1000000000)
    required_consecutive_agreements: int = Field(default=1, ge=1, le=4)
    outlet_pressure_absolute_tolerance_Pa: Positive
    outlet_pressure_relative_tolerance: Nonnegative = Field(default=1e-6, le=1e-2)
    outlet_temperature_absolute_tolerance_K: Positive
    outlet_temperature_relative_tolerance: Nonnegative = Field(default=1e-6, le=1e-2)
    total_heat_absolute_tolerance_W: Positive
    total_heat_relative_tolerance: Nonnegative = Field(default=1e-6, le=1e-2)
    refinement_mapping_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    refinement_owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def campaign(self):
        first=len(self.base.cells)*self.initial_subdivisions_per_cell
        minimum_counts=[first*self.refinement_factor**i for i in range(self.required_consecutive_agreements+1)]
        if max(minimum_counts)>self.maximum_refined_cells or sum(minimum_counts)>self.maximum_total_cell_evaluations:
            raise ValueError("the required consecutive mesh comparisons cannot fit the cell/evaluation budgets")
        if 3*sum(minimum_counts)+3*len(minimum_counts)>self.maximum_total_owner_api_work_units:
            raise ValueError("the required mesh comparisons cannot fit the declared owner API work budget")
        if self.maximum_levels<self.required_consecutive_agreements+1:
            raise ValueError("the level budget cannot contain the required consecutive comparisons")
        if set(self.reviews)!=set(REVIEWS) or set(self.review_evidence_ids)-set(REVIEWS):
            raise ValueError("all named mesh applicability reviews are required")
        if len(set(self.evidence_ids))!=len(self.evidence_ids):
            raise ValueError("refinement evidence IDs must be unique")
        return self


def adaptive_heated_transport_metadata():
    return dict(implementation_sha256=file_digest(Path(__file__)),
                heated_transport_source_sha256=file_digest(Path(owner.__file__)),
                shared_source_sha256={name:file_digest(Path(__file__).with_name(name+".py"))
                                      for name in ("contracts","datasets")},
                refinement="uniform_integer_subdivision_with_preserved_integrated_cell_inputs")


def adaptive_heated_transport_boundary(request):
    q=request
    return dict(asset_id=q.base.asset_id,event_id=q.base.event_id,clock_id=q.base.clock_id,
                time_s=q.base.time_s,inlet_port_id=q.base.inlet_port_id,outlet_port_id=q.base.outlet_port_id,
                request_digest=digest(q),base_request_digest=digest(q.base),
                refinement_mapping_evidence_id=q.refinement_mapping_evidence_id)


def bind_adaptive_heated_transport_context(request: AdaptiveHeatedTransportRequest,context: RunContext):
    q=AdaptiveHeatedTransportRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    payload=c.model_dump(mode="json")
    payload["metadata"]={**c.metadata,"adaptive_heated_transport_boundary":adaptive_heated_transport_boundary(q)}
    result=RunContext.model_validate_json(json.dumps(payload))
    _policy(q,result)
    return result


def _base_context(q,c):
    payload=c.model_dump(mode="json")
    payload["metadata"]={**c.metadata,"heated_transport_boundary":owner.boundary_metadata(q.base)}
    return RunContext.model_validate_json(json.dumps(payload))


def _policy(q,c):
    if (q.base.asset_id,q.base.event_id,q.base.clock_id)!=(c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("adaptive heated transport asset/event/clock differs from context")
    if c.metadata.get("adaptive_heated_transport_boundary")!=adaptive_heated_transport_boundary(q):
        raise ValueError("refinement context must bind the exact parent request and mapping")
    available={e.id:e for e in c.evidence}
    selected=set(q.evidence_ids)
    required={q.refinement_mapping_evidence_id,*q.review_evidence_ids.values()}
    if len(available)!=len(c.evidence) or not selected<=set(available) or not required<=selected:
        raise ValueError("refinement geometry/heat/mapping reviews require actual selected context evidence")
    mapping=available[q.refinement_mapping_evidence_id]
    if mapping.sha256 is None or mapping.origin=="synthetic":
        raise ValueError("uniform subdivision requires reviewed source-hash-pinned mapping evidence")
    issues=["review/evidence required: "+key for key in REVIEWS
            if not q.reviews[key] or key not in q.review_evidence_ids]
    issues.extend("base owner: "+note for note in owner._policy(q.base,_base_context(q,c)))
    if not q.refinement_owner_pins:
        issues.append("reviewed refinement source pins are required")
    outside=["unlocated minor loss cannot be automatically subdivided: "+cell.id
             for cell in q.base.cells if cell.minor_loss_coefficient!=0]
    return issues+outside,"out_of_scope" if outside else "needs_data"


def _parts(value,count):
    part=value/count
    if value!=0 and part==0:
        raise ValueError("uniform source subdivision underflows; no endpoint concentration is substituted")
    values=[part]*(count-1)
    values.append(value-math.fsum(values))
    if not all(math.isfinite(v) for v in values):
        raise ValueError("refined cell integral exceeds finite arithmetic")
    return values


def _conservation(source,parts):
    total=math.fsum(parts)
    residual=total-source
    allowance=4*max(math.ulp(source),math.ulp(total))
    if abs(residual)>allowance:
        raise ValueError("uniform cell subdivision failed its floating-point integral ledger")
    return dict(source=source,refined_sum=total,residual=residual,floating_point_allowance=allowance)


def _refined_request(q,c,level,subdivisions):
    cells=[]
    ledger=[]
    for original_index,original in enumerate(q.base.cells):
        if original.minor_loss_coefficient!=0:
            raise ValueError("automatic refinement has no location for a nonzero local loss")
        lengths=_parts(original.length_m,subdivisions)
        conductances=_parts(original.conductance_W_K,subdivisions)
        heaters=_parts(original.heater_into_fluid_W,subdivisions)
        elevations=_parts(original.outlet_minus_inlet_elevation_m,subdivisions)
        identifiers=[]
        for part in range(subdivisions):
            data=original.model_dump(mode="json")
            data.update(id=f"{q.id}:mesh:{level}:base:{original_index}:part:{part}",
                        length_m=lengths[part],conductance_W_K=conductances[part],
                        heater_into_fluid_W=heaters[part],outlet_minus_inlet_elevation_m=elevations[part])
            cell=HeatedTransportCell.model_validate_json(json.dumps(data,allow_nan=False))
            identifiers.append(cell.id)
            cells.append(cell)
        ledger.append(dict(original_cell_id=original.id,subdivisions=subdivisions,refined_cell_ids=identifiers,
            preserved_inner_diameter_m=original.inner_diameter_m,preserved_roughness_m=original.roughness_m,
            preserved_ambient_temperature_K=original.ambient_temperature_K,minor_loss_coefficient=0.0,
            length_m=_conservation(original.length_m,lengths),
            conductance_W_K=_conservation(original.conductance_W_K,conductances),
            distributed_heater_W=_conservation(original.heater_into_fluid_W,heaters),
            elevation_change_m=_conservation(original.outlet_minus_inlet_elevation_m,elevations)))
    payload=q.base.model_dump(mode="json")
    payload["cells"]=[cell.model_dump(mode="json") for cell in cells]
    payload["evidence_ids"]=list(dict.fromkeys((*q.base.evidence_ids,*q.evidence_ids)))
    payload["review_evidence_ids"]={**q.base.review_evidence_ids,
        "inlet_frozen_cell_mesh_and_thermal_closure":q.refinement_mapping_evidence_id}
    derived=HeatedTransportRequest.model_validate_json(json.dumps(payload,allow_nan=False))
    if derived.owner_pins!=q.base.owner_pins:
        raise ValueError("mesh refinement changed the original owner/property/runtime pins")
    context=c.model_dump(mode="json")
    context["metadata"]={**c.metadata,"heated_transport_boundary":owner.boundary_metadata(derived),
        "adaptive_heated_transport_mesh":dict(parent_request_digest=digest(q),level=level,
             subdivisions_per_original_cell=subdivisions,original_cell_ledger_digest=digest(ledger),
             refinement_mapping_evidence_id=q.refinement_mapping_evidence_id)}
    return derived,RunContext.model_validate_json(json.dumps(context,allow_nan=False)),ledger


def _check_native_record(record,request,context):
    if record.get("schema")!="pipeline_integrity.heated_transport.v1" or record.get("record_digest")!=digest({k:v for k,v in record.items() if k!="record_digest"}):
        raise ValueError("heated owner returned a changed source receipt")
    if record.get("request")!=request.model_dump(mode="json") or record.get("context")!=context.model_dump(mode="json"):
        raise ValueError("heated owner changed the refined request or mapped context")
    if record.get("implementation_sha256")!=file_digest(Path(owner.__file__)):
        raise ValueError("heated owner implementation differs from its source receipt")
    if record["status"] in ("computed_diagnostic","diagnostic_rejected"):
        if record.get("native_called") is not True or record.get("provider")!=request.owner_pins:
            raise ValueError("completed heated owner record lacks its reviewed provider/runtime binding")
        if not isinstance(record.get("values"),dict) or not isinstance(record.get("cells"),list):
            raise ValueError("completed heated owner record lacks its native values and cell history")
        if [row["cell_id"] for row in record["cells"]]!=[cell.id for cell in request.cells]:
            raise ValueError("heated owner changed the declared refined cell ordering")


def _refinable_rejection(record,request):
    if record["status"]!="diagnostic_rejected" or not isinstance(record.get("values"),dict):
        return False
    values=record["values"]
    omitted=values.get("global_omitted_kinetic_energy_rate_W")
    if type(omitted) not in (int,float) or abs(omitted)>request.maximum_omitted_kinetic_energy_rate_W:
        return False
    return all(abs(row["kinetic_energy_flux_change_W"])<=request.maximum_omitted_kinetic_energy_rate_W
               for row in record["cells"])


def _comparison(previous,current,q):
    a,b=previous["values"],current["values"]
    measurements=(
        ("outlet_pressure_pa_abs",a["outlet"]["pressure_pa_abs"],b["outlet"]["pressure_pa_abs"],
         q.outlet_pressure_absolute_tolerance_Pa,q.outlet_pressure_relative_tolerance),
        ("outlet_temperature_K",a["outlet"]["temperature_k"],b["outlet"]["temperature_k"],
         q.outlet_temperature_absolute_tolerance_K,q.outlet_temperature_relative_tolerance),
        ("total_heat_into_fluid_W",a["total_heat_into_fluid_W"],b["total_heat_into_fluid_W"],
         q.total_heat_absolute_tolerance_W,q.total_heat_relative_tolerance),
    )
    rows=[]
    for name,first,last,absolute,relative in measurements:
        difference=abs(last-first)
        limit=absolute+relative*max(abs(first),abs(last))
        if not all(math.isfinite(v) for v in (first,last,difference,limit)):
            raise ValueError("successive heated mesh comparisons exceed finite arithmetic")
        rows.append(dict(quantity=name,previous=first,current=last,absolute_difference=difference,
                         comparison_limit=limit,within_declared_difference=difference<=limit))
    return dict(metrics=rows,all_within_declared_difference=all(r["within_declared_difference"] for r in rows),
                rigorous_error_bound=False,independent_validation=False)


def evaluate_adaptive_heated_transport(request: AdaptiveHeatedTransportRequest, context: RunContext, *, property_provider=None):
    q=AdaptiveHeatedTransportRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    issues,status=_policy(q,c)
    body=dict(schema="pipeline_integrity.adaptive_heated_transport.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status=status,issues=issues,mesh_levels=[],comparisons=[],
              values=None,accepted_mesh_level=None,numerical_validation_completed=False,
              field_qualification=False,rigorous_error_bound=False,
              implementation_sha256=file_digest(Path(__file__)))
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
        raise ValueError("the retained input/context alone exceeds the mesh result byte budget")
    if not issues:
        before=adaptive_heated_transport_metadata()
        if before!=q.refinement_owner_pins:
            raise ValueError("mesh refinement source differs from the reviewed implementation pin")
        try:
            _solve(q,c,body,property_provider=property_provider)
        except (ValueError,RuntimeError,ArithmeticError,TypeError) as error:
            body.update(status="out_of_scope",issues=[f"{type(error).__name__}: {error}"],values=None,accepted_mesh_level=None)
        if adaptive_heated_transport_metadata()!=before:
            raise ValueError("mesh refinement or original heated-owner source changed during calculation")
        body["refinement_owner_pins"]=before
    body=json_value(body)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
        raise ValueError("refinement final receipt exceeds its retained-result byte budget")
    return body


def _solve(q,c,body, *, property_provider):
    subdivisions=q.initial_subdivisions_per_cell
    evaluated_cells=work=retained=0
    previous=None
    previous_level=None
    agreements=0
    for level in range(q.maximum_levels):
        count=len(q.base.cells)*subdivisions
        reserved_work=3*count+3
        if count>q.maximum_refined_cells or evaluated_cells+count>q.maximum_total_cell_evaluations or work+reserved_work>q.maximum_total_owner_api_work_units:
            body.update(status="diagnostic_rejected",issues=["refinement cell/API-work budget exhausted before consecutive mesh agreement"],values=None)
            return
        derived,derived_context,ledger=_refined_request(q,c,level,subdivisions)
        evaluated_cells+=count
        work+=reserved_work
        body["execution_budget"]=dict(levels_started=level+1,cell_evaluations_reserved=evaluated_cells,
            owner_api_work_units_reserved=work,
            work_unit_definition="per level: N hydraulic calls + N thermal calls + (N+3) property resolutions; provider-internal work is not inferred")
        native=owner.evaluate_heated_transport(derived,derived_context,property_provider=property_provider)
        _check_native_record(native,derived,derived_context)
        refinable=native["status"]=="diagnostic_rejected" and _refinable_rejection(native,derived)
        row=dict(level=level,subdivisions_per_original_cell=subdivisions,refined_cells=count,
                 derived_request_digest=digest(derived),derived_context_digest=digest(derived_context),
                 original_cell_geometry_and_integral_ledger=ledger,native_record=native,
                 native_status=native["status"],numerical_rejection_eligible_for_refinement=refinable)
        retained+=len(json.dumps(row,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        if retained>q.maximum_retained_result_bytes:
            raise ValueError("retained mesh/native histories exceed the refinement result budget")
        body["mesh_levels"].append(row)
        if native["status"] in ("needs_data","out_of_scope"):
            body.update(status=native["status"],issues=["original heated owner withheld the refined model",*native.get("notes",())],values=None)
            return
        if native["status"]=="diagnostic_rejected":
            if not refinable:
                body.update(status="diagnostic_rejected",issues=["original rejection exceeds the physical omitted-kinetic budget; mesh refinement cannot replace that model scope"],values=None)
                return
            previous=previous_level=None
            agreements=0
        elif native["status"]=="computed_diagnostic":
            if previous is not None:
                comparison=_comparison(previous,native,q)
                comparison.update(previous_level=previous_level,current_level=level,
                                  previous_native_digest=previous["record_digest"],current_native_digest=native["record_digest"])
                body["comparisons"].append(comparison)
                agreements=agreements+1 if comparison["all_within_declared_difference"] else 0
                if agreements>=q.required_consecutive_agreements:
                    body.update(status="computed_diagnostic",issues=[],values=native["values"],
                                accepted_mesh_level=level,accepted_native_record_digest=native["record_digest"],
                                consecutive_agreements=agreements,
                                acceptance_meaning="successive computed diagnostic meshes agree within caller-declared outlet P/T and heat differences")
                    return
            previous=native
            previous_level=level
        else:
            body.update(status="out_of_scope",issues=["unsupported original heated-owner status: "+str(native["status"])],values=None)
            return
        subdivisions*=q.refinement_factor
    body.update(status="diagnostic_rejected",issues=["maximum mesh levels exhausted without the required consecutive computed-diagnostic agreement"],values=None)


def verify_adaptive_heated_transport_record(record, *, request=None,context=None,property_provider=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("adaptive heated-transport receipt changed")
    q=AdaptiveHeatedTransportRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    c=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("adaptive heated-transport caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("adaptive heated-transport caller context differs")
    if evaluate_adaptive_heated_transport(q,c,property_provider=property_provider)!=record:
        raise ValueError("adaptive heated-transport original-owner replay differs")
    return True
