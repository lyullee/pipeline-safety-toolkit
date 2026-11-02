"""Synthetic OOXML fixtures only; no publisher observation rows are embedded."""

import hashlib
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from pipeline_integrity.potash_elbow_cp import (
    DATASET_ID,
    PotashElbowCPError,
    read_potash_elbow_cp,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W)


def _tag(name):
    return f"{{{W}}}{name}"


def _cell(row, value):
    cell = ET.SubElement(row, _tag("tc"))
    paragraph = ET.SubElement(cell, _tag("p"))
    run = ET.SubElement(paragraph, _tag("r"))
    text = ET.SubElement(run, _tag("t"))
    text.text = str(value)


def _paragraph(body, value):
    paragraph = ET.SubElement(body, _tag("p"))
    run = ET.SubElement(paragraph, _tag("r"))
    text = ET.SubElement(run, _tag("t"))
    text.text = value


def _synthetic_docx(path, *, invalid_header=False, duplicate_sand=False):
    document = ET.Element(_tag("document"))
    body = ET.SubElement(document, _tag("body"))
    specifications = (
        ("A.1", 2.5, 25, 9, 0),
        ("A.1", 2.5, 11, 9, 25),
        ("A.2", 3.0, 36, 4, 0),
        ("A.3", 3.5, 36, 3, 0),
        ("A.4", 4.0, 36, 3, 0),
    )
    for table_index, (label, velocity, row_count, replicate_count, start) in enumerate(
        specifications, start=1
    ):
        continued = " continued" if table_index == 2 else ""
        _paragraph(body, f"Table {label} synthetic caption at {velocity:.1f} m/s{continued}.")
        table = ET.SubElement(body, _tag("tbl"))
        headers = ET.SubElement(table, _tag("tr"))
        _cell(headers, "Slurry Concentration (wt.%)")
        _cell(headers, "Cathodic Protection Current Density (mA/cm2)")
        _cell(headers, "Average Current Density (mA/cm2)")
        _cell(headers, "Range (mA/cm2)" if invalid_header else "95% Confidence Interval (mA/cm2)")
        trial_header = ET.SubElement(table, _tag("tr"))
        _cell(trial_header, "Experimental Trials")
        trial_ids = ET.SubElement(table, _tag("tr"))
        _cell(trial_ids, "")
        for replicate in range(1, replicate_count + 1):
            _cell(trial_ids, replicate)
        _cell(trial_ids, "")
        _cell(trial_ids, "")
        for offset in range(row_count):
            row = ET.SubElement(table, _tag("tr"))
            concentration = start + offset
            if duplicate_sand and table_index == 1 and offset == 1:
                concentration = start
            _cell(row, concentration)
            for replicate in range(replicate_count):
                _cell(row, f"{1.0 + replicate / 10:.1f}")
            # Deliberately distinct from the synthetic replicate average: retain the reported field.
            _cell(row, f"{100.0 + concentration:.1f}")
            _cell(row, "0.25")
    xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)


def test_synthetic_docx_merges_fragments_and_keeps_reported_summary_separate():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.docx"
        _synthetic_docx(path)
        source_pin = hashlib.sha256(path.read_bytes()).hexdigest()
        result = read_potash_elbow_cp(path, expected_sha256=source_pin)

    assert result["dataset_id"] == DATASET_ID
    assert result["source_sha256"] == source_pin
    assert result["row_count"] == 144
    assert [
        (item["velocity_table_id"], item["fragment_index"])
        for item in result["table_fragments"]
    ] == [("A.1", 1), ("A.1", 2), ("A.2", 1), ("A.3", 1), ("A.4", 1)]
    first, second_fragment = result["rows"][0], result["rows"][25]
    assert first["row_id"] == "source_table_1:row_4"
    assert second_fragment["row_id"] == "source_table_2:row_4"
    assert first["velocity_m_s"] == second_fragment["velocity_m_s"] == 2.5
    assert first["replicate_current_density_mA_cm2"] == [1.0 + i / 10 for i in range(9)]
    assert first["reported_mean_current_density_mA_cm2"] == 100.0
    assert first["reported_mean_current_density_mA_cm2"] != sum(
        first["replicate_current_density_mA_cm2"]
    ) / 9
    assert first["reported_95_ci_mA_cm2"] == 0.25
    assert first["replicate_current_density_A_m2"][0] == 10.0
    assert first["reported_mean_current_density_A_m2"] == 1000.0
    assert first["reported_95_ci_A_m2"] == 2.5
    assert first["source_cells_text"][-2:] == ["100.0", "0.25"]
    assert not result["replicates_are_independent_field_trials"]
    assert not result["velocity_conditions_are_independent_field_trials"]
    assert result["attribution"]["creator"] == "Michael Trask"
    assert "A/m2 conversion" in result["attribution"]["modification_notice"]


def test_reader_rejects_an_unmatched_source_checksum():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.docx"
        _synthetic_docx(path)
        with pytest.raises(PotashElbowCPError, match="size"):
            read_potash_elbow_cp(path)
        with pytest.raises(PotashElbowCPError, match="SHA-256"):
            read_potash_elbow_cp(path, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"invalid_header": True}, "header does not match"),
        ({"duplicate_sand": True}, "duplicate sand concentration"),
    ],
)
def test_reader_rejects_header_drift_and_duplicate_conditions(options, message):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.docx"
        _synthetic_docx(path, **options)
        source_pin = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(PotashElbowCPError, match=message):
            read_potash_elbow_cp(path, expected_sha256=source_pin)
