"""Source-preserving NDE waveform reading and reviewed pulse-echo exchange with the owned material engine."""

import csv
import io
import math
import struct
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .adapters import ExternalResult, MaterialClient
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest


class NDEWaveform(Contract):
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    member: Name
    member_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_clock_id: Name
    source_group_label: Name
    time_s: tuple[float | None, ...] = Field(min_length=2)
    voltage_v: tuple[float | None, ...] = Field(min_length=2)
    source_rows: tuple[int, ...]
    invalid_cells: tuple[dict, ...] = ()
    omitted_empty_record_rows: tuple[int, ...] = ()
    native_time_unit: Literal["second"] = "second"
    native_signal_unit: Literal["Volt"] = "Volt"
    zero_reference: Literal["not_documented"] = "not_documented"
    acquisition_origin: Literal["not_documented"] = "not_documented"
    source_labels_are_calibration: Literal[False] = False
    waveform_digest: Name

    @model_validator(mode="after")
    def aligned(self):
        if not len(self.time_s) == len(self.voltage_v) == len(self.source_rows):
            raise ValueError("waveform timestamps, voltage and original row identities must align")
        if any(b <= a for a, b in zip(self.source_rows, self.source_rows[1:], strict=False)):
            raise ValueError("waveform source rows must keep the original file order")
        return self

    def check_integrity(self):
        NDEWaveform.model_validate(self.model_dump(mode="python"))
        if digest(self.model_dump(mode="json", exclude={"waveform_digest"})) != self.waveform_digest:
            raise ValueError("waveform values or provenance changed")

    def audit(self):
        self.check_integrity()
        pairs = [
            (a, b)
            for a, b in zip(self.time_s, self.time_s[1:], strict=False)
            if a is not None and b is not None
        ]
        deltas = [b - a for a, b in pairs]
        positive = [v for v in deltas if v > 0]
        times = [v for v in self.time_s if v is not None]
        return {
            "samples": len(self.time_s),
            "missing_times": self.time_s.count(None),
            "missing_voltages": self.voltage_v.count(None),
            "negative_native_times": sum(t < 0 for t in times),
            "nonincreasing_adjacent_times": sum(v <= 0 for v in deltas),
            "minimum_native_time_s": min(times) if times else None,
            "maximum_native_time_s": max(times) if times else None,
            "minimum_positive_step_s": min(positive) if positive else None,
            "maximum_positive_step_s": max(positive) if positive else None,
            "zero_reference": self.zero_reference,
            "source_rows_preserved": True,
        }


def _read_member(archive, member, maximum_bytes):
    name = PurePosixPath(member)
    if name.is_absolute() or ".." in name.parts or "\\" in member or ":" in member:
        raise ValueError("NDE members must be safe relative archive names")
    entries = [i for i in archive.infolist() if i.filename == member]
    if len(entries) != 1 or entries[0].is_dir():
        raise ValueError("an NDE member must identify one unambiguous file")
    entry = entries[0]
    if entry.file_size > maximum_bytes or entry.file_size > 1000 * max(entry.compress_size, 1):
        raise ValueError("NDE member exceeds the uncompressed-data budget")
    return archive.read(entry)


def load_tubers_waveform(archive, member, *, expected_archive_sha256=None, maximum_member_bytes=2_000_000):
    """Keep signed source times, incomplete rows and invalid cells; never infer a trigger or physical thickness."""
    import hashlib

    path = Path(archive)
    source_sha = file_digest(path)
    if expected_archive_sha256 is not None and source_sha != expected_archive_sha256:
        raise ValueError("the ultrasonic archive differs from its pinned acquisition")
    with zipfile.ZipFile(path) as source:
        raw = _read_member(source, member, maximum_member_bytes)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    if rows[:2] != [["x-axis", "1"], ["second", "Volt"]]:
        raise ValueError("the ultrasonic reader requires the explicit native second/Volt header")
    times, voltages, source_rows, invalid, omitted = [], [], [], [], []
    for row_number, row in enumerate(rows[2:], start=3):
        if not row or all(not value.strip() for value in row):
            omitted.append(row_number)
            continue
        if len(row) != 2:
            raise ValueError(f"unexpected ultrasonic column count at source row {row_number}")
        parsed = []
        for column, text in zip(("time", "voltage"), row, strict=True):
            try:
                value = float(text)
                if not math.isfinite(value):
                    raise ValueError("nonfinite")
            except ValueError:
                value = None
                invalid.append({"row": row_number, "column": column, "native_token": text})
            parsed.append(value)
        times.append(parsed[0])
        voltages.append(parsed[1])
        source_rows.append(row_number)
    member_sha = hashlib.sha256(raw).hexdigest()
    parts = PurePosixPath(member).parts
    initial = NDEWaveform(
        archive_sha256=source_sha,
        member=member,
        member_sha256=member_sha,
        native_clock_id="nde_native:"
        + digest({"archive": source_sha, "member": member, "content": member_sha}),
        source_group_label=parts[1] if len(parts) > 2 else "unspecified",
        time_s=tuple(times),
        voltage_v=tuple(voltages),
        source_rows=tuple(source_rows),
        invalid_cells=tuple(invalid),
        omitted_empty_record_rows=tuple(omitted),
        waveform_digest="pending",
    )
    data = initial.model_dump(mode="python", exclude={"waveform_digest"})
    return NDEWaveform(**data, waveform_digest=digest(data))


def tubers_visual_pairs(archive, *, expected_archive_sha256=None):
    """Index source image/mask correspondence without inferring pixel labels, physical scale or independent trials."""
    path = Path(archive)
    source_sha = file_digest(path)
    if expected_archive_sha256 is not None and source_sha != expected_archive_sha256:
        raise ValueError("the visual archive differs from its pinned acquisition")
    pairs = []
    with zipfile.ZipFile(path) as source:
        names = [i.filename for i in source.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("duplicate visual archive member names are ambiguous")
        images = {n for n in names if "/Images/" in n and n.endswith(".png")}
        masks = {n for n in names if "/Masks/" in n and n.endswith(".png")}
        expected_masks = {n.replace("/Images/", "/Masks/") for n in images}
        missing_masks = sorted(expected_masks - masks)
        orphan_masks = sorted(masks - expected_masks)
        for name in sorted(images):
            mask = name.replace("/Images/", "/Masks/")
            if mask not in masks:
                continue
            dimensions = []
            for member in (name, mask):
                raw = _read_member(source, member, 20_000_000)
                if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
                    raise ValueError("a declared PNG has no valid PNG/IHDR signature")
                width, height = struct.unpack(">II", raw[16:24])
                if width == 0 or height == 0:
                    raise ValueError("a source PNG has invalid dimensions")
                dimensions.append((width, height))
            if dimensions[0] != dimensions[1]:
                raise ValueError("a source image/mask pair has different native dimensions")
            pairs.append(
                {
                    "image": name,
                    "mask": mask,
                    "width_px": dimensions[0][0],
                    "height_px": dimensions[0][1],
                    "source_group_label": PurePosixPath(name).parts[1],
                }
            )
    return {
        "status": "needs_data" if missing_masks or orphan_masks else "correspondence_checked",
        "archive_sha256": source_sha,
        "pairs": pairs,
        "images_without_masks": [n.replace("/Masks/", "/Images/") for n in missing_masks],
        "masks_without_images": orphan_masks,
        "physical_pixel_scale_known": False,
        "mask_class_values_reviewed": False,
        "independent_trials_established": False,
    }


class EchoThicknessRequest(Contract):
    waveform_digest: Name
    reference_index: int = Field(ge=0)
    return_index: int = Field(ge=0)
    sound_speed_m_s: Positive
    time_zero_offset_s: Nonnegative
    incidence_angle_rad: float = Field(ge=0, lt=math.pi / 2)
    source_evidence_id: Name
    echo_review_evidence_id: Name
    calibration_evidence_id: Name
    interpretation: Literal["reviewed_pulse_echo_round_trip"] = "reviewed_pulse_echo_round_trip"

    @model_validator(mode="after")
    def ordered_picks(self):
        if self.return_index <= self.reference_index:
            raise ValueError("the reviewed return pick must follow its reference pick")
        return self


def evaluate_echo_thickness(
    trace: NDEWaveform, request: EchoThicknessRequest, context: RunContext, *, materials=None
):
    """Send explicitly reviewed, source-bound echo timing to the native material model; no automatic peak labeling."""
    trace.check_integrity()
    request = EchoThicknessRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    evidence = {e.id: e for e in context.evidence}
    required = {request.source_evidence_id, request.echo_review_evidence_id, request.calibration_evidence_id}
    if len(evidence) != len(context.evidence) or required - evidence.keys():
        raise ValueError("echo classification, source and calibration need unique context evidence")
    if (
        request.waveform_digest != trace.waveform_digest
        or context.clock_id != trace.native_clock_id
        or evidence[request.source_evidence_id].sha256 != trace.archive_sha256
    ):
        raise ValueError("echo picks, source evidence and native clock must belong to the same waveform")
    issues = []
    a, b = request.reference_index, request.return_index
    if b >= len(trace.time_s):
        issues.append("echo_pick_outside_source_waveform")
    else:
        time = trace.time_s[a : b + 1]
        signal = trace.voltage_v[a : b + 1]
        if any(t is None for t in time) or any(v is None for v in signal):
            issues.append("selected_echo_window_contains_missing_time_or_voltage")
        elif any(
            y != x + 1
            for x, y in zip(trace.source_rows[a : b + 1], trace.source_rows[a + 1 : b + 1], strict=False)
        ):
            issues.append("selected_echo_window_contains_omitted_native_records")
        elif any(y <= x for x, y in zip(time, time[1:], strict=False)):
            issues.append("selected_echo_window_clock_is_not_strictly_increasing")
    native, value = None, None
    if not issues:
        parameters = {
            "round_trip_time_s": trace.time_s[b] - trace.time_s[a],
            "sound_speed_m_s": request.sound_speed_m_s,
            "time_zero_offset_s": request.time_zero_offset_s,
            "incidence_angle_rad": request.incidence_angle_rad,
        }
        native = (MaterialClient() if materials is None else materials).evaluate(
            "inspection.ultrasonic_thickness", parameters, context, location_id=trace.member
        )
        native = ExternalResult.model_validate(native.model_dump(mode="python"))
        if (
            digest(native.payload) != native.payload_digest
            or native.owner != "matintegrity"
            or native.asset_id != context.asset_id
            or native.payload["context"]["asset_id"] != context.asset_id
            or native.payload["context"]["location_id"] != trace.member
            or native.status != native.payload["status"]
            or native.payload["inputs"] != parameters
            or native.payload["model_id"] != "inspection.ultrasonic_thickness"
            or native.payload["context"]["time_basis"] != context.clock_id
            or native.payload["context"]["metadata"]["pipeline_context_digest"] != digest(context)
        ):
            raise ValueError("the material result changed echo inputs, meaning or source clock")
        if native.status == "computed":
            if native.payload["units"].get("thickness_m") != "m":
                raise ValueError("the material engine did not return thickness in meters")
            value = native.payload["values"]["thickness_m"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("the material engine returned invalid thickness")
        else:
            issues.append("native_thickness_unresolved:" + native.status)
    body = {
        "schema": "pipeline_integrity.echo_thickness.v1",
        "status": "exchange_checked"
        if value is not None
        else ("needs_data" if native is None else "withheld"),
        "context": context.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "waveform_digest": trace.waveform_digest,
        "native_result": None if native is None else native.model_dump(mode="json"),
        "thickness_m": value,
        "issues": issues,
        "qualification": "research",
        "field_calibrated": False,
        "physical_echo_identity_automatically_verified": False,
        "source_size_labels_used_as_truth": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return body | {"result_digest": digest(body)}
