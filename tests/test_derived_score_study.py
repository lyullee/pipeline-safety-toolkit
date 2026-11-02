"""A source-derived score stays distinct from physical sensor measurements."""

import hashlib
from datetime import datetime

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.derived_score_study import (
    ChronologicalThresholdRequest,
    DerivedScoreMetadata,
    ScoreThresholdRequest,
    evaluate_chronological_score_thresholds,
    evaluate_score_thresholds,
    read_derived_score_csv,
    verify_chronological_score_thresholds,
    verify_score_threshold_result,
)


def write_csv(path, rows):
    path.write_text(
        "timestamp,fault_d7,pressure_score,energy_score\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def metadata(path):
    return DerivedScoreMetadata(
        dataset_id="derived_water_demo",
        asset_id="anonymized_zone",
        dataset_version="fixture-v1",
        source_uri="https://example.invalid/data",
        citation="Authored test fixture",
        license_id="CC-BY-4.0",
        source_file_name=path.name,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_evidence_id="source",
        clock_id="local-civil-time",
        timezone_id="Europe/Bratislava",
        timestamp_column="timestamp",
        timestamp_format="%d-%m-%Y %H:%M",
        label_column="fault_d7",
        label_definition="publisher event-proximity label",
        score_columns=("pressure_score", "energy_score"),
    )


def context():
    evidence = (
        EvidenceRef(id="source", citation="Pinned derived-score source", origin="literature"),
        EvidenceRef(
            id="threshold_review",
            citation="Preselected demonstration threshold",
            origin="user_declared",
        ),
        EvidenceRef(
            id="split_review",
            citation="Reviewed chronological cutoff and purge window",
            origin="user_declared",
        ),
    )
    return RunContext(
        asset_id="anonymized_zone",
        scenario_id="score_label_description",
        clock_id="local-civil-time",
        origin="measured",
        evidence=evidence,
        metadata={"value_representation": "publisher_derived_anomaly_scores"},
    )


def test_reader_preserves_source_order_missing_values_and_local_civil_time(tmp_path):
    path = write_csv(
        tmp_path / "scores.csv",
        [
            "01-01-2024 00:00,0,10,5",
            "01-01-2024 01:00,1,80,20",
            "01-01-2024 02:00,0,70,30",
            "01-01-2024 03:00,1,,40",
        ],
    )
    study = read_derived_score_csv(path, metadata(path))
    assert study.source_order_preserved
    assert not study.timezone_conversion_applied
    assert study.rows[0].timestamp_local.tzinfo is None
    assert study.rows[3].scores["pressure_score"] is None
    assert study.rows[3].row_id.endswith("00000004")
    assert study.rows[1].positive_label is True


def test_fixed_threshold_counts_exclude_only_missing_channel_or_label_rows(tmp_path):
    path = write_csv(
        tmp_path / "scores.csv",
        [
            "01-01-2024 00:00,0,10,5",
            "01-01-2024 01:00,1,80,20",
            "01-01-2024 02:00,0,70,30",
            "01-01-2024 03:00,1,,40",
            "01-01-2024 04:00,,95,50",
        ],
    )
    study = read_derived_score_csv(path, metadata(path))
    request = ScoreThresholdRequest(
        thresholds={"pressure_score": 50.0},
        selection_evidence_id="threshold_review",
    )

    result = evaluate_score_thresholds(study, request, context())
    metric = result.metrics[0]

    assert (
        metric.true_positive,
        metric.false_positive,
        metric.true_negative,
        metric.false_negative,
    ) == (1, 1, 1, 0)
    assert metric.evaluated_rows == 3
    assert metric.missing_score_rows == 1
    assert metric.unlabeled_rows == 1
    assert metric.precision == 0.5
    assert metric.recall == 1.0
    assert metric.specificity == 0.5
    assert result.evaluation_scope == "single_source_descriptive_no_independent_holdout"
    assert not result.statistical_independence_claim
    assert verify_score_threshold_result(result, study, context())


def test_reader_rejects_changed_source_unknown_labels_and_reversed_time(tmp_path):
    path = write_csv(
        tmp_path / "scores.csv",
        ["01-01-2024 01:00,0,10,5", "01-01-2024 00:00,1,80,20"],
    )
    with pytest.raises(ValueError, match="reverse order"):
        read_derived_score_csv(path, metadata(path))

    path = write_csv(tmp_path / "scores.csv", ["01-01-2024 00:00,unknown,10,5"])
    with pytest.raises(ValueError, match="unknown binary label"):
        read_derived_score_csv(path, metadata(path))


def test_threshold_request_requires_source_and_selection_evidence(tmp_path):
    path = write_csv(tmp_path / "scores.csv", ["01-01-2024 00:00,1,80,20"])
    study = read_derived_score_csv(path, metadata(path))
    request = ScoreThresholdRequest(
        thresholds={"pressure_score": 50.0},
        selection_evidence_id="threshold_review",
    )
    with pytest.raises(ValueError, match="source citation and preselected threshold"):
        evaluate_score_thresholds(study, request, context().model_copy(update={"evidence": ()}))


def test_chronological_fixed_threshold_receipt_purges_seven_days_each_side(tmp_path):
    path = write_csv(
        tmp_path / "scores.csv",
        [
            "01-01-2024 00:00,0,10,5",
            "07-01-2024 00:00,1,80,20",
            "08-01-2024 00:00,0,30,10",
            "14-01-2024 00:00,1,70,40",
            "21-01-2024 00:00,0,20,30",
            "22-01-2024 00:00,1,90,60",
            "30-01-2024 00:00,0,40,30",
        ],
    )
    study = read_derived_score_csv(path, metadata(path))
    split = ChronologicalThresholdRequest(
        cutoff_local=datetime(2024, 1, 15),
        split_evidence_id="split_review",
    )
    thresholds = ScoreThresholdRequest(
        thresholds={"pressure_score": 50.0},
        selection_evidence_id="threshold_review",
    )

    result = evaluate_chronological_score_thresholds(study, split, thresholds, context())

    assert result.training_source_rows == 2
    assert result.purged_source_rows == 3
    assert result.evaluation_source_rows == 2
    assert result.training.metrics[0].evaluated_rows == 2
    assert result.evaluation.metrics[0].evaluated_rows == 2
    assert result.purge_start_local.isoformat() == "2024-01-08T00:00:00"
    assert result.purge_end_local.isoformat() == "2024-01-22T00:00:00"
    assert not result.label_windows_proven_disjoint
    assert not result.independent_episode_claim
    assert not result.independent_validation_claim
    assert verify_chronological_score_thresholds(result, study, context())


def test_chronological_split_requires_local_clock_and_minimum_purge():
    from datetime import timezone

    with pytest.raises(ValueError, match="at least seven days"):
        ChronologicalThresholdRequest(
            cutoff_local=datetime(2024, 1, 15),
            purge_before_s=86400,
            split_evidence_id="split_review",
        )
    with pytest.raises(ValueError, match="naive local-civil clock"):
        ChronologicalThresholdRequest(
            cutoff_local=datetime(2024, 1, 15, tzinfo=timezone.utc),
            split_evidence_id="split_review",
        )
