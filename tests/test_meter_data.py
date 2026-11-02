"""Authored source fixtures test units, provenance and withheld gas boundaries."""

import copy
import hashlib
import io
import json
import subprocess
import sys

import pytest
from openpyxl import Workbook

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import DatasetSpec
from pipeline_integrity.meter_data import (
    SHEETS,
    VORTEX_ID,
    VSL_ID,
    VSL_WORKBOOK,
    _calendar,
    _run_sheet,
    _vortex_text,
    parse_vortex_air_pdf,
    parse_vsl_calibration,
    read_vortex_air_meter,
    read_vsl_calibration,
    verify_meter_source_record,
    vortex_gas_meter_cases,
)


def workbook_bytes(*, unit="Reference Flowrate (kg/h)", missing_ambient_unit=False):
    book = Workbook()
    book.remove(book.active)
    for name in SHEETS:
        sheet = book.create_sheet(name)
        if name == "Column headers":
            sheet.append(["Authored schema fixture"])
        elif "All runs" in name:
            sheet.append(["Day 1"])
            ambient = "Atmospheric pressure: 90000" + ("" if missing_ambient_unit else " Pa")
            if name == 'KROHNE 4" Altosonic V All runs':
                sheet.append([ambient, *([None] * 9), "programmed pulse factor", 40, "pulse/L"])
            else:
                sheet.append([ambient, "100 pulses/kg"])
            sheet.append(
                [
                    "DATE (DD-MM-YY)",
                    "TIME START",
                    "TIME END",
                    unit,
                    "MUT Upstream Pressure (bar(g))",
                    "MUT Upstream Temperature  (°C)",
                    "MUT Downstream Pressure (bar(g))",
                    "MUT Downstream Temperature  (°C)",
                    "MUT Run Time (s)",
                    "MUT Pulse Count",
                    "MUT Flowrate (kg/h)",
                    "MUT Error (%)",
                ]
            )
            sheet.append(["21-04-26", "10:00:00", "10:02:10", 3600, 2, 20, 1.5, 19, 120, 12000, 3600, 0])
        else:
            sheet.append(["Authored meter calibration with LNG"])
            summary_header = [
                None,
                "Reference Flowrate (kg/h)",
                "Meter pressure (bar(g))",
                "Meter Temperature (°C)",
                "Meter error (%)",
                "Type A mean uA (%)",
                "Expanded U (%)",
                "Repeats (n)",
            ]
            sheet.append(summary_header)
            sheet.append(["Day 1", 3600, 2, 20, 0.01, 0.02, 0.4, 3])
            sheet.append(["Component", "Methane", "Nitrogen"])
            sheet.append(["Day 1", 98, 1])
            sheet.append(["Uncertainty (cmol/mol, k=2)", 0.2, 0.1])
            sheet.append(["Authored meter calibration with water"])
            sheet.append(summary_header)
            sheet.append([None, 1800, 1, 20, 0.02, 0.01, 0.1, 4])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def vortex_text():
    return "\n".join(
        [
            "Pressures (kPa)",
            "Temperatures (°C)",
            "Gauge Atmospheric Orifice plate air pressure difference",
            *[f"RH-{i} 120 20 10 90 0.8 0.3 0.1 25 30 15" for i in range(1, 6)],
        ]
    )


def pdf_bytes(text):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    lines = ["BT /F1 10 Tf 10 780 Td"]
    for line in text.splitlines():
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"({escaped}) Tj 0 -14 Td")
    lines.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(lines).encode("latin1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def source_spec(identity, files, **changes):
    body = dict(
        id=identity,
        title="Authored fixture",
        source="https://example.org/source",
        citation="Authored fixture, not original measurements",
        creators=["Fixture author"],
        version="fixture",
        origin="laboratory_experiment",
        license_id="CC-BY-4.0",
        rights_evidence_url="https://example.org/license",
        rights_review="reviewed_license",
        acquisition="allowed",
        checked_on="2026-10-27",
        intended_uses=["contract tests"],
        limitations=["authored observations"],
        split_unit="campaign",
        assets=[
            dict(
                filename=name,
                url="https://example.org/" + str(i),
                bytes=len(raw),
                checksum="sha256:" + hashlib.sha256(raw).hexdigest(),
                checksum_origin="publisher_metadata",
                default=True,
            )
            for i, (name, raw) in enumerate(files.items())
        ],
        raw_in_code_release=False,
    )
    body.update(changes)
    return DatasetSpec.model_validate_json(json.dumps(body))


@pytest.fixture
def sources(tmp_path):
    files = {
        VSL_ID: {VSL_WORKBOOK: workbook_bytes(), "ReadMe.pdf": b"authored pinned method fixture"},
        VORTEX_ID: {"Research data.pdf": pdf_bytes(vortex_text())},
    }
    specs = {}
    for identity, assets in files.items():
        folder = tmp_path / identity
        folder.mkdir()
        for name, raw in assets.items():
            (folder / name).write_bytes(raw)
        specs[identity] = source_spec(identity, assets)
    return tmp_path, specs


def test_liquid_units_and_fluid_label():
    result = parse_vsl_calibration(workbook_bytes())
    run = result["runs"][0]
    assert run["observations_si"]["reference_mass_flow_kg_s"] == 1
    assert run["observations_si"]["upstream_pressure_pa_abs"] == 290000
    assert run["observations_si"]["upstream_temperature_K"] == pytest.approx(293.15)
    assert run["clock"]["wall_duration_s"] == 130
    assert run["observations_si"]["counter_duration_s"] == 120
    assert run["wall_minus_counter_duration_s"] == 10
    assert run["clock"]["timezone"] is None
    assert result["runs"][-1]["fluid"] == "LNG"
    assert "LH2" in result["runs"][-1]["source_sheet"]
    assert result["claims"]["independent_gas_validation"] is False


def test_expanded_uncertainty_not_covariance():
    result = parse_vsl_calibration(workbook_bytes())
    uncertainty = result["summaries"][0]["uncertainty"]
    assert uncertainty["expanded_percent"] == 0.4
    assert uncertainty["scalar_standard_percent_from_U_over_k"] == 0.2
    assert uncertainty["coverage_factor"] == 2
    assert uncertainty["component_covariance"] is None
    assert uncertainty["degrees_of_freedom"] is None
    assert uncertainty["independence_assumed"] is False
    assert result["summaries"][2]["uncertainty"]["type_A_quantity"] == "relative_mean_pulse_factor"
    assert result["summaries"][1]["fluid"] == "water"
    assert result["summaries"][1]["clock"] is None
    composition = result["compositions"][0]
    assert composition["source_sum"] == 99
    assert sum(composition["mole_fraction_values"].values()) == 0.99
    assert composition["normalized"] is False


def test_missing_ambient_unit_is_unknown():
    result = parse_vsl_calibration(workbook_bytes(missing_ambient_unit=True))
    assert result["runs"][0]["source_atmospheric_pressure"]["unit"] is None
    assert result["runs"][0]["observations_si"]["upstream_pressure_pa_abs"] is None


def test_changed_units_refused():
    with pytest.raises(ValueError, match="header or unit"):
        parse_vsl_calibration(workbook_bytes(unit="Reference Flowrate (lb/h)"))


@pytest.mark.parametrize(
    "date,start,end",
    [
        ("21-04-25", "10:00:00", "10:01:00"),
        ("31-02-26", "10:00:00", "10:01:00"),
        ("21-04-26", "23:59:00", "00:01:00"),
        ("21-04-26", None, "10:01:00"),
    ],
)
def test_invalid_or_ambiguous_clock_preserved(date, start, end):
    clock = _calendar(date, start, end)
    assert clock["valid"] is False
    assert clock["date"] == date and clock["start"] == start and clock["end"] == end
    assert clock["wall_duration_s"] is None and clock["timezone"] is None


def test_formula_cache_and_invalid_values_preserved():
    from pipeline_integrity.soil_data import _workbook

    sheet = _workbook(workbook_bytes())[SHEETS[2]]
    sheet["source_cells"][3][3] = "=1/0"
    sheet["source_cells"][3][5] = {"source_type": "nonfinite", "text": "nan"}
    sheet["source_cells"][3][8] = 0
    run = _run_sheet(SHEETS[2], sheet)[0]
    assert run["observations_si"]["reference_mass_flow_kg_s"] is None
    assert run["observations_si"]["upstream_temperature_K"] is None
    assert run["derived_error_percent"] is None
    assert run["pulse_flow_residual_si"] is None
    assert "counter_duration_s" in run["invalid_domain_fields"]
    assert sheet["source_cells"][3][3] == "=1/0"


def test_vortex_actual_pdf_reader_and_si():
    result = parse_vortex_air_pdf(pdf_bytes(vortex_text()))
    branch = result["observations"][0]["branches"][0]
    assert len(result["observations"]) == 5
    assert branch["upstream_pressure_pa_abs"] == 210000
    assert branch["orifice_pressure_difference_pa"] == 800
    assert branch["upstream_temperature_K"] == pytest.approx(298.15)
    assert branch["downstream_pressure_pa_abs"] is None
    assert branch["time_s"] is None and branch["gas_components"] is None
    assert result["claims"]["dry_gas_assumed"] is False


@pytest.mark.parametrize(
    "old,new",
    [
        ("(kPa)", "(bar)"),
        ("Gauge Atmospheric", "Absolute Atmospheric"),
        ("RH-5", "RH-6"),
        ("120 20", "nan 20"),
        ("120 20", "120"),
        ("90 0.8", "-900 0.8"),
        ("25 30", "-300 30"),
    ],
)
def test_vortex_invalid_layout_values_units(old, new):
    with pytest.raises(ValueError):
        _vortex_text(vortex_text().replace(old, new))


@pytest.mark.parametrize("payload", [b"", b"x" * 1_000_001], ids=["empty", "oversized"])
def test_pdf_byte_budget(payload):
    with pytest.raises(ValueError, match="1 MB"):
        parse_vortex_air_pdf(payload)


def test_pinned_reader_and_original_source_recheck(sources):
    root, specs = sources
    result = read_vsl_calibration(root / VSL_ID, source_spec=specs[VSL_ID])
    assert verify_meter_source_record(result, source_root=root, source_spec=specs[VSL_ID])
    path = root / VSL_ID / VSL_WORKBOOK
    payload = bytearray(path.read_bytes())
    payload[-10] ^= 1
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="checksum"):
        verify_meter_source_record(result, source_root=root, source_spec=specs[VSL_ID])


@pytest.mark.parametrize(
    "changes",
    [
        {"license_id": "LicenseRef-Unresolved"},
        {"origin": "simulation"},
        {"acquisition": "metadata_only"},
    ],
)
def test_unresolved_rights_or_origin_refused(sources, changes):
    root, specs = sources
    spec = DatasetSpec.model_validate_json(
        json.dumps(dict(json.loads(specs[VSL_ID].model_dump_json()), **changes))
    )
    with pytest.raises(ValueError):
        read_vsl_calibration(root / VSL_ID, source_spec=spec)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(origin="measured"),
        lambda r: r["summaries"][0]["uncertainty"].update(coverage_factor=1),
        lambda r: r["summaries"][0]["uncertainty"].update(component_covariance=[[0]]),
        lambda r: r["runs"][-1].update(fluid="Hydrogen"),
        lambda r: r["runs"][0]["clock"].update(timezone="UTC", utc_assigned=True),
        lambda r: r["provenance"].update(dataset_origin="simulation"),
        lambda r: r["claims"].update(independent_gas_validation=True),
        lambda r: r.update(extra_approval=True),
    ],
)
def test_rehashed_liquid_meaning_changes_refused(sources, mutation):
    root, specs = sources
    result = read_vsl_calibration(root / VSL_ID, source_spec=specs[VSL_ID])
    mutation(result)
    result["record_digest"] = digest({k: v for k, v in result.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="differs"):
        verify_meter_source_record(result, source_root=root, source_spec=specs[VSL_ID])


@pytest.mark.parametrize(
    "key,value", [("gas_volume_basis", "dry"), ("time_s", 1), ("independent_flow_reference", 0.2)]
)
def test_rehashed_vortex_inference_refused(sources, key, value):
    root, specs = sources
    result = read_vortex_air_meter(root / VORTEX_ID / "Research data.pdf", source_spec=specs[VORTEX_ID])
    result["observations"][0]["branches"][0][key] = value
    result["record_digest"] = digest({k: v for k, v in result.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="differs"):
        vortex_gas_meter_cases(result, source_root=root, source_spec=specs[VORTEX_ID])


def test_missing_boundaries_are_actual_withheld_native_requests(sources, monkeypatch):
    from pipeline_integrity import gas_meter

    def forbidden():
        raise AssertionError("incomplete source must not reach thermodynamic or meter backend")

    monkeypatch.setattr(gas_meter, "_backend", forbidden)
    root, specs = sources
    source = read_vortex_air_meter(root / VORTEX_ID / "Research data.pdf", source_spec=specs[VORTEX_ID])
    pairs = vortex_gas_meter_cases(source, source_root=root, source_spec=specs[VORTEX_ID])
    assert len(pairs) == 15
    for request, context in pairs:
        assert request.time_s is None and request.components is None and request.geometry is None
        assert request.event_id.startswith("RH-")
        assert context.origin == "laboratory"
        assert context.evidence[0].sha256 == source["provenance"]["sha256"]
        result = gas_meter.evaluate_gas_meter(request, context)
        assert result["status"] == "needs_data"
        assert not result["native_called"] and not result["meter_called"]
        assert gas_meter.verify_gas_meter_record(result, request=request, context=context)


def test_module_import_keeps_readers_lazy():
    code = "import sys; import pipeline_integrity.meter_data; assert 'openpyxl' not in sys.modules; assert 'pypdf' not in sys.modules; assert 'CoolProp' not in sys.modules; assert 'fluids' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_digest_without_original_bytes_is_not_accepted(sources):
    root, specs = sources
    result = read_vsl_calibration(root / VSL_ID, source_spec=specs[VSL_ID])
    broken = copy.deepcopy(result)
    broken["record_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        verify_meter_source_record(broken, source_root=root, source_spec=specs[VSL_ID])
    (root / VSL_ID / "ReadMe.pdf").unlink()
    with pytest.raises(FileNotFoundError):
        verify_meter_source_record(result, source_root=root, source_spec=specs[VSL_ID])
