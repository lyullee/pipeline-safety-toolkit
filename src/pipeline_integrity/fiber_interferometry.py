"""Anchored coherent differential phase to calibrated fiber-gauge strain.

Phase branches and thermal sensitivity are measured inputs. Missing anchors,
fading or clock gaps remain unknown; no phase bridge or host coupling is inferred.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, require_context_extension
from .datasets import file_digest


class OpticalPhaseAnchor(Contract):
    sample_index: int = Field(ge=0)
    unwrapped_differential_phase_rad: float
    evidence_id: Name


class OpticalGaugeTrace(Contract):
    id: Name
    fiber_id: Name
    start_distance_m: Nonnegative
    end_distance_m: Positive
    left_I: tuple[float | None,...]
    left_Q: tuple[float | None,...]
    right_I: tuple[float | None,...]
    right_Q: tuple[float | None,...]
    valid: tuple[bool,...]
    detector_amplitude_unit: Name
    minimum_valid_amplitude: Positive
    strain_phase_sensitivity_rad: Positive
    phase_at_zero_strain_rad: float
    thermal_phase_sensitivity_rad_K: float
    temperature_change_K: tuple[float | None,...]
    anchors: tuple[OpticalPhaseAnchor,...] = Field(default=(),max_length=100000)
    evidence_ids: tuple[Name,...] = Field(min_length=1)


class FiberInterferometryRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    sample_time_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=1000000)
    gauges: tuple[OpticalGaugeTrace,...] = Field(min_length=1,max_length=512)
    maximum_sample_gap_s: Positive
    maximum_reviewed_phase_increment_rad: float = Field(gt=0,lt=math.pi)
    maximum_anchor_phase_residual_rad: float = Field(gt=0,lt=math.pi)
    coherence_gauge_support_and_linear_phase_sensitivity_reviewed: bool = False
    temporal_phase_alias_bound_and_anchors_reviewed: bool = False
    temperature_correction_and_reference_state_reviewed: bool = False
    maximum_output_values: int = Field(default=1000000,ge=1,le=10000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=1000000000)

    @model_validator(mode="after")
    def layout(self):
        if any(b<=a for a,b in zip(self.sample_time_s,self.sample_time_s[1:])):
            raise ValueError("fiber acquisition clock must strictly increase")
        if len({g.id for g in self.gauges})!=len(self.gauges):
            raise ValueError("fiber gauge IDs must be unique")
        n=len(self.sample_time_s)
        if n*len(self.gauges)*3>self.maximum_output_values:
            raise ValueError("fiber output exceeds its retained-value budget")
        for g in self.gauges:
            if g.end_distance_m<=g.start_distance_m:
                raise ValueError("fiber gauge endpoints must increase on the declared fiber")
            if any(len(v)!=n for v in (g.left_I,g.left_Q,g.right_I,g.right_Q,g.valid,g.temperature_change_K)):
                raise ValueError("all optical and temperature arrays must align with the source clock")
            indices=[a.sample_index for a in g.anchors]
            if len(set(indices))!=len(indices) or any(i>=n for i in indices):
                raise ValueError("phase anchors must identify unique actual samples")
        return self


def evaluate_fiber_interferometry(request: FiberInterferometryRequest, context: RunContext):
    if (not 2<=len(request.sample_time_s)<=1000000 or not 1<=len(request.gauges)<=512
            or len(request.sample_time_s)*len(request.gauges)*3>request.maximum_output_values):
        raise ValueError("fiber arrays exceed their declared work budget before cloning")
    q=FiberInterferometryRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("fiber processing asset/clock differs from source context")
    if not all((q.coherence_gauge_support_and_linear_phase_sensitivity_reviewed,
                q.temporal_phase_alias_bound_and_anchors_reviewed,q.temperature_correction_and_reference_state_reviewed)):
        raise ValueError("fiber support, alias bounds, phase anchors and thermal calibration require review")
    evidence={e.id:e for e in c.evidence}
    required={key for g in q.gauges for key in g.evidence_ids}|{a.evidence_id for g in q.gauges for a in g.anchors}
    if len(evidence)!=len(c.evidence) or any(key not in evidence or evidence[key].sha256 is None for key in required):
        raise ValueError("optical calibration, support and branch anchors need source-pinned context evidence")
    source_pin=file_digest(Path(__file__))
    shared_pins={name:file_digest(Path(__file__).with_name(name+".py")) for name in ("contracts","datasets")}
    input_pin=digest(dict(request=q,context=c))
    results=[]
    for g in q.gauges:
        anchors={a.sample_index:a for a in g.anchors}
        previous_phase=None; unwrapped=None; segment=-1; rows=[]
        for i,time in enumerate(q.sample_time_s):
            iq=(g.left_I[i],g.left_Q[i],g.right_I[i],g.right_Q[i])
            raw_valid=g.valid[i] and all(v is not None for v in iq)
            fading=False; phase=None; reason=None
            if raw_valid:
                left=math.hypot(iq[0],iq[1]); right=math.hypot(iq[2],iq[3])
                fading=left<g.minimum_valid_amplitude or right<g.minimum_valid_amplitude
                raw_valid=not fading and math.isfinite(left) and math.isfinite(right)
            if raw_valid:
                phase=math.remainder(math.atan2(iq[3],iq[2])-math.atan2(iq[1],iq[0]),2*math.pi)
            if i and time-q.sample_time_s[i-1]>q.maximum_sample_gap_s:
                previous_phase=None; unwrapped=None
                reason="clock_gap_requires_new_anchor"
            if not raw_valid:
                if i in anchors:
                    raise ValueError("an optical branch anchor cannot reference missing or faded I/Q")
                previous_phase=None; unwrapped=None
                reason="fading" if fading else "invalid_or_missing_optical_sample"
            elif i in anchors:
                anchor=anchors[i]
                if math.ulp(anchor.unwrapped_differential_phase_rad)>q.maximum_anchor_phase_residual_rad:
                    raise ValueError("phase anchor cannot represent its declared phase residual tolerance")
                mismatch=math.remainder(anchor.unwrapped_differential_phase_rad-phase,2*math.pi)
                if abs(mismatch)>q.maximum_anchor_phase_residual_rad:
                    raise ValueError("phase anchor is inconsistent with the measured differential phase")
                if previous_phase is not None and unwrapped is not None:
                    delta=math.remainder(phase-previous_phase,2*math.pi)
                    if (abs(delta)<=q.maximum_reviewed_phase_increment_rad
                            and abs(anchor.unwrapped_differential_phase_rad-(unwrapped+delta))
                            >q.maximum_anchor_phase_residual_rad):
                        raise ValueError("contiguous phase anchors contradict the previously anchored branch")
                unwrapped=anchor.unwrapped_differential_phase_rad
                segment+=1; reason="explicit_phase_anchor"
            elif previous_phase is not None and unwrapped is not None:
                delta=math.remainder(phase-previous_phase,2*math.pi)
                if abs(delta)>q.maximum_reviewed_phase_increment_rad:
                    unwrapped=None; reason="phase_increment_outside_reviewed_alias_support"
                else:
                    following=unwrapped+delta
                    if delta!=0 and following==unwrapped:
                        raise ValueError("anchored phase magnitude cannot represent the measured phase increment")
                    unwrapped=following
            else:
                reason="phase_branch_unknown_without_anchor"
            previous_phase=phase
            strain=None
            if unwrapped is not None and g.temperature_change_K[i] is not None:
                strain=(unwrapped-g.phase_at_zero_strain_rad-g.thermal_phase_sensitivity_rad_K*g.temperature_change_K[i])/g.strain_phase_sensitivity_rad
                if not math.isfinite(strain) or not math.isfinite(unwrapped):
                    raise ValueError("fiber phase/strain calculation left its finite calibrated domain")
            elif unwrapped is not None:
                reason="temperature_correction_missing"
            rows.append(dict(time_s=time,wrapped_phase_rad=phase,unwrapped_phase_rad=unwrapped,
                             fiber_gauge_mean_axial_strain=strain,valid=strain is not None,
                             phase_segment_id=None if unwrapped is None else segment,reason=reason))
        results.append(dict(gauge_id=g.id,fiber_id=g.fiber_id,
                            distance_interval_m=[g.start_distance_m,g.end_distance_m],states=rows))
    if (file_digest(Path(__file__))!=source_pin or digest(dict(request=q,context=c))!=input_pin
            or digest(request)!=digest(q) or digest(context)!=digest(c)
            or shared_pins!={name:file_digest(Path(__file__).with_name(name+".py")) for name in shared_pins}):
        raise ValueError("fiber processing source or input/context changed during evaluation")
    body=dict(schema="pipeline_integrity.fiber_interferometry.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="computed_diagnostic" if any(r["valid"] for g in results for r in g["states"]) else "needs_data",
              gauges=results,implementation_sha256=source_pin,shared_source_sha256=shared_pins,
              differential_phase_sign="right_phase_minus_left_phase",
              strain_definition="calibrated_fiber_gauge_mean_axial_strain",
              host_pipe_strain_transfer_inferred=False,phase_aliasing_excluded_by_observed_data_alone=False,
              dropped_phase_branches_interpolated=False,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("fiber processing receipt exceeds byte budget")
    return body


def verify_fiber_interferometry_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("fiber processing receipt changed")
    q=FiberInterferometryRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("fiber processing caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("fiber processing caller context differs")
    if evaluate_fiber_interferometry(q,c)!=record:
        raise ValueError("fiber optical calibration/phase replay differs")
    return True


def fiber_interferometry_signal_frames(record, *, gauge_id: str, stream_id: str):
    verify_fiber_interferometry_record(record)
    q=FiberInterferometryRequest.model_validate_json(json.dumps(record["request"]))
    selected=[g for g in record["gauges"] if g["gauge_id"]==gauge_id]
    if len(selected)!=1:
        raise ValueError("fiber signal gauge is absent")
    origin=record["context"]["origin"]
    if origin=="user_declared":
        raise ValueError("fiber frames require an explicit measured, laboratory, simulation or synthetic source origin")
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
        values={"fiber_gauge_mean_axial_strain":row["fiber_gauge_mean_axial_strain"]},
        units={"fiber_gauge_mean_axial_strain":"1"},valid={"fiber_gauge_mean_axial_strain":row["valid"]},
        origin=origin,
        operating_mode="anchored_differential_optical_phase") for row in selected[0]["states"])
    receipt=dict(schema="pipeline_integrity.fiber_interferometry_frames.v1",source_record_digest=record["record_digest"],
                 gauge_id=gauge_id,stream_id=stream_id,frame_count=len(frames),frames_digest=digest(frames))
    receipt["record_digest"]=digest(receipt)
    return frames,receipt


class FiberDASMapping(Contract):
    gauge_id: Name
    channel_id: Name
    mapping_evidence_id: Name
    fiber_support_and_quantity_definition_reviewed: bool = False
    source_row_order_and_nominal_point_sampling_reviewed: bool = False


def bind_fiber_to_external_sensing(record, template, mappings: tuple[FiberDASMapping,...],
                                   context: RunContext, *, raw_source_evidence_id: str):
    """Prepare DAS observations without inventing fiber registration or host strain.

    The template supplies actual channel calibration, registered fiber footprint,
    row identities, timing error and review evidence. Only the replayed strain
    values/quality and the actual processing fingerprint replace placeholders.
    This path retains the source clock and nominal point-sample support; an
    exposure-average or clock transformation requires its own earlier model.
    """
    from .external_sensing import ExternalReading, ExternalSensingRequest, _reading, boundary_metadata

    original_record_pin=digest(record)
    verify_fiber_interferometry_record(record)
    q=FiberInterferometryRequest.model_validate_json(json.dumps(record["request"]))
    original_context=RunContext.model_validate_json(json.dumps(record["context"]))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    require_context_extension(original_context,c)
    t=ExternalSensingRequest.model_validate(template.model_dump(mode="python"))
    if (t.asset_id,t.clock_id,t.source_clock_id,t.origin)!=(q.asset_id,q.clock_id,q.clock_id,c.origin):
        raise ValueError("fiber-to-DAS asset, origin and actual source/analysis clocks must agree")
    if t.source_to_analysis_offset_s!=0.0:
        raise ValueError("fiber-to-DAS does not infer a clock offset or realign source samples")
    if len(t.acquisitions)!=len(q.sample_time_s):
        raise ValueError("DAS acquisition rows must preserve every original optical sample")
    maps=tuple(FiberDASMapping.model_validate(item.model_dump(mode="python")) for item in mappings)
    if (len(maps)!=len(t.channels) or len({item.gauge_id for item in maps})!=len(maps)
            or tuple(item.channel_id for item in maps)!=tuple(channel.id for channel in t.channels)):
        raise ValueError("each ordered DAS channel requires one distinct actual optical gauge mapping")
    gauges={g.id:g for g in q.gauges}
    results={g["gauge_id"]:g for g in record["gauges"]}
    evidence={entry.id:entry for entry in c.evidence}
    source_evidence={entry.id:entry for entry in original_context.evidence}
    source=source_evidence.get(raw_source_evidence_id)
    if source is None or source.sha256 is None or source.sha256!=t.source_sha256:
        raise ValueError("DAS raw-source SHA must equal the selected original optical source evidence")
    for mapping,channel in zip(maps,t.channels,strict=True):
        if (not mapping.fiber_support_and_quantity_definition_reviewed
                or not mapping.source_row_order_and_nominal_point_sampling_reviewed):
            raise ValueError("fiber support, quantity definition and exact source-row/time mapping require review")
        review=evidence.get(mapping.mapping_evidence_id)
        if review is None or review.sha256 is None:
            raise ValueError("fiber-to-DAS mapping review must be source-pinned in the downstream context")
        if mapping.gauge_id not in gauges:
            raise ValueError("DAS mapping identifies an absent optical gauge")
        g=gauges[mapping.gauge_id]
        if raw_source_evidence_id not in g.evidence_ids:
            raise ValueError("each mapped optical gauge must select the same reviewed raw-source evidence")
        if (channel.modality!="DAS" or channel.basis.quantity!="strain" or channel.basis.unit!="1"
                or channel.footprint.kind!="fiber_gauge" or channel.footprint.fiber_id!=g.fiber_id
                or channel.footprint.fiber_distance_interval_m!=(g.start_distance_m,g.end_distance_m)):
            raise ValueError("DAS target must preserve the actual fiber ID, gauge interval and dimensionless mean axial strain")
        if channel.temporal_response!="unspecified":
            raise ValueError("endpoint optical samples do not supply an exposure-average response model")
    required={t.evidence_id,t.clock_mapping_evidence_id,*t.review_evidence_ids.values()}
    for channel in t.channels:
        required.update((channel.evidence_id,channel.basis.evidence_id,channel.footprint.registration_evidence_id))
        if channel.basis.reference_state is not None:
            required.add(channel.basis.reference_state.evidence_id)
    required.update(reading.quality_evidence_id for row in t.acquisitions for reading in row.readings)
    if any(key not in evidence or evidence[key].sha256 is None for key in required):
        raise ValueError("DAS calibration, registration, clock, quality and review evidence must remain source-pinned")
    source_pin=file_digest(Path(__file__))
    external_pin=file_digest(Path(__file__).with_name("external_sensing.py"))
    data=t.model_dump(mode="python")
    data["processing_source_sha256"]=record["implementation_sha256"]
    data["processing_configuration_sha256"]=digest(dict(optical_request_digest=digest(q),
        optical_record_digest=record["record_digest"],mappings=maps,raw_source_evidence_id=raw_source_evidence_id))
    quality=[]
    for index,(time,acquisition) in enumerate(zip(q.sample_time_s,t.acquisitions,strict=True)):
        if (acquisition.time_s,acquisition.exposure_start_s,acquisition.exposure_end_s)!=(time,time,time):
            raise ValueError("DAS template must retain each exact nominal optical point-sample time")
        readings=[]
        for mapping,reading,channel in zip(maps,acquisition.readings,t.channels,strict=True):
            row=results[mapping.gauge_id]["states"][index]
            g=gauges[mapping.gauge_id]
            updated=reading.model_dump(mode="python")
            value=row["fiber_gauge_mean_axial_strain"]
            missing=(any(values[index] is None for values in (g.left_I,g.left_Q,g.right_I,g.right_Q))
                     or g.temperature_change_K[index] is None)
            updated.update(state="quantified" if row["valid"] else "missing" if missing else "invalid",
                           value=value if row["valid"] else None)
            _reading(channel,ExternalReading.model_validate(updated))
            readings.append(updated)
            quality.append(dict(source_row_id=reading.source_row_id,gauge_id=mapping.gauge_id,
                channel_id=mapping.channel_id,optical_sample_index=index,optical_processing_reason=row["reason"],
                anchored_phase_segment_id=row["phase_segment_id"],derived_state=updated["state"],
                preserved_quality_evidence_id=reading.quality_evidence_id,
                mapping_evidence_id=mapping.mapping_evidence_id))
        data["acquisitions"][index]["readings"]=tuple(readings)
    derived=ExternalSensingRequest.model_validate(data)
    bound=boundary_metadata(derived)
    if ("external_sensing_boundary" in original_context.metadata
            and original_context.metadata["external_sensing_boundary"]!=bound):
        raise ValueError("DAS preparation cannot overwrite an inherited external-source boundary identity")
    payload=c.model_dump(mode="python")
    payload["metadata"]={**c.metadata,"external_sensing_boundary":bound}
    downstream=RunContext.model_validate(payload)
    require_context_extension(original_context,downstream)
    receipt=dict(schema="pipeline_integrity.fiber_DAS_binding.v1",source_record_digest=record["record_digest"],
        source_request_digest=digest(q),source_context_digest=digest(original_context),
        template_request_digest=digest(t),derived_request_digest=digest(derived),
        derived_context_digest=digest(downstream),raw_source_evidence_id=raw_source_evidence_id,
        raw_source_sha256=source.sha256,mappings=[item.model_dump(mode="json") for item in maps],quality=quality,
        fiber_processing_source_sha256=record["implementation_sha256"],implementation_sha256=source_pin,
        external_adapter_sha256=external_pin,host_pipe_strain_transfer_inferred=False,
        optical_missing_fading_alias_or_anchor_gaps_interpolated=False)
    receipt["record_digest"]=digest(receipt)
    if (file_digest(Path(__file__))!=source_pin or file_digest(Path(__file__).with_name("external_sensing.py"))!=external_pin
            or digest(template)!=digest(t) or digest(context)!=digest(c)):
        raise ValueError("fiber-to-DAS source, template or actual context changed during preparation")
    if digest(record)!=original_record_pin or digest(mappings)!=digest(maps):
        raise ValueError("fiber-to-DAS optical source or reviewed mappings changed during preparation")
    if len(json.dumps(receipt,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("fiber-to-DAS provenance/quality receipt exceeds retained-byte budget")
    return derived,downstream,receipt
