"""Steady single-pipe soil boundary using ht geometry and the existing cooling model.

The isothermal-plane shape factor and cylindrical layers keep their library
implementation. This component does not solve transient or moisture-coupled soil.
"""

import inspect
import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .models.thermal import Cooling, cooling

REVIEWS = (
    "isothermal_plane_and_homogeneous_soil",
    "single_long_straight_pipe_and_local_radial_approximation",
    "constant_properties_and_single_phase_temperature_support",
    "film_and_contact_boundary_conditions",
)
INPUT_UNITS = {
    "length": "m",
    "temperature": "K",
    "conductivity": "W/(m*K)",
    "film_coefficient": "W/(m2*K)",
    "contact_resistance_per_length": "K*m/W",
    "mass_flow": "kg/s",
    "specific_heat": "J/(kg*K)",
    "dimensionless": "1",
}


def _thermal_backend_receipt():
    from ht import R_cylinder, S_isothermal_pipe_to_plane

    return {
        "name": "ht",
        "version": version("ht"),
        "shape_function": "S_isothermal_pipe_to_plane",
        "cylinder_function": "R_cylinder",
        "shape_implementation_sha256": digest(inspect.getsource(S_isothermal_pipe_to_plane)),
        "cylinder_implementation_sha256": digest(inspect.getsource(R_cylinder)),
        "cooling_implementation_sha256": digest(inspect.getsource(cooling)),
    }


class ThermalPipeLayer(Contract):
    id: Name
    material_condition_id: Name
    outer_radius_m: Positive
    conductivity_W_m_K: Positive | None
    upstream_contact_resistance_K_m_W: Nonnegative | None
    temperature_support_K: tuple[Positive, Positive] | None
    evidence_id: Name

    @model_validator(mode="after")
    def support(self):
        if (
            self.temperature_support_K is not None
            and self.temperature_support_K[0] > self.temperature_support_K[1]
        ):
            raise ValueError("layer temperature support must be ordered")
        return self


class BuriedThermalRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    mode: Literal["uniform_fluid_temperature", "steady_plug_flow"]
    inner_radius_m: Positive
    length_m: Positive
    depth_to_pipe_center_m: Positive
    layers: tuple[ThermalPipeLayer, ...] = Field(min_length=1, max_length=64)
    fluid_inlet_temperature_K: Positive | None
    surface_plane_temperature_K: Positive | None
    soil_conductivity_W_m_K: Positive | None
    soil_contact_resistance_K_m_W: Nonnegative | None
    inner_film_coefficient_W_m2_K: Positive | None
    soil_temperature_support_K: tuple[Positive, Positive] | None
    fluid_temperature_support_K: tuple[Positive, Positive] | None
    minimum_supported_slenderness: Positive | None
    mass_flow_kg_s: Nonnegative | None = None
    specific_heat_J_kg_K: Positive | None = None
    maximum_supported_axial_NTU: Positive | None = None
    soil_evidence_id: Name | None
    film_evidence_id: Name | None
    fluid_property_evidence_id: Name | None = None
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    input_units: dict[str, str]
    expected_ht_version: Name | None = None

    @model_validator(mode="after")
    def geometry(self):
        if self.input_units != INPUT_UNITS:
            raise ValueError("buried pipe inputs require the explicit SI/contact-resistance basis")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or len({p.id for p in self.layers}) != len(
            self.layers
        ):
            raise ValueError("evidence and layer identifiers must be distinct")
        previous = self.inner_radius_m
        for layer in self.layers:
            if layer.outer_radius_m <= previous:
                raise ValueError(
                    "ordered layers require strictly increasing radii; no sorting or overlap repair"
                )
            previous = layer.outer_radius_m
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("unknown soil-coupling review key")
        for bounds in (self.soil_temperature_support_K, self.fluid_temperature_support_K):
            if bounds is not None and bounds[0] > bounds[1]:
                raise ValueError("property temperature support must be ordered")
        return self


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("buried pipe request and context asset/clock differ")
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence) or set(request.evidence_ids) - evidence.keys():
        raise ValueError("buried pipe evidence must be distinct and present in context")
    selected = set(request.evidence_ids)
    if set(request.review_evidence_ids.values()) - selected or any(
        p.evidence_id not in selected for p in request.layers
    ):
        raise ValueError("property and review evidence must be selected source evidence")
    for key in (request.soil_evidence_id, request.film_evidence_id, request.fluid_property_evidence_id):
        if key is not None and key not in selected:
            raise ValueError("soil/film/fluid property evidence is not selected")
    issues = [
        f"review and evidence required: {key}"
        for key in REVIEWS
        if not request.applicability_reviews.get(key, False) or key not in request.review_evidence_ids
    ]
    if any(
        v is None
        for v in (
            request.fluid_inlet_temperature_K,
            request.surface_plane_temperature_K,
            request.soil_conductivity_W_m_K,
            request.soil_contact_resistance_K_m_W,
            request.inner_film_coefficient_W_m2_K,
            request.soil_temperature_support_K,
            request.fluid_temperature_support_K,
            request.minimum_supported_slenderness,
            request.soil_evidence_id,
            request.film_evidence_id,
        )
    ) or any(
        v is None
        for layer in request.layers
        for v in (
            layer.conductivity_W_m_K,
            layer.upstream_contact_resistance_K_m_W,
            layer.temperature_support_K,
        )
    ):
        issues.append("thermal properties, contact conditions or parameter support are unresolved")
    if request.mode == "steady_plug_flow" and any(
        v is None
        for v in (
            request.mass_flow_kg_s,
            request.specific_heat_J_kg_K,
            request.maximum_supported_axial_NTU,
            request.fluid_property_evidence_id,
        )
    ):
        issues.append("single-phase flow/heat capacity/axial support requires explicit evidence")
    outside = []
    if request.depth_to_pipe_center_m <= request.layers[-1].outer_radius_m:
        outside.append("the complete pipe must lie below the isothermal surface plane")
    if (
        request.minimum_supported_slenderness is not None
        and request.length_m / (2 * request.layers[-1].outer_radius_m) < request.minimum_supported_slenderness
    ):
        outside.append("segment slenderness is outside caller-reviewed local radial support")
    if request.mode == "steady_plug_flow" and request.mass_flow_kg_s == 0:
        outside.append("zero advective flow needs a stagnant/transient model; zero is not missing")
    return issues + outside, "out_of_scope" if outside else "needs_data"


def evaluate_buried_thermal(request, context):
    """Return a reviewed steady boundary; preserve signed heat and calculation limits."""
    if not isinstance(request, BuriedThermalRequest) or not isinstance(context, RunContext):
        raise TypeError("BuriedThermalRequest and RunContext are required")
    request = BuriedThermalRequest.model_validate_json(
        json.dumps(json_value(request.model_dump(mode="python")), allow_nan=False)
    )
    context = RunContext.model_validate_json(
        json.dumps(json_value(context.model_dump(mode="python")), allow_nan=False)
    )
    issues, status = _policy(request, context)
    body = {
        "schema": "pipeline_integrity.buried_thermal.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": status,
        "issues": issues,
        "values": {},
        "output_units": {},
        "calculation": None,
        "transient_soil_or_moisture_coupling_modelled": False,
        "multiple_pipe_interaction_modelled": False,
        "field_or_standard_qualification": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if not issues:
        from ht import R_cylinder, S_isothermal_pipe_to_plane

        backend_version = version("ht")
        if request.expected_ht_version is not None and request.expected_ht_version != backend_version:
            raise ValueError("ht version differs from the reviewed library pin")
        length = request.length_m
        film = 1 / (request.inner_film_coefficient_W_m2_K * 2 * math.pi * request.inner_radius_m * length)
        layer_parts = []
        inner = request.inner_radius_m
        for layer in request.layers:
            radial = R_cylinder(
                Di=2 * inner, Do=2 * layer.outer_radius_m, k=layer.conductivity_W_m_K, L=length
            )
            layer_parts.append((layer, layer.upstream_contact_resistance_K_m_W / length, radial))
            inner = layer.outer_radius_m
        shape = S_isothermal_pipe_to_plane(D=2 * inner, Z=request.depth_to_pipe_center_m, L=length)
        soil = 1 / (request.soil_conductivity_W_m_K * shape)
        soil_contact = request.soil_contact_resistance_K_m_W / length
        total = math.fsum(
            [film, soil_contact, soil, *[contact + radial for _, contact, radial in layer_parts]]
        )
        if not math.isfinite(total) or total <= 0 or not math.isfinite(1 / total):
            raise ValueError("thermal resistance/conductance exceeds finite numerical support")
        conductance = 1 / total
        surface = request.surface_plane_temperature_K
        inlet = request.fluid_inlet_temperature_K
        flow_balance = None
        ntu = None
        outlet = None
        support_issues = []
        if request.mode == "steady_plug_flow":
            capacity = request.mass_flow_kg_s * request.specific_heat_J_kg_K
            if not 0 < capacity < math.inf:
                raise ValueError("single-phase heat-capacity rate is outside numerical support")
            ntu = conductance / capacity
            result = cooling(
                Cooling(
                    inlet_temperature_k=inlet,
                    ambient_temperature_k=surface,
                    mass_flow_kg_s=request.mass_flow_kg_s,
                    specific_heat_j_kg_k=request.specific_heat_J_kg_K,
                    conductance_w_k=conductance,
                )
            )
            outlet = result.values["outlet_temperature_k"]
            heat = result.values["heat_loss_w"]
            flow_balance = heat - capacity * (inlet - outlet)
            if inlet != surface and heat == 0:
                raise ValueError(
                    "nonzero thermal driving force is unresolved at the supplied numerical scales"
                )
            mean = surface + heat * total
            if ntu > request.maximum_supported_axial_NTU:
                support_issues.append("axial NTU exceeds caller-reviewed local radial approximation support")
        else:
            heat = (inlet - surface) * conductance
            mean = inlet
        temperature = mean - heat * film
        rows = []
        for layer, contact, radial in layer_parts:
            upstream = temperature
            inside = upstream - heat * contact
            temperature = inside - heat * radial
            rows.append(
                {
                    "layer_id": layer.id,
                    "material_condition_id": layer.material_condition_id,
                    "evidence_id": layer.evidence_id,
                    "contact_resistance_K_W": contact,
                    "conduction_resistance_K_W": radial,
                    "upstream_temperature_K": upstream,
                    "inner_temperature_K": inside,
                    "outer_temperature_K": temperature,
                }
            )
            if (
                min(inside, temperature) < layer.temperature_support_K[0]
                or max(inside, temperature) > layer.temperature_support_K[1]
            ):
                support_issues.append("layer temperature outside reviewed property support: " + layer.id)
        soil_inner = temperature - heat * soil_contact
        plane_check = soil_inner - heat * soil
        axial_endpoints = []
        if outlet is not None:
            # Constant coefficients make each local interface temperature affine
            # in bulk-fluid temperature. Check both ends, not only their mean.
            for endpoint, bulk_temperature in (("inlet", inlet), ("outlet", outlet)):
                driving = bulk_temperature - surface
                local_temperature = bulk_temperature - driving * film / total
                local_layers = []
                for layer, contact, radial in layer_parts:
                    local_inner = local_temperature - driving * contact / total
                    local_temperature = local_inner - driving * radial / total
                    local_layers.append(
                        {
                            "layer_id": layer.id,
                            "inner_temperature_K": local_inner,
                            "outer_temperature_K": local_temperature,
                        }
                    )
                    if (
                        min(local_inner, local_temperature) < layer.temperature_support_K[0]
                        or max(local_inner, local_temperature) > layer.temperature_support_K[1]
                    ):
                        support_issues.append(
                            "layer temperature outside reviewed property support: " + layer.id
                        )
                local_soil = local_temperature - driving * soil_contact / total
                if (
                    min(local_soil, surface) < request.soil_temperature_support_K[0]
                    or max(local_soil, surface) > request.soil_temperature_support_K[1]
                ):
                    support_issues.append("soil temperature outside reviewed conductivity support")
                axial_endpoints.append(
                    {
                        "endpoint": endpoint,
                        "fluid_temperature_K": bulk_temperature,
                        "layer_results": local_layers,
                        "soil_inner_temperature_K": local_soil,
                    }
                )
        if (
            min(soil_inner, surface) < request.soil_temperature_support_K[0]
            or max(soil_inner, surface) > request.soil_temperature_support_K[1]
        ):
            support_issues.append("soil temperature outside reviewed conductivity support")
        fluid_temperatures = [inlet] if outlet is None else [inlet, outlet]
        if (
            min(fluid_temperatures) < request.fluid_temperature_support_K[0]
            or max(fluid_temperatures) > request.fluid_temperature_support_K[1]
        ):
            support_issues.append("fluid temperature outside reviewed constant-property support")
        support_issues = list(dict.fromkeys(support_issues))
        values = {
            "heat_from_fluid_to_soil_W": heat,
            "heat_per_length_W_m": heat / length,
            "total_resistance_K_W": total,
            "conductance_W_K": conductance,
            "soil_shape_factor_m": shape,
            "soil_resistance_K_W": soil,
            "soil_contact_resistance_K_W": soil_contact,
            "inner_film_resistance_K_W": film,
            "fluid_inlet_temperature_K": inlet,
            "fluid_mean_temperature_K": mean,
            "fluid_outlet_temperature_K": outlet,
            "axial_NTU": ntu,
            "layer_results": rows,
            "axial_endpoint_temperatures": axial_endpoints,
            "soil_inner_temperature_K": soil_inner,
            "surface_plane_temperature_K": surface,
            "temperature_drop_residual_K": plane_check - surface,
            "fluid_energy_balance_residual_W": flow_balance,
        }
        units = {
            "heat_from_fluid_to_soil_W": "W",
            "heat_per_length_W_m": "W/m",
            "total_resistance_K_W": "K/W",
            "conductance_W_K": "W/K",
            "soil_shape_factor_m": "m",
            "soil_resistance_K_W": "K/W",
            "soil_contact_resistance_K_W": "K/W",
            "inner_film_resistance_K_W": "K/W",
            "fluid_inlet_temperature_K": "K",
            "fluid_mean_temperature_K": "K",
            "fluid_outlet_temperature_K": "K",
            "axial_NTU": "1",
            "layer_results": "structured_SI",
            "axial_endpoint_temperatures": "structured_SI",
            "soil_inner_temperature_K": "K",
            "surface_plane_temperature_K": "K",
            "temperature_drop_residual_K": "K",
            "fluid_energy_balance_residual_W": "W",
        }
        calculation = {
            "request_digest": digest(request),
            "context_digest": digest(context),
            "values": values,
            "output_units": units,
            "status": "out_of_scope" if support_issues else "computed",
            "issues": support_issues,
            "backend": _thermal_backend_receipt(),
        }
        body.update(
            status=calculation["status"],
            issues=support_issues,
            values=values,
            output_units=units,
            calculation={**calculation, "calculation_digest": digest(calculation)},
        )
    return json.loads(json.dumps({**body, "record_digest": digest(body)}, allow_nan=False))


def verify_buried_thermal_record(record):
    if record.get("implementation_sha256") != file_digest(Path(__file__)):
        raise ValueError("buried thermal adapter differs from the stored implementation pin")
    if record.get("schema") != "pipeline_integrity.buried_thermal.v1" or record.get(
        "record_digest"
    ) != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("buried thermal request or result changed")
    if any(
        record.get(k) is not False
        for k in (
            "transient_soil_or_moisture_coupling_modelled",
            "multiple_pipe_interaction_modelled",
            "field_or_standard_qualification",
        )
    ):
        raise ValueError("steady boundary meaning or qualification changed")
    request = BuriedThermalRequest.model_validate_json(
        json.dumps(json_value(record["request"]), allow_nan=False)
    )
    context = RunContext.model_validate_json(json.dumps(json_value(record["context"]), allow_nan=False))
    issues, status = _policy(request, context)
    calculation = record.get("calculation")
    if calculation is None:
        if not issues or (record["issues"], record["status"], record["values"], record["output_units"]) != (
            issues,
            status,
            {},
            {},
        ):
            raise ValueError("withheld soil boundary has unsupported results")
        return
    if (
        issues
        or calculation.get("request_digest") != digest(request)
        or calculation.get("context_digest") != digest(context)
        or calculation.get("calculation_digest")
        != digest({k: v for k, v in calculation.items() if k != "calculation_digest"})
    ):
        raise ValueError("buried thermal calculation differs from its reviewed source")
    backend = calculation.get("backend")
    if (
        not isinstance(backend, dict)
        or backend != _thermal_backend_receipt()
        or (request.expected_ht_version is not None and backend["version"] != request.expected_ht_version)
    ):
        raise ValueError("buried thermal library/function/version source pin differs")
    if any(record.get(k) != calculation.get(k) for k in ("status", "issues", "values", "output_units")):
        raise ValueError("buried thermal projection differs from its original calculation")
