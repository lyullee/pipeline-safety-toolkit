"""Synthetic OOXML fixtures only; no publisher observation rows are embedded."""

import hashlib
import xml.etree.ElementTree as ET
import zipfile

import pytest

from pipeline_integrity.potash_pipe_cp import (
    DATASET_ID,
    PotashPipeCPError,
    read_potash_pipe_cp,
    to_signal_frames,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W)

def tag(name):
    return f"{{{W}}}{name}"

def cell(row, value):
    item = ET.SubElement(row, tag("tc"))
    paragraph = ET.SubElement(item, tag("p"))
    run = ET.SubElement(paragraph, tag("r"))
    text = ET.SubElement(run, tag("t"))
    text.text = str(value)

def synthetic_docx(path, *, bad_header=False, bad_time=False):
    doc = ET.Element(tag("document")); body = ET.SubElement(doc, tag("body"))
    table = ET.SubElement(body, tag("tbl"))
    row = ET.SubElement(table, tag("tr"))
    for label in ("Time (min)", "Protection Current Density (mA/cm2)", "Average (mA/cm2)",
                  "Range (mA/cm2)" if bad_header else "95% Confidence Interval (mA/cm2)"):
        cell(row, label)
    row = ET.SubElement(table, tag("tr"))
    for label in ("", "Trial 1", "Trial 2", "Trial 3", "", ""):
        cell(row, label)
    for index in range(20):
        row = ET.SubElement(table, tag("tr"))
        minute = index * 5 + (1 if bad_time and index == 3 else 0)
        for value in (minute, 10, 12, 14, 12, 1):
            cell(row, value)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", ET.tostring(doc, encoding="utf-8", xml_declaration=True))

def test_synthetic_reader_preserves_replicates_reported_summary_and_units(tmp_path):
    path = tmp_path / "synthetic.docx"; synthetic_docx(path)
    result = read_potash_pipe_cp(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert result["dataset_id"] == DATASET_ID
    assert result["row_count"] == 20 and result["replicate_count_per_time"] == 3
    first = result["points"][0]
    assert first["time_min"] == 0
    assert first["replicate_current_density_mA_cm2"] == [10, 12, 14]
    assert first["reported_mean_current_density_mA_cm2"] == 12
    assert first["reported_mean_current_density_A_m2"] == 120
    assert first["reported_95_ci_A_m2"] == 10
    assert first["reported_statistics_recomputed"] is False
    assert result["source_checksum_verified"] is True
    assert result["field_transfer_or_cathodic_protection_qualification"] is False

@pytest.mark.parametrize(("options", "message"), [
    ({"bad_header": True}, "schema"),
    ({"bad_time": True}, "five-minute grid"),
])
def test_reader_rejects_schema_or_time_grid_drift(tmp_path, options, message):
    path = tmp_path / "synthetic.docx"; synthetic_docx(path, **options)
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PotashPipeCPError, match=message):
        read_potash_pipe_cp(path, expected_sha256=pin)

def test_reader_requires_the_explicit_matching_source_pin(tmp_path):
    path = tmp_path / "synthetic.docx"; synthetic_docx(path)
    with pytest.raises(PotashPipeCPError, match="SHA-256"):
        read_potash_pipe_cp(path, expected_sha256="0" * 64)
    with pytest.raises(PotashPipeCPError, match="explicit lowercase"):
        read_potash_pipe_cp(path, expected_sha256=None)


def test_signal_adapter_retains_laboratory_provenance_and_requires_declared_series():
    dataset = {
        "schema": "pipeline_integrity.potash_pipe_cp.v1",
        "dataset_id": DATASET_ID,
        "source_sha256": "de262b94567fb6d72c6ed520cdbecb5660d68579e266e33d0b33c023def9ed2f",
        "source_checksum_verified": True,
        "license_id": "CC-BY-4.0",
        "time_basis": "minutes_from_exposure_start_as_published",
        "points": [
            {"time_min": i * 5, "reported_mean_current_density_A_m2": 120.0,
             "replicate_current_density_A_m2": [100.0, 120.0, 140.0]}
            for i in range(20)
        ],
    }
    frames, evidence = to_signal_frames(dataset, clock_id="lab_clock", stream_id="potash_cp")
    assert len(frames) == 20 and frames[-1].time_s == 5700.0
    assert frames[0].origin == "laboratory"
    assert frames[0].values["protective_demand_current_density"] == 120.0
    assert evidence.sha256 == dataset["source_sha256"]
    assert evidence.license_id == "CC-BY-4.0"
    selected, replicate_evidence = to_signal_frames(
        dataset, clock_id="lab_clock", stream_id="potash_cp", statistic="replicate", replicate_index=2
    )
    assert selected[0].values["protective_demand_current_density"] == 140.0
    assert "replicate 3" in replicate_evidence.locator


def test_signal_adapter_rejects_unverified_or_ambiguous_input():
    import pytest
    from pipeline_integrity.potash_pipe_cp import PotashPipeCPError

    dataset = {
        "schema": "pipeline_integrity.potash_pipe_cp.v1", "dataset_id": DATASET_ID,
        "source_sha256": "de262b94567fb6d72c6ed520cdbecb5660d68579e266e33d0b33c023def9ed2f",
        "source_checksum_verified": True, "license_id": "CC-BY-4.0",
        "time_basis": "minutes_from_exposure_start_as_published",
        "points": [{"time_min": i * 5, "reported_mean_current_density_A_m2": 120.0,
                    "replicate_current_density_A_m2": [100.0, 120.0, 140.0]} for i in range(20)],
    }
    with pytest.raises(PotashPipeCPError, match="checksum-verified"):
        to_signal_frames({**dataset, "source_checksum_verified": False}, clock_id="t", stream_id="s")
    with pytest.raises(PotashPipeCPError, match="replicate_index"):
        to_signal_frames(dataset, clock_id="t", stream_id="s", statistic="replicate")
