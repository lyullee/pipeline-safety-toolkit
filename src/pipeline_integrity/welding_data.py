"""Read the reviewed Tandem-GMAW NetCDF members without extracting their ZIPs.

Native signs, units, row order and invalid observations remain available.  The
reader does not infer a weld path, arc efficiency or procedure qualification.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from numbers import Real
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

import numpy as np

from .contracts import SignalFrame, digest
from .datasets import catalog

_MAXIMUM_ARCHIVE_BYTES = 256_000_000
_MAXIMUM_MEMBER_BYTES = 64_000_000
_MAXIMUM_DECODED_BYTES = 256_000_000
_MAXIMUM_SAMPLES = 10_000_000
_CHANNEL_UNITS = {
    "process": {
        "current_leading": "A",
        "current_trailing": "A",
        "voltage_leading": "V",
        "voltage_trailing": "V",
        "video_frame": "",
        "video_recording": "",
    },
    "robot": {
        "arc_on": "",
        "tcp_tool_number": "",
        "gas_flow_leading": "liter/min",
        "gas_flow_trailing": "liter/min",
        "power_leading": "",
        "power_trailing": "",
        "power_robot": "",
        "thermo_1": "°C",
        "thermo_2": "°C",
        "tcp_coords": "mm",
        "tcp_angles": "deg",
    },
}
_WALL_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?")


def _text(value):
    """NetCDF scalar strings sometimes arrive as singleton HDF attributes."""
    if isinstance(value, np.ndarray):
        if value.size != 1:
            raise ValueError("source text attribute must contain exactly one value")
        value = value.reshape(-1)[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict")
    if not isinstance(value, str):
        raise ValueError("source text attribute is missing or is not a string")
    return value


def _wall_time(value):
    match = _WALL_TIME.fullmatch(value)
    if not match:
        raise ValueError("source epoch must be an ISO wall time with at most nanosecond precision")
    whole = datetime.strptime(f"{match[1]}T{match[2]}", "%Y-%m-%dT%H:%M:%S")
    nanos = int((match[3] or "").ljust(9, "0"))
    fraction = f".{nanos:09d}".rstrip("0") if nanos else ""
    canonical = whole.isoformat() + fraction
    return whole, nanos, canonical


def _utc_time(value):
    if isinstance(value, datetime):
        if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("an explicit UTC origin is required")
        value = value.astimezone(timezone.utc).isoformat()
    if not isinstance(value, str) or not value.endswith(("+00:00", "Z")):
        raise ValueError("an explicit UTC origin with Z or +00:00 is required")
    wall = value[:-1] if value.endswith("Z") else value[:-6]
    whole, nanos, canonical = _wall_time(wall)
    return whole, nanos, canonical + "+00:00"


def _seal(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _seal(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_seal(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _readonly(value):
    array = np.ascontiguousarray(value)
    # A bytes owner also prevents callers from re-enabling numpy's write flag.
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _array_digest(array):
    result = hashlib.sha256()
    result.update(str(array.dtype).encode("ascii"))
    result.update(repr(array.shape).encode("ascii"))
    result.update(array.tobytes(order="C"))
    return result.hexdigest()


@dataclass(frozen=True)
class WeldRecording:
    """An immutable native recording; ``summary()`` returns a JSON-safe copy.

    ``time_s`` is relative to the source's stated UTC epoch.  Array invalidity
    is represented separately, so finite thermocouple faults are not replaced.
    """

    record_id: str
    kind: Literal["process", "robot"]
    time_s: np.ndarray
    values: Mapping[str, np.ndarray]
    units: Mapping[str, str]
    valid: Mapping[str, np.ndarray]
    metadata: Mapping
    _integrity_sha256: str = field(init=False, repr=False)

    def __post_init__(self):
        if self.kind not in _CHANNEL_UNITS or not self.record_id:
            raise ValueError("a reviewed recording kind and record ID are required")
        time = np.asarray(self.time_s)
        if (
            time.dtype != np.dtype("float64")
            or time.ndim != 1
            or not 1 <= len(time) <= _MAXIMUM_SAMPLES
            or not np.isfinite(time).all()
            or np.any(time < 0)
            or np.any(time[1:] <= time[:-1])
        ):
            raise ValueError("recording clock must be finite, nonnegative and strictly increasing")
        if not self.values or set(self.values) != set(self.units) or set(self.values) != set(self.valid):
            raise ValueError("recording channels, units and validity must agree")
        values, validity = {}, {}
        for name, source in self.values.items():
            array, valid = np.asarray(source), np.asarray(self.valid[name])
            if (
                array.dtype != np.dtype("float64")
                or array.ndim not in (1, 2)
                or array.shape[0] != len(time)
                or valid.dtype != np.dtype("bool")
                or valid.shape != array.shape
                or np.any(valid & ~np.isfinite(array))
            ):
                raise ValueError("native values and validity must have matching numeric shapes")
            values[name], validity[name] = _readonly(array), _readonly(valid)
        object.__setattr__(self, "time_s", _readonly(time))
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "valid", MappingProxyType(validity))
        object.__setattr__(self, "units", _seal(dict(self.units)))
        object.__setattr__(self, "metadata", _seal(_plain(self.metadata)))
        object.__setattr__(self, "_integrity_sha256", self._fingerprint())

    def _fingerprint(self):
        return digest(
            {
                "record_id": self.record_id,
                "kind": self.kind,
                "time_s": _array_digest(self.time_s),
                "values": {key: _array_digest(value) for key, value in self.values.items()},
                "valid": {key: _array_digest(value) for key, value in self.valid.items()},
                "units": _plain(self.units),
                "metadata": _plain(self.metadata),
            }
        )

    def _check_integrity(self):
        if self._fingerprint() != self._integrity_sha256:
            raise ValueError("recording arrays or source metadata changed after reading")

    def summary(self):
        """Copy complete source-quality summaries, excluding source file paths and sample arrays."""
        self._check_integrity()
        return _plain(self.metadata)

    def frames(
        self,
        *,
        start_s=None,
        finish_s=None,
        channels: tuple[str, ...],
        common_origin_utc=None,
        maximum_samples=100_000,
    ) -> tuple[tuple[SignalFrame, ...], dict]:
        """Select inclusive source-relative bounds; align only to an explicit UTC origin.

        No resampling or downsampling is performed.  A common origin must precede
        selected observations because SignalFrame uses a nonnegative clock.
        """
        self._check_integrity()
        if (
            not isinstance(channels, tuple)
            or not channels
            or any(not isinstance(name, str) for name in channels)
            or len(set(channels)) != len(channels)
            or set(channels) - set(self.values)
        ):
            raise ValueError("select a nonempty tuple of unique native channel names")
        if any(self.values[name].ndim != 1 for name in channels):
            raise ValueError("frames require scalar 1D channels; robot coordinates remain native arrays")
        if type(maximum_samples) is not int or not 1 <= maximum_samples <= _MAXIMUM_SAMPLES:
            raise ValueError("maximum_samples must be an integer from 1 through 10000000")
        for bound in (start_s, finish_s):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, Real)
                or not math.isfinite(bound)
                or bound < 0
            ):
                raise ValueError("selection bounds must be finite nonnegative source-relative seconds")
        if start_s is not None and finish_s is not None and start_s > finish_s:
            raise ValueError("selection finish precedes its start")
        first = 0 if start_s is None else int(np.searchsorted(self.time_s, start_s, side="left"))
        stop = (
            len(self.time_s)
            if finish_s is None
            else int(np.searchsorted(self.time_s, finish_s, side="right"))
        )
        if first == stop:
            raise ValueError("the requested source-relative window contains no samples")
        if stop - first > maximum_samples:
            raise ValueError(
                "selection exceeds maximum_samples; explicitly raise the limit or select a window"
            )
        source_whole, source_ns, source_origin = _utc_time(self.metadata["time_origin_utc"])
        whole, nanos, origin = _utc_time(source_origin if common_origin_utc is None else common_origin_utc)
        delta = source_whole - whole
        offset_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + source_ns - nanos
        aligned = self.time_s[first:stop] + offset_ns / 1_000_000_000
        if np.any(aligned < 0) or not np.isfinite(aligned).all() or np.any(aligned[1:] <= aligned[:-1]):
            raise ValueError("common origin creates negative, nonfinite or unresolved observation times")
        clock_id = "tandem_gmaw:utc_origin:" + digest({"origin_utc": origin})
        units = {name: self.units[name] for name in channels}
        frames = tuple(
            SignalFrame(
                stream_id=f"{self.record_id}:{self.kind}",
                clock_id=clock_id,
                time_s=float(time),
                values={
                    name: float(self.values[name][index]) if np.isfinite(self.values[name][index]) else None
                    for name in channels
                },
                units=units.copy(),
                valid={name: bool(self.valid[name][index]) for name in channels},
                origin="laboratory",
            )
            for index, time in zip(range(first, stop), aligned, strict=True)
        )
        selection = {
            "schema": "pipeline_integrity.tandem_gmaw_selection.v1",
            "record_id": self.record_id,
            "kind": self.kind,
            "recording_integrity_sha256": self._integrity_sha256,
            "source": self.summary(),
            "channels": list(channels),
            "requested_bounds_s": [
                None if start_s is None else float(start_s),
                None if finish_s is None else float(finish_s),
            ],
            "bounds_inclusive": True,
            "source_index_start": first,
            "source_index_stop_exclusive": stop,
            "selected_samples": stop - first,
            "source_samples": len(self.time_s),
            "source_full_span_s": [float(self.time_s[0]), float(self.time_s[-1])],
            "selected_source_bounds_s": [float(self.time_s[first]), float(self.time_s[stop - 1])],
            "selected_aligned_bounds_s": [float(aligned[0]), float(aligned[-1])],
            "full_source_span_selected": first == 0 and stop == len(self.time_s),
            "maximum_samples": maximum_samples,
            "common_origin_explicit": common_origin_utc is not None,
            "common_origin_utc": origin,
            "source_origin_offset_ns": offset_ns,
            "clock_id": clock_id,
            "resampled": False,
            "samples_dropped_within_selection": 0,
        }
        return frames, selection | {"selection_digest": digest(selection)}


def _quality(array, valid):
    finite = np.isfinite(array)
    return {
        "shape": list(array.shape),
        "elements": int(array.size),
        "finite": int(finite.sum()),
        "nan": int(np.isnan(array).sum()),
        "positive_infinity": int(np.isposinf(array).sum()),
        "negative_infinity": int(np.isneginf(array).sum()),
        "valid": int(valid.sum()),
        "invalid_finite": int((finite & ~valid).sum()),
        "rows_with_invalid": int((~valid).sum()) if array.ndim == 1 else int((~valid).any(axis=1).sum()),
        "minimum_finite": float(array[finite].min()) if finite.any() else None,
        "maximum_finite": float(array[finite].max()) if finite.any() else None,
    }


def _parse_member(payload, *, kind, record_id, source):
    # Optional native library is deliberately loaded after supplier verification.
    h5py = importlib.import_module("h5py")
    expected = _CHANNEL_UNITS[kind]
    with h5py.File(BytesIO(payload), "r") as native:
        expected_keys = set(expected) | {"time"} | ({"c"} if kind == "robot" else set())
        if set(native) != expected_keys:
            raise ValueError("source datasets differ from the reviewed recording schema")
        # Do not follow external/soft HDF links, including the coordinate scale.
        if any(not isinstance(native.get(name, getlink=True), h5py.HardLink) for name in native):
            raise ValueError("source recording must contain local datasets only")
        if any(not isinstance(native[name], h5py.Dataset) for name in native):
            raise ValueError("source datasets differ from the reviewed recording schema")
        if any(native[name].is_virtual or native[name].external for name in native):
            raise ValueError("source datasets cannot reference external or virtual storage")
        if sum(native[name].nbytes for name in native) > _MAXIMUM_DECODED_BYTES:
            raise ValueError("source dataset arrays exceed their decoded byte budget")
        time = native["time"]
        if time.ndim != 1 or time.dtype != np.dtype("int64") or not 1 <= len(time) <= _MAXIMUM_SAMPLES:
            raise ValueError("source clock must be a bounded nonempty int64 vector")
        raw_units = _text(time.attrs.get("units"))
        tick_name = "nanoseconds" if kind == "process" else "milliseconds"
        if not raw_units.startswith(tick_name + " since "):
            raise ValueError("source clock units differ from the reviewed recording kind")
        whole, nanos, _ = _wall_time(raw_units[len(tick_name + " since ") :])
        root_whole, root_nanos, origin = _utc_time(_text(native.attrs.get("start_time")))
        if whole != root_whole or nanos != root_nanos:
            raise ValueError("naive CF clock epoch disagrees with the explicit UTC root start_time")
        if _text(time.attrs.get("calendar")) != "proleptic_gregorian":
            raise ValueError("source clock calendar differs from the reviewed schema")
        ticks = time[...]
        if np.any(ticks < 0) or np.any(ticks[1:] <= ticks[:-1]):
            raise ValueError("source clock is negative, repeated or decreasing; samples were not repaired")
        values, units, validity = {}, {}, {}
        for name, unit in expected.items():
            node = native[name]
            shape = (len(ticks), 3) if name in {"tcp_coords", "tcp_angles"} else (len(ticks),)
            if node.shape != shape or node.dtype != np.dtype("float64"):
                raise ValueError(f"source channel {name} shape or numeric type changed")
            if _text(node.attrs.get("units")) != unit:
                raise ValueError(f"source channel {name} units changed")
            array = node[...]
            valid = np.isfinite(array)
            if unit == "°C":
                valid &= array >= -273.15
            values[name], units[name], validity[name] = array, unit, valid
        if kind == "robot":
            coordinates = tuple(_text(item) for item in native["c"][...])
            if coordinates != ("x", "y", "z"):
                raise ValueError("source robot coordinate component labels changed")
    scale = 1e-9 if kind == "process" else 1e-3
    time_s = ticks.astype(np.float64) * scale
    if np.any(time_s[1:] <= time_s[:-1]):
        raise ValueError("native tick precision cannot be represented by the relative float clock")
    steps = np.diff(ticks)
    metadata = source | {
        "schema": "pipeline_integrity.tandem_gmaw_recording.v1",
        "record_id": record_id,
        "kind": kind,
        "time_units_raw": raw_units,
        "time_origin_utc": origin,
        "calendar": "proleptic_gregorian",
        "time_ticks_sha256": _array_digest(ticks),
        "channel_sha256": {name: _array_digest(array) for name, array in values.items()},
        "channel_units": units.copy(),
        "source_samples": len(ticks),
        "source_full_span_s": [float(time_s[0]), float(time_s[-1])],
        "clock_quality": {
            "strictly_increasing": True,
            "minimum_step_ticks": int(steps.min()) if steps.size else None,
            "maximum_step_ticks": int(steps.max()) if steps.size else None,
            "minimum_step_s": float(steps.min() * scale) if steps.size else None,
            "maximum_step_s": float(steps.max() * scale) if steps.size else None,
            "distinct_step_counts": {
                str(int(value)): int(count)
                for value, count in zip(*np.unique(steps, return_counts=True), strict=True)
            },
            "tick_unit": tick_name,
            "first_tick": int(ticks[0]),
            "last_tick": int(ticks[-1]),
            "samples_dropped": 0,
            "sorted_or_repaired": False,
        },
        "quality": {name: _quality(array, validity[name]) for name, array in values.items()},
        "origin": "laboratory",
        "temperature_validity_rule": "finite and Celsius >= -273.15; original values retained",
        "empty_units_are_unresolved": True,
        "coordinate_path_inferred": False,
    }
    if kind == "process":
        metadata["electrical_product_quality"] = {
            torch: {
                "finite_pairs": int(
                    (np.isfinite(values[f"voltage_{torch}"]) & np.isfinite(values[f"current_{torch}"])).sum()
                ),
                "negative_products": int(
                    ((values[f"voltage_{torch}"] * values[f"current_{torch}"]) < 0).sum()
                ),
                "polarity_rectified": False,
            }
            for torch in ("leading", "trailing")
        }
    return WeldRecording(record_id, kind, time_s, values, units, validity, metadata)


def read_tandem_recording(archive, kind: Literal["process", "robot"] = "process") -> WeldRecording:
    """Verify a catalog-pinned supplier ZIP, then read one reviewed HDF member.

    Supported assets are WID631 layers 1 and 2, run T1, from the v2 source.
    Archive/member byte budgets are fixed; raw members are never extracted.
    """
    if kind not in _CHANNEL_UNITS:
        raise ValueError("kind must be process or robot")
    path = Path(archive)
    if not re.fullmatch(r"WID631_L[12][.]zip", path.name):
        raise ValueError("select a reviewed WID631_L1.zip or WID631_L2.zip asset")
    specification = next((row for row in catalog() if row.id == "tandem_gmaw"), None)
    asset = (
        next((item for item in specification.assets if item.filename == path.name), None)
        if specification
        else None
    )
    if not asset or not asset.checksum or asset.checksum_origin != "publisher_metadata":
        raise ValueError("a pinned supplier checksum is required before reading")
    algorithm, expected_checksum = asset.checksum.split(":", 1)
    with path.open("rb") as stream:
        original_stat = path.stat()
        if not 1 <= original_stat.st_size <= _MAXIMUM_ARCHIVE_BYTES:
            raise ValueError("source archive exceeds the reviewed byte budget")
        if asset.bytes is not None and original_stat.st_size != asset.bytes:
            raise ValueError("source archive size differs from its supplier metadata")
        supplier, sha256 = hashlib.new(algorithm), hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            supplier.update(block)
            sha256.update(block)
        if supplier.hexdigest() != expected_checksum:
            raise ValueError("source archive failed its pinned supplier checksum")
        stream.seek(0)
        record_id = path.stem + "_T1"
        member = f"{record_id}_{kind}.nc"
        with zipfile.ZipFile(stream) as zipped:
            infos = zipped.infolist()
            if len(infos) > 64 or sum(item.file_size for item in infos) > 512_000_000:
                raise ValueError("source ZIP directory exceeds the reviewed member budget")
            if len({item.filename for item in infos}) != len(infos):
                raise ValueError("source ZIP contains duplicate member paths")
            for info in infos:
                # ZipInfo normalizes Windows separators and truncates NULs;
                # inspect the original directory name as well.
                logical = PurePosixPath(info.orig_filename)
                if (
                    logical.is_absolute()
                    or ".." in logical.parts
                    or "\\" in info.orig_filename
                    or ":" in info.orig_filename
                    or info.orig_filename != info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                ):
                    raise ValueError("source ZIP contains an unsafe member path or link")
            if member not in zipped.namelist():
                raise ValueError("reviewed record ID and kind member are absent from the source ZIP")
            info = zipped.getinfo(member)
            if info.is_dir() or not 1 <= info.file_size <= _MAXIMUM_MEMBER_BYTES:
                raise ValueError("source NetCDF member exceeds its supported byte budget")
            payload = zipped.read(member)
        current_stat = path.stat()
        if (original_stat.st_size, original_stat.st_mtime_ns) != (
            current_stat.st_size,
            current_stat.st_mtime_ns,
        ):
            raise ValueError("source archive changed while it was being read")
    source = {
        "dataset_id": specification.id,
        "source_version": specification.version,
        "citation": specification.citation,
        "license_id": specification.license_id,
        "archive_filename": path.name,
        "archive_bytes": original_stat.st_size,
        "archive_sha256": sha256.hexdigest(),
        "supplier_checksum": asset.checksum,
        "supplier_checksum_verified": True,
        "member": member,
        "member_bytes": len(payload),
        "member_crc32": f"{info.CRC:08x}",
        "member_sha256": hashlib.sha256(payload).hexdigest(),
        "raw_in_code_release": False,
    }
    return _parse_member(payload, kind=kind, record_id=record_id, source=source)
