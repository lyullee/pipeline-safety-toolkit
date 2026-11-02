"""Independent chronology/unknown/endpoint checks with scripted owner replies."""

import copy
import runpy
import json
from types import SimpleNamespace

import pytest

from pipeline_integrity.alarm_exchange import replay_alarm
from pipeline_integrity.contracts import digest
from pipeline_integrity.response_exchange import (
    DeadlineEffect,
    DeadlineScenario,
    DeadlineScenarioSet,
    HumanResponseClient,
    HumanResponseRequest,
    HumanResponseScenarioResult,
    LognormalResponse,
    ResponseWindow,
)
from pipeline_integrity.risk_exchange import RiskProbability
from pipeline_integrity.response_trace import _before, trace_response, verify_response_trace_record
from pipeline_integrity.valve_motion import simulate_valve_motion


def make_receipts(*, offset=0.0):
    frames, policy, request, endpoint, specification, context = runpy.run_path("examples/response_trace.py")[
        "make_case"
    ](offset_s=offset)

    def alarm_factory(**parameters):
        native = SimpleNamespace(**parameters, last=None, since=None, clear_since=None, active=False)
        replies = iter(
            (
                "NORMAL",
                "PENDING",
                "PENDING",
                "ALARM",
                "ALARM",
                "ALARM",
                "NORMAL",
                "NORMAL",
                "NORMAL",
                "NORMAL",
                "NORMAL",
            )
        )

        def update(time_s, score, *, valid, reset_requested):
            state = next(replies)
            native.last, native.active = time_s, state == "ALARM"
            return {"time_s": time_s, "state": state, "active": native.active}

        native.update = update
        return native

    alarm = replay_alarm(
        frames, policy, context, factory=alarm_factory, provider={"id": "scripted-alarm", "version": "test"}
    )
    # These are prescribed replies, not a second actuator algorithm.
    positions = [1.0] * int(offset + 4) + [1.0, 0.75, 0.5, 0.25, 0.0, 0.0, 0.0, 0.0]

    def motion_factory(_):
        actor = SimpleNamespace(opening=1.0, target_opening=1.0)
        replies = iter(positions)

        def advance(_dt):
            actor.opening = next(replies)
            return actor.opening

        actor.advance = advance
        actor.set_target_opening = lambda target: setattr(actor, "target_opening", target)
        return actor

    motion_context = context.model_copy(update={"asset_id": request.component_id})
    motion = simulate_valve_motion(
        request, motion_context, factory=motion_factory, provider={"id": "scripted-motion", "version": "test"}
    )
    specification = specification.model_copy(
        update={"alarm_activation_id": alarm.activation_records[0]["id"]}
    )
    return alarm, motion, endpoint, specification, context


def evaluate(*, offset=0.0, spec_updates=None, frames=None, motion_updates=None):
    alarm, motion, endpoint, spec, context = make_receipts(offset=offset)
    if spec_updates:
        spec = spec.model_copy(update=spec_updates)
    if motion_updates:
        motion = motion.model_copy(update=motion_updates)
    return trace_response(alarm, motion, spec, context, process_frames=endpoint if frames is None else frames)


def scenario_result(context, spec, *, available_times=(1.0, 2.0), replay_valid=True):
    provider = {"id": "scripted_hra", "version": "test"}
    evidence_id = spec.mapping_evidence_ids[0]
    request = HumanResponseRequest(
        condition_id=spec.condition_id,
        opportunity_id=spec.opportunity_id,
        protection_ids=(spec.protection_id,),
        applicability_evidence_id=evidence_id,
        applicability_rationale="Authored response-trace integration boundary",
        effect=DeadlineEffect(
            window=ResponseWindow(
                clock_id=context.clock_id,
                cue_time_s=4.0,
                limit_time_s=spec.command_deadline_s,
                evidence_id=evidence_id,
            ),
            time_model=LognormalResponse(median_s=1.0, log_sigma=0.2, evidence_id=evidence_id),
            population_definition="Authored conditional response population",
        ),
    )
    scenarios = DeadlineScenarioSet(
        distribution_evidence_id=evidence_id,
        available_time_population_definition="Caller-weighted response-time window scenarios",
        scenarios=tuple(
            DeadlineScenario(
                id=f"deadline_{index}",
                available_time_s=value,
                probability_mass=1.0 / len(available_times),
                evidence_id=evidence_id,
            )
            for index, value in enumerate(available_times)
        ),
    )
    source = {"probability": 0.2}
    probability = RiskProbability(
        id="manual_response",
        probability=0.2,
        measure="demand_failure_probability",
        condition_id=spec.condition_id,
        conditioning="given no initial failure",
        opportunity_id=spec.opportunity_id,
        opportunity_definition="One reviewed response opportunity",
        failure_definition="Incorrect or late response under the declared deadline scenarios",
        role="response",
        includes=("timing",),
        protection_ids=(spec.protection_id,),
        evidence_ids=(evidence_id,),
        qualification="research",
        context=context,
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )
    body = dict(
        schema_version="pipeline_integrity.human_response_deadline_scenarios.v1",
        status="exchange_checked",
        context=context,
        provider=provider,
        request=request,
        scenario_set=scenarios,
        initial_estimate={"event_id": "manual_response"},
        native_event={"id": "manual_response"},
        scenario_results=tuple({"scenario_id": row.id} for row in scenarios.scenarios),
        aggregate_native_estimate={"probability": 0.2},
        probability=probability,
        issues=(),
        qualification="research",
        field_calibrated=False,
        work_authorized=False,
        implementation_sha256="a" * 64,
    )
    result = HumanResponseScenarioResult(**body, result_digest=digest(body))
    client = HumanResponseClient(api=SimpleNamespace(), risk_client=SimpleNamespace(), provider=provider)
    client.verify_deadline_scenario_result = lambda supplied: {
        "valid": replay_valid and supplied.provider == provider
    }
    return result, client


def test_alarm_cue_command_target_and_process_endpoint_are_separate():
    result = evaluate()
    assert result["status"] == "exchange_checked"
    assert [result[k] for k in ("recorded_alarm_time_s", "usable_cue_time_s", "command_issue_time_s")] == [
        3.0,
        4.0,
        5.0,
    ]
    assert result["target_completion_bracket_s"] == [8.0, 9.0]
    assert result["process_endpoint"]["first_qualifying_sample_time_s"] == 10.0
    assert result["area_at_first_target_sample_m2"] == 1e-8
    assert result["command_issue_by_deadline"] == result["target_by_process_limit"] == "met"
    assert not result["zero_flow_inferred_from_target"]
    assert result["dynamic_risk_probability"] is None
    assert result["result_digest"] == digest({k: v for k, v in result.items() if k != "result_digest"})


def test_common_clock_translation_preserves_durations_and_outcomes():
    first, shifted = evaluate(), evaluate(offset=100.0)
    for key in ("recorded_alarm_time_s", "usable_cue_time_s", "command_issue_time_s"):
        assert shifted[key] - first[key] == 100.0
    assert shifted["target_completion_bracket_s"] == [108.0, 109.0]
    assert shifted["target_by_process_limit"] == first["target_by_process_limit"]
    assert shifted["process_endpoint"]["first_qualifying_sample_time_s"] == 110.0


@pytest.mark.parametrize(
    "updates",
    [
        {"alarm_to_cue_delay_s": None},
        {"cue_delivery_reviewed": False},
        {"asset_clock_and_origin_mapping_reviewed": False},
        {"command_endpoint_reviewed": False},
        {"process_limit_reviewed": False},
        {"endpoint": None},
    ],
)
def test_unknowns_and_unreviewed_meaning_withhold_trace(updates):
    result = evaluate(spec_updates=updates)
    assert result["status"] == "needs_data" and result["issues"]
    assert result["dynamic_risk_probability"] is None


def test_position_does_not_fill_absent_process_observations():
    result = evaluate(frames=())
    assert result["target_by_process_limit"] == "met"
    assert result["process_endpoint"]["state"] == "unknown"
    assert result["status"] == "needs_data"


def test_missing_late_flow_and_time_gaps_are_not_zero_or_a_completed_hold():
    *_, endpoint, spec, context = make_receipts()
    frames = list(endpoint)
    frames[9] = frames[9].model_copy(update={"values": {"q": None}, "valid": {"q": False}})
    result = evaluate(frames=tuple(frames))
    assert result["process_endpoint"]["state"] == "unknown"
    result = evaluate(frames=tuple(f for i, f in enumerate(endpoint) if i != 9))
    assert result["process_endpoint"]["state"] == "unknown"


def test_complete_nonqualifying_data_is_not_an_unknown_or_a_human_failure_rate():
    _, _, endpoint, _, _ = make_receipts()
    frames = tuple(f.model_copy(update={"values": {"q": 1.0}}) for f in endpoint)
    result = evaluate(frames=frames)
    assert result["process_endpoint"]["state"] == "not_observed_by_limit"
    assert result["status"] == "exchange_checked"
    assert result["dynamic_risk_probability"] is None


@pytest.mark.parametrize(
    "change,match",
    [
        ({"alarm_to_cue_delay_s": 3.0}, "precede its usable cue"),
        ({"command_deadline_s": 11.0}, "cannot follow"),
        ({"command_id": "missing"}, "does not contain"),
        ({"mapping_evidence_ids": ("missing",)}, "evidence"),
    ],
)
def test_inconsistent_causality_and_mapping_are_rejected(change, match):
    with pytest.raises(ValueError, match=match):
        evaluate(spec_updates=change)


def test_completion_bracket_cannot_become_a_precise_deadline_claim():
    assert _before([8.0, 9.0], 8.5) == "uncertain"
    assert _before([8.0, 9.0], 8.0) == "uncertain"
    assert _before([8.0, 9.0], 7.9) == "missed"
    assert _before([8.0, 9.0], 9.0) == "met"


def test_digests_and_original_unit_clock_are_checked_before_use():
    with pytest.raises(ValueError, match="changed"):
        evaluate(motion_updates={"opening": (0.0,) * 13})
    alarm, motion, endpoint, spec, context = make_receipts()
    alarm.records[0]["state"] = "ALARM"
    with pytest.raises(ValueError, match="changed"):
        trace_response(alarm, motion, spec, context, process_frames=endpoint)
    _, _, endpoint, _, _ = make_receipts()
    frames = tuple(f.model_copy(update={"units": {"q": "L/s"}}) for f in endpoint)
    with pytest.raises(ValueError, match="explicit unit"):
        evaluate(frames=frames)


def test_budget_is_enforced_before_endpoint_clone():
    with pytest.raises(ValueError, match="sample budget"):
        evaluate(spec_updates={"maximum_endpoint_samples": 2})


def test_arbitrary_receipt_objects_cannot_replace_integrity_checks():
    alarm, motion, endpoint, spec, context = make_receipts()
    fake = SimpleNamespace(check_integrity=lambda: None, model_dump_json=motion.model_dump_json)
    with pytest.raises(TypeError, match="ValveMotionResult"):
        trace_response(alarm, fake, spec, context, process_frames=endpoint)
    with pytest.raises(TypeError, match="HumanResponseResult"):
        trace_response(alarm, motion, spec, context, process_frames=endpoint, human_response=fake)


def test_deadline_scenario_hra_is_replayed_and_kept_separate_from_observed_command():
    alarm, motion, endpoint, spec, context = make_receipts()
    scenario, client = scenario_result(context, spec)
    result = trace_response(
        alarm,
        motion,
        spec,
        context,
        process_frames=endpoint,
        human_response_scenarios=scenario,
        human_response_client=client,
    )
    assert result["status"] == "exchange_checked"
    assert result["command_issue_time_s"] == 5.0
    linked = result["human_response_scenario_model"]
    assert linked["scenario_deadlines_s"] == [5.0, 6.0]
    assert linked["observed_command_issue_time_s"] == 5.0
    assert [row["observed_command_issue_by_deadline"] for row in linked["observed_command_by_scenario"]] == [
        "met", "met"
    ]
    assert linked["caller_weighted_observed_command_scenario_mass"] == {
        "met": 1.0, "missed": 0.0, "uncertain": 0.0, "unknown": 0.0
    }
    assert linked["scenario_mass_is_not_an_empirical_or_dynamic_probability"]
    assert linked["scenario_probability_is_not_updated_from_observed_trajectory"]
    assert result["human_response_model"] is None
    assert result["human_probability_updated_from_this_trajectory"] is False
    assert result["dynamic_risk_probability"] is None
    assert result["result_digest"] == digest(
        {key: value for key, value in result.items() if key != "result_digest"}
    )


def test_deadline_scenario_trace_reports_caller_weighted_observed_command_mass():
    alarm, motion, endpoint, spec, context = make_receipts()
    scenario, client = scenario_result(context, spec, available_times=(0.5, 1.0))
    result = trace_response(
        alarm, motion, spec, context, process_frames=endpoint,
        human_response_scenarios=scenario, human_response_client=client,
    )
    linked = result["human_response_scenario_model"]
    assert linked["scenario_deadlines_s"] == [4.5, 5.0]
    assert [row["observed_command_issue_by_deadline"] for row in linked["observed_command_by_scenario"]] == [
        "missed", "met"
    ]
    assert linked["caller_weighted_observed_command_scenario_mass"] == {
        "met": 0.5, "missed": 0.5, "uncertain": 0.0, "unknown": 0.0
    }
    assert linked["weighted_failure_probability"] == 0.2
    assert result["human_probability_updated_from_this_trajectory"] is False
    assert result["dynamic_risk_probability"] is None


def test_deadline_scenario_trace_rejects_mismatched_or_late_scenario_deadlines():
    alarm, motion, endpoint, spec, context = make_receipts()
    late, client = scenario_result(context, spec, available_times=(2.1,))
    with pytest.raises(ValueError, match="exceeds the declared command deadline"):
        trace_response(
            alarm, motion, spec, context, process_frames=endpoint,
            human_response_scenarios=late, human_response_client=client,
        )
    scenario, client = scenario_result(context, spec)
    mismatched = scenario.model_copy(update={"context": context.model_copy(update={"clock_id": "other"})})
    body = mismatched.model_dump(mode="json", exclude={"result_digest"})
    mismatched = HumanResponseScenarioResult.model_validate_json(json.dumps(body | {"result_digest": digest(body)}))
    with pytest.raises(ValueError, match="context, condition"):
        trace_response(
            alarm, motion, spec, context, process_frames=endpoint,
            human_response_scenarios=mismatched, human_response_client=client,
        )


def test_deadline_scenario_trace_requires_a_successful_native_owner_replay():
    alarm, motion, endpoint, spec, context = make_receipts()
    scenario, client = scenario_result(context, spec, replay_valid=False)
    with pytest.raises(ValueError, match="owner replay failed"):
        trace_response(
            alarm, motion, spec, context, process_frames=endpoint,
            human_response_scenarios=scenario, human_response_client=client,
        )
    scenario, _ = scenario_result(context, spec)
    with pytest.raises(TypeError, match="source-bound HumanResponseClient"):
        trace_response(
            alarm, motion, spec, context, process_frames=endpoint,
            human_response_scenarios=scenario,
        )


def test_response_trace_adapter_replay_rejects_resealed_output_and_changed_frames():
    alarm, motion, frames, spec, context = make_receipts()
    record = trace_response(alarm, motion, spec, context, process_frames=frames)
    assert verify_response_trace_record(
        record, alarm=alarm, motion=motion, specification=spec, context=context, process_frames=frames
    )["valid"]

    changed = copy.deepcopy(record)
    changed["command_issue_by_deadline"] = "missed"
    changed["result_digest"] = digest({key: value for key, value in changed.items() if key != "result_digest"})
    assert not verify_response_trace_record(
        changed, alarm=alarm, motion=motion, specification=spec, context=context, process_frames=frames
    )["valid"]
    assert not verify_response_trace_record(
        record, alarm=alarm, motion=motion, specification=spec, context=context,
        process_frames=frames[:-1],
    )["valid"]


def test_observed_risk_timeline_binds_trace_order_without_probability_inference():
    from pipeline_integrity.risk_timeline import RiskTimelineRequest, build_observed_risk_timeline

    first = evaluate()
    second = evaluate(offset=1.0, spec_updates={"opportunity_id": "next-opportunity"})
    context = first["context"]
    request = RiskTimelineRequest(
        id="reviewed_timeline", asset_id=context["asset_id"], scenario_id=context["scenario_id"],
        clock_id=context["clock_id"], start_s=0.0, finish_s=20.0, source_origin=context["origin"],
        event_record_digests=(first["result_digest"], second["result_digest"]),
        mapping_evidence_ids=("mapping",), timeline_mapping_reviewed=True,
    )
    result = build_observed_risk_timeline(request, (first, second))
    assert [row["times_s"]["alarm_recorded"] for row in result["events"]] == [3.0, 4.0]
    assert result["dynamic_risk_probability"] is None
    assert not result["probability_inferred_from_trajectory"]
    assert result["result_digest"] == digest(
        {key: value for key, value in result.items() if key != "result_digest"}
    )


def test_observed_risk_timeline_rejects_unreviewed_mapping_and_changed_trace():
    from pipeline_integrity.risk_timeline import RiskTimelineRequest, build_observed_risk_timeline

    trace = evaluate()
    context = trace["context"]
    fields = dict(
        id="reviewed_timeline", asset_id=context["asset_id"], scenario_id=context["scenario_id"],
        clock_id=context["clock_id"], start_s=0.0, finish_s=20.0, source_origin=context["origin"],
        event_record_digests=(trace["result_digest"],), mapping_evidence_ids=("mapping",),
    )
    with pytest.raises(ValueError, match="requires review"):
        build_observed_risk_timeline(RiskTimelineRequest(**fields), (trace,))
    reviewed = RiskTimelineRequest(**(fields | {"timeline_mapping_reviewed": True}))
    changed = dict(trace, command_issue_time_s=trace["command_issue_time_s"] + 0.1)
    with pytest.raises(ValueError, match="digest"):
        build_observed_risk_timeline(reviewed, (changed,))


def test_observed_risk_timeline_rejects_cross_clock_and_duplicate_opportunity():
    from pipeline_integrity.risk_timeline import RiskTimelineRequest, build_observed_risk_timeline

    first, second = evaluate(), evaluate(offset=1.0)
    context = first["context"]
    request = RiskTimelineRequest(
        id="reviewed_timeline", asset_id=context["asset_id"], scenario_id=context["scenario_id"],
        clock_id=context["clock_id"], start_s=0.0, finish_s=20.0, source_origin=context["origin"],
        event_record_digests=(first["result_digest"], second["result_digest"]),
        mapping_evidence_ids=("mapping",), timeline_mapping_reviewed=True,
    )
    changed = dict(second, context=dict(second["context"], clock_id="other_clock"))
    changed["result_digest"] = digest(
        {key: value for key, value in changed.items() if key != "result_digest"}
    )
    mismatch = request.model_copy(
        update={"event_record_digests": (first["result_digest"], changed["result_digest"])}
    )
    with pytest.raises(ValueError, match="asset, scenario, clock or origin"):
        build_observed_risk_timeline(mismatch, (first, changed))
    with pytest.raises(ValueError, match="same opportunity/command"):
        build_observed_risk_timeline(request, (first, second))


def test_observed_risk_timeline_preserves_nonqualifying_endpoint_through_horizon():
    from pipeline_integrity.risk_timeline import RiskTimelineRequest, build_observed_risk_timeline

    trace = evaluate(frames=tuple(f.model_copy(update={"values": {"q": 1.0}}) for f in make_receipts()[2]))
    context = trace["context"]
    request = RiskTimelineRequest(
        id="reviewed_timeline", asset_id=context["asset_id"], scenario_id=context["scenario_id"],
        clock_id=context["clock_id"], start_s=0.0, finish_s=20.0, source_origin=context["origin"],
        event_record_digests=(trace["result_digest"],), mapping_evidence_ids=("mapping",),
        timeline_mapping_reviewed=True,
    )
    result = build_observed_risk_timeline(request, (trace,))
    assert result["events"][0]["process_endpoint_state"] == "not_observed_by_limit"
    assert result["events"][0]["first_qualifying_sample_time_s"] is None
    assert result["events"][0]["times_s"]["process_endpoint_observed"] == 10.0
