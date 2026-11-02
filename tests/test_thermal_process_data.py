"""Authored source fixtures retain values, missing meaning, units and provenance."""

import hashlib
import io
import zipfile
from copy import deepcopy
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from pipeline_integrity import thermal_process_data as data
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import AssetSpec, DatasetSpec


def source_spec(identity, filename, payload):
    return DatasetSpec(
        id=identity,
        title="Authored source fixture",
        source="https://example.invalid/source",
        citation="Original test fixture; not actual experimental observations",
        version="test",
        origin="laboratory_experiment",
        creators=("Fixture author",),
        license_id="CC-BY-4.0",
        rights_evidence_url="https://example.invalid/rights",
        rights_review="reviewed_license",
        acquisition="allowed",
        checked_on="2026-10-28",
        intended_uses=("test",),
        limitations=("Authored source format",),
        split_unit="fixture",
        assets=(
            AssetSpec(
                filename=filename,
                url="https://example.invalid/fixture.xlsx",
                bytes=len(payload),
                checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
                checksum_origin="publisher_metadata",
            ),
        ),
    )


def workbook_bytes(*, times=None, changed_header=False, populated_second=False, lying_dimensions=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    book.create_sheet("Sheet2")
    headers = ["Timestamp", *(f"sensor_{i}" for i in range(25))]
    if changed_header:
        headers[0] = "Unknown clock"
    sheet.append(headers)
    times = times or [
        "2022-01-01 00:00:00",
        "2022-01-01 00:15:00",
        "2022-01-01 00:15:00",
        "2022-01-01 00:00:00",
    ]
    for index, time in enumerate(times):
        sheet.append([time, -1.0, 0.0, None, *([float(index)] * 21), None])
    if populated_second:
        book["Sheet2"].append(["unexpected source"])
    stream = io.BytesIO()
    book.save(stream)
    if not lying_dimensions:
        return stream.getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as src, zipfile.ZipFile(out, "w") as dest:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                import re

                raw = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1"', raw, count=1)
            dest.writestr(item, raw)
    return out.getvalue()


def uncertainty_bytes(*, dt=4.0, q=2.0, h=0.5, header_change=False):
    book = Workbook()
    book.remove(book.active)
    header = ["ΔT_w (K)", "U_ΔT_w (K)", "q'' (W/cm²)", "U_q'' (W/cm²)", "h (W/cm²·K)", "U_h (W/cm²·K)"]
    if header_change:
        header[2] = "q (unknown)"
    for name in ("1-g", "ug_Curve 1", "ug_Curve 2", "ug_Curve 3"):
        sheet = book.create_sheet(name)
        sheet.append(header)
        sheet.append([dt, 0.2, q, 0.1, h, 0.05])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize("lying_dimensions", [False, True])
def test_compressor_stream_full_scan_missing_signed_and_clock_meaning(tmp_path, lying_dimensions):
    raw = workbook_bytes(lying_dimensions=lying_dimensions)
    filename = "Centrifugal compressor _2022.xlsx"
    spec = source_spec(data.COMPRESSOR_ID, filename, raw)
    path = tmp_path / filename
    path.write_bytes(raw)
    rows = list(data.iter_refinery_observations(path, source_spec=spec))
    assert len(rows) == 4 and rows[0]["source_row"] == 2
    assert rows[0]["values"]["sensor_0"] == -1
    assert rows[0]["values"]["sensor_1"] == 0
    assert rows[0]["values"]["sensor_2"] is None and rows[0]["values"]["sensor_24"] is None
    assert rows[0]["timestamp_utc"] is None and rows[0]["units_resolved"] is False
    r = data.read_thermal_process_source(data.COMPRESSOR_ID, tmp_path, source_spec=spec)
    result = r["products"][filename]
    assert result["observations"] == 4 and result["sensor_columns"] == 25
    assert result["clock"]["repeated_adjacent"] == 1 and result["clock"]["decreasing_adjacent"] == 1
    assert result["clock"]["timezone"] is None and result["clock"]["utc_assigned"] is False
    assert result["channels"]["sensor_0"]["negative"] == 4
    assert result["model_mapping_status"] == "needs_data" and result["native_compressor_calls"] == 0


@pytest.mark.parametrize(
    "options",
    [{"changed_header": True}, {"populated_second": True}, {"times": ["2022-01-01T00:00:00+09:00"]}],
)
def test_unreviewed_source_schema_and_timezone_changes_refused(options):
    with pytest.raises(ValueError, match="review"):
        list(data._compressor_rows(workbook_bytes(**options)))


def test_nonparseable_clock_stays_missing_without_sorting():
    rows = list(data._compressor_rows(workbook_bytes(times=["unknown calendar"])))
    assert rows[0]["source_timestamp"] == "unknown calendar" and rows[0]["calendar_time_without_zone"] is None


def test_ln2_unit_conversion_retains_unknown_coverage_and_h_residual():
    r = data._ln2_curves(uncertainty_bytes(h=1.0))
    assert r["observations"] == 4 and r["fluid"] == "Nitrogen"
    row = r["records"][0]
    assert row["heat_flux_W_m2"] == 20000 and row["heat_transfer_coefficient_W_m2_K"] == 10000
    assert row["reported_U_heat_flux_W_m2"] == 1000 and row["reported_U_htc_W_m2_K"] == 500
    assert row["reported_U_coverage_factor"] is row["standard_joint_covariance"] is None
    assert row["source_htc_minus_q_over_deltaT_W_m2_K"] == 5000
    assert r["model_mapping_status"] == "out_of_scope_for_hydrogen_only_closures"
    assert r["native_hydrogen_model_calls"] == 0 and not r["source_values_repaired"]


@pytest.mark.parametrize("dt,q,h", [(0.0, 2.0, 0.5), (4.0, None, 0.5), (4.0, "=1+1", 0.5), (4.0, -2.0, -0.5)])
def test_ln2_unknown_formula_zero_superheat_and_signed_values_not_repaired(dt, q, h):
    r = data._ln2_curves(uncertainty_bytes(dt=dt, q=q, h=h))
    row = r["records"][0]
    assert row["source_values"][2] == q
    if q is None or isinstance(q, str):
        assert row["heat_flux_W_m2"] is None
    if dt == 0:
        assert row["source_htc_minus_q_over_deltaT_W_m2_K"] is None
    if q == -2:
        assert row["heat_flux_W_m2"] == -20000


def test_changed_heat_flux_units_refused_before_interpretation():
    with pytest.raises(ValueError, match="headers"):
        data._ln2_curves(uncertainty_bytes(header_change=True))


@pytest.mark.parametrize(
    "change", [{"license_id": "unknown"}, {"rights_review": "unresolved"}, {"acquisition": "metadata_only"}]
)
def test_pinned_source_rights_stay_required(tmp_path, change):
    raw = uncertainty_bytes()
    name = "Uncertainty Data_All curves.xlsx"
    (tmp_path / name).write_bytes(raw)
    spec = source_spec(data.LN2_ID, name, raw).model_copy(update=change)
    with pytest.raises(ValueError, match="rights"):
        data.read_thermal_process_source(data.LN2_ID, tmp_path, source_spec=spec)


def test_original_source_bytes_checked_and_rehashed_receipt_replayed(tmp_path):
    folder = tmp_path / data.LN2_ID
    folder.mkdir()
    raw = uncertainty_bytes()
    name = "Uncertainty Data_All curves.xlsx"
    spec = source_spec(data.LN2_ID, name, raw)
    path = folder / name
    path.write_bytes(raw)
    r = data.read_thermal_process_source(data.LN2_ID, folder, source_spec=spec)
    assert data.verify_thermal_process_record(r, source_root=tmp_path, source_spec=spec)
    for mutation in ("unit", "coverage", "fluid", "clock", "origin"):
        changed = deepcopy(r)
        product = changed["products"][name]
        if mutation == "unit":
            product["records"][0]["heat_flux_W_m2"] *= 10000
        elif mutation == "coverage":
            product["records"][0]["reported_U_coverage_factor"] = 2.0
        elif mutation == "fluid":
            product["fluid"] = "Hydrogen"
        elif mutation == "clock":
            product["records"][0]["timestamp_utc"] = "invented"
        else:
            changed["assets"][0]["dataset_origin"] = "simulation"
        changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
        with pytest.raises(ValueError):
            data.verify_thermal_process_record(changed, source_root=tmp_path, source_spec=spec)
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError, match="checksum"):
        data.read_thermal_process_source(data.LN2_ID, folder, source_spec=spec)


@pytest.mark.parametrize("issue", ["pages", "identity", "table1", "table2"])
def test_nasa_source_structure_failure_never_becomes_numeric_table(monkeypatch, issue):
    import pypdf

    texts = {
        0: "SELF-PRESSURIZATION SPHERICAL LIQUID HYDROGEN TANKAGE Aydelott",
        28: "TABLE I.",
        29: "TABLE II.",
    }
    if issue == "identity":
        texts[0] = "unknown report"
    if issue == "table1":
        texts[28] = "wrong table"
    if issue == "table2":
        texts[29] = "wrong table"
    pages = [
        SimpleNamespace(extract_text=lambda i=i: texts.get(i, ""))
        for i in range(49 if issue == "pages" else 50)
    ]
    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: SimpleNamespace(pages=pages))
    with pytest.raises(ValueError, match="review"):
        data._nasa_report(b"authored placeholder")


def test_nasa_local_hash_is_never_supplier_verification(tmp_path):
    raw = b"original prior NASA source"
    name = "19670028965.txt"
    path = tmp_path / name
    path.write_bytes(raw)
    spec = source_spec(data.NASA_ID, name, raw)
    spec = spec.model_copy(
        update={
            "license_id": "US-GOV-PUBLIC-USE-PERMITTED",
            "assets": (spec.assets[0].model_copy(update={"checksum_origin": "previous_acquisition"}),),
        }
    )
    payload, pin = data._pinned_asset(path, spec)
    assert payload == raw and pin["supplier_checksum_verified"] is False
    with pytest.raises(ValueError):
        data._pinned_asset(path, spec.model_copy(update={"license_id": "CC-BY-4.0"}))
