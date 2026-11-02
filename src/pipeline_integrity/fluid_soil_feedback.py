"""Lumped-liquid energy feedback around the original radial solid heat solver.

Every temperature-root trial restarts the solid owner from the same accepted
cell-average state. The fluid temperature is a declared linear ramp within each
original time interval. The owner alone advances solid conduction and supplies
substep-integrated boundary energy; output heat samples are never integrated
as a substitute. Moisture, a spatial fluid PDE, and phase-front coupling are
not implied by this lumped-fluid / radial-solid closure.
"""
from __future__ import annotations

import json
import math
import inspect
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import pipe_soil_transient as solid_owner
from .adapters import MaterialClient
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_source import material_source_guard
from .pipe_soil_transient import PipeSoilShell, PipeSoilTransientRequest

REVIEWS = (
    "fixed_mass_uniform_liquid_and_constant_heat_capacity",
    "equal_pressure_flow_work_and_absent_unmodelled_mechanical_heating",
    "matched_advective_inflow_outflow_and_heat_boundaries",
    "linear_fluid_temperature_ramp_and_interval_mesh",
    "identical_initial_solid_state_for_every_root_trial",
    "shared_interface_energy_sign_and_source_clock_mapping",
)


class FluidSoilFeedbackRequest(Contract):
    id: Name
    solid: PipeSoilTransientRequest
    fluid_mass_kg: Positive
    fluid_heat_capacity_J_kg_K: Positive
    initial_fluid_temperature_K: Positive
    fluid_temperature_support_K: tuple[Positive, Positive]
    outlet_temperature_brackets_K: tuple[tuple[Positive, Positive], ...]
    mass_flow_in_equals_out_kg_s: tuple[Nonnegative, ...]
    inlet_temperature_K: tuple[Positive, ...]
    heater_into_fluid_W: tuple[float, ...]
    fluid_boundary_mode: Literal["iterated_linear_fluid_temperature_ramp"]
    fluid_boundary_mapping_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    coupling_owner_pins: dict = Field(default_factory=dict)
    root_absolute_temperature_tolerance_K: Positive = 1e-10
    root_relative_temperature_tolerance: Positive = Field(default=1e-12, ge=1e-14, le=1e-3)
    maximum_root_iterations: int = Field(default=100, ge=1, le=1000)
    maximum_trials_per_interval: int = Field(default=120, ge=2, le=2000)
    maximum_total_owner_evaluations: int = Field(default=10000, ge=2, le=1000000)
    maximum_total_owner_work_units: int = Field(default=1000000000, ge=1, le=1000000000000)
    maximum_total_native_steps: int = Field(default=1000000, ge=1, le=100000000)
    maximum_retained_result_bytes: int = Field(default=256000000, ge=1000, le=1000000000)
    fluid_energy_absolute_tolerance_J: Positive = 1e-3
    coupled_energy_absolute_tolerance_J: Positive = 1e-3
    energy_relative_tolerance: Nonnegative = Field(default=1e-7, le=1e-3)

    @model_validator(mode="after")
    def interval_contract(self):
        intervals = len(self.solid.time_s)-1
        if any(len(values) != intervals for values in
               (self.outlet_temperature_brackets_K, self.mass_flow_in_equals_out_kg_s,
                self.inlet_temperature_K, self.heater_into_fluid_W)):
            raise ValueError("fluid source and root-bracket arrays must follow every original solid time interval")
        low, high = self.fluid_temperature_support_K
        if low >= high or not low <= self.initial_fluid_temperature_K <= high:
            raise ValueError("fluid temperature support must be ordered and contain the initial state")
        for a,b in self.outlet_temperature_brackets_K:
            if not low <= a < b <= high:
                raise ValueError("fluid endpoint brackets must be ordered inside reviewed fluid-property support")
        if any(not low <= t <= high for t in self.inlet_temperature_K):
            raise ValueError("advective supply temperature exceeds the constant-property fluid support")
        inner = self.solid.inner_boundary
        if inner.kind != "convection_radiation" or inner.emissivity != 0:
            raise ValueError("fluid feedback requires the original inner convection boundary with explicitly zero emissivity")
        if set(self.reviews) != set(REVIEWS) or set(self.review_evidence_ids)-set(REVIEWS):
            raise ValueError("all named fluid/solid coupling review fields are required")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("fluid feedback evidence identifiers must be unique")
        capacity = self.fluid_mass_kg*self.fluid_heat_capacity_J_kg_K
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("lumped-fluid heat capacity exceeds finite arithmetic")
        for i,(a,b) in enumerate(zip(self.solid.time_s,self.solid.time_s[1:])):
            if self.solid.clock_offset_s+b <= self.solid.clock_offset_s+a:
                raise ValueError("the declared source-clock offset cannot represent separate feedback intervals")
            if not all(math.isfinite(v) for v in (
                self.mass_flow_in_equals_out_kg_s[i]*self.fluid_heat_capacity_J_kg_K*(b-a),
                self.heater_into_fluid_W[i]*(b-a),
                capacity*(self.outlet_temperature_brackets_K[i][1]-self.outlet_temperature_brackets_K[i][0]),
            )):
                raise ValueError("fluid feedback interval source/storage coefficients exceed finite arithmetic")
        return self


class _NativeSolidWithheld(RuntimeError):
    def __init__(self, record):
        self.record = record
        super().__init__("radial solid owner withheld a fluid-feedback trial")


def fluid_soil_feedback_metadata():
    from scipy.optimize import brentq, _zeros
    return dict(scipy_version=version("scipy"),
                brentq_source_sha256=file_digest(Path(inspect.getfile(brentq))),
                root_binary_sha256=file_digest(Path(_zeros.__file__)),
                implementation_sha256=file_digest(Path(__file__)),
                solid_adapter_source_sha256=file_digest(Path(solid_owner.__file__)),
                shared_source_sha256={name:file_digest(Path(__file__).with_name(name+".py"))
                                      for name in ("contracts","material_source","material_exchange","adapters")})


def fluid_soil_feedback_boundary(request: FluidSoilFeedbackRequest):
    q = request
    return dict(asset_id=q.solid.asset_id,location_id=q.solid.location_id,event_id=q.solid.event_id,
                clock_id=q.solid.clock_id,clock_offset_s=q.solid.clock_offset_s,
                source_time_s=list(q.solid.time_s),request_digest=digest(q),
                interface_heat_sign="positive_into_solid_equals_heat_out_of_fluid",
                fluid_boundary_mode=q.fluid_boundary_mode)


def bind_fluid_soil_feedback_context(request: FluidSoilFeedbackRequest, context: RunContext):
    q = FluidSoilFeedbackRequest.model_validate_json(request.model_dump_json())
    c = RunContext.model_validate_json(context.model_dump_json())
    payload = c.model_dump(mode="json")
    payload["metadata"] = {**c.metadata,"fluid_soil_feedback_boundary":fluid_soil_feedback_boundary(q)}
    bound = RunContext.model_validate_json(json.dumps(payload))
    _policy(q,bound)
    return bound


def _policy(q,c):
    base = q.solid
    if (base.asset_id,base.event_id,base.clock_id) != (c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("fluid feedback asset/event/clock differs from actual context")
    if c.metadata.get("fluid_soil_feedback_boundary") != fluid_soil_feedback_boundary(q):
        raise ValueError("fluid feedback context must bind the exact request, source clock and interface sign")
    available = {e.id:e for e in c.evidence}
    selected = set(q.evidence_ids)
    if len(available) != len(c.evidence) or not selected <= set(available):
        raise ValueError("fluid feedback evidence must be unique and present in context")
    required = {q.fluid_boundary_mapping_evidence_id,*q.review_evidence_ids.values()}
    if not required <= selected:
        raise ValueError("fluid/interface review evidence must belong to the selected coupling evidence")
    mapping = available[q.fluid_boundary_mapping_evidence_id]
    if mapping.sha256 is None or mapping.origin == "synthetic":
        raise ValueError("fluid/solid boundary-clock mapping requires a reviewed source-hash-pinned evidence item")
    issues = ["review/evidence required: "+key for key in REVIEWS
              if not q.reviews[key] or key not in q.review_evidence_ids]
    if not q.coupling_owner_pins:
        issues.append("reviewed nonlinear coupling source/runtime pins are required")
    return issues


def _slice_boundary(boundary, interval):
    data = boundary.model_dump(mode="json")
    for key,value in list(data.items()):
        if isinstance(value,list):
            data[key] = value[interval:interval+2]
    return data


def _trial_request(q,c,interval,shells,fluid_start,fluid_end):
    source = q.solid
    first,last = source.time_s[interval:interval+2]
    data = source.model_dump(mode="json")
    data["time_s"] = [0.0,last-first]
    data["clock_offset_s"] = source.clock_offset_s+first
    data["shells"] = [s.model_dump(mode="json") for s in shells]
    inner = _slice_boundary(source.inner_boundary,interval)
    inner["ambient_temperature_K"] = [fluid_start,fluid_end]
    inner["evidence_id"] = q.fluid_boundary_mapping_evidence_id
    data["inner_boundary"] = inner
    data["outer_boundary"] = _slice_boundary(source.outer_boundary,interval)
    data["review_evidence_ids"] = {**source.review_evidence_ids,
                                   "linear_boundary_history_and_clock_mapping":q.fluid_boundary_mapping_evidence_id}
    data["evidence_ids"] = list(dict.fromkeys((*source.evidence_ids,*q.evidence_ids)))
    derived = PipeSoilTransientRequest.model_validate_json(json.dumps(data,allow_nan=False))
    context = c.model_dump(mode="json")
    context["metadata"] = {**c.metadata,"pipe_soil_boundary":solid_owner.pipe_soil_boundary(derived),
        "fluid_soil_interval":dict(parent_request_digest=digest(q),interval_index=interval,
             original_local_time_bounds_s=[first,last],common_clock_start_s=derived.clock_offset_s,
             initial_shell_states_digest=digest(shells),initial_fluid_temperature_K=fluid_start,
             trial_final_fluid_temperature_K=fluid_end,mapping_evidence_id=q.fluid_boundary_mapping_evidence_id)}
    return derived,RunContext.model_validate_json(json.dumps(context,allow_nan=False))


def _next_shells(shells,flat_temperatures):
    if len(flat_temperatures) != sum(len(s.initial_cell_temperature_K) for s in shells):
        raise ValueError("native final solid-temperature array differs from the original shell mesh")
    output=[]
    offset=0
    for shell in shells:
        count=len(shell.initial_cell_temperature_K)
        data=shell.model_dump(mode="json")
        data["initial_cell_temperature_K"]=flat_temperatures[offset:offset+count]
        output.append(PipeSoilShell.model_validate_json(json.dumps(data,allow_nan=False)))
        offset+=count
    return tuple(output)


def _solid_energy_change(base,initial_shells,final_shells):
    materials={m.id:m for m in base.materials}
    changes=[]
    for initial,final in zip(initial_shells,final_shells):
        material=materials[initial.material_id]
        energy=math.fsum(math.pi*base.cylinder_length_m*(right*right-left*left)
                        *solid_owner._energy_density_change(material,start,end)
                        for left,right,start,end in zip(initial.radial_edges_m,initial.radial_edges_m[1:],
                                                       initial.initial_cell_temperature_K,final.initial_cell_temperature_K))
        changes.append(dict(shell_id=initial.id,energy_change_J=energy))
    return math.fsum(v["energy_change_J"] for v in changes),changes


def evaluate_fluid_soil_feedback(request: FluidSoilFeedbackRequest, context: RunContext, *,
                                 client=None, owner_source_root=None):
    q=FluidSoilFeedbackRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    body=dict(schema="pipeline_integrity.fluid_soil_feedback.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="needs_data",issues=_policy(q,c),
              intervals=[],root_trials=[],states=[],values=None,owner_source_pin_report=None,
              implementation_sha256=file_digest(Path(__file__)),
              solid_adapter_source_sha256=file_digest(Path(solid_owner.__file__)),
              qualification=dict(fluid_energy_feedback_modelled=True,fluid_spatial_transport_modelled=False,
                                  moisture_transport_modelled=False,spatial_phase_front_modelled=False,
                                  hydraulic_pressure_feedback_modelled=False,
                                  independent_validation_completed=False),
              interface_definition=dict(original_film_evidence_id=q.solid.inner_boundary.evidence_id,
                  derived_fluid_temperature_evidence_id=q.fluid_boundary_mapping_evidence_id,
                  convection_history_digest=digest(q.solid.inner_boundary.convection_W_m2_K),
                  emissivity=0.0,fluid_ramp_replaces_template_ambient_temperature_only=True),
              numerical_validation_completed=False)
    for interval,bracket in enumerate(q.outlet_temperature_brackets_K):
        derived,derived_context=_trial_request(q,c,interval,q.solid.shells,q.initial_fluid_temperature_K,bracket[0])
        issues,status=solid_owner._policy(derived,derived_context)
        if issues:
            body["issues"].extend("solid interval "+str(interval)+": "+issue for issue in issues)
            if status=="out_of_scope":
                body["status"]="out_of_scope"
    if owner_source_root is None:
        body["issues"].append("reviewed original material source root is required")
    if not body["issues"]:
        coupling_before=fluid_soil_feedback_metadata()
        if coupling_before!=q.coupling_owner_pins:
            raise ValueError("fluid/solid coupling source/runtime differs from reviewed root-solver pins")
        active_client=MaterialClient() if client is None else client
        with material_source_guard(owner_source_root,q.solid.owner_source_tree_sha256,active_client) as source_report:
            try:
                _solve(q,c,body,client=active_client,owner_source_root=owner_source_root)
            except _NativeSolidWithheld as error:
                native=error.record
                status=native["status"] if native["status"]!="computed" else "diagnostic_rejected"
                body.update(status=status,issues=["solid owner withheld a feedback trial",*native.get("issues",())],
                            withheld_native_trial=native,values=None)
            except (ValueError,RuntimeError,ArithmeticError,TypeError) as error:
                body.update(status="out_of_scope",issues=[f"{type(error).__name__}: {error}"],values=None)
        body["owner_source_pin_report"]=source_report
        if fluid_soil_feedback_metadata()!=coupling_before:
            raise ValueError("fluid/solid coupling implementation changed during calculation")
        body["coupling_owner_pins"]=coupling_before
    body=json_value(body)
    body["fluid_feedback_computed"] = body["status"]=="computed_diagnostic"
    body["record_digest"]=digest(body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
        raise ValueError("fluid feedback final structured result exceeds its declared retained-byte budget")
    return body


def _solve(q,c,body, *, client,owner_source_root):
    from scipy.optimize import brentq
    capacity=q.fluid_mass_kg*q.fluid_heat_capacity_J_kg_K
    shells=q.solid.shells
    temperature=q.initial_fluid_temperature_K
    cells=sum(len(s.initial_cell_temperature_K) for s in shells)
    calls=work=actual_steps=retained_bytes=0
    step_count_complete=True
    totals=dict(heater_into_fluid_J=0.0,advective_net_into_fluid_J=0.0,
                inner_boundary_into_solid_J=0.0,outer_boundary_into_solid_J=0.0,
                summed_solid_energy_change_J=0.0)
    body["states"].append(dict(time_s=q.solid.clock_offset_s,fluid_temperature_K=temperature,
                               accepted_solid_state_digest=digest(shells)))
    for interval,(first,last) in enumerate(zip(q.solid.time_s,q.solid.time_s[1:])):
        duration=last-first
        start=temperature
        initial_shells=shells
        initial_digest=digest(initial_shells)
        cache={}
        latest_trial={}
        interval_calls=0

        def trial(endpoint, *, force=False):
            nonlocal calls,work,actual_steps,retained_bytes,interval_calls,step_count_complete
            endpoint=float(endpoint)
            if endpoint in cache and not force:
                return cache[endpoint]
            if interval_calls>=q.maximum_trials_per_interval or calls>=q.maximum_total_owner_evaluations:
                raise ValueError("fluid feedback exceeded its original-owner evaluation budget")
            derived,derived_context=_trial_request(q,c,interval,initial_shells,start,endpoint)
            planned_steps=math.ceil(duration/derived.maximum_step_s)
            reserved_work=planned_steps*cells*derived.maximum_nonlinear_iterations
            if work+reserved_work>q.maximum_total_owner_work_units or actual_steps+planned_steps>q.maximum_total_native_steps:
                raise ValueError("fluid feedback native work/step budget would be exceeded before owner invocation")
            interval_calls+=1
            calls+=1
            work+=reserved_work
            step_count_complete=False
            body["execution_budget"]=dict(owner_evaluations=calls,reserved_owner_work_units=work,
                native_steps=actual_steps,native_step_count_complete=False,retained_result_bytes=retained_bytes)
            native=solid_owner.evaluate_pipe_soil_transient(derived,derived_context,client=client,owner_source_root=owner_source_root)
            if native.get("request")!=derived.model_dump(mode="json") or native.get("context")!=derived_context.model_dump(mode="json"):
                raise ValueError("solid owner trial returned a different interval, initial state or boundary context")
            solid_owner.verify_pipe_soil_transient_record(native)
            receipt=native.get("native_receipt")
            raw_values=None if receipt is None else receipt.get("native",{}).get("payload",{}).get("values")
            steps=0 if receipt is None else (raw_values.get("internal_steps") if isinstance(raw_values,dict) else None)
            if type(steps) is int and steps>=0:
                actual_steps+=steps
                step_count_complete=True
            body["execution_budget"]=dict(owner_evaluations=calls,reserved_owner_work_units=work,
                native_steps=actual_steps,native_step_count_complete=step_count_complete,retained_result_bytes=retained_bytes)
            audit=native.get("audit")
            if native["status"]!="computed" or not isinstance(audit,dict) or not audit.get("available") or not audit.get("energy_within_tolerance"):
                raise _NativeSolidWithheld(native)
            values=native["native_receipt"]["native"]["payload"]["values"]
            if not step_count_complete or steps>planned_steps or actual_steps>q.maximum_total_native_steps:
                raise ValueError("solid owner exceeded the reserved fixed-substep feedback work bound")
            if digest(initial_shells)!=initial_digest:
                raise ValueError("a root trial mutated the accepted initial solid state")
            if len(json.dumps(native,ensure_ascii=False,allow_nan=False).encode("utf-8"))>q.maximum_retained_result_bytes:
                raise ValueError("one native trial receipt exceeds the feedback retained-byte budget")
            inner=audit["inner_boundary_energy_into_solid_J"]
            heater=q.heater_into_fluid_W[interval]*duration
            advective=q.mass_flow_in_equals_out_kg_s[interval]*q.fluid_heat_capacity_J_kg_K*duration*(q.inlet_temperature_K[interval]-(start+endpoint)/2)
            residual=capacity*(endpoint-start)-heater-advective+inner
            log=dict(interval_index=interval,final_fluid_temperature_K=endpoint,
                     initial_solid_state_digest=initial_digest,derived_request_digest=digest(derived),
                     derived_context_digest=digest(derived_context),native_record_digest=native["record_digest"],
                     integrated_inner_boundary_energy_into_solid_J=inner,fluid_energy_residual_J=residual,
                     reserved_owner_work_units=reserved_work,native_steps=steps)
            log_size=len(json.dumps(log,ensure_ascii=False,allow_nan=False).encode("utf-8"))
            retained_bytes+=log_size
            if retained_bytes>q.maximum_retained_result_bytes:
                raise ValueError("fluid feedback trial metadata exceeded its retained-result budget")
            body["root_trials"].append(log)
            cache[endpoint]=residual
            latest_trial.clear()
            latest_trial.update(endpoint=endpoint,result=(residual,native,derived,derived_context,heater,advective))
            return residual

        lower,upper=q.outlet_temperature_brackets_K[interval]
        lo=trial(lower)
        if lo==0:
            endpoint=lower
        else:
            hi=trial(upper)
            if hi==0:
                endpoint=upper
            elif (lo>0)==(hi>0):
                raise ValueError("fluid endpoint-temperature bracket does not enclose an interface-energy root")
            else:
                endpoint=float(brentq(trial,lower,upper,xtol=q.root_absolute_temperature_tolerance_K,
                                      rtol=q.root_relative_temperature_tolerance,maxiter=q.maximum_root_iterations))
        residual=trial(endpoint)
        if latest_trial.get("endpoint")!=endpoint:
            # Root residuals are cheap scalar cache entries. Retain only one
            # full trial receipt; a selected earlier endpoint is recomputed
            # from the same immutable initial state before state promotion.
            repeated=trial(endpoint,force=True)
            if repeated!=residual:
                raise ValueError("solid owner did not reproduce the selected fluid-root residual")
        _,native,derived,derived_context,heater,advective=latest_trial["result"]
        audit=native["audit"]
        inner=audit["inner_boundary_energy_into_solid_J"]
        outer=audit["outer_boundary_energy_into_solid_J"]
        solid_energy=audit["total_solid_energy_change_J"]
        fluid_energy=capacity*(endpoint-start)
        total_residual=fluid_energy+solid_energy-heater-advective-outer
        fluid_limit=q.fluid_energy_absolute_tolerance_J+q.energy_relative_tolerance*max(abs(fluid_energy),abs(heater),abs(advective),abs(inner))
        coupled_limit=q.coupled_energy_absolute_tolerance_J+q.energy_relative_tolerance*max(abs(fluid_energy)+abs(solid_energy),abs(heater)+abs(advective)+abs(outer))
        accepted=abs(residual)<=fluid_limit and abs(total_residual)<=coupled_limit
        interval_record=dict(interval_index=interval,original_time_bounds_s=[first,last],
            common_clock_time_bounds_s=[q.solid.clock_offset_s+first,q.solid.clock_offset_s+last],
            initial_fluid_temperature_K=start,final_fluid_temperature_K=endpoint,
            fluid_energy_change_J=fluid_energy,solid_energy_change_J=solid_energy,
            heater_into_fluid_J=heater,advective_net_into_fluid_J=advective,
            inner_boundary_energy_into_solid_J=inner,outer_boundary_energy_into_solid_J=outer,
            fluid_energy_residual_J=residual,fluid_energy_balance_limit_J=fluid_limit,
            coupled_energy_residual_J=total_residual,coupled_energy_balance_limit_J=coupled_limit,
            state_promoted=accepted,root_trials=interval_calls,native_record=native)
        retained_bytes+=len(json.dumps(interval_record,ensure_ascii=False,allow_nan=False).encode("utf-8"))
        if retained_bytes>q.maximum_retained_result_bytes:
            raise ValueError("fluid feedback native receipts exceeded the retained-result budget")
        body["intervals"].append(interval_record)
        body["execution_budget"]=dict(owner_evaluations=calls,reserved_owner_work_units=work,
                                       native_steps=actual_steps,native_step_count_complete=step_count_complete,
                                       retained_result_bytes=retained_bytes)
        if not accepted:
            body.update(status="diagnostic_rejected",issues=["fluid/solid interface or total energy budget failed"],values=None)
            return
        values=native["native_receipt"]["native"]["payload"]["values"]
        shells=_next_shells(initial_shells,values["temperature_history_K"][-1])
        temperature=endpoint
        totals["heater_into_fluid_J"]+=heater
        totals["advective_net_into_fluid_J"]+=advective
        totals["inner_boundary_into_solid_J"]+=inner
        totals["outer_boundary_into_solid_J"]+=outer
        totals["summed_solid_energy_change_J"]+=solid_energy
        body["states"].append(dict(time_s=q.solid.clock_offset_s+last,fluid_temperature_K=temperature,
                                   accepted_solid_state_digest=digest(shells)))
    solid_energy,shell_changes=_solid_energy_change(q.solid,q.solid.shells,shells)
    fluid_energy=capacity*(temperature-q.initial_fluid_temperature_K)
    telescope=solid_energy-totals["summed_solid_energy_change_J"]
    residual=fluid_energy+solid_energy-totals["heater_into_fluid_J"]-totals["advective_net_into_fluid_J"]-totals["outer_boundary_into_solid_J"]
    limit=q.coupled_energy_absolute_tolerance_J+q.energy_relative_tolerance*max(
        abs(fluid_energy)+abs(solid_energy),abs(totals["heater_into_fluid_J"])+abs(totals["advective_net_into_fluid_J"])+abs(totals["outer_boundary_into_solid_J"]))
    valid=abs(residual)<=limit and abs(telescope)<=limit
    body.update(status="computed_diagnostic" if valid else "diagnostic_rejected",
                issues=[] if valid else ["global fluid/solid energy or shell-state telescoping budget failed"],
                values=dict(final_fluid_temperature_K=temperature,
                    final_solid_shells=[s.model_dump(mode="json") for s in shells],
                    fluid_energy_change_J=fluid_energy,solid_energy_change_J=solid_energy,
                    shell_energy_changes=shell_changes,boundary_energy_ledger=totals,
                    global_coupled_energy_residual_J=residual,solid_energy_telescope_residual_J=telescope,
                    global_energy_balance_limit_J=limit))


def verify_fluid_soil_feedback_record(record, *, request=None, context=None,client=None,owner_source_root=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("fluid/soil feedback receipt changed")
    q=FluidSoilFeedbackRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    c=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("fluid feedback caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("fluid feedback caller context differs")
    if evaluate_fluid_soil_feedback(q,c,client=client,owner_source_root=owner_source_root)!=record:
        raise ValueError("fluid/soil original-owner replay differs")
    return True
