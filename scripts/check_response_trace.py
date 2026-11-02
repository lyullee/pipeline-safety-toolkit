"""Execute the three native owners, separating command probability from physical flow."""

import argparse
import json
import math
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.alarm_exchange import AlarmReplaySpec, replay_alarm
from pipeline_integrity.contracts import EvidenceRef, SignalFrame, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.response_exchange import (
    DeadlineEffect,
    HumanResponseClient,
    HumanResponseRequest,
    LognormalResponse,
    ResponseWindow,
)
from pipeline_integrity.response_trace import ResponseTraceSpec, SampledEndpointRule, trace_response
from pipeline_integrity.risk_exchange import EventBranch, EventTreeRequest, RiskFrequency
from pipeline_integrity.transient import simulate_liquid_transient
from pipeline_integrity.valve_motion import (
    liquid_valve_from_motion,
    simulate_valve_motion,
)


def human_model(context, spec, *, cue, median, deadline=None):
    from human_reliability.contracts import Estimate, HumanFailureEvent

    deadline = spec.command_deadline_s if deadline is None else deadline
    event = HumanFailureEvent(
        id="correct_command_not_issued",
        task_id="original_response_reference",
        step_ids=("issue_correct_command",),
        role="response",
        failure_definition="A correct command is not issued before the declared command deadline",
        opportunity_definition="One usable warning in the original conditional reference scenario",
        conditioning="A usable warning has reached the operator in the authored scenario",
        deadline_s=deadline - cue,
        includes_timing=True,
        includes_recovery=False,
    )
    initial = Estimate(
        event_id=event.id,
        method="authored_initial_command_error",
        probability=0.2,
        conditioning=event.conditioning,
        evidence_ids=("source",),
        status="assumed",
        trace={"basis": "Original assumed command error; no field population was fitted"},
    )
    request = HumanResponseRequest(
        condition_id=spec.condition_id,
        opportunity_id=spec.opportunity_id,
        protection_ids=(spec.protection_id,),
        applicability_evidence_id="mapping",
        applicability_rationale="Original command-issue endpoint and conditional completion model only",
        effect=DeadlineEffect(
            window=ResponseWindow(
                clock_id=context.clock_id, cue_time_s=cue, limit_time_s=deadline, evidence_id="mapping"
            ),
            time_model=LognormalResponse(median_s=median, log_sigma=0.5, evidence_id="source"),
            population_definition="Illustrative correct command issue time conditional on no initial error",
        ),
    )
    owner = HumanResponseClient()
    result = owner.evaluate(initial, event, request, context)
    expected = 0.2 + 0.8 * 0.5 * math.erfc(math.log((deadline - cue) / median) / (0.5 * math.sqrt(2)))
    assert result.status == "exchange_checked"
    assert math.isclose(result.probability.probability, expected, rel_tol=0, abs_tol=1e-14)
    try:
        owner.evaluate(
            result.native_estimate
            and owner.api.Estimate.model_validate_json(json.dumps(result.native_estimate)),
            event,
            request,
            context,
        )
    except ValueError as error:
        assert "already included" in str(error)
    else:
        raise AssertionError("the same timing effect was consumed a second time")
    return result, expected


def main():
    parser = argparse.ArgumentParser()
    for name in ("monitoring", "lh2dt", "hra"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    before = {name: revision(getattr(args, name)) for name in ("monitoring", "lh2dt", "hra")}
    sys.path[:0] = [str((getattr(args, name) / "src").resolve()) for name in before]
    sys.path.insert(0, str(Path("examples").resolve()))
    from response_trace import make_case
    from valve_transient import make_case as make_liquid_case

    private = Path("artifacts/response_trace")
    private.mkdir(parents=True, exist_ok=True)
    frames, policy, request, process, spec, context = make_case()
    alarm = replay_alarm(frames, policy, context)
    spec = spec.model_copy(update={"alarm_activation_id": alarm.activation_records[0]["id"]})
    motion = simulate_valve_motion(request, context.model_copy(update={"asset_id": request.component_id}))
    human, expected = human_model(context, spec, cue=4.0, median=1.0)
    reference = trace_response(alarm, motion, spec, context, process_frames=process, human_response=human)
    assert reference["status"] == "exchange_checked"
    assert reference["target_completion_bracket_s"] == [8.0, 9.0]
    assert reference["process_endpoint"]["first_qualifying_sample_time_s"] == 10.0
    assert reference["area_at_first_target_sample_m2"] > 0
    incomplete = trace_response(alarm, motion, spec, context, human_response=human)
    assert incomplete["status"] == "needs_data" and incomplete["process_endpoint"]["state"] == "unknown"
    unknown_cue = trace_response(
        alarm, motion, spec.model_copy(update={"alarm_to_cue_delay_s": None}), context, process_frames=process
    )
    assert unknown_cue["usable_cue_time_s"] is None and unknown_cue["status"] == "needs_data"
    wrong, _ = human_model(context, spec, cue=4.0, median=1.0, deadline=spec.process_limit_time_s)
    try:
        trace_response(alarm, motion, spec, context, process_frames=process, human_response=wrong)
    except ValueError as error:
        assert "command issue" in str(error)
    else:
        raise AssertionError("command and process deadlines were incorrectly interchanged")

    # Frequency arithmetic here describes command success/failure labels only.
    # It is not a loss-of-containment, physical isolation or release model.
    source = {"annual_frequency_per_year": 0.1, "basis": "Original assumed usable-warning demand rate"}
    rate = RiskFrequency(
        id="assumed_warning_demands",
        annual_frequency_per_year=0.1,
        condition_id=spec.condition_id,
        endpoint="protection_demand",
        evidence_ids=("source",),
        context=context,
        source_payload=source,
        source_digest=digest(source),
    )
    probability = human.risk_probability()
    eta = HumanResponseClient().risk_client.event_tree(
        rate,
        EventTreeRequest(
            events=(probability,),
            partition_evidence_id="mapping",
            branches=tuple(
                EventBranch(
                    id=id,
                    probability_id=probability.id,
                    complement=complement,
                    next_condition_id=id,
                    consequence_id=id,
                )
                for id, complement in (("command_failure", False), ("command_success", True))
            ),
        ),
    )
    assert eta["status"] == "exchange_checked"
    assert math.isclose(
        sum(row["annual_frequency_per_year"] for row in eta["frequencies"]), 0.1, rel_tol=0, abs_tol=1e-15
    )

    # Reuse the previously verified liquid reference and actual owner motion.
    # Add evidence in new local contexts and regenerate its motion binding.
    liquid_spec, liquid_context, first_motion = make_liquid_case()
    evidence = tuple(
        EvidenceRef(id=id, citation=text, origin="synthetic", license_id="MIT")
        for id, text in (
            ("source", "Original two-pipe liquid reference, not field observations"),
            ("policy", "Illustrative warning at 0.05 elapsed seconds"),
            ("mapping", "Original shared clock, command deadline 0.25s and process limit 1.0s"),
            ("endpoint", "Illustrative downstream absolute flow below 0.0001m3/s for 0.1s"),
            ("authored:issued_command", "Original finite-stroke reference command"),
        )
    )
    liquid_context = liquid_context.model_copy(update={"evidence": evidence})
    liquid_motion = simulate_valve_motion(
        first_motion.request, liquid_context.model_copy(update={"asset_id": "V-1"})
    )
    valve = liquid_valve_from_motion(
        liquid_motion,
        start_node="left",
        end_node="right",
        initial_volume_flow_m3_s=0.01,
        network_context=liquid_context,
    )
    liquid_spec = liquid_spec.model_copy(update={"valves": (valve,)})
    liquid = simulate_liquid_transient(liquid_spec, liquid_context)
    assert liquid["status"] == "computed" and liquid["all_volume_balances_within_tolerance"]
    assert all(row["root_qualified"] for row in liquid["valve_solver"])
    warning_frames = tuple(
        SignalFrame(
            stream_id="assumed_warning",
            clock_id=liquid_context.clock_id,
            time_s=time,
            values={"score": 0.0 if time < 0.05 else 2.0},
            units={"score": "1"},
            valid={"score": True},
            origin="synthetic",
        )
        for time in liquid["time_s"]
    )
    warning_spec = AlarmReplaySpec(
        alarm_id="assumed_warning",
        source_stream_id="assumed_warning",
        score_channel="score",
        score_unit="1",
        trigger=1.0,
        reset=0.0,
        max_gap_s=0.025000001,
        policy_reviewed=True,
        policy_evidence_id="policy",
        source_evidence_id="source",
    )
    warning = replay_alarm(warning_frames, warning_spec, liquid_context)
    flow_frames = tuple(
        SignalFrame(
            stream_id="downstream_flow",
            clock_id=liquid_context.clock_id,
            time_s=time,
            values={"q": q},
            units={"q": "m3/s"},
            valid={"q": True},
            origin="simulation",
        )
        for time, q in zip(liquid["time_s"], liquid["pipes"]["downstream"]["end_flow_m3_s"], strict=True)
    )
    coupled_spec = ResponseTraceSpec(
        id="coupled_liquid_reference",
        alarm_activation_id=warning.activation_records[0]["id"],
        alarm_asset_id=liquid_context.asset_id,
        valve_component_id="V-1",
        command_id="close_command",
        protection_id="manual_command",
        condition_id="assumed_usable_warning",
        opportunity_id="one_original_warning",
        alarm_to_cue_delay_s=0.05,
        command_deadline_s=0.25,
        process_limit_time_s=1.0,
        endpoint=SampledEndpointRule(
            stream_id="downstream_flow",
            channel="q",
            unit="m3/s",
            relation="abs_le",
            threshold=0.0001,
            required_hold_s=0.1,
            maximum_sample_interval_s=0.025000001,
            evidence_id="endpoint",
            interpretation_reviewed=True,
        ),
        maximum_motion_sample_interval_s=0.025000001,
        mapping_evidence_ids=("mapping",),
        asset_clock_and_origin_mapping_reviewed=True,
        cue_delivery_reviewed=True,
        command_endpoint_reviewed=True,
        process_limit_reviewed=True,
    )
    coupled_human, coupled_expected = human_model(liquid_context, coupled_spec, cue=0.1, median=0.1)
    coupled = trace_response(
        warning,
        liquid_motion,
        coupled_spec,
        liquid_context,
        process_frames=flow_frames,
        human_response=coupled_human,
    )
    assert coupled["status"] == "exchange_checked"
    assert coupled["command_issue_by_deadline"] == coupled["target_by_process_limit"] == "met"
    assert coupled["process_endpoint"]["state"] == "not_observed_by_limit"
    assert coupled["dynamic_risk_probability"] is None
    completion = liquid_motion.command_records[0]["completion_bracket_s"]
    index = liquid["time_s"].index(completion[1])
    downstream_at_target = liquid["pipes"]["downstream"]["end_flow_m3_s"][index]
    assert downstream_at_target > 0.005
    after = {name: revision(getattr(args, name)) for name in before}
    assert before == after, "native source changed during verification"
    records = {
        "authored_reference": reference,
        "missing_process": incomplete,
        "unknown_cue": unknown_cue,
        "command_eta": eta,
        "coupled_liquid": coupled,
        "coupled_liquid_result": liquid,
    }
    for name, record in records.items():
        (private / f"{name}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    summary = {
        "schema": "pipeline_integrity.response_trace_verification.v1",
        "passed": True,
        "native_owners": before,
        "native_sources_unchanged": True,
        "authored_reference": {
            "alarm_s": reference["recorded_alarm_time_s"],
            "cue_s": reference["usable_cue_time_s"],
            "command_s": reference["command_issue_time_s"],
            "target_bracket_s": reference["target_completion_bracket_s"],
            "sampled_endpoint_s": reference["process_endpoint"]["first_qualifying_sample_time_s"],
            "residual_target_area_m2": reference["area_at_first_target_sample_m2"],
        },
        "human_command_probability_references": [
            {"native": human.probability.probability, "independent": expected},
            {"native": coupled_human.probability.probability, "independent": coupled_expected},
        ],
        "command_eta_terminal_count": len(eta["frequencies"]),
        "command_eta_frequency_sum_per_year": sum(
            row["annual_frequency_per_year"] for row in eta["frequencies"]
        ),
        "command_deadline_replaced_by_process_limit_rejected": True,
        "repeated_timing_rejected": True,
        "missing_process_endpoint_withheld": True,
        "missing_cue_not_zero": True,
        "coupled_liquid": {
            "trace_status": coupled["status"],
            "command_by_deadline": coupled["command_issue_by_deadline"],
            "target_by_process_limit": coupled["target_by_process_limit"],
            "sampled_process_endpoint": coupled["process_endpoint"]["state"],
            "target_completion_bracket_s": completion,
            "downstream_flow_at_target_m3_s": downstream_at_target,
            "volume_balance_passed": True,
            "maximum_pipe_volume_residual_m3": liquid["maximum_pipe_volume_residual_m3"],
            "maximum_junction_residual_m3_s": liquid["maximum_junction_residual_m3_s"],
        },
        "alarm_or_single_trajectory_used_as_hep": False,
        "full_dynamic_risk_probability_calculated": False,
        "field_validation": False,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/response_trace.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/response_trace.py")),
        "alarm_exchange_sha256": file_digest(Path("src/pipeline_integrity/alarm_exchange.py")),
        "private_record_sha256": {name: file_digest(private / f"{name}.json") for name in records},
    }
    Path("research/response_trace_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"passed": True, "coupled_liquid": summary["coupled_liquid"], "native_sources_unchanged": True}
        )
    )


if __name__ == "__main__":
    main()
