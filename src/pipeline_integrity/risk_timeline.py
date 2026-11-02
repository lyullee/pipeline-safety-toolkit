"""Source-sealed response timelines for reviewed downstream risk analysis.

Chronology is preserved; event probabilities and dynamic fault trees are not inferred.
"""

import json
import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, digest


class RiskTimelineRequest(Contract):
    id: Name
    asset_id: Name
    scenario_id: Name
    clock_id: Name
    start_s: Nonnegative
    finish_s: float = Field(gt=0)
    source_origin: Literal["measured", "observed", "laboratory", "simulation", "synthetic"]
    event_record_digests: tuple[str, ...] = Field(min_length=1, max_length=10000)
    mapping_evidence_ids: tuple[Name, ...] = Field(min_length=1)
    timeline_mapping_reviewed: bool = False

    @model_validator(mode="after")
    def valid_window(self):
        if self.finish_s <= self.start_s:
            raise ValueError("risk timeline needs a positive half-open observation window")
        if len(set(self.event_record_digests)) != len(self.event_record_digests):
            raise ValueError("a response trace cannot be included twice")
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in self.event_record_digests
        ):
            raise ValueError("each response trace requires its sealed SHA-256 digest")
        if len(set(self.mapping_evidence_ids)) != len(self.mapping_evidence_ids):
            raise ValueError("timeline mapping evidence identifiers must be unique")
        return self


def build_observed_risk_timeline(request: RiskTimelineRequest, traces):
    """Bind checked response-trace records without interpreting them as probabilities."""
    request = RiskTimelineRequest.model_validate_json(request.model_dump_json())
    traces = tuple(traces)
    if not request.timeline_mapping_reviewed:
        raise ValueError("asset, clock, scenario, origin and event mapping requires review")
    if len(traces) != len(request.event_record_digests):
        raise ValueError("each requested response-trace digest must have exactly one record")
    records = []
    for expected, supplied in zip(request.event_record_digests, traces, strict=True):
        if not isinstance(supplied, dict):
            raise TypeError("response trace records must be JSON objects")
        trace = json.loads(json.dumps(supplied, allow_nan=False))
        body = {key: value for key, value in trace.items() if key != "result_digest"}
        if trace.get("result_digest") != digest(body) or expected != trace["result_digest"]:
            raise ValueError("response trace digest differs from its sealed timeline input")
        if trace.get("schema") != "pipeline_integrity.response_trace.v1":
            raise ValueError("timeline accepts only the existing response-trace schema")
        context = trace.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("response trace context must be a JSON object")
        context_evidence = {item.get("id") for item in context.get("evidence", []) if isinstance(item, dict)}
        if not set(request.mapping_evidence_ids) <= context_evidence:
            raise ValueError("timeline mapping evidence must be present in every source context")
        if (
            context.get("asset_id") != request.asset_id
            or context.get("scenario_id") != request.scenario_id
            or context.get("clock_id") != request.clock_id
            or context.get("origin") != request.source_origin
        ):
            raise ValueError("response trace asset, scenario, clock or origin differs")
        if trace.get("status") != "exchange_checked":
            raise ValueError("unresolved response traces cannot enter the reviewed event timeline")
        specification = trace.get("specification", {})
        if not isinstance(specification, dict):
            raise ValueError("response trace specification must be a JSON object")
        endpoint = trace.get("process_endpoint") or {}
        if not isinstance(endpoint, dict):
            raise ValueError("response trace process endpoint must be a JSON object")
        endpoint_state = endpoint.get("state")
        first_qualifying_time = endpoint.get("first_qualifying_sample_time_s")
        if endpoint_state == "observed_before_limit":
            endpoint_time = first_qualifying_time
        elif endpoint_state == "not_observed_by_limit":
            endpoint_time = specification.get("process_limit_time_s")
        else:
            raise ValueError("a complete sampled endpoint result is required for the observation timeline")
        times = (
            trace.get("recorded_alarm_time_s"),
            trace.get("usable_cue_time_s"),
            trace.get("command_issue_time_s"),
        )
        event_times = (*times, endpoint_time)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in event_times
        ):
            raise ValueError("alarm, cue, command and sampled endpoint times must be finite numbers")
        if not (request.start_s <= times[0] <= times[1] <= times[2] <= endpoint_time <= request.finish_s):
            raise ValueError("response event chronology lies outside the declared timeline window")
        if (
            endpoint.get("discrete_sample_rule_only") is not True
            or endpoint.get("continuous_between_sample_safety_proven") is not False
            or trace.get("dynamic_risk_probability") is not None
        ):
            raise ValueError(
                "a complete discrete endpoint receipt is required; continuous safety is not implied"
            )
        if trace.get("alarm_source_origin") != request.source_origin:
            raise ValueError("response trace source origin differs from the reviewed timeline")
        records.append(
            {
                "record_digest": trace["result_digest"],
                "alarm_activation_id": specification.get("alarm_activation_id"),
                "command_id": specification.get("command_id"),
                "protection_id": specification.get("protection_id"),
                "opportunity_id": specification.get("opportunity_id"),
                "times_s": {
                    "alarm_recorded": times[0],
                    "cue_usable": times[1],
                    "command_issued": times[2],
                    "process_endpoint_observed": endpoint_time,
                },
                "process_endpoint_state": endpoint_state,
                "first_qualifying_sample_time_s": first_qualifying_time,
                "command_issue_by_deadline": trace.get("command_issue_by_deadline"),
                "target_by_process_limit": trace.get("target_by_process_limit"),
                "source_origin": trace.get("alarm_source_origin"),
            }
        )
    opportunities = [(row["opportunity_id"], row["command_id"]) for row in records]
    if len(opportunities) != len(set(opportunities)):
        raise ValueError("the same opportunity/command response cannot be counted twice")
    alarm_times = [row["times_s"]["alarm_recorded"] for row in records]
    if any(later < earlier for earlier, later in zip(alarm_times, alarm_times[1:], strict=False)):
        raise ValueError("response traces must be supplied in recorded alarm chronology")
    body = {
        "schema": "pipeline_integrity.observed_risk_timeline.v1",
        "status": "exchange_checked",
        "request": request.model_dump(mode="json"),
        "events": records,
        "input_digest": digest(request),
        "dynamic_risk_probability": None,
        "probability_inferred_from_trajectory": False,
        "event_dependence_modelled": False,
        "time_dependent_failure_or_repair_modelled": False,
        "annual_frequency_inferred": False,
        "field_qualification": False,
    }
    return {**body, "result_digest": digest(body)}
