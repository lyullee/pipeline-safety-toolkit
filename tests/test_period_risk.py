"""Scope/lineage boundaries use spies; native Boolean numbers run in check_period_risk.py."""

import copy
import json
import runpy
from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.period_risk import (
    PeriodEventBinding,
    PeriodLogicRequest,
    bind_creep_period_event,
    evaluate_period_logic,
)
from pipeline_integrity.risk_exchange import FaultNode, HRARiskClient, JointState, RiskProbability


def base():
    cases, context = runpy.run_path("examples/period_risk.py")["make_cases"]()
    return cases["independent_or"], context


@pytest.fixture
def owner():
    calls = []

    def native(tree, values):
        calls.append((tree, values))
        return 0.37

    return HRARiskClient(
        api=SimpleNamespace(
            Event=lambda key: ("event", key),
            Gate=lambda kind, children, threshold: (kind, children, threshold),
            fault_probability=native,
            fault_probability_joint=native,
        ),
        provider=dict(id="boundary_spy", version="1"),
    ), calls


def replace_event(request, **changes):
    first = request.events[0].model_copy(update=changes)
    return request.model_copy(update={"events": (first, *request.events[1:])})


def change_probability(binding, **changes):
    data = binding.probability.model_dump(mode="json")
    data.update(changes, binding_digest=None)
    return RiskProbability.model_validate_json(json.dumps(data))


def test_delegate_without_changing_source_measure_or_context(owner):
    request, context = base()
    snapshot = request.model_dump_json()
    result = evaluate_period_logic(request, context, client=owner[0])
    assert result["status"] == "exchange_checked" and result["probability"]["probability"] == 0.37
    assert owner[1][0][1] == {"A": 0.2, "B": 0.1}
    p = result["probability"]
    assert p["measure"] == "mission_probability" and p["qualification"] == "research"
    assert p["source_payload"]["atomic_event_ids"] == ["A", "B"]
    assert not result["temporal_coincidence_or_sequence_modelled"]
    assert not result["annual_frequency_inferred"] and not result["loss_of_containment_inferred"]
    assert request.model_dump_json() == snapshot


@pytest.mark.parametrize(
    "changes",
    [
        {"source_start_s": 11.0},
        {"source_finish_s": 21.0},
        {"common_condition_id": "other_survival"},
        {"period_asset_and_common_conditioning_reviewed": False},
    ],
)
def test_unresolved_interval_or_common_condition_withheld_before_owner(changes):
    request, context = base()
    result = evaluate_period_logic(replace_event(request, **changes), context)
    assert result["status"] == "withheld" and result["provider"] is None


@pytest.mark.parametrize("measure", ["demand_failure_probability", "point_unavailability"])
def test_other_probability_measures_are_not_relabelled(measure):
    request, context = base()
    event = change_probability(request.events[0], measure=measure)
    result = evaluate_period_logic(replace_event(request, probability=event), context)
    assert result["status"] == "withheld"


@pytest.mark.parametrize("change", [{"target_context_digest": "0" * 64}])
def test_wrong_asset_scenario_mapping_is_an_error(change):
    request, context = base()
    with pytest.raises(ValueError, match="different complete context"):
        evaluate_period_logic(replace_event(request, **change), context)


def test_clock_transform_is_never_assumed():
    request, context = base()
    wrong = request.events[0].probability.context.model_copy(update={"clock_id": "other"})
    event = change_probability(request.events[0], context=wrong.model_dump(mode="json"))
    with pytest.raises(ValueError, match="source event clock"):
        evaluate_period_logic(replace_event(request, probability=event), context)


def test_period_text_cannot_change_under_the_same_condition_id_after_review():
    request, context = base()
    period = request.period.model_copy(update={"common_conditioning": "A different initial survival state"})
    with pytest.raises(ValueError, match="definition changed"):
        evaluate_period_logic(request.model_copy(update={"period": period}), context)


@pytest.mark.parametrize("review", ["event_logic_reviewed"])
def test_logic_review_required_without_optional_owner(review):
    request, context = base()
    assert evaluate_period_logic(request.model_copy(update={review: False}), context)["status"] == "withheld"


def test_missing_or_conflicting_evidence_is_not_filled():
    request, context = base()
    with pytest.raises(ValueError, match="evidence is absent"):
        evaluate_period_logic(replace_event(request, mapping_evidence_ids=("missing",)), context)
    altered = context.evidence[0].model_copy(update={"citation": "changed evidence"})
    wrong = context.model_copy(update={"evidence": (altered, *context.evidence[1:])})
    r = request.model_copy(
        update={
            "events": tuple(
                b.model_copy(update={"target_context_digest": digest(wrong)}) for b in request.events
            )
        }
    )
    with pytest.raises(ValueError, match="unchanged"):
        evaluate_period_logic(r, wrong)


def test_bound_probability_mutation_fails_even_with_nested_model_copy():
    request, context = base()
    changed = request.events[0].probability.model_copy(update={"probability": 0.9})
    with pytest.raises(ValueError):
        evaluate_period_logic(replace_event(request, probability=changed), context)


def test_shared_model_cannot_be_assumed_independent_but_explicit_joint_can(owner):
    request, context = base()
    bindings = tuple(
        b.model_copy(update={"probability": change_probability(b, dependency_groups=["shared_model"])})
        for b in request.events
    )
    r = request.model_copy(update={"events": bindings})
    assert evaluate_period_logic(r, context)["status"] == "withheld"
    joint = runpy.run_path("examples/period_risk.py")["make_cases"]()[0]["joint_or"]
    result = evaluate_period_logic(
        r.model_copy(update={"dependence": "joint_states", "joint_states": joint.joint_states}),
        context,
        client=owner[0],
    )
    assert result["status"] == "exchange_checked" and len(owner[1]) == 1
    assert result["probability"]["dependency_groups"] == ["shared_model"]


@pytest.mark.parametrize(
    "joint",
    [
        (),
        (
            JointState(states=dict(A=False, B=False), probability=0.9),
            JointState(states=dict(A=True, B=True), probability=0.1),
        ),
    ],
)
def test_missing_or_conflicting_joint_marginals_are_withheld(joint):
    request, context = base()
    assert (
        evaluate_period_logic(
            request.model_copy(update={"dependence": "joint_states", "joint_states": joint}), context
        )["status"]
        == "withheld"
    )


@pytest.mark.parametrize(
    "joint",
    [
        (JointState(states=dict(A=False), probability=1.0),),
        (JointState(states=dict(A=False, B=False), probability=0.5),) * 2,
        (JointState(states=dict(A=False, B=False), probability=0.9),),
    ],
)
def test_bad_joint_keys_duplicate_states_and_total_are_errors(joint):
    request, context = base()
    with pytest.raises(ValueError):
        evaluate_period_logic(
            request.model_copy(update={"dependence": "joint_states", "joint_states": joint}), context
        )


def test_joint_rows_and_independence_cannot_both_be_used():
    request, context = base()
    joint = runpy.run_path("examples/period_risk.py")["make_cases"]()[0]["joint_or"]
    assert (
        evaluate_period_logic(request.model_copy(update={"joint_states": joint.joint_states}), context)[
            "status"
        ]
        == "withheld"
    )


def test_same_physical_event_under_another_id_is_withheld():
    request, context = base()
    first, last = request.events
    alias = last.model_copy(
        update={
            "probability": change_probability(first, id="B"),
            "physical_scope_id": first.physical_scope_id,
        }
    )
    assert (
        evaluate_period_logic(request.model_copy(update={"events": (first, alias)}), context)["status"]
        == "withheld"
    )


def test_repeated_tree_identity_remains_one_native_event(owner):
    request, context = base()
    leaf = FaultNode(kind="event", event_id="A")
    r = request.model_copy(
        update={"events": (request.events[0],), "root": FaultNode(kind="OR", children=(leaf, leaf))}
    )
    assert evaluate_period_logic(r, context, client=owner[0])["status"] == "exchange_checked"
    assert owner[1][0][1] == {"A": 0.2}


@pytest.mark.parametrize(
    "changes",
    [
        {"events": ()},
        {"maximum_states": 3},
        {"maximum_events": 1},
        {"maximum_tree_nodes": 2},
        {"top_id": "A"},
        {"root": FaultNode(kind="event", event_id="unknown")},
    ],
)
def test_structure_and_probability_budgets_are_checked_before_owner(changes):
    request, context = base()
    with pytest.raises(ValueError):
        evaluate_period_logic(request.model_copy(update=changes), context)


def test_duplicate_event_bindings_and_protection_effects_are_not_reapplied():
    request, context = base()
    with pytest.raises(ValueError, match="distinct input identities"):
        evaluate_period_logic(request.model_copy(update={"events": (request.events[0],) * 2}), context)
    bindings = tuple(
        b.model_copy(update={"probability": change_probability(b, protection_ids=["one_barrier"])})
        for b in request.events
    )
    assert (
        evaluate_period_logic(request.model_copy(update={"events": bindings}), context)["status"]
        == "withheld"
    )


def test_aggregate_and_its_atomic_input_cannot_double_count_lineage(owner):
    request, context = base()
    result = evaluate_period_logic(request, context, client=owner[0])
    p = RiskProbability.model_validate_json(json.dumps(result["probability"]))
    aggregate = PeriodEventBinding(
        probability=p,
        source_start_s=10.0,
        source_finish_s=20.0,
        physical_scope_id=context.asset_id,
        endpoint_id=request.top_endpoint_id,
        common_condition_id=request.period.common_condition_id,
        target_context_digest=digest(context),
        target_period_digest=digest(request.period),
        mapping_evidence_ids=("mapping",),
        period_asset_and_common_conditioning_reviewed=True,
    )
    r = request.model_copy(
        update={
            "events": (aggregate, request.events[0]),
            "root": FaultNode(
                kind="OR",
                children=(FaultNode(kind="event", event_id=p.id), FaultNode(kind="event", event_id="A")),
            ),
            "top_id": "new_top",
        }
    )
    assert evaluate_period_logic(r, context)["status"] == "withheld"
    with pytest.raises(ValueError, match="original calculation"):
        evaluate_period_logic(
            r.model_copy(
                update={"events": (aggregate.model_copy(update={"endpoint_id": "other"}), request.events[0])}
            ),
            context,
        )
    only_aggregate = r.model_copy(
        update={"events": (aggregate,), "root": FaultNode(kind="event", event_id=p.id), "top_id": "A"}
    )
    with pytest.raises(ValueError, match="atomic ancestor"):
        evaluate_period_logic(only_aggregate, context)
    changed_period = request.period.model_copy(update={"common_conditioning": "Changed initial state"})
    with pytest.raises(ValueError, match="original calculation"):
        evaluate_period_logic(
            only_aggregate.model_copy(
                update={
                    "top_id": "another_top",
                    "period": changed_period,
                    "events": (
                        aggregate.model_copy(update={"target_period_digest": digest(changed_period)}),
                    ),
                }
            ),
            context,
        )


def test_creep_factory_preserves_complete_source_and_refuses_changed_scope():
    from test_creep_exchange import prediction_fixture

    from pipeline_integrity.contracts import RunContext
    from pipeline_integrity.creep_exchange import creep_risk_probability
    from pipeline_integrity.period_risk import RiskPeriod

    prediction, mapping = prediction_fixture()
    record = creep_risk_probability(prediction, mapping)
    ctx = RunContext.model_validate_json(json.dumps(prediction["context"]))
    period = RiskPeriod(
        id="creep_period",
        clock_id=ctx.clock_id,
        start_s=10.0,
        finish_s=13.0,
        common_condition_id="survival",
        common_conditioning="Declared single component survival",
        evidence_ids=("reference",),
    )
    binding = bind_creep_period_event(
        record,
        period=period,
        target_context=ctx,
        mapping_evidence_ids=("reference",),
        common_conditioning_reviewed=True,
    )
    assert binding.source_start_s == 10.0 and binding.source_finish_s == 13.0
    req = PeriodLogicRequest(
        root=FaultNode(kind="event", event_id=binding.probability.id),
        events=(binding,),
        period=period,
        top_id="top",
        top_endpoint_id="creep_rupture",
        top_definition="One declared creep rupture",
        logic_evidence_id="reference",
        dependence="declared_independent",
        dependence_evidence_id="reference",
        event_logic_reviewed=True,
    )
    with pytest.raises(ValueError, match="differs from its source"):
        evaluate_period_logic(
            req.model_copy(update={"events": (binding.model_copy(update={"source_finish_s": 14.0}),)}), ctx
        )
    changed = copy.deepcopy(record)
    changed["mapping"]["period_finish_s"] = 14.0
    with pytest.raises(ValueError, match="changed after sealing"):
        bind_creep_period_event(
            changed, period=period, target_context=ctx, mapping_evidence_ids=("reference",)
        )
