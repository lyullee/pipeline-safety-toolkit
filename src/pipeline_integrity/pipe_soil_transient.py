"""Pipe/soil concentric-shell histories through the existing material heat solver.

The radial geometry is explicit. A cylindrical outer boundary is not a ground
surface plane, and a prescribed fluid temperature is not a coupled fluid model.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .material_exchange import MaterialReceipt, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard, verify_material_source_pin

MODEL = "thermal.transient_conduction_1d"
SCHEMA = "pipeline_integrity.pipe_soil_transient.v1"
REVIEWS = (
    "concentric_shell_geometry_and_axial_uniformity",
    "initial_cell_averages_and_material_support",
    "linear_boundary_history_and_clock_mapping",
    "perfect_contacts_and_absent_moisture_transport",
    "phase_change_absent_or_represented_in_supplied_capacity",
)
UNITS = {
    "length": "m",
    "time": "s",
    "temperature": "K",
    "conductivity": "W/(m*K)",
    "volumetric_heat_capacity": "J/(m3*K)",
    "heat_flux_into_solid": "W/m2",
    "film_coefficient": "W/(m2*K)",
}
OUTPUT_UNITS = {
    "cell_centers_m": "m",
    "temperature_history_K": "K",
    "boundary_surface_temperature_K": "K",
    "boundary_heat_rate_into_solid_W": "W",
    "distributed_heat_rate_into_solid_W": "W",
    "volumetric_generation_rate_W": "W",
    "cumulative_boundary_energy_into_solid_J": "J",
    "cumulative_distributed_energy_into_solid_J": "J",
    "cumulative_generated_energy_J": "J",
    "maximum_absolute_discrete_energy_balance_error_J": "J",
    "maximum_relative_discrete_energy_balance_error": "1",
    "internal_steps": "1",
    "maximum_nonlinear_iterations_used": "1",
}


class PipeSoilMaterial(Contract):
    id: Name
    temperature_K: tuple[Positive, ...] = Field(min_length=2, max_length=200)
    conductivity_W_m_K: tuple[Positive | None, ...] = Field(min_length=2, max_length=200)
    volumetric_heat_capacity_J_m3_K: tuple[Positive | None, ...] = Field(min_length=2, max_length=200)
    evidence_id: Name

    @model_validator(mode="after")
    def aligned(self):
        if len(self.temperature_K) != len(self.conductivity_W_m_K) or len(self.temperature_K) != len(
            self.volumetric_heat_capacity_J_m3_K
        ):
            raise ValueError("thermal property knots must align")
        if any(b <= a for a, b in zip(self.temperature_K, self.temperature_K[1:], strict=False)):
            raise ValueError("property temperatures must strictly increase")
        return self


class PipeSoilShell(Contract):
    id: Name
    role: Literal["pipe_wall", "insulation", "soil"]
    material_id: Name
    radial_edges_m: tuple[Positive, ...] = Field(min_length=2, max_length=501)
    initial_cell_temperature_K: tuple[Positive | None, ...] = Field(min_length=1, max_length=500)
    evidence_id: Name

    @model_validator(mode="after")
    def aligned(self):
        if len(self.radial_edges_m) != len(self.initial_cell_temperature_K) + 1 or any(
            b <= a for a, b in zip(self.radial_edges_m, self.radial_edges_m[1:], strict=False)
        ):
            raise ValueError("shell edges must strictly increase and enclose each initial cell average")
        return self


class PipeSoilBoundary(Contract):
    id: Name
    kind: Literal[
        "unresolved", "adiabatic", "prescribed_temperature", "prescribed_heat_flux", "convection_radiation"
    ]
    temperature_K: tuple[Positive | None, ...] | None = None
    heat_flux_into_solid_W_m2: tuple[float | None, ...] | None = None
    convection_W_m2_K: tuple[Nonnegative | None, ...] | None = None
    ambient_temperature_K: tuple[Positive | None, ...] | None = None
    emissivity: Fraction | None = None
    radiative_environment_temperature_K: tuple[Positive | None, ...] | None = None
    evidence_id: Name


def _required_boundary_fields(kind):
    return {
        "unresolved": (),
        "adiabatic": (),
        "prescribed_temperature": ("temperature_K",),
        "prescribed_heat_flux": ("heat_flux_into_solid_W_m2",),
        "convection_radiation": (
            "convection_W_m2_K",
            "ambient_temperature_K",
            "emissivity",
            "radiative_environment_temperature_K",
        ),
    }[kind]


class PipeSoilTransientRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    event_id: Name
    geometry: Literal["concentric_cylindrical_shells"] = "concentric_cylindrical_shells"
    cylinder_length_m: Positive
    materials: tuple[PipeSoilMaterial, ...] = Field(min_length=2, max_length=100)
    shells: tuple[PipeSoilShell, ...] = Field(min_length=2, max_length=100)
    time_s: tuple[Nonnegative, ...] = Field(min_length=2, max_length=5000)
    clock_offset_s: Nonnegative
    temporal_interpolation: Literal["linear_between_declared_knots"] = "linear_between_declared_knots"
    inner_boundary: PipeSoilBoundary
    outer_boundary: PipeSoilBoundary
    perfect_contacts: bool | None
    maximum_step_s: Positive
    maximum_nonlinear_iterations: int = Field(default=80, ge=2, le=500)
    nonlinear_absolute_tolerance_K: Positive = 1e-8
    nonlinear_relative_tolerance: Positive = 1e-10
    maximum_retained_states: int = Field(default=200000, ge=4, le=200000)
    maximum_steps: int = Field(default=10000, ge=1, le=200000)
    maximum_work_units: int = Field(default=20000000, ge=1, le=100000000)
    maximum_relative_energy_balance_error: Positive = 1e-7
    audit_absolute_tolerance_J: Positive = 1e-5
    audit_relative_tolerance: Positive = 1e-7
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_model_version: Name | None = None
    expected_input_schema_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    input_units: dict[str, str]

    @model_validator(mode="after")
    def layout(self):
        if self.input_units != UNITS:
            raise ValueError("pipe/soil histories require explicit SI and heat sign units")
        if self.time_s[0] != 0 or any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("local time must start at zero and strictly increase without sorting")
        for seq in (self.evidence_ids, tuple(m.id for m in self.materials), tuple(s.id for s in self.shells)):
            if len(seq) != len(set(seq)):
                raise ValueError("evidence, materials and shell identifiers must be distinct")
        if self.shells[0].role != "pipe_wall" or self.shells[-1].role != "soil":
            raise ValueError("the radial assembly must start at a pipe wall and end in soil")
        material_ids = {m.id for m in self.materials}
        seen_soil = False
        for i, shell in enumerate(self.shells):
            if shell.material_id not in material_ids:
                raise ValueError("shell material is not declared")
            if i and self.shells[i - 1].radial_edges_m[-1] != shell.radial_edges_m[0]:
                raise ValueError("adjacent shell edges must coincide exactly; no contact/gap repair")
            seen_soil |= shell.role == "soil"
            if seen_soil and shell.role != "soil":
                raise ValueError("pipe or insulation cannot lie outside the soil shells")
        if self.inner_boundary.id == self.outer_boundary.id:
            raise ValueError("inner and outer boundary identifiers must differ")
        boundary_fields = set(PipeSoilBoundary.model_fields) - {"id", "kind", "evidence_id"}
        for boundary in (self.inner_boundary, self.outer_boundary):
            required = set(_required_boundary_fields(boundary.kind))
            for field in boundary_fields:
                value = getattr(boundary, field)
                if field not in required and value is not None:
                    raise ValueError("inactive boundary fields cannot carry values")
                if isinstance(value, tuple) and len(value) != len(self.time_s):
                    raise ValueError("boundary series must align with the exact declared time knots")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("unknown radial pipe/soil review key")
        _budget(self)
        return self


def _budget(request):
    cells = sum(len(s.initial_cell_temperature_K) for s in request.shells)
    steps = sum(
        math.ceil((b - a) / request.maximum_step_s)
        for a, b in zip(request.time_s, request.time_s[1:], strict=False)
    )
    if cells > 500 or cells < 2 or cells * len(request.time_s) > request.maximum_retained_states:
        raise ValueError("cell/history budget exceeded before owner import")
    if (
        steps > request.maximum_steps
        or steps * cells * request.maximum_nonlinear_iterations > request.maximum_work_units
    ):
        raise ValueError("implicit step/work budget exceeded before owner import; no solver controls changed")


def pipe_soil_boundary(request):
    return dict(
        asset_id=request.asset_id,
        location_id=request.location_id,
        event_id=request.event_id,
        clock_id=request.clock_id,
        clock_offset_s=request.clock_offset_s,
        temporal_interpolation=request.temporal_interpolation,
        geometry=request.geometry,
        heat_sign="positive_into_solid_at_both_boundaries",
    )


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("pipe/soil asset or clock differs from RunContext")
    if context.metadata.get("pipe_soil_boundary") != pipe_soil_boundary(request):
        raise ValueError(
            "RunContext.metadata.pipe_soil_boundary must bind location/event/clock/geometry/heat sign"
        )
    selected = set(request.evidence_ids)
    evidence = {e.id: e for e in context.evidence}
    references = (
        {m.evidence_id for m in request.materials}
        | {s.evidence_id for s in request.shells}
        | {
            request.inner_boundary.evidence_id,
            request.outer_boundary.evidence_id,
            *request.review_evidence_ids.values(),
        }
    )
    if len(evidence) != len(context.evidence) or selected - set(evidence) or references - selected:
        raise ValueError("material, geometry, boundary and review evidence must be selected and present")
    issues = [
        f"review/evidence required: {k}"
        for k in REVIEWS
        if request.applicability_reviews.get(k) is not True or k not in request.review_evidence_ids
    ]
    if request.owner_source_tree_sha256 is None:
        issues.append("reviewed original material source-tree pin required")
    if request.expected_model_version is None or request.expected_input_schema_digest is None:
        issues.append("reviewed original material model version and schema pin required")
    if request.perfect_contacts is None:
        issues.append("inter-layer contact condition is unknown")
    for m in request.materials:
        if any(v is None for v in (*m.conductivity_W_m_K, *m.volumetric_heat_capacity_J_m3_K)):
            issues.append(f"thermal properties unresolved: {m.id}")
    for shell in request.shells:
        if any(t is None for t in shell.initial_cell_temperature_K):
            issues.append(f"initial cell averages unresolved: {shell.id}")
    for b in (request.inner_boundary, request.outer_boundary):
        if b.kind == "unresolved" or any(
            getattr(b, k) is None or (isinstance(getattr(b, k), tuple) and None in getattr(b, k))
            for k in _required_boundary_fields(b.kind)
        ):
            issues.append(f"boundary history unresolved: {b.id}")
    outside = []
    if request.perfect_contacts is False:
        outside.append(
            "finite thermal contact resistance requires an extended owner boundary; no zero-contact substitution"
        )
    materials = {m.id: m for m in request.materials}
    for shell in request.shells:
        bounds = materials[shell.material_id].temperature_K
        if any(t is not None and not bounds[0] <= t <= bounds[-1] for t in shell.initial_cell_temperature_K):
            outside.append(f"initial temperature outside material support: {shell.id}")
    return issues + outside, "out_of_scope" if outside else "needs_data"


def _parameters(request):
    def boundary(b):
        return {
            "boundary_id": b.id,
            "kind": b.kind,
            **{
                k: list(getattr(b, k)) if isinstance(getattr(b, k), tuple) else getattr(b, k)
                for k in set(PipeSoilBoundary.model_fields) - {"id", "kind", "evidence_id"}
            },
        }

    return dict(
        geometry="cylindrical",
        schema_version="matintegrity.transient_conduction_1d.v1",
        planar_area_m2=None,
        cylinder_length_m=request.cylinder_length_m,
        cell_edges_m=[request.shells[0].radial_edges_m[0]]
        + [r for s in request.shells for r in s.radial_edges_m[1:]],
        materials=[
            dict(
                material_id=m.id,
                material_condition_id=None,
                temperature_K=list(m.temperature_K),
                conductivity_W_m_K=list(m.conductivity_W_m_K),
                volumetric_heat_capacity_J_m3_K=list(m.volumetric_heat_capacity_J_m3_K),
                property_provider_id=None,
                property_provider_revision=None,
                property_record_id=None,
                property_source_sha256=None,
                property_evidence=[],
            )
            for m in request.materials
        ],
        cell_material_ids=[s.material_id for s in request.shells for _ in s.initial_cell_temperature_K],
        initial_temperature_K=[t for s in request.shells for t in s.initial_cell_temperature_K],
        time_s=list(request.time_s),
        left_boundary=boundary(request.inner_boundary),
        right_boundary=boundary(request.outer_boundary),
        maximum_step_s=request.maximum_step_s,
        maximum_nonlinear_iterations=request.maximum_nonlinear_iterations,
        nonlinear_absolute_tolerance_K=request.nonlinear_absolute_tolerance_K,
        nonlinear_relative_tolerance=request.nonlinear_relative_tolerance,
        volumetric_heat_generation_W_m3=None,
        distributed_exchange=None,
        maximum_relative_energy_balance_error=request.maximum_relative_energy_balance_error,
        one_dimensional_geometry_reviewed=True,
        material_property_support_reviewed=True,
        boundary_conditions_reviewed=True,
        phase_change_absent_or_included_in_capacity_reviewed=True,
    )


def _energy_density_change(material, initial, final):
    """Independent integral of the supplied capacity curve for an energy audit."""
    low, high = sorted((initial, final))
    if not material.temperature_K[0] <= low <= high <= material.temperature_K[-1]:
        raise ValueError("returned solid temperature exceeds supplied property support")
    knots = [low, *[t for t in material.temperature_K if low < t < high], high]
    capacities = np.interp(knots, material.temperature_K, material.volumetric_heat_capacity_J_m3_K)
    area = sum(
        (b - a) * (ca + cb) / 2
        for a, b, ca, cb in zip(knots, knots[1:], capacities, capacities[1:], strict=False)
    )
    return float(area if final >= initial else -area)


def _audit(request, receipt):
    native = receipt.native.payload
    values = native["values"]
    if not values:
        if native["status"] == "computed":
            raise ValueError("a completed thermal calculation must return its requested history")
        return {"available": False, "reason": "owner returned no completed thermal history"}
    # Newer matintegrity releases attach material-property provenance alongside
    # the dimensional output quantities. It is metadata, so it has no unit in
    # OUTPUT_UNITS; accept it explicitly while still rejecting unknown fields.
    optional_metadata_fields = {"material_property_lineage"}
    if not set(OUTPUT_UNITS) <= set(values) or set(values) - set(OUTPUT_UNITS) - optional_metadata_fields:
        raise ValueError("native thermal output fields differ from the pinned contract")
    if "material_property_lineage" in values:
        lineage = values["material_property_lineage"]
        if not isinstance(lineage, list) or len(lineage) != len(request.materials):
            raise ValueError("native thermal property lineage differs from the declared material set")
        for row, material in zip(lineage, request.materials, strict=True):
            if not isinstance(row, dict) or any(
                row.get(key) != expected
                for key, expected in (
                    ("material_id", material.id),
                    ("temperature_K", list(material.temperature_K)),
                    ("conductivity_W_m_K", list(material.conductivity_W_m_K)),
                    ("volumetric_heat_capacity_J_m3_K", list(material.volumetric_heat_capacity_J_m3_K)),
                )
            ):
                raise ValueError("native thermal property lineage contradicts the supplied material table")
            property_hash = row.get("property_table_sha256")
            if not isinstance(property_hash, str) or len(property_hash) != 64 or any(
                char not in "0123456789abcdef" for char in property_hash
            ):
                raise ValueError("native thermal property lineage lacks its table checksum")

    def finite(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("native thermal quantities must be finite numbers; unknown is not zero")

    for key in (
        "maximum_absolute_discrete_energy_balance_error_J",
        "maximum_relative_discrete_energy_balance_error",
    ):
        finite(values[key])
        if values[key] < 0:
            raise ValueError("reported solver energy errors must be nonnegative")
    if (
        native["status"] == "computed"
        and values["maximum_relative_discrete_energy_balance_error"]
        > request.maximum_relative_energy_balance_error
    ):
        raise ValueError("native computed status contradicts its reported relative energy error")
    for key, maximum in (
        ("internal_steps", request.maximum_steps),
        ("maximum_nonlinear_iterations_used", request.maximum_nonlinear_iterations),
    ):
        if type(values[key]) is not int or not 1 <= values[key] <= maximum:
            raise ValueError("native solver diagnostics exceed declared work/iteration bounds")

    temperatures = values["temperature_history_K"]
    n = sum(len(s.initial_cell_temperature_K) for s in request.shells)
    if len(temperatures) != len(request.time_s) or any(len(row) != n for row in temperatures):
        raise ValueError("owner thermal history dimensions differ from the requested cells/clock")
    if temperatures[0] != _parameters(request)["initial_temperature_K"]:
        raise ValueError("owner changed the initial cell-average state")
    expected_centres = [
        math.sqrt((a * a + b * b) / 2)
        for s in request.shells
        for a, b in zip(s.radial_edges_m, s.radial_edges_m[1:], strict=False)
    ]
    if values["cell_centers_m"] != expected_centres:
        raise ValueError("owner changed the declared radial cell centres")
    for key in ("boundary_surface_temperature_K", "boundary_heat_rate_into_solid_W"):
        if len(values[key]) != len(request.time_s) or any(len(row) != 2 for row in values[key]):
            raise ValueError("owner boundary histories differ from the requested clock/two surfaces")
        for row in values[key]:
            for value in row:
                finite(value)
    for key in ("distributed_heat_rate_into_solid_W", "volumetric_generation_rate_W"):
        if len(values[key]) != len(request.time_s):
            raise ValueError("native distributed/generation rate clock differs")
    if (
        any(v != 0 for v in values["distributed_heat_rate_into_solid_W"])
        or any(v != 0 for v in values["volumetric_generation_rate_W"])
        or values["cumulative_distributed_energy_into_solid_J"] != 0
        or values["cumulative_generated_energy_J"] != 0
    ):
        raise ValueError("unrequested distributed/generation heat cannot appear in pipe/soil coupling")
    materials = {m.id: m for m in request.materials}
    # At each requested knot, verify both the imposed boundary and the observable
    # surface-to-cell conduction relation. Net energy alone cannot detect two
    # inconsistent boundary heat rates that cancel each other.
    for side, (boundary, shell, cell_index, face_radius, cell_radius) in enumerate(
        (
            (
                request.inner_boundary,
                request.shells[0],
                0,
                request.shells[0].radial_edges_m[0],
                expected_centres[0],
            ),
            (
                request.outer_boundary,
                request.shells[-1],
                -1,
                request.shells[-1].radial_edges_m[-1],
                expected_centres[-1],
            ),
        )
    ):
        material = materials[shell.material_id]
        area = 2 * math.pi * face_radius * request.cylinder_length_m
        for i, row in enumerate(temperatures):
            cell_temperature = row[cell_index]
            finite(cell_temperature)
            surface = values["boundary_surface_temperature_K"][i][side]
            heat = values["boundary_heat_rate_into_solid_W"][i][side]
            if surface <= 0:
                raise ValueError("boundary surface temperature must be positive K")
            conductivity = float(
                np.interp(cell_temperature, material.temperature_K, material.conductivity_W_m_K)
            )
            resistance = abs(math.log(cell_radius / face_radius)) / (
                2 * math.pi * request.cylinder_length_m * conductivity
            )
            if not math.isclose(heat, (surface - cell_temperature) / resistance, rel_tol=1e-7, abs_tol=1e-7):
                raise ValueError(
                    "sample boundary heat contradicts surface/cell temperatures and radial geometry"
                )
            if boundary.kind == "adiabatic" and not math.isclose(heat, 0.0, abs_tol=1e-9):
                raise ValueError("adiabatic boundary cannot carry heat")
            if boundary.kind == "prescribed_temperature" and surface != boundary.temperature_K[i]:
                raise ValueError("returned prescribed boundary temperature differs from its knot")
            if boundary.kind == "prescribed_heat_flux" and not math.isclose(
                heat, area * boundary.heat_flux_into_solid_W_m2[i], rel_tol=1e-10, abs_tol=1e-9
            ):
                raise ValueError("prescribed heat flux does not match the true cylindrical face area")
            if boundary.kind == "convection_radiation":
                expected = area * (
                    boundary.convection_W_m2_K[i] * (boundary.ambient_temperature_K[i] - surface)
                    + boundary.emissivity
                    * 5.670374419e-8
                    * (boundary.radiative_environment_temperature_K[i] ** 4 - surface**4)
                )
                if not math.isclose(heat, expected, rel_tol=1e-7, abs_tol=1e-7):
                    raise ValueError("sample boundary heat contradicts declared film/radiation conditions")
    shell_energies = []
    i = 0
    for shell in request.shells:
        energy = 0.0
        material = materials[shell.material_id]
        for a, b in zip(shell.radial_edges_m, shell.radial_edges_m[1:], strict=False):
            for row in temperatures:
                finite(row[i])
                if not material.temperature_K[0] <= row[i] <= material.temperature_K[-1]:
                    raise ValueError("returned temperature exceeds reviewed material support")
            volume = math.pi * request.cylinder_length_m * (b * b - a * a)
            energy += volume * _energy_density_change(material, temperatures[0][i], temperatures[-1][i])
            i += 1
        shell_energies.append(dict(shell_id=shell.id, role=shell.role, energy_change_J=energy))
    boundary_energy = values["cumulative_boundary_energy_into_solid_J"]
    if len(boundary_energy) != 2:
        raise ValueError("two signed native boundary energies required")
    for value in boundary_energy:
        finite(value)
    total = sum(s["energy_change_J"] for s in shell_energies)
    residual = total - sum(boundary_energy)
    scale = max(abs(total), sum(abs(e) for e in boundary_energy))
    return dict(
        available=True,
        shell_energy_changes=shell_energies,
        total_solid_energy_change_J=total,
        inner_boundary_energy_into_solid_J=boundary_energy[0],
        outer_boundary_energy_into_solid_J=boundary_energy[1],
        final_energy_residual_J=residual,
        energy_within_tolerance=abs(residual)
        <= request.audit_absolute_tolerance_J + request.audit_relative_tolerance * scale,
        boundary_sample_consistency_checked=True,
        native_reported_diagnostics_checked=True,
        meaning="native substep-integrated boundary energy versus independently integrated final cell enthalpy; not output-sample trapezoidal heat",
    )


def evaluate_pipe_soil_transient(request, context, *, client=None, owner_source_root=None):
    request = PipeSoilTransientRequest.model_validate_json(
        json.dumps(request.model_dump(mode="python"), allow_nan=False)
        if isinstance(request, PipeSoilTransientRequest)
        else json.dumps(request, allow_nan=False)
    )
    context = RunContext.model_validate_json(
        json.dumps(context.model_dump(mode="python"), allow_nan=False)
        if isinstance(context, RunContext)
        else json.dumps(context, allow_nan=False)
    )
    issues, status = _policy(request, context)
    body = dict(
        schema=SCHEMA,
        request=request.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=status,
        issues=issues,
        native_receipt=None,
        source_pin_report=None,
        audit=None,
        implementation_sha256=file_digest(Path(__file__)),
        qualification=dict(
            ground_surface_plane_modelled=False,
            multiple_pipes_modelled=False,
            moisture_transport_modelled=False,
            fluid_energy_feedback_modelled=False,
            field_or_standard_qualification=False,
        ),
    )
    if not issues:
        if owner_source_root is None:
            raise ValueError("reviewed source root is required for an executed pipe/soil transient")
        with material_source_guard(owner_source_root, request.owner_source_tree_sha256, client) as report:
            job = prepare_material_job(
                MODEL,
                _parameters(request),
                context,
                location_id=request.location_id,
                evidence_ids=request.evidence_ids,
                client=client,
            )
            if (job.model_version, job.input_schema_digest, job.output_units, job.probability_semantics) != (
                request.expected_model_version,
                request.expected_input_schema_digest,
                OUTPUT_UNITS,
                {},
            ):
                raise ValueError(
                    "material heat model version, schema, units or meaning differ from reviewed pins"
                )
            receipt = evaluate_material_job(job, context, client=client)
            audit = _audit(request, receipt)
        body.update(
            native_receipt=receipt.model_dump(mode="json"),
            source_pin_report=report,
            audit=audit,
            status=receipt.native.status,
            issues=list(receipt.native.payload["notes"]),
        )
        if audit.get("available") and not audit["energy_within_tolerance"]:
            body.update(
                status="out_of_scope",
                issues=body["issues"] + ["independent final solid/boundary energy audit failed"],
            )
    body["record_digest"] = digest(body)
    return body


def verify_pipe_soil_transient_record(record):
    if record.get("schema") != SCHEMA or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("pipe/soil record schema or integrity changed")
    if (
        record.get("implementation_sha256") != file_digest(Path(__file__))
        or any(v is not False for v in record["qualification"].values())
        or set(record["qualification"])
        != {
            "ground_surface_plane_modelled",
            "multiple_pipes_modelled",
            "moisture_transport_modelled",
            "fluid_energy_feedback_modelled",
            "field_or_standard_qualification",
        }
    ):
        raise ValueError("pipe/soil implementation or model scope changed")
    request = PipeSoilTransientRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    issues, status = _policy(request, context)
    native = record.get("native_receipt")
    if native is None:
        verify_material_source_pin(
            record["source_pin_report"], request.owner_source_tree_sha256, executed=False
        )
        if not issues or (record["issues"], record["status"], record["audit"]) != (issues, status, None):
            raise ValueError("unexecuted pipe/soil request claims unsupported output")
        return
    if issues:
        raise ValueError("unreviewed pipe/soil request claims execution")
    receipt = MaterialReceipt.model_validate_json(json.dumps(native, allow_nan=False))
    receipt.check_integrity()
    job = receipt.job
    if (
        job.model_id,
        job.asset_id,
        job.location_id,
        job.clock_id,
        job.parameters,
        job.evidence_ids,
        receipt.context,
    ) != (
        MODEL,
        request.asset_id,
        request.location_id,
        request.clock_id,
        _parameters(request),
        request.evidence_ids,
        context,
    ):
        raise ValueError("native job differs from the assembled pipe/soil geometry or boundary")
    payload = receipt.native.payload
    if digest(payload["inputs"]) != digest(job.parameters):
        raise ValueError("native inputs differ from the assembled job")
    if (job.model_version, job.input_schema_digest, job.output_units, job.probability_semantics) != (
        request.expected_model_version,
        request.expected_input_schema_digest,
        OUTPUT_UNITS,
        {},
    ):
        raise ValueError("native thermal job version/schema/units/meaning changed")
    native_context = payload["context"]
    if (
        receipt.native.owner,
        receipt.native.owner_schema,
        receipt.native.asset_id,
        payload["schema_version"],
        payload["model_id"],
        payload["model_version"],
        payload["units"],
        payload["probability_semantics"],
        payload["status"],
    ) != (
        "matintegrity",
        "matintegrity.assessment.v1",
        request.asset_id,
        "matintegrity.assessment.v1",
        MODEL,
        request.expected_model_version,
        OUTPUT_UNITS,
        {},
        receipt.native.status,
    ):
        raise ValueError("native thermal identity, status or units changed")
    if (
        native_context["asset_id"],
        native_context["location_id"],
        native_context["time_basis"],
        native_context["metadata"].get("pipeline_context_digest"),
        native_context["metadata"].get("pipeline_scenario_id"),
        native_context["metadata"].get("pipeline_origin"),
        native_context["metadata"].get("pipeline_input_validation_mode"),
    ) != (
        request.asset_id,
        request.location_id,
        request.clock_id,
        digest(context),
        context.scenario_id,
        context.origin,
        "json",
    ):
        raise ValueError("native thermal context or evidence mapping changed")
    if payload["input_digest"] != digest(
        {k: payload[k] for k in ("model_id", "model_version", "inputs", "context")}
    ):
        raise ValueError("native thermal input digest differs")
    verify_material_source_pin(record["source_pin_report"], request.owner_source_tree_sha256)
    audit = _audit(request, receipt)
    expected_status = (
        "out_of_scope"
        if audit.get("available") and not audit["energy_within_tolerance"]
        else receipt.native.status
    )
    if audit != record["audit"] or record["status"] != expected_status:
        raise ValueError("pipe/soil audit or parent status changed")
    expected_issues = list(receipt.native.payload["notes"])
    if audit.get("available") and not audit["energy_within_tolerance"]:
        expected_issues.append("independent final solid/boundary energy audit failed")
    if record["issues"] != expected_issues:
        raise ValueError("pipe/soil notes differ from the native limitations and energy audit")


def pipe_soil_signal_frames(record, context):
    verify_pipe_soil_transient_record(record)
    if (
        record["status"] != "computed"
        or record["context"] != context.model_dump(mode="json")
        or not record["audit"].get("energy_within_tolerance")
    ):
        raise ValueError("completed, energy-audited pipe/soil result with matching context required")
    request = PipeSoilTransientRequest.model_validate_json(json.dumps(record["request"]))
    values = record["native_receipt"]["native"]["payload"]["values"]
    frames = []
    for i, time in enumerate(request.time_s):
        channels = {
            f"cell.{s.id}.{j}.temperature_K": (values["temperature_history_K"][i][offset + j], "K")
            for offset, s in _shell_offsets(request)
            for j in range(len(s.initial_cell_temperature_K))
        }
        channels.update(
            inner_heat_into_solid_W=(values["boundary_heat_rate_into_solid_W"][i][0], "W"),
            outer_heat_into_solid_W=(values["boundary_heat_rate_into_solid_W"][i][1], "W"),
        )
        frames.append(
            SignalFrame(
                stream_id=f"{request.asset_id}:{request.location_id}:{request.event_id}",
                clock_id=context.clock_id,
                time_s=time + request.clock_offset_s,
                values={k: v for k, (v, _) in channels.items()},
                units={k: u for k, (_, u) in channels.items()},
                valid={k: True for k in channels},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode="radial-pipe-soil-point-heat-and-cell-average-temperature",
            )
        )
    return tuple(frames)


def _shell_offsets(request):
    offset = 0
    for shell in request.shells:
        yield offset, shell
        offset += len(shell.initial_cell_temperature_K)
