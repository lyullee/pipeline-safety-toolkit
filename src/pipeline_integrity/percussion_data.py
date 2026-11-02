"""Bounded selected-member reader for the induced-corrosion pipe-percussion archive."""

from __future__ import annotations

import csv
import math
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal
from dataclasses import dataclass

from pydantic import Field

from .contracts import Contract, Name, digest
from .datasets import file_digest, inspect_rar

ARCHIVE_BYTES = 5_426_985_868
ARCHIVE_SHA256 = "f22a5d01347a2dce8830763954a6cb99ba3914ec97070277096370f52e0529c6"
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_SAMPLES = 2_000_000


class PercussionRecording(Contract):
    """One tap response; the arrays are source observations and must stay private."""

    dataset_id: Literal["percussion"] = "percussion"
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    member_path: Name
    member_bytes: int = Field(gt=0, le=MAX_MEMBER_BYTES)
    condition: Literal["A", "B", "C", "D", "E", "F", "G"]
    tap_position: int = Field(ge=1, le=5)
    stroke_number: int = Field(ge=1, le=110)
    timestamp_header: Literal["时间 - cDAQ9185-1D34FAFMod1/ai0"]
    pressure_header: Literal["Pascals - cDAQ9185-1D34FAFMod1/ai0"]
    time_unit: Literal["s"] = "s"
    pressure_unit: Literal["Pa"] = "Pa"
    times_s: tuple[float, ...] = Field(min_length=8, max_length=MAX_SAMPLES)
    pressure_pa: tuple[float, ...] = Field(min_length=8, max_length=MAX_SAMPLES)
    row_count: int = Field(ge=8, le=MAX_SAMPLES)
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        if len(self.times_s) != self.row_count or len(self.pressure_pa) != self.row_count:
            raise ValueError("percussion samples and declared row count differ")
        if any(not math.isfinite(value) for value in (*self.times_s, *self.pressure_pa)):
            raise ValueError("percussion samples must be finite")
        if any(right <= left for left, right in zip(self.times_s, self.times_s[1:])):
            raise ValueError("percussion timestamps must strictly increase")
        if digest(self.model_dump(mode="json", exclude={"source_digest"})) != self.source_digest:
            raise ValueError("percussion recording source digest differs")
        return True


def _member_identity(member_path):
    if not isinstance(member_path, str) or "\\" in member_path or ":" in member_path:
        raise ValueError("select one canonical POSIX RAR member path")
    logical = PurePosixPath(member_path)
    if logical.is_absolute() or ".." in logical.parts or logical.as_posix() != member_path:
        raise ValueError("selected RAR member path is unsafe")
    match = re.fullmatch(r"Pipe percussion dataset/([A-G])/([1-5])/([a-e]?)([0-9]{3})\.csv", member_path)
    if not match:
        raise ValueError("select a dataset condition, tap position and numbered stroke CSV")
    condition, position, prefix, stroke = match.groups()
    expected_prefix = "abcde"[int(position) - 1] if condition == "D" else ""
    if prefix != expected_prefix:
        raise ValueError("member filename prefix conflicts with the condition/tap position")
    if not 1 <= int(stroke) <= 110:
        raise ValueError("stroke number lies outside the reviewed 1..110 recording range")
    return condition, int(position), int(stroke)


def read_percussion_recording(
    archive_path,
    member_path,
    *,
    maximum_archive_bytes=ARCHIVE_BYTES,
    maximum_member_bytes=MAX_MEMBER_BYTES,
    maximum_samples=MAX_SAMPLES,
    timeout_s=300,
):
    """Stream one reviewed CSV member without extracting or materializing the RAR."""
    source = Path(archive_path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("percussion archive must be a regular non-symlink file")
    if type(maximum_archive_bytes) is not int or maximum_archive_bytes < ARCHIVE_BYTES:
        raise ValueError("explicit archive budget is below the supplier asset size")
    if type(maximum_member_bytes) is not int or not 1 <= maximum_member_bytes <= MAX_MEMBER_BYTES:
        raise ValueError("explicit member byte budget is invalid")
    if type(maximum_samples) is not int or not 8 <= maximum_samples <= MAX_SAMPLES:
        raise ValueError("explicit sample count budget is invalid")
    if type(timeout_s) is not int or not 1 <= timeout_s <= 600:
        raise ValueError("explicit member read timeout is invalid")
    if source.stat().st_size != ARCHIVE_BYTES or file_digest(source) != ARCHIVE_SHA256:
        raise ValueError("percussion archive differs from the reviewed publisher asset")
    inventory = inspect_rar(source, timeout_s=timeout_s)
    return _read_percussion_member(source, member_path, inventory, maximum_member_bytes, maximum_samples, timeout_s)


@dataclass(frozen=True)
class _ArchiveFingerprint:
    size: int
    modified_ns: int
    file_id: int


def _fingerprint(path):
    stat = path.stat()
    return _ArchiveFingerprint(stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0))


class PercussionArchiveReader:
    """Verify a reviewed archive once, then stream multiple selected members.

    The source must remain unchanged while this reader is used. Its size, mtime
    and file identity are checked before each read; callers needing stronger
    adversarial mutation guarantees should use the single-recording function,
    which recomputes the full archive digest for every call.
    """

    def __init__(self, archive_path, *, timeout_s=300):
        self.path = Path(archive_path)
        self.timeout_s = timeout_s
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("percussion archive must be a regular non-symlink file")
        if self.path.stat().st_size != ARCHIVE_BYTES or file_digest(self.path) != ARCHIVE_SHA256:
            raise ValueError("percussion archive differs from the reviewed publisher asset")
        self._fingerprint = _fingerprint(self.path)
        self._inventory = inspect_rar(self.path, timeout_s=timeout_s)

    def read(self, member_path, *, maximum_member_bytes=MAX_MEMBER_BYTES, maximum_samples=MAX_SAMPLES):
        if _fingerprint(self.path) != self._fingerprint:
            raise ValueError("percussion archive changed after reader verification")
        recording = _read_percussion_member(
            self.path, member_path, self._inventory, maximum_member_bytes, maximum_samples, self.timeout_s
        )
        if _fingerprint(self.path) != self._fingerprint:
            raise ValueError("percussion archive changed while a member was being read")
        return recording


def _read_percussion_member(source, member_path, inventory, maximum_member_bytes, maximum_samples, timeout_s):
    if type(maximum_member_bytes) is not int or not 1 <= maximum_member_bytes <= MAX_MEMBER_BYTES:
        raise ValueError("explicit member byte budget is invalid")
    if type(maximum_samples) is not int or not 8 <= maximum_samples <= MAX_SAMPLES:
        raise ValueError("explicit sample count budget is invalid")
    if type(timeout_s) is not int or not 1 <= timeout_s <= 600:
        raise ValueError("explicit member read timeout is invalid")
    condition, position, stroke = _member_identity(member_path)
    matches = [row for row in inventory if row["name"] == member_path]
    if len(matches) != 1 or matches[0]["kind"] != "regular_file":
        raise ValueError("selected recording is absent or not a regular RAR member")
    member_bytes = matches[0]["bytes"]
    if member_bytes <= 0 or member_bytes > maximum_member_bytes:
        raise ValueError("selected recording exceeds the explicit member byte budget")
    tool = os.environ.get("PIPELINE_RAR_LIST_TOOL") or shutil.which("tar")
    if not tool:
        raise FileNotFoundError("tar or PIPELINE_RAR_LIST_TOOL is required to stream this RAR member")
    process = subprocess.Popen(
        [str(tool), "-xOf", str(source.resolve()), member_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    times, pressures = [], []
    try:
        if process.stdout is None:
            raise ValueError("RAR member stream could not be opened")
        first_line = process.stdout.readline(4097)
        actual_member_bytes = len(first_line)
        if not first_line or len(first_line) > 4096 or not first_line.endswith(b"\n"):
            raise ValueError("selected percussion CSV header is empty or exceeds its line budget")
        try:
            header = next(csv.reader([first_line.decode("utf-8-sig", errors="strict")]))
        except (UnicodeDecodeError, StopIteration) as error:
            raise ValueError("selected percussion CSV header is invalid") from error
        if header != ["时间 - cDAQ9185-1D34FAFMod1/ai0", "Pascals - cDAQ9185-1D34FAFMod1/ai0"]:
            raise ValueError("selected CSV columns differ from the reviewed time/pressure header")
        for raw_line in process.stdout:
            actual_member_bytes += len(raw_line)
            if actual_member_bytes > min(maximum_member_bytes, member_bytes) or len(raw_line) > 4096:
                raise ValueError("selected CSV exceeded its member byte or row-line budget")
            try:
                row = next(csv.reader([raw_line.decode("utf-8", errors="strict")]))
            except (UnicodeDecodeError, StopIteration) as error:
                raise ValueError("selected percussion CSV row is not valid UTF-8") from error
            if not row:
                continue
            if len(row) != 2 or len(times) >= maximum_samples:
                raise ValueError("selected CSV row width or sample budget is invalid")
            try:
                time_s, pressure_pa = (float(value) for value in row)
            except ValueError as error:
                raise ValueError("selected CSV contains a nonnumeric sample") from error
            if not math.isfinite(time_s) or not math.isfinite(pressure_pa) or time_s < 0:
                raise ValueError("selected CSV contains a nonfinite or negative time value")
            if times and time_s <= times[-1]:
                raise ValueError("selected CSV time must increase strictly")
            times.append(time_s)
            pressures.append(pressure_pa)
        status = process.wait(timeout=timeout_s)
        stderr = process.stderr.read(4096).decode("utf-8", errors="replace") if process.stderr else ""
        if status != 0:
            raise ValueError("RAR member extraction failed: " + stderr[:120])
    except Exception:
        process.kill()
        process.wait()
        raise
    finally:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    if len(times) < 8:
        raise ValueError("selected percussion recording contains fewer than eight samples")
    if actual_member_bytes != member_bytes:
        raise ValueError("streamed CSV bytes differ from the RAR member-list size")
    body = {
        "dataset_id": "percussion",
        "archive_sha256": ARCHIVE_SHA256,
        "member_path": member_path,
        "member_bytes": member_bytes,
        "condition": condition,
        "tap_position": position,
        "stroke_number": stroke,
        "timestamp_header": header[0],
        "pressure_header": header[1],
        "times_s": tuple(times),
        "pressure_pa": tuple(pressures),
        "row_count": len(times),
        "time_unit": "s",
        "pressure_unit": "Pa",
    }
    result = PercussionRecording(**body, source_digest=digest(body))
    result.check_integrity()
    return result


def percussion_recording_to_sampled_trace(
    recording,
    *,
    asset_id,
    sensor_id,
    clock_id,
    quantity_id="acoustic_pressure",
    maximum_relative_jitter=0.01,
):
    """Bridge a regular measured pressure recording to the existing acoustic API.

    Sensor identity and shared clock are explicit caller inputs. This function
    does not establish tap-location geometry, between-record synchronization or
    damage truth labels.
    """
    from .acoustics import SampledTrace

    record = PercussionRecording.model_validate(recording.model_dump(mode="python"))
    record.check_integrity()
    if (
        type(maximum_relative_jitter) not in {int, float}
        or not math.isfinite(maximum_relative_jitter)
        or maximum_relative_jitter < 0
        or maximum_relative_jitter > 0.05
    ):
        raise ValueError("relative sample-jitter tolerance must be within [0, 0.05]")
    intervals = [b - a for a, b in zip(record.times_s, record.times_s[1:])]
    period = sorted(intervals)[len(intervals) // 2]
    if any(abs(delta - period) / period > maximum_relative_jitter for delta in intervals):
        raise ValueError("recording is not uniform within the caller-declared timing tolerance")
    trace = SampledTrace(
        id=f"{record.condition}-{record.tap_position}-{record.stroke_number}",
        asset_id=asset_id,
        sensor_id=sensor_id,
        clock_id=clock_id,
        start_time_s=record.times_s[0],
        sample_period_s=period,
        quantity_id=quantity_id,
        unit=record.pressure_unit,
        origin="laboratory",
        source_digest=digest(
            {
                "record_digest": record.source_digest,
                "timebase_assumption": "uniform_at_median_interval_within_caller_limit",
                "median_sample_period_s": period,
                "maximum_relative_jitter_observed": max(abs(delta - period) / period for delta in intervals),
                "maximum_relative_jitter_accepted": maximum_relative_jitter,
            }
        ),
        values=record.pressure_pa,
        valid=tuple(True for _ in record.pressure_pa),
    )
    return trace


def summarize_percussion_recording(recording):
    """Return sample/time/pressure aggregates without the observation vector."""
    record = PercussionRecording.model_validate(recording.model_dump(mode="python"))
    record.check_integrity()
    intervals = [b - a for a, b in zip(record.times_s, record.times_s[1:])]
    period = sorted(intervals)[len(intervals) // 2]
    body = {
        "dataset_id": record.dataset_id,
        "archive_sha256": record.archive_sha256,
        "member_path": record.member_path,
        "condition": record.condition,
        "tap_position": record.tap_position,
        "stroke_number": record.stroke_number,
        "row_count": record.row_count,
        "start_time_s": record.times_s[0],
        "end_time_s": record.times_s[-1],
        "median_sample_period_s": period,
        "maximum_relative_interval_deviation": max(abs(delta - period) / period for delta in intervals),
        "pressure_min_pa": min(record.pressure_pa),
        "pressure_max_pa": max(record.pressure_pa),
        "pressure_mean_pa": sum(record.pressure_pa) / record.row_count,
        "pressure_values_included": False,
        "pipeline_damage_truth_established": False,
        "field_transfer_qualified": False,
    }
    return {**body, "record_digest": digest(body)}
