"""Independent analytic CTMC references; numerical execution belongs to Nurion."""

import copy
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.stochastic_process import (
    HazardWindow,
    StochasticProcessRequest,
    StochasticState,
    StochasticTransition,
    evaluate_stochastic_process,
    verify_stochastic_process_record,
)


def case(failure=0.2, recovery=0.3, duration=4.0):
    context = RunContext(
        asset_id="unit", scenario_id="analytic", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored two-state analytic fixture",
                              origin="synthetic", sha256="a" * 64),),
    )
    request = StochasticProcessRequest(
        id="two-state", asset_id="unit", clock_id="seconds", condition_id="fixed",
        conditioning="initially available; constant conditional rates",
        states=(StochasticState(id="up", initial_probability=1.0, definition_id="available", evidence_id="review"),
                StochasticState(id="down", initial_probability=0.0, definition_id="unavailable", evidence_id="review")),
        transitions=(StochasticTransition(id="fail", from_state_id="up", to_state_id="down", kind="failure",
                                          event_definition_id="failure", evidence_id="review"),
                     StochasticTransition(id="repair", from_state_id="down", to_state_id="up", kind="recovery",
                                          event_definition_id="repair", evidence_id="review")),
        windows=(HazardWindow(id="window", duration_s=duration,
                              transition_hazard_per_s=(failure, recovery), evidence_id="review"),),
        finite_states_memoryless_conditional_hazards_reviewed=True,
        competing_events_and_recovery_state_definitions_reviewed=True,
        rate_windows_and_common_conditioning_reviewed=True,
        evidence_ids=("review",),
    )
    return request, context


@pytest.mark.parametrize("failure,recovery,duration", [(0.2, 0.3, 4.0), (0.2, 0.0, 3.0), (0.0, 0.3, 2.0)])
def test_two_state_probability_occupation_and_expected_counts(failure, recovery, duration):
    request, context = case(failure, recovery, duration)
    record = evaluate_stochastic_process(request, context)
    rate = failure + recovery
    down = failure / rate * (1 - math.exp(-rate * duration))
    down_occupation = failure / rate * (duration - (1 - math.exp(-rate * duration)) / rate)
    up_occupation = duration - down_occupation
    assert record["status"] == "computed_diagnostic", record.get("failure")
    assert record["states"][-1]["state_probability"] == pytest.approx([1 - down, down], abs=1e-11)
    assert record["total_state_occupation_s"] == pytest.approx([up_occupation, down_occupation], abs=1e-10)
    assert record["total_expected_transition_counts"] == pytest.approx(
        [failure * up_occupation, recovery * down_occupation], abs=1e-10)
    assert verify_stochastic_process_record(record, request=request, context=context)["valid"]


def test_zero_generator_retains_probability_and_integrates_time():
    request, context = case(0.0, 0.0, 5.0)
    record = evaluate_stochastic_process(request, context)
    assert record["status"] == "computed_diagnostic", record.get("failure")
    assert record["states"][-1]["state_probability"] == pytest.approx([1.0, 0.0])
    assert record["total_state_occupation_s"] == pytest.approx([5.0, 0.0])
    assert record["total_expected_transition_counts"] == pytest.approx([0.0, 0.0])


def test_time_partition_preserves_constant_rate_solution():
    request, context = case()
    whole = evaluate_stochastic_process(request, context)
    window = request.windows[0]
    split = request.model_copy(update={"windows": (
        window.model_copy(update={"id": "first", "duration_s": 1.0}),
        window.model_copy(update={"id": "second", "duration_s": 3.0}),
    )})
    divided = evaluate_stochastic_process(split, context)
    assert divided["status"] == "computed_diagnostic", divided.get("failure")
    assert divided["states"][-1]["state_probability"] == pytest.approx(whole["states"][-1]["state_probability"], abs=1e-11)
    assert divided["total_state_occupation_s"] == pytest.approx(whole["total_state_occupation_s"], abs=1e-10)
    assert divided["total_expected_transition_counts"] == pytest.approx(whole["total_expected_transition_counts"], abs=1e-10)


def test_missing_review_or_changed_clock_is_rejected():
    request, context = case()
    with pytest.raises(ValueError, match="reviews"):
        evaluate_stochastic_process(request.model_copy(update={"rate_windows_and_common_conditioning_reviewed": False}), context)
    with pytest.raises(ValueError, match="asset/clock"):
        evaluate_stochastic_process(request, context.model_copy(update={"clock_id": "other"}))


def test_unpinned_evidence_and_tampered_receipt_are_rejected():
    request, context = case()
    unpinned = context.model_copy(update={"evidence": (context.evidence[0].model_copy(update={"sha256": None}),)})
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_stochastic_process(request, unpinned)
    record = copy.deepcopy(evaluate_stochastic_process(request, context))
    record["states"][-1]["state_probability"][0] += 0.01
    with pytest.raises(ValueError, match="receipt changed"):
        verify_stochastic_process_record(record)
