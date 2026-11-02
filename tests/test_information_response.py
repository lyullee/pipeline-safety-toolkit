"""Paired information error records retain dependence and explicit selection."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.response_exchange import (
    InformationResponseBootstrapRequest,
    InformationResponseFaultTreeMissingOutcomeRequest,
    InformationResponseFaultTreeBootstrapRequest,
    InformationResponseFaultTreeBootstrapResult,
    InformationResponseStudy,
    InformationResponseTrial,
    information_response_cluster_bootstrap,
    information_response_fault_tree_cluster_bootstrap,
    information_response_fault_tree_missing_outcome_bounds,
    information_response_joint_counts,
    information_response_missingness_audit,
    information_response_missing_outcome_bounds,
    verify_information_response_cluster_bootstrap,
    verify_information_response_fault_tree_cluster_bootstrap,
    verify_information_response_fault_tree_missing_outcome_bounds,
    verify_information_response_missingness_audit,
    verify_information_response_missing_outcome_bounds,
)
from pipeline_integrity.risk_exchange import FaultNode, HRARiskClient, RiskProbability, joint_states_from_counts


def case(outcomes, **changes):
    context = RunContext(
        asset_id="pipe",
        scenario_id="alarm",
        clock_id="trial",
        origin="synthetic",
        evidence=(EvidenceRef(id="review", citation="Authored paired observations", origin="synthetic"),),
    )
    fields = dict(
        information_event_id="bad_cue",
        response_event_id="wrong_action",
        condition_id="alarm",
        opportunity_id="one_cue",
        opportunity_definition="one paired cue and response trial",
        population_id="authored",
        grouping_unit="trial",
        evidence_id="review",
        missingness_review_evidence_id="review",
        trials=tuple(
            InformationResponseTrial(id=f"t{i}", information_error=a, response_error=b, evidence_id="review")
            for i, (a, b) in enumerate(outcomes)
        ),
    )
    return InformationResponseStudy(**{**fields, **changes}), context


def risk_event(event_id, context):
    source = {"probability": 0.5, "declaration": "authored paired-state fixture"}
    return RiskProbability(
        id=event_id,
        probability=0.5,
        measure="demand_failure_probability",
        condition_id="alarm",
        conditioning="Declared demand",
        opportunity_id="one_cue",
        opportunity_definition="one paired cue and response trial",
        failure_definition=f"{event_id} occurs",
        role="equipment",
        evidence_ids=("review",),
        qualification="assumed",
        context=context,
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )


def risk_client():
    def joint_probability(tree, states):
        return sum(probability for state, probability in states if state["bad_cue"] or state["wrong_action"])

    api = SimpleNamespace(
        Event=lambda event_id: event_id,
        Gate=lambda kind, children, threshold: (kind, children, threshold),
        fault_probability=lambda tree, probabilities: 0.0,
        fault_probability_joint=joint_probability,
    )
    return HRARiskClient(api=api, provider={"id": "authored-joint-state-owner", "version": "1"})


def test_paired_dependence_is_preserved_without_independence_assumption():
    study, context = case([(False, False), (False, False), (True, True), (True, True)])
    table = information_response_joint_counts(study, context)
    states, receipt = joint_states_from_counts(table)
    assert len(states) == 2
    assert receipt["marginal_probability_by_event"] == {"bad_cue": 0.5, "wrong_action": 0.5}
    assert sum(s.probability for s in states if all(s.states.values())) == 0.5
    assert not table.source_payload["causal_information_effect_estimated"]


def test_unknown_outcomes_require_explicit_complete_case_review():
    study, context = case([(False, False), (True, None)])
    with pytest.raises(ValueError, match="selection review"):
        information_response_joint_counts(study, context)
    selected = study.model_copy(update={"missing_policy": "reviewed_complete_cases"})
    table = information_response_joint_counts(selected, context)
    assert sum(r.count for r in table.rows) == 1
    assert table.source_payload["excluded_trial_ids"] == ["t1"]
    assert not table.source_payload["unknown_outcomes_imputed"]


def test_empty_selection_and_missing_evidence_are_rejected():
    study, context = case([(None, True)], missing_policy="reviewed_complete_cases")
    with pytest.raises(ValueError, match="no complete"):
        information_response_joint_counts(study, context)
    with pytest.raises(ValueError, match="evidence"):
        information_response_joint_counts(study, context.model_copy(update={"evidence": ()}))


def test_missingness_audit_counts_each_paired_pattern_and_cluster_without_imputation():
    study, context = case([(False, False), (None, False), (True, True), (False, None), (None, None)])
    trials = tuple(
        trial.model_copy(
            update={"sampling_cluster_id": "operator_A" if index < 2 else "operator_B"}
        )
        for index, trial in enumerate(study.trials)
    )
    study = study.model_copy(
        update={"grouping_unit": "operator", "sampling_unit_evidence_id": "review", "trials": trials}
    )

    result = information_response_missingness_audit(study, context)

    counts = {item.pattern: item.count for item in result.pattern_counts}
    assert result.trial_count == 5
    assert result.complete_trial_count == 2
    assert result.incomplete_trial_count == 3
    assert counts == {
        "complete_00": 1,
        "complete_01": 0,
        "complete_10": 0,
        "complete_11": 1,
        "missing_information_only": 1,
        "missing_response_only": 1,
        "missing_both": 1,
    }
    by_cluster = {item.cluster_id: item for item in result.cluster_summaries}
    assert by_cluster["operator_A"].trial_count == 2
    assert by_cluster["operator_A"].incomplete_trial_count == 1
    assert by_cluster["operator_B"].incomplete_trial_count == 2
    assert result.unknown_outcomes_imputed is False
    assert result.missingness_mechanism_inferred is False
    assert result.selection_bias_corrected is False
    assert result.independent_sampling_claim is False
    assert verify_information_response_missingness_audit(result, study, context)


def test_missingness_audit_does_not_invent_cluster_details_for_trial_grouping():
    study, context = case([(False, False), (None, True)])

    result = information_response_missingness_audit(study, context)

    assert result.grouping_unit == "trial"
    assert result.cluster_summaries == ()
    assert result.cluster_summaries_available is False
    assert result.incomplete_trial_count == 1


def test_missingness_audit_requires_review_evidence_and_rejects_receipt_tampering():
    study, context = case([(False, False), (True, None)])
    with pytest.raises(ValueError, match="evidence"):
        information_response_missingness_audit(study, context.model_copy(update={"evidence": ()}))

    result = information_response_missingness_audit(study, context)
    altered = result.model_copy(update={"missingness_mechanism_inferred": True})
    with pytest.raises(ValueError, match="receipt changed"):
        altered.check_integrity()


def test_missing_outcome_bounds_cover_all_compatible_completions_without_imputation():
    study, context = case([(False, False), (True, None), (None, False), (None, None)])

    result = information_response_missing_outcome_bounds(study, context)

    states = {(row.information_error, row.response_error): row for row in result.state_bounds}
    assert result.trial_count == 4
    assert result.complete_trial_count == 1
    assert result.incomplete_trial_count == 3
    assert (states[(False, False)].lower_count, states[(False, False)].upper_count) == (1, 3)
    assert (states[(False, True)].lower_count, states[(False, True)].upper_count) == (0, 1)
    assert (states[(True, False)].lower_count, states[(True, False)].upper_count) == (0, 3)
    assert (states[(True, True)].lower_count, states[(True, True)].upper_count) == (0, 2)
    assert states[(False, False)].lower_probability == 0.25
    assert states[(False, False)].upper_probability == 0.75
    assert result.unknown_outcomes_imputed is False
    assert result.selection_bias_corrected is False
    assert result.sampling_uncertainty_estimated is False
    assert verify_information_response_missing_outcome_bounds(result, study, context)
    altered_counts = result.model_dump(mode="python")
    altered_counts["state_bounds"][0]["upper_probability"] = 0.9
    with pytest.raises(ValidationError, match="probabilities must match"):
        type(result).model_validate(altered_counts)


def test_missing_outcome_bounds_reject_resealed_changes_and_missing_evidence():
    study, context = case([(None, True), (False, False)])
    result = information_response_missing_outcome_bounds(study, context)
    altered = result.model_copy(update={"selection_bias_corrected": True})
    with pytest.raises(ValueError, match="receipt changed"):
        altered.check_integrity()
    with pytest.raises(ValueError, match="evidence"):
        information_response_missing_outcome_bounds(study, context.model_copy(update={"evidence": ()}))


def test_cluster_bootstrap_preserves_pairs_and_replays_deterministically():
    study, context = case([(False, False)] * 4 + [(True, True)] * 4)
    outcomes = [(False, False)] * 4 + [(True, True)] * 4
    trials = tuple(
        InformationResponseTrial(
            id=f"trial_{i}",
            information_error=information_error,
            response_error=response_error,
            evidence_id="review",
            sampling_cluster_id="operator_A" if i < 4 else "operator_B",
        )
        for i, (information_error, response_error) in enumerate(outcomes)
    )
    study = study.model_copy(
        update={"grouping_unit": "operator", "sampling_unit_evidence_id": "review", "trials": trials}
    )
    request = InformationResponseBootstrapRequest(replicates=512, seed=71, confidence_level=0.9)

    result = information_response_cluster_bootstrap(study, context, request)

    assert result.cluster_count == 2
    assert result.complete_trial_count == 8
    assert result.interval_method == "cluster_percentile"
    assert result.estimand == "trial_weighted_complete_case_joint_state_probability"
    assert result.interval_scope == "four_separate_pointwise_intervals"
    assert result.state_intervals[0].observed_probability == 0.5
    assert result.state_intervals[0].observed_count == 4
    assert not result.state_intervals[0].degenerate_interval
    assert result.state_intervals[3].observed_probability == 0.5
    assert result.state_intervals[1].upper_bound == result.state_intervals[2].upper_bound == 0.0
    assert result.state_intervals[1].degenerate_interval
    assert verify_information_response_cluster_bootstrap(result, study, context)


def test_cluster_bootstrap_rejects_single_independent_cluster():
    study, context = case(
        [(False, False)] * 3,
        grouping_unit="operator",
        sampling_unit_evidence_id="review",
        trials=tuple(
            InformationResponseTrial(
                id=f"t{i}",
                information_error=False,
                response_error=False,
                evidence_id="review",
                sampling_cluster_id="operator_A",
            )
            for i in range(3)
        ),
    )
    request = InformationResponseBootstrapRequest(replicates=128, seed=23)
    with pytest.raises(ValueError, match="two independent complete clusters"):
        information_response_cluster_bootstrap(study, context, request)


def test_cluster_bootstrap_requires_declared_clusters_and_honors_work_budget():
    study, context = case([(False, False), (True, True)])
    request = InformationResponseBootstrapRequest(replicates=100, seed=11)
    non_trial = study.model_copy(update={"grouping_unit": "operator", "sampling_unit_evidence_id": "review"})
    with pytest.raises(ValueError, match="cluster ID"):
        information_response_cluster_bootstrap(non_trial, context, request)

    clustered = non_trial.model_copy(
        update={
            "trials": tuple(
                row.model_copy(update={"sampling_cluster_id": f"operator_{index}"})
                for index, row in enumerate(study.trials)
            )
        }
    )
    limited = request.model_copy(update={"maximum_resampling_work": 10})
    with pytest.raises(ValueError, match="work budget"):
        information_response_cluster_bootstrap(clustered, context, limited)


def test_cluster_bootstrap_complete_case_result_records_exclusions():
    study, context = case([(False, False), (True, None), (True, True)])
    with pytest.raises(ValueError, match="selection review"):
        information_response_cluster_bootstrap(
            study, context, InformationResponseBootstrapRequest(replicates=128, seed=3)
        )
    selected = study.model_copy(update={"missing_policy": "reviewed_complete_cases"})
    result = information_response_cluster_bootstrap(
        selected, context, InformationResponseBootstrapRequest(replicates=128, seed=3)
    )
    assert result.complete_trial_count == 2
    assert result.cluster_count == 2
    assert result.excluded_trial_ids == ("t1",)


def test_cluster_bootstrap_propagates_joint_states_through_existing_fta_and_replays():
    study, context = case([(False, False), (True, True)])
    events = (risk_event("wrong_action", context), risk_event("bad_cue", context))
    root = FaultNode(
        kind="OR",
        children=(FaultNode(kind="event", event_id="bad_cue"), FaultNode(kind="event", event_id="wrong_action")),
    )
    client = risk_client()
    request = InformationResponseFaultTreeBootstrapRequest(
        replicates=100,
        seed=901,
        maximum_tree_nodes=10,
        maximum_fault_tree_work=4040,
    )

    result = information_response_fault_tree_cluster_bootstrap(study, context, root, events, client, request)

    assert result.point_estimate == 0.5
    assert 0.0 <= result.lower_bound <= result.upper_bound <= 1.0
    assert result.interval_scope == "single_top_event_pointwise"
    assert result.estimand == "trial_weighted_complete_case_top_event_probability"
    assert result.grouping_unit == "trial"
    assert result.missing_policy == "require_complete"
    assert result.risk_provider_identity_kind == "caller_declared"
    assert verify_information_response_fault_tree_cluster_bootstrap(result, study, context, root, events, client)

    changed = result.model_dump(mode="json")
    changed["confidence_level"] = 0.90
    changed["result_digest"] = digest({key: value for key, value in changed.items() if key != "result_digest"})
    with pytest.raises(ValidationError, match="confidence level must match the request"):
        InformationResponseFaultTreeBootstrapResult.model_validate_json(json.dumps(changed))


def test_cluster_bootstrap_rejects_resealed_fta_receipt_for_different_request():
    study, context = case([(False, False), (True, True)])
    events = (risk_event("wrong_action", context), risk_event("bad_cue", context))
    root = FaultNode(
        kind="OR",
        children=(FaultNode(kind="event", event_id="bad_cue"), FaultNode(kind="event", event_id="wrong_action")),
    )
    client = risk_client()
    original = client.fault_tree

    def mismatched_receipt(request, run_context):
        receipt = original(request, run_context)
        receipt["inputs"]["maximum_events"] += 1
        receipt["result_digest"] = digest({key: value for key, value in receipt.items() if key != "result_digest"})
        return receipt

    client.fault_tree = mismatched_receipt
    request = InformationResponseFaultTreeBootstrapRequest(
        replicates=100,
        seed=902,
        maximum_tree_nodes=10,
        maximum_fault_tree_work=4040,
    )
    with pytest.raises(ValueError, match="withheld a bootstrap replicate"):
        information_response_fault_tree_cluster_bootstrap(study, context, root, events, client, request)


def test_fta_bootstrap_requires_matching_events_and_enforces_tree_work_budget():
    study, context = case([(False, False), (True, True)])
    events = (risk_event("bad_cue", context), risk_event("wrong_action", context))
    root = FaultNode(
        kind="OR",
        children=(FaultNode(kind="event", event_id="bad_cue"), FaultNode(kind="event", event_id="wrong_action")),
    )
    client = risk_client()
    request = InformationResponseFaultTreeBootstrapRequest(
        replicates=100,
        seed=7,
        maximum_tree_nodes=10,
        maximum_fault_tree_work=3999,
    )
    with pytest.raises(ValueError, match="tree evaluation work budget"):
        information_response_fault_tree_cluster_bootstrap(study, context, root, events, client, request)
    with pytest.raises(ValueError, match="match the paired"):
        information_response_fault_tree_cluster_bootstrap(study, context, root, events[:1], client, request)


def test_missing_outcome_bounds_propagate_all_completions_through_existing_fta():
    study, context = case([(False, False), (True, None), (None, False), (None, None)])
    events = (risk_event("wrong_action", context), risk_event("bad_cue", context))
    root = FaultNode(
        kind="OR",
        children=(FaultNode(kind="event", event_id="bad_cue"), FaultNode(kind="event", event_id="wrong_action")),
    )
    client = risk_client()

    result = information_response_fault_tree_missing_outcome_bounds(
        study,
        context,
        root,
        events,
        client,
        InformationResponseFaultTreeMissingOutcomeRequest(maximum_tree_nodes=10, maximum_fault_tree_work=40),
    )

    truth = {(row.information_error, row.response_error): row.top_event for row in result.truth_table}
    assert truth == {(False, False): False, (False, True): True, (True, False): True, (True, True): True}
    assert result.trial_count == 4
    assert result.complete_trial_count == 1
    assert result.incomplete_trial_count == 3
    assert result.observed_complete_top_event_count == 0
    assert result.lower_top_event_count == 1
    assert result.upper_top_event_count == 3
    assert result.lower_probability == 0.25
    assert result.upper_probability == 0.75
    assert result.unknown_outcomes_imputed is False
    assert result.sampling_uncertainty_estimated is False
    altered_counts = result.model_dump(mode="python")
    altered_counts["upper_top_event_count"] = 4
    altered_counts["upper_probability"] = 1.0
    with pytest.raises(ValidationError, match="observed plus incomplete"):
        type(result).model_validate(altered_counts)
    assert verify_information_response_fault_tree_missing_outcome_bounds(
        result, study, context, root, events, client
    )


def test_missing_outcome_fta_bounds_reject_resealed_results_and_work_budget():
    study, context = case([(None, True), (False, False)])
    events = (risk_event("bad_cue", context), risk_event("wrong_action", context))
    root = FaultNode(
        kind="OR",
        children=(FaultNode(kind="event", event_id="bad_cue"), FaultNode(kind="event", event_id="wrong_action")),
    )
    client = risk_client()
    request = InformationResponseFaultTreeMissingOutcomeRequest(maximum_tree_nodes=10, maximum_fault_tree_work=39)
    with pytest.raises(ValueError, match="work budget"):
        information_response_fault_tree_missing_outcome_bounds(study, context, root, events, client, request)
    valid = request.model_copy(update={"maximum_fault_tree_work": 40})
    result = information_response_fault_tree_missing_outcome_bounds(study, context, root, events, client, valid)
    altered = result.model_copy(update={"unknown_outcomes_imputed": True})
    with pytest.raises(ValueError, match="receipt changed"):
        altered.check_integrity()
