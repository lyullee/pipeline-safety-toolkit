"""Source-pinned clip diagnostics using the existing PCM reader and SciPy Welch.

Digital full-scale features are not calibrated pressure, strain or detection
performance. Physical grouping is required for an explicit monitoring handoff.
"""

from __future__ import annotations

import inspect
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from . import audio_data
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .monitoring_exchange import MonitoringSplit

REVIEWS = ("digital_pcm_and_full_scale_meaning", "source_clip_selection_and_welch_resolution")
SHA = r"^[a-f0-9]{64}$"


class AudioBand(Contract):
    id: Name
    lower_Hz: Nonnegative
    upper_Hz: Positive

    @model_validator(mode="after")
    def ordered(self):
        if self.lower_Hz >= self.upper_Hz:
            raise ValueError("frequency band must increase")
        return self


class AudioStudyRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    archive_filename: Name
    member: Name
    expected_source_record_digest: str | None = Field(default=None, pattern=SHA)
    start_frame: int = Field(ge=0, le=2000000)
    frame_count: int = Field(ge=8, le=2000000)
    maximum_source_frames: int = Field(default=100000, ge=8, le=2000000)
    source_timeout_s: Positive = Field(default=20.0, le=120)
    window: Literal["hann", "boxcar"]
    nperseg: int = Field(ge=8, le=65536)
    noverlap: int = Field(ge=0, le=65535)
    detrend: Literal["constant", "none"]
    bands: tuple[AudioBand, ...] = Field(default=(), max_length=64)
    maximum_welch_segments: int = Field(default=10000, ge=1, le=100000)
    declared_condition: Literal["unassigned", "publisher_leak_folder", "publisher_no_leak_folder"] = (
        "unassigned"
    )
    evidence_id: Name
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    runtime_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def support(self):
        path = PurePosixPath(self.archive_filename)
        if (
            path.name != self.archive_filename
            or "\\" in self.archive_filename
            or ":" in self.archive_filename
            or self.archive_filename in {".", ".."}
        ):
            raise ValueError("archive must be a source-root basename")
        if (
            self.noverlap >= self.nperseg
            or self.nperseg > self.frame_count
            or self.start_frame + self.frame_count > self.maximum_source_frames
        ):
            raise ValueError("selected frames/Welch window exceed the explicit source scope")
        segments = 1 + (self.frame_count - self.nperseg) // (self.nperseg - self.noverlap)
        if segments > self.maximum_welch_segments:
            raise ValueError("Welch segment work budget exceeded")
        if set(self.reviews) != set(REVIEWS) or len({b.id for b in self.bands}) != len(self.bands):
            raise ValueError("named reviews and distinct band IDs required")
        ordered = sorted(self.bands, key=lambda b: b.lower_Hz)
        if any(b.lower_Hz < a.upper_Hz for a, b in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("bands must not overlap; shared edges use half-open frequency bins")
        return self


def runtime_metadata():
    import numpy as np
    from scipy import fft, signal
    from scipy.signal import windows

    return {
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "scipy": version("scipy"),
        "welch_source_sha256": file_digest(Path(inspect.getfile(signal.welch))),
        # Pin public entry points. SciPy's private _windows and
        # _pocketfft modules are implementation details that move between
        # releases and are not reliable provenance interfaces.
        "window_source_sha256": file_digest(Path(windows.__file__)),
        "fft_api_source_sha256": file_digest(Path(fft.__file__)),
        "numpy_source_sha256": file_digest(Path(np.__file__)),
        "pcm_reader_sha256": file_digest(Path(audio_data.__file__)),
        "implementation_sha256": file_digest(Path(__file__)),
        "licenses": {"scipy": "BSD-3-Clause", "numpy": "BSD-3-Clause"},
    }


def boundary_metadata(q):
    return {
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "request_digest": digest(q),
        "clock_meaning": "file-relative sample index divided by source sample rate; no physical synchronization",
    }


def signal_origin_for_audio_source(source):
    if source["source_origin"] != "measured_process":
        raise ValueError("selected training-base source is not the reviewed measured-process origin")
    return "measured"


def _source(q, source_root, tar_executable):
    root = Path(source_root).resolve(strict=True)
    selected = root / q.archive_filename
    if selected.is_symlink() or not selected.resolve(strict=True).is_relative_to(root):
        raise ValueError("selected audio archive leaves the explicit source root")
    return audio_data.read_training_audio(
        selected,
        q.member,
        tar_executable=tar_executable,
        timeout_s=q.source_timeout_s,
        maximum_frames=q.maximum_source_frames,
    )


def evaluate_audio_clip(request, context, *, source_root, tar_executable):
    q = AudioStudyRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "audio_study_boundary"
    ) != boundary_metadata(q):
        raise ValueError("actual collection/relative-clock/source-processing context differs")
    ids = [e.id for e in c.evidence]
    if len(set(ids)) != len(ids) or {q.evidence_id, *q.review_evidence_ids.values()} - set(ids):
        raise ValueError("source and review evidence must be actual unique context members")
    missing = ["review:" + k for k in REVIEWS if not q.reviews[k] or k not in q.review_evidence_ids]
    if q.expected_source_record_digest is None:
        missing.append("preselected actual PCM source receipt")
    if not q.runtime_pins:
        missing.append("reviewed signal/runtime source pins")
    body = {
        "schema": "pipeline_integrity.audio_study.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "missing": missing,
        "native_called": False,
        "provider": None,
        "source_receipt": None,
        "values": None,
        "spectrum": None,
        "physical_amplitude_calibrated": False,
        "physical_amplitude_unit": None,
        "physical_event_group_inferred": False,
        "independent_experiment_count": None,
        "leak_probability": None,
        "probability_of_detection": None,
        "HEP": None,
        "trained_or_independently_validated": False,
        "third_party_environmental_noise_used": False,
    }
    if missing:
        return {**body, "record_digest": digest(body)}
    before = runtime_metadata()
    if before != q.runtime_pins:
        raise ValueError("reviewed signal/runtime/source pins differ")
    source = _source(q, source_root, tar_executable)
    if source["record_digest"] != q.expected_source_record_digest:
        raise ValueError("actual selected archive/member/PCM source receipt differs")
    expected_condition = {
        "publisher_leak_folder": "leak acoustic data.rar",
        "publisher_no_leak_folder": "no leak acoustic data.rar",
    }
    if (
        q.declared_condition != "unassigned"
        and expected_condition[q.declared_condition] != source["archive_filename"]
    ):
        raise ValueError("declared publisher folder condition differs from actual selected archive")
    pcm = source["observations"]
    if c.origin != signal_origin_for_audio_source(source):
        raise ValueError("actual source observation origin differs from context")
    if q.start_frame + q.frame_count > pcm["sample_frames"]:
        raise ValueError("selected clip exceeds actual source frame count")
    fs = pcm["sample_rate_hz"]
    if any(b.upper_Hz > fs / 2 for b in q.bands):
        raise ValueError("frequency band exceeds source Nyquist support")
    import numpy as np
    from scipy import signal

    bits = 8 * pcm["sample_width_bytes"]
    full_scale = 2 ** (bits - 1)
    codes = np.asarray(pcm["pcm_codes"][q.start_frame : q.start_frame + q.frame_count], dtype=np.int64)
    x = codes.astype(np.float64) / full_scale
    window = signal.get_window(q.window, q.nperseg, fftbins=True)
    frequency, psd = signal.welch(
        x,
        fs=fs,
        window=window,
        nperseg=q.nperseg,
        noverlap=q.noverlap,
        nfft=q.nperseg,
        detrend="constant" if q.detrend == "constant" else False,
        return_onesided=True,
        scaling="density",
        average="mean",
    )
    body["native_called"] = True
    if not np.isfinite(psd).all() or np.any(psd < 0):
        raise ValueError("native Welch PSD must be finite nonnegative")
    df = fs / q.nperseg
    total = float(np.sum(psd) * df)
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    powers = []
    for band in q.bands:
        mask = (frequency >= band.lower_Hz) & (
            (frequency < band.upper_Hz) | ((band.upper_Hz == fs / 2) & (frequency == fs / 2))
        )
        count = int(np.count_nonzero(mask))
        powers.append(
            {
                "id": band.id,
                "grid_bins": count,
                "power_full_scale_squared": None if count == 0 else float(np.sum(psd[mask]) * df),
                "support": "selected DFT bin centers in [lower, upper); Nyquist endpoint included when upper=fs/2",
            }
        )
    body.update(
        status="computed_diagnostic" if all(p["grid_bins"] for p in powers) else "needs_data",
        missing=[]
        if all(p["grid_bins"] for p in powers)
        else ["a selected frequency band contains no resolved DFT bin"],
        provider=before,
        source_receipt={k: v for k, v in source.items() if k != "observations"},
        values={
            "sample_rate_hz": fs,
            "selected_frames": q.frame_count,
            "relative_start_s": q.start_frame / fs,
            "relative_end_exclusive_s": (q.start_frame + q.frame_count) / fs,
            "full_scale_normalization_divisor": full_scale,
            "amplitude_basis": "centered integer PCM divided by 2^(bits-1); digital only",
            "mean_full_scale": float(np.mean(x)),
            "rms_full_scale": rms,
            "peak_absolute_full_scale": peak,
            "crest_factor": None if rms == 0 else peak / rms,
            "rms_dBFS": None if rms == 0 else 20 * math.log10(rms),
            "edge_code_fraction": float(
                np.count_nonzero((codes == -full_scale) | (codes == full_scale - 1)) / q.frame_count
            ),
            "edge_code_meaning": "digitizer endpoint occupancy; neither analog clipping proof nor calibration",
            "welch_segment_count": 1 + (q.frame_count - q.nperseg) // (q.nperseg - q.noverlap),
            "welch_ignored_tail_frames": (q.frame_count - q.nperseg) % (q.nperseg - q.noverlap),
            "frequency_resolution_Hz": df,
            "welch_total_bin_power_full_scale_squared": total,
            "spectral_centroid_Hz": None if total == 0 else float(np.sum(frequency * psd) * df / total),
            "band_powers": powers,
            "declared_condition": q.declared_condition,
            "condition_meaning": "publisher folder annotation only; no physical cause or independent trial label inferred",
        },
        spectrum={"frequency_Hz": frequency.tolist(), "PSD_full_scale_squared_per_Hz": psd.tolist()},
    )
    if runtime_metadata() != before:
        raise ValueError("signal/runtime/source changed during diagnostics")
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def verify_audio_study_record(record, *, source_root, tar_executable, request=None, context=None):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("audio study receipt changed")
    q = AudioStudyRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if (request is not None and request != q) or (context is not None and context != c):
        raise ValueError("caller request/context differs")
    if evaluate_audio_clip(q, c, source_root=source_root, tar_executable=tar_executable) != record:
        raise ValueError("actual selected PCM source/native signal replay differs")
    return True


class AudioMonitoringBinding(Contract):
    study_record_digest: str = Field(pattern=SHA)
    analysis_clock_id: Name
    clip_start_time_s: Nonnegative | None
    physical_event_id: Name | None
    device_id: Name | None
    site_id: Name | None
    source_group_id: Name | None
    calibration_basis_id: Name | None
    event_device_site_clock_and_calibration_reviewed: bool = False
    evidence_id: Name


def monitoring_boundary_metadata(records, bindings, *, role, split_id, reference_kind):
    return {
        "study_record_digests": [r["record_digest"] for r in records],
        "physical_bindings_digest": digest(tuple(bindings)),
        "role": role,
        "split_id": split_id,
        "reference_kind": reference_kind,
        "meaning": "explicit single physical group assignment; no independent split inferred",
    }


def prepare_audio_monitoring_split(
    records, bindings, context, *, role, split_id, reference_kind, source_root, tar_executable
):
    """Build one explicitly assigned shared split; never choose train/test groups or fit a detector."""
    if (
        role not in {"training", "calibration", "evaluation"}
        or not 1 <= len(records) <= 10000
        or len(records) != len(bindings)
    ):
        raise ValueError("one bounded explicitly assigned role and aligned clip bindings required")
    c = RunContext.model_validate(context.model_dump(mode="python"))
    bs = tuple(AudioMonitoringBinding.model_validate(b.model_dump(mode="python")) for b in bindings)
    ids = [e.id for e in c.evidence]
    if len(set(ids)) != len(ids) or {b.evidence_id for b in bs} - set(ids):
        raise ValueError("physical group/calibration bindings need actual unique context evidence")
    missing = []
    for i, b in enumerate(bs):
        if b.study_record_digest != records[i].get("record_digest") or b.analysis_clock_id != c.clock_id:
            raise ValueError("physical binding does not match actual clip or analysis clock")
        if not b.event_device_site_clock_and_calibration_reviewed or any(
            getattr(b, k) is None
            for k in (
                "clip_start_time_s",
                "physical_event_id",
                "device_id",
                "site_id",
                "source_group_id",
                "calibration_basis_id",
            )
        ):
            missing.append(f"clip:{i}:reviewed physical grouping/clock/calibration")
    body = {
        "schema": "pipeline_integrity.audio_monitoring_preparation.v1",
        "status": "needs_data",
        "missing": missing,
        "bindings": [b.model_dump(mode="json") for b in bs],
        "split": None,
        "trained": False,
        "independent_split_generated": False,
        "performance_claim": None,
    }
    if missing:
        return {**body, "record_digest": digest(body)}
    if c.metadata.get("audio_monitoring_boundary") != monitoring_boundary_metadata(
        records, bs, role=role, split_id=split_id, reference_kind=reference_kind
    ):
        raise ValueError(
            "actual monitoring context must bind the exact clips, physical groups and assigned role"
        )
    common = {
        (b.physical_event_id, b.device_id, b.site_id, b.source_group_id, b.calibration_basis_id) for b in bs
    }
    if len(common) != 1:
        raise ValueError(
            "one shared MonitoringSplit must represent one explicit physical event/device/site/source group"
        )
    frames = []
    observations = []
    previous_end = None
    processing = None
    for record, b in zip(records, bs, strict=True):
        verify_audio_study_record(record, source_root=source_root, tar_executable=tar_executable)
        if (
            record["status"] != "computed_diagnostic"
            or record["context"]["asset_id"] != c.asset_id
            or record["context"]["origin"] != c.origin
        ):
            raise ValueError("computed clip diagnostics and actual asset/origin match are required")
        q = AudioStudyRequest.model_validate_json(json.dumps(record["request"]))
        recipe = (
            q.window,
            q.nperseg,
            q.noverlap,
            q.detrend,
            digest(q.bands),
            record["values"]["sample_rate_hz"],
            record["values"]["selected_frames"],
            record["values"]["full_scale_normalization_divisor"],
        )
        if processing is not None and recipe != processing:
            raise ValueError(
                "monitoring clip features must share the same explicit processing/digitizer basis"
            )
        processing = recipe
        start = b.clip_start_time_s + record["values"]["relative_start_s"]
        end = b.clip_start_time_s + record["values"]["relative_end_exclusive_s"]
        if not math.isfinite(end) or end <= start or (previous_end is not None and start < previous_end):
            raise ValueError("explicit clip supports must be representable, ordered and nonoverlapping")
        values = {
            k: record["values"][k]
            for k in ("rms_full_scale", "peak_absolute_full_scale", "edge_code_fraction")
        }
        for power in record["values"]["band_powers"]:
            values["band_power:" + power["id"]] = power["power_full_scale_squared"]
        frames.append(
            SignalFrame(
                stream_id=f"{c.asset_id}/audio/{bs[0].device_id}",
                clock_id=c.clock_id,
                time_s=end,
                values=values,
                units=dict.fromkeys(values, "1"),
                valid=dict.fromkeys(values, True),
                origin=c.origin,
            )
        )
        observations.append(record["record_digest"])
        previous_end = end
    split = MonitoringSplit(
        role=role,
        split_id=split_id,
        physical_run_id=bs[0].physical_event_id,
        source_group_id=bs[0].source_group_id,
        source_evidence_id=bs[0].evidence_id,
        observation_ids=tuple(observations),
        frames=tuple(frames),
        reference_kind=reference_kind,
    )
    body.update(status="prepared", split=split.model_dump(mode="json"))
    return {**body, "record_digest": digest(body)}
