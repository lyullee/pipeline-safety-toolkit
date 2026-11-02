"""Independent cognitive-chain, response-deadline and recovery oracles through HRA APIs."""

from __future__ import annotations

import math

import pytest
from human_reliability import HumanFailureEvent

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.response_exchange import (
    CognitiveResponse,
    CognitiveStage,
    DeadlineEffect,
    HumanResponseClient,
    HumanResponseRequest,
    LognormalResponse,
    RecoveryEffect,
    ResponseWindow,
)


STAGES = (("detect", 0.10), ("diagnose", 0.20), ("act", 0.15))
EVIDENCE_IDS = (
    "review",
    "detect_basis",
    "diagnose_basis",
    "act_basis",
    "deadline_window",
    "response_time_model",
    "recovery_effect",
)


def context():
    return RunContext(
        asset_id="synthetic_process",
        scenario_id="human_response_oracle",
        clock_id="elapsed_seconds",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(
                id=key,
                citation="Authored synthetic human-response reference case",
                origin="synthetic",
            )
            for key in EVIDENCE_IDS
        ),
    )


def specification():
    return CognitiveResponse(
        stages=tuple(
            CognitiveStage(id=key, conditional_failure_probability=p, evidence_id=f"{key}_basis")
            for key, p in STAGES
        ),
        applicability_evidence_id="review",
        applicability_rationale="Synthetic stage inputs exercise the API and do not represent a plant task.",
    )


def event(*, timing, recovery, deadline):
    return HumanFailureEvent(
        id="manual_response_failure",
        task_id="synthetic_manual_isolation",
        step_ids=tuple(key for key, _ in STAGES),
        role="response",
        failure_definition="The authored manual response objective is not completed as specified",
        opportunity_definition="One synthetic manual response demand",
        conditioning="The synthetic initiating condition is present",
        deadline_s=deadline,
        includes_recovery=recovery,
        includes_timing=timing,
        dependency_group="manual_response_context",
    )


def initial_probability():
    return 1.0 - math.prod(1.0 - p for _, p in STAGES)


def test_deadline_adjusted_response_matches_lognormal_cdf_and_replays():
    ctx = context()
    client = HumanResponseClient()
    initial_event = event(timing=True, recovery=False, deadline=12.0)
    initial = client.cognitive_estimate(initial_event, specification(), ctx)
    window = ResponseWindow(
        clock_id=ctx.clock_id,
        cue_time_s=2.0,
        limit_time_s=14.0,
        evidence_id="deadline_window",
    )
    time_model = LognormalResponse(median_s=8.0, log_sigma=0.4, evidence_id="response_time_model")
    request = HumanResponseRequest(
        condition_id="one_manual_demand",
        opportunity_id="one_synthetic_opportunity",
        protection_ids=("manual_isolation", "response_deadline"),
        applicability_evidence_id="review",
        applicability_rationale="Use analytic timing inputs only to verify the response exchange.",
        effect=DeadlineEffect(
            window=window,
            time_model=time_model,
            population_definition="synthetic response-time population",
        ),
    )
    result = client.evaluate(initial, initial_event, request, ctx)

    completion_probability = 0.5 * (
        1.0 + math.erf(math.log(window.available_s / time_model.median_s) / (time_model.log_sigma * math.sqrt(2.0)))
    )
    expected = 1.0 - (1.0 - initial_probability()) * completion_probability
    assert initial.probability == pytest.approx(initial_probability(), abs=1e-15)
    assert result.status == "exchange_checked"
    assert result.native_estimate["probability"] == pytest.approx(expected, abs=2e-14)
    assert result.probability.probability == pytest.approx(expected, abs=2e-14)
    assert result.probability.includes == ("timing",)
    assert result.native_completion_bins == ()
    assert client.verify_result(result)["valid"]


def test_conditional_recovery_matches_probability_product_and_replays():
    ctx = context()
    client = HumanResponseClient()
    initial_event = event(timing=False, recovery=True, deadline=None)
    initial = client.cognitive_estimate(initial_event, specification(), ctx)
    request = HumanResponseRequest(
        condition_id="one_manual_demand",
        opportunity_id="one_synthetic_opportunity",
        protection_ids=("manual_isolation", "manual_recovery"),
        applicability_evidence_id="review",
        applicability_rationale="Use a declared conditional recovery factor only as a synthetic oracle.",
        effect=RecoveryEffect(
            failure_to_recover_given_error=0.25,
            evidence_id="recovery_effect",
            population_definition="synthetic recovery population",
        ),
    )
    result = client.evaluate(initial, initial_event, request, ctx)
    expected = initial_probability() * 0.25
    assert result.status == "exchange_checked"
    assert result.native_estimate["probability"] == pytest.approx(expected, abs=1e-15)
    assert result.probability.probability == pytest.approx(expected, abs=1e-15)
    assert result.probability.includes == ("recovery",)
    assert client.verify_result(result)["valid"]
