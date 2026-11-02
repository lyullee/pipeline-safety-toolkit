"""Pressure-test loads, signed gas inventories and source-preserving laboratory histories."""

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .adapters import _aligned_frames
from .contracts import Contract, Name, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import catalog, file_digest
from .material_exchange import evaluate_material_job, prepare_material_job
from .thermo import CheckedProvider, CoolPropProvider, StateRequest
from .units import convert_quantity

R_NATIVE = 8.31446261815324
REVIEWS = {
    "inspection.pressure_test_load": ("gauge_design_and_test_rule", "allowables_and_elevation_basis"),
    "inspection.gas_expansion_energy": (
        "absolute_pressures_and_initial_inventory",
        "ideal_reversible_adiabatic_constant_gamma",
    ),
    "inspection.gas_pressure_decay": (
        "rigid_isolated_equilibrated_fixed_composition",
        "absolute_state_and_reference_conditions",
    ),
    "inspection.vacuum_rise": ("rigid_isolated_isothermal", "background_and_pressure_basis"),
}


class GasExpansionInputParameters(Contract):
    """Recognized ideal-gas expansion inputs; unknown values remain explicit.

    This validates SI coordinates only. The original material owner evaluates
    expansion applicability and work; no calculation is duplicated here.
    """
    initial_absolute_pressure_Pa: float | None = Field(default=None,gt=0)
    final_absolute_pressure_Pa: float | None = Field(default=None,gt=0)
    initial_volume_m3: float | None = Field(default=None,gt=0)
    heat_capacity_ratio: float | None = Field(default=None,gt=1)


class PressureTestRequest(Contract):
    method: Literal[
        "inspection.pressure_test_load",
        "inspection.gas_expansion_energy",
        "inspection.gas_pressure_decay",
        "inspection.vacuum_rise",
    ]
    asset_id: Name
    location_id: Name
    clock_id: Name
    parameters: dict
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @field_validator('parameters')
    @classmethod
    def recognized_parameter_inputs(cls,value,info):
        if info.data.get('method')=='inspection.gas_expansion_energy':
            GasExpansionInputParameters.model_validate(value)
        return value

    @model_validator(mode="after")
    def meanings(self):
        json_value(self.parameters)
        if set(self.applicability_reviews) - set(REVIEWS[self.method]):
            raise ValueError("pressure-test reviews must belong to the selected method")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("pressure-test evidence must be unique")
        return self


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _context(asset_id, clock_id, evidence_ids, context):
    if (asset_id, clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("pressure-test asset/clock and context differ")
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)) or set(evidence_ids) - set(ids):
        raise ValueError("pressure-test evidence must be unique and present")


def evaluate_pressure_test(request: PressureTestRequest, context: RunContext, *, client=None):
    request = PressureTestRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    _context(request.asset_id, request.clock_id, request.evidence_ids, context)
    body = dict(
        schema="pipeline_integrity.pressure_test.v1",
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status="needs_data",
        values={},
        output_units={},
        native_receipt=None,
        issues=[],
        output_origin="simulation",
        inspection_acceptance_or_strength_qualification=False,
        net_inventory_identifies_leak_path=False,
        blast_or_consequence_energy_inferred=False,
        pressure_basis="gauge_design_and_local_test_load"
        if request.method == "inspection.pressure_test_load"
        else "absolute_gas_state",
        implementation_sha256=file_digest(Path(__file__)),
    )
    if any(not request.applicability_reviews.get(k, False) for k in REVIEWS[request.method]):
        body["issues"] = ["method-specific pressure-test applicability reviews are required"]
        return _seal(body)
    if any(v is None for v in request.parameters.values()):
        body["issues"] = ["unresolved native input; no default pressure, volume or rule is assumed"]
        return _seal(body)
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=client,
    )
    receipt = evaluate_material_job(job, context, client=client)
    receipt.check_integrity()
    p = receipt.native.payload
    body.update(
        status=p["status"],
        values=p["values"],
        output_units=p["units"],
        native_receipt=receipt.model_dump(mode="json"),
        issues=list(p["notes"]),
    )
    return _seal(body)


class MolarGasState(Contract):
    fluid: Name
    backend: Name
    pressure_pa_abs: Positive
    temperature_k: Positive
    molar_density_mol_m3: Positive
    phase: Name
    provider: dict


class CoolPropMolarProvider:
    """Reuse the checked common property service; add its molar-density query for inventory exchange."""

    def resolve(self, request: StateRequest):
        state = CheckedProvider(CoolPropProvider()).resolve(request)
        from CoolProp.CoolProp import PropsSI

        density = float(
            PropsSI(
                "Dmolar",
                "P",
                state.pressure_pa_abs,
                "T",
                state.temperature_k,
                request.backend + "::" + request.fluid,
            )
        )
        return MolarGasState(
            fluid=state.fluid,
            backend=state.backend,
            pressure_pa_abs=state.pressure_pa_abs,
            temperature_k=state.temperature_k,
            molar_density_mol_m3=density,
            phase=state.phase,
            provider=dict(id="CoolProp", version=state.provider_version, revision=state.provider_revision),
        )


class GasDecaySpec(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    pressure_channel: Name
    temperature_channel: Name
    pressure_reference: Literal["absolute", "gauge"]
    ambient_pa_abs: Positive | None = None
    rigid_volume_m3: Positive | None
    reference_pressure_pa_abs: Positive | None
    reference_temperature_k: Positive | None
    compressibility_source: Literal["declared_constant", "provider"]
    compressibility_factor: Positive | None = None
    reference_compressibility_factor: Positive | None = None
    fluid: Name | None = None
    backend: Literal["HEOS", "PR", "SRK"] = "HEOS"
    maximum_interval_s: Positive
    gas_temperature_representative_reviewed: bool = False
    isolated_rigid_fixed_composition_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def basis(self):
        if self.pressure_reference == "absolute" and self.ambient_pa_abs is not None:
            raise ValueError("ambient pressure belongs to gauge conversion only")
        if self.compressibility_source == "provider" and any(
            x is not None for x in (self.compressibility_factor, self.reference_compressibility_factor)
        ):
            raise ValueError("provider states and declared compressibility factors cannot both be supplied")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("gas-history evidence must be unique")
        return self


def evaluate_gas_decay_series(
    frames, specification: GasDecaySpec, context: RunContext, *, client=None, provider=None
):
    spec = GasDecaySpec.model_validate_json(specification.model_dump_json())
    if provider is not None and spec.compressibility_source != "provider":
        raise ValueError("an injected gas provider requires the provider compressibility mode")
    context = RunContext.model_validate_json(context.model_dump_json())
    _context(spec.asset_id, spec.clock_id, spec.evidence_ids, context)
    frames = tuple(SignalFrame.model_validate_json(f.model_dump_json()) for f in frames)
    aligned, channels = _aligned_frames(frames)
    if (
        len(aligned) < 2
        or aligned[0].clock_id != spec.clock_id
        or {spec.pressure_channel, spec.temperature_channel} - set(channels)
    ):
        raise ValueError(
            "gas history requires matching pressure/temperature channels, clock and at least two samples"
        )
    body = dict(
        schema="pipeline_integrity.gas_decay_series.v1",
        specification=spec.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        input_digest=digest(frames),
        status="needs_data",
        issues=[],
        intervals=[],
        endpoint_result=None,
        states=[],
        values={},
        invalid_sample_indices=[],
        uncovered_intervals=[],
        output_origin="simulation",
        net_inventory_identifies_leak_path=False,
        inspection_acceptance_or_field_validation=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    invalid = [
        i
        for i, f in enumerate(aligned)
        if any(
            f.values[k] is None or not f.valid[k] for k in (spec.pressure_channel, spec.temperature_channel)
        )
    ]
    gaps = [
        i
        for i, (a, b) in enumerate(zip(aligned, aligned[1:], strict=False))
        if b.time_s - a.time_s > spec.maximum_interval_s
    ]
    body.update(invalid_sample_indices=invalid, uncovered_intervals=gaps)
    if invalid or gaps:
        body["issues"] = ["missing/invalid samples or uncovered intervals cannot be dropped or filled"]
        return _seal(body)
    if not spec.gas_temperature_representative_reviewed or not spec.isolated_rigid_fixed_composition_reviewed:
        body["issues"] = [
            "representative gas temperature and isolated rigid fixed-composition volume require review"
        ]
        return _seal(body)
    if any(
        x is None
        for x in (spec.rigid_volume_m3, spec.reference_pressure_pa_abs, spec.reference_temperature_k)
    ) or (spec.pressure_reference == "gauge" and spec.ambient_pa_abs is None):
        body["issues"] = ["volume, absolute/reference conditions or measured gauge ambient are unresolved"]
        return _seal(body)
    if (
        spec.compressibility_source == "provider"
        and spec.fluid is None
        or spec.compressibility_source == "declared_constant"
        and any(x is None for x in (spec.compressibility_factor, spec.reference_compressibility_factor))
    ):
        body["issues"] = ["gas composition or declared compressibility/reference factors are unresolved"]
        return _seal(body)
    pressures = [
        convert_quantity(f.values[spec.pressure_channel], f.units[spec.pressure_channel], "Pa")
        + (spec.ambient_pa_abs if spec.pressure_reference == "gauge" else 0.0)
        for f in aligned
    ]
    temperatures = [
        convert_quantity(f.values[spec.temperature_channel], f.units[spec.temperature_channel], "K")
        for f in aligned
    ]
    if min(pressures) <= 0 or min(temperatures) <= 0:
        raise ValueError("gas inventory requires positive absolute pressure and temperature")
    if spec.compressibility_source == "provider":
        provider = CoolPropMolarProvider() if provider is None else provider
        states = []
        for p, t in [
            *zip(pressures, temperatures, strict=True),
            (spec.reference_pressure_pa_abs, spec.reference_temperature_k),
        ]:
            state = provider.resolve(
                StateRequest(fluid=spec.fluid, backend=spec.backend, inputs=dict(P=p, T=t))
            )
            state = MolarGasState.model_validate_json(state.model_dump_json())
            if (
                (state.fluid, state.backend) != (spec.fluid, spec.backend)
                or not math.isclose(state.pressure_pa_abs, p, rel_tol=1e-9, abs_tol=1e-8)
                or not math.isclose(state.temperature_k, t, rel_tol=1e-9, abs_tol=1e-8)
            ):
                raise ValueError("molar provider changed the requested gas/backend/state")
            if state.phase not in ("gas", "supercritical_gas", "supercritical"):
                body["states"] = [s.model_dump(mode="json") for s in [*states, state]]
                body.update(
                    status="out_of_scope", issues=["gas inventory property provider returned a non-gas phase"]
                )
                return _seal(body)
            states.append(state)
        # Match the native engine's R convention exactly, including a backend using a different fitted gas constant.
        z = [s.pressure_pa_abs / (s.molar_density_mol_m3 * R_NATIVE * s.temperature_k) for s in states]
        body["states"] = [
            s.model_dump(mode="json")
            | dict(compressibility_for_native_R=v, native_gas_constant_J_mol_K=R_NATIVE)
            for s, v in zip(states, z, strict=True)
        ]
        z, zref = z[:-1], z[-1]
    else:
        z, zref = [spec.compressibility_factor] * len(aligned), spec.reference_compressibility_factor

    def job(i, j):
        params = dict(
            rigid_volume_m3=spec.rigid_volume_m3,
            initial_absolute_pressure_Pa=pressures[i],
            final_absolute_pressure_Pa=pressures[j],
            initial_temperature_K=temperatures[i],
            final_temperature_K=temperatures[j],
            initial_compressibility_factor=z[i],
            final_compressibility_factor=z[j],
            duration_s=aligned[j].time_s - aligned[i].time_s,
            reference_pressure_Pa=spec.reference_pressure_pa_abs,
            reference_temperature_K=spec.reference_temperature_k,
            reference_compressibility_factor=zref,
        )
        return evaluate_pressure_test(
            PressureTestRequest(
                method="inspection.gas_pressure_decay",
                asset_id=spec.asset_id,
                location_id=spec.location_id,
                clock_id=spec.clock_id,
                parameters=params,
                applicability_reviews=dict.fromkeys(REVIEWS["inspection.gas_pressure_decay"], True),
                evidence_ids=spec.evidence_ids,
            ),
            context,
            client=client,
        )

    intervals = [job(i, i + 1) for i in range(len(aligned) - 1)]
    endpoint = job(0, len(aligned) - 1)
    body.update(intervals=intervals, endpoint_result=endpoint)
    if any(r["status"] != "computed" for r in [*intervals, endpoint]):
        body["issues"] = ["one or more original inventory calculations are withheld"]
        return _seal(body)
    total = math.fsum(
        r["values"]["net_molar_outflow_mol_s"] * (b.time_s - a.time_s)
        for r, a, b in zip(intervals, aligned[:-1], aligned[1:], strict=True)
    )
    duration = aligned[-1].time_s - aligned[0].time_s
    body.update(
        status="computed",
        values=dict(
            total_net_moles_out=total,
            mean_net_molar_outflow_mol_s=endpoint["values"]["net_molar_outflow_mol_s"],
            reference_volume_outflow_m3_s=endpoint["values"]["reference_volume_outflow_m3_s"],
            interval_endpoint_mole_residual=total - endpoint["values"]["net_molar_outflow_mol_s"] * duration,
        ),
    )
    return _seal(body)


def load_pe100_pressure_decay(workbook):
    """Read the checked ten capillary profiles; never manufacture metadata-only reference/ambient channels."""
    path = Path(workbook)
    source = next(d for d in catalog() if d.id == "pe100_pressure_decay")
    asset = source.assets[0]
    algorithm, expected = asset.checksum.split(":", 1)
    if file_digest(path, algorithm) != expected:
        raise ValueError("pressure workbook differs from pinned supplier checksum")
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if book.sheetnames != ["Hárok1"]:
            raise ValueError("pressure workbook sheet layout changed")
        sheet = book.worksheets[0]
        rows = list(sheet.values)
        if (
            len(rows) != 34
            or rows[2][0] != "Time t [s]"
            or any(any(v is not None for v in row[21:]) for row in rows)
        ):
            raise ValueError("pressure workbook rows/columns changed")
        records = []
        for group, (label, fluid) in enumerate(
            (
                ("Nitorgen N2", "Nitrogen"),
                ("Air", "Air"),
                ("Argon Ar", "Argon"),
                ("Carbon dioxide CO2", "CarbonDioxide"),
                ("Propane-butane PB", None),
            )
        ):
            start = 1 + 4 * group
            if rows[0][start] != label:
                raise ValueError("gas label or column grouping changed")
            for offset, length in ((0, 100.0), (2, 20.0)):
                column = start + offset
                if (
                    rows[1][column] != f"{int(length)} mm"
                    or rows[2][column] != "Temperatures T [°C]"
                    or rows[2][column + 1] != "Pressure [Pa]"
                ):
                    raise ValueError("pressure/temperature/configuration headers changed")
                samples = []
                for index, row in enumerate(rows[3:], start=4):
                    values = [row[0], row[column], row[column + 1]]
                    if any(
                        v is not None
                        and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v))
                        for v in values
                    ):
                        raise ValueError("unexpected pressure-history cell type")
                    if values[0] is None:
                        raise ValueError("source history time is missing")
                    samples.append(
                        dict(
                            source_excel_row=index,
                            time_s=float(values[0]),
                            reported_temperature_k=None if values[1] is None else float(values[1]) + 273.15,
                            gauge_pressure_pa=None if values[2] is None else float(values[2]),
                        )
                    )
                if any(b["time_s"] <= a["time_s"] for a, b in zip(samples, samples[1:], strict=False)):
                    raise ValueError("source pressure-test clock repeats or decreases")
                records.append(
                    dict(
                        id=f"gas_{group}_capillary_{int(length)}mm",
                        source_gas_label=label,
                        fluid=fluid,
                        fluid_names_route_property_models_not_measured_composition=True,
                        capillary_length_mm=length,
                        samples=samples,
                        temperature_measurement_role="unresolved_reported_T",
                        ambient_pressure_pa_abs=None,
                        rigid_volume_m3=None,
                        no_defect_reference_present=False,
                    )
                )
    finally:
        book.close()
    return _seal(
        dict(
            schema="pipeline_integrity.pe100_pressure_source.v1",
            dataset_id=source.id,
            citation=source.citation,
            source=source.source,
            license_id=source.license_id,
            source_sha256=file_digest(path),
            supplier_checksum=asset.checksum,
            records=records,
            source_metadata_reference_or_ambient_channels_manufactured=False,
            implementation_sha256=file_digest(Path(__file__)),
        )
    )
