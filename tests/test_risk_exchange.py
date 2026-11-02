"""Boundary policy tests use spies; numerical owner checks run in check_risk_exchange.py."""

from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.risk_exchange import (
    DemandExposure,
    EventBranch,
    EventTreeRequest,
    FaultNode,
    FaultTreeRequest,
    HRARiskClient,
    JointState,
    JointStateCount,
    JointStateCountTable,
    ProtectionLayer,
    RiskFrequency,
    RiskHandoff,
    RiskProbability,
    fault_tree_request_from_joint_counts,
    fault_tree_request_from_verified_joint_counts,
    joint_states_from_counts,
    run_risk_job,
    verify_joint_state_count_receipt,
)


def context(**changes):
    return RunContext(
        **dict(asset_id="pipe", scenario_id="synthetic", clock_id="elapsed", origin="synthetic") | changes
    )


def probability(key="A", p=0.2, **changes):
    source = {"probability": p, "assumption": "authored boundary test"}
    fields = dict(
        id=key,
        probability=p,
        measure="demand_failure_probability",
        condition_id="demand",
        conditioning="Declared demand",
        opportunity_id="one_demand",
        opportunity_definition="One demand",
        failure_definition=f"{key} fails",
        role="equipment",
        protection_ids=(key,),
        evidence_ids=(f"assumed:{key}",),
        qualification="assumed",
        context=context(),
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )
    return RiskProbability(**(fields | changes))


def frequency(**changes):
    source = {"annual_frequency_per_year": 0.01, "assumption": "authored boundary test"}
    fields = dict(
        id="initial",
        annual_frequency_per_year=0.01,
        condition_id="demand",
        endpoint="protection_demand",
        evidence_ids=("assumed:exposure",),
        context=context(),
        source_payload=source,
        source_digest=digest(source),
    )
    return RiskFrequency(**(fields | changes))


def node(key):
    return FaultNode(kind="event", event_id=key)


def fault_request(events=None, **changes):
    fields = dict(
        root=FaultNode(
            kind="OR", children=(node("A"), FaultNode(kind="AND", children=(node("A"), node("B"))))
        ),
        events=events or (probability(), probability("B", 0.1)),
        condition_id="demand",
        opportunity_id="one_demand",
        dependence="declared_independent",
        dependence_evidence_id="authored:independence_assumption",
    )
    return FaultTreeRequest(**(fields | changes))


def joint_count_table(**changes):
    source = {"records": "authored grouped Boolean count fixture", "population": "four states"}
    fields = dict(
        event_ids=("A", "B"),
        rows=(
            JointStateCount(states={"A": False, "B": False}, count=80),
            JointStateCount(states={"A": True, "B": False}, count=10),
            JointStateCount(states={"A": False, "B": True}, count=5),
            JointStateCount(states={"A": True, "B": True}, count=5),
        ),
        condition_id="demand",
        opportunity_id="one_demand",
        opportunity_definition="One demand",
        population_id="authored-population",
        grouping_unit="same-demand-group",
        evidence_id="joint-counts",
        source_payload=source,
        source_digest=digest(source),
    )
    return JointStateCountTable(**(fields | changes))


def test_observed_joint_counts_normalize_without_fitting_a_common_cause_model():
    states, receipt = joint_states_from_counts(joint_count_table())
    assert [state.probability for state in states] == pytest.approx([0.8, 0.1, 0.05, 0.05])
    assert receipt["marginal_probability_by_event"] == pytest.approx({"A": 0.15, "B": 0.1})
    assert receipt["normalization_only"] and not receipt["common_cause_model_fitted"]
    assert not receipt["sampling_uncertainty_quantified"] and not receipt["field_validation_claim"]
    assert verify_joint_state_count_receipt(receipt, joint_count_table())


def test_joint_count_request_binds_population_scope_and_existing_fta_contract():
    evidence = EvidenceRef(id="joint-counts", citation="Authored count fixture", origin="synthetic")
    scoped = context(evidence=(evidence,))
    events = (
        probability("A", 0.15, context=scoped, evidence_ids=("joint-counts",)),
        probability("B", 0.1, context=scoped, evidence_ids=("joint-counts",)),
    )
    root = FaultNode(kind="OR", children=(node("A"), node("B")))
    request, receipt = fault_tree_request_from_joint_counts(root, events, joint_count_table())
    assert request.dependence == "joint_states"
    assert request.dependence_evidence_id == "joint-counts"
    assert request.joint_states[0].states == {"A": False, "B": False}
    assert request.joint_state_receipt_digest == receipt["record_digest"]
    assert receipt["table"]["population_id"] == "authored-population"


@pytest.mark.parametrize("change", ["missing_state", "duplicate", "source", "scope", "evidence"])
def test_joint_count_boundary_rejects_incomplete_or_mismatched_observations(change):
    table = joint_count_table()
    if change == "missing_state":
        table = table.model_copy(update={"rows": (JointStateCount(states={"A": False}, count=1),)})
    elif change == "duplicate":
        table = table.model_copy(update={"rows": (table.rows[0], table.rows[0])})
    elif change == "source":
        table = table.model_copy(update={"source_payload": {"changed": True}})
    evidence = EvidenceRef(id="joint-counts", citation="Authored count fixture", origin="synthetic")
    scoped = context(evidence=() if change == "evidence" else (evidence,))
    events = (
        probability("A", 0.15, context=scoped, evidence_ids=("joint-counts",)),
        probability(
            "B",
            0.1,
            context=scoped,
            evidence_ids=("joint-counts",),
            opportunity_id="different" if change == "scope" else "one_demand",
        ),
    )
    with pytest.raises(ValueError):
        fault_tree_request_from_joint_counts(
            FaultNode(kind="OR", children=(node("A"), node("B"))), events, table
        )


def test_joint_count_receipt_replay_rejects_resealed_source_or_count_changes():
    table = joint_count_table()
    _, receipt = joint_states_from_counts(table)
    changed = table.model_copy(
        update={
            "rows": (
                JointStateCount(states={"A": False, "B": False}, count=79),
                *table.rows[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="differs on replay"):
        verify_joint_state_count_receipt(receipt, changed)
    resealed = dict(receipt, common_cause_model_fitted=True)
    resealed["record_digest"] = digest({k: v for k, v in resealed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="differs on replay"):
        verify_joint_state_count_receipt(resealed, table)


def test_verified_joint_count_fta_builder_requires_and_retains_source_receipt():
    table = joint_count_table()
    _, receipt = joint_states_from_counts(table)
    evidence = EvidenceRef(id="joint-counts", citation="Authored count fixture", origin="synthetic")
    scoped = context(evidence=(evidence,))
    events = (
        probability("A", 0.15, context=scoped, evidence_ids=("joint-counts",)),
        probability("B", 0.1, context=scoped, evidence_ids=("joint-counts",)),
    )
    root = FaultNode(kind="OR", children=(node("A"), node("B")))
    request, bound_receipt = fault_tree_request_from_verified_joint_counts(
        root, events, table, receipt
    )
    assert request.joint_state_receipt_digest == receipt["record_digest"]
    assert bound_receipt == receipt
    with pytest.raises(ValueError, match="receipt seal changed"):
        fault_tree_request_from_verified_joint_counts(root, events, table, {**receipt, "total": 99})


def event_request(manual=None):
    automatic = probability("automatic", 0.1, dependency_groups=("automatic_group",))
    manual = manual or probability(
        "manual", condition_id="auto_failed", role="response", dependency_groups=("manual_group",)
    )
    return EventTreeRequest(
        events=(automatic, manual),
        partition_evidence_id="assumed:exhaustive_partition",
        branches=(
            EventBranch(
                id="auto_ok",
                probability_id="automatic",
                complement=True,
                next_condition_id="auto_isolated",
                consequence_id="auto_isolated",
            ),
            EventBranch(
                id="auto_failed",
                probability_id="automatic",
                next_condition_id="auto_failed",
                children=(
                    EventBranch(
                        id="manual_ok",
                        probability_id="manual",
                        complement=True,
                        next_condition_id="manual_isolated",
                        consequence_id="manual_isolated",
                    ),
                    EventBranch(
                        id="manual_failed",
                        probability_id="manual",
                        next_condition_id="not_isolated",
                        consequence_id="not_isolated",
                    ),
                ),
            ),
        ),
    )


@pytest.fixture
def owner():
    calls = []

    def record(name, result):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return run

    rows = (
        {"path": ("auto_ok",), "annual_frequency_per_year": 0.009, "consequence_id": "auto_isolated"},
        {
            "path": ("auto_failed", "manual_ok"),
            "annual_frequency_per_year": 0.0008,
            "consequence_id": "manual_isolated",
        },
        {
            "path": ("auto_failed", "manual_failed"),
            "annual_frequency_per_year": 0.0002,
            "consequence_id": "not_isolated",
        },
    )
    api = SimpleNamespace(
        Event=lambda key: ("event", key),
        Gate=lambda *args: ("gate", *args),
        Branch=lambda *args, **kwargs: (args, kwargs),
        Barrier=lambda *args, **kwargs: (args, kwargs),
        fault_probability=record("independent", 0.123),
        fault_probability_joint=record("joint", 0.123),
        demand_frequency=record("annualize", 0.002),
        event_tree=record("eta", rows),
        lopa_screen=record(
            "lopa",
            {
                "status": "declared_independent_screen",
                "residual_frequency_per_year": 0.00005,
                "certified_ipl_claim": False,
            },
        ),
    )
    client = HRARiskClient(api=api, provider={"id": "policy-spy", "version": "test"})
    return client, calls


def test_repeated_basic_event_is_one_owner_identity_and_result_contains_replay_input(owner):
    client, calls = owner
    result = client.fault_tree(fault_request(), context())
    assert result["probability"]["probability"] == 0.123
    assert calls[0][0] == "independent" and calls[0][1][1] == {"A": 0.2, "B": 0.1}
    assert digest(result["inputs"]) == result["input_digest"]
    assert result["probability"]["source_payload"]["request"] == result["inputs"]


@pytest.mark.parametrize(
    "change",
    [
        {"condition_id": "different"},
        {"opportunity_id": "different"},
        {"opportunity_definition": "Different denominator"},
        {"measure": "mission_probability"},
        {"protection_ids": ("A",)},
    ],
)
def test_incompatible_fault_probabilities_cannot_reach_owner(owner, change):
    client, calls = owner
    request = fault_request(events=(probability(), probability("B", 0.1, **change)))
    assert client.fault_tree(request, context())["status"] == "withheld"
    assert not calls


def test_copied_or_nested_scope_mutations_are_detected_before_evaluation(owner):
    client, _ = owner
    changed = probability().model_copy(update={"condition_id": "other"})
    with pytest.raises(ValueError, match="scope or lineage changed"):
        client.annualize(changed, None)
    rate = frequency()
    rate.context.metadata["new_scope"] = "accidental mutable edit"
    with pytest.raises(ValueError, match="scope or lineage changed"):
        client.event_tree(rate, event_request())
    with pytest.raises(ValueError, match="context differs"):
        client.fault_tree(fault_request(), context(asset_id="other_pipe"))


def test_shared_dependencies_need_joint_states_and_joint_marginals_are_bound(owner):
    client, calls = owner
    events = tuple(probability(k, 0.1, dependency_groups=("common_supply",)) for k in ("A", "B"))
    assert client.fault_tree(fault_request(events), context())["status"] == "withheld"
    joint = (
        JointState(states={"A": False, "B": False}, probability=0.9),
        JointState(states={"A": True, "B": True}, probability=0.1),
    )
    request = fault_request(events, dependence="joint_states", joint_states=joint)
    assert client.fault_tree(request, context())["status"] == "exchange_checked"
    assert [c[0] for c in calls] == ["joint"]
    wrong = fault_request((probability(), events[1]), dependence="joint_states", joint_states=joint)
    assert client.fault_tree(wrong, context())["status"] == "withheld"


@pytest.mark.parametrize(
    "rows",
    [
        (({"A": False, "B": False}, 0.8), ({"A": True, "B": True}, 0.1)),
        (({"A": False, "B": False}, 0.8), ({"A": True, "B": True}, 0.1), ({"A": True, "B": True}, 0.1)),
        (({"A": False}, 0.9), ({"A": True}, 0.1)),
    ],
)
def test_invalid_joint_distributions_are_rejected_without_native_evaluation(owner, rows):
    client, calls = owner
    request = fault_request(
        dependence="joint_states", joint_states=tuple(JointState(states=s, probability=p) for s, p in rows)
    )
    with pytest.raises(ValueError, match="joint state"):
        client.fault_tree(request, context())
    assert not calls


def test_tree_budget_and_exact_evidence_set_are_enforced(owner):
    client, calls = owner
    with pytest.raises(ValueError, match="state budget"):
        client.fault_tree(fault_request(maximum_states=3), context())
    with pytest.raises(ValueError, match="structure budget"):
        client.fault_tree(fault_request(maximum_tree_nodes=1), context())
    with pytest.raises(ValueError, match="distinct event IDs"):
        client.fault_tree(fault_request(root=node("A")), context())
    assert not calls


def test_annualization_requires_matching_documented_opportunities(owner):
    client, calls = owner
    event = probability()
    assert client.annualize(event, None)["status"] == "withheld"
    exposure = DemandExposure(
        opportunities_per_year=0.01,
        opportunity_id="one_demand",
        opportunity_definition="One demand",
        condition_id="demand",
        evidence_id="exposure",
    )
    result = client.annualize(event, exposure)
    assert result["frequencies"][0]["annual_frequency_per_year"] == 0.002
    assert result["frequencies"][0]["observed_annual_frequency"] is False
    assert calls[0][1] == (0.01, 0.2)
    assert (
        client.annualize(event, exposure.model_copy(update={"condition_id": "other"}))["status"] == "withheld"
    )


def test_conditional_eta_preserves_all_ancestor_dependencies_and_consumed_credits(owner):
    client, _ = owner
    result = client.event_tree(frequency(), event_request())
    failure = result["frequencies"][-1]
    assert result["status"] == "exchange_checked"
    assert failure["consumed_event_ids"] == ["automatic", "manual"]
    assert failure["dependency_groups"] == ["automatic_group", "manual_group"]
    assert failure["endpoint"] == "consequence_outcome"
    assert result["native_result"]["frequency_conservation_error_per_year"] == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize(
    "manual",
    [
        probability("manual", role="response"),
        probability("manual", role="response", condition_id="auto_failed", protection_ids=("automatic",)),
    ],
)
def test_wrong_eta_condition_or_duplicate_recovery_credit_is_withheld(owner, manual):
    client, calls = owner
    assert client.event_tree(frequency(), event_request(manual))["status"] == "withheld"
    assert not calls


def test_lopa_preserves_ledger_and_does_not_create_a_release_frequency(owner):
    client, calls = owner
    events = (probability(), probability("B", 0.05))
    layers = tuple(
        ProtectionLayer(
            id=k, probability_id=k, kind="automatic", independence_evidence_id="assumed:separate_supply"
        )
        for k in ("A", "B")
    )
    result = client.lopa(frequency(), events, layers)
    assert calls[0][0] == "lopa"
    assert result["frequencies"][0]["endpoint"] == "protection_failure_combination"
    assert result["frequencies"][0]["consumed_protection_ids"] == ["A", "B"]
    assert result["native_result"]["certified_ipl_claim"] is False
    assert client.lopa(frequency(consumed_protection_ids=("A",)), events, layers)["status"] == "withheld"
    assert client.lopa(frequency(endpoint="event_occurrence"), events, layers)["status"] == "withheld"
    relabeled = (probability(role="response"), events[1])
    assert client.lopa(frequency(), relabeled, layers)["status"] == "withheld"
    assert len(calls) == 1


def test_handoff_returns_copies_and_rejects_changed_native_or_evidence():
    packet = {"failure_probability_per_demand": 0.2}
    body = {"native_payload": packet.copy(), "native_payload_digest": digest(packet)}
    handoff = RiskHandoff(packet, {**body, "result_digest": digest(body)})
    handoff.record()["native_payload"]["failure_probability_per_demand"] = 0.8
    assert handoff.record()["native_payload"]["failure_probability_per_demand"] == 0.2
    packet["failure_probability_per_demand"] = 0.5
    with pytest.raises(ValueError, match="receiving demand packet"):
        handoff.record()


def test_json_jobs_keep_boundary_contracts_and_reject_unknown_operations_before_import(owner):
    client, calls = owner
    result = run_risk_job(
        {
            "operation": "fault_tree",
            "request": fault_request().model_dump(mode="json"),
            "context": context().model_dump(mode="json"),
        },
        client=client,
    )
    assert result["status"] == "exchange_checked" and calls[0][0] == "independent"
    for job in ({"operation": "unknown"}, {"operation": "fault_tree", "execute": "anything"}):
        with pytest.raises(ValueError, match="exact input fields"):
            run_risk_job(job)


def test_small_annual_frequencies_do_not_accept_a_large_relative_conservation_error(owner):
    client, _ = owner
    source = {"annual_frequency_per_year": 1e-20}
    tiny_rate = frequency(
        annual_frequency_per_year=1e-20, source_payload=source, source_digest=digest(source)
    )
    with pytest.raises(ValueError, match="conserve initiating frequency"):
        client.event_tree(tiny_rate, event_request())


@pytest.mark.parametrize("change", ["duplicate_path", "renamed_consequence"])
def test_owner_cannot_change_or_duplicate_terminal_outcomes(owner, change):
    client, _ = owner
    rows = [dict(row) for row in client.event_tree(frequency(), event_request())["native_result"]["rows"]]
    if change == "duplicate_path":
        rows.append(dict(rows[0]))
    else:
        rows[0]["consequence_id"] = "different_outcome"
    client.api.event_tree = lambda *args: rows
    with pytest.raises(ValueError, match="paths differ|consequence identity"):
        client.event_tree(frequency(), event_request())
