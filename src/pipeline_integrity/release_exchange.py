"""Bind owner-computed accidental release histories to explicit clocks and checked property identities."""

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, digest, json_value
from .equipment_exchange import EquipmentBuildReport


class PipeAccidentRequest(Contract):
    event_id: Name
    port_id: Name
    clock_id: Name
    start_time_s: Nonnegative
    source_id: Name
    initial_pressure_pa_abs: Positive
    initial_temperature_k: Positive
    initial_vapour_mass_fraction: Fraction | None = None
    wall_temperature_k: Positive
    ambient_pressure_pa_abs: Positive
    ambient_temperature_k: Positive
    failure_opening_diameter_m: Positive
    discharge_coefficient: Positive = Field(le=1)
    horizon_s: Positive
    time_step_s: Positive
    position_enu_m: tuple[float, float, float]
    direction_enu: tuple[float, float, float]
    isolation_time_s: Nonnegative | None = None
    post_isolation_observation_s: Positive | None = None
    post_pressure_equalization_observation_s: Nonnegative = 0.0
    droplet_flight_time_s: Nonnegative | None = None
    impact_position_enu_m: tuple[float, float, float] | None = None
    droplet_diameter_m: Positive | None = None
    maximum_intervals: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def explicit_boundary(self):
        if self.initial_pressure_pa_abs <= self.ambient_pressure_pa_abs:
            raise ValueError("the outward source pressure must exceed the ambient pressure")
        if self.time_step_s > self.horizon_s:
            raise ValueError("the requested time step exceeds the observation horizon")
        nominal_intervals = self.horizon_s / self.time_step_s
        if not math.isfinite(nominal_intervals) or math.ceil(nominal_intervals) > self.maximum_intervals:
            raise ValueError("the nominal history exceeds the declared interval budget")
        if not math.isfinite(self.start_time_s + self.horizon_s) or (
            self.start_time_s + self.time_step_s == self.start_time_s
        ):
            raise ValueError("the requested clock increment is not representable")
        if not math.isclose(sum(x * x for x in self.direction_enu), 1.0, rel_tol=0, abs_tol=1e-10):
            raise ValueError("provide an explicit unit ENU direction")
        if self.isolation_time_s is None:
            if self.post_isolation_observation_s is not None:
                raise ValueError("an isolation observation requires an explicit isolation time")
        elif self.post_isolation_observation_s is None or (
            self.isolation_time_s + self.post_isolation_observation_s > self.horizon_s
        ):
            raise ValueError("isolation needs a positive observation tail inside the horizon")
        fate = (self.droplet_flight_time_s, self.impact_position_enu_m, self.droplet_diameter_m)
        if any(x is not None for x in fate) and any(x is None for x in fate):
            raise ValueError("provide all ground-liquid fate fields together")
        return self


class AccidentHistory(Contract):
    schema_version: Literal["pipeline_integrity.accident_history.v1"] = (
        "pipeline_integrity.accident_history.v1"
    )
    status: Literal["exchange_checked", "withheld"]
    request: PipeAccidentRequest
    build: EquipmentBuildReport
    provider_request: dict[str, Any]
    provider_response: dict[str, Any]
    audit: dict[str, Any]
    issues: tuple[str, ...]
    provider_response_digest: Name
    envelope_digest: Name
    physical_actuation_or_field_validation: Literal[False] = False

    def check_integrity(self):
        body = self.model_dump(mode="json", exclude={"envelope_digest"})
        if (
            digest(body) != self.envelope_digest
            or digest(self.provider_response) != self.provider_response_digest
        ):
            raise ValueError("the retained accident history or binding metadata changed after verification")


def _number(mapping, key, *, nonnegative=False):
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    if nonnegative and value < 0:
        raise ValueError(f"{key} must be nonnegative")
    return float(value)


def _audit_response(response, build, request, properties, provider_request):
    issues, audit = (
        [],
        {
            "integrated_internal_energy_and_heat_ledger_supplied": False,
            "isolation_boundary": (
                "prescribed_instantaneous_outlet_closure" if request.isolation_time_s is not None else None
            ),
        },
    )
    if response.get("provider_export_schema") != "prism.external_accident_history.v1":
        issues.append("unexpected_provider_schema")
    if response.get("provider_source_digest") != provider_request["provider_source_digest"]:
        issues.append("provider_source_digest_changed")
    if response.get("provider_state_snapshot_digest") != provider_request["state_snapshot_digest"]:
        issues.append("provider_snapshot_digest_changed")
    data = response.get("data")
    if not isinstance(data, dict):
        return [*issues, "missing_provider_data"], audit
    for key, expected in (
        ("component_id", build.asset_id),
        ("port_id", request.port_id),
        ("event_id", request.event_id),
        ("event_kind", "accidental_leak"),
        ("fluid", build.fluid),
    ):
        if data.get(key) != expected:
            issues.append(f"provider_{key}_mismatch")
    basis = data.get("property_basis")
    if basis is not None and (
        not isinstance(basis, dict)
        or basis.get("fluid") != build.fluid
        or basis.get("backend") != "CoolProp-HEOS"
        or basis.get("energy_reference") != f"CoolProp-HEOS:{build.fluid}:native"
    ):
        issues.append("provider_property_basis_mismatch")
    if data.get("ambient_pressure_pa_abs") != request.ambient_pressure_pa_abs:
        issues.append("provider_ambient_pressure_mismatch")
    rows = data.get("steps")
    if not isinstance(rows, list) or not rows or len(rows) > request.maximum_intervals:
        return [*issues, "missing_or_over_budget_history"], audit
    try:
        duration = _number(data, "duration_s", nonnegative=True)
        times = [_number(row, "time_s", nonnegative=True) for row in rows]
        if (
            duration <= 0
            or duration > request.horizon_s + 1e-10
            or times[0] != 0
            or times[-1] >= duration
            or any(b <= a for a, b in zip(times, times[1:], strict=False))
        ):
            raise ValueError("invalid elapsed release clock")
        phase = data.get("phase_basis")
        if phase not in {"gas", "liquid", "two_phase"}:
            raise ValueError("unsupported provider phase basis")
        if request.isolation_time_s is not None and not any(
            math.isclose(t, request.isolation_time_s, rel_tol=0, abs_tol=1e-12) for t in times
        ):
            issues.append("provider_clock_does_not_retain_isolation_event")
        mass_key = "mass_flow_kg_s" if phase == "gas" else "total_mass_flow_kg_s"
        if any(mass_key not in row for row in rows):
            return [*issues, "provider_phase_history_shape_mismatch"], audit
        mass, enthalpy, max_density_relative_error = 0.0, 0.0, 0.0
        for row, start, end in zip(rows, times, [*times[1:], duration], strict=True):
            rate = _number(row, mass_key, nonnegative=True)
            specific_h = _number(row, "specific_enthalpy_j_kg")
            mass += rate * (end - start)
            enthalpy += rate * specific_h * (end - start)
            if phase == "gas":
                pressure, temperature, rho = (
                    _number(row, key) for key in ("pressure_pa_abs", "temperature_k", "density_kg_m3")
                )
                expected = properties.from_ph(pressure, specific_h)
                if expected.quality is not None or expected.phase not in {
                    "gas",
                    "supercritical",
                    "supercritical_gas",
                }:
                    issues.append("gas_exit_enthalpy_resolves_to_non_gas")
            else:
                gas = _number(row, "gas_mass_flow_kg_s", nonnegative=True)
                ground = _number(row, "ground_liquid_mass_flow_kg_s", nonnegative=True)
                airborne = _number(row, "airborne_liquid_mass_flow_kg_s", nonnegative=True)
                if not math.isclose(rate, gas + ground + airborne, rel_tol=1e-8, abs_tol=1e-12):
                    issues.append("liquid_mass_partition_mismatch")
                if ground > 1e-12:
                    if request.impact_position_enu_m is None:
                        issues.append("ground_liquid_fate_not_supplied")
                    for fate in row.get("droplet_class_outcomes", []):
                        if (
                            request.impact_position_enu_m is not None
                            and tuple(fate.get("impact_position_m", ())) != request.impact_position_enu_m
                        ):
                            issues.append("provider_changed_impact_position")
                if gas <= 1e-12:
                    continue
                pressure, temperature, rho = (
                    _number(row, key)
                    for key in ("gas_pressure_pa_abs", "gas_temperature_k", "gas_density_kg_m3")
                )
                mixture = properties.from_ph(pressure, specific_h)
                expected = properties.saturated_vapor(pressure) if mixture.quality is not None else mixture
                if expected.phase not in {"gas", "supercritical", "supercritical_gas", "twophase"}:
                    issues.append("positive_gas_branch_resolves_to_liquid")
            if not math.isclose(pressure, request.ambient_pressure_pa_abs, rel_tol=1e-8, abs_tol=1e-5):
                issues.append("resolved_exit_is_not_at_declared_ambient_pressure")
            rho_error = abs(rho - expected.density_kg_m3) / expected.density_kg_m3
            max_density_relative_error = max(max_density_relative_error, rho_error)
            if rho_error > 1e-7 or not math.isclose(
                temperature, expected.temperature_K, rel_tol=1e-7, abs_tol=1e-5
            ):
                issues.append("gas_branch_property_state_mismatch")
        declared_mass = _number(data, "cumulative_mass_out_kg", nonnegative=True)
        declared_h = _number(data, "cumulative_static_enthalpy_out_j")
        if not math.isclose(mass, declared_mass, rel_tol=1e-8, abs_tol=1e-10):
            issues.append("left_held_mass_integral_mismatch")
        if not math.isclose(enthalpy, declared_h, rel_tol=1e-8, abs_tol=1e-6):
            issues.append("left_held_enthalpy_integral_mismatch")
        meta = data.get("provider_meta", {})
        initial = _number(meta, "initial_mass_kg", nonnegative=True)
        final = _number(meta, "final_mass_kg", nonnegative=True)
        available = _number(data, "available_mass_kg", nonnegative=True)
        residual = final + declared_mass - initial
        tolerance = max(1e-10, initial * 1e-9)
        if abs(residual) > tolerance or not math.isclose(initial, available, rel_tol=1e-9, abs_tol=1e-10):
            issues.append("finite_inventory_mass_balance_mismatch")
        if "cumulative_mass_in_kg" in meta and _number(meta, "cumulative_mass_in_kg") != 0:
            issues.append("single_outlet_export_has_unexpected_inflow")
        audit.update(
            interval_count=len(rows),
            duration_s=duration,
            integrated_mass_kg=mass,
            integrated_static_enthalpy_j=enthalpy,
            remaining_mass_kg=final,
            inventory_residual_kg=residual,
            inventory_tolerance_kg=tolerance,
            maximum_gas_density_relative_disagreement=max_density_relative_error,
            termination_basis_owner=data.get("termination_basis"),
            retained_context_start_time_s=request.start_time_s,
            retained_context_end_time_s=request.start_time_s + duration,
            provider_time_basis="elapsed_from_declared_start",
            property_check_scope="same_EOS_state_consistency_not_independent_physical_validation",
        )
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        issues.append(f"invalid_provider_history: {error}")
    return list(dict.fromkeys(issues)), audit


def _export_pipe_accident(client, handle, request):
    if not isinstance(request, PipeAccidentRequest):
        raise TypeError("PipeAccidentRequest is required")
    if handle.report.model_id != "dynamic_hem_pipe":
        raise ValueError("this inspected accident protocol requires dynamic_hem_pipe")
    if request.clock_id != handle.report.context.clock_id:
        raise ValueError("accident and equipment context clocks differ")
    if request.initial_vapour_mass_fraction is not None:
        saturated = client.properties.saturated_liquid(request.initial_pressure_pa_abs)
        if not math.isclose(saturated.temperature_K, request.initial_temperature_k, abs_tol=1e-5, rel_tol=0):
            raise ValueError("the declared quality and initial temperature contradict the saturation state")
    payload = request.model_dump(
        mode="json",
        exclude={
            "clock_id",
            "start_time_s",
            "source_id",
            "maximum_intervals",
            "initial_vapour_mass_fraction",
            "impact_position_enu_m",
        },
        exclude_none=True,
    )
    payload.update(component_id=handle.report.asset_id, provider_source_digest=digest(handle.report))
    payload["state_snapshot_digest"] = digest({"build": handle.report, "accident_initialization": request})
    if request.initial_vapour_mass_fraction is not None:
        payload["initial_quality"] = request.initial_vapour_mass_fraction
    if request.impact_position_enu_m is not None:
        payload["impact_position_m"] = list(request.impact_position_enu_m)
    response = json_value(handle.native.export_accident_history(payload))
    if not isinstance(response, dict):
        raise ValueError("the owner accident exporter must return a JSON object")
    issues, audit = _audit_response(response, handle.report, request, client.properties, payload)
    body = {
        "schema_version": "pipeline_integrity.accident_history.v1",
        "status": "withheld" if issues else "exchange_checked",
        "request": request.model_dump(mode="json"),
        "build": handle.report.model_dump(mode="json"),
        "provider_request": payload,
        "provider_response": response,
        "audit": audit,
        "issues": issues,
        "provider_response_digest": digest(response),
        "physical_actuation_or_field_validation": False,
    }
    return AccidentHistory.model_validate_json(
        json.dumps({**body, "envelope_digest": digest(body)}, allow_nan=False)
    )


@dataclass(frozen=True)
class ReleaseHandoff:
    native: Any
    history: AccidentHistory
    native_digest: str

    def record(self):
        self.history.check_integrity()
        if digest(self.native.model_dump(mode="json")) != self.native_digest:
            raise ValueError("the receiving release packet changed after its handoff")
        return {
            "schema": "pipeline_integrity.release_handoff.v1",
            "native_packet": self.native.model_dump(mode="json"),
            "native_packet_digest": self.native_digest,
            "history_envelope_digest": self.history.envelope_digest,
            "context": self.history.build.context.model_dump(mode="json"),
            "provider_time_origin_in_context_s": self.history.request.start_time_s,
            "property_basis_id": self.history.build.property_basis,
            "initiating_frequency_or_consequence_calculated": False,
            "physical_actuation_or_field_validation": False,
        }


def prism_release(history: AccidentHistory, *, gas_adapter=None, liquid_adapter=None):
    """Call PRISM's existing resolved-release builder without repairing a rejected source history."""
    if not isinstance(history, AccidentHistory):
        raise TypeError("AccidentHistory is required")
    history.check_integrity()
    if history.status != "exchange_checked":
        raise ValueError(f"history verification withheld: {', '.join(history.issues)}")
    phase = history.provider_response["data"]["phase_basis"]
    if phase == "gas":
        if gas_adapter is None:
            from lh2_tno.equipment_release_adapter import build_resolved_gas_release_from_provider_response

            gas_adapter = build_resolved_gas_release_from_provider_response
        adapter = gas_adapter
    else:
        if liquid_adapter is None:
            from lh2_tno.equipment_release_adapter import build_resolved_liquid_release_from_provider_response

            liquid_adapter = build_resolved_liquid_release_from_provider_response
        adapter = liquid_adapter
    request = history.request
    native = adapter(
        history.provider_response,
        component_id=history.build.asset_id,
        port_id=request.port_id,
        event_id=request.event_id,
        provider_source_digest=history.provider_request["provider_source_digest"],
        upstream_service_provenance=request.source_id,
        provider_boundary_reference=f"accident-envelope:{history.envelope_digest}",
        input_origin="synthetic" if history.build.context.origin == "synthetic" else "model_prediction",
        position_enu_m=request.position_enu_m,
        direction_enu=request.direction_enu,
        ambient_pressure_pa_abs=request.ambient_pressure_pa_abs,
        ambient_temperature_k=request.ambient_temperature_k,
        failure_opening_diameter_m=request.failure_opening_diameter_m,
        provider_result_digest=history.provider_response_digest,
    )
    if (
        native.provider_export_schema != "prism.resolved_release.v1"
        or native.property_basis.fluid != history.build.fluid
        or native.component_id != history.build.asset_id
        or native.port_id != request.port_id
        or native.event_id != request.event_id
        or native.provider_result_digest != history.provider_response_digest
        or native.provider_source_digest != history.provider_request["provider_source_digest"]
        or native.property_basis.energy_reference != f"CoolProp-HEOS:{history.build.fluid}:native"
        or not math.isclose(native.duration_s, history.audit["duration_s"], rel_tol=0, abs_tol=1e-12)
        or not math.isclose(
            native.cumulative_mass_out_kg, history.audit["integrated_mass_kg"], rel_tol=1e-8, abs_tol=1e-10
        )
    ):
        raise ValueError("the receiving packet changed the release identity or property binding")
    return ReleaseHandoff(
        native=native,
        history=history.model_copy(deep=True),
        native_digest=digest(native.model_dump(mode="json")),
    )
