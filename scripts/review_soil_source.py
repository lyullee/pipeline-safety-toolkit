"""Apply pinned soil source readers; publish aggregate facts without observation rows."""

import argparse
import copy
import json
import re
from importlib.metadata import version
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import DatasetSpec, catalog, file_digest
from pipeline_integrity.soil_data import (
    BOREHOLE_ID,
    COVER_FILES,
    COVERS_ID,
    borehole_temperature_frames,
    read_borehole_temperature,
    read_soil_cover_workbook,
    verify_soil_source_record,
)


def save(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def compact(value):
    if isinstance(value, dict):
        return {
            k: compact(v)
            for k, v in value.items()
            if k not in {"source_row_numbers", "undated_nonempty_source_rows"}
        }
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def context_for(record, depth_index=0):
    asset = "publisher-borehole-processed-observation"
    clock = "publisher-borehole-processing-seconds"
    return RunContext(
        asset_id=asset,
        scenario_id="source-replay-no-pipe-model",
        clock_id=clock,
        origin="measured",
        evidence=(
            EvidenceRef(
                id="source",
                citation=record["provenance"]["citation"],
                origin="measured",
                sha256=record["provenance"]["sha256"],
                license_id="CC-BY-4.0",
                limitations=(
                    "Publisher estimation input; full original raw trace not acquired",
                    "No radial pipe or absolute calendar-time mapping",
                ),
            ),
        ),
        metadata={
            "soil_observation_binding": dict(
                asset_id=asset,
                clock_id=clock,
                record_digest=record["record_digest"],
                source_clock_kind=record["source_clock_kind"],
                coordinate_kind=record["coordinate_kind"],
                depth_index=depth_index,
                depth_m=record["source_arrays"]["depth"][depth_index],
                clock_offset_s=0.0,
                quantity_meaning="publisher_processed_point_temperature_not_pipe_or_radial_boundary",
            )
        },
    )


def review(raw_root, specs, artifacts):
    covers = {}
    assets = []
    checks = {}
    before = {}
    for name in COVER_FILES:
        path = raw_root / COVERS_ID / name
        before[f"{COVERS_ID}/{name}"] = file_digest(path)
        record = read_soil_cover_workbook(path, source_spec=specs[COVERS_ID])
        checks[f"{name}:receipt"] = verify_soil_source_record(record)
        save(artifacts / (path.stem.replace(" ", "_") + "_record.json"), record)
        covers[name] = {
            "origin": record["origin"],
            "record_digest": record["record_digest"],
            "worksheet_extents": {k: [s["rows"], s["columns"]] for k, s in record["worksheets"].items()},
            "diagnostics": compact(record["diagnostics"]),
            "applicability": record["applicability"],
        }
        assets.append(record["provenance"])
    folder = raw_root / BOREHOLE_ID
    for name in ("estimation_data.mat", "data_process.mlx", "estimation_process.mlx"):
        before[f"{BOREHOLE_ID}/{name}"] = file_digest(folder / name)
    borehole = read_borehole_temperature(folder, source_spec=specs[BOREHOLE_ID])
    checks["borehole:receipt"] = verify_soil_source_record(borehole)
    save(artifacts / "borehole_record.json", borehole)
    assets.extend([borehole["provenance"], *borehole["method_assets"]])
    context = context_for(borehole)
    frames = borehole_temperature_frames(borehole, context, depth_index=0, evidence_id="source")
    save(artifacts / "source_observation_context.json", context.model_dump(mode="json"))
    save(artifacts / "selected_depth_frames.json", [f.model_dump(mode="json") for f in frames])
    obs = borehole["observations"]
    checks.update(
        source_assets_7=len(assets) == 7,
        source_bytes_7822891=sum(a["bytes"] for a in assets) == 7_822_891,
        all_supplier_md5=all(
            a["supplier_checksum_verified"] and a["expected_checksum"].startswith("md5:") for a in assets
        ),
        hdf5_orientation=borehole["storage"]["temperature"]["hdf5_shape"] == [1054, 315],
        temperature_count=obs["temperatures"] == obs["finite_temperatures"] == 332010,
        actual_clock_origin_not_reset=frames[0].time_s == 5.0 and frames[-1].time_s == 129481.0,
        actual_nonuniform_clock=obs["time_clock"]["adjacent_interval_counts"]
        == {"121": 27, "122": 1013, "123": 11, "427": 1, "843": 1},
        source_kelvin_conversion=frames[0].values["temperature_K"]
        == borehole["source_arrays"]["temperature"][0][0] + 273.15,
        vertical_depth_not_radial=borehole["source_arrays"]["depth"][-1] == 79.818
        and borehole["coordinate_kind"] == "vertical_borehole_depth_not_radial_radius",
        no_calendar_invention=borehole["source_calendar_origin"] is None
        and all(f.timestamp_utc is None for f in frames),
        no_pipe_validation=borehole["applicability"]["transient_pipeline_validation"] == "needs_data",
        no_property_imputation=borehole["applicability"]["conductivity_W_m_K"] is None
        and borehole["applicability"]["volumetric_heat_capacity_J_m3_K"] is None,
    )
    t = covers["Soil temperature.xlsx"]["diagnostics"]
    checks.update(
        daily_rows=t["B"]["daily_rows"] == 274
        and t["T"]["daily_rows"] == 167
        and t["S"]["daily_rows"] == 274,
        t_header_alignment_mismatch=t["T"]["inconsistent_header_aligned_ranges"] == 557
        and t["T"]["complete_header_aligned_range_comparisons"] == 564,
        no_daily_extrema_to_instantaneous=all(
            not d["header_or_value_positions_repaired"]
            and d["source_temperature_unit"] is None
            and d["level_depths_m"] is None
            for d in t.values()
        ),
    )
    refusals = {}
    for label in ("units", "clock", "geometry", "calibration"):
        changed = copy.deepcopy(borehole)
        if label == "units":
            changed["source_units"]["temperature"] = "K"
        elif label == "clock":
            changed["observations"]["time_clock"]["first"] = 0.0
        elif label == "geometry":
            changed["coordinate_kind"] = "radial_radius"
        else:
            changed["applicability"]["transient_pipeline_validation"] = "computed"
        changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
        try:
            verify_soil_source_record(changed)
        except ValueError:
            refusals[label] = True
        else:
            refusals[label] = False
    changed_context = context.model_copy(update={"clock_id": "different-clock"})
    try:
        borehole_temperature_frames(borehole, changed_context, depth_index=0, evidence_id="source")
    except ValueError:
        refusals["changed_context_clock"] = True
    else:
        refusals["changed_context_clock"] = False
    checks["source_assets_unchanged"] = all(
        file_digest(raw_root / key) == value for key, value in before.items()
    )
    assert all(checks.values()), checks
    assert all(refusals.values()), refusals
    return dict(
        schema_version="pipeline_integrity.soil_source_review.v1",
        status="measured_source_readers_verified_model_mapping_needs_data",
        acquired_groups=2,
        acquired_files=len(assets),
        acquired_bytes=sum(a["bytes"] for a in assets),
        assets=assets,
        cover_workbooks=covers,
        borehole=dict(
            origin=borehole["origin"],
            record_digest=borehole["record_digest"],
            storage=borehole["storage"],
            observations=borehole["observations"],
            source_units=borehole["source_units"],
            coordinate_kind=borehole["coordinate_kind"],
            source_clock_kind=borehole["source_clock_kind"],
            source_calendar_origin=None,
            source_timezone=None,
            processing=borehole["processing"],
            applicability=borehole["applicability"],
            selected_depth_observation_frames=len(frames),
            selected_depth_m=borehole["source_arrays"]["depth"][0],
            first_frame_time_s=frames[0].time_s,
            last_frame_time_s=frames[-1].time_s,
            frame_origin="measured",
        ),
        checks=checks,
        source_contract_checks=sum(checks.values()),
        independent_physical_model_validation_cases=0,
        actual_source_receipt_corruption_refusals=refusals,
        model_or_inverse_calls=0,
        rows_and_detailed_receipts_in_release=False,
        unacquired_full_borehole_asset=dict(
            filename="insitu_measurements.mat",
            bytes=329832492,
            expected_checksum="md5:02e88e184c13af98b165d4bd0f7d6b60",
            default=False,
            manual_download_requested=False,
        ),
        runtime={name: version(name) for name in ("openpyxl", "h5py", "numpy")},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/soil_data"))
    parser.add_argument("--output", type=Path, default=Path("research/soil_source_results.json"))
    args = parser.parse_args()
    specs = (
        catalog()
        if args.catalog_file is None
        else tuple(
            DatasetSpec.model_validate_json(json.dumps(row))
            for row in json.loads(args.catalog_file.read_text(encoding="utf-8"))["datasets"]
        )
    )
    selected = {s.id: s for s in specs if s.id in {COVERS_ID, BOREHOLE_ID}}
    if set(selected) != {COVERS_ID, BOREHOLE_ID}:
        raise ValueError("the two reviewed soil source specs must be present")
    result = review(args.raw_root, selected, args.artifacts)
    root = Path(__file__).resolve().parents[1]
    guide = root / "docs/SOIL_DATA_KO.md"
    blocks = re.findall(r"```python\s*\n(.*?)\n```", guide.read_text(encoding="utf-8"), re.S)
    for block in blocks:
        exec(
            compile(block, "SOIL_DATA_KO.md", "exec"),
            {"SOIL_RAW_ROOT": args.raw_root, "SOIL_SOURCE_SPECS": selected},
        )
    result["doc_python_blocks_executed"] = len(blocks)
    paths = [
        root / name
        for name in (
            "src/pipeline_integrity/soil_data.py",
            "tests/test_soil_data.py",
            "scripts/review_soil_source.py",
            "docs/SOIL_DATA_KO.md",
        )
    ]
    result["project_file_sha256"] = {
        str(path.relative_to(root)).replace("\\", "/"): file_digest(path) for path in paths
    }
    save(args.output, result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "acquired_groups",
                    "acquired_files",
                    "acquired_bytes",
                    "source_contract_checks",
                    "doc_python_blocks_executed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
