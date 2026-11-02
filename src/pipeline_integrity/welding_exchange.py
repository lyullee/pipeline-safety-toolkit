"""Audited welding indicators and recorded electrical/thermal histories around owner engines."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import _aligned_frames
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .lifecycle_exchange import PowerIntegration, integrate_power_frames
from .material_exchange import evaluate_material_job, prepare_material_job
from .units import convert_quantity

REVIEWS = {
    "welding.arc_energy": ("steady_representative_electrical_state", "travel_speed_and_efficiency"),
    "welding.waveform_energy": ("synchronized_signed_electrical_channels", "full_time_distance_efficiency"),
    "welding.carbon_equivalents": ("complete_mass_percent_chemistry", "steel_family_and_indicator_scope"),
    "welding.cooling_interval": ("selected_nonincreasing_branch", "crossing_temperatures_and_sensor_basis"),
    "welding.dilution": ("melted_mass_not_wire_feed_or_area", "composition_mass_fraction_complete_mixing"),
    "welding.hardness_traverse": ("method_force_dwell_and_uncertainty", "region_rule_and_measured_locations"),
    "welding.heat_treatment_history": (
        "common_sensor_clock_and_no_hidden_gaps",
        "sensor_representativeness_and_hold_band",
    ),
    "welding.joint_profile": ("transverse_registration_and_base_regions", "fit_method_noise_and_support"),
}
HISTORIES = {
    "welding.waveform_energy": ("voltage_V", "current_A"),
    "welding.cooling_interval": ("temperature_K",),
    "welding.heat_treatment_history": ("sensor_temperature_K",),
}


class WeldingRequest(Contract):
    method: Literal[
        "welding.arc_energy",
        "welding.waveform_energy",
        "welding.carbon_equivalents",
        "welding.cooling_interval",
        "welding.dilution",
        "welding.hardness_traverse",
        "welding.heat_treatment_history",
        "welding.joint_profile",
    ]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    parameters: dict
    maximum_sample_interval_s: Positive | None = None
    maximum_samples: int = Field(default=100000, ge=2, le=10000000)
    chemistry_basis: Literal["mass_percent", "mass_fraction"] | None = None
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def meanings(self):
        json_value(self.parameters)
        if set(self.applicability_reviews) - set(REVIEWS[self.method]):
            raise ValueError("welding review keys must belong to the selected method")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("welding evidence IDs must be unique")
        return self


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _scope(asset, clock, evidence, context):
    if (asset, clock) != (context.asset_id, context.clock_id):
        raise ValueError("welding asset/clock and context differ")
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)) or set(evidence) - set(ids):
        raise ValueError("welding evidence must be unique and present")


def evaluate_welding(request: WeldingRequest, context: RunContext, *, client=None):
    request = WeldingRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    _scope(request.asset_id, request.clock_id, request.evidence_ids, context)
    body = dict(
        schema="pipeline_integrity.welding_exchange.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="needs_data",
        issues=[],
        values={},
        output_units={},
        native_receipt=None,
        output_origin="simulation",
        procedure_or_field_qualification=False,
        preheat_cracking_probability_or_allowable_inferred=False,
        implementation_sha256=file_digest(Path(__file__)),
    )

    def hold(status, message):
        body.update(status=status, issues=[message])
        return _seal(body)

    if any(not request.applicability_reviews.get(k, False) for k in REVIEWS[request.method]):
        return hold("needs_data", "method-specific welding applicability reviews are required")
    p = request.parameters
    if any(v is None for v in p.values()):
        return hold("needs_data", "unresolved native input; no nominal grade or process defaults")
    basis = {"welding.carbon_equivalents": "mass_percent", "welding.dilution": "mass_fraction"}.get(
        request.method
    )
    if basis is not None and request.chemistry_basis != basis:
        return hold("needs_data", "explicit chemistry basis must match the selected native method")
    if request.method in HISTORIES:
        time = p.get("time_s")
        if not isinstance(time, list) or len(time) < 2:
            raise ValueError("welding history requires the complete recorded time vector")
        if len(time) > request.maximum_samples:
            raise ValueError("declared welding-history sample budget exceeded")
        if any(t is None for t in time):
            return hold("needs_data", "missing history times cannot be synthesized")
        if any(b <= a for a, b in zip(time, time[1:], strict=False)):
            raise ValueError("welding-history time must increase without sorting or deduplication")
        if request.maximum_sample_interval_s is None:
            return hold("needs_data", "a declared maximum recorded interval is required")
        if any(b - a > request.maximum_sample_interval_s for a, b in zip(time, time[1:], strict=False)):
            return hold("needs_data", "uncovered history intervals cannot be interpolated silently")
        for name in HISTORIES[request.method]:
            values = p.get(name)
            if not isinstance(values, list):
                raise ValueError("recorded welding-history vectors are required")
            vectors = values if name == "sensor_temperature_K" else [values]
            if any(v is None for row in vectors for v in row):
                return hold("needs_data", "missing recorded history values cannot be filled")
        if request.method == "welding.cooling_interval" and any(
            b > a for a, b in zip(p["temperature_K"], p["temperature_K"][1:], strict=False)
        ):
            return hold(
                "out_of_scope",
                "select an explicit nonincreasing cooling branch; no automatic branch selection",
            )
    job = prepare_material_job(
        request.method,
        p,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    receipt = evaluate_material_job(job, context, client=client)
    receipt.check_integrity()
    native = receipt.native.payload
    body.update(
        status=native["status"],
        values=native["values"],
        output_units=native["units"],
        native_receipt=receipt.model_dump(mode="json"),
        issues=list(native["notes"]),
    )
    return _seal(body)


class WeldWaveformSpec(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    voltage_channel: Name
    current_channel: Name
    maximum_sample_interval_s: Positive
    maximum_samples: int = Field(default=100000, ge=2, le=10000000)
    travel_distance_m: Positive | None
    efficiency: float | None = Field(gt=0, le=1)
    electrical_synchronization_and_polarity_reviewed: bool = False
    distance_efficiency_and_window_reviewed: bool = False
    electrical_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def electrical_evidence(self):
        if self.voltage_channel == self.current_channel:
            raise ValueError("voltage and current require distinct recorded channels")
        if (
            len(set(self.evidence_ids)) != len(self.evidence_ids)
            or self.electrical_evidence_id not in self.evidence_ids
        ):
            raise ValueError("distinct evidence must include the electrical source")
        return self


def evaluate_weld_waveform(frames, specification: WeldWaveformSpec, context: RunContext, *, client=None):
    spec = WeldWaveformSpec.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    _scope(spec.asset_id, spec.clock_id, (*spec.evidence_ids, spec.electrical_evidence_id), context)
    frames = tuple(frames)
    if len(frames) > spec.maximum_samples:
        raise ValueError("declared welding waveform sample budget exceeded")
    frames = tuple(SignalFrame.model_validate_json(f.model_dump_json()) for f in frames)
    aligned, channels = _aligned_frames(frames)
    if (
        len(aligned) < 2
        or len(aligned) > spec.maximum_samples
        or aligned[0].clock_id != spec.clock_id
        or {spec.voltage_channel, spec.current_channel} - set(channels)
    ):
        raise ValueError(
            "complete aligned welding channels/clock and the declared sample budget are required"
        )
    body = dict(
        schema="pipeline_integrity.weld_waveform.v1",
        specification=spec.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        input_digest=digest(frames),
        status="needs_data",
        issues=[],
        electrical_diagnostic=None,
        native_result=None,
        output_origin="simulation",
        procedure_or_field_qualification=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if not spec.electrical_synchronization_and_polarity_reviewed:
        body["issues"] = ["recorded voltage/current synchronization and polarity require review"]
        return _seal(body)
    voltage, current, power_frames = [], [], []
    for frame in aligned:
        u = (
            None
            if frame.values[spec.voltage_channel] is None
            else convert_quantity(frame.values[spec.voltage_channel], frame.units[spec.voltage_channel], "V")
        )
        i = (
            None
            if frame.values[spec.current_channel] is None
            else convert_quantity(frame.values[spec.current_channel], frame.units[spec.current_channel], "A")
        )
        voltage.append(u)
        current.append(i)
        good = frame.valid[spec.voltage_channel] and frame.valid[spec.current_channel]
        power_frames.append(
            SignalFrame(
                stream_id=frame.stream_id + ":signed_arc_power",
                clock_id=frame.clock_id,
                time_s=frame.time_s,
                timestamp_utc=frame.timestamp_utc,
                values=dict(power=None if u is None or i is None else u * i),
                units=dict(power="W"),
                valid=dict(power=good),
                origin=frame.origin,
            )
        )
    diagnostic = integrate_power_frames(
        power_frames,
        PowerIntegration(
            channel="power",
            maximum_sample_interval_s=spec.maximum_sample_interval_s,
            sign_convention="signed_import_positive",
            evidence_id=spec.electrical_evidence_id,
        ),
        context,
    )
    body["electrical_diagnostic"] = diagnostic
    if diagnostic["status"] != "computed":
        body["issues"] = [
            "invalid/unknown samples or uncovered intervals leave incomplete electrical history"
        ]
        return _seal(body)
    if any(u * i < 0 for u, i in zip(voltage, current, strict=True)):
        body.update(
            status="out_of_scope",
            issues=[
                "signed electrical exchange is retained; the native nonnegative arc-power heat model is inapplicable"
            ],
        )
        return _seal(body)
    if (
        not spec.distance_efficiency_and_window_reviewed
        or spec.travel_distance_m is None
        or spec.efficiency is None
    ):
        body["issues"] = [
            "recorded-window distance and heat-transfer efficiency require separate explicit evidence"
        ]
        return _seal(body)
    request = WeldingRequest(
        method="welding.waveform_energy",
        asset_id=spec.asset_id,
        location_id=spec.location_id,
        clock_id=spec.clock_id,
        material_condition_id=spec.material_condition_id,
        maximum_sample_interval_s=spec.maximum_sample_interval_s,
        maximum_samples=spec.maximum_samples,
        parameters=dict(
            time_s=[f.time_s for f in aligned],
            voltage_V=voltage,
            current_A=current,
            travel_distance_m=spec.travel_distance_m,
            efficiency=spec.efficiency,
        ),
        applicability_reviews=dict.fromkeys(REVIEWS["welding.waveform_energy"], True),
        evidence_ids=spec.evidence_ids,
    )
    native = evaluate_welding(request, context, client=client)
    body.update(status=native["status"], native_result=native, issues=native["issues"])
    return _seal(body)


class WeldThermalSpec(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    kind: Literal["cooling", "hold"]
    temperature_channels: tuple[Name, ...] = Field(min_length=1)
    lower_temperature_k: Positive
    upper_temperature_k: Positive
    maximum_sample_interval_s: Positive
    maximum_samples: int = Field(default=100000, ge=2, le=10000000)
    sensor_locations_temperature_and_rule_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sensors_and_band(self):
        if len(set(self.temperature_channels)) != len(self.temperature_channels) or len(
            set(self.evidence_ids)
        ) != len(self.evidence_ids):
            raise ValueError("thermal sensor channels and evidence must be distinct")
        if (
            self.upper_temperature_k < self.lower_temperature_k
            or self.kind == "cooling"
            and self.upper_temperature_k == self.lower_temperature_k
        ):
            raise ValueError("thermal band/targets must be ordered for the selected operation")
        return self


def evaluate_weld_thermal(frames, specification: WeldThermalSpec, context: RunContext, *, client=None):
    spec = WeldThermalSpec.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    _scope(spec.asset_id, spec.clock_id, spec.evidence_ids, context)
    frames = tuple(frames)
    if len(frames) > spec.maximum_samples:
        raise ValueError("declared welding thermal sample budget exceeded")
    frames = tuple(SignalFrame.model_validate_json(f.model_dump_json()) for f in frames)
    aligned, channels = _aligned_frames(frames)
    if (
        len(aligned) < 2
        or aligned[0].clock_id != spec.clock_id
        or set(spec.temperature_channels) - set(channels)
    ):
        raise ValueError("welding thermal history requires aligned recorded channels/clock")
    if spec.kind == "cooling" and len(spec.temperature_channels) != 1:
        raise ValueError("one explicitly selected cooling sensor is required")
    body = dict(
        schema="pipeline_integrity.weld_thermal.v1",
        specification=spec.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        input_digest=digest(frames),
        status="needs_data",
        issues=[],
        native_result=None,
        procedure_or_field_qualification=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if not spec.sensor_locations_temperature_and_rule_reviewed:
        body["issues"] = ["sensor locations, temperature basis and declared thermal criterion require review"]
        return _seal(body)
    if any(f.values[k] is None or not f.valid[k] for f in aligned for k in spec.temperature_channels):
        body["issues"] = ["missing or invalid recorded temperatures cannot be filled"]
        return _seal(body)
    temperatures = [
        [convert_quantity(f.values[k], f.units[k], "K") for f in aligned] for k in spec.temperature_channels
    ]
    if spec.kind == "cooling":
        method = "welding.cooling_interval"
        parameters = dict(
            temperature_K=temperatures[0],
            upper_temperature_K=spec.upper_temperature_k,
            lower_temperature_K=spec.lower_temperature_k,
        )
    else:
        method = "welding.heat_treatment_history"
        parameters = dict(
            sensor_temperature_K=temperatures,
            minimum_hold_temperature_K=spec.lower_temperature_k,
            maximum_hold_temperature_K=spec.upper_temperature_k,
        )
    parameters["time_s"] = [f.time_s for f in aligned]
    native = evaluate_welding(
        WeldingRequest(
            method=method,
            asset_id=spec.asset_id,
            location_id=spec.location_id,
            clock_id=spec.clock_id,
            material_condition_id=spec.material_condition_id,
            parameters=parameters,
            maximum_sample_interval_s=spec.maximum_sample_interval_s,
            maximum_samples=spec.maximum_samples,
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            evidence_ids=spec.evidence_ids,
        ),
        context,
        client=client,
    )
    body.update(status=native["status"], native_result=native, issues=native["issues"])
    return _seal(body)
