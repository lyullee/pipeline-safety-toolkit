"""Create an aggregate-only, caller-threshold study record for the water-pilot source."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.derived_score_study import (
    DerivedScoreMetadata,
    evaluate_score_thresholds,
    read_derived_score_csv,
)
from pipeline_integrity.datasets import catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--ledger", type=Path, default=Path("artifacts/acquisition/water_pilot.json"))
    parser.add_argument(
        "--threshold", action="append", required=True, help="score_column=value, fixed before analysis"
    )
    parser.add_argument(
        "--direction",
        choices=("high_is_positive", "low_is_positive"),
        default="high_is_positive",
    )
    parser.add_argument("--threshold-evidence-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("research/water_pilot_study_results.json"))
    args = parser.parse_args()

    spec = next((item for item in catalog() if item.id == "water_pilot"), None)
    if spec is None or spec.acquisition != "allowed" or spec.rights_review != "reviewed_license":
        raise ValueError("water-pilot source is not cleared for this analysis boundary")
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    if (
        ledger.get("dataset_id") != spec.id
        or ledger.get("license_id") != spec.license_id
        or not ledger.get("complete")
    ):
        raise ValueError("the completed acquisition ledger does not match the reviewed dataset catalog")
    by_file = {row["filename"]: row for row in ledger["files"]}
    asset = next(
        (item for item in spec.assets if item.filename == "input_model_potenc_predXfault7_A.csv"), None
    )
    readme = next((item for item in spec.assets if item.filename == "README.txt"), None)
    if asset is None or readme is None or not {asset.filename, readme.filename} <= set(by_file):
        raise ValueError("both selected publisher assets must be present in the acquisition ledger")
    if any(not by_file[name].get("expected_checksum_verified") for name in (asset.filename, readme.filename)):
        raise ValueError("publisher checksums must be verified before source analysis")

    thresholds = {}
    for expression in args.threshold:
        key, separator, raw_value = expression.partition("=")
        if not separator or not key.strip() or key.strip() in thresholds:
            raise ValueError("each threshold must be a unique score_column=value pair")
        thresholds[key.strip()] = float(raw_value)
    source_evidence_id = "water_pilot_source"
    metadata = DerivedScoreMetadata(
        dataset_id=spec.id,
        asset_id="anonymized_single_wdn_zone",
        dataset_version=spec.version,
        source_uri=spec.source,
        citation=spec.citation,
        license_id=spec.license_id,
        source_file_name=asset.filename,
        expected_sha256=by_file[asset.filename]["sha256"],
        source_evidence_id=source_evidence_id,
        clock_id="Europe/Bratislava_local_civil_time",
        timezone_id="Europe/Bratislava",
        timestamp_column="timestamp",
        timestamp_format="%d-%m-%Y %H:%M",
        label_column="fault_d7",
        label_definition=(
            "publisher label: recorded leak within a plus/minus seven-day window; not event time"
        ),
    )
    study = read_derived_score_csv(args.raw_root / spec.id / asset.filename, metadata)
    context = RunContext(
        asset_id=metadata.asset_id,
        scenario_id="published_fault_d7_score_description",
        clock_id=metadata.clock_id,
        # RunContext.origin captures the observed operational source; this field preserves
        # that the published values are already-derived scores, not raw sensor observations.
        origin="measured",
        evidence=(
            EvidenceRef(
                id=source_evidence_id,
                citation=spec.citation,
                origin="literature",
                locator=spec.source,
                sha256=study.source_sha256,
                license_id=spec.license_id,
                limitations=spec.limitations,
            ),
            EvidenceRef(
                id=args.threshold_evidence_id,
                citation="Caller-declared fixed thresholds selected before this descriptive analysis",
                origin="user_declared",
            ),
        ),
        metadata={
            "value_representation": "publisher_derived_anomaly_scores_not_physical_measurements",
            "observation_origin": "publisher_derived_values",
            "local_timezone": metadata.timezone_id,
            "label_definition": metadata.label_definition,
        },
    )
    result = evaluate_score_thresholds(
        study,
        {
            "thresholds": thresholds,
            "direction": args.direction,
            "selection_evidence_id": args.threshold_evidence_id,
        },
        context,
    )
    labels = {"positive": 0, "negative": 0, "missing": 0}
    missing_by_channel = dict.fromkeys(study.score_columns, 0)
    for row in study.rows:
        label = "missing" if row.positive_label is None else "positive" if row.positive_label else "negative"
        labels[label] += 1
        for column, value in row.scores.items():
            missing_by_channel[column] += value is None
    body = {
        "schema": "pipeline_integrity.water_pilot_descriptive_application.v1",
        "dataset_id": spec.id,
        "dataset_version": spec.version,
        "license_id": spec.license_id,
        "source_sha256": study.source_sha256,
        "source_bytes": study.source_bytes,
        "readme_sha256": by_file[readme.filename]["sha256"],
        "source_rows": len(study.rows),
        "score_columns": study.score_columns,
        "score_range": [metadata.score_minimum, metadata.score_maximum],
        "local_timezone": metadata.timezone_id,
        "local_timestamp_duplicates": study.duplicate_local_timestamp_count,
        "label_definition": metadata.label_definition,
        "label_counts": labels,
        "missing_scores_by_channel": missing_by_channel,
        "threshold_result": result.model_dump(mode="json"),
        "row_level_values_included": False,
        "independent_holdout_claim": False,
        "field_validation_claim": False,
        "causal_or_event_time_claim": False,
    }
    record = {**body, "record_digest": digest(body)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "rows": len(study.rows), "score_columns": len(study.score_columns)}
        )
    )


if __name__ == "__main__":
    main()
