"""Bounded, checksum-pinned ZIP access shared by heterogeneous source readers."""

import hashlib
import io
import os
import stat
import struct
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .datasets import catalog


@contextmanager
def pinned_source_zip(
    path,
    dataset_id,
    *,
    asset_filename=None,
    maximum_archive_bytes=128 * 1024 * 1024,
    maximum_member_bytes=32 * 1024 * 1024,
    maximum_total_bytes=128 * 1024 * 1024,
    maximum_members=10000,
):
    """Verify publisher size/checksum before opening a bounded in-memory snapshot.

    No archived path is extracted or executed. Readers still validate their
    own content schemas and retain original order, units and missing values.
    """
    budgets = (maximum_archive_bytes, maximum_member_bytes, maximum_total_bytes, maximum_members)
    if any(type(v) is not int or v <= 0 for v in budgets):
        raise ValueError("positive integer ZIP budgets are required")
    path = Path(path)
    selected = [d for d in catalog() if d.id == dataset_id]
    if len(selected) != 1:
        raise ValueError("select one reviewed source dataset")
    dataset = selected[0]
    filename = path.name if asset_filename is None else asset_filename
    assets = [a for a in dataset.assets if a.filename == filename]
    if (
        dataset.acquisition != "allowed"
        or dataset.rights_review != "reviewed_license"
        or len(assets) != 1
        or assets[0].bytes is None
        or assets[0].checksum is None
        or assets[0].checksum_origin != "publisher_metadata"
    ):
        raise ValueError("ZIP source requires reviewed rights and pinned size/checksum")
    asset = assets[0]
    if path.stat().st_size != asset.bytes or asset.bytes > maximum_archive_bytes:
        raise ValueError("source ZIP byte size or archive budget differs")
    with path.open("rb") as stream:
        raw = stream.read(asset.bytes + 1)
    algorithm, expected = asset.checksum.split(":", 1)
    if len(raw) != asset.bytes or hashlib.new(algorithm, raw).hexdigest() != expected:
        raise ValueError("source ZIP checksum differs from the reviewed supplier asset")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > maximum_members:
            raise ValueError("source ZIP member budget exceeded")
        names, total = set(), 0
        for member in infos:
            name = member.filename
            logical = PurePosixPath(name)
            if (
                not name
                or member.orig_filename != name
                or logical.is_absolute()
                or ".." in logical.parts
                or "\\" in name
                or ":" in name
                or name in names
                or stat.S_ISLNK(member.external_attr >> 16)
                or member.flag_bits & 1
            ):
                raise ValueError("source ZIP contains an unsafe, duplicate or encrypted member")
            if member.file_size > maximum_member_bytes:
                raise ValueError("source ZIP member byte budget exceeded")
            names.add(name)
            total += member.file_size
        if total > maximum_total_bytes:
            raise ValueError("source ZIP uncompressed byte budget exceeded")
        yield (
            archive,
            {
                "dataset_id": dataset.id,
                "citation": dataset.citation,
                "license_id": dataset.license_id,
                "source_version": dataset.version,
                "archive_filename": asset.filename,
                "archive_bytes": len(raw),
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "supplier_checksum": asset.checksum,
                "checksum_origin": asset.checksum_origin,
                "supplier_checksum_verified": True,
                "member_count": len(infos),
                "uncompressed_bytes": total,
            },
        )


@contextmanager
def pinned_source_zip_stream(
    path,
    dataset_id,
    *,
    maximum_archive_bytes,
    maximum_member_bytes,
    maximum_total_bytes,
    maximum_members,
    maximum_central_directory_bytes,
    asset_filename=None,
    allow_deferred_capacity=False,
):
    """Verify a large pinned ZIP on one open handle without copying the archive to RAM.

    This inventories central-directory metadata only. CRC is checked by ZipFile
    when a consumer reads an individual member; unread members are not claimed
    as CRC-verified. Callers must supply explicit archive/member/count budgets.
    """
    budgets = (maximum_archive_bytes, maximum_member_bytes, maximum_total_bytes, maximum_members, maximum_central_directory_bytes)
    if any(type(value) is not int or value <= 0 for value in budgets):
        raise ValueError("positive integer archive, member and count budgets are required")
    if type(allow_deferred_capacity) is not bool:
        raise ValueError("deferred-capacity opt-in must be an explicit Boolean")
    path = Path(path)
    if path.is_symlink():
        raise ValueError("source archive must be a regular non-symlink file")
    selected = [item for item in catalog() if item.id == dataset_id]
    if len(selected) != 1:
        raise ValueError("select one reviewed source dataset")
    dataset = selected[0]
    filename = path.name if asset_filename is None else asset_filename
    assets = [item for item in dataset.assets if item.filename == filename]
    acquisition_allowed = dataset.acquisition == "allowed" or (
        dataset.acquisition == "deferred_capacity" and allow_deferred_capacity
    )
    if (
        not acquisition_allowed
        or dataset.rights_review != "reviewed_license"
        or len(assets) != 1
        or assets[0].bytes is None
        or assets[0].checksum is None
        or assets[0].checksum_origin != "publisher_metadata"
    ):
        raise ValueError("streamed ZIP requires reviewed rights, explicit capacity opt-in and supplier size/checksum")
    asset = assets[0]
    if type(asset.bytes) is not int or asset.bytes <= 0 or asset.bytes > maximum_archive_bytes:
        raise ValueError("supplier archive size is missing or exceeds the explicit archive budget")
    before_path = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before_path.st_mode) or before_path.st_size != asset.bytes:
        raise ValueError("source archive is not regular or supplier size differs")
    before = (before_path.st_dev, before_path.st_ino, before_path.st_size, before_path.st_mtime_ns)
    algorithm, expected = asset.checksum.split(":", 1)
    if algorithm not in hashlib.algorithms_available:
        raise ValueError("supplier checksum algorithm is unavailable")

    def hash_open_file(stream):
        value = hashlib.new(algorithm)
        sha256 = hashlib.sha256()
        stream.seek(0)
        size = 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > maximum_archive_bytes:
                raise ValueError("source archive exceeds the explicit byte budget")
            value.update(block)
            sha256.update(block)
        return size, value.hexdigest(), sha256.hexdigest()

    def verify_identity(stream):
        opened = os.fstat(stream.fileno())
        current_path = path.stat(follow_symlinks=False)
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        current_identity = (current_path.st_dev, current_path.st_ino, current_path.st_size, current_path.st_mtime_ns)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current_path.st_mode)
            or opened_identity != before
            or current_identity != before
        ):
            raise ValueError("source archive file identity changed during streamed access")

    with path.open("rb") as stream:
        verify_identity(stream)
        size, checksum, archive_sha256 = hash_open_file(stream)
        verify_identity(stream)
        if size != asset.bytes or checksum != expected:
            raise ValueError("source archive supplier size/checksum differs")
        directory_entries, directory_bytes = _zip_directory_metadata(stream)
        if directory_entries > maximum_members or directory_bytes > maximum_central_directory_bytes:
            raise ValueError("source ZIP central-directory entry or byte budget exceeded")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                if len(infos) > maximum_members:
                    raise ValueError("source ZIP member budget exceeded")
                names, kinds, descendants = set(), {}, {}
                total = 0
                for member in infos:
                    raw_name = member.filename
                    name = raw_name[:-1] if member.is_dir() and raw_name.endswith("/") else raw_name
                    logical = PurePosixPath(name)
                    if (
                        not name
                        or member.orig_filename != raw_name
                        or logical.is_absolute()
                        or ".." in logical.parts
                        or logical.as_posix() != name
                        or "\\" in raw_name
                        or ":" in raw_name
                        or any(ord(character) < 32 or ord(character) == 127 for character in raw_name)
                    ):
                        raise ValueError("source ZIP contains an unsafe logical member path")
                    key = name.casefold()
                    if key in names:
                        raise ValueError("source ZIP contains duplicate or case-folded member paths")
                    parts = key.split("/")
                    parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
                    if any(kinds.get(parent) == "file" for parent in parents):
                        raise ValueError("source ZIP file blocks a descendant path")
                    kind = "directory" if member.is_dir() else "file"
                    if kind == "file" and descendants.get(key, 0):
                        raise ValueError("source ZIP file conflicts with an existing descendant")
                    names.add(key)
                    kinds[key] = kind
                    for parent in parents:
                        descendants[parent] = descendants.get(parent, 0) + 1
                    mode = (member.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(mode)
                    expected_type = stat.S_IFDIR if member.is_dir() else stat.S_IFREG
                    if file_type not in {0, expected_type} or member.flag_bits & 0x1:
                        raise ValueError("source ZIP contains a symlink, special or encrypted member")
                    if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}:
                        raise ValueError("source ZIP uses an unsupported compression method")
                    if member.file_size > maximum_member_bytes:
                        raise ValueError("source ZIP member byte budget exceeded")
                    total += member.file_size
                    if total > maximum_total_bytes:
                        raise ValueError("source ZIP uncompressed byte budget exceeded")
                provenance = {
                    "dataset_id": dataset.id,
                    "citation": dataset.citation,
                    "license_id": dataset.license_id,
                    "source_version": dataset.version,
                    "archive_filename": asset.filename,
                    "archive_bytes": size,
                    "archive_sha256": archive_sha256,
                    "supplier_checksum": asset.checksum,
                    "checksum_origin": asset.checksum_origin,
                    "supplier_checksum_verified": True,
                    "member_count": len(infos),
                    "uncompressed_bytes": total,
                    "all_member_crc_verified": False,
                    "archive_storage": "file_backed_same_open_handle",
                }
                stream.seek(0)
                yield archive, provenance
        finally:
            verify_identity(stream)
            final_size, final_checksum, final_sha256 = hash_open_file(stream)
            verify_identity(stream)
            if final_size != asset.bytes or final_checksum != expected or final_sha256 != archive_sha256:
                raise ValueError("source archive bytes changed during streamed access")



def _zip_directory_metadata(stream):
    """Read ZIP end records before ZipFile allocates its central-directory entries."""
    size = os.fstat(stream.fileno()).st_size
    tail_bytes = min(size, 22 + 65535)
    stream.seek(size - tail_bytes)
    tail = stream.read(tail_bytes)
    signature = b"PK\x05\x06"
    position = tail.rfind(signature)
    end = None
    while position >= 0:
        if position + 22 <= len(tail):
            values = struct.unpack_from("<4s4H2LH", tail, position)
            comment_size = values[-1]
            if position + 22 + comment_size == len(tail):
                end = (size - tail_bytes + position, values)
                break
        position = tail.rfind(signature, 0, position)
    if end is None:
        raise ValueError("source ZIP has no valid end-of-central-directory record")
    end_offset, fields = end
    _, disk, directory_disk, entries_disk, entries_total, directory_size, directory_offset, _ = fields
    if disk != 0 or directory_disk != 0 or entries_disk != entries_total:
        raise ValueError("multi-disk ZIP archives are unsupported")
    locator_offset = end_offset - 20
    has_zip64_locator = False
    if locator_offset >= 0:
        stream.seek(locator_offset)
        has_zip64_locator = stream.read(4) == b"PK\x06\x07"
    is_zip64 = entries_total == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF or has_zip64_locator
    if is_zip64:
        if locator_offset < 0:
            raise ValueError("ZIP64 locator is missing")
        stream.seek(locator_offset)
        locator = stream.read(20)
        if len(locator) != 20:
            raise ValueError("ZIP64 locator is truncated")
        locator_signature, zip64_disk, zip64_offset, disk_count = struct.unpack("<4sLQL", locator)
        if locator_signature != b"PK\x06\x07" or zip64_disk != 0 or disk_count != 1:
            raise ValueError("ZIP64 locator is invalid or multi-disk")
        stream.seek(zip64_offset)
        record_prefix = stream.read(12)
        if len(record_prefix) != 12:
            raise ValueError("ZIP64 end record is truncated")
        record_signature, record_size = struct.unpack("<4sQ", record_prefix)
        if record_signature != b"PK\x06\x06" or record_size < 44:
            raise ValueError("ZIP64 end record is invalid")
        if zip64_offset < 0 or zip64_offset + 12 + record_size != locator_offset:
            raise ValueError("ZIP64 end record boundary is invalid")
        stream.seek(zip64_offset)
        record = stream.read(56)
        if len(record) != 56:
            raise ValueError("ZIP64 end record is truncated")
        record_fields = struct.unpack("<4sQ2H2L4Q", record)
        if record_fields[4] != 0 or record_fields[5] != 0:
            raise ValueError("ZIP64 end record is multi-disk")
        zip64_entries_disk, zip64_entries_total, zip64_directory_size, zip64_directory_offset = record_fields[6:10]
        if entries_total not in {0xFFFF, zip64_entries_total} or entries_disk not in {0xFFFF, zip64_entries_disk}:
            raise ValueError("ZIP64 entry counts disagree with end record")
        if directory_size not in {0xFFFFFFFF, zip64_directory_size} or directory_offset not in {0xFFFFFFFF, zip64_directory_offset}:
            raise ValueError("ZIP64 central directory offsets disagree with end record")
        entries_total, directory_size, directory_offset = zip64_entries_total, zip64_directory_size, zip64_directory_offset
    directory_end = directory_offset + directory_size
    expected_end = zip64_offset if is_zip64 else end_offset
    if directory_end != expected_end:
        raise ValueError("ZIP central directory is not adjacent to its end record")
    return entries_total, directory_size
