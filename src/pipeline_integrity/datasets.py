"""Reviewed source assets, atomic acquisition and inspectable integrity records."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    filename: str
    url: str
    checksum: str | None = None
    checksum_origin: Literal["publisher_metadata", "previous_acquisition"] = "publisher_metadata"
    checksum_note: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    repository_blob_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    bytes: int | None = Field(default=None, ge=0)
    default: bool = True
    cache_paths: tuple[str, ...] = ()

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        if (not value or Path(value).name != value or value in {".", ".."}
                or any(character in value for character in ("/", "\\", ":", "\x00"))):
            raise ValueError("asset filename must be a local basename")
        return value

    @field_validator("url")
    @classmethod
    def public_https(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("an explicit HTTPS asset URL without credentials is required")
        return value

    @field_validator("checksum")
    @classmethod
    def supported_checksum(cls, value):
        if value is None:
            return value
        algorithm, separator, expected = value.partition(":")
        sizes = {"md5": 32, "sha256": 64, "sha512": 128}
        if (
            not separator
            or algorithm not in sizes
            or len(expected) != sizes[algorithm]
            or any(c not in "0123456789abcdef" for c in expected)
        ):
            raise ValueError("pin a lowercase md5, sha256 or sha512 checksum")
        return value

    @field_validator("checksum_note")
    @classmethod
    def concise_checksum_note(cls, value):
        if value is not None and (not value.strip() or len(value) > 240):
            raise ValueError("checksum note must be concise when provided")
        return value

    @field_validator("cache_paths")
    @classmethod
    def relative_cache_paths(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
                raise ValueError("cache paths must remain relative to a supplied cache root")
        return values


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    title: str
    source: str
    citation: str
    creators: tuple[str, ...] = ()
    version: str
    origin: Literal[
        "measured_process",
        "laboratory_experiment",
        "simulation",
        "incident_register",
        "human_experiment",
        "image_collection",
        "derived_scores",
        "mixed_compilation",
    ]
    license_id: str
    rights_evidence_url: str
    rights_review: Literal["reviewed_license", "public_access_analysis_only", "unresolved"]
    acquisition: Literal["allowed", "metadata_only", "deferred_capacity"]
    checked_on: str
    intended_uses: tuple[str, ...]
    limitations: tuple[str, ...]
    split_unit: str
    guide: str | None = None
    assets: tuple[AssetSpec, ...]
    raw_in_code_release: Literal[False] = False

    @field_validator("guide")
    @classmethod
    def safe_guide_path(cls, value):
        if value is None:
            return value
        path = PurePosixPath(value)
        if not value.startswith("docs/") or path.as_posix() != value or path.is_absolute() or ".." in path.parts:
            raise ValueError("dataset guide must be a safe project-relative docs path")
        return value


def catalog() -> tuple[DatasetSpec, ...]:
    payload = files("pipeline_integrity.catalog_data").joinpath("datasets.json").read_text(encoding="utf-8")
    rows = json.loads(payload)["datasets"]
    specs = tuple(DatasetSpec.model_validate_json(json.dumps(row)) for row in rows)
    if len({spec.id for spec in specs}) != len(specs):
        raise ValueError("duplicate dataset IDs")
    return specs


def file_digest(path: Path, algorithm="sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_acquisition_allowed(spec: DatasetSpec) -> None:
    """Apply the catalog rights/capacity gate before any source file is read or copied."""
    if spec.acquisition != "allowed" or spec.rights_review == "unresolved":
        raise ValueError(f"{spec.id}: explicit policy withholds acquisition")


def _canonical_digest(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def acquisition_provenance(spec: DatasetSpec, asset: AssetSpec) -> dict:
    """Snapshot catalog rights and selected asset metadata into each receipt."""
    dataset_snapshot = spec.model_dump(mode="json")
    asset_snapshot = asset.model_dump(mode="json")
    asset_catalogued = asset_snapshot in dataset_snapshot["assets"]
    return {
        "catalog_snapshot": dataset_snapshot,
        "catalog_snapshot_sha256": _canonical_digest(dataset_snapshot),
        "asset_snapshot": asset_snapshot,
        "asset_snapshot_sha256": _canonical_digest(asset_snapshot),
        "asset_catalogued": asset_catalogued,
    }


def verify_acquisition_provenance(record: dict) -> bool:
    """Validate a receipt's optional catalog and asset snapshots.

    Returns ``False`` for legacy receipts without snapshots. Partial, altered,
    or receipt-inconsistent snapshots are rejected instead of silently copied
    into the public inventory.
    """
    keys = {
        "catalog_snapshot",
        "catalog_snapshot_sha256",
        "asset_snapshot",
        "asset_snapshot_sha256",
        "asset_catalogued",
    }
    present = {key for key in keys if record.get(key) is not None}
    if not present:
        return False
    if present != keys:
        raise ValueError("acquisition provenance snapshot is incomplete")
    dataset = DatasetSpec.model_validate_json(json.dumps(record["catalog_snapshot"], ensure_ascii=False))
    asset = AssetSpec.model_validate_json(json.dumps(record["asset_snapshot"], ensure_ascii=False))
    asset_catalogued = record["asset_snapshot"] in record["catalog_snapshot"]["assets"]
    if (
        _canonical_digest(record["catalog_snapshot"]) != record["catalog_snapshot_sha256"]
        or _canonical_digest(record["asset_snapshot"]) != record["asset_snapshot_sha256"]
        or record.get("dataset_id") != dataset.id
        or record.get("filename") != asset.filename
        or record.get("source_url") != asset.url
        or record.get("asset_catalogued") is not asset_catalogued
        or record.get("checksum_origin") != (asset.checksum_origin if asset.checksum or asset.sha256 else None)
    ):
        raise ValueError("acquisition receipt differs from its pinned catalog or asset snapshot")
    expected_checksum = asset.checksum or ("sha256:" + asset.sha256 if asset.sha256 else None)
    if record.get("expected_checksum") != expected_checksum:
        raise ValueError("acquisition receipt checksum differs from its asset snapshot")
    return True


def verify_acquired_asset(record: dict, path: Path, *, maximum_bytes: int = 2_000_000_000) -> bool:
    """Rehash one acquired source file and verify the read used a stable regular file.

    The receipt's SHA-256 is mandatory. When a publisher or prior-acquisition
    checksum was recorded, that algorithm is checked in the same streaming
    pass. Catalog provenance, when present, is validated before touching the
    file. No archive members are extracted.
    """
    if not isinstance(record, dict):
        raise TypeError("an acquisition receipt mapping is required")
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be a positive integer")
    recorded_bytes = record.get("bytes")
    recorded_sha256 = record.get("sha256")
    if type(recorded_bytes) is not int or recorded_bytes <= 0 or recorded_bytes > maximum_bytes:
        raise ValueError("receipt byte count is missing, empty, or exceeds the verification budget")
    if (
        not isinstance(recorded_sha256, str)
        or len(recorded_sha256) != 64
        or any(character not in "0123456789abcdef" for character in recorded_sha256)
    ):
        raise ValueError("receipt must pin a lowercase SHA-256")

    provenance_verified = verify_acquisition_provenance(record)
    pinned_asset = None
    if provenance_verified:
        pinned_asset = AssetSpec.model_validate_json(json.dumps(record["asset_snapshot"], ensure_ascii=False))
        if pinned_asset.bytes is not None and pinned_asset.bytes != recorded_bytes:
            raise ValueError("receipt byte count differs from the pinned asset size")

    expected_checksum = record.get("expected_checksum")
    checksum_hasher = None
    if expected_checksum is not None:
        algorithm, separator, expected = expected_checksum.partition(":")
        checksum_sizes = {"md5": 32, "sha256": 64, "sha512": 128}
        if (
            not separator
            or algorithm not in checksum_sizes
            or len(expected) != checksum_sizes[algorithm]
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise ValueError("receipt contains an unsupported expected checksum")
        if record.get("expected_checksum_verified") is not True:
            raise ValueError("receipt expected checksum was not verified during acquisition")
        supplier_verified = algorithm in checksum_sizes and record.get("checksum_origin") == "publisher_metadata"
        if record.get("supplier_checksum_verified") is not supplier_verified:
            raise ValueError("receipt supplier-checksum status conflicts with its checksum origin")
        checksum_hasher = hashlib.new(algorithm)
    elif record.get("supplier_checksum_verified") is True or record.get("expected_checksum_verified") is True:
        raise ValueError("receipt claims checksum verification without a pinned checksum")

    source = Path(path)
    try:
        path_before = source.lstat()
    except OSError as error:
        raise ValueError("acquired source is unavailable") from error
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise ValueError("acquired source must be a regular non-symlink file")
    if path_before.st_size != recorded_bytes or path_before.st_size > maximum_bytes:
        raise ValueError("acquired source size differs from its receipt or verification budget")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise ValueError("acquired source could not be opened safely") from error
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        descriptor_before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(descriptor_before.st_mode)
            or descriptor_before.st_size != recorded_bytes
            or (path_before.st_dev, path_before.st_ino) != (descriptor_before.st_dev, descriptor_before.st_ino)
        ):
            raise ValueError("acquired source changed before verification began")
        sha256 = hashlib.sha256()
        total = 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(block)
            if total > maximum_bytes:
                raise ValueError("acquired source exceeded the verification byte budget while reading")
            sha256.update(block)
            if checksum_hasher is not None:
                checksum_hasher.update(block)
        descriptor_after = os.fstat(stream.fileno())
        path_after = source.lstat()

    def identity(snapshot):
        return (
            snapshot.st_dev,
            snapshot.st_ino,
            snapshot.st_size,
            getattr(snapshot, "st_mtime_ns", None),
            getattr(snapshot, "st_ctime_ns", None),
        )

    if (
        total != recorded_bytes
        or identity(descriptor_before) != identity(descriptor_after)
        or identity(descriptor_after) != identity(path_after)
        or stat.S_ISLNK(path_after.st_mode)
        or not stat.S_ISREG(path_after.st_mode)
    ):
        raise ValueError("acquired source changed during verification")
    if sha256.hexdigest() != recorded_sha256:
        raise ValueError("acquired source SHA-256 differs from its receipt")
    if pinned_asset is not None and pinned_asset.sha256 is not None and sha256.hexdigest() != pinned_asset.sha256:
        raise ValueError("acquired source SHA-256 differs from its pinned asset metadata")
    if checksum_hasher is not None and checksum_hasher.hexdigest() != expected:
        raise ValueError("acquired source differs from its pinned expected checksum")
    return True


def _matches(path, asset):
    if asset.bytes is not None and path.stat().st_size != asset.bytes:
        return False
    if asset.checksum is None and asset.sha256 is None:
        return False
    if asset.checksum:
        algorithm, expected = asset.checksum.split(":", 1)
        if file_digest(path, algorithm) != expected:
            return False
    if asset.sha256 and file_digest(path, "sha256") != asset.sha256:
        return False
    return True


def inspect_zip(path: Path):
    """Inventory unambiguous regular ZIP entries without extracting archive content."""
    with zipfile.ZipFile(path) as archive:
        members = []
        seen = set()
        entry_kind = {}
        descendant_count = {}
        for info in archive.infolist():
            raw_name = info.filename
            logical_name = raw_name[:-1] if info.is_dir() and raw_name.endswith("/") else raw_name
            member = PurePosixPath(logical_name)
            if (
                not logical_name
                or member.is_absolute()
                or ".." in member.parts
                or "\\" in raw_name
                or ":" in raw_name
                or any(ord(character) < 32 or ord(character) == 127 for character in raw_name)
                or member.as_posix() != logical_name
            ):
                raise ValueError("archive contains a path outside its logical root")
            portable_name = logical_name.casefold()
            if portable_name in seen:
                raise ValueError("archive contains duplicate or cross-platform ambiguous member names")
            path_parts = portable_name.split("/")
            ancestors = ["/".join(path_parts[:index]) for index in range(1, len(path_parts))]
            if any(entry_kind.get(parent) == "file" for parent in ancestors):
                raise ValueError("archive file blocks a descendant path")
            kind = "directory" if info.is_dir() else "file"
            if kind == "file" and descendant_count.get(portable_name, 0):
                raise ValueError("archive file conflicts with an existing descendant path")
            seen.add(portable_name)
            entry_kind[portable_name] = kind
            for parent in ancestors:
                descendant_count[parent] = descendant_count.get(parent, 0) + 1
            if info.flag_bits & 0x1:
                raise ValueError("encrypted ZIP members cannot be safely inventoried for reproducible use")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            expected_type = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
            if file_type not in {0, expected_type}:
                raise ValueError("archive contains a link or non-regular filesystem member")
            members.append(
                {
                    "name": logical_name,
                    "bytes": info.file_size,
                    "crc32": f"{info.CRC:08x}",
                    "kind": "directory" if info.is_dir() else "file",
                    "encrypted": False,
                    "filesystem_entry": "directory" if info.is_dir() else "regular_file",
                }
            )
        return members


def inspect_rar(path: Path, *, executable=None, timeout_s=120):
    """Inventory RAR entries with an installed 7-Zip-compatible listing tool.

    This bounded listing never extracts members. RAR5 symlink/hardlink and
    anti-item metadata are rejected because listing text alone cannot prove
    that those entries are safe for a downstream consumer.
    """
    if type(timeout_s) is not int or not 1 <= timeout_s <= 600:
        raise ValueError("RAR listing timeout must be between 1 and 600 seconds")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("RAR source must be a regular non-symlink file")
    tool = executable or os.environ.get("PIPELINE_RAR_LIST_TOOL") or shutil.which("7z") or shutil.which("7zz")
    if not tool:
        tool = shutil.which("tar")
        mode = "tar" if tool else None
    else:
        mode = "7z"
    if not tool:
        raise FileNotFoundError("install 7z/7zz or tar, or set PIPELINE_RAR_LIST_TOOL")
    command = (
        [str(tool), "-tvf", str(path.resolve())]
        if mode == "tar"
        else [str(tool), "l", "-slt", "--", str(path.resolve())]
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("RAR listing tool could not read the archive")
    if len(result.stdout.encode("utf-8")) > 64 * 1024 * 1024:
        raise ValueError("RAR listing output exceeds the 64 MiB inventory budget")
    if mode == "tar":
        import re

        entries = []
        for line in result.stdout.splitlines():
            match = re.match(r"^([\-dhl][rwxstST-]{9})\s+\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\d+\s+\d+\s+(.+)$", line)
            if not match:
                raise ValueError("RAR listing contains an unsupported entry format")
            mode_bits, size_text, name = match.groups()
            if mode_bits[0] not in {"-", "d"} or " -> " in name or " link to " in name:
                raise ValueError("RAR links or non-regular entries cannot be safely inventoried")
            logical_name = name[:-1] if mode_bits[0] == "d" and name.endswith("/") else name
            logical = PurePosixPath(logical_name)
            if (
                not logical_name
                or logical.is_absolute()
                or ".." in logical.parts
                or "\\" in logical_name
                or ":" in logical_name
                or any(ord(character) < 32 or ord(character) == 127 for character in logical_name)
                or logical.as_posix() != logical_name
            ):
                raise ValueError("RAR contains an unsafe member path")
            entries.append(
                {
                    "name": logical_name,
                    "bytes": int(size_text),
                    "kind": "directory" if mode_bits[0] == "d" else "regular_file",
                    "encrypted": None,
                    "crc": None,
                }
            )
        if not entries or len(entries) > 200_000:
            raise ValueError("RAR entry count is empty or exceeds the 200,000-member budget")
        names = [row["name"].casefold() for row in entries]
        if len(names) != len(set(names)):
            raise ValueError("RAR contains duplicate or case-colliding member names")
        return entries
    entries, fields = [], {}
    for line in result.stdout.splitlines() + [""]:
        if not line.strip():
            if "Path" in fields and "Type" not in fields and fields.get("Path") != str(path.resolve()):
                name = fields["Path"]
                logical = PurePosixPath(name)
                if (
                    logical.is_absolute()
                    or ".." in logical.parts
                    or "\\" in name
                    or ":" in name
                    or logical.as_posix() != name
                ):
                    raise ValueError("RAR contains an unsafe member path")
                folder = fields.get("Folder", "-") == "+"
                attributes = fields.get("Attributes", "")
                if "L" in attributes or "H" in attributes or fields.get("Hard Link") or fields.get("Symbolic Link"):
                    raise ValueError("RAR links cannot be safely inventoried")
                if fields.get("Anti") == "+":
                    raise ValueError("RAR anti-items cannot be safely inventoried")
                size_text = fields.get("Size", "0")
                if not size_text.isdigit():
                    raise ValueError("RAR member has an invalid size")
                entries.append(
                    {
                        "name": name,
                        "bytes": int(size_text),
                        "kind": "directory" if folder else "regular_file",
                        "encrypted": fields.get("Encrypted") == "+",
                        "crc": fields.get("CRC"),
                    }
                )
            fields = {}
            continue
        key, separator, value = line.partition(" = ")
        if separator:
            fields[key] = value
    if not entries:
        raise ValueError("RAR archive listing contains no member entries")
    names = [row["name"].casefold() for row in entries]
    if len(names) != len(set(names)):
        raise ValueError("RAR contains duplicate or case-colliding member names")
    if len(entries) > 200_000:
        raise ValueError("RAR entry count exceeds the 200,000-member budget")
    return entries


def acquire_asset(
    spec: DatasetSpec,
    asset: AssetSpec,
    destination: Path,
    *,
    cache_roots=(),
    maximum_bytes=2_000_000_000,
    maximum_seconds=180,
):
    if maximum_seconds <= 0 or maximum_bytes <= 0:
        raise ValueError("acquisition budgets must be positive")
    require_acquisition_allowed(spec)
    if asset.bytes is not None and asset.bytes > maximum_bytes:
        raise ValueError("asset exceeds the declared acquisition budget")
    root = Path(destination).resolve()
    target = root / spec.id / asset.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    method = "existing"
    if target.exists():
        if target.stat().st_size > maximum_bytes:
            raise ValueError("existing asset exceeds the declared acquisition budget")
        if (asset.checksum or asset.sha256) and not _matches(target, asset):
            raise ValueError(f"existing asset failed integrity: {spec.id}/{asset.filename}")
        if asset.bytes is not None and target.stat().st_size != asset.bytes:
            raise ValueError("existing asset has an unexpected size")
    else:
        cached = None
        for cache_root in cache_roots:
            for relative in asset.cache_paths:
                candidate = Path(cache_root) / relative
                if candidate.is_file() and _matches(candidate, asset):
                    cached = candidate
                    break
            if cached:
                break
        temporary = target.with_name(target.name + ".partial")
        if cached:
            if cached.stat().st_size > maximum_bytes:
                raise ValueError("cached asset exceeds the declared acquisition budget")
            shutil.copyfile(cached, temporary)
            method = "verified_cache_copy"
        else:
            request = json.dumps(
                {
                    "url": asset.url,
                    "target": str(temporary),
                    "maximum_bytes": maximum_bytes,
                    "expected_bytes": asset.bytes,
                    "checksum": asset.checksum,
                }
            )
            try:
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", "-m", "pipeline_integrity._download"],
                    input=request,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=maximum_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise TimeoutError("source exceeded the acquisition wall-clock budget") from error
            if completed.returncode:
                stderr = completed.stderr or ""
                error_type = stderr.strip().splitlines()[-1][:180] if stderr.strip() else "download failed"
                raise OSError(error_type)
            method = "download"
        if asset.bytes is not None and temporary.stat().st_size != asset.bytes:
            raise ValueError("download size differs from source metadata")
        if (asset.checksum or asset.sha256) and not _matches(temporary, asset):
            raise ValueError("download checksum differs from the pinned source")
        if temporary.stat().st_size == 0:
            raise ValueError("an empty response is not a usable source asset")
        if target.suffix.lower() == ".zip":
            inspect_zip(temporary)
        temporary.replace(target)
    if target.stat().st_size == 0:
        raise ValueError("an empty response is not a usable source asset")
    if target.suffix.lower() == ".zip":
        members = inspect_zip(target)
    elif target.suffix.lower() == ".rar":
        members = inspect_rar(target)
    else:
        members = None
    return {
        "dataset_id": spec.id,
        "filename": asset.filename,
        "bytes": target.stat().st_size,
        "sha256": file_digest(target),
        "expected_checksum": asset.checksum or ("sha256:" + asset.sha256 if asset.sha256 else None),
        "checksum_origin": asset.checksum_origin if asset.checksum or asset.sha256 else None,
        "expected_checksum_verified": bool(asset.checksum or asset.sha256),
        "supplier_checksum_verified": bool(asset.checksum or asset.sha256) and asset.checksum_origin == "publisher_metadata",
        "method": method,
        "source_url": asset.url,
        "members": members,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_in_code_release": False,
        **acquisition_provenance(spec, asset),
    }


def acquire(
    dataset_ids,
    destination,
    *,
    cache_roots=(),
    workers=3,
    maximum_bytes=2_000_000_000,
    maximum_seconds=180,
    progress=None,
):
    if not dataset_ids or type(workers) is not int or workers < 1:
        raise ValueError("select at least one dataset and a positive integer worker count")
    lookup = {spec.id: spec for spec in catalog()}
    if len(set(dataset_ids)) != len(dataset_ids) or set(dataset_ids) - set(lookup):
        raise ValueError("known unique dataset IDs are required")
    jobs = []
    for identity in dataset_ids:
        spec = lookup[identity]
        require_acquisition_allowed(spec)
        if not any(asset.default for asset in spec.assets):
            raise ValueError(f"{identity}: no default assets selected")
        jobs.extend((spec, asset) for asset in spec.assets if asset.default)
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(
                acquire_asset,
                spec,
                asset,
                Path(destination),
                cache_roots=cache_roots,
                maximum_bytes=maximum_bytes,
                maximum_seconds=maximum_seconds,
            ): (spec.id, asset.filename)
            for spec, asset in jobs
        }
        for future in as_completed(pending):
            identity, filename = pending[future]
            try:
                records.append(future.result())
            except Exception as error:
                failures.append(
                    {
                        "dataset_id": identity,
                        "filename": filename,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
            if progress is not None:
                progress({"files": list(records), "failures": list(failures)})
    return {
        "schema": "pipeline_integrity.acquisition.v1",
        "files": sorted(records, key=lambda r: (r["dataset_id"], r["filename"])),
        "failures": failures,
        "complete": not failures,
    }
