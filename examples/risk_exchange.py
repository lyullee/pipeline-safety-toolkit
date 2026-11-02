"""Authored conditional FTA example; write JSON without installing the optional owner engine."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.risk_exchange import FaultNode, FaultTreeRequest, RiskProbability, run_risk_job


def make_job():
    context = RunContext(
        asset_id="example_pipe",
        scenario_id="shared_event_reference",
        clock_id="declared_demands",
        origin="synthetic",
    )
    events = []
    for key, value in (("A", 0.2), ("B", 0.1)):
        source = {"probability": value, "basis": "authored synthetic assumption"}
        events.append(
            RiskProbability(
                id=key,
                probability=value,
                measure="demand_failure_probability",
                condition_id="declared_demand",
                conditioning="A declared synthetic equipment demand",
                opportunity_id="one_demand",
                opportunity_definition="One equipment demand in the reference scenario",
                failure_definition=f"Equipment {key} does not perform its declared function",
                role="equipment",
                protection_ids=(f"function:{key}",),
                evidence_ids=(f"assumed:{key}",),
                qualification="assumed",
                context=context,
                source_kind="declared",
                source_payload=source,
                source_digest=digest(source),
            )
        )
    a, b = (FaultNode(kind="event", event_id=key) for key in ("A", "B"))
    request = FaultTreeRequest(
        root=FaultNode(kind="OR", children=(a, FaultNode(kind="AND", children=(a, b)))),
        events=tuple(events),
        condition_id="declared_demand",
        opportunity_id="one_demand",
        dependence="declared_independent",
        dependence_evidence_id="assumed:separate_equipment_reference",
    )
    return {
        "operation": "fault_tree",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, help="optional local human-reliability-toolkit checkout")
    parser.add_argument("--write-job", type=Path)
    args = parser.parse_args()
    job = make_job()
    if args.write_job:
        args.write_job.parent.mkdir(parents=True, exist_ok=True)
        args.write_job.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        return
    if args.hra:
        sys.path.insert(0, str((args.hra / "src").resolve()))
    result = run_risk_job(job)
    assert abs(result["probability"]["probability"] - 0.2) < 1e-12
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
