"""Source-bound adapter for owner-supplied multi-product batch transport results."""

import math
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest


class BatchComposition(Contract):
    fraction_by_component: dict[Name, Fraction]

    @model_validator(mode="after")
    def normalized(self):
        if not self.fraction_by_component or not math.isclose(
            math.fsum(self.fraction_by_component.values()), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("batch component fractions must sum to one")
        return self


class BatchInletStep(Contract):
    time_s: Nonnegative
    volume_flow_m3_s: Positive
    composition: BatchComposition
    temperature_K: Positive
    pressure_Pa_abs: Positive


class BatchTransportRequest(Contract):
    id: Name
    asset_id: Name
    location_id: Name
    clock_id: Name
    route_length_m: Positive
    cross_section_area_m2: Positive
    duration_s: Positive
    component_ids: tuple[Name, ...] = Field(min_length=2, max_length=64)
    initial_composition: BatchComposition
    inlet_steps: tuple[BatchInletStep, ...] = Field(min_length=1, max_length=100000)
    property_basis_id: Name
    dispersion_model_id: Name
    geometry_evidence_id: Name
    boundary_evidence_id: Name
    property_evidence_id: Name
    maximum_time_points: int = Field(default=10000, ge=2, le=1000000)
    maximum_position_points: int = Field(default=10000, ge=2, le=1000000)
    absolute_mass_balance_tolerance_kg: Nonnegative
    relative_mass_balance_tolerance: Nonnegative
    maximum_mass_numerical_adjustment_kg: Nonnegative
    thermal_mode: Literal["prescribed_temperature", "energy_coupled"]
    thermal_model_id: Name | None = None
    thermal_evidence_id: Name | None = None
    absolute_energy_balance_tolerance_J: Nonnegative | None = None
    relative_energy_balance_tolerance: Nonnegative | None = None
    maximum_energy_numerical_adjustment_J: Nonnegative | None = None

    @model_validator(mode="after")
    def scope(self):
        components = set(self.component_ids)
        if len(components) != len(self.component_ids):
            raise ValueError("batch component IDs must be distinct")
        compositions = (self.initial_composition, *(step.composition for step in self.inlet_steps))
        if any(set(item.fraction_by_component) != components for item in compositions):
            raise ValueError("every batch composition must cover the exact component set")
        times = [step.time_s for step in self.inlet_steps]
        if times[0] != 0 or any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("inlet steps require a zero-origin strictly increasing elapsed clock")
        if times[-1] >= self.duration_s:
            raise ValueError("the last inlet change must precede the analysis horizon")
        thermal_fields = (
            self.thermal_model_id,
            self.thermal_evidence_id,
            self.absolute_energy_balance_tolerance_J,
            self.relative_energy_balance_tolerance,
            self.maximum_energy_numerical_adjustment_J,
        )
        if self.thermal_mode == "energy_coupled" and any(value is None for value in thermal_fields):
            raise ValueError("energy-coupled transport requires model, evidence and energy tolerances")
        if self.thermal_mode == "prescribed_temperature" and any(
            value is not None for value in thermal_fields
        ):
            raise ValueError("prescribed-temperature transport cannot claim unused energy settings")
        return self


class BatchTransportProvider(Protocol):
    identity: dict

    def evaluate(self, request: BatchTransportRequest, context: RunContext) -> dict: ...


def evaluate_batch_transport(request, context, *, provider: BatchTransportProvider):
    """Validate an owner result; no transport equation or property model is copied here."""
    q = BatchTransportRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("batch request asset/clock differs from the actual context")
    evidence = {item.id for item in c.evidence}
    required = {q.geometry_evidence_id, q.boundary_evidence_id, q.property_evidence_id}
    if q.thermal_evidence_id is not None:
        required.add(q.thermal_evidence_id)
    if len(evidence) != len(c.evidence) or not required <= evidence:
        raise ValueError("batch geometry, boundary and property evidence must be actual context members")
    identity = json_value(provider.identity)
    if (
        not isinstance(identity, dict)
        or not all(identity.get(key) for key in ("id", "version", "source_digest"))
        or not isinstance(identity.get("source_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", identity["source_digest"]) is None
    ):
        raise ValueError("batch provider needs explicit id, version and source digest")
    native = json_value(provider.evaluate(q, c))
    if not isinstance(native, dict) or native.get("request_digest") != digest(q):
        raise ValueError("batch provider result differs from the exact request")
    if (
        native.get("field_layout") != "time_major_position_minor"
        or native.get("mass_ledger_sign_convention")
        != "initial + inlet - outlet - final + numerical_adjustment = residual"
    ):
        raise ValueError("batch provider field layout or mass-ledger sign convention is unresolved")
    times = native.get("time_s")
    positions = native.get("position_m")
    fractions = native.get("fraction_by_component")
    if (
        not isinstance(times, list)
        or not isinstance(positions, list)
        or not 2 <= len(times) <= q.maximum_time_points
        or not 2 <= len(positions) <= q.maximum_position_points
        or times[0] != 0
        or times[-1] != q.duration_s
        or positions[0] != 0
        or positions[-1] != q.route_length_m
        or any(not math.isfinite(v) for v in (*times, *positions))
        or any(b <= a for a, b in zip(times, times[1:], strict=False))
        or any(b <= a for a, b in zip(positions, positions[1:], strict=False))
    ):
        raise ValueError("batch provider returned an invalid bounded time/position grid")
    expected = len(times) * len(positions)
    if not isinstance(fractions, dict) or set(fractions) != set(q.component_ids):
        raise ValueError("batch provider component field differs from the request")
    for values in fractions.values():
        if (
            not isinstance(values, list)
            or len(values) != expected
            or any(
                not isinstance(v, (int, float))
                or isinstance(v, bool)
                or not math.isfinite(v)
                or not 0 <= v <= 1
                for v in values
            )
        ):
            raise ValueError("batch provider returned invalid component fractions")
    for index in range(expected):
        if not math.isclose(
            math.fsum(fractions[key][index] for key in q.component_ids), 1.0, rel_tol=0, abs_tol=1e-10
        ):
            raise ValueError("batch provider component fractions do not close")
    for name in ("density_kg_m3", "dynamic_viscosity_Pa_s", "temperature_K", "pressure_Pa_abs"):
        values = native.get(name)
        if (
            not isinstance(values, list)
            or len(values) != expected
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
                for value in values
            )
        ):
            raise ValueError(f"batch provider returned invalid {name} field")
    ledger = native.get("mass_ledger_kg")
    keys = {"initial", "inlet", "outlet", "final", "numerical_adjustment"}
    if (
        not isinstance(ledger, dict)
        or set(ledger) != keys
        or any(
            not isinstance(ledger[key], (int, float))
            or isinstance(ledger[key], bool)
            or not math.isfinite(ledger[key])
            for key in keys
        )
    ):
        raise ValueError("batch provider requires a finite signed mass ledger")
    residual = (
        ledger["initial"]
        + ledger["inlet"]
        - ledger["outlet"]
        - ledger["final"]
        + ledger["numerical_adjustment"]
    )
    scale = max(abs(ledger["initial"]) + abs(ledger["inlet"]), 1.0)
    total_balance_ok = (
        abs(residual) <= q.absolute_mass_balance_tolerance_kg + q.relative_mass_balance_tolerance * scale
    )
    total_adjustment_ok = abs(ledger["numerical_adjustment"]) <= q.maximum_mass_numerical_adjustment_kg
    component_ledgers = native.get("component_mass_ledger_kg")
    if not isinstance(component_ledgers, dict) or set(component_ledgers) != set(q.component_ids):
        raise ValueError("batch provider requires one mass ledger for every component")
    component_residuals = {}
    component_balance_ok = True
    component_adjustment_ok = True
    for component_id, component_ledger in component_ledgers.items():
        if (
            not isinstance(component_ledger, dict)
            or set(component_ledger) != keys
            or any(
                not isinstance(component_ledger[key], (int, float))
                or isinstance(component_ledger[key], bool)
                or not math.isfinite(component_ledger[key])
                for key in keys
            )
        ):
            raise ValueError("batch provider returned an invalid component mass ledger")
        component_residual = (
            component_ledger["initial"]
            + component_ledger["inlet"]
            - component_ledger["outlet"]
            - component_ledger["final"]
            + component_ledger["numerical_adjustment"]
        )
        component_scale = max(abs(component_ledger["initial"]) + abs(component_ledger["inlet"]), 1.0)
        component_residuals[component_id] = component_residual
        component_balance_ok = component_balance_ok and abs(component_residual) <= (
            q.absolute_mass_balance_tolerance_kg + q.relative_mass_balance_tolerance * component_scale
        )
        component_adjustment_ok = (
            component_adjustment_ok
            and abs(component_ledger["numerical_adjustment"]) <= q.maximum_mass_numerical_adjustment_kg
        )
    component_sum_matches_total = all(
        math.isclose(
            math.fsum(component_ledgers[component_id][key] for component_id in q.component_ids),
            ledger[key],
            rel_tol=q.relative_mass_balance_tolerance,
            abs_tol=q.absolute_mass_balance_tolerance_kg,
        )
        for key in keys
    )
    energy_residual = None
    energy_balance_ok = None
    energy_adjustment_ok = None
    if q.thermal_mode == "energy_coupled":
        energy_ledger = native.get("energy_ledger_J")
        energy_keys = {
            "initial",
            "inlet",
            "outlet",
            "final",
            "heat_transfer",
            "shaft_work",
            "numerical_adjustment",
        }
        if (
            native.get("energy_ledger_sign_convention")
            != "initial + inlet - outlet - final + heat_transfer + shaft_work + numerical_adjustment = residual"
            or not isinstance(energy_ledger, dict)
            or set(energy_ledger) != energy_keys
            or any(
                not isinstance(energy_ledger[key], (int, float))
                or isinstance(energy_ledger[key], bool)
                or not math.isfinite(energy_ledger[key])
                for key in energy_keys
            )
        ):
            raise ValueError("energy-coupled provider requires a finite signed energy ledger")
        energy_residual = (
            energy_ledger["initial"]
            + energy_ledger["inlet"]
            - energy_ledger["outlet"]
            - energy_ledger["final"]
            + energy_ledger["heat_transfer"]
            + energy_ledger["shaft_work"]
            + energy_ledger["numerical_adjustment"]
        )
        energy_scale = max(
            abs(energy_ledger["initial"])
            + abs(energy_ledger["inlet"])
            + abs(energy_ledger["heat_transfer"])
            + abs(energy_ledger["shaft_work"]),
            1.0,
        )
        energy_balance_ok = abs(energy_residual) <= (
            q.absolute_energy_balance_tolerance_J + q.relative_energy_balance_tolerance * energy_scale
        )
        energy_adjustment_ok = (
            abs(energy_ledger["numerical_adjustment"]) <= q.maximum_energy_numerical_adjustment_J
        )
    elif "energy_ledger_J" in native or "energy_ledger_sign_convention" in native:
        raise ValueError("prescribed-temperature result cannot imply an unchecked energy balance")
    mass_balance_ok = total_balance_ok and component_balance_ok and component_sum_matches_total
    numerical_adjustments_ok = (
        total_adjustment_ok and component_adjustment_ok and energy_adjustment_ok is not False
    )
    conservation_ok = mass_balance_ok and energy_balance_ok is not False and numerical_adjustments_ok
    body = {
        "schema": "pipeline_integrity.batch_transport.v1",
        "status": "computed" if conservation_ok else "withheld",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "provider": identity,
        "native": native,
        "mass_balance_residual_kg": residual,
        "component_mass_balance_residual_kg": component_residuals,
        "total_mass_balance_within_tolerance": total_balance_ok,
        "component_mass_balances_within_tolerance": component_balance_ok,
        "component_ledger_sum_matches_total": component_sum_matches_total,
        "mass_balance_within_tolerance": mass_balance_ok,
        "total_mass_numerical_adjustment_within_limit": total_adjustment_ok,
        "component_mass_numerical_adjustments_within_limit": component_adjustment_ok,
        "energy_balance_residual_J": energy_residual,
        "energy_balance_within_tolerance": energy_balance_ok,
        "energy_numerical_adjustment_within_limit": energy_adjustment_ok,
        "numerical_adjustments_within_limits": numerical_adjustments_ok,
        "conservation_within_tolerance": conservation_ok,
        "constant_density_or_water_quality_assumed": False,
        "field_validation_claim": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return {**body, "record_digest": digest(body)}


def verify_batch_transport_record(record, *, request, context, provider):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("batch transport record seal changed")
    if evaluate_batch_transport(request, context, provider=provider) != record:
        raise ValueError("batch transport provider result or boundary differs on replay")
    return True
