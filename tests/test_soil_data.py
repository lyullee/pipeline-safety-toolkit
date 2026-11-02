"""Authored fixtures check source shape, clocks, missingness and explicit observation binding."""

import hashlib
import io
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta

import h5py
import numpy as np
import pytest
from openpyxl import Workbook

from pipeline_integrity import soil_data as data
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import DatasetSpec


def borehole_bytes(*, times=(5.0, 127.0, 970.0), depth=(0.0, 0.25), values=None, change=None):
    values = [[20.0, 21.0], [22.0, np.nan], [23.0, 24.0]] if values is None else values
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as book:
        for name, array in {
            "time_seconds": np.array(times).reshape(-1, 1),
            "depth": np.array(depth).reshape(1, -1),
            "temperature": np.array(values),
        }.items():
            book.create_dataset(name, data=array).attrs["MATLAB_class"] = np.bytes_("double")
        if change:
            change(book)
    return buffer.getvalue()


def temperature_bytes(*, shift=False, bad_header=False):
    book = Workbook()
    book.remove(book.active)
    for name, day_col, start, max_col, title_row, label_row in (
        ("B", 2, 4, 3, 2, 3),
        ("T", 1, 6, 2, 4, 5),
        ("S", 1, 5, 2, 3, 4),
    ):
        sheet = book.create_sheet(name)
        for col, title in (
            (max_col, "Daily maximum temperature"),
            (max_col + 6, "Daily minmum temperature"),
            (max_col + 12, "Diurnal temperature ranges"),
        ):
            sheet.cell(title_row, col, title)
            for i in range(5):
                sheet.cell(label_row, col + i, f"L{i + 1}")
        for offset in range(2):
            row = start + offset
            sheet.cell(row, day_col, datetime(2021, 11, 19) + timedelta(days=offset))
            for i in range(5):
                sheet.cell(row, max_col + i, 20.0 + i)
                sheet.cell(row, max_col + 6 + i, 10.0 + i)
                sheet.cell(row, max_col + 12 + i, 10.0)
        if shift and name == "T":
            sheet.cell(start, max_col + 12, 7.0)
        if bad_header and name == "B":
            sheet.cell(title_row, max_col, "hourly temperature")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def sensor_bytes(*, filename="Water content.xlsx"):
    book = Workbook()
    sheet = book.active
    sheet.title = "含水率全部" if filename.startswith("Water") else "吸力"
    sheet.append([None, None, "S-5cm", "T-5cm", "B-5cm", "S-15cm", "T-15cm", "B-15cm"])
    sheet.append([None, datetime(2021, 11, 19), 0.0, None, -1.0, 0.1, 0.2, 0.3])
    sheet.append([None, datetime(2021, 11, 19), 0.0, "-", -2.0, 0.1, 0.2, 0.3])
    sheet.append([None, None, 9.0, None, None, None, None, None])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def source_spec(identity, assets):
    return DatasetSpec.model_validate_json(
        json.dumps(
            dict(
                id=identity,
                title="Authored supplier fixture",
                source="https://example.org/fictional",
                citation="Original independent source-reader fixture; not observed field data",
                creators=["Test author"],
                version="fixture-1",
                origin="laboratory_experiment",
                license_id="CC-BY-4.0",
                rights_evidence_url="https://example.org/fictional-rights",
                rights_review="reviewed_license",
                acquisition="allowed",
                checked_on="2026-10-27",
                intended_uses=["Test local source-byte binding"],
                limitations=["Fictional supplier records only"],
                split_unit="authored fixture",
                assets=[
                    dict(
                        filename=name,
                        url=f"https://example.org/{name}",
                        bytes=len(raw),
                        checksum="sha256:" + hashlib.sha256(raw).hexdigest(),
                    )
                    for name, raw in assets.items()
                ],
            )
        )
    )


def pinned_borehole(tmp_path, **kwargs):
    assets = {
        "estimation_data.mat": borehole_bytes(**kwargs),
        "data_process.mlx": b"authored method fixture",
        "estimation_process.mlx": b"second authored method fixture",
    }
    for name, raw in assets.items():
        (tmp_path / name).write_bytes(raw)
    spec = source_spec(data.BOREHOLE_ID, assets)
    return data.read_borehole_temperature(tmp_path, source_spec=spec), spec


def observation_context(record, *, depth_index=0, offset=20.0):
    return RunContext(
        asset_id="authored_observed_borehole",
        scenario_id="source-clock-replay",
        clock_id="original-processing-seconds",
        origin="measured",
        evidence=(
            EvidenceRef(
                id="source",
                citation="Authored fixture provenance",
                origin="measured",
                sha256=record["provenance"]["sha256"],
                license_id="CC-BY-4.0",
            ),
        ),
        metadata={
            "soil_observation_binding": dict(
                asset_id="authored_observed_borehole",
                clock_id="original-processing-seconds",
                record_digest=record["record_digest"],
                source_clock_kind=record["source_clock_kind"],
                coordinate_kind=record["coordinate_kind"],
                depth_index=depth_index,
                depth_m=record["source_arrays"]["depth"][depth_index],
                clock_offset_s=offset,
                quantity_meaning="publisher_processed_point_temperature_not_pipe_or_radial_boundary",
            )
        },
    )


def reseal(record):
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    return record


def test_borehole_original_hdf5_orientation_and_nonuniform_clock():
    record = data.parse_borehole_temperature(borehole_bytes())
    assert record["storage"]["temperature"]["hdf5_shape"] == [3, 2]
    assert record["source_arrays"]["time_seconds"] == [5.0, 127.0, 970.0]
    assert record["observations"]["time_clock"]["adjacent_interval_counts"] == {"122": 1, "843": 1}
    assert record["source_arrays"]["temperature"][1][1] == {"source_type": "nonfinite", "text": "nan"}
    assert record["observations"]["missing_or_invalid_temperatures"] == 1
    assert record["source_calendar_origin"] is None and record["source_timezone"] is None
    assert record["applicability"]["radial_soil_pipe_model"] == "needs_data"
    assert record["applicability"]["conductivity_W_m_K"] is None
    assert data.verify_soil_source_record(record)
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize("times", [(5.0, 5.0, 2.0), (-5.0, 127.0, 970.0), (5.0, np.nan, 970.0)])
def test_bad_source_clocks_preserved_but_not_exported(tmp_path, times):
    record, _ = pinned_borehole(tmp_path, times=times)
    assert data.verify_soil_source_record(record)
    with pytest.raises(ValueError, match="clock"):
        data.borehole_temperature_frames(
            record, observation_context(record), depth_index=0, evidence_id="source", clock_offset_s=20.0
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda f: f.create_dataset("extra", data=[1.0]),
        lambda f: f["depth"].attrs.__setitem__("MATLAB_class", np.bytes_("char")),
        lambda f: f.__delitem__("time_seconds"),
    ],
)
def test_hdf5_unknown_variables_and_class_refused(mutation):
    with pytest.raises(ValueError):
        data.parse_borehole_temperature(borehole_bytes(change=mutation))


def test_hdf5_orientation_refused():
    with pytest.raises(ValueError, match="orientation"):
        data.parse_borehole_temperature(borehole_bytes(values=[[20.0, 21.0, 22.0], [23.0, 24.0, 25.0]]))


def test_hdf5_external_link_refused():
    def link(book):
        del book["depth"]
        book["depth"] = h5py.ExternalLink("unopened.h5", "/depth")

    with pytest.raises(ValueError, match="links"):
        data.parse_borehole_temperature(borehole_bytes(change=link))


def test_workbook_daily_statistics_keep_unknown_units_and_depths():
    record = data.parse_soil_cover_workbook(temperature_bytes(shift=True), filename="Soil temperature.xlsx")
    b, t = record["diagnostics"]["B"], record["diagnostics"]["T"]
    assert b["source_temperature_unit"] is None and b["level_depths_m"] is None
    assert b["inconsistent_header_aligned_ranges"] == 0 and t["inconsistent_header_aligned_ranges"] == 1
    assert t["header_or_value_positions_repaired"] is False
    assert t["statistic"].endswith("not_instantaneous")
    assert record["worksheets"]["T"]["source_cells"][5][13] == 7.0
    assert b["clock"]["utc_assigned"] is False and b["clock"]["timezone"] is None
    assert data.verify_soil_source_record(record)


def test_workbook_changed_statistic_requires_schema_review():
    with pytest.raises(ValueError, match="header"):
        data.parse_soil_cover_workbook(temperature_bytes(bad_header=True), filename="Soil temperature.xlsx")


@pytest.mark.parametrize("filename", ["Water content.xlsx", "Suction.xlsx"])
def test_sensor_signed_nulls_duplicates_depths_and_unknown_units(filename):
    record = data.parse_soil_cover_workbook(sensor_bytes(filename=filename), filename=filename)
    sheet = next(iter(record["worksheets"].values()))
    diagnostic = next(iter(record["diagnostics"].values()))
    assert sheet["source_cells"][1][2:5] == [0, None, -1]
    assert diagnostic["source_unit"] is None
    assert diagnostic["depth_m"] == [0.05] * 3 + [0.15] * 3
    assert diagnostic["clock"]["repeated_adjacent"] == 1
    assert diagnostic["clock"]["undated_nonempty_source_rows"] == [4]
    assert diagnostic["physical_sensor_ids"] is None
    assert data.verify_soil_source_record(record)


def test_workbook_formulas_remain_formulas_with_separate_missing_cache():
    book = Workbook()
    air = book.active
    air.title = "Air temperature"
    air.cell(2, 8, "header extent")
    air.cell(3, 4, datetime(2021, 11, 19))
    air.cell(3, 5, datetime(2021, 11, 19, 0, 14))
    rain = book.create_sheet("Rainfall")
    for col, label in enumerate(
        ("Total precipitaion (mm)", "Hourly precipitaion (mm)", "Daily precipitaion (mm)"), 5
    ):
        rain.cell(1, col, label)
    rain.cell(2, 4, datetime(2021, 11, 19))
    rain.cell(2, 6, 1.0)
    rain.cell(2, 7, "=SUM(F2:F2)")
    buffer = io.BytesIO()
    book.save(buffer)
    record = data.parse_soil_cover_workbook(buffer.getvalue(), filename="Climate data of experiment.xlsx")
    assert record["worksheets"]["Rainfall"]["source_cells"][1][6] == "=SUM(F2:F2)"
    assert record["worksheets"]["Rainfall"]["formula_cache"] == {"G2": None}
    assert record["diagnostics"]["Air temperature"]["source_clock_column"] == 4
    assert record["diagnostics"]["Air temperature"]["second_source_clock_column"] == 5


def test_pinned_source_bytes_rights_and_method_assets(tmp_path):
    record, spec = pinned_borehole(tmp_path)
    assert len(record["method_assets"]) == 2 and data.verify_soil_source_record(record)
    (tmp_path / "data_process.mlx").write_bytes(b"changed source method")
    with pytest.raises(ValueError, match="size|checksum"):
        data.read_borehole_temperature(tmp_path, source_spec=spec)


@pytest.mark.parametrize(
    "change", [{"rights_review": "unresolved"}, {"license_id": "unknown"}, {"acquisition": "metadata_only"}]
)
def test_unreviewed_rights_refused_before_reader(tmp_path, change):
    _, spec = pinned_borehole(tmp_path)
    with pytest.raises(ValueError, match="rights"):
        data.read_borehole_temperature(tmp_path, source_spec=spec.model_copy(update=change))


def test_source_checksum_catches_same_size_change(tmp_path):
    _, spec = pinned_borehole(tmp_path)
    target = tmp_path / "estimation_data.mat"
    raw = target.read_bytes()
    target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError, match="checksum"):
        data.read_borehole_temperature(tmp_path, source_spec=spec)


def test_frames_preserve_original_nonzero_clock_offset_and_missingness(tmp_path):
    record, _ = pinned_borehole(tmp_path)
    context = observation_context(record, depth_index=1)
    frames = data.borehole_temperature_frames(
        record, context, depth_index=1, evidence_id="source", clock_offset_s=20.0
    )
    assert [f.time_s for f in frames] == [25.0, 147.0, 990.0]
    assert frames[0].values["temperature_K"] == 294.15
    assert frames[1].values["temperature_K"] is None and frames[1].valid["temperature_K"] is False
    assert all(f.origin == "measured" and f.timestamp_utc is None for f in frames)


@pytest.mark.parametrize(
    "fault", ["clock", "clock_name", "asset_name", "evidence", "origin", "coordinate", "offset", "record"]
)
def test_frames_refuse_changed_context_binding(tmp_path, fault):
    record, _ = pinned_borehole(tmp_path)
    context = observation_context(record)
    values = context.model_dump()
    if fault == "clock":
        values["metadata"] = {}
    elif fault == "clock_name":
        values["clock_id"] = "another-processing-clock"
    elif fault == "asset_name":
        values["asset_id"] = "another-borehole"
    elif fault == "evidence":
        values["evidence"][0]["sha256"] = "a" * 64
    elif fault == "origin":
        values["origin"] = "simulation"
    elif fault == "coordinate":
        values["metadata"]["soil_observation_binding"]["coordinate_kind"] = "radial"
    elif fault == "offset":
        values["metadata"]["soil_observation_binding"]["clock_offset_s"] = 0.0
    else:
        values["metadata"]["soil_observation_binding"]["record_digest"] = "f" * 64
    with pytest.raises(ValueError):
        data.borehole_temperature_frames(
            record,
            RunContext.model_validate(values),
            depth_index=0,
            evidence_id="source",
            clock_offset_s=20.0,
        )


@pytest.mark.parametrize(
    "fault",
    [
        "units",
        "geometry",
        "calibration",
        "processing",
        "diagnostics",
        "storage",
        "provenance",
        "method",
        "origin",
    ],
)
def test_rehashed_false_semantics_are_refused(tmp_path, fault):
    record, _ = pinned_borehole(tmp_path)
    bad = deepcopy(record)
    if fault == "units":
        bad["source_units"]["temperature"] = "K"
    elif fault == "geometry":
        bad["coordinate_kind"] = "radial_radius"
    elif fault == "calibration":
        bad["applicability"]["thermal_property_calibration"] = "computed"
    elif fault == "processing":
        bad["processing"]["exact_trace_specific_preprocessing_lineage_resolved"] = True
    elif fault == "diagnostics":
        bad["observations"]["time_clock"]["first"] = 0.0
    elif fault == "storage":
        bad["storage"]["temperature"]["hdf5_shape"] = [2, 3]
    elif fault == "provenance":
        bad["provenance"]["dataset_id"] = data.COVERS_ID
    elif fault == "origin":
        bad["origin"] = "simulation"
    else:
        bad["method_assets"] = []
    with pytest.raises(ValueError):
        data.verify_soil_source_record(reseal(bad))


def test_import_of_reader_is_optional_dependency_lazy():
    code = "import builtins; old=builtins.__import__; builtins.__import__=lambda name,*a,**k: (_ for _ in ()).throw(ImportError(name)) if name.split('.')[0] in {'h5py','numpy','openpyxl'} else old(name,*a,**k); import pipeline_integrity.soil_data"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15).returncode == 0


def test_source_time_of_day_preserves_unknown_date_and_timezone():
    import json
    from datetime import time

    from pipeline_integrity.soil_data import _iso, _scalar

    value = _scalar(time(8, 58, 30, 123456))
    assert value == {"source_type": "time", "iso": "08:58:30.123456", "timezone": None}
    assert json.loads(json.dumps(value)) == value
    assert _iso(value) is None  # A clock reading alone is not a calendar timestamp.
