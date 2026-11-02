"""Content-level crosswalk for two closely related open leak-testbed deposits.

The public pages describe matching experiments, but only exact member hashes can
establish that the two packaged deposits contain the same digital signal files.
"""
from __future__ import annotations

import hashlib
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .contracts import digest
from .datasets import file_digest
from .leak_multisensor_data import (
    ACCELEROMETER_PREFIX,
    HYDROPHONE_PREFIX,
    PRESSURE_PREFIX,
    LeakMultisensorArchiveSession,
)

TBRNP_DATASET_ID = "leak_testbed"
TBRNP_DOI = "10.17632/tbrnp6vrnj.1"
TBRNP_ARCHIVE_NAME = "leak_testbed-v1.zip"
TBRNP_ARCHIVE_BYTES = 1_080_066_320
# Locally measured for the downloaded public aggregate ZIP; the provider publishes no ZIP hash.
TBRNP_ARCHIVE_SHA256 = "a8954b3cec0a0773a956177fed388fb891b25ef2cdf2d264b4b9557cf2a271a1"
TBRNP_ROOT = "Dataset of Leak Simulations in Experimental Testbed Water Distribution System/"
TBRNP_NESTED = {
    "Accelerometer.zip": ("accelerometer", "Accelerometer"),
    "Dynamic Pressure Sensor.zip": ("dynamic_pressure", "Dynamic Pressure Sensor"),
    "Hydrophone.zip": ("hydrophone", "Hydrophone"),
}
TBRNP_NESTED_BYTES = {
    "Accelerometer.zip": 459_278_311,
    "Dynamic Pressure Sensor.zip": 562_748_973,
    "Hydrophone.zip": 63_994_754,
}
XW44_ROOT = "Dataset of Leak Simulations in Experimental Testbed Water Network/"
_XW44_PREFIXES = {
    "accelerometer": ACCELEROMETER_PREFIX,
    "dynamic_pressure": PRESSURE_PREFIX,
    "hydrophone": HYDROPHONE_PREFIX,
}
_ALLOWED_SUFFIXES = {
    "accelerometer": ".csv",
    "dynamic_pressure": ".csv",
    "hydrophone": ".raw",
}
_MAX_MEMBER_BYTES = 128 * 1024 * 1024


class LeakDepositCrosswalkError(ValueError):
    """One source package or its member inventory differs from the reviewed pin."""


def _source_hash(path: Path, expected_sha256: str, expected_bytes: int):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected_bytes:
        raise LeakDepositCrosswalkError("source archive is not the reviewed regular file")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    actual = hasher.hexdigest()
    if actual != expected_sha256:
        raise LeakDepositCrosswalkError("source archive SHA-256 differs from its reviewed pin")
    return path.resolve(strict=True), actual


def _safe_zip_path(name: str):
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
        or name not in {path.as_posix(), path.as_posix() + "/"}
        or not path.parts
    ):
        raise LeakDepositCrosswalkError("ZIP member path is unsafe or ambiguous")
    return path


def _zip_regular_file(info: zipfile.ZipInfo, *, max_bytes: int = _MAX_MEMBER_BYTES):
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if info.flag_bits & 1 or kind not in {0, stat.S_IFREG}:
        raise LeakDepositCrosswalkError("encrypted or non-regular ZIP member is not accepted")
    if info.file_size < 0 or info.file_size > max_bytes:
        raise LeakDepositCrosswalkError("ZIP member exceeds the explicit content budget")


def _copy_zip_member_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination, *, max_bytes: int):
    if info.file_size < 0 or info.file_size > max_bytes:
        raise LeakDepositCrosswalkError("ZIP member exceeds the explicit content budget")
    observed = 0
    with archive.open(info, "r") as source:
        while True:
            block = source.read(min(1024 * 1024, max_bytes - observed + 1))
            if not block:
                break
            observed += len(block)
            if observed > info.file_size or observed > max_bytes:
                raise LeakDepositCrosswalkError("ZIP member expanded beyond its declared bound")
            destination.write(block)
    if observed != info.file_size:
        raise LeakDepositCrosswalkError("ZIP member expanded to an unexpected byte count")
    return observed


def _sha_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    hasher = hashlib.sha256()
    observed = 0
    with archive.open(info, "r") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            observed += len(block)
            if observed > info.file_size or observed > _MAX_MEMBER_BYTES:
                raise LeakDepositCrosswalkError("ZIP member expanded beyond its declared bound")
            hasher.update(block)
    if observed != info.file_size:
        raise LeakDepositCrosswalkError("ZIP member expanded to an unexpected byte count")
    return hasher.hexdigest()


def _nested_zip_ledger(outer: zipfile.ZipFile, info: zipfile.ZipInfo, family: str, label: str):
    # ZipExtFile cannot seek, so spool only this nested archive to a temporary seekable object.
    entries, seen = {}, set()
    with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024, mode="w+b") as nested_file:
        _copy_zip_member_bounded(outer, info, nested_file, max_bytes=600_000_000)
        nested_file.seek(0)
        try:
            nested = zipfile.ZipFile(nested_file)
        except (zipfile.BadZipFile, OSError) as exc:
            raise LeakDepositCrosswalkError("nested sensor archive is invalid") from exc
        with nested:
            for member in nested.infolist():
                name = member.filename
                _safe_zip_path(name)
                key = PurePosixPath(name).as_posix().casefold()
                if key in seen:
                    raise LeakDepositCrosswalkError("nested sensor archive contains duplicate or case-colliding paths")
                seen.add(key)
                if member.is_dir():
                    kind = stat.S_IFMT(member.external_attr >> 16)
                    if member.flag_bits & 1 or kind not in {0, stat.S_IFDIR}:
                        raise LeakDepositCrosswalkError("encrypted or special nested ZIP directory is not accepted")
                    continue
                _zip_regular_file(member)
                suffix = Path(name).suffix.casefold()
                if suffix != _ALLOWED_SUFFIXES[family]:
                    raise LeakDepositCrosswalkError("nested archive contains an unexpected sensor file type")
                relative = name
                prefix = label + "/"
                if relative.startswith(prefix):
                    relative = relative[len(prefix):]
                canonical = f"{family}/{relative}"
                if canonical in entries:
                    raise LeakDepositCrosswalkError("nested sensor archive contains duplicate canonical members")
                entries[canonical] = {
                    "bytes": member.file_size,
                    "sha256": _sha_zip_member(nested, member),
                }
    return entries


def _tbrnp_member_ledger(archive_path: Path):
    local, archive_sha = _source_hash(
        archive_path, TBRNP_ARCHIVE_SHA256, TBRNP_ARCHIVE_BYTES
    )
    outer_seen, nested_files, result = set(), {}, {}
    try:
        with zipfile.ZipFile(local) as outer:
            for info in outer.infolist():
                path = _safe_zip_path(info.filename)
                folded = path.as_posix().casefold()
                if folded in outer_seen:
                    raise LeakDepositCrosswalkError("outer ZIP contains duplicate or case-colliding paths")
                outer_seen.add(folded)
                if info.is_dir():
                    kind = stat.S_IFMT(info.external_attr >> 16)
                    if info.flag_bits & 1 or kind not in {0, stat.S_IFDIR}:
                        raise LeakDepositCrosswalkError("encrypted or special ZIP directory is not accepted")
                    continue
                _zip_regular_file(info, max_bytes=600_000_000)
                if not info.filename.startswith(TBRNP_ROOT):
                    raise LeakDepositCrosswalkError("outer ZIP member is outside the reviewed root")
                basename = PurePosixPath(info.filename).name
                if basename in TBRNP_NESTED:
                    family, label = TBRNP_NESTED[basename]
                    if info.filename != TBRNP_ROOT + basename or info.file_size != TBRNP_NESTED_BYTES[basename]:
                        raise LeakDepositCrosswalkError("outer nested ZIP path or size changed")
                    nested_files[basename] = _nested_zip_ledger(outer, info, family, label)
                elif basename == "Python code to convert RAW acoustic data to pandas DataFrame.py":
                    # Preserve its identity only; never import or execute publisher-supplied code.
                    continue
                else:
                    raise LeakDepositCrosswalkError("outer ZIP contains an unreviewed asset")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, LeakDepositCrosswalkError):
            raise
        raise LeakDepositCrosswalkError("outer ZIP cannot be inventoried safely") from exc
    if set(nested_files) != set(TBRNP_NESTED):
        raise LeakDepositCrosswalkError("outer ZIP does not contain the three reviewed sensor archives")
    expected_counts = {"accelerometer": 80, "dynamic_pressure": 80, "hydrophone": 122}
    for basename, (family, _) in TBRNP_NESTED.items():
        entries = nested_files[basename]
        if sum(name.startswith(family + "/") for name in entries) != expected_counts[family]:
            raise LeakDepositCrosswalkError("nested sensor archive member count differs from the reviewed source")
        for name, value in entries.items():
            if name in result:
                raise LeakDepositCrosswalkError("sensor family paths overlap ambiguously")
            result[name] = value
    if _source_hash(local, TBRNP_ARCHIVE_SHA256, TBRNP_ARCHIVE_BYTES)[1] != archive_sha:
        raise LeakDepositCrosswalkError("outer ZIP changed while its members were read")
    return local, archive_sha, result


def _xw44_member_ledger(archive_path: Path, tar_executable: Path, timeout_s: float):
    result = {}
    with LeakMultisensorArchiveSession(
        archive_path, tar_executable=tar_executable, byte_budget=1024 * 1024, timeout_s=timeout_s
    ) as session:
        archive_sha = session.inventory["archive"]["sha256"]
        for family in ("accelerometer", "dynamic_pressure", "hydrophone"):
            prefix = _XW44_PREFIXES[family]
            for item in session.hash_members(family=family, batch_size=16):
                member = item["member"]
                if not member.startswith(prefix) or len(member) <= len(prefix):
                    raise LeakDepositCrosswalkError("pinned archive member is outside its declared family")
                canonical = f"{family}/{member[len(prefix):]}"
                result[canonical] = {"bytes": item["bytes"], "sha256": item["sha256"]}
        if archive_sha != session.inventory["archive"]["sha256"]:
            raise LeakDepositCrosswalkError("pinned 7z source inventory changed during replay")
    return archive_sha, result


def compare_member_ledgers(left: dict, right: dict):
    """Compare canonical path, byte count and content hash without a size-only shortcut."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise TypeError("member ledgers must be mappings")
    for ledger in (left, right):
        for name, entry in ledger.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(entry, dict)
                or type(entry.get("bytes")) is not int
                or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str)
                or len(entry["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in entry["sha256"])
            ):
                raise ValueError("member ledger entry is invalid")
    left_names, right_names = set(left), set(right)
    common = left_names & right_names
    same_bytes = sorted(n for n in common if left[n]["bytes"] == right[n]["bytes"])
    same_content = sorted(
        n for n in common if left[n]["bytes"] == right[n]["bytes"] and left[n]["sha256"] == right[n]["sha256"]
    )
    return {
        "left_member_count": len(left),
        "right_member_count": len(right),
        "same_path_count": len(common),
        "same_path_and_size_count": len(same_bytes),
        "same_path_size_and_sha256_count": len(same_content),
        "left_only": sorted(left_names - right_names),
        "right_only": sorted(right_names - left_names),
        "different_size": sorted(n for n in common if left[n]["bytes"] != right[n]["bytes"]),
        "different_sha256": sorted(
            n for n in common if left[n]["bytes"] == right[n]["bytes"] and left[n]["sha256"] != right[n]["sha256"]
        ),
        "byte_identical_member_set": left_names == right_names and len(same_content) == len(left) == len(right),
    }


def compare_leak_testbed_deposits(
    multisensor_7z: Path, water_distribution_zip: Path, *, tar_executable: Path, timeout_s: float = 1800
):
    """Hash all sensor members in the two CC BY deposits and issue an exact content crosswalk.

    This is an archive-level data comparison; it does not establish independent experiments,
    calibration, physical units, clock synchronization, signal validity or field performance.
    """
    xw_sha, xw_members = _xw44_member_ledger(multisensor_7z, tar_executable, timeout_s)
    tbr_local, tbr_sha, tbr_members = _tbrnp_member_ledger(water_distribution_zip)
    comparison = compare_member_ledgers(xw_members, tbr_members)
    identity = "byte_identical_sensor_member_sets" if comparison["byte_identical_member_set"] else "member_content_differs"
    body = {
        "schema": "pipeline_integrity.leak_deposit_crosswalk.v1",
        "datasets": [
            {
                "dataset_id": "leak_multisensor_transients",
                "doi": "10.17632/xw44wv2g88.1",
                "filename": "Dataset of Leak Simulations in Experimental Testbed Water Network.7z",
                "bytes": 717_637_500,
                "sha256": xw_sha,
                "license_id": "CC-BY-4.0",
            },
            {
                "dataset_id": TBRNP_DATASET_ID,
                "doi": TBRNP_DOI,
                "filename": TBRNP_ARCHIVE_NAME,
                "bytes": TBRNP_ARCHIVE_BYTES,
                "sha256": tbr_sha,
                "archive_checksum_origin": "locally_measured_public_aggregate_zip",
                "license_id": "CC-BY-4.0",
            },
        ],
        "member_comparison": comparison,
        "member_manifest_digests": {
            "xw44wv2g88.1": digest(xw_members),
            "tbrnp6vrnj.1": digest(tbr_members),
        },
        "identity_classification": identity,
        "independent_experiment_or_trial_established": False,
        "raw_signal_values_included": False,
        "publisher_conversion_code_executed": False,
        "physical_units_or_clock_truth_inferred": False,
        "limitations": [
            "Equal member bytes establish digital file identity, not independent experimental opportunities.",
            "Different member bytes do not by themselves establish a scientifically independent experiment.",
            "The aggregate ZIP checksum is a local source pin; Mendeley publishes no aggregate ZIP SHA-256 for this deposit.",
        ],
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return {**body, "record_digest": digest(body)}
