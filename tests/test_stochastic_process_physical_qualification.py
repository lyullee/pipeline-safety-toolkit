"""Bounded physical qualification for finite-state constant/piecewise CTMCs.

The independent oracle is the closed-form competing-exponential solution;
this suite does not infer hazards from process data or claim field rates.
"""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.stochastic_process import (
    HazardWindow,
    ProcessReward,
    StochasticProcessRequest,
    StochasticState,
    StochasticTransition,
    evaluate_stochastic_process,
)


def competing_case(rates=(0.12, 0.08), duration=7.0, split=False):
    context = RunContext(
        asset_id="synthetic-unit", scenario_id="competing-hazards",
        clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(
            id="analytic-fixture", citation="Authored closed-form CTMC fixture",
            origin="synthetic", sha256="b" * 64,
        ),),
    )
    states = (
        StochasticState(id="operating", initial_probability=1.0, definition_id="operating", evidence_id="analytic-fixture"),
        StochasticState(id="failure_a", initial_probability=0.0, absorbing=True, definition_id="failure-a", evidence_id="analytic-fixture"),
        StochasticState(id="failure_b", initial_probability=0.0, absorbing=True, definition_id="failure-b", evidence_id="analytic-fixture"),
    )
    transitions = (
        StochasticTransition(id="event_a", from_state_id="operating", to_state_id="failure_a", kind="failure", event_definition_id="event-a", evidence_id="analytic-fixture"),
        StochasticTransition(id="event_b", from_state_id="operating", to_state_id="failure_b", kind="failure", event_definition_id="event-b", evidence_id="analytic-fixture"),
    )
    durations = (duration / 3, duration * 2 / 3) if split else (duration,)
    windows = tuple(HazardWindow(
        id=f"window-{i}", duration_s=dt,
        transition_hazard_per_s=tuple(rates), evidence_id="analytic-fixture",
    ) for i, dt in enumerate(durations))
    request = StochasticProcessRequest(
        id="closed-form-competing-risks", asset_id="synthetic-unit", clock_id="seconds",
        condition_id="constant-rate", conditioning="fixed conditional hazard rates",
        states=states, transitions=transitions, windows=windows,
        rewards=(ProcessReward(
            id="operating-time", quantity_unit="s",
            state_quantity_rate_per_s=(1.0, 0.0, 0.0),
            transition_quantity_per_event=(0.0, 0.0),
            definition_id="operating-time", evidence_id="analytic-fixture",
        ),),
        finite_states_memoryless_conditional_hazards_reviewed=True,
        competing_events_and_recovery_state_definitions_reviewed=True,
        rate_windows_and_common_conditioning_reviewed=True,
        evidence_ids=("analytic-fixture",),
    )
    return request, context


@pytest.mark.parametrize("rates,duration", [
    ((0.12, 0.08), 7.0),
    ((0.0, 0.35), 2.5),
    ((0.006, 0.014), 120.0),
    ((0.45, 0.55), 0.03),
])
def test_competing_absorbing_events_match_closed_form_probability_counts_and_occupation(rates, duration):
    request, context = competing_case(rates, duration)
    result = evaluate_stochastic_process(request, context)
    total_rate = math.fsum(rates)
    survival = math.exp(-total_rate * duration)
    event_fraction = -math.expm1(-total_rate * duration)
    op_occupation = event_fraction / total_rate if total_rate else duration
    expected_probs = [survival, rates[0] / total_rate * event_fraction, rates[1] / total_rate * event_fraction]
    expected_counts = [rates[0] * op_occupation, rates[1] * op_occupation]
    assert result["status"] == "computed_diagnostic", result.get("failure")
    assert result["states"][-1]["state_probability"] == pytest.approx(expected_probs, abs=2e-11)
    assert result["total_expected_transition_counts"] == pytest.approx(expected_counts, abs=2e-10)
    assert result["total_state_occupation_s"] == pytest.approx(
        [op_occupation, rates[0] / total_rate * (duration - op_occupation),
         rates[1] / total_rate * (duration - op_occupation)], abs=2e-9)
    assert result["total_expected_reward_quantity"] == pytest.approx([op_occupation], abs=2e-9)
    assert math.fsum(result["states"][-1]["state_probability"]) == pytest.approx(1.0, abs=2e-11)
    assert result["absorbing_endpoint_probability"] == pytest.approx(
        {"failure_a": expected_probs[1], "failure_b": expected_probs[2]}, abs=2e-11)


def test_piecewise_partition_preserves_solution_and_transition_fluxes():
    whole, context = competing_case((0.12, 0.08), 7.0)
    divided, _ = competing_case((0.12, 0.08), 7.0, split=True)
    one = evaluate_stochastic_process(whole, context)
    two = evaluate_stochastic_process(divided, context)
    assert two["status"] == "computed_diagnostic", two.get("failure")
    assert two["states"][-1]["state_probability"] == pytest.approx(one["states"][-1]["state_probability"], abs=3e-11)
    assert two["total_state_occupation_s"] == pytest.approx(one["total_state_occupation_s"], abs=3e-10)
    assert two["total_expected_transition_counts"] == pytest.approx(one["total_expected_transition_counts"], abs=3e-10)
    assert two["total_expected_reward_quantity"] == pytest.approx(one["total_expected_reward_quantity"], abs=3e-10)


def test_analytic_two_state_failure_recovery_equilibrium_and_counts():
    from test_stochastic_process_reference import case

    failure, recovery, duration = 0.2, 0.3, 4.0
    request, context = case(failure, recovery, duration)
    result = evaluate_stochastic_process(request, context)
    total = failure + recovery
    down_end = failure / total * (1 - math.exp(-total * duration))
    down_time = failure / total * (duration - (1 - math.exp(-total * duration)) / total)
    up_time = duration - down_time
    assert result["states"][-1]["state_probability"] == pytest.approx([1-down_end, down_end], abs=2e-11)
    assert result["total_state_occupation_s"] == pytest.approx([up_time, down_time], abs=2e-10)
    assert result["total_expected_transition_counts"] == pytest.approx([failure*up_time, recovery*down_time], abs=2e-10)
