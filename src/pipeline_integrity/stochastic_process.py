"""Finite-state competing-event/recovery processes with supplied hazard windows.

The state probability column evolves with a sparse continuous-time generator.
An augmented exponential also integrates state occupation. Transition counts
and rewards therefore use the same propagation, including repeated failures
and recoveries. Hazards are inputs; process traces never generate failure rates.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, require_context_extension
from .datasets import file_digest


class StochasticState(Contract):
    id: Name
    initial_probability: Fraction
    absorbing: bool = False
    definition_id: Name
    evidence_id: Name


class StochasticTransition(Contract):
    id: Name
    from_state_id: Name
    to_state_id: Name
    kind: Literal["failure","recovery","process_endpoint","other"]
    event_definition_id: Name
    evidence_id: Name


class HazardWindow(Contract):
    id: Name
    duration_s: Positive
    transition_hazard_per_s: tuple[Nonnegative, ...]
    hazard_unit: Literal["s^-1"] = "s^-1"
    evidence_id: Name
    physical_history_source_digest: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")
    end_state_mapping: tuple[Name,...] | None = None
    boundary_mapping_evidence_id: Name | None = None
    deterministic_boundary_state_transfer_reviewed: bool = False


class ProcessReward(Contract):
    id: Name
    quantity_unit: Name
    state_quantity_rate_per_s: tuple[float, ...]
    transition_quantity_per_event: tuple[float, ...]
    definition_id: Name
    evidence_id: Name


class StochasticProcessRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    condition_id: Name
    conditioning: Name
    states: tuple[StochasticState, ...] = Field(min_length=2,max_length=1000)
    transitions: tuple[StochasticTransition, ...] = Field(min_length=1,max_length=10000)
    windows: tuple[HazardWindow, ...] = Field(min_length=1,max_length=1000)
    rewards: tuple[ProcessReward, ...] = Field(default=(),max_length=100)
    probability_conservation_tolerance: Positive = Field(default=1e-11,le=1e-6)
    relative_balance_tolerance: Positive = Field(default=1e-10,le=1e-5)
    absolute_occupation_tolerance_s: Positive = 1e-10
    absolute_expectation_tolerance: Positive = 1e-10
    maximum_operator_work_units: int = Field(default=100000000,ge=1,le=100000000000)
    maximum_retained_values: int = Field(default=4000000,ge=100,le=20000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=256000000)
    finite_states_memoryless_conditional_hazards_reviewed: bool = False
    competing_events_and_recovery_state_definitions_reviewed: bool = False
    rate_windows_and_common_conditioning_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def layout(self):
        for values in (self.states,self.transitions,self.windows,self.rewards):
            if len({v.id for v in values}) != len(values):
                raise ValueError("stochastic state/transition/window/reward IDs must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("stochastic evidence IDs must be unique")
        if not math.isclose(math.fsum(v.initial_probability for v in self.states),1,rel_tol=0,abs_tol=1e-12):
            raise ValueError("initial stochastic state probabilities must sum to one without normalization")
        state_map = {s.id:s for s in self.states}
        for edge in self.transitions:
            if edge.from_state_id not in state_map or edge.to_state_id not in state_map or edge.from_state_id == edge.to_state_id:
                raise ValueError("stochastic transitions must join distinct existing states")
        for window in self.windows:
            if len(window.transition_hazard_per_s) != len(self.transitions):
                raise ValueError("each hazard window must explicitly identify every transition, including zero rates")
            if any(state_map[e.from_state_id].absorbing and rate != 0
                   for e,rate in zip(self.transitions,window.transition_hazard_per_s,strict=True)):
                raise ValueError("an absorbing state cannot have a positive outgoing hazard")
            if window.end_state_mapping is not None:
                if (len(window.end_state_mapping) != len(self.states)
                        or not set(window.end_state_mapping) <= set(state_map)
                        or window.boundary_mapping_evidence_id is None
                        or not window.deterministic_boundary_state_transfer_reviewed):
                    raise ValueError("deadline/boundary state mapping requires a complete reviewed source-pinned mapping")
                if any(state.absorbing and destination != state.id
                       for state,destination in zip(self.states,window.end_state_mapping,strict=True)):
                    raise ValueError("an absorbing endpoint cannot be undone by a deterministic boundary mapping")
            elif window.boundary_mapping_evidence_id is not None or window.deterministic_boundary_state_transfer_reviewed:
                raise ValueError("boundary transfer evidence/review requires an actual state mapping")
        for reward in self.rewards:
            if len(reward.state_quantity_rate_per_s) != len(self.states) or len(reward.transition_quantity_per_event) != len(self.transitions):
                raise ValueError("reward coefficients must follow the full state and transition orders")
        if len(self.windows)*(8*len(self.states)+2*len(self.transitions)+len(self.rewards)+20) > self.maximum_retained_values:
            raise ValueError("stochastic window/state/transition output budget exceeded")
        return self


def stochastic_process_metadata():
    return {name:file_digest(Path(__file__).with_name(name+".py"))
            for name in ("stochastic_process","risk_exchange","period_risk","adapter_jobs","adapter_campaign")}


def evaluate_stochastic_process(request: StochasticProcessRequest,context: RunContext):
    import numpy as np
    import scipy
    from scipy.sparse import bmat,coo_matrix,eye,csc_matrix
    from scipy.sparse.linalg import LinearOperator,expm_multiply
    q = StochasticProcessRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("stochastic process asset/clock differs from context")
    if not all((q.finite_states_memoryless_conditional_hazards_reviewed,
                q.competing_events_and_recovery_state_definitions_reviewed,
                q.rate_windows_and_common_conditioning_reviewed)):
        raise ValueError("finite-state hazards, competing/recovery definitions and interval conditioning reviews are required")
    evidence = {e.id:e for e in c.evidence}
    required = set(q.evidence_ids) | {x.evidence_id for values in (q.states,q.transitions,q.windows,q.rewards) for x in values}
    required |= {w.boundary_mapping_evidence_id for w in q.windows if w.boundary_mapping_evidence_id is not None}
    if len(evidence) != len(c.evidence) or not required <= set(evidence) or any(evidence[key].sha256 is None for key in required):
        raise ValueError("stochastic state/rate/reward evidence requires unambiguous source SHA-256 pins")
    pins,q_pin,c_pin = stochastic_process_metadata(),digest(q),digest(c)
    if len(json.dumps({"request":q.model_dump(mode="json"),"context":c.model_dump(mode="json")}).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("stochastic request/context already exceed the complete result-byte budget")
    n = len(q.states)
    index = {s.id:i for i,s in enumerate(q.states)}
    origin = [index[e.from_state_id] for e in q.transitions]
    target = [index[e.to_state_id] for e in q.transitions]
    p = np.array([s.initial_probability for s in q.states],dtype=float)
    occupation_total = np.zeros(n)
    transition_total = np.zeros(len(q.transitions))
    reward_total = np.zeros(len(q.rewards))
    time = q.start_time_s
    rows = [dict(time_s=time,state_probability=p.tolist(),window_id=None)]
    windows = []
    work = retained_bytes = 0
    failure = None
    for window in q.windows:
        try:
            dt = window.duration_s
            if not math.isfinite(time+dt) or time+dt <= time:
                raise ValueError("stochastic hazard interval is not representable on the declared clock")
            rates = np.array(window.transition_hazard_per_s,dtype=float)
            leaving = np.zeros(n)
            for i,rate in zip(origin,rates,strict=True):
                leaving[i] += rate
            values = rates.tolist()+(-leaving).tolist()
            generator = coo_matrix((values,(target+list(range(n)),origin+list(range(n)))),shape=(n,n)).tocsc()
            if not np.all(np.isfinite(generator.data)):
                raise ValueError("stochastic generator exceeds finite arithmetic")
            # Occupation/dt is integrated rather than occupation itself, so both
            # blocks of this augmented system are dimensionless probabilities.
            zero = csc_matrix((n,n))
            augmented = bmat([[dt*generator,zero],[eye(n,format="csc"),zero]],format="csc")
            if not np.all(np.isfinite(augmented.data)):
                raise ValueError("duration-scaled stochastic generator exceeds finite arithmetic")

            def apply(vector,adjoint=False):
                nonlocal work
                columns = 1 if np.ndim(vector) == 1 else np.shape(vector)[1]
                work += max(1,augmented.nnz)*columns
                if work > q.maximum_operator_work_units:
                    raise ValueError("stochastic exponential operator-work budget exhausted")
                return (augmented.T if adjoint else augmented) @ vector

            operator = LinearOperator((2*n,2*n),matvec=apply,matmat=apply,
                                      rmatvec=lambda x:apply(x,True),rmatmat=lambda x:apply(x,True),dtype=float)
            evolved = np.asarray(expm_multiply(operator,np.concatenate((p,np.zeros(n))),
                                               traceA=-dt*float(np.sum(leaving))),dtype=float)
            next_p = evolved[:n]
            occupation = dt*evolved[n:]
            if not np.all(np.isfinite(evolved)) or np.any(next_p < 0) or np.any(next_p > 1) or np.any(occupation < 0):
                raise ValueError("stochastic exponential left the nonnegative probability/occupation domain; no clipping applied")
            probability_residual = float(np.sum(next_p)-np.sum(p))
            occupation_residual = float(np.sum(occupation)-dt)
            if (abs(probability_residual) > q.probability_conservation_tolerance
                    or abs(occupation_residual) > q.absolute_occupation_tolerance_s+q.relative_balance_tolerance*dt):
                raise ValueError("stochastic exponential failed probability/occupation conservation")
            expected_counts = rates*occupation[np.asarray(origin)]
            state_flow = np.zeros(n)
            gross_state_flow = np.zeros(n)
            for i,j,count in zip(origin,target,expected_counts,strict=True):
                state_flow[i] -= count
                state_flow[j] += count
                gross_state_flow[i] += abs(count)
                gross_state_flow[j] += abs(count)
            state_balance = next_p-p-state_flow
            state_gate = q.probability_conservation_tolerance+q.relative_balance_tolerance*np.maximum(
                gross_state_flow,np.abs(next_p-p))
            if not np.all(np.isfinite(expected_counts)) or np.any(np.abs(state_balance) > state_gate):
                raise ValueError("stochastic transition-count fluxes do not reconcile state probability changes")
            reward_values = np.array([float(np.dot(r.state_quantity_rate_per_s,occupation)
                                           +np.dot(r.transition_quantity_per_event,expected_counts)) for r in q.rewards])
            if not np.all(np.isfinite(reward_values)):
                raise ValueError("stochastic expected reward exceeds finite arithmetic")
            pre_boundary = next_p.copy()
            boundary_transfer = None
            if window.end_state_mapping is not None:
                next_p = np.zeros(n)
                for i,destination in enumerate(window.end_state_mapping):
                    next_p[index[destination]] += pre_boundary[i]
                if (np.any(next_p < 0) or np.any(next_p > 1)
                        or abs(float(np.sum(next_p)-np.sum(pre_boundary))) > q.probability_conservation_tolerance):
                    raise ValueError("deterministic deadline/state transfer failed probability conservation")
                boundary_transfer = dict(destination_state_by_source=list(window.end_state_mapping),
                    source_probability=pre_boundary.tolist(),destination_probability=next_p.tolist(),
                    mapping_evidence_id=window.boundary_mapping_evidence_id,
                    hazard_used_to_emulate_deadline=False)
            receipt = dict(window_id=window.id,start_time_s=time,finish_time_s=time+dt,
                state_occupation_s=occupation.tolist(),expected_transition_counts=expected_counts.tolist(),
                expected_reward_quantity=reward_values.tolist(),probability_residual=probability_residual,
                occupation_residual_s=occupation_residual,state_probability_flux_residual=state_balance.tolist(),
                physical_history_source_digest=window.physical_history_source_digest,
                deterministic_boundary_transfer=boundary_transfer,
                state_probability_before_boundary_mapping=pre_boundary.tolist(),
                generator_digest=digest(dict(shape=[n,n],data=generator.data.tolist(),
                                             indices=generator.indices.tolist(),indptr=generator.indptr.tolist())))
            row = dict(time_s=time+dt,state_probability=next_p.tolist(),window_id=window.id)
            retained_bytes += len(json.dumps([row,receipt]).encode("utf-8"))
            if retained_bytes > q.maximum_result_bytes:
                raise ValueError("stochastic retained probability/occupation trajectory exceeds result-byte budget")
            time += dt
            p = next_p
            occupation_total += occupation
            transition_total += expected_counts
            reward_total += reward_values
            windows.append(receipt)
            rows.append(row)
        except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as error:
            failure = type(error).__name__+": "+str(error)
            break
    if stochastic_process_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("stochastic process source/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.stochastic_process.v1",request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",
                failure=failure,implementation=pins,state_order=[s.id for s in q.states],
                transition_order=[e.id for e in q.transitions],reward_order=[r.id for r in q.rewards],
                states=rows,windows=windows,total_state_occupation_s=occupation_total.tolist(),
                total_expected_transition_counts=transition_total.tolist(),
                total_expected_reward_quantity=reward_total.tolist(),reward_quantity_units=[r.quantity_unit for r in q.rewards],
                absorbing_endpoint_probability={s.id:float(p[i]) for i,s in enumerate(q.states) if s.absorbing},
                operator_work_units=work,numerical_backend={"numpy":np.__version__,"scipy":scipy.__version__},
                hazards_inferred_from_deterministic_process=False,semi_markov_or_continuous_state_feedback_solved=False,
                deterministic_deadlines_use_reviewed_state_mapping=True,
                boundary_transfer_rewards_inferred=False,
                expected_transition_count_is_a_probability=False,annual_frequency_inferred=False,
                result_origin="simulation",
                numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("complete stochastic process receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_stochastic_process_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("stochastic process receipt changed")
    q = StochasticProcessRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("stochastic process caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("stochastic process caller context differs")
    if record.get("status") != "computed_diagnostic":
        raise ValueError("withheld stochastic trajectories do not establish a completed replay")
    replay = evaluate_stochastic_process(q,c)
    if replay["status"] != "computed_diagnostic" or replay["implementation"] != record["implementation"] or replay["numerical_backend"] != record["numerical_backend"]:
        raise ValueError("stochastic replay status/source/backend differs")
    # Sparse exponential norm estimation may use different internal probes. Do
    # not turn its work telemetry into a claim of bitwise execution identity.
    def compare(a,b,key=""):
        if isinstance(a,dict) and isinstance(b,dict):
            if set(a) != set(b):
                raise ValueError("stochastic replay fields differ")
            for name in a:
                if name not in {"record_digest","operator_work_units"}:
                    compare(a[name],b[name],name)
        elif isinstance(a,list) and isinstance(b,list):
            if len(a) != len(b):
                raise ValueError("stochastic replay array lengths differ")
            for x,y in zip(a,b,strict=True):
                compare(x,y,key)
        elif type(a) is float and type(b) is float:
            absolute = q.probability_conservation_tolerance if "probability" in key else q.absolute_expectation_tolerance
            if not math.isclose(a,b,rel_tol=q.relative_balance_tolerance,abs_tol=absolute):
                raise ValueError("stochastic replay differs beyond its declared numerical tolerance")
        elif a != b:
            raise ValueError("stochastic replay nonnumeric metadata differs")
    compare(record,replay)
    return {"valid":True,"verification":"current-source bounded numerical replay; operator work telemetry is not execution identity"}


class ProcessHazardInterval(Contract):
    window_id: Name
    start_row_index: int = Field(ge=0)
    finish_row_index: int = Field(gt=0)
    mapping_evidence_id: Name


def prepare_process_hazard_windows(template: StochasticProcessRequest,source_adapter_record,context: RunContext, *,
                                   intervals: tuple[ProcessHazardInterval,...],rows_pointer: str,
                                   row_time_pointer: str,source_time_offset_s: float = 0.0,providers=None):
    """Bind supplied rates to replayed physical-history intervals; fit no hazard.

    JSON pointers select actual retained result rows and their scalar SI second
    clocks. Offset is an explicit caller-reviewed translation on the same clock,
    not a fitted synchronization or a conversion from process values to rates.
    """
    from .adapter_jobs import AdapterJob,verify_adapter_job_record
    from .adapter_campaign import _read,_tokens
    verify_adapter_job_record(source_adapter_record,providers=providers)
    source_job = AdapterJob.model_validate_json(json.dumps(source_adapter_record["job"]))
    q = StochasticProcessRequest.model_validate(template.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    require_context_extension(source_job.context,c)
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("process-hazard template asset/clock differs from the source/receiving context")
    if source_adapter_record["result"].get("status") not in {"computed","computed_diagnostic","ready"}:
        raise ValueError("hazard timing requires an accepted replayed physical-process source")
    if not math.isfinite(source_time_offset_s):
        raise ValueError("process clock offset must be finite seconds")
    rows = _read(source_adapter_record["result"],_tokens(rows_pointer))
    if not isinstance(rows,list) or len(rows) < 2:
        raise ValueError("process-time pointer must select at least two retained physical-history rows")
    times = []
    for row in rows:
        value = _read(row,_tokens(row_time_pointer))
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError("process row time must be a supplied finite SI second scalar")
        times.append(float(value)+source_time_offset_s)
    if times[0] < 0 or any(not math.isfinite(t) for t in times) or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError("translated physical-process times must be nonnegative and strictly increasing")
    mappings = tuple(ProcessHazardInterval.model_validate(x.model_dump(mode="python")) for x in intervals)
    if tuple(x.window_id for x in mappings) != tuple(w.id for w in q.windows):
        raise ValueError("process intervals must identify all hazard windows in their exact order")
    evidence = {e.id:e for e in c.evidence}
    if any(x.mapping_evidence_id not in evidence or evidence[x.mapping_evidence_id].sha256 is None for x in mappings):
        raise ValueError("process-to-rate interval mapping requires source-hash-pinned review evidence")
    data = q.model_dump(mode="json")
    previous_finish = None
    for mapping,window in zip(mappings,data["windows"],strict=True):
        a,b = mapping.start_row_index,mapping.finish_row_index
        if not 0 <= a < b < len(times) or (previous_finish is not None and a != previous_finish):
            raise ValueError("process-hazard intervals must be valid contiguous retained-row boundaries")
        if previous_finish is None and times[a] != q.start_time_s:
            raise ValueError("first mapped process time differs from the hazard process start clock")
        window["duration_s"] = times[b]-times[a]
        window["physical_history_source_digest"] = source_adapter_record["record_digest"]
        previous_finish = b
    data["evidence_ids"] = list(dict.fromkeys((*q.evidence_ids,*(x.mapping_evidence_id for x in mappings))))
    derived = StochasticProcessRequest.model_validate_json(json.dumps(data))
    receipt = dict(schema="pipeline_integrity.process_hazard_binding.v1",
        source_adapter_record_digest=source_adapter_record["record_digest"],source_adapter_id=source_job.adapter_id,
        source_context_digest=digest(source_job.context),template_digest=digest(q),derived_request_digest=digest(derived),
        intervals=[x.model_dump(mode="json") for x in mappings],rows_pointer=rows_pointer,row_time_pointer=row_time_pointer,
        source_time_unit="s",source_time_offset_s=source_time_offset_s,
        transition_hazards_changed_or_estimated=False,implementation=stochastic_process_metadata())
    receipt["record_digest"] = digest(receipt)
    cdata = c.model_dump(mode="python")
    cdata["metadata"] = {**c.metadata,"stochastic_process_history_binding":receipt["record_digest"]}
    return derived,RunContext.model_validate(cdata),receipt


class StochasticProbabilityBinding(Contract):
    id: Name
    selected_state_ids: tuple[Name,...] = Field(min_length=1,max_length=1000)
    measure: Literal["mission_probability","point_unavailability"]
    snapshot_index: int = Field(default=-1,ge=-1,le=1000)
    opportunity_id: Name
    opportunity_definition: Name
    failure_definition: Name
    protection_ids: tuple[Name,...] = ()
    mapping_evidence_id: Name
    state_event_conditioning_and_time_meaning_reviewed: bool = False


def _bound_probability(record,q,binding,context):
    from .risk_exchange import RiskProbability
    b = StochasticProbabilityBinding.model_validate(binding.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    source_context = RunContext.model_validate_json(json.dumps(record["context"]))
    require_context_extension(source_context,c)
    evidence = {e.id:e for e in c.evidence}
    if not b.state_event_conditioning_and_time_meaning_reviewed or b.mapping_evidence_id not in evidence or evidence[b.mapping_evidence_id].sha256 is None:
        raise ValueError("stochastic state→risk mapping requires reviewed time/conditioning and source-pinned evidence")
    selected = set(b.selected_state_ids)
    states = {s.id:s for s in q.states}
    if len(selected) != len(b.selected_state_ids) or not selected <= set(states):
        raise ValueError("stochastic risk mapping requires distinct existing source state IDs")
    if b.measure == "mission_probability" and any(not states[key].absorbing for key in selected):
        raise ValueError("a mission first-hit probability requires selected absorbing states; repairable occupancy is point_unavailability")
    snapshot = len(record["states"])-1 if b.snapshot_index == -1 else b.snapshot_index
    if not 0 <= snapshot < len(record["states"]):
        raise ValueError("stochastic risk snapshot is absent")
    row = record["states"][snapshot]
    probability = math.fsum(row["state_probability"][i] for i,s in enumerate(q.states) if s.id in selected)
    if not 0 <= probability <= 1:
        raise ValueError("stochastic risk probability is outside [0,1]; no projection/normalization applied")
    includes = ["timing","dependency"]
    if any(e.kind == "recovery" and any(w.transition_hazard_per_s[i] > 0 for w in q.windows[:snapshot]) for i,e in enumerate(q.transitions)):
        includes.append("recovery")
    source = dict(probability=probability,process_record_digest=record["record_digest"],
        process_request_digest=digest(q),mapping=b.model_dump(mode="json"),snapshot_index=snapshot,
        source_start_time_s=q.start_time_s,source_finish_time_s=row["time_s"],
        meaning="absorbing_state_first_hit_by_selected_time_including_initial_mass" if b.measure == "mission_probability" else "state_occupancy_at_selected_time",
        process_expected_transition_counts_used_as_probabilities=False,demand_probability_conversion_performed=False)
    required = set(q.evidence_ids)|{x.evidence_id for values in (q.states,q.transitions,q.windows,q.rewards) for x in values}|{b.mapping_evidence_id}
    required |= {w.boundary_mapping_evidence_id for w in q.windows if w.boundary_mapping_evidence_id is not None}
    return RiskProbability(id=b.id,probability=probability,measure=b.measure,condition_id=q.condition_id,
        conditioning=q.conditioning,opportunity_id=b.opportunity_id,opportunity_definition=b.opportunity_definition,
        failure_definition=b.failure_definition,role="derived",includes=tuple(includes),protection_ids=b.protection_ids,
        dependency_groups=(q.id+"/shared_stochastic_states",),evidence_ids=tuple(sorted(required)),qualification="research",
        context=c,source_kind="derived",source_payload=source,source_digest=digest(source))


def probability_from_stochastic_process(record,binding: StochasticProbabilityBinding,context: RunContext):
    """Keep mission first-hit and repairable point-occupancy meanings distinct."""
    verify_stochastic_process_record(record)
    q = StochasticProcessRequest.model_validate_json(json.dumps(record["request"]))
    return _bound_probability(record,q,binding,context)


def prepare_stochastic_period_logic(record,bindings: tuple[StochasticProbabilityBinding,...],root,period,context: RunContext, *,
                                    top_id: str,top_endpoint_id: str,top_definition: str,
                                    logic_evidence_id: str,dependence_evidence_id: str,event_logic_reviewed: bool = False):
    """Feed exact CTMC joint endpoint states to the owner's period Boolean path.

    The demand-only FTA entry point intentionally remains unavailable for these
    mission probabilities. Existing PeriodLogicRequest delegates its Boolean
    joint-state combination to the same owner without changing the measure.
    """
    from .risk_exchange import FaultNode,JointState
    from .period_risk import RiskPeriod,PeriodEventBinding,PeriodLogicRequest
    verify_stochastic_process_record(record)
    q = StochasticProcessRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    p = RiskPeriod.model_validate(period.model_dump(mode="python"))
    tree = FaultNode.model_validate(root.model_dump(mode="python"))
    if not bindings or len(bindings) > 20 or len({b.id for b in bindings}) != len(bindings):
        raise ValueError("period CTMC mapping requires one to twenty distinct bound events")
    events = tuple(_bound_probability(record,q,b,c) for b in bindings)
    snapshots = {e.source_payload["snapshot_index"] for e in events}
    if len(snapshots) != 1 or any(e.measure != "mission_probability" for e in events):
        raise ValueError("period CTMC logic requires mission events at the exact same snapshot")
    snapshot = snapshots.pop()
    if (p.clock_id,p.start_s,p.finish_s,p.common_condition_id,p.common_conditioning) != (
            q.clock_id,q.start_time_s,record["states"][snapshot]["time_s"],q.condition_id,q.conditioning):
        raise ValueError("period conditioning/clock and exact CTMC horizon differ")
    if len({(e.opportunity_id,e.opportunity_definition) for e in events}) != 1:
        raise ValueError("CTMC period event opportunity definitions must agree")
    evidence = {e.id:e for e in c.evidence}
    if any(key not in evidence or evidence[key].sha256 is None for key in (logic_evidence_id,dependence_evidence_id,*p.evidence_ids)):
        raise ValueError("period CTMC Boolean/dependence/period evidence must be source-pinned")
    selected = {b.id:set(b.selected_state_ids) for b in bindings}
    grouped = {}
    for i,state in enumerate(q.states):
        key = tuple((event.id,state.id in selected[event.id]) for event in events)
        grouped.setdefault(key,[]).append(record["states"][snapshot]["state_probability"][i])
    joint = tuple(JointState(states=dict(key),probability=math.fsum(values)) for key,values in grouped.items())
    if not math.isclose(math.fsum(j.probability for j in joint),1,rel_tol=0,abs_tol=1e-12):
        raise ValueError("CTMC joint-state mass does not meet the existing owner's stricter probability budget")
    event_bindings = tuple(PeriodEventBinding(probability=event,source_start_s=p.start_s,source_finish_s=p.finish_s,
        physical_scope_id=q.asset_id,endpoint_id=event.failure_definition,common_condition_id=q.condition_id,
        target_context_digest=digest(c),target_period_digest=digest(p),mapping_evidence_ids=(binding.mapping_evidence_id,),
        period_asset_and_common_conditioning_reviewed=True) for event,binding in zip(events,bindings,strict=True))
    derived = PeriodLogicRequest(root=tree,events=event_bindings,period=p,top_id=top_id,top_endpoint_id=top_endpoint_id,
        top_definition=top_definition,logic_evidence_id=logic_evidence_id,event_logic_reviewed=event_logic_reviewed,
        dependence="joint_states",dependence_evidence_id=dependence_evidence_id,joint_states=joint)
    receipt = dict(schema="pipeline_integrity.stochastic_period_logic_binding.v1",source_record_digest=record["record_digest"],
        derived_request_digest=digest(derived),context_digest=digest(c),exact_joint_states_from_same_process=True,
        independence_assumed=False,demand_probability_conversion_performed=False,
        event_bindings=[b.model_dump(mode="json") for b in bindings],implementation=stochastic_process_metadata())
    receipt["record_digest"] = digest(receipt)
    return derived,receipt
