"""Boundary policy with fixed owner replies; native numerical checks live in the research runner."""

import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import Field

from pipeline_integrity.contracts import Contract, EvidenceRef, Fraction, Name, RunContext, digest
from pipeline_integrity.response_exchange import (
    CognitiveResponse,
    CognitiveStage,
    DeadlineEffect,
    DeadlineScenario,
    DeadlineScenarioSet,
    EmpiricalResponse,
    HumanResponseClient,
    HumanResponseRequest,
    HumanResponseResult,
    LognormalResponse,
    RecoveryEffect,
    ResponseObservation,
    ResponseWindow,
)
from pipeline_integrity.risk_exchange import HRARiskClient


class EstimateSpy(Contract):
    event_id: Name
    method: Name = "fixed_owner_reply"
    probability: Fraction
    conditioning: Name = "declared demand"
    evidence_ids: tuple[Name, ...] = ("e",)
    includes: tuple[Literal["timing", "recovery", "dependency"], ...] = ()
    status: Literal["assumed", "research"] = "assumed"
    trace: dict = Field(default_factory=dict)


class EventSpy(Contract):
    id: Name = "hfe"
    role: Literal["response"] = "response"
    conditioning: Name = "declared demand"
    failure_definition: Name = "wrong or late response in the authored reference"
    opportunity_definition: Name = "one authored response demand"
    deadline_s: float | None = 10.0
    includes_timing: bool = True
    includes_recovery: bool = False
    dependency_group: str | None = None


def case():
    context = RunContext(
        asset_id="a",
        scenario_id="s",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(EvidenceRef(id="e", citation="Authored policy test", origin="synthetic"),),
    )
    request = HumanResponseRequest(
        condition_id="condition",
        opportunity_id="opportunity",
        protection_ids=("manual",),
        applicability_evidence_id="e",
        applicability_rationale="Authored policy reference only",
        effect=DeadlineEffect(
            window=ResponseWindow(clock_id="elapsed", cue_time_s=5.0, limit_time_s=15.0, evidence_id="e"),
            time_model=LognormalResponse(median_s=10.0, log_sigma=0.5, evidence_id="e"),
            population_definition="Completion conditional on no initial failure",
        ),
    )
    return EstimateSpy(event_id="hfe", probability=0.2), EventSpy(), request, context


def client(*, native_probability=0.6, bin_mass=1.0):
    calls = []

    def fixed_effect(initial, *args, evidence_id):
        calls.append((args, evidence_id))
        effect = "recovery" if isinstance(args[0], float) else "timing"
        return EstimateSpy(
            event_id=initial.event_id,
            probability=native_probability,
            evidence_ids=tuple(dict.fromkeys((*initial.evidence_ids, evidence_id))),
            includes=initial.includes + (effect,),
            status="research",
            trace={"owner": "fixed reply"},
        )

    api = SimpleNamespace(
        Estimate=EstimateSpy,
        HumanFailureEvent=EventSpy,
        LognormalTime=lambda *args: ("native_lognormal", args),
        WeibullTime=lambda *args: ("native_weibull", args),
        KaplanMeierTime=lambda *args: ("native_km", args),
        apply_deadline=fixed_effect,
        apply_recovery=fixed_effect,
        response_bins=lambda *args: [{"probability": bin_mass}],
        cognitive_chain=lambda event_id, stages, **kwargs: EstimateSpy(
            event_id=event_id,
            probability=0.28,
            conditioning=kwargs["conditioning"],
            evidence_ids=kwargs["evidence_ids"],
            status="research",
            trace={
                "first_failure_probabilities": {"detect": 0.1, "act": 0.18},
                "all_stages_succeed": 0.72,
                "stage_order": list(stages),
            },
        ),
    )
    provider = {"id": "policy_spy", "version": "test"}
    risk = HRARiskClient(api=api, provider=provider)
    return HumanResponseClient(api=api, risk_client=risk, provider=provider), calls


def test_native_result_and_explicit_clock_binding_survive_risk_export():
    owner, calls = client()
    result = owner.evaluate(*case())
    assert calls[0][0][1] == 10.0
    assert result.native_estimate["trace"] == {"owner": "fixed reply"}
    risk = result.risk_probability()
    binding = risk.source_payload["estimate"]["trace"]["pipeline_response_binding"]
    assert binding["request"]["effect"]["window"]["cue_time_s"] == 5.0
    assert risk.includes == ("timing",) and risk.qualification == "research"
    assert not result.field_calibrated and not result.work_authorized


def test_single_response_result_replays_owner_inputs_and_detects_resealed_change():
    owner, _ = client()
    result = owner.evaluate(*case())
    assert owner.verify_result(result) == {"valid": True}

    altered_native = dict(result.native_estimate, probability=0.37)
    changed = result.model_copy(update={"native_estimate": altered_native})
    body = changed.model_dump(mode="json", exclude={"result_digest"})
    resealed = HumanResponseResult.model_validate_json(json.dumps(body | {"result_digest": digest(body)}))
    replay = owner.verify_result(resealed)
    assert replay["valid"] is False
    assert "replay differs" in replay["reason"]


def test_reviewed_deadline_scenarios_integrate_owner_outputs_and_replay():
    owner, _ = client()
    seen_models = []

    def deadline_effect(initial, model, available_s, *, evidence_id):
        seen_models.append(model)
        probability = 0.8 if available_s < 10 else 0.4
        return EstimateSpy(
            event_id=initial.event_id,
            probability=probability,
            evidence_ids=tuple(dict.fromkeys((*initial.evidence_ids, evidence_id))),
            includes=initial.includes + ("timing",),
            status="research",
            trace={"owner_fixed_deadline_s": available_s},
        )

    owner.api.apply_deadline = deadline_effect
    initial, event, request, context = case()
    context = context.model_copy(
        update={
            "evidence": context.evidence
            + (EvidenceRef(id="deadline_set", citation="Authored scenario set", origin="synthetic"),)
        }
    )
    scenarios = DeadlineScenarioSet(
        distribution_evidence_id="e",
        available_time_population_definition="available-time distribution for one declared response opportunity",
        scenarios=(
            DeadlineScenario(
                id="short", available_time_s=5, probability_mass=0.25, evidence_id="deadline_set"
            ),
            DeadlineScenario(
                id="long",
                available_time_s=15,
                probability_mass=0.75,
                evidence_id="deadline_set",
                time_model=LognormalResponse(median_s=20, log_sigma=0.4, evidence_id="deadline_set"),
                response_population_definition="no-initial-failure opportunities under the long-time condition",
            ),
        ),
    )
    result = owner.evaluate_deadline_scenarios(initial, event, request, scenarios, context)
    assert result.status == "exchange_checked"
    assert result.probability.probability == pytest.approx(0.5)
    assert seen_models[0][1][0] == 10.0 and seen_models[1][1][0] == 20.0
    assert result.aggregate_native_estimate["trace"]["pipeline_deadline_scenario_aggregation"]
    assert owner.verify_deadline_scenario_result(result)["valid"]
    changed = result.model_copy(update={"scenario_results": ()})
    assert not owner.verify_deadline_scenario_result(changed)["valid"]


def test_deadline_scenario_set_rejects_unnormalized_weights():
    with pytest.raises(ValueError, match="sum to one"):
        DeadlineScenarioSet(
            distribution_evidence_id="e",
            available_time_population_definition="one declared response opportunity",
            scenarios=(
                DeadlineScenario(id="short", available_time_s=5, probability_mass=0.4, evidence_id="e"),
            ),
        )
    with pytest.raises(ValueError, match="finite and positive"):
        DeadlineScenario(id="zero_weight", available_time_s=5, probability_mass=0, evidence_id="e")
    with pytest.raises(ValueError, match="finite"):
        DeadlineScenario(
            id="infinite_time", available_time_s=float("inf"), probability_mass=1, evidence_id="e"
        )
    with pytest.raises(ValueError, match="own population definition"):
        DeadlineScenario(
            id="unbound_population",
            available_time_s=5,
            probability_mass=1,
            evidence_id="e",
            time_model=LognormalResponse(median_s=3, log_sigma=0.4, evidence_id="e"),
        )


def test_deadline_scenario_set_enforces_caller_evaluation_budget():
    with pytest.raises(ValueError, match="evaluation budget"):
        DeadlineScenarioSet(
            distribution_evidence_id="e",
            available_time_population_definition="one declared response opportunity",
            maximum_scenarios=1,
            scenarios=(
                DeadlineScenario(id="short", available_time_s=5, probability_mass=0.5, evidence_id="e"),
                DeadlineScenario(id="long", available_time_s=15, probability_mass=0.5, evidence_id="e"),
            ),
        )
    with pytest.raises(ValueError, match="empirical-observation budget"):
        DeadlineScenarioSet(
            distribution_evidence_id="e",
            available_time_population_definition="one declared response opportunity",
            maximum_empirical_observations=1,
            scenarios=tuple(
                DeadlineScenario(
                    id=trial,
                    response_population_definition="authored empirical response population",
                    available_time_s=5,
                    probability_mass=0.5,
                    evidence_id="e",
                    time_model=EmpiricalResponse(
                        observations=(
                            ResponseObservation(
                                id=trial,
                                duration_s=2,
                                outcome="correct_completion",
                                evidence_id="e",
                            ),
                        ),
                        censoring_review_evidence_id="e",
                    ),
                )
                for trial in ("trial_a", "trial_b")
            ),
        )


def test_unresolved_competing_response_blocks_entire_scenario_probability():
    owner, _ = client()
    initial, event, request, context = case()
    context = context.model_copy(
        update={
            "evidence": context.evidence
            + (
                EvidenceRef(id="wrong", citation="Authored wrong-response trial", origin="synthetic"),
                EvidenceRef(id="censor_review", citation="Authored censoring review", origin="synthetic"),
            )
        }
    )
    scenarios = DeadlineScenarioSet(
        distribution_evidence_id="e",
        available_time_population_definition="one declared response opportunity",
        scenarios=(
            DeadlineScenario(id="ordinary", available_time_s=10, probability_mass=0.5, evidence_id="e"),
            DeadlineScenario(
                id="wrong_response",
                response_population_definition="authored censored response population",
                available_time_s=10,
                probability_mass=0.5,
                evidence_id="e",
                time_model=EmpiricalResponse(
                    observations=(
                        ResponseObservation(
                            id="trial_wrong",
                            duration_s=3,
                            outcome="wrong_response",
                            evidence_id="wrong",
                        ),
                    ),
                    censoring_review_evidence_id="censor_review",
                ),
            ),
        ),
    )
    result = owner.evaluate_deadline_scenarios(initial, event, request, scenarios, context)
    assert result.status == "needs_data" and result.probability is None
    assert any("wrong responses/unknown" in issue for issue in result.issues)


@pytest.mark.parametrize("effect", ["timing", "recovery"])
def test_already_included_effects_cannot_be_applied_again_or_in_an_unsupported_order(effect):
    initial, event, request, context = case()
    owner, calls = client()
    with pytest.raises(ValueError, match="already included"):
        owner.evaluate(initial.model_copy(update={"includes": (effect,)}), event, request, context)
    assert not calls


@pytest.mark.parametrize("field,value", [("event_id", "other"), ("conditioning", "other")])
def test_source_identity_and_conditioning_are_not_relabelled(field, value):
    initial, event, request, context = case()
    owner, _ = client()
    with pytest.raises(ValueError, match="same identity"):
        owner.evaluate(initial.model_copy(update={field: value}), event, request, context)


@pytest.mark.parametrize(
    "changes",
    [
        {"deadline_s": 15.0},
        {"deadline_s": None},
        {"includes_timing": False},
        {"includes_recovery": True},
    ],
)
def test_final_event_must_match_the_effect_and_cue_relative_deadline(changes):
    initial, event, request, context = case()
    owner, _ = client()
    with pytest.raises(ValueError):
        owner.evaluate(initial, event.model_copy(update=changes), request, context)


def test_clock_evidence_and_nested_python_types_are_revalidated_before_owner_calls():
    initial, event, request, context = case()
    owner, calls = client()
    for updated in [
        context.model_copy(update={"clock_id": "other"}),
        context.model_copy(update={"evidence": ()}),
    ]:
        with pytest.raises(ValueError):
            owner.evaluate(initial, event, request, updated)
    bad_window = request.effect.window.model_copy(update={"cue_time_s": True})
    bad = request.model_copy(update={"effect": request.effect.model_copy(update={"window": bad_window})})
    with pytest.raises(ValueError):
        owner.evaluate(initial, event, bad, context)
    with pytest.raises(ValueError):
        owner.evaluate(initial.model_copy(update={"probability": True}), event, request, context)
    assert not calls


@pytest.mark.parametrize("outcome", ["wrong_response", "unknown"])
def test_competing_wrong_actions_are_not_reclassified_as_censoring(outcome):
    initial, event, request, context = case()
    empirical = EmpiricalResponse(
        observations=(ResponseObservation(id="trial", duration_s=3.0, outcome=outcome, evidence_id="e"),),
        censoring_review_evidence_id="e",
    )
    request = request.model_copy(
        update={"effect": request.effect.model_copy(update={"time_model": empirical})}
    )
    owner, calls = client()
    result = owner.evaluate(initial, event, request, context)
    assert result.status == "needs_data" and result.native_estimate is None and not calls
    with pytest.raises(ValueError, match="unresolved"):
        result.risk_probability()


def test_censoring_flags_reach_native_model_without_replacement_by_completed_times():
    initial, event, request, context = case()
    model = EmpiricalResponse(
        observations=tuple(
            ResponseObservation(id=str(i), duration_s=float(i + 1), outcome=outcome, evidence_id="e")
            for i, outcome in enumerate(["correct_completion", "right_censored"])
        ),
        censoring_review_evidence_id="e",
    )
    request = request.model_copy(update={"effect": request.effect.model_copy(update={"time_model": model})})
    owner, calls = client()
    owner.evaluate(initial, event, request, context)
    assert calls[0][0][0] == ("native_km", ([1.0, 2.0], [True, False]))


def test_untimed_recovery_is_separate_and_rejects_a_timing_claim():
    initial, event, request, context = case()
    request = request.model_copy(
        update={
            "effect": RecoveryEffect(
                failure_to_recover_given_error=0.25,
                evidence_id="e",
                population_definition="Initial errors only",
            )
        }
    )
    owner, calls = client(native_probability=0.05)
    with pytest.raises(ValueError, match="untimed recovery"):
        owner.evaluate(initial, event, request, context)
    event = event.model_copy(update={"deadline_s": None, "includes_timing": False, "includes_recovery": True})
    result = owner.evaluate(initial, event, request, context)
    assert calls[0][0] == (0.25,) and result.risk_probability().probability == 0.05


@pytest.mark.parametrize("native_probability,bin_mass", [(0.1, 1.0), (0.6, 0.9)])
def test_bad_owner_probability_direction_or_partition_is_rejected(native_probability, bin_mass):
    initial, event, request, context = case()
    request = request.model_copy(
        update={"effect": request.effect.model_copy(update={"bin_boundaries_s": (10.0,)})}
    )
    owner, _ = client(native_probability=native_probability, bin_mass=bin_mass)
    with pytest.raises(ValueError):
        owner.evaluate(initial, event, request, context)


def test_tampering_is_detected_and_output_is_serializable():
    owner, _ = client()
    result = owner.evaluate(*case())
    assert (
        result.__class__.model_validate_json(result.model_dump_json()).risk_probability().probability == 0.6
    )
    result.native_estimate["probability"] = 0.9
    with pytest.raises(ValueError, match="source record changed"):
        result.risk_probability()


def test_clock_model_and_applicability_evidence_remain_attached_after_risk_export():
    initial, event, request, context = case()
    effect = request.effect.model_copy(
        update={
            "window": request.effect.window.model_copy(update={"evidence_id": "window"}),
            "time_model": request.effect.time_model.model_copy(update={"evidence_id": "model"}),
        }
    )
    request = request.model_copy(update={"effect": effect, "applicability_evidence_id": "review"})
    context = context.model_copy(
        update={
            "evidence": context.evidence
            + tuple(
                EvidenceRef(id=key, citation=f"Authored {key} evidence", origin="synthetic")
                for key in ["window", "model", "review"]
            )
        }
    )
    owner, _ = client()
    risk = owner.evaluate(initial, event, request, context).risk_probability()
    assert set(risk.evidence_ids) == {"e", "window", "model", "review"}


def test_native_cognitive_stages_keep_conditional_order_and_cannot_change_context():
    _, event, request, context = case()
    specification = CognitiveResponse(
        stages=(
            CognitiveStage(id="detect", conditional_failure_probability=0.1, evidence_id="e"),
            CognitiveStage(id="act", conditional_failure_probability=0.2, evidence_id="e"),
        ),
        applicability_evidence_id="e",
        applicability_rationale="Ordered conditional stages in a test",
    )
    owner, _ = client()
    initial = owner.cognitive_estimate(event, specification, context)
    assert initial.probability == 0.28 and initial.trace["stage_order"] == ["detect", "act"]
    assert owner.evaluate(initial, event, request, context).status == "exchange_checked"
    with pytest.raises(ValueError, match="different source context"):
        owner.evaluate(initial, event, request, context.model_copy(update={"scenario_id": "other"}))


def test_duplicate_cognitive_stage_id_does_not_overwrite_a_conditional_stage():
    stage = CognitiveStage(id="detect", conditional_failure_probability=0.1, evidence_id="e")
    with pytest.raises(ValueError, match="distinct identities"):
        CognitiveResponse(
            stages=(stage, stage), applicability_evidence_id="e", applicability_rationale="test"
        )


@pytest.mark.parametrize(
    "changes", [{"cue_time_s": 15.0}, {"limit_time_s": 2.0}, {"limit_time_s": float("inf")}]
)
def test_nonpositive_or_nonfinite_time_windows_are_not_repaired(changes):
    with pytest.raises(ValueError):
        ResponseWindow(
            **({"clock_id": "c", "cue_time_s": 5.0, "limit_time_s": 15.0, "evidence_id": "e"} | changes)
        )
