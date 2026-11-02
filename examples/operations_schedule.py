"""Original planning example; times, crews, costs and isolation conflicts are declared assumptions."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.scheduling import (
    CapacityWindow,
    Operation,
    OperationMode,
    PlanningResource,
    ResourceHold,
    ScheduleRequest,
    TimingRelation,
    solve_schedule,
)


def make_case():
    source = "declared_operations_reference"
    context = RunContext(
        asset_id="example_pipe",
        scenario_id="inspection_planning",
        clock_id="planning_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=source,
                citation="Original planning example with hypothetical durations and resources",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )

    def mode(name, duration, resources, **kwargs):
        return OperationMode(id=name, duration_s=duration, resources=resources, evidence_id=source, **kwargs)

    specification = ScheduleRequest(
        horizon_s=600.0,
        time_quantum_s=60.0,
        evidence_id=source,
        resources=(
            PlanningResource(
                id="operator",
                capacity=1,
                evidence_id=source,
                calendar=(CapacityWindow(start_s=0.0, finish_s=60.0, capacity=0),),
            ),
            PlanningResource(id="inspector", capacity=2, evidence_id=source),
            PlanningResource(id="asset", capacity=1, evidence_id=source),
        ),
        consumable_budgets={"spare": 1},
        operations=(
            Operation(id="isolate", modes=(mode("normal", 60.0, {"operator": 1}),)),
            Operation(id="verify", modes=(mode("normal", 60.0, {"inspector": 1}),)),
            Operation(
                id="inspect",
                modes=(
                    mode("one_person", 120.0, {"inspector": 1}),
                    mode("two_people", 60.0, {"inspector": 2}, fixed_cost=3, consumables={"spare": 1}),
                ),
            ),
            Operation(id="restore", modes=(mode("normal", 60.0, {"operator": 1}),)),
            Operation(id="other_use", modes=(mode("normal", 180.0, {"asset": 1}),)),
        ),
        relations=tuple(
            TimingRelation(id=f"{a}_before_{b}", before=a, after=b)
            for a, b in (("isolate", "verify"), ("verify", "inspect"), ("inspect", "restore"))
        ),
        holds=(
            ResourceHold(
                id="declared_asset_hold",
                from_operation="isolate",
                to_operation="restore",
                resources={"asset": 1},
                evidence_id=source,
            ),
        ),
    )
    return specification, context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-job", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    specification, context = make_case()
    if args.write_job:
        args.write_job.parent.mkdir(parents=True, exist_ok=True)
        args.write_job.write_text(
            json.dumps(
                {
                    "specification": specification.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return
    result = solve_schedule(specification, context)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    else:
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
