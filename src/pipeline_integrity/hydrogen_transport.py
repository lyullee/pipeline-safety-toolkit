"""Atom-molar transport boundaries around existing equilibrium and kinetic owners."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .material_exchange import MaterialJob, MaterialReceipt, evaluate_material_job, prepare_material_job
from .material_source import material_source_guard, verify_material_source_pin

SEMI = "transport.semi_infinite_diffusion"
EQUILIBRIUM = "transport.hydrogen_trap_equilibrium"
KINETIC = "transport.hydrogen_slab_kinetic"
REVIEWS = {
    SEMI: (
        "isothermal_half_space_constant_diffusivity_fixed_boundary",
        "hydrogen_atom_moles_and_point_coordinate_basis",
        "material_temperature_and_parameter_support",
    ),
    EQUILIBRIUM: (
        "local_equilibrium_independent_single_occupancy_fixed_population",
        "hydrogen_atom_moles_and_binding_energy_reference",
        "material_temperature_and_parameter_support",
    ),
    KINETIC: (
        "planar_cell_averages_and_elapsed_time_origin",
        "fixed_independent_dilute_sites_and_selected_rate_model",
        "hydrogen_atom_moles_signed_inward_boundaries_and_parameter_support",
        "uniform_temperature_and_supported_history",
    ),
}
INPUT_UNITS = {
    SEMI: {
        "diffusivity_m2_s": "m2/s",
        "exposure_s": "s",
        "depth_m": "m",
        "initial_concentration_mol_m3": "mol/m3",
        "surface_concentration_mol_m3": "mol/m3",
    },
    EQUILIBRIUM: {
        "total_hydrogen_atoms_mol_m3": "mol/m3",
        "lattice_site_density_mol_m3": "mol/m3",
        "lattice_diffusivity_m2_s": "m2/s",
        "temperature_K": "K",
        "maximum_supported_lattice_occupancy": "1",
        "traps[].site_density_mol_m3": "mol/m3",
        "traps[].binding_energy_J_mol": "J/mol",
        "traps[].maximum_supported_occupancy": "1",
    },
    KINETIC: {
        "thickness_m": "m",
        "cell_count": "1",
        "lattice_site_density_mol_m3": "mol/m3",
        "initial_lattice_hydrogen_atoms_mol_m3": "mol/m3",
        "elapsed_time_s": "s",
        "uniform_temperature_K": "K",
        "lattice_diffusivity_m2_s": "m2/s",
        "boundary_concentrations": "mol/m3",
        "boundary_transfer_coefficient": "m/s",
        "boundary_capacity_fluxes": "mol/(m2*s)",
        "traps[].population.site_density_mol_m3": "mol/m3",
        "traps[].population.binding_energy_J_mol": "J/mol",
        "traps[].initial_occupancy": "1",
        "traps[].capture_frequency_per_s": "1/s",
        "traps[].release_frequency_per_s": "1/s",
        "maximum_supported_lattice_occupancy": "1",
        "maximum_supported_total_trap_to_lattice_site_ratio": "1",
    },
}
MEASURES = {
    SEMI: "point_lattice",
    EQUILIBRIUM: "local_total_and_partition",
    KINETIC: "cell_average_lattice_and_traps",
}
FLUX_SIGNS = {
    SEMI: "positive_inward_at_declared_surface",
    EQUILIBRIUM: "not_used",
    KINETIC: "positive_inward_left_and_right",
}
REQUIRED = {
    SEMI: set(INPUT_UNITS[SEMI]),
    EQUILIBRIUM: {
        "material_condition_id",
        "reference_lattice_id",
        "total_hydrogen_atoms_mol_m3",
        "lattice_site_density_mol_m3",
        "lattice_diffusivity_m2_s",
        "temperature_K",
        "maximum_supported_lattice_occupancy",
        "traps",
        "local_equilibrium_confirmed",
        "independent_single_occupancy_sites_confirmed",
        "fixed_trap_population_and_uniform_temperature_confirmed",
    },
    KINETIC: {
        "material_condition_id",
        "reference_lattice_id",
        "elapsed_time_origin_id",
        "thickness_m",
        "cell_count",
        "lattice_site_density_mol_m3",
        "initial_lattice_hydrogen_atoms_mol_m3",
        "elapsed_time_s",
        "uniform_temperature_K",
        "lattice_diffusivity_m2_s",
        "left_boundary",
        "right_boundary",
        "traps",
        "maximum_supported_lattice_occupancy",
        "maximum_supported_total_trap_to_lattice_site_ratio",
        "planar_geometry_reviewed",
        "fixed_populations_and_uniform_temperature_reviewed",
        "independent_single_occupancy_and_dilute_trap_regime_reviewed",
        "negligible_stress_gradient_reviewed",
        "detailed_balance_rate_ratio_reviewed",
        "relative_tolerance",
        "absolute_occupancy_tolerance",
        "maximum_step_s",
        "maximum_step_attempts",
        "maximum_relative_mass_balance_error",
        "store_profiles",
    },
}


class HydrogenTransportRequest(Contract):
    method: Literal[
        "transport.semi_infinite_diffusion",
        "transport.hydrogen_trap_equilibrium",
        "transport.hydrogen_slab_kinetic",
    ]
    asset_id: Name
    location_id: Name
    clock_id: Name
    material_condition_id: Name | None = None
    reference_lattice_id: Name | None = None
    population_id: Name | None = None
    parameter_support_id: Name | None = None
    geometry_id: Name | None = None
    elapsed_time_origin_id: Name | None = None
    concentration_basis: Literal["hydrogen_atoms_mol_m3", "unresolved"] = "unresolved"
    concentration_measure: Literal[
        "point_lattice", "local_total_and_partition", "cell_average_lattice_and_traps", "unresolved"
    ] = "unresolved"
    binding_energy_reference: Literal["positive_relative_to_lattice_site", "not_used", "unresolved"] = (
        "unresolved"
    )
    flux_sign_convention: Literal[
        "positive_inward_at_declared_surface", "positive_inward_left_and_right", "not_used", "unresolved"
    ] = "unresolved"
    declared_temperature_K: Positive | None = None
    temperature_support_K: tuple[Positive, Positive] | None = None
    depth_point_ids: tuple[Name, ...] = Field(default=(), max_length=100000)
    cell_ids: tuple[Name, ...] = Field(default=(), max_length=512)
    time_knot_ids: tuple[Name, ...] = Field(default=(), max_length=4096)
    parameters: dict | None
    input_units: dict[str, str]
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    maximum_depth_points: int = Field(default=100000, ge=1, le=100000)
    maximum_cells: int = Field(default=512, ge=2, le=512)
    maximum_time_knots: int = Field(default=4096, ge=2, le=4096)
    maximum_trap_populations: int = Field(default=100, ge=0, le=100)
    maximum_state_values: int = Field(default=1000000, ge=1, le=1000000)
    maximum_step_attempts: int = Field(default=50000, ge=1, le=500000)
    owner_source_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def fields(self):
        _budgets(self)
        json_value(self.parameters)
        if self.input_units != INPUT_UNITS[self.method]:
            raise ValueError("hydrogen inputs require the explicitly declared SI and atom-molar basis")
        if self.parameters is not None and set(self.parameters) - REQUIRED[self.method]:
            raise ValueError("unknown native hydrogen-transport parameter")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS[self.method]):
            raise ValueError("review keys must belong to the selected hydrogen method")
        if any(
            len(v) != len(set(v))
            for v in (self.evidence_ids, self.depth_point_ids, self.cell_ids, self.time_knot_ids)
        ):
            raise ValueError("hydrogen evidence and sample identities must not repeat")
        if (
            self.temperature_support_K is not None
            and self.temperature_support_K[0] > self.temperature_support_K[1]
        ):
            raise ValueError("temperature parameter support must be ordered")
        for actual, expected in (
            (self.concentration_measure, MEASURES[self.method]),
            (self.flux_sign_convention, FLUX_SIGNS[self.method]),
            (
                self.binding_energy_reference,
                "not_used" if self.method == SEMI else "positive_relative_to_lattice_site",
            ),
        ):
            if actual not in (expected, "unresolved"):
                raise ValueError("hydrogen point/cell, flux sign or energy reference meaning differs")
        return self


def _unknown(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_unknown(v) for k, v in value.items() if k not in {"sha256", "license_id"})
    if isinstance(value, (list, tuple)):
        return any(_unknown(v) for v in value)
    return False


def _unknown_parameters(method, p):
    if p is None or REQUIRED[method] - set(p):
        return True
    if method != KINETIC:
        return _unknown(p)
    if any(p[k] is None for k in REQUIRED[KINETIC] - {"maximum_step_s"}):
        return True
    if any(
        _unknown(p[k])
        for k in (
            "initial_lattice_hydrogen_atoms_mol_m3",
            "elapsed_time_s",
            "uniform_temperature_K",
            "lattice_diffusivity_m2_s",
        )
    ):
        return True
    boundary_fields = {
        "no_flux": (),
        "prescribed_lattice_concentration": ("lattice_hydrogen_atoms_mol_m3",),
        "linear_surface_transfer": ("equilibrium_lattice_hydrogen_atoms_mol_m3", "transfer_coefficient_m_s"),
        "dissociative_surface_reaction": (
            "absorption_capacity_flux_mol_m2_s",
            "desorption_capacity_flux_mol_m2_s",
        ),
    }
    for boundary in (p["left_boundary"], p["right_boundary"]):
        if not isinstance(boundary, dict) or any(
            _unknown(boundary.get(k)) for k in ("boundary_id", "kind", "evidence")
        ):
            return True
        if any(_unknown(boundary.get(k)) for k in boundary_fields.get(boundary["kind"], ())):
            return True
    for trap in p["traps"]:
        if not isinstance(trap, dict) or any(
            _unknown(trap.get(k))
            for k in (
                "population",
                "rate_model",
                "capture_frequency_per_s",
                "initial_occupancy",
                "rate_evidence",
            )
        ):
            return True
        if trap["rate_model"] == "independent_frequencies" and _unknown(trap.get("release_frequency_per_s")):
            return True
    return False


def _budgets(request):
    p = request.parameters
    if not isinstance(p, dict):
        return
    limits = {
        "depth_m": request.maximum_depth_points,
        "elapsed_time_s": request.maximum_time_knots,
        "uniform_temperature_K": request.maximum_time_knots,
        "lattice_diffusivity_m2_s": request.maximum_time_knots,
        "initial_lattice_hydrogen_atoms_mol_m3": request.maximum_cells,
        "traps": request.maximum_trap_populations,
    }
    for key, limit in limits.items():
        if isinstance(p.get(key), (list, tuple)) and len(p[key]) > limit:
            raise ValueError("hydrogen array/population budget exceeded before serialization")
    if request.method != KINETIC:
        return
    cells, times, traps = p.get("cell_count"), p.get("elapsed_time_s"), p.get("traps")
    if type(cells) is int and cells > request.maximum_cells:
        raise ValueError("hydrogen spatial-cell budget exceeded")
    if (
        type(p.get("maximum_step_attempts")) is int
        and p["maximum_step_attempts"] > request.maximum_step_attempts
    ):
        raise ValueError("hydrogen solver-attempt budget exceeded; no solver controls changed")
    if type(cells) is int and isinstance(times, (list, tuple)) and isinstance(traps, (list, tuple)):
        if len(traps) > 16 or (
            p.get("store_profiles") is True
            and cells * len(times) * (1 + len(traps)) > request.maximum_state_values
        ):
            raise ValueError("hydrogen retained-state budget exceeded; no profiles dropped")
    for boundary in (p.get("left_boundary"), p.get("right_boundary")):
        if isinstance(boundary, dict) and any(
            isinstance(v, (list, tuple)) and k != "evidence" and len(v) > request.maximum_time_knots
            for k, v in boundary.items()
        ):
            raise ValueError("hydrogen boundary-history budget exceeded")
    for trap in traps if isinstance(traps, (list, tuple)) else ():
        if isinstance(trap, dict) and any(
            isinstance(trap.get(k), (list, tuple)) and len(trap[k]) > limit
            for k, limit in (
                ("capture_frequency_per_s", request.maximum_time_knots),
                ("release_frequency_per_s", request.maximum_time_knots),
                ("initial_occupancy", request.maximum_cells),
            )
        ):
            raise ValueError("hydrogen trap-history budget exceeded")


def _provider_evidence(value):
    if isinstance(value, dict):
        if "source_id" in value and "citation" in value:
            yield value
        else:
            for item in value.values():
                yield from _provider_evidence(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _provider_evidence(item)


def evaluate_hydrogen_transport(request, context, *, client=None, pinned_job=None, owner_source_root=None):
    """Preserve physical meanings and support before calling the original transport owner."""
    if not isinstance(request, HydrogenTransportRequest) or not isinstance(context, RunContext):
        raise TypeError("HydrogenTransportRequest and RunContext are required")
    _budgets(request)
    request = HydrogenTransportRequest.model_validate_json(
        json.dumps(json_value(request.model_dump(mode="python")), allow_nan=False)
    )
    context = RunContext.model_validate_json(
        json.dumps(json_value(context.model_dump(mode="python")), allow_nan=False)
    )
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("hydrogen source and context asset/clock differ")
    evidence = {e.id: e for e in context.evidence}
    if (
        len(evidence) != len(context.evidence)
        or set(request.evidence_ids) - evidence.keys()
        or set(request.review_evidence_ids.values()) - set(request.evidence_ids)
    ):
        raise ValueError("hydrogen evidence must be distinct, selected and present in context")
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS[request.method]
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    p = request.parameters
    if _unknown_parameters(request.method, p):
        issues.append("native hydrogen inputs or required declarations are unresolved")
    if (
        any(
            v is None
            for v in (
                request.material_condition_id,
                request.reference_lattice_id,
                request.population_id,
                request.parameter_support_id,
            )
        )
        or request.temperature_support_K is None
    ):
        issues.append("material/lattice/population/parameter and temperature support are unresolved")
    if "unresolved" in (
        request.concentration_basis,
        request.concentration_measure,
        request.binding_energy_reference,
        request.flux_sign_convention,
    ):
        issues.append("hydrogen atom-molar, point/cell, energy or flux-sign convention is unresolved")
    if request.method in (SEMI, KINETIC) and (
        request.geometry_id is None or request.elapsed_time_origin_id is None
    ):
        issues.append("transport geometry and physical elapsed-time origin are required")
    if request.method in (SEMI, EQUILIBRIUM) and request.declared_temperature_K is None:
        issues.append("isothermal/local temperature is unresolved")
    temperatures = []
    if p is not None:
        for key in ("material_condition_id", "reference_lattice_id", "elapsed_time_origin_id"):
            if (
                key in p
                and p[key] is not None
                and getattr(request, key) is not None
                and p[key] != getattr(request, key)
            ):
                raise ValueError("native hydrogen material/lattice/time identity differs from source")
        for item in _provider_evidence(p):
            key = item["source_id"]
            if key not in request.evidence_ids:
                raise ValueError("trap or boundary evidence is not selected source evidence")
            original = evidence[key]
            if (item.get("citation"), item.get("license_id"), item.get("sha256")) != (
                original.citation,
                original.license_id,
                original.sha256,
            ):
                raise ValueError("hydrogen provider citation/license/checksum differs from source")
        for values, ids in (
            (p.get("depth_m"), request.depth_point_ids),
            (p.get("elapsed_time_s"), request.time_knot_ids),
            (p.get("initial_lattice_hydrogen_atoms_mol_m3"), request.cell_ids),
        ):
            if isinstance(values, (list, tuple)):
                if not ids:
                    issues.append("ordered original depth/time/cell identities are required")
                elif len(values) != len(ids):
                    raise ValueError("hydrogen source identities must match every original sample")
        if request.method == EQUILIBRIUM and p.get("temperature_K") is not None:
            if (
                request.declared_temperature_K is not None
                and p["temperature_K"] != request.declared_temperature_K
            ):
                raise ValueError("native and declared local temperature differ")
        if request.method == KINETIC and isinstance(p.get("uniform_temperature_K"), (list, tuple)):
            temperatures = p["uniform_temperature_K"]
            if request.declared_temperature_K is not None and any(
                t is not None and t != request.declared_temperature_K for t in temperatures
            ):
                raise ValueError("one declared temperature cannot replace a different thermal history")
    if request.method != KINETIC and request.declared_temperature_K is not None:
        temperatures = [request.declared_temperature_K]
    status = "needs_data"
    if request.temperature_support_K is not None and any(
        type(t) in (int, float)
        and not request.temperature_support_K[0] <= t <= request.temperature_support_K[1]
        for t in temperatures
    ):
        issues.append("temperature lies outside declared material/parameter support")
        status = "out_of_scope"
    body = {
        "schema": "pipeline_integrity.hydrogen_transport.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": status,
        "values": {},
        "output_units": {},
        "probability_semantics": {},
        "issues": issues,
        "native_receipt": None,
        "source_pin": None,
        "transport_is_embrittlement_or_failure_probability": False,
        "local_differential_diffusivity_is_global_permeation_coefficient": False,
        "current_signal_was_inverted_into_boundary_concentration": False,
        "field_or_standard_qualification": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        with material_source_guard(owner_source_root, request.owner_source_tree_sha256, client=client) as pin:
            if pinned_job is None:
                job = prepare_material_job(
                    request.method,
                    p,
                    context,
                    location_id=request.location_id,
                    evidence_ids=request.evidence_ids,
                    client=client,
                )
            else:
                if not isinstance(pinned_job, MaterialJob):
                    raise TypeError("a native MaterialJob is required for hydrogen pinning")
                job = MaterialJob.model_validate_json(
                    json.dumps(json_value(pinned_job.model_dump(mode="python")), allow_nan=False)
                )
                if (
                    job.model_id,
                    job.parameters,
                    job.asset_id,
                    job.location_id,
                    job.clock_id,
                    job.evidence_ids,
                ) != (
                    request.method,
                    p,
                    request.asset_id,
                    request.location_id,
                    request.clock_id,
                    request.evidence_ids,
                ):
                    raise ValueError("hydrogen job differs from its original source request")
            receipt = evaluate_material_job(job, context, client=client)
            receipt.check_integrity()
            native = receipt.native.payload
            body.update(
                status=native["status"],
                values=native["values"],
                output_units=native["units"],
                probability_semantics=native["probability_semantics"],
                issues=list(native["notes"]),
                native_receipt=receipt.model_dump(mode="json"),
            )
        body["source_pin"] = pin
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def verify_hydrogen_transport_record(record):
    if record.get("schema") != "pipeline_integrity.hydrogen_transport.v1" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("hydrogen source request or result changed")
    if any(
        record.get(k) is not False
        for k in (
            "transport_is_embrittlement_or_failure_probability",
            "local_differential_diffusivity_is_global_permeation_coefficient",
            "current_signal_was_inverted_into_boundary_concentration",
            "field_or_standard_qualification",
        )
    ):
        raise ValueError("hydrogen output meaning or qualification changed")
    request = HydrogenTransportRequest.model_validate_json(
        json.dumps(json_value(record["request"]), allow_nan=False)
    )
    context = RunContext.model_validate_json(json.dumps(json_value(record["context"]), allow_nan=False))
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("hydrogen source/context asset or clock differs")
    verify_material_source_pin(
        record.get("source_pin"),
        request.owner_source_tree_sha256,
        executed=record.get("native_receipt") is not None,
    )
    if record.get("native_receipt") is None:
        if record.get("status") not in {"needs_data", "out_of_scope"} or any(
            record.get(k) != {} for k in ("values", "output_units", "probability_semantics")
        ):
            raise ValueError("withheld hydrogen output must retain empty native values")
    else:
        receipt = MaterialReceipt.model_validate_json(
            json.dumps(json_value(record["native_receipt"]), allow_nan=False)
        )
        receipt.check_integrity()
        if receipt.context != context or (
            receipt.job.model_id,
            receipt.job.asset_id,
            receipt.job.location_id,
            receipt.job.clock_id,
            receipt.job.parameters,
            receipt.job.evidence_ids,
        ) != (
            request.method,
            request.asset_id,
            request.location_id,
            request.clock_id,
            request.parameters,
            request.evidence_ids,
        ):
            raise ValueError("hydrogen receipt differs from its source request and context")
        if (
            receipt.native.status,
            receipt.native.payload["values"],
            receipt.native.payload["units"],
            receipt.native.payload["probability_semantics"],
        ) != (record["status"], record["values"], record["output_units"], record["probability_semantics"]):
            raise ValueError("hydrogen projection differs from original native results")
