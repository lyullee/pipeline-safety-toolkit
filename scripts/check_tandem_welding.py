"""Full source-quality review and explicit small-window interchange; no complete weld heat is inferred."""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.welding_data import read_tandem_recording
from pipeline_integrity.welding_exchange import WeldWaveformSpec, evaluate_weld_waveform


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/tandem_gmaw"))
    args = parser.parse_args()
    output = Path("artifacts/tandem_welding")
    output.mkdir(parents=True, exist_ok=True)
    source = next(s for s in catalog() if s.id == "tandem_gmaw")
    csv_path = args.root / "experiment_list.csv"
    asset = next(a for a in source.assets if a.filename == csv_path.name)
    algorithm, checksum = asset.checksum.split(":", 1)
    assert file_digest(csv_path, algorithm) == checksum
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["wid"] == "No Unit"
    selected = [r for r in rows[1:] if r["wid"] == "631" and r["layer"] in ("1", "2")]
    assert len(selected) == 2 and {r["config"] for r in selected} == {"single", "tandem"}
    summaries = {}
    diagnostics = []
    errors = []
    offsets = {}
    for layer in (1, 2):
        archive = args.root / f"WID631_L{layer}.zip"
        process = read_tandem_recording(archive, "process")
        robot = read_tandem_recording(archive, "robot")
        ps, rs = process.summary(), robot.summary()
        summaries[f"L{layer}_process"] = ps
        summaries[f"L{layer}_robot"] = rs
        origin = rs["time_origin_utc"]
        delta = datetime.fromisoformat(ps["time_origin_utc"]) - datetime.fromisoformat(origin)
        offset_ns = (delta.days * 86400 + delta.seconds) * 1000000000 + delta.microseconds * 1000
        offsets[f"L{layer}"] = offset_ns
        # This is a declared 0.1-second source-relative diagnostic window, not a whole pass.
        for side in ("leading", "trailing"):
            u, i = f"voltage_{side}", f"current_{side}"
            frames, selection = process.frames(
                start_s=5.0, finish_s=5.1, channels=(u, i), common_origin_utc=origin
            )
            assert selection["source_origin_offset_ns"] == offset_ns
            assert not selection["full_source_span_selected"] and not selection["resampled"]
            robot_frames, robot_selection = robot.frames(
                start_s=offset_ns / 1e9 + 5.0,
                finish_s=offset_ns / 1e9 + 5.1,
                channels=("thermo_1",),
                common_origin_utc=origin,
            )
            assert robot_selection["clock_id"] == selection["clock_id"]
            assert len(robot_frames) != len(frames)
            context = RunContext(
                asset_id=f"WID631_L{layer}",
                scenario_id=f"declared_recorded_product_{side}",
                clock_id=frames[0].clock_id,
                origin="synthetic",
                evidence=(
                    EvidenceRef(
                        id="source",
                        citation=source.citation,
                        origin="laboratory",
                        license_id=source.license_id,
                        sha256=ps["member_sha256"],
                    ),
                    EvidenceRef(
                        id="review",
                        citation="Declared diagnostic assumption: shared recorded process coordinate and unmodified sensor polarity; hardware synchronization, calorimetric efficiency and weld-path distance unqualified",
                        origin="synthetic",
                        license_id="MIT",
                    ),
                ),
            )
            spec = WeldWaveformSpec(
                asset_id=context.asset_id,
                location_id=side,
                clock_id=context.clock_id,
                material_condition_id="source_nominal_S355J2N_ER70S6_composition_unresolved",
                voltage_channel=u,
                current_channel=i,
                maximum_sample_interval_s=0.00015,
                travel_distance_m=None,
                efficiency=None,
                electrical_synchronization_and_polarity_reviewed=True,
                distance_efficiency_and_window_reviewed=False,
                electrical_evidence_id="source",
                evidence_ids=("source", "review"),
            )
            record = evaluate_weld_waveform(frames, spec, context)
            assert record["status"] in ("needs_data", "out_of_scope") and record["native_result"] is None
            power = np.asarray(
                [f.values[u] * f.values[i] if f.valid[u] and f.valid[i] else np.nan for f in frames]
            )
            times = np.asarray([f.time_s for f in frames])
            dt = np.diff(times)
            known = np.isfinite(power[:-1]) & np.isfinite(power[1:]) & (dt <= 0.00015 + 1e-9)
            expected = float(np.sum((power[:-1][known] + power[1:][known]) * dt[known] / 2))
            values = record["electrical_diagnostic"]["values"]
            actual = values["import_energy_j"] - values["export_energy_j"]
            error = abs(actual - expected)
            assert np.isclose(actual, expected, rtol=1e-11, atol=1e-10)
            errors.append(error)
            diagnostics.append(
                dict(
                    layer=layer,
                    side=side,
                    requested_source_window_s=[5.0, 5.1],
                    selected_samples=len(frames),
                    electrical_status=record["electrical_diagnostic"]["status"],
                    heat_input_status=record["status"],
                    native_heat_input_inferred=False,
                    selection_digest=selection["selection_digest"],
                    result_digest=record["record_digest"],
                    comparison_absolute_error_J=error,
                )
            )
            (output / f"L{layer}_{side}.json").write_text(
                json.dumps(
                    dict(result=record, selection=selection, robot_selection=robot_selection), indent=2
                )
                + "\n",
                encoding="utf-8",
            )
    assert sum(s["source_samples"] for k, s in summaries.items() if k.endswith("process")) == 696814
    assert sum(s["source_samples"] for k, s in summaries.items() if k.endswith("robot")) == 280590
    result = dict(
        schema="pipeline_integrity.tandem_welding_verification.v1",
        dataset_id=source.id,
        source=source.source,
        citation=source.citation,
        license_id=source.license_id,
        version=source.version,
        selected_workpieces=1,
        selected_layers=2,
        source_records=summaries,
        experiment_metadata_sha256=file_digest(csv_path),
        nominal_grade_not_expanded_to_chemistry=True,
        inactive_root_trailing_channel_not_a_second_arc=True,
        explicit_window_diagnostics=diagnostics,
        maximum_independent_signed_integral_error_J=max(errors),
        source_origin_offsets_ns=offsets,
        recorded_UTC_coordinate_alignment_only=True,
        hardware_clock_sync_or_drift_validated=False,
        source_nan_or_negative_power_repaired=False,
        full_pass_heat_input_or_distance_inferred=False,
        raw_or_transformed_sample_rows_in_code_release=False,
        procedure_or_field_qualification=False,
        reader_sha256=file_digest(Path("src/pipeline_integrity/welding_data.py")),
        implementation_sha256=file_digest(Path("src/pipeline_integrity/welding_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
    )
    Path("research/tandem_welding_results.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                source_records=4,
                source_process_rows=696814,
                source_robot_rows=280590,
                declared_diagnostic_windows=4,
                whole_pass_heat_inferred=False,
                maximum_integral_error_J=max(errors),
            )
        )
    )


if __name__ == "__main__":
    main()
