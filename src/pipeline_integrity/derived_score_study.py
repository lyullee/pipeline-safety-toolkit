"""Source-pinned descriptive analysis for already-derived process anomaly scores."""

from __future__ import annotations

import csv
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest
from .datasets import file_digest


class DerivedScoreMetadata(Contract):
    dataset_id: Name
    asset_id: Name
    dataset_version: Name
    source_uri: Name
    citation: Name
    license_id: Name
    source_file_name: Name
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_evidence_id: Name
    clock_id: Name
    timezone_id: Name
    timestamp_column: Name
    timestamp_format: Name
    label_column: Name
    label_definition: Name
    score_columns: tuple[Name, ...] = ()
    score_minimum: float = 0.0
    score_maximum: float = 100.0
    positive_label_values: tuple[str, ...] = ("1",)
    negative_label_values: tuple[str, ...] = ("0",)
    missing_tokens: tuple[str, ...] = ("", "NULL", "null")
    maximum_rows: int = Field(default=100000, ge=1, le=100000)

    @model_validator(mode="after")
    def schema(self):
        if not self.score_minimum < self.score_maximum:
            raise ValueError("derived-score scale must be ordered")
        if not self.positive_label_values or not self.negative_label_values:
            raise ValueError("both positive and negative label encodings are required")
        if set(self.positive_label_values) & set(self.negative_label_values):
            raise ValueError("positive and negative label encodings must not overlap")
        if len(set(self.score_columns)) != len(self.score_columns):
            raise ValueError("score columns must be unique")
        return self


class DerivedScoreObservation(Contract):
    row_id: Name
    source_row_number: int = Field(ge=2)
    timestamp_local: datetime
    positive_label: bool | None
    scores: dict[Name, float | None]

    @model_validator(mode="after")
    def local_time_and_values(self):
        if self.timestamp_local.utcoffset() is not None:
            raise ValueError("source local civil timestamps must not gain an invented UTC offset")
        if not self.scores:
            raise ValueError("at least one derived score channel is required")
        if any(value is not None and not math.isfinite(value) for value in self.scores.values()):
            raise ValueError(
                "nonfinite score values are invalid; preserve only declared missing tokens as null"
            )
        return self


class DerivedScoreStudy(Contract):
    schema_version: Literal["pipeline_integrity.derived_score_study.v1"] = (
        "pipeline_integrity.derived_score_study.v1"
    )
    metadata: DerivedScoreMetadata
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_bytes: int = Field(ge=1)
    score_columns: tuple[Name, ...] = Field(min_length=1, max_length=512)
    rows: tuple[DerivedScoreObservation, ...] = Field(min_length=1, max_length=100000)
    duplicate_local_timestamp_count: int = Field(ge=0)
    source_order_preserved: Literal[True] = True
    timezone_conversion_applied: Literal[False] = False
    row_level_distribution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def aligned_rows(self):
        if len(self.rows) > self.metadata.maximum_rows:
            raise ValueError("derived-score study exceeds its row budget")
        if self.source_sha256 != self.metadata.expected_sha256:
            raise ValueError("derived-score source digest differs from the reviewed pin")
        if len({row.row_id for row in self.rows}) != len(self.rows):
            raise ValueError("derived-score row identities must be unique")
        if any(
            current.source_row_number <= previous.source_row_number
            or current.timestamp_local < previous.timestamp_local
            for previous, current in zip(self.rows, self.rows[1:], strict=False)
        ):
            raise ValueError("derived-score rows must preserve source-row and local-clock order")
        if any(tuple(row.scores) != self.score_columns for row in self.rows):
            raise ValueError("every source row must preserve the same declared score-column order")
        return self


def read_derived_score_csv(path, metadata: DerivedScoreMetadata) -> DerivedScoreStudy:
    """Read a pinned score CSV without sorting, imputing, converting clocks or exporting rows."""
    q = DerivedScoreMetadata.model_validate(metadata.model_dump(mode="python"))
    source = Path(path)
    if not source.is_file() or source.name != q.source_file_name:
        raise ValueError("source path must identify the reviewed dataset file name")
    source_hash = file_digest(source)
    if source_hash != q.expected_sha256:
        raise ValueError("derived-score CSV SHA-256 differs from its reviewed source pin")
    observations = []
    previous = None
    seen_timestamps = set()
    duplicate_count = 0
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)):
            raise ValueError("derived-score CSV requires unique named columns")
        if q.timestamp_column not in headers or q.label_column not in headers:
            raise ValueError("timestamp or target column is absent from the source header")
        scores = q.score_columns or tuple(
            name for name in headers if name not in {q.timestamp_column, q.label_column}
        )
        if not scores or any(name not in headers for name in scores):
            raise ValueError("declared score columns must exist in the source header")
        if set(scores) & {q.timestamp_column, q.label_column}:
            raise ValueError("timestamp and label columns cannot be treated as scores")
        for index, row in enumerate(reader):
            if index >= q.maximum_rows:
                raise ValueError("derived-score CSV row budget exceeded")
            if None in row or set(row) != set(headers):
                raise ValueError("source row width differs from the declared CSV header")
            try:
                timestamp_text = row[q.timestamp_column].strip()
                timestamp = datetime.strptime(timestamp_text, q.timestamp_format)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid local timestamp at source row {index + 2}") from exc
            if previous is not None and timestamp < previous:
                raise ValueError("source timestamps reverse order; reader will not sort observations")
            previous = timestamp
            if timestamp in seen_timestamps:
                duplicate_count += 1
            seen_timestamps.add(timestamp)
            label_text = row[q.label_column].strip()
            if label_text in q.missing_tokens:
                label = None
            elif label_text in q.positive_label_values:
                label = True
            elif label_text in q.negative_label_values:
                label = False
            else:
                raise ValueError(f"unknown binary label encoding at source row {index + 2}")
            values = {}
            for name in scores:
                cell = row[name].strip()
                if cell in q.missing_tokens:
                    values[name] = None
                    continue
                try:
                    value = float(cell)
                except ValueError as exc:
                    raise ValueError(f"nonnumeric score at source row {index + 2}") from exc
                if not math.isfinite(value) or not q.score_minimum <= value <= q.score_maximum:
                    raise ValueError(f"score outside the reviewed scale at source row {index + 2}")
                values[name] = value
            observations.append(
                DerivedScoreObservation(
                    row_id=f"{q.dataset_id}:row:{index + 1:08d}",
                    source_row_number=index + 2,
                    timestamp_local=timestamp,
                    positive_label=label,
                    scores=values,
                )
            )
    if not observations:
        raise ValueError("derived-score CSV contains no data rows")
    return DerivedScoreStudy(
        metadata=q,
        source_sha256=source_hash,
        source_bytes=source.stat().st_size,
        score_columns=tuple(scores),
        rows=tuple(observations),
        duplicate_local_timestamp_count=duplicate_count,
    )


class ScoreThresholdRequest(Contract):
    thresholds: dict[Name, float] = Field(min_length=1, max_length=512)
    direction: Literal["high_is_positive", "low_is_positive"] = "high_is_positive"
    selection_evidence_id: Name
    missing_score_policy: Literal["exclude_that_channel_row"] = "exclude_that_channel_row"

    @model_validator(mode="after")
    def threshold_values(self):
        if any(not math.isfinite(value) for value in self.thresholds.values()):
            raise ValueError("thresholds must be finite")
        return self


class ScoreThresholdMetric(Contract):
    score_column: Name
    threshold: float
    direction: Literal["high_is_positive", "low_is_positive"]
    evaluated_rows: int = Field(ge=0)
    missing_score_rows: int = Field(ge=0)
    unlabeled_rows: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float | None
    recall: float | None
    specificity: float | None


class ScoreThresholdResult(Contract):
    schema_version: Literal["pipeline_integrity.derived_score_threshold_result.v1"] = (
        "pipeline_integrity.derived_score_threshold_result.v1"
    )
    study_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: ScoreThresholdRequest
    metrics: tuple[ScoreThresholdMetric, ...] = Field(min_length=1, max_length=512)
    evaluation_scope: Literal["single_source_descriptive_no_independent_holdout"] = (
        "single_source_descriptive_no_independent_holdout"
    )
    label_is_event_proximity_not_event_time: Literal[True] = True
    statistical_independence_claim: Literal[False] = False
    field_validation_claim: Literal[False] = False
    score_generator_validation_claim: Literal[False] = False
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = ScoreThresholdResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("derived-score threshold receipt changed")


class ChronologicalThresholdRequest(Contract):
    cutoff_local: datetime
    purge_before_s: float = 604800.0
    purge_after_s: float = 604800.0
    split_evidence_id: Name

    @model_validator(mode="after")
    def reviewed_window(self):
        if self.cutoff_local.utcoffset() is not None:
            raise ValueError("cutoff must use the source naive local-civil clock")
        if self.purge_before_s < 604800 or self.purge_after_s < 604800:
            raise ValueError("the event-proximity label requires at least seven days of purge per side")
        if not math.isfinite(self.purge_before_s) or not math.isfinite(self.purge_after_s):
            raise ValueError("purge durations must be finite")
        return self


class ChronologicalThresholdResult(Contract):
    schema_version: Literal["pipeline_integrity.derived_score_chronological_result.v1"] = (
        "pipeline_integrity.derived_score_chronological_result.v1"
    )
    source_study_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    split_request: ChronologicalThresholdRequest
    threshold_request: ScoreThresholdRequest
    training: ScoreThresholdResult
    evaluation: ScoreThresholdResult
    training_source_rows: int = Field(ge=1)
    purged_source_rows: int = Field(ge=0)
    evaluation_source_rows: int = Field(ge=1)
    purge_start_local: datetime
    purge_end_local: datetime
    label_windows_proven_disjoint: Literal[False] = False
    independent_episode_claim: Literal[False] = False
    independent_validation_claim: Literal[False] = False
    field_validation_claim: Literal[False] = False
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def aligned_receipts(self):
        if (
            self.training.request != self.threshold_request
            or self.evaluation.request != self.threshold_request
        ):
            raise ValueError("training and evaluation receipts must use the same caller-fixed threshold")
        if self.purge_start_local >= self.purge_end_local:
            raise ValueError("chronological purge interval must be ordered")
        return self

    def check_integrity(self):
        checked = ChronologicalThresholdResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("chronological score receipt changed")


def evaluate_score_thresholds(
    study: DerivedScoreStudy,
    request: ScoreThresholdRequest,
    context: RunContext,
) -> ScoreThresholdResult:
    """Compute caller-fixed descriptive confusion counts; no threshold fitting or inference."""
    q = DerivedScoreStudy.model_validate(study.model_dump(mode="python"))
    opts = ScoreThresholdRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (c.asset_id, c.clock_id) != (q.metadata.asset_id, q.metadata.clock_id):
        raise ValueError("derived-score asset or local clock differs from the run context")
    evidence = {item.id for item in c.evidence}
    if q.metadata.source_evidence_id not in evidence or opts.selection_evidence_id not in evidence:
        raise ValueError("source citation and preselected threshold evidence must be bound to context")
    if set(opts.thresholds) - set(q.score_columns):
        raise ValueError("threshold request names a score channel absent from the pinned source")
    if any(
        not q.metadata.score_minimum <= value <= q.metadata.score_maximum
        for value in opts.thresholds.values()
    ):
        raise ValueError("threshold lies outside the source score scale")
    metrics = []
    for column, threshold in opts.thresholds.items():
        tp = fp = tn = fn = missing = unlabeled = evaluated = 0
        for row in q.rows:
            score = row.scores[column]
            if score is None:
                missing += 1
                continue
            if row.positive_label is None:
                unlabeled += 1
                continue
            alarm = score >= threshold if opts.direction == "high_is_positive" else score <= threshold
            evaluated += 1
            if alarm and row.positive_label:
                tp += 1
            elif alarm:
                fp += 1
            elif row.positive_label:
                fn += 1
            else:
                tn += 1
        metrics.append(
            ScoreThresholdMetric(
                score_column=column,
                threshold=threshold,
                direction=opts.direction,
                evaluated_rows=evaluated,
                missing_score_rows=missing,
                unlabeled_rows=unlabeled,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
                precision=tp / (tp + fp) if tp + fp else None,
                recall=tp / (tp + fn) if tp + fn else None,
                specificity=tn / (tn + fp) if tn + fp else None,
            )
        )
    body = {
        "schema_version": "pipeline_integrity.derived_score_threshold_result.v1",
        "study_digest": digest(q),
        "context_digest": digest(c),
        "request": opts,
        "metrics": tuple(metrics),
        "evaluation_scope": "single_source_descriptive_no_independent_holdout",
        "label_is_event_proximity_not_event_time": True,
        "statistical_independence_claim": False,
        "field_validation_claim": False,
        "score_generator_validation_claim": False,
    }
    result = ScoreThresholdResult(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def verify_score_threshold_result(
    result: ScoreThresholdResult,
    study: DerivedScoreStudy,
    context: RunContext,
) -> bool:
    """Recompute the fixed-threshold descriptive counts from the pinned input."""
    checked = ScoreThresholdResult.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    replay = evaluate_score_thresholds(study, checked.request, context)
    if replay != checked:
        raise ValueError("derived-score threshold replay differs from its receipt")
    return True


def evaluate_chronological_score_thresholds(
    study: DerivedScoreStudy,
    split: ChronologicalThresholdRequest,
    thresholds: ScoreThresholdRequest,
    context: RunContext,
) -> ChronologicalThresholdResult:
    """Apply caller-fixed thresholds across chronological partitions with a seven-day purge gap."""
    q = DerivedScoreStudy.model_validate(study.model_dump(mode="python"))
    split_request = ChronologicalThresholdRequest.model_validate(split.model_dump(mode="python"))
    threshold_request = ScoreThresholdRequest.model_validate(thresholds.model_dump(mode="python"))
    run_context = RunContext.model_validate(context.model_dump(mode="python"))
    if split_request.split_evidence_id not in {item.id for item in run_context.evidence}:
        raise ValueError("chronological cutoff and purge selection require context evidence")
    before = split_request.cutoff_local - timedelta(seconds=split_request.purge_before_s)
    after = split_request.cutoff_local + timedelta(seconds=split_request.purge_after_s)
    training_rows = tuple(row for row in q.rows if row.timestamp_local < before)
    purged_rows = tuple(row for row in q.rows if before <= row.timestamp_local < after)
    evaluation_rows = tuple(row for row in q.rows if row.timestamp_local >= after)
    if not training_rows or not evaluation_rows:
        raise ValueError("chronological split must retain at least one source row on both sides")

    def evaluate(rows):
        subset = q.model_copy(update={"rows": rows})
        subset = DerivedScoreStudy.model_validate(subset.model_dump(mode="python"))
        return evaluate_score_thresholds(subset, threshold_request, run_context)

    training = evaluate(training_rows)
    evaluation = evaluate(evaluation_rows)
    body = {
        "schema_version": "pipeline_integrity.derived_score_chronological_result.v1",
        "source_study_digest": digest(q),
        "context_digest": digest(run_context),
        "split_request": split_request,
        "threshold_request": threshold_request,
        "training": training,
        "evaluation": evaluation,
        "training_source_rows": len(training_rows),
        "purged_source_rows": len(purged_rows),
        "evaluation_source_rows": len(evaluation_rows),
        "purge_start_local": before,
        "purge_end_local": after,
        "label_windows_proven_disjoint": False,
        "independent_episode_claim": False,
        "independent_validation_claim": False,
        "field_validation_claim": False,
    }
    result = ChronologicalThresholdResult(**body, result_digest="0" * 64)
    result = result.model_copy(update={"result_digest": digest(result.model_dump(mode="json", exclude={"result_digest"}))})
    result.check_integrity()
    return result


def verify_chronological_score_thresholds(
    result: ChronologicalThresholdResult,
    study: DerivedScoreStudy,
    context: RunContext,
) -> bool:
    """Rebuild both time partitions and replay their fixed-threshold aggregate receipts."""
    checked = ChronologicalThresholdResult.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    replay = evaluate_chronological_score_thresholds(
        study, checked.split_request, checked.threshold_request, context
    )
    if replay != checked:
        raise ValueError("chronological threshold replay differs from its receipt")
    return True
