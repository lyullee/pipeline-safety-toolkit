"""Check actual optional project classes, recording revisions without publishing checkout paths."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from pipeline_integrity.adapters import MaterialClient, hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.loaders import iter_battledim_frames


def revision(path):
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, capture_output=True)
    dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], text=True, capture_output=True)
    fingerprint = hashlib.sha256()
    sources = sorted((path / "src").rglob("*.py"))
    if not sources:
        raise ValueError("the selected application source root contains no Python sources")
    for source in sources:
        fingerprint.update(source.relative_to(path).as_posix().encode("utf-8"))
        fingerprint.update(source.read_bytes())
    return {
        "commit": result.stdout.strip() if result.returncode == 0 else None,
        "working_tree_modified": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        "source_tree_sha256": fingerprint.hexdigest(),
        "source_tree_files": len(sources),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("research/integration_results.json"))
    args = parser.parse_args()
    for folder in (args.materials, args.monitoring, args.hra):
        sys.path.insert(0, str((folder / "src").resolve()))
    import human_reliability
    import matintegrity
    import process_monitoring
    from process_monitoring import Monitor, create_detector

    frames = tuple(iter_battledim_frames(args.raw_root / "battledim", 2018, maximum_rows=864))
    batch = monitoring_batch(frames)
    monitor = Monitor(create_detector("robust_z"))
    monitor.fit(batch.subset(np.arange(288)))
    monitor.calibrate(batch.subset(np.arange(288, 576)), false_alarm_fraction=0.01)
    detection = monitor.score(batch.subset(np.arange(576, 864)))
    reference = batch.values[:288]
    center = np.median(reference, axis=0)
    zero_mad_channels = [
        batch.channels[index]
        for index, value in enumerate(np.median(np.abs(reference - center), axis=0))
        if value == 0.0
    ]
    series = hra_signal_series(frames, "pressure_head:n1")
    context = RunContext(
        asset_id="example_pipe",
        scenario_id="declared_parameter_demo",
        clock_id="inspection_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="declared_example",
                citation="Caller-declared demonstration parameters",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    client = MaterialClient()
    material = client.evaluate(
        "corrosion.uniform_forecast",
        {
            "current_thickness_m": 0.01,
            "required_thickness_m": 0.006,
            "rate_m_s": 1e-11,
            "horizon_s": 365.25 * 86400,
        },
        context,
        location_id="example_segment",
    )
    thermal = client.evaluate(
        "thermal.cylindrical_layers",
        {
            "radii_m": [0.05, 0.055, 0.10],
            "conductivity_W_m_K": [45.0, 0.04],
            "length_m": 1.0,
            "inner_fluid_temperature_K": 350.0,
            "ambient_temperature_K": 293.15,
            "radiative_environment_temperature_K": 293.15,
            "inner_convection_W_m2_K": 1000.0,
            "outer_convection_W_m2_K": 8.0,
            "outer_emissivity": 0.8,
        },
        context,
        location_id="example_segment",
    )
    assert material.status == thermal.status == "computed"
    assert series.clock_id == frames[0].clock_id and series.unit == "m"
    assert detection.valid.all()
    result = {
        "schema": "pipeline_integrity.integration_checks.v1",
        "materials": {
            **revision(args.materials),
            "version": matintegrity.__version__,
            "model_results": [material.model_dump(mode="json"), thermal.model_dump(mode="json")],
        },
        "monitoring": {
            **revision(args.monitoring),
            "version": process_monitoring.__version__,
            "detector": "robust_z",
            "origin": batch.origin,
            "ordered_temporal_partitions": {"fit_rows": 288, "calibration_rows": 288, "evaluation_rows": 288},
            "valid_evaluation_rows": int(detection.valid.sum()),
            "alarm_count": int(detection.alarms.sum()),
            "threshold": float(detection.threshold),
            "raw_training_zero_mad_channels": zero_mad_channels,
            "scale_policy_requires_review": bool(zero_mad_channels),
            "leak_detection_accuracy_established": False,
            "note": "Three early days exercise the interface; this is not a labeled detector benchmark.",
        },
        "hra": {
            **revision(args.hra),
            "version": human_reliability.__version__,
            "actual_signal_series_rows": len(series.time_s),
            "clock_id": series.clock_id,
            "unit": series.unit,
            "origin": series.origin,
            "hep_inferred_from_pressure": False,
        },
        "other_repositories_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "materials": [material.status, thermal.status],
                "monitoring_valid_rows": int(detection.valid.sum()),
                "hra_rows": len(series.time_s),
            }
        )
    )


if __name__ == "__main__":
    main()
