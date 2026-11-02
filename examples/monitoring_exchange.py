"""Original causal reference data; no observed faults or performance labels are invented."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.monitoring_exchange import (
    FAMILIES,
    REVIEWS,
    MonitoringRequest,
    MonitoringSplit,
)


def make_case(
    method="zscore",
    family="statistical",
    *,
    owner_version=None,
    native_spec_digest=None,
    source_sha256=None,
    parameters=None,
):
    evidence = EvidenceRef(
        id="authored-monitoring",
        citation="Original deterministic monitoring reference history",
        origin="synthetic",
        license_id="MIT",
    )
    context = RunContext(
        asset_id="authored-process",
        scenario_id="separate-training-calibration-heldout",
        clock_id="authored-process-elapsed",
        origin="synthetic",
        evidence=(evidence,),
    )
    frames = []
    for i in range(192):
        values = dict(
            sensor_a=math.sin(i * 0.37) + 0.15 * math.cos(i * 0.19),
            sensor_b=math.cos(i * 0.23) + 0.2 * math.sin(i * 0.41),
            sensor_c=0.4 * math.sin(i * 0.11) + 0.1 * math.cos(i * 0.79),
        )
        if i >= 184:
            values["sensor_a"] += 3.0  # Authored intervention, not a measured fault label.
        frames.append(
            SignalFrame(
                stream_id="authored-process",
                clock_id=context.clock_id,
                time_s=float(i),
                values=values,
                units=dict(sensor_a="Pa", sensor_b="K", sensor_c="m3/s"),
                valid=dict.fromkeys(values, True),
                origin="synthetic",
            )
        )
    splits = []
    for role, start, stop in (("training", 0, 96), ("calibration", 96, 144), ("evaluation", 144, 192)):
        splits.append(
            MonitoringSplit(
                role=role,
                split_id=role,
                physical_run_id="one-authored-run",
                source_group_id="one-authored-source-group",
                source_evidence_id=evidence.id,
                observation_ids=tuple(f"row-{i}" for i in range(start, stop)),
                frames=tuple(frames[start:stop]),
                reference_kind="not_used" if role == "evaluation" else "caller_declared_reference",
            )
        )
    request = MonitoringRequest(
        family=family,
        method=method,
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        population_id="one-authored-series",
        split_policy="chronological_within_run",
        parameters={} if parameters is None else parameters,
        normalize=False,
        false_alarm_fraction=0.1,
        expected_owner_version=owner_version,
        expected_native_spec_digest=native_spec_digest,
        owner_source_tree_sha256=source_sha256,
        applicability_reviews=dict.fromkeys(REVIEWS, True),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence.id),
        evidence_ids=(evidence.id,),
    )
    return request, tuple(splits), context


def authored_native_spec(method, family):
    """Test fixture helper; real execution discovers and pins the owner's actual spec."""
    return dict(
        name=method,
        family=FAMILIES[family],
        training="normal",
        extra="deep" if family in {"neural", "temporal_neural"} else None,
        temporal=family in {"dynamic_subspace", "temporal_neural"} or method.startswith("lag_"),
    )


if __name__ == "__main__":
    from pipeline_integrity.monitoring_exchange import evaluate_monitoring

    request, splits, context = make_case()
    record = evaluate_monitoring(request, splits, context)
    assert record["status"] == "needs_data" and record["native_receipt"] is None
    print(digest(record))
