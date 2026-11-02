"""Steady liquid heating/viscosity/loss coupling through existing model owners.

Each supplied cell freezes inlet coefficients. Independent validation and mesh
convergence are pending; no crude-oil pseudo fluid or rheology law is embedded.
"""

from __future__ import annotations

import inspect
import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import thermo
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .models import hydraulics, thermal
from .thermo import CheckedProvider, CoolPropProvider, StateRequest, ThermodynamicState

REVIEWS = (
    "single_phase_newtonian_property_domain",
    "geometry_heat_and_flow_boundaries",
    "inlet_frozen_cell_mesh_and_thermal_closure",
    "kinetic_energy_and_pressure_loss_qualification",
)


class HeatedTransportCell(Contract):
    id: Name
    length_m: Positive
    inner_diameter_m: Positive
    roughness_m: Nonnegative
    minor_loss_coefficient: Nonnegative
    outlet_minus_inlet_elevation_m: float
    conductance_W_K: Nonnegative | None
    ambient_temperature_K: Positive | None
    heater_into_fluid_W: float | None
    evidence_id: Name

    @model_validator(mode="after")
    def geometry(self):
        if self.roughness_m / self.inner_diameter_m > 0.1:
            raise ValueError("roughness exceeds existing hydraulic owner domain")
        return self


class HeatedTransportRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    time_s: Nonnegative
    inlet_port_id: Name
    outlet_port_id: Name
    inlet: StateRequest
    mass_flow_kg_s: Positive | None
    cells: tuple[HeatedTransportCell, ...] = Field(min_length=1, max_length=4096)
    property_provider_kind: Literal["coolprop", "custom"]
    property_evidence_id: Name
    pressure_support_pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    gravity_m_s2: Positive
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"
    maximum_cell_temperature_closure_error_K: Nonnegative
    maximum_omitted_kinetic_energy_rate_W: Nonnegative
    energy_balance_absolute_W: Nonnegative = 1e-7
    balance_relative_tolerance: Nonnegative = Field(default=1e-8, le=1e-3)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def contract(self):
        if self.inlet_port_id == self.outlet_port_id:
            raise ValueError("inlet and outlet ports must differ")
        if not self.inlet.single_phase_required or set(self.inlet.inputs) not in ({"P", "T"}, {"P", "Hmass"}):
            raise ValueError("single-phase P/T or P/Hmass inlet is required")
        if (
            self.pressure_support_pa_abs[0] >= self.pressure_support_pa_abs[1]
            or self.temperature_support_K[0] >= self.temperature_support_K[1]
        ):
            raise ValueError("reviewed property domains must be ordered")
        if len({c.id for c in self.cells}) != len(self.cells) or len(set(self.evidence_ids)) != len(
            self.evidence_ids
        ):
            raise ValueError("cell/evidence IDs must be unique")
        if set(self.reviews) != set(REVIEWS):
            raise ValueError("all named review fields are required")
        return self


def boundary_metadata(q):
    return {
        "asset_id": q.asset_id,
        "event_id": q.event_id,
        "clock_id": q.clock_id,
        "time_s": q.time_s,
        "inlet_port_id": q.inlet_port_id,
        "outlet_port_id": q.outlet_port_id,
        "pressure_reference": "absolute Pa",
        "direction": "positive mass flow inlet to outlet",
        "request_digest": digest(q),
    }


def _provider(q, supplied):
    if q.property_provider_kind == "coolprop":
        if supplied is not None:
            raise ValueError("CoolProp request cannot silently substitute another property provider")
        return CoolPropProvider(use_cache=False)
    if supplied is None or not callable(getattr(supplied, "runtime_identity", None)):
        raise ValueError("custom provider needs resolve(StateRequest) and runtime_identity()")
    return supplied


def _state(provider, request, q):
    raw = CheckedProvider(provider).resolve(request)
    state = ThermodynamicState.model_validate(raw.model_dump(mode="python"))
    if state.phase != "liquid":
        raise ValueError("only resolved single-phase liquid is qualified")
    if state.viscosity_pa_s is None or state.cp_j_kg_k is None:
        raise ValueError("liquid viscosity and heat capacity must be available")
    if not (
        q.pressure_support_pa_abs[0] <= state.pressure_pa_abs <= q.pressure_support_pa_abs[1]
        and q.temperature_support_K[0] <= state.temperature_k <= q.temperature_support_K[1]
    ):
        raise ValueError("resolved state exceeds reviewed property support")
    if "P" in request.inputs and not math.isclose(state.pressure_pa_abs, request.inputs["P"], rel_tol=1e-10):
        raise ValueError("provider did not preserve prescribed absolute pressure")
    if "T" in request.inputs and not math.isclose(state.temperature_k, request.inputs["T"], rel_tol=1e-10):
        raise ValueError("provider did not preserve prescribed temperature")
    if "Hmass" in request.inputs and not math.isclose(
        state.enthalpy_j_kg, request.inputs["Hmass"], rel_tol=1e-10, abs_tol=1e-7
    ):
        raise ValueError("provider did not preserve prescribed enthalpy reference")
    return state


def native_backend_metadata(q, *, property_provider=None):
    """Pin owner code, provider identity and an uncached inlet reference marker."""
    from fluids import friction

    provider = _provider(q, property_provider)
    if q.property_provider_kind == "coolprop":
        import CoolProp.CoolProp as CP

        identity = {
            "name": "CoolProp",
            "version": version("CoolProp"),
            "revision": CP.get_global_param_string("gitrevision"),
            "binary_sha256": file_digest(Path(CP.__file__)),
            "license": "MIT",
        }
    else:
        identity = json_value(provider.runtime_identity())
        if not isinstance(identity, dict) or not identity or not identity.get("reference_basis"):
            raise ValueError("custom runtime identity must include the explicit enthalpy reference_basis")
        identity = {"declared": identity, "source_sha256": file_digest(Path(inspect.getfile(type(provider))))}
    return {
        "property_owner": identity,
        "inlet_reference_marker": _state(provider, q.inlet, q).model_dump(mode="json"),
        "fluids_version": version("fluids"),
        "fluids_license": "MIT",
        "friction_module_sha256": file_digest(Path(friction.__file__)),
        "hydraulic_adapter_sha256": file_digest(Path(hydraulics.__file__)),
        "thermal_adapter_sha256": file_digest(Path(thermal.__file__)),
        "shared_property_adapter_sha256": file_digest(Path(thermo.__file__)),
        "implementation_sha256": file_digest(Path(__file__)),
    }


def _policy(q, c):
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id) or c.metadata.get(
        "heated_transport_boundary"
    ) != boundary_metadata(q):
        raise ValueError("actual asset/event/clock/ports/provider request context differs")
    available = [e.id for e in c.evidence]
    selected = set(q.evidence_ids)
    used = {q.property_evidence_id, *(cell.evidence_id for cell in q.cells), *q.review_evidence_ids.values()}
    if len(set(available)) != len(available) or selected - set(available) or used - selected:
        raise ValueError(
            "property/geometry/thermal/review evidence must select unique actual context evidence"
        )
    notes = ["review:" + k for k in REVIEWS if not q.reviews[k] or k not in q.review_evidence_ids]
    if not q.owner_pins:
        notes.append("reviewed owner/property/runtime pins")
    if q.mass_flow_kg_s is None:
        notes.append("known positive mass flow")
    notes += [
        f"{cell.id}:missing:{key}"
        for cell in q.cells
        for key in ("conductance_W_K", "ambient_temperature_K", "heater_into_fluid_W")
        if getattr(cell, key) is None
    ]
    return notes


def _march(q, provider, body):
    state = initial = _state(provider, q.inlet, q)
    rows = []
    heat = dissipated = elevation = x = pressure_work = 0.0
    first_velocity = (
        q.mass_flow_kg_s / initial.density_kg_m3 / (math.pi * q.cells[0].inner_diameter_m ** 2 / 4)
    )
    previous_diameter = q.cells[0].inner_diameter_m
    for cell in q.cells:
        # Area changes require additional momentum/kinetic treatment; no hidden reducer model.
        if cell.inner_diameter_m != previous_diameter:
            raise ValueError("area-changing cell interfaces require a receiving owner momentum model")
        area = math.pi * cell.inner_diameter_m**2 / 4
        volume_flow = q.mass_flow_kg_s / state.density_kg_m3
        hydraulic = hydraulics.liquid_pressure_loss(
            hydraulics.LiquidPressureLoss(
                diameter_m=cell.inner_diameter_m,
                length_m=cell.length_m,
                roughness_m=cell.roughness_m,
                volume_flow_m3_s=volume_flow,
                density_kg_m3=state.density_kg_m3,
                viscosity_pa_s=state.viscosity_pa_s,
                minor_loss_coefficient=cell.minor_loss_coefficient,
                outlet_minus_inlet_elevation_m=cell.outlet_minus_inlet_elevation_m,
                gravity_m_s2=q.gravity_m_s2,
                transition_policy=q.transition_policy,
                friction_method=q.friction_method,
            )
        )
        if hydraulic.status != "computed":
            raise ValueError("existing hydraulic owner withheld cell: " + "; ".join(hydraulic.notes))
        cooled = thermal.cooling(
            thermal.Cooling(
                inlet_temperature_k=state.temperature_k,
                ambient_temperature_k=cell.ambient_temperature_K,
                mass_flow_kg_s=q.mass_flow_kg_s,
                specific_heat_j_kg_k=state.cp_j_kg_k,
                conductance_w_k=cell.conductance_W_K,
            )
        )
        ambient_heat = -cooled.values["heat_loss_w"]
        cell_heat = ambient_heat + cell.heater_into_fluid_W
        pout = state.pressure_pa_abs - hydraulic.values["required_inlet_minus_outlet_pa"]
        hout = (
            state.enthalpy_j_kg
            + cell_heat / q.mass_flow_kg_s
            - q.gravity_m_s2 * cell.outlet_minus_inlet_elevation_m
        )
        outlet = _state(
            provider,
            StateRequest(fluid=q.inlet.fluid, backend=q.inlet.backend, inputs={"P": pout, "Hmass": hout}),
            q,
        )
        vin, vout = volume_flow / area, q.mass_flow_kg_s / outlet.density_kg_m3 / area
        ke_rate = q.mass_flow_kg_s * (vout**2 - vin**2) / 2
        enthalpy_rate = q.mass_flow_kg_s * (outlet.enthalpy_j_kg - state.enthalpy_j_kg)
        potential_rate = q.mass_flow_kg_s * q.gravity_m_s2 * cell.outlet_minus_inlet_elevation_m
        pv_rate = q.mass_flow_kg_s * (
            outlet.pressure_pa_abs / outlet.density_kg_m3 - state.pressure_pa_abs / state.density_kg_m3
        )
        u_rate = q.mass_flow_kg_s * (
            (outlet.enthalpy_j_kg - outlet.pressure_pa_abs / outlet.density_kg_m3)
            - (state.enthalpy_j_kg - state.pressure_pa_abs / state.density_kg_m3)
        )
        reduced_residual = enthalpy_rate + potential_rate - cell_heat
        decomposition_residual = u_rate + pv_rate - enthalpy_rate
        complete_residual = reduced_residual + ke_rate
        limit = q.energy_balance_absolute_W + q.balance_relative_tolerance * max(
            abs(enthalpy_rate), abs(potential_rate), abs(cell_heat)
        )
        predicted_temperature = (
            cooled.values["outlet_temperature_k"]
            + (
                cell.heater_into_fluid_W / q.mass_flow_kg_s
                - q.gravity_m_s2 * cell.outlet_minus_inlet_elevation_m
            )
            / state.cp_j_kg_k
        )
        temperature_error = outlet.temperature_k - predicted_temperature
        heat += cell_heat
        dissipated += hydraulic.values["dissipated_power_w"]
        elevation += cell.outlet_minus_inlet_elevation_m
        pressure_work += pv_rate
        x += cell.length_m
        valid = (
            abs(reduced_residual) <= limit
            and abs(decomposition_residual) <= limit
            and abs(ke_rate) <= q.maximum_omitted_kinetic_energy_rate_W
            and abs(temperature_error) <= q.maximum_cell_temperature_closure_error_K
        )
        rows.append(
            {
                "cell_id": cell.id,
                "outlet_distance_m": x,
                "inlet": state.model_dump(mode="json"),
                "outlet": outlet.model_dump(mode="json"),
                "hydraulic_owner": hydraulic.model_dump(mode="json"),
                "thermal_owner": cooled.model_dump(mode="json"),
                "mass_flow_in_kg_s": q.mass_flow_kg_s,
                "mass_flow_out_kg_s": q.mass_flow_kg_s,
                "mass_flux_residual_kg_s": 0.0,
                "ambient_heat_into_fluid_W": ambient_heat,
                "heater_into_fluid_W": cell.heater_into_fluid_W,
                "total_heat_into_fluid_W": cell_heat,
                "enthalpy_flux_change_W": enthalpy_rate,
                "potential_energy_flux_change_W": potential_rate,
                "kinetic_energy_flux_change_W": ke_rate,
                "pressure_flow_work_flux_change_W": pv_rate,
                "internal_energy_flux_change_W": u_rate,
                "internal_plus_flow_work_residual_W": decomposition_residual,
                "reduced_energy_residual_W": reduced_residual,
                "full_energy_residual_W": complete_residual,
                "energy_balance_limit_W": limit,
                "constant_cp_temperature_prediction_K": predicted_temperature,
                "temperature_closure_error_K": temperature_error,
                "within_reviewed_budget": valid,
            }
        )
        state = outlet
    final_velocity = (
        q.mass_flow_kg_s / state.density_kg_m3 / (math.pi * q.cells[-1].inner_diameter_m ** 2 / 4)
    )
    total_ke = q.mass_flow_kg_s * (final_velocity**2 - first_velocity**2) / 2
    total_reduced = (
        q.mass_flow_kg_s * (state.enthalpy_j_kg - initial.enthalpy_j_kg + q.gravity_m_s2 * elevation) - heat
    )
    total_limit = q.energy_balance_absolute_W + q.balance_relative_tolerance * max(
        abs(heat), abs(q.mass_flow_kg_s * (state.enthalpy_j_kg - initial.enthalpy_j_kg))
    )
    total_enthalpy = q.mass_flow_kg_s * (state.enthalpy_j_kg - initial.enthalpy_j_kg)
    total_internal = q.mass_flow_kg_s * (
        (state.enthalpy_j_kg - state.pressure_pa_abs / state.density_kg_m3)
        - (initial.enthalpy_j_kg - initial.pressure_pa_abs / initial.density_kg_m3)
    )
    total_decomposition = total_internal + pressure_work - total_enthalpy
    valid = (
        all(r["within_reviewed_budget"] for r in rows)
        and abs(total_reduced) <= total_limit
        and abs(total_ke) <= q.maximum_omitted_kinetic_energy_rate_W
        and abs(total_decomposition) <= total_limit
    )
    body.update(
        status="computed_diagnostic" if valid else "diagnostic_rejected",
        notes=[]
        if valid
        else ["cell/global reduced-energy, omitted-kinetic or constant-property closure budget failed"],
        cells=rows,
        values={
            "outlet": state.model_dump(mode="json"),
            "net_inlet_minus_outlet_pressure_Pa": initial.pressure_pa_abs - state.pressure_pa_abs,
            "total_heat_into_fluid_W": heat,
            "summed_native_dissipated_power_W": dissipated,
            "pressure_flow_work_flux_change_W": pressure_work,
            "global_enthalpy_flux_change_W": total_enthalpy,
            "global_internal_energy_flux_change_W": total_internal,
            "global_internal_plus_flow_work_residual_W": total_decomposition,
            "global_reduced_energy_residual_W": total_reduced,
            "global_full_energy_residual_W": total_reduced + total_ke,
            "global_omitted_kinetic_energy_rate_W": total_ke,
            "initial_viscosity_Pa_s": initial.viscosity_pa_s,
            "final_viscosity_Pa_s": state.viscosity_pa_s,
        },
    )


def evaluate_heated_transport(request, context, *, property_provider=None):
    q = HeatedTransportRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": "pipeline_integrity.heated_transport.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "notes": _policy(q, c),
        "native_called": False,
        "provider": None,
        "cells": None,
        "values": None,
        "physical_validation_claim": False,
        "whole_heated_oil_complete": False,
        "qualification": "steady Newtonian single-phase liquid; inlet-frozen segmented partial coupling; no crude pseudo-fluid or transient model",
    }
    if not body["notes"]:
        before = native_backend_metadata(q, property_provider=property_provider)
        if before != q.owner_pins:
            raise ValueError("reviewed property/hydraulic/thermal owner pins differ")
        body["provider"] = before
        try:
            body["native_called"] = True
            _march(q, _provider(q, property_provider), body)
            json_value(body)
        except (ValueError, RuntimeError, ArithmeticError, TypeError) as exc:
            body.update(
                status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"], cells=None, values=None
            )
        if native_backend_metadata(q, property_provider=property_provider) != before:
            raise ValueError("property/owner reference changed during marching")
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def verify_heated_transport_record(record, *, request=None, context=None, property_provider=None):
    try:
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("heated transport receipt changed")
        q = HeatedTransportRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and request != q) or (context is not None and context != c):
            raise ValueError("caller request/context differs")
        if evaluate_heated_transport(q, c, property_provider=property_provider) != record:
            raise ValueError("actual native/property/ledger replay differs")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}
