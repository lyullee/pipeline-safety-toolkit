"""Audited material-engine jobs, pressure sections and solid-temperature signal exchange."""

import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters import ExternalResult, MaterialClient
from .contracts import (
    Contract,
    Name,
    Nonnegative,
    Positive,
    Pressure,
    RunContext,
    SignalFrame,
    digest,
    json_value,
)
from .datasets import catalog, file_digest


class MaterialJob(Contract):
    model_id: Name
    model_version: Name
    asset_id: Name
    location_id: Name
    clock_id: Name
    parameters: dict
    output_units: dict[str, str]
    probability_semantics: dict[str, str] = Field(default_factory=dict)
    input_schema_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[Name, ...] = Field(min_length=1)


class MaterialReceipt(Contract):
    schema_version: Literal["pipeline_integrity.material_job.v1"] = "pipeline_integrity.material_job.v1"
    job: MaterialJob
    context: RunContext
    native: ExternalResult
    implementation_sha256: str
    result_digest: str
    field_or_design_acceptance: Literal[False] = False

    def check_integrity(self):
        if digest(self.native.payload) != self.native.payload_digest:
            raise ValueError("material-engine payload changed")
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("material job, context or result changed")


def prepare_material_job(model_id, parameters, context, *, location_id, evidence_ids, client=None):
    """Snapshot the live owner schema; the resulting job can be persisted and explicitly reviewed."""
    client = MaterialClient() if client is None else client
    description = client.describe(model_id)
    if description["model_id"] != model_id:
        raise ValueError("native schema model identity differs")
    return MaterialJob(
        model_id=model_id,
        model_version=description["model_version"],
        asset_id=context.asset_id,
        location_id=location_id,
        clock_id=context.clock_id,
        parameters=json_value(parameters),
        output_units=description["output_units"],
        probability_semantics=description["probability_semantics"],
        input_schema_digest=digest(description["input_schema"]),
        evidence_ids=tuple(evidence_ids),
    )


def _preserved(supplied, normalized):
    if isinstance(supplied, bool) or isinstance(normalized, bool):
        return type(supplied) is type(normalized) and supplied == normalized
    if isinstance(supplied, dict):
        return isinstance(normalized, dict) and all(
            k in normalized and _preserved(v, normalized[k]) for k, v in supplied.items()
        )
    if isinstance(supplied, list):
        return (
            isinstance(normalized, list)
            and len(supplied) == len(normalized)
            and all(_preserved(a, b) for a, b in zip(supplied, normalized, strict=True))
        )
    return supplied == normalized


def evaluate_material_job(job: MaterialJob, context: RunContext, *, client=None):
    job = MaterialJob.model_validate(job.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if job.asset_id != context.asset_id or job.clock_id != context.clock_id:
        raise ValueError("material job and context asset or clock differ")
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)) or set(job.evidence_ids) - set(ids):
        raise ValueError("material jobs need unique, present evidence identifiers")
    client = MaterialClient() if client is None else client
    description = client.describe(job.model_id)
    if description["model_id"] != job.model_id:
        raise ValueError("native schema model identity differs")
    if (
        description["model_version"] != job.model_version
        or description["output_units"] != job.output_units
        or description["probability_semantics"] != job.probability_semantics
        or digest(description["input_schema"]) != job.input_schema_digest
    ):
        raise ValueError("the material model schema, version, units or probability meaning changed")
    external = client.evaluate(
        job.model_id, job.parameters, context, location_id=job.location_id, input_mode="json"
    )
    payload = external.payload
    if external.status not in {"computed", "needs_data", "out_of_scope"}:
        raise ValueError("unrecognized material calculation status")
    if digest(payload) != external.payload_digest:
        raise ValueError("native material payload digest differs")
    if (
        external.owner != "matintegrity"
        or external.asset_id != job.asset_id
        or external.owner_schema != "matintegrity.assessment.v1"
        or payload.get("schema_version") != "matintegrity.assessment.v1"
        or payload.get("model_id") != job.model_id
        or payload.get("model_version") != job.model_version
        or payload.get("units") != job.output_units
        or payload.get("probability_semantics") != job.probability_semantics
        or external.status != payload.get("status")
    ):
        raise ValueError("native material result changed its model, status, units or probability meaning")
    native_context = payload["context"]
    if (
        native_context["asset_id"] != job.asset_id
        or native_context["location_id"] != job.location_id
        or native_context["time_basis"] != job.clock_id
        or native_context["metadata"].get("pipeline_context_digest") != digest(context)
        or native_context["metadata"].get("pipeline_scenario_id") != context.scenario_id
        or native_context["metadata"].get("pipeline_origin") != context.origin
        or native_context["metadata"].get("pipeline_input_validation_mode") != "json"
    ):
        raise ValueError("native material result changed its clock, location or source context")
    if not _preserved(json_value(job.parameters), payload["inputs"]):
        raise ValueError("native material result changed a supplied input")
    canonical = {key: payload[key] for key in ("model_id", "model_version", "inputs", "context")}
    if payload["input_digest"] != digest(canonical):
        raise ValueError("native material input receipt differs")
    body = dict(job=job, context=context, native=external, implementation_sha256=file_digest(Path(__file__)))
    provisional = MaterialReceipt(**body, result_digest="pending")
    return provisional.model_copy(
        update={"result_digest": digest(provisional.model_dump(mode="json", exclude={"result_digest"}))}
    )


class PressureSection(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    time_s: Nonnegative
    inner_radius_m: Positive
    outer_radius_m: Positive
    evaluation_radii_m: tuple[Positive, ...] = Field(min_length=2, max_length=1000)
    internal_pressure: Pressure | None
    external_pressure: Pressure | None
    pressure_loaded_closed_ends: bool
    geometry_evidence_id: Name
    pressure_evidence_id: Name
    absolute_stress_tolerance_pa: Positive = 1e-6

    @model_validator(mode="after")
    def profile(self):
        r = self.evaluation_radii_m
        if (
            self.outer_radius_m <= self.inner_radius_m
            or r[0] != self.inner_radius_m
            or r[-1] != self.outer_radius_m
            or any(b <= a for a, b in zip(r, r[1:], strict=False))
        ):
            raise ValueError("section radii strictly span the complete inner-to-outer annulus")
        if any(
            p is not None and p.reference == "differential"
            for p in (self.internal_pressure, self.external_pressure)
        ):
            raise ValueError("two pressure tractions need individual absolute or explicit gauge references")
        return self


def evaluate_pressure_section(specification: PressureSection, context: RunContext, *, client=None):
    s = PressureSection.model_validate(specification.model_dump())
    context = RunContext.model_validate(context.model_dump())
    if s.asset_id != context.asset_id or s.clock_id != context.clock_id:
        raise ValueError("pressure section and context identities or clocks differ")
    if {s.geometry_evidence_id, s.pressure_evidence_id} - {e.id for e in context.evidence}:
        raise ValueError("section geometry or pressure evidence is absent")
    records = []
    status = "needs_data"
    residuals = None
    if s.internal_pressure is not None and s.external_pressure is not None:
        pi, pe = s.internal_pressure.absolute_pa(), s.external_pressure.absolute_pa()
        client = MaterialClient() if client is None else client
        for r in s.evaluation_radii_m:
            job = prepare_material_job(
                "mechanics.lame_cylinder",
                dict(
                    inner_radius_m=s.inner_radius_m,
                    outer_radius_m=s.outer_radius_m,
                    evaluation_radius_m=r,
                    internal_pressure_Pa=pi,
                    external_pressure_Pa=pe,
                    closed_ends=s.pressure_loaded_closed_ends,
                ),
                context,
                location_id=s.location_id,
                evidence_ids=(s.geometry_evidence_id, s.pressure_evidence_id),
                client=client,
            )
            receipt = evaluate_material_job(job, context, client=client)
            records.append(receipt.model_dump(mode="json"))
        states = {r["native"]["status"] for r in records}
        status = (
            "computed"
            if states == {"computed"}
            else "needs_data"
            if "needs_data" in states
            else "out_of_scope"
        )
        if status == "computed":
            residuals = dict(
                inner_radial_traction_pa=records[0]["native"]["payload"]["values"]["radial_stress_Pa"] + pi,
                outer_radial_traction_pa=records[-1]["native"]["payload"]["values"]["radial_stress_Pa"] + pe,
            )
            if any(abs(v) > s.absolute_stress_tolerance_pa for v in residuals.values()):
                status = "withheld"
    body = dict(
        schema="pipeline_integrity.pressure_section.v1",
        specification=s.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=status,
        material_records=records,
        boundary_traction_residuals=residuals,
        total_stresses_include_absolute_pressure=True,
        open_end_model_has_zero_axial_stress=not s.pressure_loaded_closed_ends,
        buckling_plasticity_or_fitness_for_service_qualified=False,
    )
    body["record_digest"] = digest(body)
    return body


def solid_thermal_frames(receipt: MaterialReceipt):
    receipt.check_integrity()
    native = receipt.native.payload
    if receipt.job.model_id != "thermal.transient_conduction_1d" or native["status"] != "computed":
        raise ValueError("only a computed solid-conduction receipt can produce simulation frames")
    times = native["inputs"]["time_s"]
    centers = native["values"]["cell_centers_m"]
    temperatures = native["values"]["temperature_history_K"]
    if len(times) != len(temperatures) or any(len(row) != len(centers) for row in temperatures):
        raise ValueError("native temperature rows, cells and requested output times do not align")
    if any(t < 0 for t in times) or any(b <= a for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("native thermal output clock changed")
    for t, row in zip(times, temperatures, strict=True):
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in row):
            raise ValueError("native solid temperatures must be positive SI values")
        yield SignalFrame(
            stream_id=receipt.job.asset_id + ":" + receipt.job.location_id,
            clock_id=receipt.job.clock_id,
            time_s=float(t),
            values={f"cell_temperature:{i}": float(v) for i, v in enumerate(row)},
            units={f"cell_temperature:{i}": "K" for i in range(len(row))},
            valid={f"cell_temperature:{i}": True for i in range(len(row))},
            origin="simulation",
        )


def thermal_rod_frames(archive, run_id, *, reader=None):
    """Reuse the owned reader; excess temperatures remain differences and controller counts remain counts."""
    archive = Path(archive)
    asset = next(row for row in catalog() if row.id == "thermal_rod").assets[0]
    source_sha = file_digest(archive)
    if source_sha != asset.checksum.split(":", 1)[1] or archive.stat().st_size != asset.bytes:
        raise ValueError("thermal-rod laboratory origin requires the reviewed source archive")
    if reader is None:
        from matintegrity.thermal_data import read_thermal_rod_series as reader
    data = reader(archive, run_id)
    expected = ["time_s", *(f"tc{i}_excess_temperature_K" for i in range(1, 7)), "controller_setting_counts"]
    if data.columns.tolist() != expected or data.empty:
        raise ValueError("native thermal-rod columns changed")
    if data.attrs.get("source_archive_sha256") != source_sha or data.attrs.get("run_id") != run_id:
        raise ValueError("native thermal-rod source identity changed")
    import hashlib

    with zipfile.ZipFile(archive) as source:
        member = data.attrs["source_member"]
        if hashlib.sha256(source.read(member)).hexdigest() != data.attrs["source_member_sha256"]:
            raise ValueError("native thermal-rod member receipt changed")
    clock = "thermal_rod:" + run_id + ":" + data.attrs["source_member_sha256"]
    channels = expected[1:]
    frames = []
    for row in data.itertuples(index=False, name=None):
        if frames and row[0] <= frames[-1].time_s:
            raise ValueError("source thermal-rod clock is not strictly increasing")
        frames.append(
            SignalFrame(
                stream_id="thermal_rod:" + run_id,
                clock_id=clock,
                time_s=float(row[0]),
                values={k: float(v) for k, v in zip(channels, row[1:], strict=True)},
                units={k: "count" if k == "controller_setting_counts" else "K" for k in channels},
                valid={k: True for k in channels},
                origin="laboratory",
            )
        )
    metadata = dict(
        source=dict(data.attrs),
        clock_id=clock,
        temperature_basis="excess above source ambient",
        absolute_temperature_or_heat_input_inferred=False,
        source_rows_preserved=True,
    )
    return tuple(frames), metadata
