"""Pinned process observations; source statistics are never performance maps."""

from __future__ import annotations

import hashlib
import io
import platform
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from .contracts import digest
from .datasets import DatasetSpec, catalog, file_digest
from .soil_data import _bounded_xlsx, _calendar_clock, _numeric, _pinned, _scalar, _workbook

LN2_ID = "cryogenic_ln2_boiling"
COMPRESSOR_ID = "refinery_compressor_process"
NASA_ID = "nasa_lh2_d4171"
IDS = (LN2_ID, COMPRESSOR_ID, NASA_ID)
SCHEMA = "pipeline_integrity.thermal_process_source.v1"


def _spec(identity, supplied):
    choices = (
        [s for s in catalog() if s.id == identity]
        if supplied is None
        else [DatasetSpec.model_validate_json(supplied.model_dump_json())]
    )
    if identity not in IDS or len(choices) != 1 or choices[0].id != identity:
        raise ValueError("one explicitly reviewed thermal process source is required")
    return choices[0]


def _pinned_asset(path, spec):
    if spec.id != NASA_ID:
        return _pinned(path, spec.id, source_spec=spec, maximum_bytes=32_000_000)
    if (spec.rights_review, spec.acquisition, spec.license_id) != (
        "reviewed_license",
        "allowed",
        "US-GOV-PUBLIC-USE-PERMITTED",
    ):
        raise ValueError("NASA source requires its explicitly reviewed public-use determination")
    assets = [a for a in spec.assets if a.filename == path.name]
    if len(assets) != 1:
        raise ValueError("one original NASA report asset is required")
    asset = assets[0]
    if (
        asset.bytes is None
        or asset.bytes > 8_000_000
        or asset.checksum is None
        or asset.checksum_origin != "previous_acquisition"
    ):
        raise ValueError("NASA source requires a bounded, explicitly local prior-acquisition checksum")
    if path.stat().st_size != asset.bytes:
        raise ValueError("NASA original source size changed")
    raw = path.read_bytes()
    algorithm, expected = asset.checksum.split(":", 1)
    if hashlib.new(algorithm, raw).hexdigest() != expected:
        raise ValueError("NASA original source checksum changed")
    return raw, {
        "dataset_id": spec.id,
        "filename": path.name,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "expected_checksum": asset.checksum,
        "checksum_origin": asset.checksum_origin,
        "supplier_checksum_verified": False,
        "license_id": spec.license_id,
        "rights_evidence_url": spec.rights_evidence_url,
        "source_url": asset.url,
        "citation": spec.citation,
        "creators": list(spec.creators),
        "version": spec.version,
        "dataset_origin": spec.origin,
    }


def _preview(payload):
    """Bounded source preview; declared extents are explicitly not a full scan."""
    _bounded_xlsx(
        payload,
        maximum_bytes=32_000_000,
        maximum_uncompressed_bytes=128_000_000,
        maximum_member_bytes=64_000_000,
    )
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
    try:
        if len(book.worksheets) > 20:
            raise ValueError("source sheet budget exceeded")
        sheets = {}
        for sheet in book:
            if sheet.max_column is None or sheet.max_column > 256:
                raise ValueError("source preview column extent is unresolved or too large")
            rows = []
            for row in sheet.iter_rows(max_row=3):
                rows.append([_scalar(c.value) for c in row])
            sheets[sheet.title] = {
                "declared_rows": sheet.max_row,
                "declared_columns": sheet.max_column,
                "source_preview": rows,
                "complete_rows_scanned": False,
                "formulas_executed": False,
            }
        return sheets
    finally:
        book.close()


def _compressor_rows(payload):
    _bounded_xlsx(payload, maximum_bytes=16_000_000, maximum_member_bytes=48_000_000)
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
    try:
        if book.sheetnames != ["Sheet1", "Sheet2"]:
            raise ValueError("review the changed compressor workbook sheets")
        if any(value is not None for row in book["Sheet2"].iter_rows(values_only=True) for value in row):
            raise ValueError("review newly populated compressor Sheet2")
        sheet = book["Sheet1"]
        sheet.reset_dimensions()
        iterator = iter(sheet.iter_rows(values_only=True))
        headers = next(iterator, None)
        if (
            headers is None
            or len(headers) != 26
            or headers[0] != "Timestamp"
            or len(set(headers)) != len(headers)
            or any(not isinstance(v, str) for v in headers)
        ):
            raise ValueError("review the changed timestamp and 25-sensor compressor schema")
        for source_row, row in enumerate(iterator, 2):
            if source_row > 100001 or len(row) > len(headers):
                raise ValueError("compressor source row/column budget or shape changed")
            row = (*row, *((None,) * (len(headers) - len(row))))
            values = dict(zip(headers[1:], (_scalar(v) for v in row[1:]), strict=True))
            source_time = _scalar(row[0])
            if isinstance(source_time, str):
                try:
                    parsed = datetime.fromisoformat(source_time)
                except ValueError:
                    parsed = None
                if parsed is not None and parsed.utcoffset() is not None:
                    raise ValueError("review changed source timezone semantics")
                source_time_iso = None if parsed is None else parsed.isoformat()
            elif isinstance(source_time, dict):
                source_time_iso = source_time.get("iso")
            else:
                source_time_iso = None
            yield {
                "source_row": source_row,
                "source_timestamp": source_time,
                "calendar_time_without_zone": source_time_iso,
                "timestamp_utc": None,
                "values": values,
                "units_resolved": False,
            }
    finally:
        book.close()


def iter_refinery_observations(path, *, source_spec=None):
    """Stream original signed/unknown sensor values after rights and checksum checks."""
    spec = _spec(COMPRESSOR_ID, source_spec)
    payload, _ = _pinned_asset(Path(path), spec)
    yield from _compressor_rows(payload)


def _compressor_summary(payload):
    times, stats, samples = [], {}, []
    count = 0
    for row in _compressor_rows(payload):
        count += 1
        times.append(row["calendar_time_without_zone"])
        if len(samples) < 3:
            samples.append(row)
        for name, value in row["values"].items():
            state = stats.setdefault(
                name,
                {
                    "source_unit": None,
                    "quantity_role": None,
                    "finite": 0,
                    "missing_or_nonnumeric": 0,
                    "negative": 0,
                    "zero": 0,
                    "minimum": None,
                    "maximum": None,
                },
            )
            number = _numeric(value)
            if number is None:
                state["missing_or_nonnumeric"] += 1
            else:
                state["finite"] += 1
                state["negative"] += int(number < 0)
                state["zero"] += int(number == 0)
                state["minimum"] = number if state["minimum"] is None else min(state["minimum"], number)
                state["maximum"] = number if state["maximum"] is None else max(state["maximum"], number)
    return {
        "observations": count,
        "sensor_columns": len(stats),
        "channels": stats,
        "source_preview": samples,
        "clock": _calendar_clock(times),
        "chronology_sorted": False,
        "map_points_inferred": False,
        "fluid_inferred": False,
        "native_compressor_calls": 0,
        "model_mapping_status": "needs_data",
        "missing_model_inputs": [
            "fluid/composition",
            "sensor units and physical port roles",
            "absolute total inlet P/T",
            "mass flow",
            "shaft speed unit/reference",
            "pressure-ratio/efficiency map and surge/choke boundaries",
            "drive and intercooler boundary",
        ],
    }


def _ln2_curves(payload):
    sheets = _workbook(payload)
    expected = ("ΔT_w (K)", "U_ΔT_w (K)", "q'' (W/cm²)", "U_q'' (W/cm²)", "h (W/cm²·K)", "U_h (W/cm²·K)")
    if set(sheets) != {"1-g", "ug_Curve 1", "ug_Curve 2", "ug_Curve 3"}:
        raise ValueError("review changed LN2 uncertainty curve population")
    records = []
    for name, sheet in sheets.items():
        rows = sheet["source_cells"]
        indices = [i for i, row in enumerate(rows[:3]) if tuple(row) == expected]
        if len(indices) != 1:
            raise ValueError("review changed source quantity/unit headers")
        for index, row in enumerate(rows[indices[0] + 1 :], indices[0] + 2):
            if all(v is None for v in row):
                continue
            if len(row) != 6:
                raise ValueError("LN2 curve shape changed")
            values = [_numeric(v) for v in row]
            dt, udt, q, uq, h, uh = values
            records.append(
                {
                    "curve": name,
                    "source_row": index,
                    "source_values": row,
                    "wall_superheat_K": dt,
                    "heat_flux_W_m2": None if q is None else q * 10000,
                    "heat_transfer_coefficient_W_m2_K": None if h is None else h * 10000,
                    "reported_U_wall_superheat_K": udt,
                    "reported_U_heat_flux_W_m2": None if uq is None else uq * 10000,
                    "reported_U_htc_W_m2_K": None if uh is None else uh * 10000,
                    "reported_U_coverage_factor": None,
                    "standard_joint_covariance": None,
                    "source_htc_minus_q_over_deltaT_W_m2_K": None
                    if h is None or q is None or dt in (None, 0)
                    else h * 10000 - q * 10000 / dt,
                }
            )
    return {
        "fluid": "Nitrogen",
        "source_kind": "processed_experimental_curves_with_reported_uncertainties",
        "records": records,
        "observations": len(records),
        "uncertainty_interpretation": "source U columns retained; no k, distribution or independence inferred",
        "native_hydrogen_model_calls": 0,
        "model_mapping_status": "out_of_scope_for_hydrogen_only_closures",
        "source_values_repaired": False,
    }


def _nasa_report(payload):
    from pypdf import PdfReader

    if not isinstance(payload, bytes) or not 0 < len(payload) <= 8_000_000:
        raise ValueError("bounded original NASA report required")
    reader = PdfReader(io.BytesIO(payload))
    if len(reader.pages) != 50:
        raise ValueError("review changed NASA report pagination")
    first = reader.pages[0].extract_text()
    table1 = reader.pages[28].extract_text()
    table2 = reader.pages[29].extract_text()
    if (
        "SELF-PRESSURIZATION" not in first
        or "SPHERICAL LIQUID HYDROGEN TANKAGE" not in first
        or "Aydelott" not in first
        or "TABLE I." not in table1
        or "TABLE II." not in table2
    ):
        raise ValueError("review changed report identity or source table locations")
    return {
        "report": "NASA-TN-D-4171",
        "source_pages": 50,
        "fluid": "Hydrogen",
        "table_pages_one_based": {"experimental_results": 29, "energy_distribution": 30},
        "table_text_sha256": {
            "experimental_results": hashlib.sha256(table1.encode()).hexdigest(),
            "energy_distribution": hashlib.sha256(table2.encode()).hexdigest(),
        },
        "numeric_table_cells_qualified": False,
        "figure_digitization_performed": False,
        "table_limitation": "OCR merges rows, column values and rule artifacts; automatic numeric reconstruction is withheld pending a cell-level visual mapping",
        "native_phase_model_calls": 0,
        "model_mapping_status": "needs_data",
        "model_scope": "tank self-pressurization and distributed heating; not a standalone interface or pipe-boiling validation",
    }


def read_thermal_process_source(identity, folder, *, source_spec=None):
    spec = _spec(identity, source_spec)
    folder = Path(folder)
    assets = []
    products = {}
    for asset in spec.assets:
        raw, provenance = _pinned_asset(folder / asset.filename, spec)
        assets.append(provenance)
        if identity == COMPRESSOR_ID:
            products[asset.filename] = _compressor_summary(raw)
        elif identity == LN2_ID:
            if asset.filename == "Uncertainty Data_All curves.xlsx":
                products[asset.filename] = _ln2_curves(raw)
            else:
                products[asset.filename] = {
                    "worksheets": _preview(raw),
                    "only_preview_qualified": True,
                    "third_party_comparison_columns_excluded": asset.filename
                    == "All boiling curves_after ANSYS.xlsx",
                    "raw_clock_units_pressure_reference_and_formula_meaning_require_review": True,
                }
        elif asset.filename.endswith(".pdf"):
            products[asset.filename] = _nasa_report(raw)
        else:
            text = raw.decode("utf-8")
            products[asset.filename] = {
                "bytes": len(raw),
                "text_sha256": hashlib.sha256(raw).hexdigest(),
                "nonempty": bool(text.strip()),
                "ocr_is_not_a_numeric_table": True,
            }
    body = {
        "schema": SCHEMA,
        "dataset_id": identity,
        "assets": assets,
        "products": products,
        "supplier_programs_executed": False,
        "independent_physical_model_validation": False,
        "raw_observations_in_code_release": False,
        "implementation_sha256": file_digest(Path(__file__)),
        "shared_reader_sha256": file_digest(Path(__file__).with_name("soil_data.py")),
        "runtime": {
            "python": platform.python_version(),
            **{name: version(name) for name in (("pypdf",) if identity == NASA_ID else ("openpyxl",))},
        },
    }
    return {**body, "record_digest": digest(body)}


def verify_thermal_process_record(record, *, source_root, source_spec=None):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("thermal source receipt seal changed")
    expected = read_thermal_process_source(
        record["dataset_id"], Path(source_root) / record["dataset_id"], source_spec=source_spec
    )
    if expected != record:
        raise ValueError(
            "thermal source observations, units, clock, origin or qualification differs on replay"
        )
    return True
