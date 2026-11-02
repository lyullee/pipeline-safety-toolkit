"""Local independent truth-table oracles through the real HRA fault-tree owner."""

from __future__ import annotations

import itertools
import math

import pytest

from pipeline_integrity.common_cause import (
    CommonCauseBinding,
    fault_tree_request_from_common_cause_counts,
    verify_common_cause_binding_receipt,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.risk_exchange import (
    FaultNode,
    FaultTreeRequest,
    HRARiskClient,
    JointStateCount,
    JointStateCountTable,
    RiskProbability,
    joint_states_from_counts,
)


def context(*evidence_ids):
    evidence = tuple(
        EvidenceRef(id=key, citation="Authored synthetic Boolean probability fixture", origin="synthetic")
        for key in evidence_ids
    )
    return RunContext(
        asset_id="synthetic_process",
        scenario_id="fault_tree_oracle",
        clock_id="one_declared_demand",
        origin="synthetic",
        evidence=evidence,
    )


def event_probability(event_id, probability, ctx, *, dependency_groups=(), evidence_ids=None):
    source = {"probability": probability, "basis": "authored synthetic demand probability"}
    return RiskProbability(
        id=event_id,
        probability=probability,
        measure="demand_failure_probability",
        condition_id="one_demand_condition",
        conditioning="Authored synthetic demand condition",
        opportunity_id="one_demand",
        opportunity_definition="One Boolean fault-tree demand",
        failure_definition=f"Synthetic event {event_id} occurs",
        role="equipment",
        protection_ids=(f"protection_{event_id}",),
        dependency_groups=dependency_groups,
        evidence_ids=tuple(evidence_ids or (f"probability_{event_id}",)),
        qualification="assumed",
        context=ctx,
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )


def _bernoulli_oracle(gate, probabilities):
    ids = tuple(sorted(probabilities))
    total = 0.0
    for flags in itertools.product((False, True), repeat=len(ids)):
        state = dict(zip(ids, flags, strict=True))
        weight = math.prod(probabilities[key] if state[key] else 1 - probabilities[key] for key in ids)
        top = gate(state)
        total += weight * top
    return total


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        (FaultNode(kind="AND", children=(FaultNode(kind="event", event_id="A"), FaultNode(kind="event", event_id="B"))), 0.06),
        (FaultNode(kind="OR", children=(FaultNode(kind="event", event_id="A"), FaultNode(kind="event", event_id="B"))), 0.44),
        (
            FaultNode(
                kind="VOTE",
                threshold=2,
                children=tuple(FaultNode(kind="event", event_id=key) for key in ("A", "B", "C")),
            ),
            0.212,
        ),
        (
            FaultNode(
                kind="OR",
                children=(
                    FaultNode(kind="event", event_id="A"),
                    FaultNode(kind="AND", children=(FaultNode(kind="event", event_id="A"), FaultNode(kind="event", event_id="B"))),
                ),
            ),
            0.2,
        ),
    ],
)
def test_real_hra_fta_matches_independent_complete_bernoulli_enumeration(gate, expected):
    probabilities = {"A": 0.2, "B": 0.3, "C": 0.4}
    used = sorted({
        node.event_id
        for node in _walk(gate)
        if node.kind == "event"
    })
    ctx = context(*(f"probability_{key}" for key in used), "independence_evidence")
    events = tuple(event_probability(key, probabilities[key], ctx) for key in used)
    request = FaultTreeRequest(
        root=gate,
        events=events,
        condition_id="one_demand_condition",
        opportunity_id="one_demand",
        dependence="declared_independent",
        dependence_evidence_id="independence_evidence",
    )
    independent = _bernoulli_oracle(
        lambda state: _evaluate(gate, state), {key: probabilities[key] for key in used}
    )
    result = HRARiskClient().fault_tree(request, ctx)
    assert result["status"] == "exchange_checked"
    assert result["probability"]["probability"] == pytest.approx(expected, abs=1e-14)
    assert result["probability"]["probability"] == pytest.approx(independent, abs=1e-14)
    assert result["native_result"] == {"top_probability": pytest.approx(expected, abs=1e-14)}


def _walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def _evaluate(node, state):
    if node.kind == "event":
        return state[node.event_id]
    outcomes = [_evaluate(child, state) for child in node.children]
    if node.kind == "AND":
        return all(outcomes)
    if node.kind == "OR":
        return any(outcomes)
    if node.kind == "NOT":
        return not outcomes[0]
    return sum(outcomes) >= node.threshold


def test_reviewed_common_cause_count_table_reaches_native_joint_state_fta():
    event_ids = ("C", "A", "B")
    count_by_flags = {
        (False, False, False): 50,
        (False, False, True): 5,
        (False, True, False): 5,
        (False, True, True): 5,
        (True, False, False): 5,
        (True, False, True): 5,
        (True, True, False): 5,
        (True, True, True): 20,
    }
    source = {"source": "authored complete eight-state grouped Boolean table"}
    table = JointStateCountTable(
        event_ids=event_ids,
        rows=tuple(
            JointStateCount(states=dict(zip(event_ids, state, strict=True)), count=count)
            for state, count in count_by_flags.items()
        ),
        condition_id="one_demand_condition",
        opportunity_id="one_demand",
        opportunity_definition="One Boolean fault-tree demand",
        population_id="synthetic_population",
        grouping_unit="one_grouped_demand",
        evidence_id="joint_counts",
        source_payload=source,
        source_digest=digest(source),
    )
    _, count_receipt = joint_states_from_counts(table)
    ctx = context("joint_counts", "shared_cause_review", "probability_C", "probability_A", "probability_B")
    marginal = {
        event_id: sum(count for state, count in count_by_flags.items() if state[index]) / 100
        for index, event_id in enumerate(event_ids)
    }
    events = tuple(
        event_probability(
            key,
            marginal[key],
            ctx,
            dependency_groups=() if key == "C" else ("C",),
            evidence_ids=("joint_counts", "shared_cause_review"),
        )
        for key in event_ids
    )
    root = FaultNode(kind="OR", children=tuple(FaultNode(kind="event", event_id=key) for key in event_ids))
    binding = CommonCauseBinding(
        cause_event_id="C",
        cause_definition_id="shared_power_supply_loss",
        affected_event_ids=("A", "B"),
        cause_evidence_id="shared_cause_review",
        review_state="reviewed_shared_cause",
    )
    request, binding_receipt = fault_tree_request_from_common_cause_counts(
        root, events, table, count_receipt, binding
    )
    assert verify_common_cause_binding_receipt(binding_receipt, request, count_receipt, binding)

    expected_union = sum(count for state, count in count_by_flags.items() if any(state)) / 100
    result = HRARiskClient().fault_tree(request, ctx)
    assert result["status"] == "exchange_checked"
    assert result["probability"]["probability"] == pytest.approx(expected_union, abs=1e-14)
    assert result["probability"]["probability"] == pytest.approx(0.5, abs=1e-14)
    assert binding_receipt["common_cause_model_fitted"] is False
    assert binding_receipt["sampling_uncertainty_quantified"] is False
