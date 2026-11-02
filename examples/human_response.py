"""Original conditional response reference; install the separate HRA project to run."""

from human_reliability.contracts import Estimate, HumanFailureEvent

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.response_exchange import (
    DeadlineEffect,
    HumanResponseClient,
    HumanResponseRequest,
    LognormalResponse,
    ResponseWindow,
)


def make_case():
    context = RunContext(
        asset_id="authored_reference",
        scenario_id="response_window",
        clock_id="scenario_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="assumption",
                citation="Original API example; all numerical inputs are hypothetical",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    event = HumanFailureEvent(
        id="manual_response",
        task_id="authored_task",
        step_ids=("response_objective",),
        role="response",
        failure_definition="The correct response is not completed within the reviewed cue-to-limit window",
        opportunity_definition="One declared response demand",
        conditioning="A usable response cue exists",
        deadline_s=10.0,
        includes_timing=True,
    )
    initial = Estimate(
        event_id=event.id,
        method="authored_reference_assumption",
        probability=0.2,
        conditioning=event.conditioning,
        evidence_ids=("assumption",),
        status="assumed",
    )
    request = HumanResponseRequest(
        condition_id="usable_cue",
        opportunity_id="one_response_demand",
        protection_ids=("manual_response",),
        applicability_evidence_id="assumption",
        applicability_rationale="Synthetic API reference only",
        effect=DeadlineEffect(
            window=ResponseWindow(
                clock_id=context.clock_id, cue_time_s=5.0, limit_time_s=15.0, evidence_id="assumption"
            ),
            time_model=LognormalResponse(median_s=10.0, log_sigma=0.5, evidence_id="assumption"),
            population_definition="Completion time conditional on no initial response failure",
            bin_boundaries_s=(5.0, 10.0, 20.0),
        ),
    )
    return initial, event, request, context


if __name__ == "__main__":
    print(HumanResponseClient().evaluate(*make_case()).model_dump_json(indent=2))
