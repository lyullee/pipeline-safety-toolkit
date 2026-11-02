"""Evidence-qualified use of existing Mode-I, growth and declared-boundary models."""

import csv
import hashlib
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import MaterialClient
from .contracts import Contract, Name, Nonnegative, Positive, Pressure, RunContext, SignalFrame, digest
from .datasets import catalog, file_digest
from .material_exchange import evaluate_material_job, prepare_material_job


class FractureIdentity(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name
    flaw_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)


class FractureResistance(Contract):
    material_condition_id: Name
    value_pa_sqrt_m: Positive | None
    kind: Literal["mode_i_initiation", "crack_arrest", "unqualified_observation", "synthetic_reference"]
    definition: Name
    minimum_temperature_k: Positive
    maximum_temperature_k: Positive
    evidence_id: Name
    applicability_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if self.maximum_temperature_k < self.minimum_temperature_k:
            raise ValueError("fracture-resistance temperature support is reversed")
        return self


class FractureReviews(Contract):
    geometry_and_stress_solution_reviewed: bool = False
    linear_elastic_regime_reviewed: bool = False
    loading_and_environment_reviewed: bool = False


class ModeIRequest(FractureIdentity):
    time_s: Nonnegative
    crack_depth_m: Positive
    wall_thickness_m: Positive
    effective_opening_stress_pa: float | None
    opening_stress_definition: Name
    opening_stress_receipt_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    geometry_factor: Positive | None
    minimum_supported_depth_m: Positive
    maximum_supported_depth_m: Positive
    temperature_k: Positive | None
    resistance: FractureResistance
    reviews: FractureReviews

    @model_validator(mode="after")
    def support(self):
        if self.maximum_supported_depth_m < self.minimum_supported_depth_m:
            raise ValueError("geometry-factor support is reversed")
        return self


class GrowthCalibration(Contract):
    material_condition_id: Name
    coefficient_m_cycle_per_pa_sqrt_m_power: Positive | None
    exponent: Positive | None
    threshold_delta_k_pa_sqrt_m: Nonnegative
    minimum_delta_k_pa_sqrt_m: Positive
    maximum_delta_k_pa_sqrt_m: Positive
    minimum_stress_ratio: float = Field(ge=0, lt=1)
    maximum_stress_ratio: float = Field(ge=0, lt=1)
    minimum_temperature_k: Positive
    maximum_temperature_k: Positive
    evidence_id: Name
    applicability_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if any(
            b < a
            for a, b in (
                (self.minimum_delta_k_pa_sqrt_m, self.maximum_delta_k_pa_sqrt_m),
                (self.minimum_stress_ratio, self.maximum_stress_ratio),
                (self.minimum_temperature_k, self.maximum_temperature_k),
            )
        ):
            raise ValueError("crack-growth calibration support is reversed")
        if self.threshold_delta_k_pa_sqrt_m > self.maximum_delta_k_pa_sqrt_m:
            raise ValueError("growth threshold exceeds the declared calibration maximum")
        return self


class CrackLoadBlock(Contract):
    id: Name
    minimum_opening_stress_pa: float
    maximum_opening_stress_pa: float
    cycles: Positive
    evidence_id: Name

    @model_validator(mode="after")
    def extrema(self):
        if self.maximum_opening_stress_pa < self.minimum_opening_stress_pa:
            raise ValueError("crack-load extrema are reversed")
        return self


class CrackGrowthRequest(FractureIdentity):
    initial_depth_m: Positive
    terminal_depth_m: Positive
    wall_thickness_m: Positive
    geometry_depth_m: tuple[Positive, ...] = Field(min_length=2, max_length=10000)
    geometry_factor: tuple[Positive, ...] | None
    blocks: tuple[CrackLoadBlock, ...] = Field(min_length=1, max_length=10000)
    counting_basis: Name
    cycle_order_reviewed: bool = False
    temperature_k: Positive | None
    resistance: FractureResistance
    calibration: GrowthCalibration
    reviews: FractureReviews
    relative_integration_tolerance: float = Field(default=1e-8, gt=0, le=0.01)
    absolute_cycle_tolerance: Positive = 1e-5
    absolute_depth_tolerance_m: Positive = 1e-12

    @model_validator(mode="after")
    def ordered(self):
        if self.terminal_depth_m < self.initial_depth_m:
            raise ValueError("terminal depth precedes initial depth")
        if any(b <= a for a, b in zip(self.geometry_depth_m, self.geometry_depth_m[1:], strict=False)):
            raise ValueError("geometry depths must strictly increase without sorting")
        if self.geometry_factor is not None and len(self.geometry_factor) != len(self.geometry_depth_m):
            raise ValueError("one factor is required for each geometry depth")
        if len({b.id for b in self.blocks}) != len(self.blocks):
            raise ValueError("ordered crack-load block IDs must be unique")
        return self


def _context(request, context, extra_ids=()):
    context = RunContext.model_validate(context.model_dump())
    if request.asset_id != context.asset_id or request.clock_id != context.clock_id:
        raise ValueError("fracture and context asset or clock differ")
    ids = [e.id for e in context.evidence]
    if len(set(ids)) != len(ids) or set((*request.evidence_ids, *extra_ids)) - set(ids):
        raise ValueError("fracture evidence identifiers must be present and unambiguous")
    return context


def _basis(request):
    r = request.resistance
    if r.material_condition_id != request.material_condition_id:
        return "needs_data", "resistance_material_condition_mismatch"
    if r.kind not in {"mode_i_initiation", "synthetic_reference"}:
        return "needs_data", "resistance_definition_is_not_qualified_initiation"
    if r.value_pa_sqrt_m is None or request.temperature_k is None:
        return "needs_data", "missing_resistance_or_temperature"
    if not r.applicability_reviewed or not all(request.reviews.model_dump().values()):
        return "needs_data", "fracture_applicability_not_reviewed"
    if not r.minimum_temperature_k <= request.temperature_k <= r.maximum_temperature_k:
        return "out_of_scope", "resistance_temperature_outside_support"
    return None, None


def _result(kind, request, context, status, reason, native=None, **values):
    body = dict(
        schema="pipeline_integrity.fracture_exchange.v1",
        kind=kind,
        status=status,
        reason=reason,
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        native=None if native is None else native.model_dump(mode="json"),
        values=values,
        implementation_sha256=file_digest(Path(__file__)),
        failure_probability_calculated=False,
        code_or_field_acceptance=False,
        cycles_converted_to_elapsed_life=False,
        output_origin="simulation",
    )
    body["record_digest"] = digest(body)
    return body


def assess_mode_i(
    specification: ModeIRequest, context: RunContext, *, client=None, opening_stress_receipt=None
):
    s = ModeIRequest.model_validate(specification.model_dump())
    context = _context(s, context, (s.resistance.evidence_id,))
    if s.opening_stress_receipt_digest is not None:
        r = opening_stress_receipt
        if (
            r is None
            or r.get("record_digest") != s.opening_stress_receipt_digest
            or digest({k: v for k, v in r.items() if k != "record_digest"}) != r["record_digest"]
            or r["context_digest"] != digest(context)
            or r["value_pa"] != s.effective_opening_stress_pa
            or r["mapping"]["interpretation"] != s.opening_stress_definition
        ):
            raise ValueError("opening-stress receipt, context, definition or value differs")
        if r["status"] != "computed":
            return _result(
                "mode_i",
                s,
                context,
                "needs_data",
                "opening_stress_receipt_not_computed",
                opening_source_status=r["status"],
            )
    elif opening_stress_receipt is not None:
        raise ValueError("bind the opening-stress receipt digest explicitly")
    status, reason = _basis(s)
    if status is None:
        if s.effective_opening_stress_pa is None or s.geometry_factor is None:
            status, reason = "needs_data", "missing_opening_stress_or_geometry_factor"
        elif (
            s.effective_opening_stress_pa < 0
            or s.crack_depth_m >= s.wall_thickness_m
            or not s.minimum_supported_depth_m <= s.crack_depth_m <= s.maximum_supported_depth_m
        ):
            status, reason = "out_of_scope", "opening_load_or_geometry_outside_support"
    if status is not None:
        return _result("mode_i", s, context, status, reason)
    client = MaterialClient() if client is None else client
    job = prepare_material_job(
        "fracture.stress_intensity",
        dict(
            crack_depth_m=s.crack_depth_m,
            tensile_stress_Pa=s.effective_opening_stress_pa,
            geometry_factor=s.geometry_factor,
            fracture_toughness_Pa_sqrt_m=s.resistance.value_pa_sqrt_m,
        ),
        context,
        location_id=s.location_id,
        evidence_ids=(*s.evidence_ids, s.resistance.evidence_id),
        client=client,
    )
    native = evaluate_material_job(job, context, client=client)
    outcome = "unknown"
    if native.native.status == "computed":
        outcome = (
            "at_or_below_declared_resistance"
            if native.native.payload["values"]["toughness_margin_Pa_sqrt_m"] >= 0
            else "above_declared_resistance"
        )
    critical = (
        native.native.payload["values"].get("critical_depth_m")
        if native.native.status == "computed"
        else None
    )
    qualified = (
        critical is not None
        and s.minimum_supported_depth_m <= critical <= s.maximum_supported_depth_m
        and critical < s.wall_thickness_m
    )
    return _result(
        "mode_i",
        s,
        context,
        native.native.status,
        None,
        native,
        declared_resistance_outcome=outcome,
        qualified_critical_depth_m=critical if qualified else None,
        native_critical_depth_within_supported_geometry=qualified,
    )


def assess_crack_growth(specification: CrackGrowthRequest, context: RunContext, *, client=None):
    s = CrackGrowthRequest.model_validate(specification.model_dump())
    context = _context(
        s, context, (s.resistance.evidence_id, s.calibration.evidence_id, *(b.evidence_id for b in s.blocks))
    )
    status, reason = _basis(s)
    c = s.calibration
    if status is None:
        if (
            s.geometry_factor is None
            or c.coefficient_m_cycle_per_pa_sqrt_m_power is None
            or c.exponent is None
            or not s.cycle_order_reviewed
            or not c.applicability_reviewed
            or c.material_condition_id != s.material_condition_id
        ):
            status, reason = "needs_data", "growth_calibration_geometry_or_order_not_qualified"
        elif (
            s.terminal_depth_m >= s.wall_thickness_m
            or not s.geometry_depth_m[0] <= s.initial_depth_m <= s.terminal_depth_m <= s.geometry_depth_m[-1]
            or any(b < a for a, b in zip(s.geometry_factor, s.geometry_factor[1:], strict=False))
            or any(b.minimum_opening_stress_pa < 0 for b in s.blocks)
            or not c.minimum_temperature_k <= s.temperature_k <= c.maximum_temperature_k
        ):
            status, reason = "out_of_scope", "growth_load_geometry_or_temperature_outside_support"
    if status is not None:
        return _result("ordered_growth", s, context, status, reason)
    client = MaterialClient() if client is None else client
    parameters = dict(
        initial_depth_m=s.initial_depth_m,
        terminal_depth_m=s.terminal_depth_m,
        geometry_depth_m=list(s.geometry_depth_m),
        geometry_factor=list(s.geometry_factor),
        blocks=[
            dict(
                minimum_stress_Pa=b.minimum_opening_stress_pa,
                maximum_stress_Pa=b.maximum_opening_stress_pa,
                cycles=b.cycles,
            )
            for b in s.blocks
        ],
        coefficient_m_cycle_per_Pa_sqrt_m_power=c.coefficient_m_cycle_per_pa_sqrt_m_power,
        exponent=c.exponent,
        threshold_delta_K_Pa_sqrt_m=c.threshold_delta_k_pa_sqrt_m,
        minimum_calibrated_delta_K_Pa_sqrt_m=c.minimum_delta_k_pa_sqrt_m,
        maximum_calibrated_delta_K_Pa_sqrt_m=c.maximum_delta_k_pa_sqrt_m,
        minimum_calibrated_stress_ratio=c.minimum_stress_ratio,
        maximum_calibrated_stress_ratio=c.maximum_stress_ratio,
        fracture_toughness_Pa_sqrt_m=s.resistance.value_pa_sqrt_m,
        relative_integration_tolerance=s.relative_integration_tolerance,
        absolute_cycle_tolerance=s.absolute_cycle_tolerance,
        absolute_depth_tolerance_m=s.absolute_depth_tolerance_m,
    )
    job = prepare_material_job(
        "fracture.variable_paris",
        parameters,
        context,
        location_id=s.location_id,
        evidence_ids=(
            *s.evidence_ids,
            s.resistance.evidence_id,
            c.evidence_id,
            *(b.evidence_id for b in s.blocks),
        ),
        client=client,
    )
    native = evaluate_material_job(job, context, client=client)
    return _result(
        "ordered_growth",
        s,
        context,
        native.native.status,
        None,
        native,
        ordered_block_ids=[b.id for b in s.blocks],
        counting_basis=s.counting_basis,
    )


class SectionOpeningMapping(Contract):
    radius_m: Positive
    component: Literal["hoop_stress_Pa", "axial_stress_Pa"]
    crack_face_pressure: Pressure | None
    additional_opening_stress_pa: float
    interpretation: Name
    evidence_id: Name
    scalar_mapping_reviewed: bool = False


def opening_stress_from_section(section, mapping: SectionOpeningMapping, context: RunContext):
    mapping = SectionOpeningMapping.model_validate(mapping.model_dump())
    if digest({k: v for k, v in section.items() if k != "record_digest"}) != section.get("record_digest"):
        raise ValueError("pressure-section receipt changed")
    original = section["context"]
    if any(original[k] != getattr(context, k) for k in ("asset_id", "clock_id", "scenario_id")):
        raise ValueError("pressure-section asset, clock or load case differs")
    if mapping.evidence_id not in {e.id for e in context.evidence}:
        raise ValueError("opening-stress mapping evidence is absent")
    status, reason, value = "needs_data", "section_or_mapping_not_qualified", None
    if (
        section["status"] == "computed"
        and mapping.scalar_mapping_reviewed
        and mapping.crack_face_pressure is not None
    ):
        pressure = mapping.crack_face_pressure.absolute_pa()
        radii = section["specification"]["evaluation_radii_m"]
        if mapping.radius_m not in radii:
            raise ValueError(
                "opening stress needs an exact evaluated section radius; interpolation is not automatic"
            )
        row = section["material_records"][radii.index(mapping.radius_m)]
        value = (
            row["native"]["payload"]["values"][mapping.component]
            + pressure
            + mapping.additional_opening_stress_pa
        )
        status, reason = "computed", None
    body = dict(
        schema="pipeline_integrity.section_opening_stress.v1",
        status=status,
        reason=reason,
        value_pa=value,
        source_section_digest=section["record_digest"],
        source_section_status=section["status"],
        mapping=mapping.model_dump(mode="json"),
        context_digest=digest(context),
        valid_for_arbitrary_crack_pressure_or_stress_gradient=False,
    )
    body["record_digest"] = digest(body)
    return body


def assess_failure_points(parameters, context: RunContext, *, location_id, evidence_ids, client=None):
    if set(e["source_id"] for e in parameters["curve"]["evidence"]) - {e.id for e in context.evidence}:
        raise ValueError("declared failure-assessment curve evidence is absent")
    for point in parameters["points"]:
        if point["location_id"] != location_id or point["load_case_id"] != context.scenario_id:
            raise ValueError("failure-assessment point location or load case differs")
        ids = (
            point["geometry_solution_id"],
            point["reference_stress_solution_id"],
            point["toughness_source_id"],
        )
        if set(ids) - {e.id for e in context.evidence}:
            raise ValueError("K, reference-stress or toughness solution evidence is absent")
    client = MaterialClient() if client is None else client
    job = prepare_material_job(
        "structural.failure_assessment_points",
        parameters,
        context,
        location_id=location_id,
        evidence_ids=evidence_ids,
        client=client,
    )
    return evaluate_material_job(job, context, client=client)


def crack_arrest_frames(archive, specimen_id, *, reader=None):
    archive = Path(archive)
    asset = next(row for row in catalog() if row.id == "crack_arrest").assets[0]
    sha = file_digest(archive)
    if sha != asset.checksum.split(":", 1)[1] or archive.stat().st_size != asset.bytes:
        raise ValueError("laboratory crack-arrest origin requires the reviewed source archive")
    if reader is None:
        from matintegrity.fracture_data import read_crack_arrest_series as reader
    data = reader(archive, specimen_id)
    expected = [
        "time_s",
        "machine_displacement_m",
        "force_N",
        "extensometer_displacement_m",
        "reported_cycle_index",
    ]
    if data.columns.tolist() != expected or data.empty:
        raise ValueError("native crack-arrest columns changed")
    if data.attrs["source_archive_sha256"] != sha or data.attrs["specimen_id"] != specimen_id:
        raise ValueError("native crack-arrest identity changed")
    with zipfile.ZipFile(archive) as source:
        payload = source.read(data.attrs["source_member"])
    if hashlib.sha256(payload).hexdigest() != data.attrs["source_member_sha256"]:
        raise ValueError("native crack-arrest member digest differs")
    clock = "crack_arrest:" + specimen_id + ":" + data.attrs["source_member_sha256"]
    channels = expected[1:]
    frames = []
    for row in data.itertuples(index=False, name=None):
        if frames and row[0] <= frames[-1].time_s:
            raise ValueError("crack-arrest source sample clock did not strictly increase")
        frames.append(
            SignalFrame(
                stream_id="crack_arrest:" + specimen_id,
                clock_id=clock,
                time_s=float(row[0]),
                values={k: float(v) for k, v in zip(channels, row[1:], strict=True)},
                units=dict(
                    machine_displacement_m="m",
                    force_N="N",
                    extensometer_displacement_m="m",
                    reported_cycle_index="1",
                ),
                valid={k: True for k in channels},
                origin="laboratory",
            )
        )
    metadata = dict(
        source=dict(data.attrs),
        clock_id=clock,
        raw_header_sha256=hashlib.sha256(payload.splitlines()[0]).hexdigest(),
        native_header_representation_normalized=len(
            next(csv.reader([payload.decode("utf-8-sig").splitlines()[0]], delimiter=";"))
        )
        == 1,
        source_sample_count=len(frames),
        source_rows_sorted_or_resampled=False,
        temperature_is_filename_metadata_not_measured_history=True,
        reported_cycle_index_is_not_inferred_fatigue_cycle_count=True,
        initiation_toughness_or_dynamic_arrest_inferred=False,
    )
    return tuple(frames), metadata
