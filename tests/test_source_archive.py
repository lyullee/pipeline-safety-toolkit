"""Publisher pin, bounded snapshot and archive-path checks shared by source readers."""

import hashlib
import io
import stat
import zipfile
from types import SimpleNamespace

import pytest

from pipeline_integrity import source_archive as module


def source(
    tmp_path,
    monkeypatch,
    *,
    name="record.txt",
    content=b"original",
    member=None,
    checksum_origin="publisher_metadata",
):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if member is None:
            # Windows ZipInfo construction normalizes backslashes. Override
            # the write header explicitly to test the actual malformed bytes.
            member = zipfile.ZipInfo(name)
            member.filename = member.orig_filename = name
        archive.writestr(member, content)
    raw = buffer.getvalue()
    path = tmp_path / "sample.zip"
    path.write_bytes(raw)
    asset = SimpleNamespace(
        filename="sample.zip",
        bytes=len(raw),
        checksum="sha256:" + hashlib.sha256(raw).hexdigest(),
        checksum_origin=checksum_origin,
    )
    dataset = SimpleNamespace(
        id="example",
        acquisition="allowed",
        rights_review="reviewed_license",
        assets=(asset,),
        citation="Authored ZIP reference",
        license_id="MIT",
        version="test",
    )
    monkeypatch.setattr(module, "catalog", lambda: (dataset,))
    return path, asset, dataset


def test_source_is_a_pinned_snapshot_without_extraction(tmp_path, monkeypatch):
    path, _, _ = source(tmp_path, monkeypatch)
    with module.pinned_source_zip(path, "example") as (archive, meta):
        path.write_bytes(b"changed on disk")
        assert archive.read("record.txt") == b"original"
        assert meta["supplier_checksum_verified"]
        assert meta["checksum_origin"] == "publisher_metadata"
    assert not (tmp_path / "record.txt").exists()


def test_previous_local_checksum_cannot_be_labelled_supplier_verification(tmp_path, monkeypatch):
    path, _, _ = source(tmp_path, monkeypatch, checksum_origin="previous_acquisition")
    with pytest.raises(ValueError, match="reviewed rights"):
        with module.pinned_source_zip(path, "example"):
            pass


@pytest.mark.parametrize("name", ["../record.txt", "/record.txt", "folder\\record.txt", "C:record.txt"])
def test_unsafe_members_are_rejected_before_read(tmp_path, monkeypatch, name):
    path, _, _ = source(tmp_path, monkeypatch, name=name)
    with pytest.raises(ValueError, match="unsafe"):
        with module.pinned_source_zip(path, "example"):
            pass


def test_symbolic_link_is_not_treated_as_observation_text(tmp_path, monkeypatch):
    member = zipfile.ZipInfo("linked.txt")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    path, _, _ = source(tmp_path, monkeypatch, member=member)
    with pytest.raises(ValueError, match="unsafe"):
        with module.pinned_source_zip(path, "example"):
            pass


@pytest.mark.parametrize(
    "budget",
    [
        {"maximum_archive_bytes": 1},
        {"maximum_member_bytes": 1},
        {"maximum_total_bytes": 1},
        {"maximum_members": False},
    ],
)
def test_budget_limits_are_explicit_and_not_bool(tmp_path, monkeypatch, budget):
    path, _, _ = source(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        with module.pinned_source_zip(path, "example", **budget):
            pass


def test_unreviewed_rights_and_corrupt_asset_are_rejected(tmp_path, monkeypatch):
    path, asset, dataset = source(tmp_path, monkeypatch)
    dataset.rights_review = "unresolved"
    with pytest.raises(ValueError, match="reviewed rights"):
        with module.pinned_source_zip(path, "example"):
            pass
    dataset.rights_review = "reviewed_license"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError, match="checksum differs"):
        with module.pinned_source_zip(path, "example"):
            pass



def test_streamed_source_zip_is_file_backed_and_reports_checksum_identity(tmp_path, monkeypatch):
    path, _, _ = source(tmp_path, monkeypatch, name="nested/record.txt")
    with module.pinned_source_zip_stream(
        path, "example", maximum_archive_bytes=1024 * 1024,
        maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8, maximum_central_directory_bytes=4096,
    ) as (archive, meta):
        assert not isinstance(archive.fp, io.BytesIO)
        assert archive.read("nested/record.txt") == b"original"
        assert meta["archive_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert meta["archive_storage"] == "file_backed_same_open_handle"
        assert meta["all_member_crc_verified"] is False


def test_streamed_source_zip_rejects_changes_during_member_access(tmp_path, monkeypatch):
    path, _, _ = source(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="identity changed|bytes changed"):
        with module.pinned_source_zip_stream(
            path, "example", maximum_archive_bytes=1024 * 1024,
            maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8, maximum_central_directory_bytes=4096,
        ) as (archive, _):
            assert archive.read("record.txt") == b"original"
            path.write_bytes(path.read_bytes() + b"changed")


def test_streamed_source_zip_requires_explicit_deferred_capacity_opt_in(tmp_path, monkeypatch):
    path, _, dataset = source(tmp_path, monkeypatch)
    dataset.acquisition = "deferred_capacity"
    args = dict(
        maximum_archive_bytes=1024 * 1024, maximum_member_bytes=1024,
        maximum_total_bytes=4096, maximum_members=8, maximum_central_directory_bytes=4096,
    )
    with pytest.raises(ValueError, match="explicit capacity opt-in"):
        with module.pinned_source_zip_stream(path, "example", **args):
            pass
    with module.pinned_source_zip_stream(path, "example", allow_deferred_capacity=True, **args) as (archive, _):
        assert archive.read("record.txt") == b"original"



def test_streamed_source_zip_checks_central_directory_budget_before_open(tmp_path, monkeypatch):
    path, _, _ = source(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="central-directory entry or byte budget"):
        with module.pinned_source_zip_stream(
            path, "example", maximum_archive_bytes=1024 * 1024,
            maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8,
            maximum_central_directory_bytes=1,
        ):
            pass


def _zip64_fixture(path, *, tamper_counts=False):
    raw_zip = bytearray(path.read_bytes())
    eocd = raw_zip.rfind(b"PK\x05\x06")
    assert eocd >= 0
    entries = int.from_bytes(raw_zip[eocd + 10:eocd + 12], "little")
    directory_size = int.from_bytes(raw_zip[eocd + 12:eocd + 16], "little")
    directory_offset = int.from_bytes(raw_zip[eocd + 16:eocd + 20], "little")
    zip64_end_offset = eocd
    zip64_record = (
        b"PK\x06\x06" + (44).to_bytes(8, "little") + b"\x2d\x00\x2d\x00"
        + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00"
        + entries.to_bytes(8, "little") * 2
        + directory_size.to_bytes(8, "little") + directory_offset.to_bytes(8, "little")
    )
    locator = b"PK\x06\x07" + b"\x00\x00\x00\x00" + zip64_end_offset.to_bytes(8, "little") + b"\x01\x00\x00\x00"
    raw_zip[eocd + 4:eocd + 20] = (
        b"\x00\x00\x00\x00" + entries.to_bytes(2, "little") * 2
        + directory_size.to_bytes(4, "little") + directory_offset.to_bytes(4, "little")
    )
    if tamper_counts:
        # Keep the single-disk legacy counts internally consistent, but make
        # both disagree with the separate ZIP64 record. Do this after writing
        # the legacy fields so the intended corruption is not overwritten.
        raw_zip[eocd + 8:eocd + 12] = (entries + 1).to_bytes(2, "little") * 2
    raw_zip[eocd:eocd] = zip64_record + locator
    path.write_bytes(raw_zip)
    return bytes(raw_zip)


def test_zip64_locator_is_honored_even_without_sentinel_counts(tmp_path, monkeypatch):
    path, asset, _ = source(tmp_path, monkeypatch)
    raw = _zip64_fixture(path)
    asset.bytes = len(raw)
    asset.checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
    with module.pinned_source_zip_stream(
        path, "example", maximum_archive_bytes=1024 * 1024,
        maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8,
        maximum_central_directory_bytes=4096,
    ) as (archive, _):
        assert archive.read("record.txt") == b"original"


def test_zip64_end_record_must_match_legacy_counts(tmp_path, monkeypatch):
    path, asset, _ = source(tmp_path, monkeypatch)
    raw = _zip64_fixture(path, tamper_counts=True)
    asset.bytes = len(raw)
    asset.checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="ZIP64 entry counts disagree"):
        with module.pinned_source_zip_stream(
            path, "example", maximum_archive_bytes=1024 * 1024,
            maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8,
            maximum_central_directory_bytes=4096,
        ):
            pass


def test_zip64_extensible_record_length_does_not_trigger_large_allocation(tmp_path, monkeypatch):
    path, asset, _ = source(tmp_path, monkeypatch)
    raw = bytearray(_zip64_fixture(path))
    locator = raw.rfind(b"PK\x06\x07")
    zip64_offset = int.from_bytes(raw[locator + 8:locator + 16], "little")
    raw[zip64_offset + 4:zip64_offset + 12] = (2**63).to_bytes(8, "little")
    path.write_bytes(raw)
    asset.bytes = len(raw)
    asset.checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="ZIP64 end record boundary"):
        with module.pinned_source_zip_stream(
            path, "example", maximum_archive_bytes=1024 * 1024,
            maximum_member_bytes=1024, maximum_total_bytes=4096, maximum_members=8,
            maximum_central_directory_bytes=4096,
        ):
            pass
