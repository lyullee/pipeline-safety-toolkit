"""Synthetic boundary tests; numerical execution is deferred to the HPC suite."""

import pytest

from pipeline_integrity.common_cause import (
    CommonCauseBinding,
    fault_tree_request_from_common_cause_counts,
    verify_common_cause_binding_receipt,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.risk_exchange import (
    FaultNode,
    JointStateCount,
    JointStateCountTable,
    RiskProbability,
    joint_states_from_counts,
)


def _context(*, include_cause=True):
    evidence = [EvidenceRef(id="joint-count-evidence", citation="Authored joint fixture", origin="synthetic")]
    if include_cause:
        evidence.append(EvidenceRef(id="cause-definition-evidence", citation="Authored cause definition", origin="synthetic"))
    return RunContext(
        asset_id="synthetic-header",
        scenario_id="common-cause-fixture",
        clock_id="demand-order",
        origin="synthetic",
        evidence=tuple(evidence),
    )


def _event(event_id, probability, *, groups=(), include_cause=True):
    source = {"probability": probability, "fixture": "authored aggregate"}
    evidence = ("joint-count-evidence", "cause-definition-evidence") if include_cause else ("joint-count-evidence",)
    return RiskProbability(
        id=event_id,
        probability=probability,
        measure="demand_failure_probability",
        condition_id="same-demand",
        conditioning="Synthetic shared-cause condition",
        opportunity_id="demand-group",
        opportunity_definition="One grouped demand population",
        failure_definition=f"Observed state for {event_id}",
        role="equipment" if event_id != "C" else "initiating",
        dependency_groups=groups,
        evidence_ids=evidence,
        qualification="research",
        context=_context(include_cause=include_cause),
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )


def _fixture():
    source = {"fixture": "authored same-opportunity grouped states", "common_cause_event_id": "C"}
    table = JointStateCountTable(
        event_ids=("A", "B", "C"),
        rows=(
            JointStateCount(states={"A": False, "B": False, "C": False}, count=70),
            JointStateCount(states={"A": False, "B": False, "C": True}, count=10),
            JointStateCount(states={"A": True, "B": False, "C": True}, count=5),
            JointStateCount(states={"A": False, "B": True, "C": True}, count=5),
            JointStateCount(states={"A": True, "B": True, "C": True}, count=10),
        ),
        condition_id="same-demand",
        opportunity_id="demand-group",
        opportunity_definition="One grouped demand population",
        population_id="synthetic-population",
        grouping_unit="one-demand-observation-group",
        evidence_id="joint-count-evidence",
        source_payload=source,
        source_digest=digest(source),
    )
    events = (_event("A", 0.15, groups=("C",)), _event("B", 0.15, groups=("C",)), _event("C", 0.30))
    binding = CommonCauseBinding(
        cause_event_id="C",
        cause_definition_id="shared-header-disturbance",
        affected_event_ids=("A", "B"),
        cause_evidence_id="cause-definition-evidence",
        review_state="reviewed_shared_cause",
    )
    root = FaultNode(
        kind="OR",
        children=(
            FaultNode(kind="event", event_id="C"),
            FaultNode(kind="AND", children=(FaultNode(kind="event", event_id="A"), FaultNode(kind="event", event_id="B"))),
        ),
    )
    _, receipt = joint_states_from_counts(table)
    return root, events, table, receipt, binding


def test_common_cause_binding_delegates_to_observed_joint_state_fta():
    root, events, table, count_receipt, binding = _fixture()
    request, receipt = fault_tree_request_from_common_cause_counts(root, events, table, count_receipt, binding)
    assert request.dependence == "joint_states"
    assert request.joint_state_receipt_digest == count_receipt["record_digest"]
    assert receipt["joint_state_normalization_only"]
    assert receipt["common_cause_model_fitted"] is False
    assert receipt["dependence_inferred_from_marginals"] is False
    assert verify_common_cause_binding_receipt(receipt, request, count_receipt, binding)


def test_common_cause_binding_rejects_missing_dependency_group():
    root, events, table, count_receipt, binding = _fixture()
    changed = (_event("A", 0.15), events[1], events[2])
    with pytest.raises(ValueError, match="dependency group"):
        fault_tree_request_from_common_cause_counts(root, changed, table, count_receipt, binding)


def test_common_cause_binding_rejects_missing_definition_evidence():
    root, events, table, count_receipt, binding = _fixture()
    changed = (_event("A", 0.15, groups=("C",), include_cause=False), *events[1:])
    with pytest.raises(ValueError, match="cause-definition evidence"):
        fault_tree_request_from_common_cause_counts(root, changed, table, count_receipt, binding)


def test_common_cause_binding_rejects_cause_or_affected_event_unused_by_fta():
    root, events, table, count_receipt, binding = _fixture()
    incomplete = FaultNode(kind="event", event_id="C")
    with pytest.raises(ValueError, match="FTA root must consume"):
        fault_tree_request_from_common_cause_counts(
            incomplete, events, table, count_receipt, binding
        )


def test_common_cause_receipt_rejects_resealed_altered_scope():
    root, events, table, count_receipt, binding = _fixture()
    request, receipt = fault_tree_request_from_common_cause_counts(
        root, events, table, count_receipt, binding
    )
    altered = dict(receipt, population_id="different-population")
    altered["record_digest"] = digest(
        {key: value for key, value in altered.items() if key != "record_digest"}
    )
    with pytest.raises(ValueError, match="seal changed"):
        verify_common_cause_binding_receipt(altered, request, count_receipt, binding)


def test_common_cause_replay_rechecks_joint_count_receipt_contents():
    root, events, table, count_receipt, binding = _fixture()
    request, receipt = fault_tree_request_from_common_cause_counts(
        root, events, table, count_receipt, binding
    )
    altered_counts = dict(count_receipt, total_observed_groups=1)
    altered_counts["record_digest"] = digest(
        {key: value for key, value in altered_counts.items() if key != "record_digest"}
    )
    altered_receipt = dict(receipt, joint_count_receipt_digest=altered_counts["record_digest"])
    altered_receipt["record_digest"] = digest(
        {key: value for key, value in altered_receipt.items() if key != "record_digest"}
    )
    with pytest.raises(ValueError, match="source or normalization differs"):
        verify_common_cause_binding_receipt(altered_receipt, request, altered_counts, binding)


def test_common_cause_receipt_rejects_resealed_model_fit_claim():
    root, events, table, count_receipt, binding = _fixture()
    request, receipt = fault_tree_request_from_common_cause_counts(root, events, table, count_receipt, binding)
    altered = dict(receipt, common_cause_model_fitted=True)
    altered["record_digest"] = digest({key: value for key, value in altered.items() if key != "record_digest"})
    with pytest.raises(ValueError, match="cannot claim fitted"):
        verify_common_cause_binding_receipt(altered, request, count_receipt, binding)
