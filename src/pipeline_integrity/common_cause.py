"""Evidence gates for binding an observed shared-cause state table to the owner FTA."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, digest
from .risk_exchange import (
    FaultNode,
    FaultTreeRequest,
    JointStateCountTable,
    RiskProbability,
    fault_tree_request_from_verified_joint_counts,
    verify_joint_state_count_receipt,
)


def _tree_event_ids(root: FaultNode) -> set[str]:
    """Collect event leaves without changing or interpreting owner FTA logic."""
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind == "event":
            if node.event_id is not None:
                found.add(node.event_id)
        else:
            stack.extend(node.children)
    return found


class CommonCauseBinding(Contract):
    """Caller-reviewed definition linking one observed cause to dependent failures."""

    cause_event_id: Name
    cause_definition_id: Name
    affected_event_ids: tuple[Name, ...] = Field(min_length=2)
    cause_evidence_id: Name
    review_state: Literal["reviewed_shared_cause"]

    @model_validator(mode="after")
    def distinct_affected_events(self):
        if len(set(self.affected_event_ids)) != len(self.affected_event_ids):
            raise ValueError("affected event identifiers must be distinct")
        if self.cause_event_id in self.affected_event_ids:
            raise ValueError("the cause occurrence cannot also be a dependent component event")
        return self


def fault_tree_request_from_common_cause_counts(
    root: FaultNode,
    events: tuple[RiskProbability, ...],
    table: JointStateCountTable,
    count_receipt: dict,
    binding: CommonCauseBinding,
    *,
    maximum_events=20,
    maximum_states=1048576,
    maximum_tree_nodes=10000,
):
    """Bind caller-classified shared-cause counts to the existing joint-state FTA path.

    The function only validates and seals a supplied cause/failure table. It does not infer
    common-cause states from marginal probabilities, fit a beta factor, or estimate sampling
    uncertainty. The caller must provide an exhaustive grouped Boolean count table whose
    cause event and affected failures refer to the same declared opportunity population.
    """
    checked_binding = CommonCauseBinding.model_validate_json(binding.model_dump_json())
    checked_events = tuple(RiskProbability.model_validate_json(e.model_dump_json()) for e in events)
    checked_table = JointStateCountTable.model_validate_json(table.model_dump_json())
    event_by_id = {event.id: event for event in checked_events}
    table_event_ids = set(checked_table.event_ids)
    required = {checked_binding.cause_event_id, *checked_binding.affected_event_ids}
    if len(event_by_id) != len(checked_events) or not required <= table_event_ids:
        raise ValueError("the joint table must include the distinct cause and every affected event")
    if set(event_by_id) != table_event_ids:
        raise ValueError("the caller must bind every observed joint-table event to a risk probability")
    if checked_binding.cause_evidence_id == checked_table.evidence_id:
        raise ValueError("cause classification evidence must be distinct from joint-count evidence")
    tree_event_ids = _tree_event_ids(root)
    if not required <= tree_event_ids:
        raise ValueError("the FTA root must consume the declared cause and every affected event")
    for event_id in required:
        event = event_by_id[event_id]
        context_evidence = {item.id for item in event.context.evidence}
        if checked_binding.cause_evidence_id not in context_evidence:
            raise ValueError("cause-definition evidence is absent from an event context")
        if checked_binding.cause_evidence_id not in event.evidence_ids:
            raise ValueError("cause-definition evidence is absent from an event source")
        if event.condition_id != checked_table.condition_id:
            raise ValueError("cause and failure events use different condition scopes")
        if checked_binding.cause_event_id in checked_binding.affected_event_ids:
            raise ValueError("cause and affected event identities overlap")
    for event_id in checked_binding.affected_event_ids:
        event = event_by_id[event_id]
        if checked_binding.cause_event_id not in event.dependency_groups:
            raise ValueError("an affected event lacks the declared common-cause dependency group")

    request, verified_counts = fault_tree_request_from_verified_joint_counts(
        root,
        checked_events,
        checked_table,
        count_receipt,
        maximum_events=maximum_events,
        maximum_states=maximum_states,
        maximum_tree_nodes=maximum_tree_nodes,
    )
    body = {
        "schema": "pipeline_integrity.common_cause_fta_binding.v1",
        "binding": checked_binding.model_dump(mode="json"),
        "joint_count_table_digest": verified_counts["table_digest"],
        "joint_count_receipt_digest": verified_counts["record_digest"],
        "cause_evidence_id": checked_binding.cause_evidence_id,
        "cause_definition_id": checked_binding.cause_definition_id,
        "cause_and_affected_event_ids": sorted(required),
        "fta_consumed_event_ids": sorted(tree_event_ids),
        "condition_id": checked_table.condition_id,
        "opportunity_id": checked_table.opportunity_id,
        "population_id": checked_table.population_id,
        "grouping_unit": checked_table.grouping_unit,
        "fault_tree_request_digest": digest(request),
        "observed_group_count": verified_counts["total_observed_groups"],
        "joint_state_normalization_only": True,
        "common_cause_model_fitted": False,
        "dependence_inferred_from_marginals": False,
        "sampling_uncertainty_quantified": False,
        "field_validation_claim": False,
    }
    return request, {**body, "record_digest": digest(body)}


def verify_common_cause_binding_receipt(
    receipt: dict,
    request: FaultTreeRequest,
    count_receipt: dict,
    binding: CommonCauseBinding,
):
    """Check a binding receipt against the exact FTA and observed-count receipt."""
    if not isinstance(receipt, dict) or receipt.get("record_digest") != digest(
        {key: value for key, value in receipt.items() if key != "record_digest"}
    ):
        raise ValueError("common-cause binding receipt seal changed")
    if receipt.get("binding") != binding.model_dump(mode="json"):
        raise ValueError("common-cause definition changed after binding")
    if receipt.get("fault_tree_request_digest") != digest(request):
        raise ValueError("fault-tree request changed after common-cause binding")
    if receipt.get("joint_count_receipt_digest") != count_receipt.get("record_digest"):
        raise ValueError("joint-count source receipt changed after common-cause binding")
    if receipt.get("joint_count_table_digest") != count_receipt.get("table_digest"):
        raise ValueError("joint-count table changed after common-cause binding")
    if not isinstance(count_receipt.get("table"), dict):
        raise ValueError("joint-count receipt does not include its replayable source table")
    table = JointStateCountTable.model_validate_json(json.dumps(count_receipt["table"]))
    verify_joint_state_count_receipt(count_receipt, table)
    if (receipt.get("population_id"), receipt.get("grouping_unit"), receipt.get("observed_group_count")) != (
        table.population_id, table.grouping_unit, count_receipt["total_observed_groups"]
    ):
        raise ValueError("common-cause receipt seal changed its verified population or grouping scope")
    if (
        request.dependence != "joint_states"
        or request.joint_state_receipt_digest != count_receipt.get("record_digest")
        or {tuple(sorted(state.states.items())) for state in request.joint_states}
        != {tuple(sorted(row.states.items())) for row in table.rows}
    ):
        raise ValueError("FTA joint-state inputs do not replay from the verified count table")
    if receipt.get("cause_definition_id") != binding.cause_definition_id:
        raise ValueError("cause-definition identity changed after binding")
    required = {binding.cause_event_id, *binding.affected_event_ids}
    if receipt.get("cause_and_affected_event_ids") != sorted(required):
        raise ValueError("cause and affected-event set changed after binding")
    if receipt.get("fta_consumed_event_ids") != sorted(_tree_event_ids(request.root)):
        raise ValueError("FTA event leaves changed after common-cause binding")
    if not required <= set(receipt.get("fta_consumed_event_ids", [])):
        raise ValueError("FTA request no longer consumes the declared shared-cause events")
    if receipt.get("condition_id") != request.condition_id or receipt.get("opportunity_id") != request.opportunity_id:
        raise ValueError("FTA condition or opportunity scope changed after binding")
    event_by_id = {event.id: event for event in request.events}
    if set(event_by_id) != set(table.event_ids):
        raise ValueError("FTA event inputs do not match the verified joint-count table")
    for event_id in required:
        event = event_by_id[event_id]
        if binding.cause_evidence_id not in event.evidence_ids or binding.cause_evidence_id not in {
            evidence.id for evidence in event.context.evidence
        }:
            raise ValueError("cause-definition evidence is absent from the replayed FTA event")
    for event_id in binding.affected_event_ids:
        if binding.cause_event_id not in event_by_id[event_id].dependency_groups:
            raise ValueError("replayed affected event lacks the common-cause dependency group")
    if (
        receipt.get("schema") != "pipeline_integrity.common_cause_fta_binding.v1"
        or receipt.get("cause_evidence_id") != binding.cause_evidence_id
        or receipt.get("joint_state_normalization_only") is not True
        or receipt.get("field_validation_claim") is not False
        or receipt.get("common_cause_model_fitted") is not False
        or receipt.get("dependence_inferred_from_marginals") is not False
        or receipt.get("sampling_uncertainty_quantified") is not False
    ):
        raise ValueError("common-cause binding cannot claim fitted or inferred evidence")
    return True
