"""Pinned soil observations with source clocks, unresolved units and geometry retained."""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from datetime import date, datetime
from datetime import time as source_time
from pathlib import Path, PurePosixPath

from .contracts import RunContext, SignalFrame, digest
from .datasets import DatasetSpec, catalog

COVERS_ID = "soil_hydrothermal_covers"
BOREHOLE_ID = "soil_borehole_heating"
COVER_FILES = (
    "Climate data of experiment.xlsx",
    "Soil temperature.xlsx",
    "Water content.xlsx",
    "Suction.xlsx",
)
METHOD_FILES = ("data_process.mlx", "estimation_process.mlx")
SCHEMA = "pipeline_integrity.soil_source.v1"


def _seal(body):
    return dict(body, record_digest=digest(body))


def _scalar(value):
    if isinstance(value, (datetime, date, source_time)):
        return {"source_type": type(value).__name__, "iso": value.isoformat(), "timezone": None}
    if isinstance(value, float) and not math.isfinite(value):
        return {"source_type": "nonfinite", "text": str(value)}
    return value


def _numeric(value):
    if value is None or isinstance(value, dict):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _iso(value):
    return (
        value.get("iso")
        if isinstance(value, dict) and value.get("source_type") in {"datetime", "date"}
        else None
    )


def _clock(values):
    finite = [_numeric(v) for v in values]
    intervals = [b - a for a, b in zip(finite, finite[1:], strict=False) if a is not None and b is not None]
    counts = {}
    for value in intervals:
        label = format(value, ".17g")
        counts[label] = counts.get(label, 0) + 1
    return dict(
        observations=len(values),
        missing_or_invalid=sum(v is None for v in finite),
        repeated_adjacent=sum(v == 0 for v in intervals),
        decreasing_adjacent=sum(v < 0 for v in intervals),
        adjacent_interval_counts=counts,
        first=finite[0] if finite else None,
        last=finite[-1] if finite else None,
        source_order_changed=False,
    )


def _calendar_clock(values):
    parsed = [datetime.fromisoformat(v) if isinstance(v, str) else None for v in values]
    intervals = [(b - a).total_seconds() for a, b in zip(parsed, parsed[1:], strict=False) if a and b]
    return dict(
        observations=len(values),
        unresolved_rows=sum(v is None for v in parsed),
        repeated_adjacent=sum(v == 0 for v in intervals),
        decreasing_adjacent=sum(v < 0 for v in intervals),
        minimum_adjacent_interval_s=min(intervals) if intervals else None,
        maximum_adjacent_interval_s=max(intervals) if intervals else None,
        first=values[0] if values else None,
        last=values[-1] if values else None,
        timezone=None,
        utc_assigned=False,
        source_order_changed=False,
    )


def _positive_budget(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("resource budgets must be positive integer counts")
    return value


def _bounded_xlsx(
    payload,
    *,
    maximum_bytes=8_000_000,
    maximum_uncompressed_bytes=64_000_000,
    maximum_member_bytes=32_000_000,
):
    for value in (maximum_bytes, maximum_uncompressed_bytes, maximum_member_bytes):
        _positive_budget(value)
    if not isinstance(payload, bytes) or not 0 < len(payload) <= maximum_bytes:
        raise ValueError("nonempty original workbook bytes within the declared byte budget are required")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(infos) > 1024 or len(set(names)) != len(names):
            raise ValueError("workbook archive member budget or duplicate member")
        if sum(item.file_size for item in infos) > maximum_uncompressed_bytes:
            raise ValueError("workbook uncompressed byte budget")
        for item in infos:
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in item.orig_filename
                or ":" in item.filename
                or item.flag_bits & 1
            ):
                raise ValueError("unsafe workbook member")
            if item.file_size > maximum_member_bytes:
                raise ValueError("workbook member byte budget")


def _workbook(
    payload,
    *,
    maximum_bytes=8_000_000,
    maximum_uncompressed_bytes=64_000_000,
    maximum_member_bytes=32_000_000,
    maximum_cells=1_000_000,
    maximum_sheets=20,
):
    _positive_budget(maximum_cells)
    _positive_budget(maximum_sheets)
    _bounded_xlsx(
        payload,
        maximum_bytes=maximum_bytes,
        maximum_uncompressed_bytes=maximum_uncompressed_bytes,
        maximum_member_bytes=maximum_member_bytes,
    )
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), data_only=False, read_only=True, keep_links=False)
    cached = None
    try:
        if (
            len(book.worksheets) > maximum_sheets
            or sum(s.max_row * s.max_column for s in book) > maximum_cells
        ):
            raise ValueError("workbook cell/sheet budget exceeded before row materialization")
        sheets = {}
        formulas = {}
        for sheet in book:
            rows = []
            for row in sheet:
                rows.append([_scalar(cell.value) for cell in row])
                for cell in row:
                    if cell.data_type == "f":
                        formulas.setdefault(sheet.title, []).append(cell.coordinate)
            sheets[sheet.title] = dict(
                source_cells=rows, rows=sheet.max_row, columns=sheet.max_column, formula_cache={}
            )
        if formulas:
            cached = load_workbook(io.BytesIO(payload), data_only=True, read_only=True, keep_links=False)
            for name, coordinates in formulas.items():
                wanted = set(coordinates)
                for row in cached[name]:
                    for cell in row:
                        if getattr(cell, "coordinate", None) in wanted:
                            sheets[name]["formula_cache"][cell.coordinate] = _scalar(cell.value)
        return sheets
    finally:
        book.close()
        if cached is not None:
            cached.close()


def _sheet_clock(sheet, column, start):
    rows = sheet["source_cells"]
    selected = [
        (i + 1, row) for i, row in enumerate(rows) if i + 1 >= start and _iso(row[column - 1]) is not None
    ]
    clock = _calendar_clock([_iso(row[column - 1]) for _, row in selected])
    clock["undated_nonempty_source_rows"] = [
        i + 1
        for i, row in enumerate(rows)
        if i + 1 >= start and any(v is not None for v in row) and _iso(row[column - 1]) is None
    ]
    clock["blank_source_rows"] = sum(
        not any(v is not None for v in row) for i, row in enumerate(rows) if i + 1 >= start
    )
    return selected, clock


def _cover_body(filename, sheets):
    if filename not in COVER_FILES:
        raise ValueError("one reviewed soil cover workbook filename is required")
    expected = {
        "Soil temperature.xlsx": ("B", "T", "S"),
        "Climate data of experiment.xlsx": ("Air temperature", "Rainfall"),
        "Water content.xlsx": ("含水率全部",),
        "Suction.xlsx": ("吸力",),
    }
    if tuple(sheets) != expected[filename]:
        raise ValueError("soil workbook worksheet identities/order differ from the reviewed layout")
    diagnostics = {}
    for name, sheet in sheets.items():
        rows = sheet["source_cells"]
        if sheet["rows"] != len(rows) or any(len(row) != sheet["columns"] for row in rows):
            raise ValueError("soil workbook row/column extent differs")
        if filename == "Soil temperature.xlsx":
            layout = {"B": (2, 4, 3, 9, 15, 2, 3), "T": (1, 6, 2, 8, 14, 4, 5), "S": (1, 5, 2, 8, 14, 3, 4)}[
                name
            ]
            day_col, start, max_col, min_col, range_col, title_row, label_row = layout
            for col, label in (
                (max_col, "Daily maximum temperature"),
                (min_col, "Daily minmum temperature"),
                (range_col, "Diurnal temperature ranges"),
            ):
                if rows[title_row - 1][col - 1] != label:
                    raise ValueError("soil daily-statistic header changed")
                if rows[label_row - 1][col - 1 : col + 4] != ["L1", "L2", "L3", "L4", "L5"]:
                    raise ValueError("soil opaque level labels changed")
            selected, clock = _sheet_clock(sheet, day_col, start)
            comparisons = []
            unresolved = 0
            for _, row in selected:
                for i in range(5):
                    maximum, minimum, reported = [
                        _numeric(row[col - 1 + i]) for col in (max_col, min_col, range_col)
                    ]
                    if None in (maximum, minimum, reported):
                        unresolved += 1
                    else:
                        comparisons.append(maximum - minimum - reported)
            diagnostics[name] = dict(
                statistic="daily_maximum_minimum_and_range_not_instantaneous",
                clock=clock,
                daily_rows=len(selected),
                source_row_numbers=[i for i, _ in selected],
                source_date_column=day_col,
                source_statistic_columns={
                    "maximum": list(range(max_col, max_col + 5)),
                    "minimum": list(range(min_col, min_col + 5)),
                    "range": list(range(range_col, range_col + 5)),
                },
                source_temperature_unit=None,
                level_labels=["L1", "L2", "L3", "L4", "L5"],
                level_depths_m=None,
                treatment_code=name,
                treatment_code_mapping=None,
                complete_header_aligned_range_comparisons=len(comparisons),
                unresolved_header_aligned_range_comparisons=unresolved,
                inconsistent_header_aligned_ranges=sum(abs(v) > 1e-9 for v in comparisons),
                maximum_abs_header_aligned_range_residual=max(map(abs, comparisons)) if comparisons else None,
                header_or_value_positions_repaired=False,
            )
        elif filename in {"Water content.xlsx", "Suction.xlsx"}:
            labels = ["S-5cm", "T-5cm", "B-5cm", "S-15cm", "T-15cm", "B-15cm"]
            if rows[0] != [None, None, *labels]:
                raise ValueError("soil sensor depth/treatment headers changed")
            selected, clock = _sheet_clock(sheet, 2, 2)
            diagnostics[name] = dict(
                statistic="reported_sensor_value",
                quantity="water_content" if filename.startswith("Water") else "signed_suction",
                clock=clock,
                source_row_numbers=[i for i, _ in selected],
                numeric_observations=sum(_numeric(v) is not None for _, row in selected for v in row[2:]),
                missing_or_invalid_observations=sum(
                    _numeric(v) is None for _, row in selected for v in row[2:]
                ),
                source_unit=None,
                source_depth_labels=labels,
                depth_m=[0.05] * 3 + [0.15] * 3,
                depth_direction="below_surface_as_source_cm_label",
                treatment_code_mapping=None,
                physical_sensor_ids=None,
            )
        else:
            column = 4 if name == "Air temperature" else 4
            start = 3 if name == "Air temperature" else 2
            _, clock = _sheet_clock(sheet, column, start)
            diagnostics[name] = dict(clock=clock, source_clock_column=column, source_temperature_unit=None)
            if name == "Air temperature":
                _, other = _sheet_clock(sheet, 5, 3)
                diagnostics[name]["second_source_clock_column"] = 5
                diagnostics[name]["second_source_clock"] = other
                diagnostics[name]["statistic"] = "daily_air_mean_maximum_minimum_not_soil_surface_temperature"
            else:
                if rows[0][4:7] != [
                    "Total precipitaion (mm)",
                    "Hourly precipitaion (mm)",
                    "Daily precipitaion (mm)",
                ]:
                    raise ValueError("rain depth unit/header changed")
                diagnostics[name]["rain_depth_unit"] = "mm"
                diagnostics[name]["formula_count"] = len(sheet["formula_cache"])
    return dict(
        schema_version=SCHEMA,
        kind="soil_cover_workbook",
        origin="laboratory",
        filename=filename,
        worksheets=sheets,
        diagnostics=diagnostics,
        applicability=dict(
            transient_pipeline_validation="needs_data",
            thermal_property_calibration="needs_data",
            instantaneous_soil_temperature_reconstructed=False,
            pipe_geometry=None,
            conductivity_W_m_K=None,
            volumetric_heat_capacity_J_m3_K=None,
            clock_timezone=None,
        ),
        source_rows_sorted_filtered_interpolated_or_repaired=False,
    )


def parse_soil_cover_workbook(payload, *, filename):
    """Parse original cells and declared headers without guessing units or sensor levels."""
    return _seal(_cover_body(filename, _workbook(payload)))


def _borehole_body(arrays, storage):
    if set(arrays) != {"depth", "temperature", "time_seconds"}:
        raise ValueError("reviewed borehole variables required")
    depth, times, temperatures = arrays["depth"], arrays["time_seconds"], arrays["temperature"]
    if not 1 <= len(depth) <= 1000 or not 2 <= len(times) <= 10000 or len(depth) * len(times) > 1_000_000:
        raise ValueError("borehole array budget")
    if len(temperatures) != len(times) or any(len(row) != len(depth) for row in temperatures):
        raise ValueError("borehole temperature time/depth orientation differs")
    shapes = {
        "depth": [1, len(depth)],
        "time_seconds": [len(times), 1],
        "temperature": [len(times), len(depth)],
    }
    if set(storage) != set(shapes) or any(
        storage[name] != dict(hdf5_shape=shape, dtype="float64", matlab_class="double")
        for name, shape in shapes.items()
    ):
        raise ValueError("borehole HDF5 storage/class projection differs")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float, dict, type(None))) for v in [*times, *depth]
    ):
        raise ValueError("borehole numeric coordinate storage required")
    values = [v for row in temperatures for v in row]
    if any(isinstance(v, bool) or not isinstance(v, (int, float, dict, type(None))) for v in values):
        raise ValueError("borehole numeric temperature storage required")
    if any(
        isinstance(v, dict)
        and (
            set(v) != {"source_type", "text"}
            or v["source_type"] != "nonfinite"
            or v["text"] not in {"nan", "inf", "-inf"}
        )
        for v in [*times, *depth, *values]
    ):
        raise ValueError("nonfinite source representation differs")
    finite = [_numeric(v) for v in values]
    valid = [v for v in finite if v is not None]
    time_clock = _clock(times)
    depth_clock = _clock(depth)
    return dict(
        schema_version=SCHEMA,
        kind="processed_borehole_temperature",
        origin="measured",
        source_arrays=arrays,
        storage=storage,
        source_units={"time_seconds": "s", "depth": "m", "temperature": "degC"},
        unit_evidence="axis labels in the checksum-pinned publisher method scripts; not guessed from values",
        coordinate_kind="vertical_borehole_depth_not_radial_radius",
        source_clock_kind="publisher_processing_elapsed_seconds",
        source_calendar_origin=None,
        source_timezone=None,
        observations=dict(
            time_clock=time_clock,
            depth_coordinates=depth_clock,
            temperatures=len(values),
            finite_temperatures=len(valid),
            missing_or_invalid_temperatures=len(values) - len(valid),
            minimum_temperature_degC=min(valid) if valid else None,
            maximum_temperature_degC=max(valid) if valid else None,
            temperature_orientation="time_by_depth",
        ),
        processing=dict(
            supplied_estimation_input=True,
            original_full_measurements_acquired=False,
            correction_and_thinning_described_by_publisher=True,
            exact_trace_specific_preprocessing_lineage_resolved=False,
            source_time_rebased_or_resampled=False,
            independent_depth_specimens_assumed=False,
        ),
        applicability=dict(
            radial_soil_pipe_model="needs_data",
            transient_pipeline_validation="needs_data",
            thermal_property_calibration="needs_data",
            pipe_geometry=None,
            radial_boundary_location_m=None,
            heating_power_W=None,
            conductivity_W_m_K=None,
            volumetric_heat_capacity_J_m3_K=None,
            groundwater_advection=None,
        ),
    )


def parse_borehole_temperature(payload):
    """Read MATLAB v7.3 numeric arrays in native HDF5 orientation; execute no scripts."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 8_000_000:
        raise ValueError("nonempty original MATLAB bytes within 8 MB required")
    import h5py

    with h5py.File(io.BytesIO(payload), "r") as book:
        if set(book) != {"depth", "temperature", "time_seconds"}:
            raise ValueError("reviewed HDF5 variable set differs")
        storage = {}
        arrays = {}
        for name in book:
            if not isinstance(book.get(name, getlink=True), h5py.HardLink):
                raise ValueError("external/soft HDF5 links are not source observations")
            item = book[name]
            if (
                not isinstance(item, h5py.Dataset)
                or str(item.dtype) != "float64"
                or item.is_virtual
                or item.external
            ):
                raise ValueError("self-contained numeric HDF5 dataset required")
            if item.ndim != 2 or item.size > 1_000_000 or item.attrs.get("MATLAB_class") != b"double":
                raise ValueError("MATLAB numeric class/shape/budget differs")
            values = item[...].tolist()
            storage[name] = dict(hdf5_shape=list(item.shape), dtype=str(item.dtype), matlab_class="double")
            if name == "temperature":
                arrays[name] = [[_scalar(v) for v in row] for row in values]
            else:
                if name == "depth" and item.shape[0] != 1 or name == "time_seconds" and item.shape[1] != 1:
                    raise ValueError("HDF5 coordinate vector orientation differs")
                arrays[name] = [_scalar(v) for row in values for v in row]
    return _seal(_borehole_body(arrays, storage))


def _pinned(path, identity, *, source_spec=None, maximum_bytes=8_000_000):
    _positive_budget(maximum_bytes)
    path = Path(path)
    choices = (
        [d for d in catalog() if d.id == identity]
        if source_spec is None
        else [DatasetSpec.model_validate_json(source_spec.model_dump_json())]
    )
    if len(choices) != 1 or choices[0].id != identity:
        raise ValueError("one reviewed source specification required")
    spec = choices[0]
    selected = [a for a in spec.assets if a.filename == path.name]
    if (
        spec.rights_review != "reviewed_license"
        or spec.acquisition != "allowed"
        or spec.license_id != "CC-BY-4.0"
        or len(selected) != 1
    ):
        raise ValueError("soil source rights/identity unresolved")
    asset = selected[0]
    if (
        asset.bytes is None
        or asset.bytes > maximum_bytes
        or asset.checksum is None
        or asset.checksum_origin != "publisher_metadata"
    ):
        raise ValueError("source publisher byte/checksum pin required")
    if path.stat().st_size != asset.bytes:
        raise ValueError("source size differs from reviewed publisher asset")
    payload = path.read_bytes()
    algorithm, expected = asset.checksum.split(":", 1)
    if hashlib.new(algorithm, payload).hexdigest() != expected:
        raise ValueError("source checksum differs from reviewed publisher asset")
    return payload, dict(
        dataset_id=spec.id,
        filename=asset.filename,
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        expected_checksum=asset.checksum,
        checksum_origin=asset.checksum_origin,
        supplier_checksum_verified=True,
        citation=spec.citation,
        creators=list(spec.creators),
        version=spec.version,
        license_id=spec.license_id,
        rights_evidence_url=spec.rights_evidence_url,
        source_url=asset.url,
        dataset_origin=spec.origin,
    )


def read_soil_cover_workbook(path, *, source_spec=None):
    raw, provenance = _pinned(path, COVERS_ID, source_spec=source_spec)
    parsed = parse_soil_cover_workbook(raw, filename=Path(path).name)
    body = {k: v for k, v in parsed.items() if k != "record_digest"}
    return _seal(dict(body, provenance=provenance))


def read_borehole_temperature(folder, *, source_spec=None):
    """Require data plus both method source pins; neither method file is executed."""
    folder = Path(folder)
    raw, provenance = _pinned(folder / "estimation_data.mat", BOREHOLE_ID, source_spec=source_spec)
    methods = [_pinned(folder / name, BOREHOLE_ID, source_spec=source_spec)[1] for name in METHOD_FILES]
    parsed = parse_borehole_temperature(raw)
    body = {k: v for k, v in parsed.items() if k != "record_digest"}
    return _seal(dict(body, provenance=provenance, method_assets=methods))


def verify_soil_source_record(record):
    """Check unsigned byte bindings and recomputed source diagnostics, never field truth."""
    body = {k: v for k, v in record.items() if k != "record_digest"}
    if record.get("record_digest") != digest(body):
        raise ValueError("soil source receipt digest differs")
    if record.get("kind") == "soil_cover_workbook":
        expected = _cover_body(record["filename"], record["worksheets"])
    elif record.get("kind") == "processed_borehole_temperature":
        expected = _borehole_body(record["source_arrays"], record["storage"])
    else:
        raise ValueError("unknown soil source record")
    if any(record.get(k) != v for k, v in expected.items()):
        raise ValueError("soil source semantics or diagnostic projection differs")
    if "provenance" in record:
        for item in [record["provenance"], *record.get("method_assets", [])]:
            if (
                item.get("license_id") != "CC-BY-4.0"
                or item.get("supplier_checksum_verified") is not True
                or item.get("checksum_origin") != "publisher_metadata"
            ):
                raise ValueError("soil source provenance claim differs")
        identity = COVERS_ID if record["kind"] == "soil_cover_workbook" else BOREHOLE_ID
        filename = record.get("filename", "estimation_data.mat")
        if (
            record["provenance"].get("dataset_id") != identity
            or record["provenance"].get("filename") != filename
        ):
            raise ValueError("soil record and provenance source identity differ")
        for item in [record["provenance"], *record.get("method_assets", [])]:
            if (
                not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])
            ):
                raise ValueError("soil asset SHA-256 binding required")
        if record["kind"] == "processed_borehole_temperature" and [
            a.get("filename") for a in record.get("method_assets", [])
        ] != list(METHOD_FILES):
            raise ValueError("borehole unit/method source pins absent")
    return True


def borehole_temperature_frames(record, context, *, depth_index, evidence_id, clock_offset_s=0.0):
    """Export observed point temperatures on the explicit processing clock, not a pipe boundary."""
    verify_soil_source_record(record)
    if record["kind"] != "processed_borehole_temperature" or "provenance" not in record:
        raise ValueError("publisher-pinned borehole source record required")
    context = RunContext.model_validate(context.model_dump() if isinstance(context, RunContext) else context)
    if type(depth_index) is not int or not 0 <= depth_index < len(record["source_arrays"]["depth"]):
        raise ValueError("explicit original depth index required")
    if (
        isinstance(clock_offset_s, bool)
        or not isinstance(clock_offset_s, (int, float))
        or not math.isfinite(clock_offset_s)
        or clock_offset_s < 0
    ):
        raise ValueError("nonnegative processing-clock offset seconds required")
    if context.origin != "measured":
        raise ValueError("processed field observations retain measured origin")
    depth = _numeric(record["source_arrays"]["depth"][depth_index])
    if depth is None or depth < 0:
        raise ValueError("finite nonnegative vertical depth required for export")
    evidence = [e for e in context.evidence if e.id == evidence_id]
    if (
        len({e.id for e in context.evidence}) != len(context.evidence)
        or len(evidence) != 1
        or evidence[0].sha256 != record["provenance"]["sha256"]
        or evidence[0].origin != "measured"
        or evidence[0].license_id != "CC-BY-4.0"
    ):
        raise ValueError("context evidence must bind the exact source asset and rights")
    binding = dict(
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        record_digest=record["record_digest"],
        source_clock_kind=record["source_clock_kind"],
        coordinate_kind=record["coordinate_kind"],
        depth_index=depth_index,
        depth_m=record["source_arrays"]["depth"][depth_index],
        clock_offset_s=clock_offset_s,
        quantity_meaning="publisher_processed_point_temperature_not_pipe_or_radial_boundary",
    )
    if context.metadata.get("soil_observation_binding") != binding:
        raise ValueError("context lacks the exact explicit observation clock/location binding")
    times = record["source_arrays"]["time_seconds"]
    if any(_numeric(t) is None or t < 0 for t in times) or any(
        b <= a for a, b in zip(times, times[1:], strict=False)
    ):
        raise ValueError("nonnegative strictly increasing processing clock required for export")
    frames = []
    for time, row in zip(times, record["source_arrays"]["temperature"], strict=True):
        value = _numeric(row[depth_index])
        temperature = None if value is None else value + 273.15
        if temperature is not None and temperature <= 0:
            raise ValueError("observed temperature is outside absolute Kelvin domain")
        frames.append(
            SignalFrame(
                stream_id=f"{BOREHOLE_ID}:depth-index-{depth_index}",
                clock_id=context.clock_id,
                time_s=time + clock_offset_s,
                values={"temperature_K": temperature},
                units={"temperature_K": "K"},
                valid={"temperature_K": temperature is not None},
                origin="measured",
                operating_mode="publisher-processed-field-thermal-test",
            )
        )
    return tuple(frames)
