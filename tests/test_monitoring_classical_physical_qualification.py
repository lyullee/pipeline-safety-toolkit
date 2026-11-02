"""Bounded native PCA-SPE exchange against a hand-solvable orthogonal subspace."""

from pathlib import Path

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.monitoring_exchange import (
    REVIEWS,
    MonitoringRequest,
    MonitoringSplit,
    NativeMonitoringClient,
    evaluate_monitoring,
    monitoring_source_fingerprint,
)


BOOK_PROJECTS = Path(__file__).resolve().parents[3]
OWNER_ROOT = next(
    candidate
    for candidate in BOOK_PROJECTS.glob("*/process-monitoring-toolkit")
    if (candidate / "src/process_monitoring").is_dir()
)
SOURCE_SHA = monitoring_source_fingerprint(OWNER_ROOT)["source_tree_sha256"]
CLIENT = NativeMonitoringClient(OWNER_ROOT)
SPEC = CLIENT.describe("pca", expected_source_sha256=SOURCE_SHA)
EVIDENCE = EvidenceRef(
    id="analytic-pca-reference",
    citation="Independently authored orthogonal-subspace reference data",
    origin="synthetic",
    license_id="MIT",
)
CONTEXT = RunContext(
    asset_id="synthetic-pca-process",
    scenario_id="closed_form_spe_projection",
    clock_id="seconds_from_reference_start",
    origin="synthetic",
    evidence=(EVIDENCE,),
)


def _split(role, start, values):
    frames = tuple(
        SignalFrame(
            stream_id="synthetic-pca-stream",
            clock_id=CONTEXT.clock_id,
            time_s=float(start + index),
            values={"channel_x": float(x), "channel_y": float(y)},
            units={"channel_x": "MPa", "channel_y": "MPa"},
            valid={"channel_x": True, "channel_y": True},
            origin="synthetic",
        )
        for index, (x, y) in enumerate(values)
    )
    return MonitoringSplit(
        role=role,
        split_id=f"{role}-pca",
        physical_run_id="synthetic-run-01",
        source_group_id="synthetic-source-01",
        source_evidence_id=EVIDENCE.id,
        observation_ids=tuple(f"{role}-{i}" for i in range(len(frames))),
        frames=frames,
        reference_kind="not_used" if role == "evaluation" else "caller_declared_reference",
    )


TRAIN = _split("training", 0, [(-2, 0), (-1, 0), (1, 0), (2, 0), (0, -1), (0, 1)])
CALIBRATION = _split("calibration", 10, [(0, -1), (0, 1), (0, -1), (0, 1)])
EVALUATION = _split("evaluation", 20, [(1, 0), (3, 2)])
BASE_REQUEST = MonitoringRequest(
    family="classical",
    method="pca",
    asset_id=CONTEXT.asset_id,
    clock_id=CONTEXT.clock_id,
    population_id="one-synthetic-normal-reference-population",
    split_policy="chronological_within_run",
    parameters={"n_components": 1, "statistic": "spe"},
    normalize=False,
    false_alarm_fraction=0.5,
    expected_owner_version=SPEC["owner_version"],
    expected_native_spec_digest=SPEC["native_spec_digest"],
    owner_source_tree_sha256=SOURCE_SHA,
    applicability_reviews=dict.fromkeys(REVIEWS, True),
    review_evidence_ids=dict.fromkeys(REVIEWS, EVIDENCE.id),
    evidence_ids=(EVIDENCE.id,),
)


def test_native_pca_spe_scores_match_hand_computed_training_subspace_residual():
    record = evaluate_monitoring(
        BASE_REQUEST,
        (TRAIN, CALIBRATION, EVALUATION),
        CONTEXT,
        client=CLIENT,
    )
    assert record["status"] == "computed"
    native = record["native_receipt"]["evaluation"]
    # Training covariance is diagonal: Var(x)=10/6 and Var(y)=2/6, so the
    # one-component principal subspace is exactly the x axis. Mean training
    # squared residual is (0+0+0+0+1+1)/6=1/3. Thus SPE is y^2/(1/3).
    assert native["scores"] == pytest.approx([0.0, 12.0], abs=1e-10)
    assert native["threshold"] == pytest.approx(3.0, abs=1e-10)
    assert native["alarms"] == [False, True]
    assert record["score_is_probability_or_HEP"] is False
    assert record["native_receipt"]["source_pin"]["source_unchanged"] is True


def test_missing_applicability_review_withholds_native_computation():
    request = BASE_REQUEST.model_copy(update={"applicability_reviews": {}})
    record = evaluate_monitoring(
        request,
        (TRAIN, CALIBRATION, EVALUATION),
        CONTEXT,
        client=CLIENT,
    )
    assert record["status"] == "needs_data"
    assert record["native_receipt"] is None
    assert any("review and evidence required" in issue for issue in record["issues"])
