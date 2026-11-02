"""Pinned raw PCM access; digitizer codes never imply calibrated physical amplitude."""

import hashlib
import io
import json
import platform
import stat
import wave
from pathlib import Path, PurePosixPath

from .contracts import digest
from .datasets import catalog, file_digest
from .pressure_wave_data import _archive_tool_identity, _bounded_command

DATASET_ID = "water_acoustic_training_base"


def _regular_archive_identity(path):
    """Pin one regular, non-symlink archive object for the complete member read."""
    path = Path(path)
    link_info = path.lstat()
    if stat.S_ISLNK(link_info.st_mode) or not stat.S_ISREG(link_info.st_mode):
        raise ValueError("source archive must be a regular non-symlink file")
    resolved = path.resolve(strict=True)
    target_info = resolved.stat()
    identity = (target_info.st_dev, target_info.st_ino, target_info.st_size, target_info.st_mtime_ns)
    link_identity = (link_info.st_dev, link_info.st_ino, link_info.st_size, link_info.st_mtime_ns)
    if not stat.S_ISREG(target_info.st_mode) or identity != link_identity:
        raise ValueError("source archive changed while its filesystem identity was resolved")
    return resolved, identity


def parse_pcm_wave(raw, *, maximum_frames=100_000):
    if type(maximum_frames) is not int or not 8 <= maximum_frames <= 2_000_000:
        raise ValueError("explicit frame budget must be in [8, 2000000]")
    if not isinstance(raw, bytes) or len(raw) > 16_000_000:
        raise ValueError("bounded source WAV bytes required")
    with wave.open(io.BytesIO(raw), "rb") as stream:
        channels, width, rate, frames = (
            stream.getnchannels(),
            stream.getsampwidth(),
            stream.getframerate(),
            stream.getnframes(),
        )
        if (
            stream.getcomptype() != "NONE"
            or channels != 1
            or width not in (1, 2, 3, 4)
            or rate <= 0
            or not 8 <= frames <= maximum_frames
        ):
            raise ValueError("only bounded mono integer PCM is supported")
        payload = stream.readframes(frames + 1)
    if len(payload) != frames * width:
        raise ValueError("truncated or inconsistent PCM data chunk")
    codes = tuple(
        (payload[i] - 128 if width == 1 else int.from_bytes(payload[i : i + width], "little", signed=True))
        for i in range(0, len(payload), width)
    )
    body = {
        "schema": "pipeline_integrity.pcm_observations.v1",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "sample_rate_hz": rate,
        "sample_frames": frames,
        "channels": channels,
        "sample_width_bytes": width,
        "pcm_codes": list(codes),
        "sample_time_basis": "file_relative_index_only",
        "physical_clock_id": None,
        "physical_amplitude_unit": None,
        "amplitude_calibration": None,
        "saturation_interpretation": None,
        "field_validation": False,
        "module_sha256": file_digest(Path(__file__)),
        "wave_reader_sha256": file_digest(Path(wave.__file__)),
        "python_version": platform.python_version(),
    }
    return dict(body, record_digest=digest(body))


def read_training_audio(archive, member, *, tar_executable, timeout_s=20.0, maximum_frames=100_000):
    """Read one regular allowlisted WAV without extracting paths or executing programs."""
    archive = Path(archive)
    source_archive, source_identity = _regular_archive_identity(archive)
    spec = next(s for s in catalog() if s.id == DATASET_ID)
    if (spec.rights_review, spec.acquisition, spec.license_id) != (
        "reviewed_license",
        "allowed",
        "CC-BY-4.0",
    ):
        raise ValueError("source rights or acquisition policy changed")
    assets = [a for a in spec.assets if a.filename == archive.name]
    if len(assets) != 1:
        raise ValueError("select a reviewed on-site leak or no-leak archive")
    asset = assets[0]
    if (
        asset.bytes is None
        or asset.bytes > 10_000_000
        or source_archive.stat().st_size != asset.bytes
        or asset.checksum is None
        or asset.checksum_origin != "publisher_metadata"
    ):
        raise ValueError("source size/budget/publisher pin differs")
    algorithm, expected = asset.checksum.split(":", 1)
    if file_digest(source_archive, algorithm) != expected:
        raise ValueError("source publisher checksum differs")
    archive_sha = file_digest(source_archive)
    logical = PurePosixPath(member)
    prefix = asset.filename.removesuffix(".rar")
    if (
        logical.is_absolute()
        or ".." in logical.parts
        or "\\" in member
        or ":" in member
        or logical.as_posix() != member
        or any(ord(c) < 32 or ord(c) == 127 for c in member)
        or logical.suffix.lower() != ".wav"
        or len(logical.parts) != 2
        or logical.parts[0] != prefix
    ):
        raise ValueError("select a safe WAV member in the reviewed source folder")
    tool = _archive_tool_identity(tar_executable)
    listing = _bounded_command(
        [tool["path"], "-tvf", str(source_archive)], byte_budget=1_000_000, timeout_s=timeout_s
    )
    selected = []
    for line in listing.decode("utf-8", errors="strict").splitlines():
        parts = line.split(maxsplit=8)
        if len(parts) == 9 and parts[8] == member:
            if not parts[0].startswith("-") or parts[1] not in {"0", "1"}:
                raise ValueError("selected WAV must be a regular nonlinked member")
            selected.append(int(parts[4]))
    if len(selected) != 1 or not 0 < selected[0] <= 16_000_000:
        raise ValueError("selected member is absent, duplicated or oversized")
    raw = _bounded_command(
        [tool["path"], "-xOf", str(source_archive), member],
        byte_budget=selected[0],
        timeout_s=timeout_s,
    )
    if (
        len(raw) != selected[0]
        or file_digest(source_archive) != archive_sha
        or _archive_tool_identity(tool["path"]) != tool
    ):
        raise ValueError("source/member changed during access")
    current_source, current_identity = _regular_archive_identity(archive)
    if current_source != source_archive or current_identity != source_identity:
        raise ValueError("source archive filesystem identity changed during member access")
    body = {
        "schema": "pipeline_integrity.training_audio_source.v1",
        "dataset_id": DATASET_ID,
        "catalog_entry_sha256": digest(spec),
        "archive_filename": archive.name,
        "archive_sha256": archive_sha,
        "supplier_checksum": asset.checksum,
        "supplier_checksum_verified": True,
        "member": member,
        "filename_label_interpreted": False,
        "physical_event_group_id": None,
        "source_origin": spec.origin,
        "citation": spec.citation,
        "license_id": spec.license_id,
        "observations": parse_pcm_wave(raw, maximum_frames=maximum_frames),
        "module_sha256": file_digest(Path(__file__)),
        "dependencies": {
            n: file_digest(Path(__file__).with_name(n + ".py"))
            for n in ("contracts", "datasets", "pressure_wave_data")
        },
        "tar_executable_sha256": tool["sha256"],
        "timeout_s": timeout_s,
        "maximum_frames": maximum_frames,
    }
    return dict(body, record_digest=digest(body))


def verify_training_audio_record(record, *, source_root, tar_executable):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("audio source record seal changed")
    filename = record["archive_filename"]
    if (
        not isinstance(filename, str)
        or not filename
        or PurePosixPath(filename).name != filename
        or "\\" in filename
        or ":" in filename
        or filename in {".", ".."}
    ):
        raise ValueError("audio replay archive must be a source-root basename")
    root = Path(source_root).resolve(strict=True)
    selected = root / filename
    if selected.is_symlink() or not selected.resolve(strict=True).is_relative_to(root):
        raise ValueError("audio replay archive leaves the declared source root")
    replay = read_training_audio(
        selected,
        record["member"],
        tar_executable=tar_executable,
        timeout_s=record["timeout_s"],
        maximum_frames=record["maximum_frames"],
    )
    if json.dumps(replay, sort_keys=True) != json.dumps(record, sort_keys=True):
        raise ValueError("audio source, parser or archive tool differs on replay")
    return True
