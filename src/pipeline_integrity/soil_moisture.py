"""Supplied moisture snapshots and thin contact layers around the soil FEM owner.

Moisture is uniform and fixed during each declared interval. Its effect on k
and volumetric heat capacity comes from a reviewed table. Changing capacity at
an interval boundary requires an explicitly supplied energy exchange, checked
against the carried native P1 temperature field. No infiltration, vapor flow,
evaporation, freezing or water-content equation is inferred.
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Positive, RunContext, digest
from .datasets import file_digest
from .soil_fem import SoilFEMRequest, boundary_metadata, evaluate_soil_fem


class SoilMoistureTable(Contract):
    id: Name
    volumetric_water_fraction: tuple[Fraction, ...] = Field(min_length=2, max_length=500)
    conductivity_W_m_K: tuple[Positive, ...] = Field(min_length=2, max_length=500)
    volumetric_heat_capacity_J_m3_K: tuple[Positive, ...] = Field(min_length=2, max_length=500)
    temperature_support_K: tuple[Positive, Positive]
    sensible_energy_reference_temperature_K: Positive
    evidence_id: Name
    uniform_moisture_and_piecewise_linear_properties_reviewed: bool = False

    @model_validator(mode="after")
    def knots(self):
        if not (len(self.volumetric_water_fraction) == len(self.conductivity_W_m_K)
                == len(self.volumetric_heat_capacity_J_m3_K)):
            raise ValueError("moisture/property knots must align")
        if any(b <= a for a,b in zip(self.volumetric_water_fraction,self.volumetric_water_fraction[1:])):
            raise ValueError("moisture fraction knots must strictly increase")
        if self.temperature_support_K[0] >= self.temperature_support_K[1]:
            raise ValueError("moisture property temperature support must have positive width")
        return self


class SoilContactLayer(Contract):
    boundary_id: Name
    thickness_m: Positive
    conductivity_W_m_K: Positive
    contact_resistance_m2_K_W: float = Field(default=0.0, ge=0)
    evidence_id: Name
    thin_flat_equal_area_geometry_and_negligible_storage_reviewed: bool = False


class SoilMoistureStage(Contract):
    id: Name
    volumetric_water_fraction: Fraction
    fem: SoilFEMRequest
    moisture_energy_exchange_at_start_J: float = 0.0
    evidence_id: Name


class SoilMoistureRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    property_table: SoilMoistureTable
    stages: tuple[SoilMoistureStage, ...] = Field(min_length=1, max_length=100)
    contact_layers: tuple[SoilContactLayer, ...] = Field(default=(), max_length=100)
    absolute_parameter_energy_tolerance_J: Positive
    relative_parameter_energy_tolerance: Positive = Field(default=1e-8, le=1e-3)
    maximum_result_bytes: int = Field(default=64000000, ge=1000, le=256000000)
    supplied_moisture_and_energy_exchange_mapping_reviewed: bool = False

    @model_validator(mode="after")
    def layout(self):
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError("moisture stage IDs must be unique")
        if len({x.boundary_id for x in self.contact_layers}) != len(self.contact_layers):
            raise ValueError("only one effective thin contact layer is allowed per boundary")
        first = self.stages[0].fem
        for i, stage in enumerate(self.stages):
            r = stage.fem
            if r.mode != "transient" or (r.asset_id,r.clock_id) != (self.asset_id,self.clock_id):
                raise ValueError("moisture stages must be transient on the request asset/clock")
            if (digest(r.mesh),r.location_id,r.event_id,r.triangulation,r.mass_scheme) != (
                    digest(first.mesh),first.location_id,first.event_id,first.triangulation,first.mass_scheme):
                raise ValueError("moisture stages must preserve mesh, node identity, event and integration scheme")
            if i and r.clock_offset_s != self.stages[i-1].fem.clock_offset_s+self.stages[i-1].fem.time_s[-1]:
                raise ValueError("moisture stages must be exactly contiguous on the declared clock")
        if self.stages[0].moisture_energy_exchange_at_start_J != 0:
            raise ValueError("initial moisture defines the initial inventory and cannot have a parameter jump")
        if sum(len(s.fem.time_s)*len(s.fem.mesh.vertices_m) for s in self.stages) > 500000:
            raise ValueError("moisture snapshot input history budget exceeded")
        return self


def soil_moisture_properties(table: SoilMoistureTable, volumetric_water_fraction: float):
    """Interpolate measured/supplied properties without extrapolating moisture."""
    q = SoilMoistureTable.model_validate(table.model_dump(mode="python"))
    x = volumetric_water_fraction
    knots = q.volumetric_water_fraction
    if not math.isfinite(x) or not knots[0] <= x <= knots[-1]:
        raise ValueError("moisture state is outside the supplied property table")
    j = min(len(knots)-2,max(0,bisect.bisect_right(knots,x)-1))
    weight = (x-knots[j])/(knots[j+1]-knots[j])
    def at(values):
        return values[j]+weight*(values[j+1]-values[j])
    return dict(conductivity_W_m_K=at(q.conductivity_W_m_K),
                volumetric_heat_capacity_J_m3_K=at(q.volumetric_heat_capacity_J_m3_K),
                bracket_indices=[j,j+1],right_weight=weight)


def bind_soil_contact_layers(request: SoilFEMRequest, layers: tuple[SoilContactLayer, ...]):
    """Compose actual layer thickness/k and contact resistance with a Robin film.

    The unchanged FEM cavity facet is the soil-side surface. The remote film
    ambient is connected through a negligible-storage thin flat layer of equal
    area. This does not insert a new soil cell or move the polygon boundary.
    """
    q = SoilFEMRequest.model_validate(request.model_dump(mode="python"))
    mapping = {layer.boundary_id:layer for layer in layers}
    if len(mapping) != len(layers) or not set(mapping) <= {b.id for b in q.boundaries}:
        raise ValueError("contact layers require unique existing FEM boundaries")
    data = q.model_dump(mode="json")
    receipts = []
    for boundary in data["boundaries"]:
        if boundary["id"] not in mapping:
            continue
        layer = mapping[boundary["id"]]
        if not layer.thin_flat_equal_area_geometry_and_negligible_storage_reviewed:
            raise ValueError("thin-layer equal-area geometry and negligible-storage review is required")
        if boundary["kind"] != "convection" or boundary["natural_field_representation"] != "face_constant":
            raise ValueError("thin contact composition requires explicit face-constant convection boundaries")
        history = boundary["convection_W_m2_K"]
        if history is None or any(v is None for row in history for v in row):
            raise ValueError("thin contact composition requires complete supplied film histories")
        resistance = layer.thickness_m/layer.conductivity_W_m_K+layer.contact_resistance_m2_K_W
        if not math.isfinite(resistance) or resistance <= 0:
            raise ValueError("thin-layer area-specific resistance is outside finite positive arithmetic")
        boundary["convection_W_m2_K"] = [[0.0 if h == 0 else 1/(resistance+1/h) for h in row] for row in history]
        receipts.append(dict(boundary_id=layer.boundary_id,layer=layer.model_dump(mode="json"),
                             layer_area_specific_resistance_m2_K_W=resistance,
                             original_film_W_m2_K=history,
                             effective_film_W_m2_K=boundary["convection_W_m2_K"],
                             boundary_geometry_changed=False,layer_storage_solved=False))
    data["evidence_ids"] = list(dict.fromkeys((*q.evidence_ids,*(x.evidence_id for x in layers))))
    derived = SoilFEMRequest.model_validate_json(json.dumps(data))
    return derived,receipts


def soil_moisture_metadata():
    return {name:file_digest(Path(__file__).with_name(name+".py"))
            for name in ("soil_moisture","soil_fem","soil_cross_section")}


def evaluate_soil_moisture(request: SoilMoistureRequest, context: RunContext):
    q = SoilMoistureRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("moisture wrapper asset/clock differs from context")
    if not q.supplied_moisture_and_energy_exchange_mapping_reviewed or not q.property_table.uniform_moisture_and_piecewise_linear_properties_reviewed:
        raise ValueError("supplied moisture/property and common-reference energy reviews are required")
    required = {q.property_table.evidence_id,*(s.evidence_id for s in q.stages),
                *(x.evidence_id for x in q.contact_layers)}
    evidence_ids = [e.id for e in c.evidence]
    if len(set(evidence_ids)) != len(evidence_ids) or not required <= set(evidence_ids):
        raise ValueError("moisture property/stage/contact evidence is absent or ambiguous")
    pins, q_pin, c_pin = soil_moisture_metadata(),digest(q),digest(c)
    results, transitions = [],[]
    previous = None
    initial_energy = final_energy = boundary_energy = parameter_energy = 0.0
    previous_capacity = None
    failure = None
    retained_bytes = 0
    reference = q.property_table.sensible_energy_reference_temperature_K

    def temperature_offset_volume(node_temperatures, geometry, axial_length):
        """Integrate the P1 field relative to its reference without a Kelvin-scale mean first."""
        areas = geometry["triangle_areas_m2"]
        element_dofs = geometry["element_dof_order"]
        if len(areas) != len(element_dofs):
            raise ValueError("native P1 element areas and degree-of-freedom map differ")
        triangle_integrals = (
            area
            * math.fsum(float(node_temperatures[dof]) - reference for dof in dofs)
            / len(dofs)
            for area, dofs in zip(areas, element_dofs, strict=True)
        )
        return axial_length * math.fsum(triangle_integrals)

    for stage in q.stages:
        properties = soil_moisture_properties(q.property_table,stage.volumetric_water_fraction)
        capacity = properties["volumetric_heat_capacity_J_m3_K"]
        data = stage.fem.model_dump(mode="json")
        data.update(conductivity_W_m_K=properties["conductivity_W_m_K"],
                    volumetric_heat_capacity_J_m3_K=capacity,
                    temperature_support_K=q.property_table.temperature_support_K,
                    property_evidence_id=q.property_table.evidence_id)
        data["evidence_ids"] = list(dict.fromkeys((*stage.fem.evidence_ids,*sorted(required))))
        expected_jump = 0.0
        if previous is not None:
            last = previous["native"]["rows"][-1]
            geometry = previous["native"]["geometry"]
            volume_temperature = temperature_offset_volume(
                last["node_temperature_K"], geometry, stage.fem.mesh.axial_extrusion_length_m
            )
            expected_jump = (capacity-previous_capacity)*volume_temperature
            tolerance = q.absolute_parameter_energy_tolerance_J+q.relative_parameter_energy_tolerance*max(
                abs(expected_jump),abs(stage.moisture_energy_exchange_at_start_J))
            if abs(stage.moisture_energy_exchange_at_start_J-expected_jump) > tolerance:
                failure = "supplied moisture energy does not reconcile the native temperature/capacity change at stage "+stage.id
                break
            data["initial_node_temperature_K"] = last["node_temperature_K"]
            data["nodal_field_evidence_id"] = stage.evidence_id
        derived = SoilFEMRequest.model_validate_json(json.dumps(data))
        derived,contacts = bind_soil_contact_layers(derived,q.contact_layers)
        cdata = c.model_dump(mode="json")
        cdata["metadata"] = {**c.metadata,"soil_fem_boundary":boundary_metadata(derived),
                             "soil_moisture_stage":dict(wrapper_request_digest=q_pin,stage_id=stage.id,
                                 moisture=stage.volumetric_water_fraction,property_table_digest=digest(q.property_table),
                                 previous_native_record_digest=None if previous is None else previous["record_digest"])}
        native_context = RunContext.model_validate_json(json.dumps(cdata))
        try:
            owner = evaluate_soil_fem(derived,native_context)
        except (ValueError,RuntimeError,ArithmeticError) as error:
            failure = "soil FEM moisture stage "+stage.id+": "+type(error).__name__+": "+str(error)
            break
        wrapped = dict(stage_id=stage.id,properties=properties,contact_bindings=contacts,owner_record=owner)
        retained_bytes += len(json.dumps(wrapped).encode("utf-8"))
        if retained_bytes > q.maximum_result_bytes:
            raise ValueError("moisture native receipts exceed result-byte budget")
        results.append(wrapped)
        if owner["status"] != "computed":
            failure = "soil FEM withheld/rejected moisture stage "+stage.id+": "+owner["status"]
            break
        native = owner["native"]
        first,last = native["rows"][0],native["rows"][-1]
        if previous is not None and first["node_temperature_K"] != previous["native"]["rows"][-1]["node_temperature_K"]:
            failure = "FEM moisture restart changed the carried nodal field at stage "+stage.id
            break
        geometry = native["geometry"]
        volume_factor = stage.fem.mesh.axial_extrusion_length_m
        def energy(row):
            return capacity * temperature_offset_volume(row["node_temperature_K"], geometry, volume_factor)
        stage_initial,stage_final = energy(first),energy(last)
        if previous is None:
            initial_energy = stage_initial
        else:
            jump_gate = q.absolute_parameter_energy_tolerance_J+q.relative_parameter_energy_tolerance*max(
                abs(stage_initial-final_energy),abs(expected_jump))
            if abs(stage_initial-final_energy-expected_jump) > jump_gate:
                failure = "FEM initial boundary/field discontinuity invalidates carried moisture state at stage "+stage.id
                break
        parameter_energy += stage.moisture_energy_exchange_at_start_J
        boundary_energy += last["cumulative_total_boundary_energy_J"]
        final_energy = stage_final
        transitions.append(dict(stage_id=stage.id,property_jump_energy_required_J=expected_jump,
                                supplied_moisture_energy_J=stage.moisture_energy_exchange_at_start_J,
                                stage_initial_energy_J=stage_initial,stage_final_energy_J=stage_final,
                                stage_boundary_energy_J=last["cumulative_total_boundary_energy_J"]))
        previous,previous_capacity = owner,capacity
    if soil_moisture_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("moisture source/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.soil_moisture.v1",request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",
                failure=failure,stages=results,energy_transitions=transitions,implementation=pins,
                initial_sensible_energy_J=initial_energy,final_sensible_energy_J=final_energy,
                thermal_boundary_energy_J=boundary_energy,supplied_moisture_energy_J=parameter_energy,
                energy_residual_J=final_energy-initial_energy-boundary_energy-parameter_energy,
                infiltration_or_moisture_transport_solved=False,latent_energy_inferred=False,
                contact_layer_storage_solved=False,numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("complete moisture receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_soil_moisture_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("moisture receipt changed")
    q = SoilMoistureRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("moisture caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("moisture caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("withheld moisture snapshots do not establish a completed replay")
    if evaluate_soil_moisture(q,c) != record:
        raise ValueError("moisture native replay differs")
    return True
