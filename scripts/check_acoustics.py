"""Apply declared delay methods to simultaneous laboratory records; export aggregate evidence only."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.acoustic_data import SheffieldArchive
from pipeline_integrity.acoustics import DelayRequest, delay_feature_frames, estimate_delay, slice_trace
from pipeline_integrity.adapters import monitoring_batch
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.datasets import catalog, file_digest


def finite_summary(values):
    values = [v for v in values if v is not None and np.isfinite(v)]
    return (
        None
        if not values
        else {
            "minimum": float(min(values)),
            "median": float(np.median(values)),
            "maximum": float(max(values)),
        }
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive", type=Path, default=Path("data/raw/sheffield_acoustic/sheffield-acoustic-v1.zip")
    )
    parser.add_argument("--monitoring", type=Path)
    args = parser.parse_args()
    folder = Path("artifacts/acoustics")
    folder.mkdir(parents=True, exist_ok=True)
    specs = {
        method: DelayRequest(method=method, maximum_delay_s=0.1, band_hz=(100.0, 2000.0), polarity="absolute")
        for method in ("normalized_xcorr", "gcc_phat")
    }
    results = {method: [] for method in specs}
    records = []
    with SheffieldArchive(args.archive) as reader:
        for i, member in enumerate(reader.members()):
            left, right, report = reader.read_pair(member)
            ctx = RunContext(
                asset_id=left.asset_id,
                scenario_id="predeclared_four_second_band",
                clock_id=left.clock_id,
                origin="laboratory",
            )
            row = {"reader": report, "results": {}}
            for method, spec in specs.items():
                r = estimate_delay(left, right, spec, ctx)
                results[method].append(r)
                row["results"][method] = r
            records.append(row)
            if (i + 1) % 10 == 0:
                print(json.dumps({"paired_records": i + 1}), flush=True)
        # Window features remain local; every window keeps the same acquisition clock.
        left, right, report = reader.read_pair(reader.members()[0], sample_count=245760)
        windows = []
        ctx = RunContext(
            asset_id=left.asset_id,
            scenario_id="same_record_nonoverlapping_windows",
            clock_id=left.clock_id,
            origin="laboratory",
        )
        for start in range(0, len(left.values) - 32768 + 1, 32768):
            windows.append(
                estimate_delay(
                    slice_trace(left, start, 32768),
                    slice_trace(right, start, 32768),
                    specs["normalized_xcorr"],
                    ctx,
                )
            )
        archive_sha = reader.sha256
    (folder / "paired_records.json").write_text(
        json.dumps(records, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (folder / "window_results.json").write_text(
        json.dumps(windows, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    handoff = None
    if args.monitoring is not None:
        sys.path.insert(0, str((args.monitoring / "src").resolve()))
        batch = monitoring_batch(
            delay_feature_frames(windows, stream_id="paired_acoustic_delay_windows"),
            metadata={
                "asset_id": ctx.asset_id,
                "source_result_digests": [r["result_digest"] for r in windows],
                "input_origin": "laboratory",
                "features_are_leak_probabilities": False,
            },
        )
        assert batch.values.shape == (len(windows), 4)
        handoff = {
            "owner_source": revision(args.monitoring),
            "native_type": f"{type(batch).__module__}.{type(batch).__qualname__}",
            "shape": list(batch.values.shape),
            "units": list(batch.units),
            "origin": batch.origin,
            "valid_values": int(batch.valid.sum()),
            "total_values": int(batch.valid.size),
            "metadata": batch.metadata,
            "leak_detection_accuracy_or_HEP_computed": False,
        }
    summaries = {}
    for method, rows in results.items():
        qualified = [r for r in rows if r["status"] == "estimated"]
        summaries[method] = {
            "states": dict(Counter(r["status"] for r in rows)),
            "withheld_issues": dict(Counter(issue for r in rows for issue in r["issues"])),
            "qualified_apparent_delay_s": finite_summary([r["apparent_delay_s"] for r in qualified]),
            "qualified_absolute_peak_correlation": finite_summary(
                [abs(r["peak_correlation_signed"]) for r in qualified]
            ),
            "mean_coherence_in_band": finite_summary(
                [r.get("coherence", {}).get("mean_in_band") for r in rows]
            ),
        }
    dataset = next(d for d in catalog() if d.id == "sheffield_acoustic")
    record = {
        "schema": "pipeline_integrity.acoustic_application.v1",
        "source_citation": dataset.citation,
        "license_id": dataset.license_id,
        "archive_sha256": archive_sha,
        "data_origin": "laboratory",
        "paired_records_examined": len(records),
        "samples_per_initial_record": 32768,
        "sample_rate_hz": 8192.0,
        "analysis_requests": {k: v.model_dump(mode="json") for k, v in specs.items()},
        "methods": summaries,
        "same_record_window_handoff": handoff,
        "same_record_window_count": len(windows),
        "single_record_unused_tail_samples": len(left.values) - len(windows) * 32768,
        "combined_spatial_tables_used": False,
        "native_acceleration_rescaled": False,
        "physical_position_calculated": False,
        "location_missing": [
            "propagation_speed_and_error",
            "reference_sensor_offset",
            "relative_latency_error_bound",
            "single_path_source_bracketing",
        ],
        "field_validation_claim": False,
        "leak_vs_no_leak_detection_evaluated": False,
        "raw_or_transformed_external_sample_rows_in_public_summary": False,
        "implementation_files_sha256": {
            name: file_digest(Path("src/pipeline_integrity") / name)
            for name in ("acoustics.py", "acoustic_data.py")
        },
    }
    Path("research/acoustic_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "paired_records": len(records),
                "states": {k: v["states"] for k, v in summaries.items()},
                "public_summary": "research/acoustic_results.json",
            }
        )
    )


if __name__ == "__main__":
    main()
