"""Evidence and conditioning boundaries around application-owned FTA, ETA and LOPA engines."""

import json
import math
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest


class RiskProbability(Contract):
    id: Name
    probability: Fraction
    measure: Literal["demand_failure_probability", "mission_probability", "point_unavailability"]
    condition_id: Name
    conditioning: Name
    opportunity_id: Name
    opportunity_definition: Name
    failure_definition: Name
    role: Literal["initiating", "latent", "response", "equipment", "derived"]
    includes: tuple[Literal["timing", "recovery", "dependency"], ...] = ()
    protection_ids: tuple[Name, ...] = ()
    dependency_groups: tuple[Name, ...] = ()
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    qualification: Literal["assumed", "empirical", "method_estimate", "research"]
    context: RunContext
    source_kind: Literal["human_reliability", "declared", "derived"]
    source_payload: dict
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def source(self):
        if any(
            len(v) != len(set(v))
            for v in (self.includes, self.protection_ids, self.dependency_groups, self.evidence_ids)
        ):
            raise ValueError("probability scope identifiers must not repeat")
        if digest(self.source_payload) != self.source_digest:
            raise ValueError("the probability source payload changed")
        if self.source_kind == "human_reliability":
            estimate, event = self.source_payload["estimate"], self.source_payload["event"]
            if (
                self.id,
                self.probability,
                self.conditioning,
                self.opportunity_definition,
                self.failure_definition,
                self.role,
                self.qualification,
            ) != (
                estimate["event_id"],
                estimate["probability"],
                estimate["conditioning"],
                event["opportunity_definition"],
                event["failure_definition"],
                event["role"],
                estimate["status"],
            ):
                raise ValueError("the human estimate meaning or qualification was changed")
            if (
                event["id"] != self.id
                or event["conditioning"] != self.conditioning
                or tuple(estimate["includes"]) != self.includes
                or tuple(estimate["evidence_ids"]) != self.evidence_ids
            ):
                raise ValueError("the human event scope differs from its original estimate")
            if any(
                event.get(f"includes_{effect}") and effect not in self.includes
                for effect in ("timing", "recovery")
            ):
                raise ValueError("the native human event requires a missing included effect")
        elif self.source_payload.get("probability") != self.probability:
            raise ValueError("the declared or derived source must preserve its probability")
        _seal(self)
        return self


class DemandExposure(Contract):
    opportunities_per_year: Nonnegative
    opportunity_id: Name
    opportunity_definition: Name
    condition_id: Name
    evidence_id: Name


class RiskFrequency(Contract):
    id: Name
    annual_frequency_per_year: Nonnegative
    unit: Literal["year^-1"] = "year^-1"
    condition_id: Name
    endpoint: Literal[
        "event_occurrence",
        "protection_demand",
        "protection_failure_combination",
        "uncontrolled_process_state",
        "loss_of_containment",
        "consequence_outcome",
    ]
    consumed_event_ids: tuple[Name, ...] = ()
    consumed_protection_ids: tuple[Name, ...] = ()
    dependency_groups: tuple[Name, ...] = ()
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    context: RunContext
    source_payload: dict
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    observed_annual_frequency: Literal[False] = False
    field_validation_claim: Literal[False] = False

    @model_validator(mode="after")
    def source(self):
        if (
            digest(self.source_payload) != self.source_digest
            or self.source_payload.get("annual_frequency_per_year") != self.annual_frequency_per_year
        ):
            raise ValueError("the frequency and its source payload differ")
        if any(
            len(v) != len(set(v))
            for v in (
                self.consumed_event_ids,
                self.consumed_protection_ids,
                self.dependency_groups,
                self.evidence_ids,
            )
        ):
            raise ValueError("frequency lineage identifiers must not repeat")
        _seal(self)
        return self


class FaultNode(Contract):
    kind: Literal["event", "AND", "OR", "NOT", "VOTE"]
    event_id: Name | None = None
    children: tuple["FaultNode", ...] = ()
    threshold: int | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "event":
            if self.event_id is None or self.children or self.threshold is not None:
                raise ValueError("an event node needs only its event ID")
        elif self.event_id is not None or not self.children:
            raise ValueError("a gate requires children and no event ID")
        if self.kind == "NOT" and len(self.children) != 1:
            raise ValueError("NOT requires one child")
        if self.kind == "VOTE":
            if self.threshold is None or not 1 <= self.threshold <= len(self.children):
                raise ValueError("VOTE requires a threshold within its children")
        elif self.threshold is not None:
            raise ValueError("threshold belongs to VOTE")
        return self


class JointState(Contract):
    states: dict[str, bool]
    probability: Fraction


class JointStateCount(Contract):
    states: dict[str, bool]
    count: int = Field(ge=1)


class JointStateCountTable(Contract):
    event_ids: tuple[Name, ...] = Field(min_length=2, max_length=20)
    rows: tuple[JointStateCount, ...] = Field(min_length=1, max_length=1048576)
    condition_id: Name
    opportunity_id: Name
    opportunity_definition: Name
    population_id: Name
    grouping_unit: Name
    evidence_id: Name
    source_payload: dict
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def complete_states(self):
        if (
            len(set(self.event_ids)) != len(self.event_ids)
            or digest(self.source_payload) != self.source_digest
        ):
            raise ValueError("joint count event IDs or source payload changed")
        expected = set(self.event_ids)
        states = []
        for row in self.rows:
            if set(row.states) != expected or any(type(value) is not bool for value in row.states.values()):
                raise ValueError("every joint count row must cover the exact Boolean event set")
            states.append(tuple((key, row.states[key]) for key in self.event_ids))
        if len(states) != len(set(states)):
            raise ValueError("joint count states must not repeat")
        return self


def joint_states_from_counts(table: JointStateCountTable, *, maximum_states=1048576):
    """Normalize an observed exhaustive-state count table; fit no common-cause model."""
    table = _checked(table, JointStateCountTable)
    if type(maximum_states) is not int or not 1 <= maximum_states <= 1048576:
        raise ValueError("explicit joint-state work budget is required")
    if len(table.rows) > maximum_states:
        raise ValueError("joint count table exceeds the state work budget")
    total = sum(row.count for row in table.rows)
    states = tuple(JointState(states=row.states, probability=row.count / total) for row in table.rows)
    marginals = {
        event_id: math.fsum(state.probability for state in states if state.states[event_id])
        for event_id in table.event_ids
    }
    body = {
        "schema": "pipeline_integrity.joint_state_count_receipt.v1",
        "table": table.model_dump(mode="json"),
        "table_digest": digest(table),
        "total_observed_groups": total,
        "joint_states": [state.model_dump(mode="json") for state in states],
        "marginal_probability_by_event": marginals,
        "normalization_only": True,
        "common_cause_model_fitted": False,
        "sampling_uncertainty_quantified": False,
        "field_validation_claim": False,
    }
    return states, {**body, "record_digest": digest(body)}


def verify_joint_state_count_receipt(receipt, table, *, maximum_states=1048576):
    """Replay observed-count normalization against a caller-supplied original table."""
    if not isinstance(receipt, dict) or receipt.get("record_digest") != digest(
        {key: value for key, value in receipt.items() if key != "record_digest"}
    ):
        raise ValueError("joint state count receipt seal changed")
    _, replayed = joint_states_from_counts(table, maximum_states=maximum_states)
    if replayed != receipt:
        raise ValueError("joint state count source or normalization differs on replay")
    return True


def fault_tree_request_from_verified_joint_counts(
    root,
    events,
    table,
    receipt,
    *,
    maximum_events=20,
    maximum_states=1048576,
    maximum_tree_nodes=10000,
):
    """Create an FTA request only when caller counts replay against their sealed receipt.

    This is an explicit verification boundary around the existing FTA adapter. It does not
    estimate common-cause effects or quantify sampling uncertainty.
    """
    checked_table = _checked(table, JointStateCountTable)
    verify_joint_state_count_receipt(
        receipt, checked_table, maximum_states=maximum_states
    )
    request, rebuilt_receipt = fault_tree_request_from_joint_counts(
        root,
        events,
        checked_table,
        maximum_events=maximum_events,
        maximum_states=maximum_states,
        maximum_tree_nodes=maximum_tree_nodes,
    )
    if rebuilt_receipt != receipt:
        raise ValueError("verified joint-count receipt differs from the FTA input table")
    if request.joint_state_receipt_digest != receipt["record_digest"]:
        raise ValueError("FTA request does not retain the verified joint-count receipt")
    return request, receipt


def fault_tree_request_from_joint_counts(
    root,
    events,
    table,
    *,
    maximum_events=20,
    maximum_states=1048576,
    maximum_tree_nodes=10000,
):
    """Bind an observed joint count receipt to the existing owner FTA request."""
    checked_events = tuple(_checked(event, RiskProbability) for event in events)
    checked_table = _checked(table, JointStateCountTable)
    if set(checked_table.event_ids) != {event.id for event in checked_events}:
        raise ValueError("joint count event IDs differ from the bound risk probabilities")
    for event in checked_events:
        if (event.condition_id, event.opportunity_id, event.opportunity_definition) != (
            checked_table.condition_id,
            checked_table.opportunity_id,
            checked_table.opportunity_definition,
        ):
            raise ValueError("joint counts and event probabilities use different opportunity scopes")
        if checked_table.evidence_id not in {item.id for item in event.context.evidence}:
            raise ValueError("joint-count evidence is absent from an event context")
    states, receipt = joint_states_from_counts(checked_table, maximum_states=maximum_states)
    request = FaultTreeRequest(
        root=root,
        events=checked_events,
        condition_id=checked_table.condition_id,
        opportunity_id=checked_table.opportunity_id,
        dependence="joint_states",
        dependence_evidence_id=checked_table.evidence_id,
        joint_states=states,
        joint_state_receipt_digest=receipt["record_digest"],
        maximum_events=maximum_events,
        maximum_states=maximum_states,
        maximum_tree_nodes=maximum_tree_nodes,
    )
    return request, receipt


class FaultTreeRequest(Contract):
    root: FaultNode
    events: tuple[RiskProbability, ...] = Field(min_length=1)
    condition_id: Name
    opportunity_id: Name
    dependence: Literal["declared_independent", "joint_states"]
    dependence_evidence_id: Name
    joint_states: tuple[JointState, ...] = ()
    joint_state_receipt_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    maximum_events: int = Field(default=20, ge=1, le=20)
    maximum_states: int = Field(default=1000000, ge=1, le=1048576)
    maximum_tree_nodes: int = Field(default=10000, ge=1, le=100000)


class EventBranch(Contract):
    id: Name
    probability_id: Name
    complement: bool = False
    next_condition_id: Name
    children: tuple["EventBranch", ...] = ()
    consequence_id: Name | None = None

    @model_validator(mode="after")
    def terminal(self):
        if bool(self.children) == (self.consequence_id is not None):
            raise ValueError(
                "only a terminal event branch requires a consequence, including no-harm outcomes"
            )
        return self


class EventTreeRequest(Contract):
    branches: tuple[EventBranch, ...] = Field(min_length=1)
    events: tuple[RiskProbability, ...] = Field(min_length=1)
    partition_evidence_id: Name
    maximum_branches: int = Field(default=10000, ge=1, le=100000)


class ProtectionLayer(Contract):
    id: Name
    probability_id: Name
    kind: Literal["human", "automatic", "passive"]
    independence_evidence_id: str = ""
    response_feasible: bool | None = None


def _seal(value):
    expected = digest(value.model_dump(mode="json", exclude={"binding_digest"}))
    if value.binding_digest is None:
        object.__setattr__(value, "binding_digest", expected)
    elif value.binding_digest != expected:
        raise ValueError("the bound probability/frequency scope or lineage changed")


def _checked(value, cls):
    return cls.model_validate_json(value.model_dump_json())


def _unique(values):
    return tuple(dict.fromkeys(values))


def _indices(events):
    checked = tuple(_checked(e, RiskProbability) for e in events)
    if len({e.id for e in checked}) != len(checked):
        raise ValueError("event identities must be unique in the evidence table")
    return {e.id: e for e in checked}


def _context(event, context):
    if event.context != context:
        raise ValueError("event/rate context differs; an explicit asset/scenario/clock mapping is required")


def _overlap(events, *, initial_protection=(), initial_events=()):
    protections = set(initial_protection)
    identities = set(initial_events)
    issues = []
    for event in events:
        if event.id in identities:
            issues.append(f"{event.id}: event already consumed on this path")
        if protections.intersection(event.protection_ids):
            issues.append(f"{event.id}: protection effect already consumed")
        identities.add(event.id)
        protections.update(event.protection_ids)
    return issues


@dataclass(frozen=True)
class RiskHandoff:
    native: Any
    _record: dict

    def record(self):
        payload = json_value(
            self.native.model_dump(mode="json") if hasattr(self.native, "model_dump") else self.native
        )
        body = {k: v for k, v in self._record.items() if k != "result_digest"}
        if digest(payload) != body["native_payload_digest"] or digest(body) != self._record["result_digest"]:
            raise ValueError("the receiving demand packet or its evidence record changed")
        return json.loads(json.dumps(self._record, allow_nan=False))


class HRARiskClient:
    def __init__(self, *, api=None, provider=None):
        if api is None:
            import human_reliability as hra
            from human_reliability.contracts import Estimate, HumanFailureEvent

            adapters, barriers, event_tree, fault_tree, probability = (
                import_module(f"human_reliability.{name}")
                for name in ("adapters", "barriers", "event_tree", "fault_tree", "probability")
            )
            api = SimpleNamespace(
                Event=fault_tree.Event,
                Gate=fault_tree.Gate,
                fault_probability=fault_tree.fault_probability,
                fault_probability_joint=fault_tree.fault_probability_joint,
                Branch=event_tree.Branch,
                event_tree=event_tree.event_tree,
                Barrier=barriers.Barrier,
                lopa_screen=barriers.lopa_screen,
                demand_frequency=probability.demand_frequency,
                Estimate=Estimate,
                HumanFailureEvent=HumanFailureEvent,
                prism_demand=adapters.prism_demand,
            )
            root = Path(hra.__file__).parent
            provider = {
                "id": "human-reliability-toolkit",
                "version": hra.__version__,
                "source_digest": digest({p.name: file_digest(p) for p in sorted(root.glob("*.py"))}),
            }
        if provider is None:
            raise ValueError("an injected owner API needs explicit identity and version metadata")
        self.api, self.provider = api, json_value(provider)

    def _result(self, kind, context, inputs, native, *, issues=(), probability=None, frequencies=()):
        body = {
            "schema": "pipeline_integrity.risk_exchange.v1",
            "kind": kind,
            "status": "withheld" if issues else "exchange_checked",
            "context": context.model_dump(mode="json"),
            "inputs": json_value(inputs),
            "input_digest": digest(inputs),
            "provider": self.provider,
            "native_result": json_value(native),
            "probability": None if probability is None else probability.model_dump(mode="json"),
            "frequencies": [r.model_dump(mode="json") for r in frequencies],
            "issues": list(issues),
            "qualification": "research",
            "field_validation_claim": False,
            "implementation_sha256": file_digest(Path(__file__)),
        }
        return {**body, "result_digest": digest(body)}

    def bind_human_estimate(self, estimate, event, *, condition_id, opportunity_id, context, protection_ids):
        native_estimate = self.api.Estimate.model_validate_json(estimate.model_dump_json())
        native_event = self.api.HumanFailureEvent.model_validate_json(event.model_dump_json())
        if native_event.role == "response" and not protection_ids:
            raise ValueError("a human response requires explicit IDs for its credited protection effects")
        source = {
            "estimate": native_estimate.model_dump(mode="json"),
            "event": native_event.model_dump(mode="json"),
            "provider": self.provider,
        }
        return RiskProbability(
            id=native_event.id,
            probability=native_estimate.probability,
            measure="demand_failure_probability",
            condition_id=condition_id,
            conditioning=native_event.conditioning,
            opportunity_id=opportunity_id,
            opportunity_definition=native_event.opportunity_definition,
            failure_definition=native_event.failure_definition,
            role=native_event.role,
            includes=native_estimate.includes,
            protection_ids=protection_ids,
            dependency_groups=()
            if native_event.dependency_group is None
            else (native_event.dependency_group,),
            evidence_ids=native_estimate.evidence_ids,
            qualification=native_estimate.status,
            context=context,
            source_kind="human_reliability",
            source_payload=source,
            source_digest=digest(source),
        )

    def annualize(self, event: RiskProbability, exposure: DemandExposure | None):
        event = _checked(event, RiskProbability)
        if exposure is None:
            return self._result(
                "annualize", event.context, event, None, issues=("documented opportunity exposure required",)
            )
        exposure = _checked(exposure, DemandExposure)
        issues = []
        if event.measure != "demand_failure_probability":
            issues.append("probability measure is incompatible with opportunity exposure")
        if (event.condition_id, event.opportunity_id, event.opportunity_definition) != (
            exposure.condition_id,
            exposure.opportunity_id,
            exposure.opportunity_definition,
        ):
            issues.append("exposure does not match the event conditioning and opportunity definition")
        if issues:
            return self._result("annualize", event.context, (event, exposure), None, issues=issues)
        value = self.api.demand_frequency(exposure.opportunities_per_year, event.probability)
        source = {
            "annual_frequency_per_year": value,
            "event": event.model_dump(mode="json"),
            "exposure": exposure.model_dump(mode="json"),
            "provider": self.provider,
        }
        frequency = RiskFrequency(
            id=f"{event.id}:expected_annual_count",
            annual_frequency_per_year=value,
            condition_id=event.condition_id,
            endpoint="event_occurrence",
            consumed_event_ids=(event.id,),
            consumed_protection_ids=event.protection_ids,
            dependency_groups=event.dependency_groups,
            evidence_ids=_unique((*event.evidence_ids, exposure.evidence_id)),
            context=event.context,
            source_payload=source,
            source_digest=digest(source),
        )
        return self._result(
            "annualize",
            event.context,
            (event, exposure),
            {"annual_frequency_per_year": value},
            frequencies=(frequency,),
        )

    def fault_tree(self, request: FaultTreeRequest, context: RunContext):
        request = _checked(request, FaultTreeRequest)
        events = _indices(request.events)
        used = set()
        count = 0

        def compile_node(node, depth=0):
            nonlocal count
            count += 1
            if depth > 64 or count > request.maximum_tree_nodes:
                raise ValueError("fault-tree structure budget exceeded")
            if node.kind == "event":
                used.add(node.event_id)
                return self.api.Event(node.event_id)
            return self.api.Gate(
                node.kind, tuple(compile_node(c, depth + 1) for c in node.children), node.threshold
            )

        tree = compile_node(request.root)
        if used != set(events):
            raise ValueError("fault-tree evidence must match its distinct event IDs exactly")
        if (
            len(events) > request.maximum_events
            or (request.dependence == "declared_independent" and 2 ** len(events) > request.maximum_states)
            or len(request.joint_states) > request.maximum_states
        ):
            raise ValueError("fault-tree probability state budget exceeded")
        issues = _overlap(events.values())
        for event in events.values():
            _context(event, context)
            if event.measure != "demand_failure_probability" or (
                event.condition_id,
                event.opportunity_id,
            ) != (request.condition_id, request.opportunity_id):
                issues.append(f"{event.id}: incompatible measure or conditioning/opportunity scope")
        definitions = {e.opportunity_definition for e in events.values()}
        if len(definitions) != 1:
            issues.append("opportunity IDs have conflicting definitions")
        groups = []
        for event in events.values():
            groups.extend(event.dependency_groups)
        if request.dependence == "declared_independent":
            if request.joint_states:
                issues.append("independent evaluation cannot also supply joint states")
            if len(set(groups)) != len(groups):
                issues.append("shared dependency requires an explicit joint model")
        elif not request.joint_states:
            issues.append("joint states are missing")
        else:
            for row in request.joint_states:
                if set(row.states) != set(events):
                    raise ValueError("joint state keys must match distinct events")
            states = {tuple(sorted(row.states.items())) for row in request.joint_states}
            if len(states) != len(request.joint_states):
                raise ValueError("joint states must not repeat")
            if not math.isclose(
                math.fsum(row.probability for row in request.joint_states), 1.0, abs_tol=1e-12, rel_tol=0
            ):
                raise ValueError("joint state probabilities must sum to one")
            for key, event in events.items():
                marginal = math.fsum(r.probability for r in request.joint_states if r.states[key])
                if not math.isclose(marginal, event.probability, abs_tol=0, rel_tol=1e-10):
                    issues.append(f"{key}: joint marginal differs from the bound probability")
        if issues:
            return self._result("fault_tree", context, request, None, issues=issues)
        value = (
            self.api.fault_probability(tree, {k: e.probability for k, e in events.items()})
            if request.dependence == "declared_independent"
            else self.api.fault_probability_joint(
                tree, tuple((r.states, r.probability) for r in request.joint_states)
            )
        )
        source = {
            "probability": value,
            "kind": "fault_tree",
            "request_digest": digest(request),
            "request": request.model_dump(mode="json"),
            "provider": self.provider,
        }
        result = RiskProbability(
            id="fault_tree_top",
            probability=value,
            measure="demand_failure_probability",
            condition_id=request.condition_id,
            conditioning="Declared fault-tree conditional case",
            opportunity_id=request.opportunity_id,
            opportunity_definition=next(iter(definitions)),
            failure_definition="Declared Boolean top event",
            role="derived",
            includes=_unique(effect for e in events.values() for effect in e.includes),
            protection_ids=_unique(p for e in events.values() for p in e.protection_ids),
            dependency_groups=_unique(groups),
            evidence_ids=_unique(
                (*[p for e in events.values() for p in e.evidence_ids], request.dependence_evidence_id)
            ),
            qualification="research",
            context=context,
            source_kind="derived",
            source_payload=source,
            source_digest=digest(source),
        )
        return self._result("fault_tree", context, request, {"top_probability": value}, probability=result)

    def event_tree(self, rate: RiskFrequency, request: EventTreeRequest):
        rate = _checked(rate, RiskFrequency)
        request = _checked(request, EventTreeRequest)
        events = _indices(request.events)
        used = set()
        paths = {}
        issues = []
        ids = set()
        count = 0

        def build(branch, current_condition, path, consumed, protections, depth=0):
            nonlocal count
            count += 1
            if depth > 64 or count > request.maximum_branches:
                raise ValueError("event-tree structure budget exceeded")
            if branch.id in ids:
                raise ValueError("branch IDs must be globally unique")
            ids.add(branch.id)
            if branch.probability_id not in events:
                raise ValueError("event-tree branch probability is absent")
            event = events[branch.probability_id]
            used.add(event.id)
            _context(event, rate.context)
            if event.condition_id != current_condition or event.measure != "demand_failure_probability":
                issues.append(f"{branch.id}: conditional probability does not match its parent path")
            issues.extend(_overlap((event,), initial_protection=protections, initial_events=consumed))
            current = path + (branch.id,)
            next_events = (*consumed, event.id)
            next_protection = (*protections, *event.protection_ids)
            children = tuple(
                build(b, branch.next_condition_id, current, next_events, next_protection, depth + 1)
                for b in branch.children
            )
            if not children:
                paths[current] = (branch, event, next_events, next_protection)
            return self.api.Branch(
                branch.id,
                1 - event.probability if branch.complement else event.probability,
                children=children,
                consequence_id=branch.consequence_id,
                evidence=digest(event),
            )

        branches = tuple(
            build(b, rate.condition_id, (), rate.consumed_event_ids, rate.consumed_protection_ids)
            for b in request.branches
        )
        if used != set(events):
            raise ValueError("event-tree evidence contains unused probabilities")
        if issues:
            return self._result("event_tree", rate.context, (rate, request), None, issues=issues)
        native = self.api.event_tree(rate.annual_frequency_per_year, branches)
        if len(native) != len(paths) or {tuple(r["path"]) for r in native} != set(paths):
            raise ValueError("owner event-tree paths differ from requested terminal paths")
        error = math.fsum(r["annual_frequency_per_year"] for r in native) - rate.annual_frequency_per_year
        if not math.isclose(
            math.fsum(r["annual_frequency_per_year"] for r in native),
            rate.annual_frequency_per_year,
            abs_tol=0,
            rel_tol=1e-12,
        ):
            raise ValueError("the event tree did not conserve initiating frequency")
        frequencies = []
        for row in native:
            branch, event, consumed, protections = paths[tuple(row["path"])]
            if row.get("consequence_id") != branch.consequence_id:
                raise ValueError("the owner changed the terminal consequence identity")
            source = {
                "annual_frequency_per_year": row["annual_frequency_per_year"],
                "native_row": json_value(row),
                "initiating_digest": digest(rate),
                "request_digest": digest(request),
                "initiating": rate.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                "provider": self.provider,
            }
            frequencies.append(
                RiskFrequency(
                    id="/".join(row["path"]),
                    annual_frequency_per_year=row["annual_frequency_per_year"],
                    condition_id=branch.next_condition_id,
                    endpoint="consequence_outcome",
                    consumed_event_ids=consumed,
                    consumed_protection_ids=protections,
                    dependency_groups=_unique(
                        (
                            *rate.dependency_groups,
                            *[g for x in consumed if x in events for g in events[x].dependency_groups],
                        )
                    ),
                    evidence_ids=_unique(
                        (
                            *rate.evidence_ids,
                            *[e for x in consumed if x in events for e in events[x].evidence_ids],
                            request.partition_evidence_id,
                        )
                    ),
                    context=rate.context,
                    source_payload=source,
                    source_digest=digest(source),
                )
            )
        return self._result(
            "event_tree",
            rate.context,
            (rate, request),
            {"rows": native, "frequency_conservation_error_per_year": error},
            frequencies=tuple(frequencies),
        )

    def lopa(
        self, rate: RiskFrequency, events: tuple[RiskProbability, ...], layers: tuple[ProtectionLayer, ...]
    ):
        rate = _checked(rate, RiskFrequency)
        layers = tuple(_checked(layer, ProtectionLayer) for layer in layers)
        bound = _indices(events)
        if not layers or len({p.id for p in layers}) != len(layers):
            raise ValueError("distinct protection layer IDs are required")
        if {p.probability_id for p in layers} != set(bound) or len({p.probability_id for p in layers}) != len(
            layers
        ):
            raise ValueError("each layer needs one distinct bound event")
        issues = _overlap(
            bound.values(),
            initial_protection=rate.consumed_protection_ids,
            initial_events=rate.consumed_event_ids,
        )
        if rate.endpoint != "protection_demand":
            issues.append("additional preventive protection requires a protection-demand boundary")
        native_layers = []
        for layer in layers:
            event = bound[layer.probability_id]
            _context(event, rate.context)
            if event.measure != "demand_failure_probability" or event.condition_id != rate.condition_id:
                issues.append(f"{layer.id}: incompatible conditioning or probability measure")
            if layer.id not in event.protection_ids:
                issues.append(f"{layer.id}: the event does not identify the credited protection")
            if (layer.kind == "human" and event.role != "response") or (
                layer.kind != "human" and event.role != "equipment"
            ):
                issues.append(f"{layer.id}: the layer kind conflicts with its bound event role")
            native_layers.append(
                self.api.Barrier(
                    layer.id,
                    layer.kind,
                    event.probability,
                    evidence=digest(event),
                    independence_evidence=layer.independence_evidence_id,
                    dependency_groups=event.dependency_groups,
                    response_feasible=layer.response_feasible,
                )
            )
        if issues:
            return self._result("lopa", rate.context, (rate, events, layers), None, issues=issues)
        native = self.api.lopa_screen(
            rate.annual_frequency_per_year,
            tuple(native_layers),
            initiating_dependency_groups=rate.dependency_groups,
        )
        if native.get("status") not in {"withheld", "declared_independent_screen"}:
            raise ValueError("the owner returned an unrecognized protection-credit status")
        reasons = (
            tuple(native.get("reasons", ())) or ("owner withheld independent protection credit",)
            if native.get("status") == "withheld"
            else ()
        )
        frequencies = ()
        if not reasons:
            value = native["residual_frequency_per_year"]
            source = {
                "annual_frequency_per_year": value,
                "inputs": json_value((rate, events, layers)),
                "native": json_value(native),
                "provider": self.provider,
            }
            frequencies = (
                RiskFrequency(
                    id=f"{rate.id}:all_declared_layers_failed",
                    annual_frequency_per_year=value,
                    condition_id=f"{rate.condition_id}:all_declared_layers_failed",
                    endpoint="protection_failure_combination",
                    consumed_event_ids=_unique((*rate.consumed_event_ids, *bound)),
                    consumed_protection_ids=_unique(
                        (
                            *rate.consumed_protection_ids,
                            *[p for e in bound.values() for p in e.protection_ids],
                        )
                    ),
                    dependency_groups=_unique(
                        (*rate.dependency_groups, *[g for e in bound.values() for g in e.dependency_groups])
                    ),
                    evidence_ids=_unique(
                        (
                            *rate.evidence_ids,
                            *[p for e in bound.values() for p in e.evidence_ids],
                            *[p.independence_evidence_id for p in layers],
                        )
                    ),
                    context=rate.context,
                    source_payload=source,
                    source_digest=digest(source),
                ),
            )
        return self._result(
            "lopa", rate.context, (rate, events, layers), native, issues=reasons, frequencies=frequencies
        )

    def prism_demand(self, event: RiskProbability, exposure: DemandExposure, *, factory=None):
        event = _checked(event, RiskProbability)
        checked = self.annualize(event, exposure)
        if checked["status"] != "exchange_checked":
            raise ValueError("a compatible documented demand exposure is required")
        if event.source_kind != "human_reliability":
            raise ValueError("this receiving adapter requires the original native human estimate")
        if factory is None:
            from lh2_tno.frequency import DemandFailure

            factory = DemandFailure
        estimate = self.api.Estimate.model_validate_json(json.dumps(event.source_payload["estimate"]))
        native = self.api.prism_demand(estimate, exposure.opportunities_per_year, factory=factory)
        payload = json_value(native.model_dump(mode="json") if hasattr(native, "model_dump") else native)
        receiver_module = sys.modules.get(type(native).__module__)
        receiver_path = getattr(receiver_module, "__file__", None)
        body = {
            "schema": "pipeline_integrity.prism_demand_handoff.v1",
            "probability": event.model_dump(mode="json"),
            "exposure": exposure.model_dump(mode="json"),
            "native_payload": payload,
            "native_payload_digest": digest(payload),
            "context": event.context.model_dump(mode="json"),
            "provider": self.provider,
            "receiving_contract": {
                "class": f"{type(native).__module__}.{type(native).__qualname__}",
                "module_sha256": None if receiver_path is None else file_digest(Path(receiver_path)),
            },
            "implementation_sha256": file_digest(Path(__file__)),
            "meaning": "expected human event count, not a loss-of-containment or release frequency",
            "physical_validation_claim": False,
        }
        return RiskHandoff(native, {**body, "result_digest": digest(body)})


def run_risk_job(document: dict, *, client: HRARiskClient | None = None):
    """Run one JSON-compatible exchange with the installed or explicitly injected owner API."""
    shapes = {
        "fault_tree": {"operation", "request", "context"},
        "event_tree": {"operation", "frequency", "request"},
        "annualize": {"operation", "probability", "exposure"},
        "lopa": {"operation", "frequency", "events", "layers"},
        "prism_demand": {"operation", "probability", "exposure"},
    }
    operation = document.get("operation")
    if not isinstance(operation, str) or operation not in shapes or set(document) != shapes[operation]:
        raise ValueError("risk job operation or exact input fields are invalid")

    def parse(cls, value):
        return cls.model_validate_json(json.dumps(json_value(value), allow_nan=False))

    client = client or HRARiskClient()
    if operation == "fault_tree":
        return client.fault_tree(
            parse(FaultTreeRequest, document["request"]), parse(RunContext, document["context"])
        )
    if operation == "event_tree":
        return client.event_tree(
            parse(RiskFrequency, document["frequency"]), parse(EventTreeRequest, document["request"])
        )
    if operation == "lopa":
        return client.lopa(
            parse(RiskFrequency, document["frequency"]),
            tuple(parse(RiskProbability, event) for event in document["events"]),
            tuple(parse(ProtectionLayer, layer) for layer in document["layers"]),
        )
    probability = parse(RiskProbability, document["probability"])
    exposure = None if document["exposure"] is None else parse(DemandExposure, document["exposure"])
    if operation == "annualize":
        return client.annualize(probability, exposure)
    if exposure is None:
        raise ValueError("PRISM demand exchange requires a documented exposure")
    return client.prism_demand(probability, exposure).record()
