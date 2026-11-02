"""Typed electrochemical meaning and applicability around the existing material engine.

Protection demand, net external electrode current and partial metal-dissolution
current have different meanings. No absolute-value conversion identifies one from
another, and a charge budget alone does not establish a protective potential.
"""

import math
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .adapters import _aligned_frames
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import catalog, file_digest
from .material_exchange import evaluate_material_job, prepare_material_job
from .units import convert_quantity

METHOD_REVIEWS = {
    "corrosion.coupon_loss": ("net_mass_after_cleaning", "area_density_and_exposure"),
    "corrosion.faraday": ("partial_dissolution_or_conditional_icorr", "valence_density_efficiency"),
    "corrosion.charge_to_loss": ("partial_dissolution_or_conditional_icorr", "valence_density_efficiency"),
    "corrosion.stern_geary": ("activation_and_linear_polarization", "tafel_and_area_normalization"),
    "corrosion.galvanic_linear": (
        "common_potential_reference",
        "compatible_surface_environment",
        "declared_linear_regime",
    ),
    "protection.coating_coverage": ("dry_film_solids_basis", "application_transfer_efficiency"),
    "protection.cathodic_budget": (
        "bare_fraction_and_demand_basis",
        "anode_capacity_utilization",
        "minimum_current_installation_interference",
    ),
}
Method = Literal[
    "corrosion.coupon_loss",
    "corrosion.faraday",
    "corrosion.charge_to_loss",
    "corrosion.stern_geary",
    "corrosion.galvanic_linear",
    "protection.coating_coverage",
    "protection.cathodic_budget",
]
Meaning = Literal[
    "anodic_dissolution",
    "conditional_corrosion_current_estimate",
    "protective_demand",
    "net_external_electrode_current",
    "not_applicable",
    "unresolved",
]


class ElectrochemicalRequest(Contract):
    method: Method
    asset_id: Name
    location_id: Name
    clock_id: Name
    condition_id: Name
    material_id: Name | None
    environment_id: Name | None
    current_meaning: Meaning
    potential_reference_id: Name | None = None
    parameters: dict
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @field_validator("parameters")
    @classmethod
    def serializable(cls, value):
        return json_value(value)

    @model_validator(mode="after")
    def known_reviews(self):
        if set(self.applicability_reviews) - set(METHOD_REVIEWS[self.method]):
            raise ValueError("applicability review keys must belong to the selected method")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence identifiers must be unique")
        return self


class SurfaceCurrentReduction(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    condition_id: Name
    channel: Name
    exposed_area_m2: Positive | None
    density_area_basis: Literal["bare_surface", "full_exposed_surface"]
    current_meaning: Meaning
    maximum_interval_s: Positive
    time_tolerance_s: float = Field(default=1e-9, ge=0)
    sign_and_surface_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _verify(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("electrochemical source or result changed after sealing")


def reduce_surface_current(
    observations: tuple[SignalFrame, ...], specification: SurfaceCurrentReduction, context: RunContext
):
    """Reduce declared point-linear current density, preserving gaps, sign and time support."""
    spec = SurfaceCurrentReduction.model_validate_json(specification.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (spec.asset_id, spec.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("surface-current source and context asset/clock differ")
    if set(spec.evidence_ids) - {e.id for e in context.evidence}:
        raise ValueError("surface-current source evidence is absent")
    frames, channels = _aligned_frames(
        tuple(SignalFrame.model_validate_json(f.model_dump_json()) for f in observations)
    )
    if len(frames) < 2 or spec.channel not in channels or frames[0].clock_id != spec.clock_id:
        raise ValueError("surface-current reduction requires a matching channel/clock and two or more points")
    unit = frames[0].units[spec.channel]
    convert_quantity(0.0, unit, "A/m2")
    values = [
        None if f.values[spec.channel] is None else convert_quantity(f.values[spec.channel], unit, "A/m2")
        for f in frames
    ]
    terms, durations, missing = [], [], []
    issues, status = [], "computed"
    for index, (first, last) in enumerate(zip(frames, frames[1:], strict=False)):
        dt, a, b = last.time_s - first.time_s, values[index], values[index + 1]
        if (
            a is None
            or b is None
            or not first.valid[spec.channel]
            or not last.valid[spec.channel]
            or dt > spec.maximum_interval_s + spec.time_tolerance_s
        ):
            missing.append(index)
            continue
        terms.append((a / 2 + b / 2) * dt)
        durations.append(dt)
    charge, total = None, None
    try:
        charge = math.fsum(terms)
        total = None if spec.exposed_area_m2 is None else charge * spec.exposed_area_m2
        if not math.isfinite(charge) or total is not None and not math.isfinite(total):
            raise OverflowError
    except (OverflowError, ValueError):
        charge, total, status = None, None, "out_of_scope"
        issues.append("current/charge arithmetic is not representable; no clipping")
    if missing and status != "out_of_scope":
        status = "needs_data"
        issues.append("missing, invalid or long-gap intervals leave unresolved charge")
    if (
        not spec.sign_and_surface_reviewed
        or spec.exposed_area_m2 is None
        or spec.current_meaning in ("unresolved", "not_applicable")
    ):
        if status != "out_of_scope":
            status = "needs_data"
        issues.append("current meaning, sign, surface/area and review must be resolved")
    if spec.current_meaning in (
        "anodic_dissolution",
        "conditional_corrosion_current_estimate",
        "protective_demand",
    ) and any(v is not None and v < 0 for v in values):
        status = "out_of_scope"
        issues.append("nonnegative declared current has negative samples; no absolute-value conversion")
    body = dict(
        schema="pipeline_integrity.surface_current.v1",
        specification=spec.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=status,
        issues=issues,
        time_s=[f.time_s for f in frames],
        current_density_A_m2=values,
        source_frames_digest=digest(frames),
        source_origin=frames[0].origin,
        source_stream_id=frames[0].stream_id,
        density_charge_C_m2_on_known_intervals=charge,
        charge_C_on_known_intervals=total,
        covered_duration_s=math.fsum(durations),
        observed_span_s=frames[-1].time_s - frames[0].time_s,
        unresolved_interval_indices=missing,
        integration="point_linear_no_sorting_or_gap_fill",
        total_metal_dissolution_identified=False,
        protective_potential_established=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    return _seal(body)


def evaluate_electrochemical(
    request: ElectrochemicalRequest, context: RunContext, *, history_record=None, client=None
):
    """Review semantics, then reuse strict schema-pinned MaterialJob; never clone the owner's laws."""
    request = ElectrochemicalRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("electrochemical request and context asset/clock differ")
    if set(request.evidence_ids) - {e.id for e in context.evidence}:
        raise ValueError("electrochemical input/applicability evidence is absent")
    result = dict(
        schema="pipeline_integrity.electrochemical_exchange.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="computed",
        issues=[],
        values={},
        output_units={},
        native_receipt=None,
        history_digest=None,
        reference_model_only=True,
        output_origin="simulation",
        field_or_standard_qualification=False,
        remaining_life_or_failure_probability_inferred=False,
        protective_potential_established=False,
        implementation_sha256=file_digest(Path(__file__)),
    )

    def withheld(status, issue):
        result.update(status=status, issues=[issue])
        return _seal(result)

    if (
        request.material_id is None
        or request.environment_id is None
        or any(not request.applicability_reviews.get(key, False) for key in METHOD_REVIEWS[request.method])
    ):
        return withheld(
            "needs_data", "material/environment and all method-specific applicability reviews are required"
        )
    if any(value is None for value in request.parameters.values()):
        return withheld("needs_data", "an unresolved native model input cannot be silently filled")
    meanings = {
        "corrosion.coupon_loss": ("not_applicable",),
        "corrosion.faraday": ("anodic_dissolution", "conditional_corrosion_current_estimate"),
        "corrosion.charge_to_loss": ("anodic_dissolution", "conditional_corrosion_current_estimate"),
        "corrosion.stern_geary": ("conditional_corrosion_current_estimate",),
        "corrosion.galvanic_linear": ("net_external_electrode_current",),
        "protection.coating_coverage": ("not_applicable",),
        "protection.cathodic_budget": ("protective_demand",),
    }
    if request.current_meaning == "unresolved":
        return withheld("needs_data", "current meaning is unresolved")
    if request.current_meaning not in meanings[request.method]:
        return withheld("out_of_scope", "current meaning cannot supply the selected physical model")
    if request.method == "corrosion.galvanic_linear" and request.potential_reference_id is None:
        return withheld("needs_data", "galvanic free potentials require one explicit common reference")
    if history_record is not None:
        _verify(history_record)
        if history_record.get("schema") != "pipeline_integrity.surface_current.v1":
            raise ValueError("bound source is not a surface-current reduction")
        spec = history_record["specification"]
        if history_record["context"] != context.model_dump(mode="json") or (
            spec["asset_id"],
            spec["location_id"],
            spec["clock_id"],
            spec["condition_id"],
        ) != (request.asset_id, request.location_id, request.clock_id, request.condition_id):
            raise ValueError("history/request asset, location, condition, clock or context changed")
        result["history_digest"] = history_record["record_digest"]
        if history_record["status"] != "computed":
            return withheld(
                history_record["status"], "surface-current history is incomplete or outside scope"
            )
        if spec["current_meaning"] != request.current_meaning:
            return withheld(
                "out_of_scope", "history current meaning differs from the requested physical model"
            )
        p = request.parameters
        if request.method == "corrosion.charge_to_loss":
            if p.get("anodic_charge_C") != history_record["charge_C_on_known_intervals"]:
                raise ValueError("dissolution charge differs from the complete bound source history")
        elif request.method == "protection.cathodic_budget":
            if (
                p.get("time_s") != history_record["time_s"]
                or p.get("bare_surface_current_density_A_m2") != history_record["current_density_A_m2"]
                or p.get("protected_area_m2") != spec["exposed_area_m2"]
            ):
                raise ValueError("protection history changed its time, current density or area")
            if spec["density_area_basis"] == "full_exposed_surface" and any(
                v != 1 for v in p.get("bare_surface_fraction", [])
            ):
                return withheld(
                    "out_of_scope",
                    "full-surface demand cannot be multiplied by a bare fraction a second time",
                )
        else:
            raise ValueError("bound current histories supply charge-to-loss or cathodic budgets only")
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    native = evaluate_material_job(job, context, client=client)
    native.check_integrity()
    payload = native.native.payload
    result.update(
        status=payload["status"],
        values=payload["values"],
        output_units=payload["units"],
        native_receipt=native.model_dump(mode="json"),
        issues=list(payload["notes"]),
    )
    return _seal(result)


def load_galvanized_tafel_summary(path):
    """Read a pinned reported-summary table without inventing area, reference or replicates.

    Reported icorr is an author's fitted estimate. Signed cathodic Tafel slopes
    and unspecified plus/minus statistics remain distinct from positive model
    slope magnitudes and independent measurement uncertainty distributions.
    """
    path = Path(path)
    dataset = next(d for d in catalog() if d.id == "galvanized_polarization")
    asset = next(a for a in dataset.assets if a.filename == "metals-3156094_data.xlsx")
    algorithm, expected = asset.checksum.split(":", 1)
    if file_digest(path, algorithm) != expected:
        raise ValueError("reported Tafel workbook differs from the pinned supplier checksum")
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parsed = []

    def reported(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return dict(
                value=float(value), plus_minus=None, uncertainty_kind="not_reported", source_text=str(value)
            )
        matched = re.fullmatch(
            r"\s*([+-]?[0-9]+(?:\.[0-9]+)?)\s*(?:±\s*([0-9]+(?:\.[0-9]+)?))?\s*", str(value)
        )
        if matched is None:
            raise ValueError("unsupported reported numeric/statistic format")
        return dict(
            value=float(matched[1]),
            plus_minus=None if matched[2] is None else float(matched[2]),
            uncertainty_kind="unspecified_plus_minus" if matched[2] else "not_reported",
            source_text=value,
        )

    try:
        sheet = workbook["Tafel"]
        if (sheet.max_row, sheet.max_column) != (22, 9):
            raise ValueError("unsupported reported Tafel table layout")
        for start, electrolyte in ((2, "0.1 M HCl"), (9, "0.1 M H2SO4"), (16, "5% NaCl")):
            if sheet.cell(start, 2).value != electrolyte:
                raise ValueError("reported electrolyte order changed")
            labels = [str(sheet.cell(start + offset, 1).value) for offset in range(1, 7)]
            if not all(
                token in label
                for token, label in zip(
                    ("Ecorr", "icorr", "βa", "βc", "Corrosion rate", "Polarization resistance"),
                    labels,
                    strict=True,
                )
            ):
                raise ValueError("reported Tafel endpoints or units changed")
            for col in range(2, 10):
                specimen = str(sheet.cell(1, col).value)
                values = [reported(sheet.cell(start + offset, col).value) for offset in range(1, 7)]
                if values[2]["value"] <= 0 or values[3]["value"] >= 0:
                    raise ValueError("reported Tafel slope sign convention changed")
                parsed.append(
                    dict(
                        specimen_id=specimen,
                        electrolyte_id=electrolyte,
                        zinc_coated=specimen.startswith("Z"),
                        source_cells=[sheet.cell(start + offset, col).coordinate for offset in range(1, 7)],
                        corrosion_potential_reported_V=values[0],
                        corrosion_current_density_reported_mA_cm2=values[1],
                        corrosion_current_density_estimate_A_m2=values[1]["value"] * 10.0,
                        anodic_slope_V_decade=values[2]["value"],
                        cathodic_signed_slope_V_decade=values[3]["value"],
                        cathodic_slope_magnitude_V_decade=-values[3]["value"],
                        reported_rate_mm_year=values[4],
                        polarization_resistance_ohm=values[5]["value"],
                        exposed_area_m2=None,
                        potential_reference_id=None,
                        independently_identified_dissolution_current=False,
                    )
                )
    finally:
        workbook.close()
    return _seal(
        dict(
            schema="pipeline_integrity.galvanized_tafel_summary.v1",
            dataset_id=dataset.id,
            source=dataset.source,
            citation=dataset.citation,
            creators=list(dataset.creators),
            license_id=dataset.license_id,
            source_sha256=file_digest(path),
            supplier_checksum=asset.checksum,
            rows=parsed,
            source_rows_are_independent_trials=False,
            polarization_resistance_is_area_normalized=False,
            source_fit_reproduced_or_validated=False,
            implementation_sha256=file_digest(Path(__file__)),
        )
    )
