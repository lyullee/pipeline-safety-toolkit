"""Explicit common-period scope around the existing HRA Boolean probability engine.

An AND of events occurring during a period does not establish simultaneous
unavailability. Lifetime, repair, exposure and consequence models keep their owners.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .risk_exchange import FaultNode, HRARiskClient, JointState, RiskProbability, _checked, _overlap, _unique


class RiskPeriod(Contract):
    id: Name
    clock_id: Name
    start_s: Nonnegative
    finish_s: Positive
    common_condition_id: Name
    common_conditioning: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def window(self):
        if self.finish_s <= self.start_s or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("a positive period and distinct evidence IDs are required")
        return self


class PeriodEventBinding(Contract):
    probability: RiskProbability
    source_start_s: Nonnegative
    source_finish_s: Positive
    physical_scope_id: Name
    endpoint_id: Name
    common_condition_id: Name
    target_context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_period_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_evidence_ids: tuple[Name, ...] = Field(min_length=1)
    period_asset_and_common_conditioning_reviewed: bool = False

    @model_validator(mode="after")
    def window(self):
        if self.source_finish_s <= self.source_start_s or len(set(self.mapping_evidence_ids)) != len(
            self.mapping_evidence_ids
        ):
            raise ValueError("a positive source interval and distinct mapping evidence IDs are required")
        return self


class PeriodLogicRequest(Contract):
    root: FaultNode
    events: tuple[PeriodEventBinding, ...] = Field(min_length=1, max_length=20)
    period: RiskPeriod
    top_id: Name
    top_endpoint_id: Name
    top_definition: Name
    logic_evidence_id: Name
    event_logic_reviewed: bool = False
    dependence: Literal["declared_independent", "joint_states"]
    dependence_evidence_id: Name
    joint_states: tuple[JointState, ...] = ()
    maximum_events: int = Field(default=20, ge=1, le=20)
    maximum_states: int = Field(default=1000000, ge=1, le=1048576)
    maximum_tree_nodes: int = Field(default=10000, ge=1, le=100000)


def _seal(body):
    return {**body, "result_digest": digest(body)}


def _creep_source(event):
    source = event.source_payload
    prediction = source.get("prediction")
    if not isinstance(prediction, dict) or prediction.get("schema") != "pipeline_integrity.creep_exchange.v1":
        return None
    from .creep_exchange import CreepRiskMapping, creep_risk_probability

    mapping = CreepRiskMapping.model_validate_json(json.dumps(source["mapping"]))
    restored = creep_risk_probability(source["prediction"], mapping)
    if restored["status"] != "computed" or restored["probability"] != event.model_dump(mode="json"):
        raise ValueError("period input differs from its original creep prediction/age binding")
    return mapping


def bind_creep_period_event(
    record: dict,
    *,
    period: RiskPeriod,
    target_context: RunContext,
    mapping_evidence_ids: tuple[str, ...],
    common_conditioning_reviewed: bool = False,
):
    """Retain a complete creep risk binding; the caller reviews conditioning on the full system state."""
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("creep risk binding changed after sealing")
    if record.get("schema") != "pipeline_integrity.creep_risk_binding.v1" or record["status"] != "computed":
        raise ValueError("a computed complete creep risk binding is required")
    event = RiskProbability.model_validate_json(json.dumps(record["probability"]))
    mapping = _creep_source(event)
    if mapping is None or mapping.model_dump(mode="json") != record["mapping"]:
        raise ValueError("complete creep source mapping is required")
    return PeriodEventBinding(
        probability=event,
        source_start_s=mapping.period_start_s,
        source_finish_s=mapping.period_finish_s,
        physical_scope_id=mapping.physical_scope_id,
        endpoint_id=mapping.endpoint,
        common_condition_id=period.common_condition_id,
        target_context_digest=digest(target_context),
        target_period_digest=digest(period),
        mapping_evidence_ids=mapping_evidence_ids,
        period_asset_and_common_conditioning_reviewed=common_conditioning_reviewed,
    )


def _source_scope(binding):
    event = binding.probability
    original = _creep_source(event)
    if original is not None and (
        original.period_start_s,
        original.period_finish_s,
        original.physical_scope_id,
        original.endpoint,
    ) != (binding.source_start_s, binding.source_finish_s, binding.physical_scope_id, binding.endpoint_id):
        raise ValueError("creep period, physical scope or endpoint differs from its source")
    source = event.source_payload
    if source.get("schema") == "pipeline_integrity.period_logic_source.v1":
        period = source["period"]
        if digest(period) != binding.target_period_digest or (
            period["start_s"],
            period["finish_s"],
            period["clock_id"],
            period["common_condition_id"],
            source["physical_scope_id"],
            source["endpoint_id"],
        ) != (
            binding.source_start_s,
            binding.source_finish_s,
            event.context.clock_id,
            binding.common_condition_id,
            binding.physical_scope_id,
            binding.endpoint_id,
        ):
            raise ValueError("derived period-event scope differs from its original calculation")
        return source["atomic_event_ids"], source["atomic_scope_digests"]
    physical = dict(
        asset_id=event.context.asset_id,
        clock_id=event.context.clock_id,
        physical_scope_id=binding.physical_scope_id,
        endpoint_id=binding.endpoint_id,
        start_s=binding.source_start_s,
        finish_s=binding.source_finish_s,
    )
    return [event.id], [digest(physical)]


def evaluate_period_logic(request: PeriodLogicRequest, context: RunContext, *, client=None):
    """Call the owner for a static Boolean combination of explicitly common-conditioned period events."""
    request, context = _checked(request, PeriodLogicRequest), _checked(context, RunContext)
    period = request.period
    if context.clock_id != period.clock_id:
        raise ValueError("period and target context clocks differ")
    bindings = {b.probability.id: b for b in request.events}
    if len(bindings) != len(request.events) or request.top_id in bindings:
        raise ValueError("distinct input identities and a separate top-event ID are required")
    used, nodes = set(), 0

    def inspect(node, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > request.maximum_tree_nodes:
            raise ValueError("period event-tree structure budget exceeded")
        if node.kind == "event":
            used.add(node.event_id)
        for child in node.children:
            inspect(child, depth + 1)

    inspect(request.root)
    if used != set(bindings):
        raise ValueError("period bindings must match distinct Boolean event IDs exactly")
    if (
        len(bindings) > request.maximum_events
        or (request.dependence == "declared_independent" and 2 ** len(bindings) > request.maximum_states)
        or len(request.joint_states) > request.maximum_states
    ):
        raise ValueError("period event probability-state budget exceeded")
    target_evidence = {e.id: e for e in context.evidence}
    if len(target_evidence) != len(context.evidence):
        raise ValueError("target evidence identifiers must be unique")
    required = {request.logic_evidence_id, request.dependence_evidence_id, *period.evidence_ids}
    issues = [] if request.event_logic_reviewed else ["period Boolean event logic requires review"]
    values, groups, atomic_ids, atomic_scopes = {}, [], [], []
    for key, binding in bindings.items():
        event = binding.probability
        if binding.target_context_digest != digest(context):
            raise ValueError("period asset/scenario mapping targets a different complete context")
        if binding.target_period_digest != digest(period):
            raise ValueError("period/survival definition changed after its mapping review")
        if event.context.clock_id != period.clock_id:
            raise ValueError("source event clock differs; no implicit time transform is available")
        source_evidence = {e.id: e for e in event.context.evidence}
        if len(source_evidence) != len(event.context.evidence):
            raise ValueError("source evidence identifiers must be unique")
        for evidence_id in event.evidence_ids:
            if (
                evidence_id not in source_evidence
                or target_evidence.get(evidence_id) != source_evidence[evidence_id]
            ):
                raise ValueError("source evidence must exist unchanged in source and target contexts")
        required.update((*event.evidence_ids, *binding.mapping_evidence_ids))
        if event.measure != "mission_probability":
            issues.append(f"{key}: a period event requires mission_probability")
        if (binding.source_start_s, binding.source_finish_s) != (period.start_s, period.finish_s):
            issues.append(f"{key}: source interval differs from the exact common period")
        if binding.common_condition_id != period.common_condition_id:
            issues.append(f"{key}: common conditioning differs")
        if not binding.period_asset_and_common_conditioning_reviewed:
            issues.append(f"{key}: period/asset/common-survival conditioning mapping requires review")
        ancestors, scopes = _source_scope(binding)
        if set(ancestors).intersection(atomic_ids) or set(scopes).intersection(atomic_scopes):
            issues.append(
                f"{key}: atomic source already included; expand the tree using distinct atomic events"
            )
        atomic_ids.extend(ancestors)
        atomic_scopes.extend(scopes)
        values[key] = event.probability
        groups.extend(event.dependency_groups)
    if request.top_id in atomic_ids:
        raise ValueError("top-event identity cannot replace an atomic ancestor")
    if required - target_evidence.keys():
        raise ValueError("period, mapping, logic or dependence evidence is absent from target context")
    issues.extend(_overlap(b.probability for b in bindings.values()))
    if request.dependence == "declared_independent":
        if request.joint_states:
            issues.append("independent evaluation cannot also supply joint states")
        if len(groups) != len(set(groups)):
            issues.append("shared model/dependency group requires explicit joint states")
    elif not request.joint_states:
        issues.append("joint states are missing")
    else:
        seen = set()
        for row in request.joint_states:
            if set(row.states) != set(bindings) or tuple(sorted(row.states.items())) in seen:
                raise ValueError("joint states require every distinct event once per unique state")
            seen.add(tuple(sorted(row.states.items())))
        if not math.isclose(
            math.fsum(r.probability for r in request.joint_states), 1, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("joint period-state probabilities must sum to one")
        for key, probability in values.items():
            marginal = math.fsum(r.probability for r in request.joint_states if r.states[key])
            if not math.isclose(marginal, probability, abs_tol=0, rel_tol=1e-10):
                issues.append(f"{key}: joint marginal differs from bound period probability")
    body = dict(
        schema="pipeline_integrity.period_logic.v1",
        status="withheld" if issues else "exchange_checked",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        input_digest=digest(request),
        provider=None,
        native_result=None,
        probability=None,
        issues=issues,
        qualification="research",
        temporal_coincidence_or_sequence_modelled=False,
        repair_or_repeated_failure_modelled=False,
        annual_frequency_inferred=False,
        loss_of_containment_inferred=False,
        field_validation_claim=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if issues:
        return _seal(body)
    owner = HRARiskClient() if client is None else client

    def compile_node(node):
        if node.kind == "event":
            return owner.api.Event(node.event_id)
        return owner.api.Gate(node.kind, tuple(compile_node(c) for c in node.children), node.threshold)

    tree = compile_node(request.root)
    value = (
        owner.api.fault_probability(tree, values)
        if request.dependence == "declared_independent"
        else owner.api.fault_probability_joint(
            tree, tuple((r.states, r.probability) for r in request.joint_states)
        )
    )
    source = dict(
        schema="pipeline_integrity.period_logic_source.v1",
        probability=value,
        period=period.model_dump(mode="json"),
        physical_scope_id=context.asset_id,
        endpoint_id=request.top_endpoint_id,
        atomic_event_ids=_unique(atomic_ids),
        atomic_scope_digests=_unique(atomic_scopes),
        request=request.model_dump(mode="json"),
        provider=owner.provider,
    )
    result = RiskProbability(
        id=request.top_id,
        probability=value,
        measure="mission_probability",
        condition_id=period.common_condition_id,
        conditioning=period.common_conditioning,
        opportunity_id=period.id,
        opportunity_definition=f"Boolean events in [{period.start_s}, {period.finish_s}] s on clock {period.clock_id}",
        failure_definition=request.top_definition,
        role="derived",
        includes=_unique(x for b in bindings.values() for x in b.probability.includes),
        protection_ids=_unique(x for b in bindings.values() for x in b.probability.protection_ids),
        dependency_groups=_unique(groups),
        evidence_ids=tuple(sorted(required)),
        qualification="research",
        context=context,
        source_kind="derived",
        source_payload=source,
        source_digest=digest(source),
    )
    body.update(
        provider=owner.provider,
        native_result=dict(top_probability=value),
        probability=result.model_dump(mode="json"),
    )
    return _seal(body)
