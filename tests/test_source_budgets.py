"""Explicit source-size budgets never relax provenance or archive safeguards."""

import hashlib
import io
import zipfile

import pytest
from openpyxl import Workbook

from pipeline_integrity.datasets import AssetSpec, DatasetSpec
from pipeline_integrity.soil_data import _bounded_xlsx, _pinned, _workbook


def original_spec(payload):
    return DatasetSpec(
        id="budget_fixture",
        title="Authored byte-budget fixture",
        source="https://example.invalid/source",
        citation="Original synthetic fixture",
        version="1",
        origin="laboratory_experiment",
        license_id="CC-BY-4.0",
        rights_evidence_url="https://example.invalid/rights",
        rights_review="reviewed_license",
        acquisition="allowed",
        checked_on="2026-10-27",
        intended_uses=("test",),
        limitations=("No actual public observations",),
        split_unit="fixture",
        assets=(
            AssetSpec(
                filename="fixture.xlsx",
                url="https://example.invalid/fixture.xlsx",
                bytes=len(payload),
                checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
                checksum_origin="publisher_metadata",
            ),
        ),
    )


def test_explicit_pin_budget_and_size_checksum_rights_are_independent(tmp_path):
    payload = b"authored original bytes"
    path = tmp_path / "fixture.xlsx"
    path.write_bytes(payload)
    spec = original_spec(payload)
    with pytest.raises(ValueError, match="byte/checksum"):
        _pinned(path, spec.id, source_spec=spec, maximum_bytes=len(payload) - 1)
    recovered, provenance = _pinned(path, spec.id, source_spec=spec, maximum_bytes=len(payload))
    assert recovered == payload and provenance["supplier_checksum_verified"]
    for changed in (
        spec.model_copy(update={"rights_review": "unresolved"}),
        spec.model_copy(update={"license_id": "unknown"}),
        spec.model_copy(update={"acquisition": "metadata_only"}),
    ):
        with pytest.raises(ValueError, match="rights"):
            _pinned(path, spec.id, source_spec=changed, maximum_bytes=1_000_000)
    path.write_bytes(payload[:-1] + b"X")
    with pytest.raises(ValueError, match="checksum"):
        _pinned(path, spec.id, source_spec=spec, maximum_bytes=1_000_000)


@pytest.mark.parametrize("invalid", [False, True, 0, -1, 1.5, float("inf"), "100"])
def test_invalid_resource_budgets_refused_before_io(invalid, tmp_path):
    with pytest.raises(ValueError, match="positive integer"):
        _pinned(tmp_path / "missing", "unread", maximum_bytes=invalid)
    for key in ("maximum_bytes", "maximum_uncompressed_bytes", "maximum_member_bytes"):
        with pytest.raises(ValueError, match="positive integer"):
            _bounded_xlsx(b"not a zip", **{key: invalid})
    for key in ("maximum_cells", "maximum_sheets"):
        with pytest.raises(ValueError, match="positive integer"):
            _workbook(b"not a zip", **{key: invalid})


def test_compressed_member_and_total_budgets_are_separate():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("one.xml", b"X" * 2000)
        archive.writestr("two.xml", b"Y" * 2000)
    payload = stream.getvalue()
    assert len(payload) < 1000
    _bounded_xlsx(payload, maximum_bytes=1000, maximum_member_bytes=2000, maximum_uncompressed_bytes=4000)
    with pytest.raises(ValueError, match="original workbook"):
        _bounded_xlsx(payload, maximum_bytes=len(payload) - 1)
    with pytest.raises(ValueError, match="uncompressed"):
        _bounded_xlsx(payload, maximum_uncompressed_bytes=3999)
    with pytest.raises(ValueError, match="member byte"):
        _bounded_xlsx(payload, maximum_member_bytes=1999)


@pytest.mark.parametrize("name", ["../outside.xml", "/absolute.xml", "C:/file.xml", "a\\b.xml"])
def test_increased_budget_preserves_member_path_refusal(name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, "authored")
    payload = stream.getvalue()
    if "\\" in name:
        # Windows ZipInfo normalizes separators when authoring a fixture. Restore
        # the actual foreign archive name in both local/central headers.
        payload = payload.replace(name.replace("\\", "/").encode(), name.encode())
    with pytest.raises(ValueError, match="unsafe"):
        _bounded_xlsx(
            payload,
            maximum_bytes=16_000_000,
            maximum_uncompressed_bytes=128_000_000,
            maximum_member_bytes=64_000_000,
        )


def test_explicit_workbook_cell_and_sheet_budgets_preserve_source_cells():
    book = Workbook()
    book.active.append([0, None, -1])
    book.create_sheet("second").append(["=1+2"])
    buffer = io.BytesIO()
    book.save(buffer)
    payload = buffer.getvalue()
    with pytest.raises(ValueError, match="cell/sheet"):
        _workbook(payload, maximum_cells=3)
    with pytest.raises(ValueError, match="cell/sheet"):
        _workbook(payload, maximum_sheets=1)
    values = _workbook(payload, maximum_cells=4, maximum_sheets=2)
    assert values["Sheet"]["source_cells"] == [[0, None, -1]]
    assert values["second"]["source_cells"] == [["=1+2"]]
    assert values["second"]["formula_cache"] == {"A1": None}
