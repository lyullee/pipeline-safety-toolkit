"""Join recorded alarms, reviewed human deadlines and native actuator receipts.

This trace retains observed/simulated chronology. It does not turn an alarm or
one successful trajectory into a human failure probability or a safe process.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import _aligned_frames
from .alarm_exchange import AlarmReplayResult
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .response_exchange import (
    DeadlineEffect,
    HumanResponseClient,
    HumanResponseResult,
    HumanResponseScenarioResult,
)
from .valve_motion import ValveMotionResult


class SampledEndpointRule(Contract):
    stream_id: Name
    channel: Name
    unit: Name
    relation: Literal["le", "ge", "abs_le"]
    threshold: float
    required_hold_s: Nonnegative
    maximum_sample_interval_s: Positive
    evidence_id: Name
    interpretation_reviewed: bool = False

    @model_validator(mode="after")
    def absolute_bound(self):
        if self.relation == "abs_le" and self.threshold < 0:
            raise ValueError("an absolute-value limit must be nonnegative")
        return self


class ResponseTraceSpec(Contract):
    id: Name
    alarm_activation_id: Name
    alarm_asset_id: Name
    valve_component_id: Name
    command_id: Name
    protection_id: Name
    condition_id: Name
    opportunity_id: Name
    alarm_to_cue_delay_s: Nonnegative | None
    command_deadline_s: Nonnegative | None
    process_limit_time_s: Nonnegative | None
    endpoint: SampledEndpointRule | None = None
    maximum_motion_sample_interval_s: Positive
    maximum_endpoint_samples: int = Field(default=100000, ge=1, le=1000000)
    maximum_human_deadline_scenarios: int = Field(default=256, ge=1, le=10000)
    mapping_evidence_ids: tuple[Name, ...] = Field(min_length=1)
    asset_clock_and_origin_mapping_reviewed: bool = False
    cue_delivery_reviewed: bool = False
    command_endpoint_reviewed: bool = False
    process_limit_reviewed: bool = False

    @model_validator(mode="after")
    def ordered_limits(self):
        if len(set(self.mapping_evidence_ids)) != len(self.mapping_evidence_ids):
            raise ValueError("mapping evidence must have distinct identities")
        if (
            self.command_deadline_s is not None
            and self.process_limit_time_s is not None
            and self.command_deadline_s > self.process_limit_time_s
        ):
            raise ValueError("the command deadline cannot follow the process limit")
        return self


def _clone(value, cls):
    return cls.model_validate_json(value.model_dump_json())


def _before(bracket, limit):
    if bracket is None or limit is None:
        return "unknown"
    start, finish = bracket
    if finish <= limit:
        return "met"
    if start > limit:
        return "missed"
    return "uncertain"


def _sampled_endpoint(frames, rule, *, start, limit, context, maximum_samples):
    result = {
        "state": "unknown",
        "first_qualifying_sample_time_s": None,
        "discrete_sample_rule_only": True,
        "continuous_between_sample_safety_proven": False,
        "source_frames_digest": None,
        "source_origin": None,
        "issues": [],
    }
    if rule is None or not rule.interpretation_reviewed or start is None or limit is None:
        result["issues"].append("reviewed sampled endpoint, cue and process limit required")
        return result
    if not frames:
        result["issues"].append("process observations are absent; target position does not prove safety")
        return result
    if len(frames) > maximum_samples:
        raise ValueError("endpoint sample budget exceeded")
    frames, channels = _aligned_frames(tuple(_clone(f, SignalFrame) for f in frames))
    if (
        frames[0].stream_id != rule.stream_id
        or frames[0].clock_id != context.clock_id
        or rule.channel not in channels
        or frames[0].units[rule.channel] != rule.unit
    ):
        raise ValueError("sampled endpoint stream, clock, channel or explicit unit differs")
    result["source_frames_digest"] = digest(frames)
    result["source_origin"] = frames[0].origin
    if frames[0].time_s > start or frames[-1].time_s < limit:
        result["issues"].append("process observations do not cover cue through process limit")
        return result
    relevant = [f for f in frames if start <= f.time_s <= limit]
    # Boundary samples are required. No unseen endpoint or threshold crossing is filled.
    if not relevant or relevant[0].time_s != start or relevant[-1].time_s != limit:
        result["issues"].append("explicit samples at cue and process limit are required")
        return result
    since, previous, incomplete = None, None, False
    first = None
    for frame in relevant:
        gap = previous is not None and frame.time_s - previous > rule.maximum_sample_interval_s
        valid = frame.valid[rule.channel] and frame.values[rule.channel] is not None
        if gap or not valid:
            since = None
            incomplete = True
        if valid:
            value = frame.values[rule.channel]
            satisfies = (
                value <= rule.threshold
                if rule.relation == "le"
                else value >= rule.threshold
                if rule.relation == "ge"
                else abs(value) <= rule.threshold
            )
            if satisfies:
                if since is None:
                    since = frame.time_s
                if frame.time_s - since >= rule.required_hold_s and first is None:
                    first = frame.time_s
            else:
                since = None
        previous = frame.time_s
    result["first_qualifying_sample_time_s"] = first
    # A later unknown does not erase an earlier explicitly witnessed hold. This
    # endpoint means achieved once, not maintained for the rest of the horizon.
    result["state"] = (
        "observed_before_limit" if first is not None else "unknown" if incomplete else "not_observed_by_limit"
    )
    if incomplete:
        result["issues"].append("missing or separated samples cannot support a continuous hold across them")
    return result


def trace_response(
    alarm: AlarmReplayResult,
    motion: ValveMotionResult | None,
    specification: ResponseTraceSpec,
    context: RunContext,
    *,
    process_frames: tuple[SignalFrame, ...] = (),
    human_response: HumanResponseResult | None = None,
    human_response_scenarios: HumanResponseScenarioResult | None = None,
    human_response_client: HumanResponseClient | None = None,
):
    """Bind owner results while keeping the recorded command and HRA scenarios separate.

    Deadline scenarios describe caller-weighted response opportunities. They are
    replayed by the native HRA client and never inferred from the one observed
    command or process trajectory.
    """
    if not isinstance(alarm, AlarmReplayResult):
        raise TypeError("a source-bound AlarmReplayResult is required")
    alarm.check_integrity()
    alarm = alarm.model_copy(deep=True)
    alarm.check_integrity()
    specification, context = _clone(specification, ResponseTraceSpec), _clone(context, RunContext)
    if alarm.context != context or context.asset_id != specification.alarm_asset_id:
        raise ValueError("the alarm and trace context must match the explicitly mapped source asset")
    evidence = {e.id for e in context.evidence}
    needed = set(specification.mapping_evidence_ids)
    if specification.endpoint is not None:
        needed.add(specification.endpoint.evidence_id)
    if not needed <= evidence:
        raise ValueError("trace mapping and sampled endpoint evidence must exist in context")
    activations = {row["id"]: row for row in alarm.activation_records}
    if specification.alarm_activation_id not in activations:
        raise ValueError("the chosen activation does not exist in the original alarm receipt")
    activation = activations[specification.alarm_activation_id]
    alarm_time = float(activation["time_s"])
    issues = []
    if alarm.status != "exchange_checked":
        issues.append("alarm policy is unresolved")
    if not specification.asset_clock_and_origin_mapping_reviewed:
        issues.append("asset, common clock and distinct source origins require review")
    cue = None
    if specification.alarm_to_cue_delay_s is None or not specification.cue_delivery_reviewed:
        issues.append("reviewed alarm-to-usable-cue delay required")
    else:
        cue = alarm_time + specification.alarm_to_cue_delay_s
    deadline = specification.command_deadline_s if specification.command_endpoint_reviewed else None
    limit = specification.process_limit_time_s if specification.process_limit_reviewed else None
    if deadline is None or limit is None:
        issues.append("reviewed command endpoint deadline and distinct process limit required")
    if cue is not None and ((deadline is not None and deadline < cue) or (limit is not None and limit < cue)):
        raise ValueError("the declared limit precedes the usable cue")
    command_time, completion, residual_area, motion_payload = None, None, None, None
    if motion is None:
        issues.append("native actuator history is absent")
    else:
        if not isinstance(motion, ValveMotionResult):
            raise TypeError("a native ValveMotionResult is required")
        motion.check_integrity()
        motion = _clone(motion, ValveMotionResult)
        motion.check_integrity()
        if (
            motion.context.asset_id != specification.valve_component_id
            or motion.request.component_id != specification.valve_component_id
            or motion.context.clock_id != context.clock_id
            or motion.context.scenario_id != context.scenario_id
        ):
            raise ValueError("the actuator needs its mapped component, scenario and common clock")
        commands = {c.id: c for c in motion.request.commands}
        if specification.command_id not in commands:
            raise ValueError("the original native request does not contain the selected command")
        command = commands[specification.command_id]
        if command.evidence_id not in {e.id for e in motion.context.evidence}:
            raise ValueError("the actuator command evidence is absent from its original context")
        records = [r for r in motion.command_records if r["command_id"] == command.id]
        if len(records) != 1:
            raise ValueError("exactly one matching native command record is required")
        record = records[0]
        if record["issued_time_s"] != command.time_s or record["target_opening"] != command.target_opening:
            raise ValueError("the native command receipt differs from the original request")
        command_time = command.time_s
        if cue is not None and command_time < cue:
            raise ValueError("a response command cannot precede its usable cue")
        completion = record["completion_bracket_s"]
        if completion is not None:
            lo, hi = completion
            if lo < command_time or lo > hi or hi not in motion.time_s:
                raise ValueError("the native completion bracket is inconsistent with the command/history")
            index = motion.time_s.index(hi)
            if abs(motion.opening[index] - command.target_opening) > motion.request.position_tolerance:
                raise ValueError("the reported completion sample does not reach the native target")
            residual_area = motion.effective_area_m2[index]
        else:
            issues.append("target attainment was not witnessed in the native sampled horizon")
        if any(
            b - a > specification.maximum_motion_sample_interval_s
            for a, b in zip(motion.time_s, motion.time_s[1:], strict=False)
            if a <= (limit if limit is not None else motion.time_s[-1]) and b >= command_time
        ):
            issues.append("actuator history exceeds the declared sample-interval bound")
            completion = None
        if limit is not None and motion.time_s[-1] < limit:
            issues.append("actuator horizon does not cover the declared process limit")
        motion_payload = motion.model_dump(mode="json")
    sampled = _sampled_endpoint(
        process_frames,
        specification.endpoint,
        start=cue,
        limit=limit,
        context=context,
        maximum_samples=specification.maximum_endpoint_samples,
    )
    issues.extend(sampled["issues"])
    if human_response is not None and human_response_scenarios is not None:
        raise ValueError("supply one HRA result type; single and scenario results cannot be combined")
    response_payload = None
    response_scenario_payload = None
    if human_response is not None:
        if not isinstance(human_response, HumanResponseResult):
            raise TypeError("a native HumanResponseResult is required")
        human_response.check_integrity()
        response = _clone(human_response, HumanResponseResult)
        response.check_integrity()
        effect = response.request.effect
        if (
            response.context != context
            or response.request.condition_id != specification.condition_id
            or response.request.opportunity_id != specification.opportunity_id
            or response.request.protection_ids != (specification.protection_id,)
            or not isinstance(effect, DeadlineEffect)
        ):
            raise ValueError(
                "human model context, opportunity, protection or command-deadline endpoint differs"
            )
        if cue is None or deadline is None:
            issues.append("human timing cannot bind without reviewed cue and command deadline")
        elif (
            effect.window.clock_id != context.clock_id
            or not math.isclose(effect.window.cue_time_s, cue, rel_tol=0, abs_tol=1e-9)
            or not math.isclose(effect.window.limit_time_s, deadline, rel_tol=0, abs_tol=1e-9)
        ):
            raise ValueError("the human window must end at command issue, not silently at the process limit")
        elif response.status != "exchange_checked":
            issues.append("the human response model is unresolved")
        else:
            response.risk_probability()
            response_payload = response.model_dump(mode="json")
    if human_response_scenarios is not None:
        if not isinstance(human_response_scenarios, HumanResponseScenarioResult):
            raise TypeError("a native HumanResponseScenarioResult is required")
        if not isinstance(human_response_client, HumanResponseClient):
            raise TypeError("deadline-scenario replay requires the source-bound HumanResponseClient")
        scenario_response = _clone(human_response_scenarios, HumanResponseScenarioResult)
        scenario_response.check_integrity()
        if len(scenario_response.scenario_set.scenarios) > specification.maximum_human_deadline_scenarios:
            raise ValueError("human deadline scenarios exceed the response-trace evaluation budget")
        effect = scenario_response.request.effect
        if (
            scenario_response.context != context
            or scenario_response.request.condition_id != specification.condition_id
            or scenario_response.request.opportunity_id != specification.opportunity_id
            or scenario_response.request.protection_ids != (specification.protection_id,)
            or not isinstance(effect, DeadlineEffect)
        ):
            raise ValueError(
                "human scenario context, condition, opportunity, protection or deadline effect differs"
            )
        scenario_deadlines = None
        if cue is None or deadline is None or limit is None:
            issues.append("human deadline scenarios require reviewed cue, command and process limits")
        else:
            base_deadline = effect.window.limit_time_s
            if (
                effect.window.clock_id != context.clock_id
                or not math.isclose(effect.window.cue_time_s, cue, rel_tol=0, abs_tol=1e-9)
                or not math.isclose(base_deadline, deadline, rel_tol=0, abs_tol=1e-9)
            ):
                raise ValueError(
                    "the HRA scenario base window must match the reviewed cue and command deadline"
                )
            scenario_deadlines = tuple(
                cue + row.available_time_s for row in scenario_response.scenario_set.scenarios
            )
            if any(not math.isfinite(value) for value in scenario_deadlines):
                raise ValueError("an HRA scenario deadline is outside the finite clock range")
            if any(value > deadline + 1e-9 for value in scenario_deadlines):
                raise ValueError("an HRA scenario deadline exceeds the declared command deadline")
            if any(value > limit + 1e-9 for value in scenario_deadlines):
                raise ValueError("an HRA scenario deadline exceeds the declared process limit")
        replay = human_response_client.verify_deadline_scenario_result(scenario_response)
        if not isinstance(replay, dict) or replay.get("valid") is not True:
            reason = (
                replay.get("reason", "owner replay did not confirm")
                if isinstance(replay, dict)
                else "invalid replay response"
            )
            raise ValueError("HRA deadline-scenario owner replay failed: " + str(reason))
        probability = scenario_response.probability
        if scenario_response.status != "exchange_checked" or probability is None:
            issues.append("the HRA deadline-scenario result is unresolved")
        elif (
            probability.context != context
            or probability.measure != "demand_failure_probability"
            or probability.role != "response"
            or "timing" not in probability.includes
            or probability.condition_id != specification.condition_id
            or probability.opportunity_id != specification.opportunity_id
            or probability.protection_ids != (specification.protection_id,)
        ):
            raise ValueError("the HRA scenario probability scope differs from the reviewed trace")
        observed_command_by_scenario = None
        command_scenario_mass = None
        if scenario_deadlines is not None:
            observed_command_by_scenario = [
                {
                    "scenario_id": row.id,
                    "available_time_s": row.available_time_s,
                    "probability_mass": row.probability_mass,
                    "absolute_deadline_s": scenario_deadline,
                    "observed_command_issue_by_deadline": _before(
                        None if command_time is None else [command_time, command_time],
                        scenario_deadline,
                    ),
                }
                for row, scenario_deadline in zip(
                    scenario_response.scenario_set.scenarios, scenario_deadlines, strict=True
                )
            ]
            command_scenario_mass = {
                state: math.fsum(
                    row["probability_mass"]
                    for row in observed_command_by_scenario
                    if row["observed_command_issue_by_deadline"] == state
                )
                for state in ("met", "missed", "uncertain", "unknown")
            }
        response_scenario_payload = {
            "result": scenario_response.model_dump(mode="json"),
            "scenario_deadlines_s": None if scenario_deadlines is None else list(scenario_deadlines),
            "observed_command_by_scenario": observed_command_by_scenario,
            "caller_weighted_observed_command_scenario_mass": command_scenario_mass,
            "scenario_mass_is_not_an_empirical_or_dynamic_probability": True,
            "weighted_failure_probability": None if probability is None else probability.probability,
            "status": scenario_response.status,
            "issues": list(scenario_response.issues),
            "observed_command_issue_time_s": command_time,
            "observed_command_issue_by_deadline": _before(
                None if command_time is None else [command_time, command_time], deadline
            ),
            "scenario_probability_is_not_updated_from_observed_trajectory": True,
        }
    if sampled["state"] == "unknown":
        issues.append("process endpoint remains unknown")
    body = {
        "schema": "pipeline_integrity.response_trace.v1",
        "status": "needs_data" if issues else "exchange_checked",
        "context": context.model_dump(mode="json"),
        "specification": specification.model_dump(mode="json"),
        "alarm_result_digest": alarm.result_digest,
        "alarm_receipt": alarm.model_dump(mode="json"),
        "alarm_source_origin": alarm.context.origin,
        "recorded_alarm_time_s": alarm_time,
        "usable_cue_time_s": cue,
        "command_issue_time_s": command_time,
        "command_issue_by_deadline": _before(
            None if command_time is None else [command_time, command_time], deadline
        ),
        "target_completion_bracket_s": completion,
        "target_by_process_limit": _before(completion, limit),
        "area_at_first_target_sample_m2": residual_area,
        "zero_flow_inferred_from_target": False,
        "process_endpoint": sampled,
        "native_motion": motion_payload,
        "human_response_model": response_payload,
        "human_response_scenario_model": response_scenario_payload,
        "human_probability_updated_from_this_trajectory": False,
        "dynamic_risk_probability": None,
        "field_validation": False,
        "issues": list(dict.fromkeys(issues)),
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return json.loads(json.dumps({**body, "result_digest": digest(body)}, allow_nan=False))


def verify_response_trace_record(
    record,
    *,
    alarm: AlarmReplayResult,
    motion: ValveMotionResult | None,
    specification: ResponseTraceSpec,
    context: RunContext,
    process_frames: tuple[SignalFrame, ...] = (),
    human_response: HumanResponseResult | None = None,
    human_response_scenarios: HumanResponseScenarioResult | None = None,
    human_response_client: HumanResponseClient | None = None,
):
    """Replay the trace adapter from its exact checked owner receipts and source frames.

    This verifies the boundary and sampled-endpoint transformation. It does not
    substitute for independently replaying the alarm, valve, or HRA owner itself.
    """
    try:
        if not isinstance(record, dict):
            raise TypeError("response trace record must be a JSON object")
        checked = json.loads(json.dumps(record, allow_nan=False))
        if checked.get("schema") != "pipeline_integrity.response_trace.v1":
            raise ValueError("unsupported response trace schema")
        if checked.get("result_digest") != digest(
            {key: value for key, value in checked.items() if key != "result_digest"}
        ):
            raise ValueError("response trace seal differs")
        replayed = trace_response(
            alarm,
            motion,
            specification,
            context,
            process_frames=process_frames,
            human_response=human_response,
            human_response_scenarios=human_response_scenarios,
            human_response_client=human_response_client,
        )
        if replayed != checked:
            raise ValueError("response trace adapter or sampled endpoint differs on replay")
        return {"valid": True, "result_digest": checked["result_digest"]}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as error:
        return {"valid": False, "reason": str(error)}
