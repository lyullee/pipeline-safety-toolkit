"""Spatially supported external measurements with explicit censoring and owner handoffs."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .acoustics import SampledTrace
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .metrology import MeasurementBasis
from .monitoring_exchange import MonitoringSplit, evaluate_monitoring

SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "coordinate_registration_and_physical_support",
    "source_clock_and_exposure_alignment",
    "quantity_species_detection_limits_and_saturation",
    "source_processing_and_quality_flags",
)


class SensorFootprint(Contract):
    id: Name
    coordinate_frame_id: Name
    kind: Literal["point", "fiber_gauge", "line_of_sight", "pixel_polygon"]
    vertices_m: tuple[tuple[float, float, float], ...] = Field(min_length=1, max_length=128)
    registration_evidence_id: Name
    asset_port_ids: tuple[Name, ...] = ()
    spatial_error_bound_m: Nonnegative | None
    fiber_id: Name | None = None
    fiber_distance_interval_m: tuple[Nonnegative, Positive] | None = None

    @model_validator(mode="after")
    def geometry(self):
        n = len(self.vertices_m)
        if (
            (self.kind == "point" and n != 1)
            or (self.kind in {"fiber_gauge", "line_of_sight"} and n < 2)
            or (self.kind == "pixel_polygon" and n < 3)
        ):
            raise ValueError("footprint kind requires explicit point, path, or polygon vertices")
        if self.kind != "point" and len(set(self.vertices_m)) != n:
            raise ValueError("support vertices must be distinct; polygon closure is implicit")
        if self.kind == "fiber_gauge":
            if (
                self.fiber_id is None
                or self.fiber_distance_interval_m is None
                or self.fiber_distance_interval_m[1] <= self.fiber_distance_interval_m[0]
            ):
                raise ValueError("fiber gauge requires its fiber and increasing along-fiber interval")
        elif self.fiber_id is not None or self.fiber_distance_interval_m is not None:
            raise ValueError("along-fiber support belongs only to a fiber gauge")
        return self


class ExternalChannel(Contract):
    id: Name
    sensor_id: Name
    modality: Literal["DAS", "gas_detector", "remote_column"]
    basis: MeasurementBasis
    footprint: SensorFootprint
    species_id: Name | None = None
    detection_limit: Nonnegative | None
    detection_rule: Literal["absolute_magnitude", "upper_one_sided"]
    saturation_lower: float | None
    saturation_upper: float | None
    instrument_configuration_sha256: str = Field(pattern=SHA)
    evidence_id: Name
    temporal_response: Literal["unspecified", "uniform_exposure_mean"] = "unspecified"

    @model_validator(mode="after")
    def modality_scope(self):
        kind, unit = self.footprint.kind, self.basis.unit
        if self.modality == "DAS":
            if (
                kind != "fiber_gauge"
                or (self.basis.quantity, unit) not in {("strain", "1"), ("strain_rate", "1/s")}
                or self.species_id is not None
                or self.detection_rule != "absolute_magnitude"
            ):
                raise ValueError(
                    "DAS requires a fiber gauge and signed strain/strain-rate; raw optical phase needs an owner conversion"
                )
        elif self.modality == "gas_detector":
            allowed = (
                {("mole_fraction", "1"), ("mass_concentration", "kg/m^3")}
                if kind == "point"
                else {("path_molar_column", "mol/m^2"), ("path_mass_column", "kg/m^2")}
            )
            if (
                kind not in {"point", "line_of_sight"}
                or (self.basis.quantity, unit) not in allowed
                or self.species_id is None
            ):
                raise ValueError(
                    "gas sensing requires species and distinct local-concentration or path-column basis"
                )
        elif (
            kind != "pixel_polygon"
            or (self.basis.quantity, unit) not in {("molar_column", "mol/m^2"), ("mass_column", "kg/m^2")}
            or self.species_id is None
        ):
            raise ValueError(
                "remote retrieval requires species and a registered pixel column; radiance needs an owner retrieval"
            )
        if (
            self.saturation_lower is not None
            and self.saturation_upper is not None
            and self.saturation_lower >= self.saturation_upper
        ):
            raise ValueError("saturation interval must increase")
        return self


class ExternalReading(Contract):
    channel_id: Name
    state: Literal["quantified", "non_detect", "saturated_lower", "saturated_upper", "missing", "invalid"]
    value: float | None
    source_row_id: Name
    quality_evidence_id: Name


class ExternalAcquisition(Contract):
    id: Name
    time_s: Nonnegative
    exposure_start_s: Nonnegative
    exposure_end_s: Nonnegative
    readings: tuple[ExternalReading, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def exposure(self):
        if not self.exposure_start_s <= self.time_s <= self.exposure_end_s:
            raise ValueError("report time must lie in its explicitly declared exposure support")
        return self


class ExternalSensingRequest(Contract):
    id: Name
    asset_id: Name
    stream_id: Name
    clock_id: Name
    source_clock_id: Name
    source_to_analysis_offset_s: float | None
    clock_error_bound_s: Nonnegative | None
    clock_mapping_evidence_id: Name
    origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    source_sha256: str = Field(pattern=SHA)
    processing_source_sha256: str = Field(pattern=SHA)
    processing_configuration_sha256: str = Field(pattern=SHA)
    channels: tuple[ExternalChannel, ...] = Field(min_length=1, max_length=512)
    acquisitions: tuple[ExternalAcquisition, ...] = Field(min_length=1, max_length=100000)
    maximum_readings: int = Field(default=1000000, ge=1, le=5000000)
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_id: Name

    @model_validator(mode="after")
    def ordering(self):
        if (set(self.reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("external review keys must match the named applicability boundary")
        if len(self.channels) * len(self.acquisitions) > self.maximum_readings:
            raise ValueError("external-sensing work budget exceeded before frame/sidecar creation")
        ids = tuple(k.id for k in self.channels)
        if len(set(ids)) != len(ids) or len({a.id for a in self.acquisitions}) != len(self.acquisitions):
            raise ValueError("channel and acquisition IDs must be unique")
        if any(tuple(r.channel_id for r in a.readings) != ids for a in self.acquisitions):
            raise ValueError("every acquisition must explicitly contain every channel in declared order")
        if any(b.time_s <= a.time_s for a, b in zip(self.acquisitions, self.acquisitions[1:], strict=False)):
            raise ValueError("source clock must increase without sorting or alignment by row position")
        source_ids = [r.source_row_id for a in self.acquisitions for r in a.readings]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source observation row IDs must be distinct")
        return self


def boundary_metadata(q):
    return {
        "request_sha256": digest(q),
        "asset_id": q.asset_id,
        "origin": q.origin,
        "scope": "external spatial measurements and censoring; no physical cause or detection probability",
    }


def _reading(channel, reading):
    state, value = reading.state, reading.value
    if state in {"missing", "non_detect"} and value is not None:
        raise ValueError("missing/non-detect requires unknown value; retain raw instrument output in source")
    if state == "quantified":
        if value is None:
            raise ValueError("quantified observations require a value")
        if channel.basis.quantity == "mole_fraction" and not 0 <= value <= 1:
            raise ValueError("quantified mole fractions must be SI fractions between zero and one")
        if (
            channel.saturation_lower is not None
            and value <= channel.saturation_lower
            or channel.saturation_upper is not None
            and value >= channel.saturation_upper
        ):
            raise ValueError("quantified value is at or outside saturation; declare its censored state")
        if (
            channel.detection_limit is not None
            and (abs(value) if channel.detection_rule == "absolute_magnitude" else value)
            < channel.detection_limit
        ):
            raise ValueError("quantified value violates declared detection rule; preserve as non-detect")
    bounds = None
    if state == "non_detect":
        limit = channel.detection_limit
        if limit is None:
            raise ValueError("non-detect requires a declared detection limit")
        bounds = {
            "lower": -limit if channel.detection_rule == "absolute_magnitude" else None,
            "upper": limit,
            "meaning": "censoring support under declared instrument rule; not confidence interval",
        }
    if state.startswith("saturated_"):
        threshold = channel.saturation_lower if state == "saturated_lower" else channel.saturation_upper
        if threshold is None:
            raise ValueError("saturation requires the corresponding instrument limit")
        if value is not None and (
            (state == "saturated_lower" and value > threshold)
            or (state == "saturated_upper" and value < threshold)
        ):
            raise ValueError("reported saturated raw value contradicts instrument limit")
        bounds = {
            "lower": threshold if state == "saturated_upper" else None,
            "upper": threshold if state == "saturated_lower" else None,
            "meaning": "instrument clipping support; not confidence interval",
        }
    return value if state == "quantified" else None, state == "quantified", bounds


def adapt_external_sensing(request: ExternalSensingRequest, context: RunContext):
    if (
        not 1 <= len(request.channels) <= 512
        or not 1 <= len(request.acquisitions) <= 100000
        or not 1 <= request.maximum_readings <= 5000000
        or len(request.channels) * len(request.acquisitions) > request.maximum_readings
        or any(len(a.readings) != len(request.channels) for a in request.acquisitions)
        or any(len(k.footprint.vertices_m) > 128 for k in request.channels)
    ):
        raise ValueError("external source work budget exceeded before cloning or serialization")
    q = ExternalSensingRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id, q.origin) != (c.asset_id, c.clock_id, c.origin) or c.metadata.get(
        "external_sensing_boundary"
    ) != boundary_metadata(q):
        raise ValueError("context must bind exact request, asset, analysis clock and source origin")
    ids = [e.id for e in c.evidence]
    needed = {q.evidence_id, q.clock_mapping_evidence_id, *q.review_evidence_ids.values()}
    for channel in q.channels:
        needed.update(
            (channel.evidence_id, channel.basis.evidence_id, channel.footprint.registration_evidence_id)
        )
        if channel.basis.reference_state:
            needed.add(channel.basis.reference_state.evidence_id)
    needed.update(r.quality_evidence_id for a in q.acquisitions for r in a.readings)
    if len(set(ids)) != len(ids) or needed - set(ids):
        raise ValueError("every source/quality/registration/review evidence must be selected")
    missing = [k for k in REVIEWS if q.reviews.get(k) is not True or k not in q.review_evidence_ids]
    if q.source_to_analysis_offset_s is None or q.clock_error_bound_s is None:
        missing.append("known source-to-analysis offset and timing error bound")
    if any(k.footprint.spatial_error_bound_m is None for k in q.channels):
        missing.append("registered spatial error bound for every support")
    body = {
        "schema": "external_sensing.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "status": "needs_data" if missing else "computed",
        "missing": missing,
        "frames": [],
        "quality_sidecar": [],
        "quantity_is_cause_or_HEP": False,
        "leak_detection_probability": None,
        "field_qualified": False,
        "native_DAS_or_image_algorithm_executed": False,
        "source_sha256": file_digest(Path(__file__)),
        "dependency_source_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py"))
            for name in ("contracts", "metrology", "acoustics", "monitoring_exchange")
        },
        "runtime": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "pydantic_core": version("pydantic-core"),
            "pint": version("pint"),
        },
    }
    # State consistency is checked even when an applicability review is still missing.
    for acquisition in q.acquisitions:
        values, valid, sidecars = {}, {}, []
        for channel, reading in zip(q.channels, acquisition.readings, strict=True):
            values[channel.id], valid[channel.id], bounds = _reading(channel, reading)
            sidecars.append(
                {
                    "channel_id": channel.id,
                    "source_row_id": reading.source_row_id,
                    "state": reading.state,
                    "reported_value": reading.value,
                    "censoring": bounds,
                    "footprint_sha256": digest(channel.footprint),
                    "quantified_for_monitoring": valid[channel.id],
                }
            )
        if missing:
            continue
        offset = q.source_to_analysis_offset_s
        report_time = acquisition.time_s + offset
        # A window summary becomes available only after its full exposure.
        time = acquisition.exposure_end_s + offset
        start, end = acquisition.exposure_start_s + offset, acquisition.exposure_end_s + offset
        if not all(math.isfinite(v) and v >= 0 for v in (time, start, end)):
            raise ValueError("mapped source exposure is negative or cannot be represented on analysis clock")
        if acquisition.exposure_end_s > acquisition.exposure_start_s and end <= start:
            raise ValueError("clock mapping collapses a nonzero source exposure")
        if body["frames"] and time <= body["frames"][-1]["time_s"]:
            raise ValueError("completed exposure endpoints must strictly increase on the analysis clock")
        frame = SignalFrame(
            stream_id=q.stream_id,
            clock_id=q.clock_id,
            time_s=time,
            values=values,
            units={k.id: k.basis.unit for k in q.channels},
            valid=valid,
            origin=q.origin,
        )
        body["frames"].append(frame.model_dump(mode="json"))
        body["quality_sidecar"].append(
            {
                "acquisition_id": acquisition.id,
                "source_time_s": acquisition.time_s,
                "analysis_report_time_s": report_time,
                "frame_time_basis": "nominal completed exposure end; clock error bound remains in request",
                "analysis_exposure_s": [start, end],
                "readings": sidecars,
            }
        )
    json_value(body)
    return dict(body, record_sha256=digest(body))


def verify_external_sensing_record(record, *, request=None, context=None):
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if digest(body) != record.get("record_sha256"):
        raise ValueError("external measurement record seal changed")
    q = ExternalSensingRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if (
        request is not None
        and digest(request) != digest(q)
        or context is not None
        and digest(context) != digest(c)
    ):
        raise ValueError("caller boundary differs from stored external measurement boundary")
    if adapt_external_sensing(q, c) != record:
        raise ValueError("external measurement source/runtime/quality replay differs")
    return True


def to_monitoring_split(
    record, *, role, split_id, physical_run_id, source_group_id, reference_kind="unresolved"
):
    """Use existing exchange; sidecar remains authoritative for censoring and spatial support."""
    verify_external_sensing_record(record)
    if record["status"] != "computed":
        raise ValueError("unresolved spatial/clock applicability cannot enter owner monitoring")
    q = ExternalSensingRequest.model_validate_json(json.dumps(record["request"]))
    return MonitoringSplit(
        role=role,
        split_id=split_id,
        physical_run_id=physical_run_id,
        source_group_id=source_group_id,
        source_evidence_id=q.evidence_id,
        observation_ids=tuple(a.id for a in q.acquisitions),
        frames=tuple(SignalFrame.model_validate_json(json.dumps(f)) for f in record["frames"]),
        reference_kind=reference_kind,
    )


def to_sampled_trace(record, channel_id, *, sample_period_s, maximum_grid_error_s=0.0):
    """Pass one fixed DAS gauge to existing acoustics; no strain-to-pressure conversion."""
    verify_external_sensing_record(record)
    if (
        not math.isfinite(sample_period_s)
        or sample_period_s <= 0
        or not math.isfinite(maximum_grid_error_s)
        or maximum_grid_error_s < 0
    ):
        raise ValueError("explicit finite sampling period and nonnegative source-grid tolerance required")
    q = ExternalSensingRequest.model_validate_json(json.dumps(record["request"]))
    selected = [k for k in q.channels if k.id == channel_id]
    if record["status"] != "computed" or not selected or selected[0].modality != "DAS":
        raise ValueError("a resolved fixed-gauge DAS channel is required")
    frames = record["frames"]
    start = frames[0]["time_s"]
    if any(
        abs(f["time_s"] - (start + i * sample_period_s)) > maximum_grid_error_s for i, f in enumerate(frames)
    ):
        raise ValueError("source samples do not lie on the explicitly bounded grid; no resampling")
    channel = selected[0]
    durations = [a.exposure_end_s - a.exposure_start_s for a in q.acquisitions]
    if any(duration > 0 for duration in durations):
        if channel.temporal_response != "uniform_exposure_mean" or any(
            duration <= 0 or duration != durations[0] for duration in durations
        ):
            raise ValueError(
                "nonzero exposures require an explicit identical uniform-mean response; no point-sample reinterpretation"
            )
        quantity_id = f"{channel.basis.quantity}:uniform_exposure_mean:{durations[0]!r}s:end_time"
    else:
        quantity_id = channel.basis.quantity
    return SampledTrace(
        id=q.id + ":" + channel.id,
        asset_id=q.asset_id,
        sensor_id=channel.sensor_id,
        clock_id=q.clock_id,
        start_time_s=start,
        sample_period_s=sample_period_s,
        quantity_id=quantity_id,
        unit=channel.basis.unit,
        origin=q.origin,
        source_digest=record["record_sha256"],
        values=tuple(f["values"][channel_id] for f in frames),
        valid=tuple(f["valid"][channel_id] for f in frames),
    )


def evaluate_external_monitoring(
    records,
    monitoring_request,
    context,
    *,
    split_ids,
    physical_run_ids,
    source_group_ids,
    reference_kinds,
    client=None,
    owner_source_root=None,
):
    """Keep spatial/quality evidence while the existing PMT exchange owns all statistics."""
    if any(len(v) != 3 for v in (records, split_ids, physical_run_ids, source_group_ids, reference_kinds)):
        raise ValueError("three explicit training/calibration/evaluation boundaries are required")
    requests = []
    for record in records:
        verify_external_sensing_record(record)
        requests.append(ExternalSensingRequest.model_validate_json(json.dumps(record["request"])))
    first = requests[0]
    if any(
        (q.asset_id, q.clock_id, q.stream_id, q.origin, digest(q.channels))
        != (first.asset_id, first.clock_id, first.stream_id, first.origin, digest(first.channels))
        for q in requests
    ):
        raise ValueError(
            "monitoring splits cannot silently mix footprints, species, instruments or quantities"
        )
    if (context.asset_id, context.clock_id, context.origin) != (first.asset_id, first.clock_id, first.origin):
        raise ValueError("monitoring context differs from original external observations")
    expected = [r["record_sha256"] for r in records]
    if context.metadata.get("external_sensing_records") != expected:
        raise ValueError("monitoring context must bind the ordered three external measurement receipts")
    splits = tuple(
        to_monitoring_split(
            record, role=role, split_id=sid, physical_run_id=run, source_group_id=group, reference_kind=ref
        )
        for record, role, sid, run, group, ref in zip(
            records,
            ("training", "calibration", "evaluation"),
            split_ids,
            physical_run_ids,
            source_group_ids,
            reference_kinds,
            strict=True,
        )
    )
    return evaluate_monitoring(
        monitoring_request, splits, context, client=client, owner_source_root=owner_source_root
    )
