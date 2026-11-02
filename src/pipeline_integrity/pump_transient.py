"""Source-bound boundary for owner-supplied pump coastdown responses."""

import math
import re
from pathlib import Path
from typing import Protocol

from pydantic import Field

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest


class PumpCoastdownRequest(Contract):
    id: Name
    asset_id: Name
    pump_id: Name
    clock_id: Name
    duration_s: Positive
    density_kg_m3: Positive
    gravity_m_s2: Positive = 9.80665
    rotor_inertia_kg_m2: Positive
    initial_angular_speed_rad_s: Nonnegative
    pump_model_id: Name
    drive_model_id: Name
    hydraulic_evidence_id: Name
    rotor_evidence_id: Name
    drive_evidence_id: Name
    maximum_points: int = Field(default=10000, ge=2, le=1000000)
    absolute_power_consistency_tolerance_W: Nonnegative
    relative_power_consistency_tolerance: Nonnegative
    absolute_energy_balance_tolerance_J: Nonnegative
    relative_energy_balance_tolerance: Nonnegative
    maximum_energy_numerical_adjustment_J: Nonnegative


class PumpTransientProvider(Protocol):
    identity: dict

    def evaluate(self, request: PumpCoastdownRequest, context: RunContext) -> dict: ...


def _finite_series(native, name, count, *, nonnegative=False):
    values = native.get(name)
    if (
        not isinstance(values, list)
        or len(values) != count
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or nonnegative
            and value < 0
            for value in values
        )
    ):
        raise ValueError(f"pump provider returned an invalid {name} series")
    return values


def evaluate_pump_coastdown(request, context, *, provider: PumpTransientProvider):
    """Validate owner dynamics and energy accounting; do not solve pump rotation or MOC here."""
    q = PumpCoastdownRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("pump request asset/clock differs from the actual context")
    evidence = [item.id for item in c.evidence]
    required = {q.hydraulic_evidence_id, q.rotor_evidence_id, q.drive_evidence_id}
    if len(evidence) != len(set(evidence)) or not required <= set(evidence):
        raise ValueError("pump hydraulic, rotor and drive evidence must be actual context members")
    identity = json_value(provider.identity)
    if (
        not isinstance(identity, dict)
        or not all(identity.get(key) for key in ("id", "version", "source_digest"))
        or not isinstance(identity.get("source_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", identity["source_digest"]) is None
    ):
        raise ValueError("pump transient provider needs id, version and source digest")
    native = json_value(provider.evaluate(q, c))
    if not isinstance(native, dict) or native.get("request_digest") != digest(q):
        raise ValueError("pump provider result differs from the exact request")
    if (
        native.get("energy_ledger_sign_convention")
        != "initial_rotor + drive - hydraulic - dissipated - final_rotor + numerical_adjustment = residual"
    ):
        raise ValueError("pump provider energy-ledger sign convention is unresolved")
    times = native.get("time_s")
    if (
        not isinstance(times, list)
        or not 2 <= len(times) <= q.maximum_points
        or times[0] != 0
        or times[-1] != q.duration_s
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in times)
        or any(not math.isfinite(value) for value in times)
        or any(b <= a for a, b in zip(times, times[1:], strict=False))
    ):
        raise ValueError("pump provider returned an invalid bounded elapsed clock")
    count = len(times)
    speed = _finite_series(native, "angular_speed_rad_s", count, nonnegative=True)
    flow = _finite_series(native, "volume_flow_m3_s", count)
    head = _finite_series(native, "head_gain_m", count)
    shaft_power = _finite_series(native, "shaft_power_W", count)
    loss_power = _finite_series(native, "dissipated_power_W", count, nonnegative=True)
    if speed[0] != q.initial_angular_speed_rad_s:
        raise ValueError("pump provider initial speed differs from the request")
    hydraulic_power = [q.density_kg_m3 * q.gravity_m_s2 * f * h for f, h in zip(flow, head, strict=True)]
    supplied_hydraulic_power = _finite_series(native, "hydraulic_power_W", count)
    power_errors = [
        abs(expected - supplied)
        for expected, supplied in zip(hydraulic_power, supplied_hydraulic_power, strict=True)
    ]
    power_scales = [
        max(abs(expected), abs(supplied), 1.0)
        for expected, supplied in zip(hydraulic_power, supplied_hydraulic_power, strict=True)
    ]
    power_consistent = all(
        error <= q.absolute_power_consistency_tolerance_W + q.relative_power_consistency_tolerance * scale
        for error, scale in zip(power_errors, power_scales, strict=True)
    )
    ledger = native.get("energy_ledger_J")
    keys = {
        "initial_rotor",
        "drive",
        "hydraulic",
        "dissipated",
        "final_rotor",
        "numerical_adjustment",
    }
    if (
        not isinstance(ledger, dict)
        or set(ledger) != keys
        or any(
            isinstance(ledger[key], bool)
            or not isinstance(ledger[key], (int, float))
            or not math.isfinite(ledger[key])
            for key in keys
        )
    ):
        raise ValueError("pump provider requires one finite signed energy ledger")

    def integrate(values):
        return math.fsum(
            0.5 * (values[index - 1] + values[index]) * (times[index] - times[index - 1])
            for index in range(1, count)
        )

    integrated_energies = {
        "drive": integrate(shaft_power),
        "hydraulic": integrate(supplied_hydraulic_power),
        "dissipated": integrate(loss_power),
    }
    work_integrals_consistent = all(
        abs(ledger[key] - integrated)
        <= q.absolute_energy_balance_tolerance_J
        + q.relative_energy_balance_tolerance * max(abs(ledger[key]), abs(integrated), 1.0)
        for key, integrated in integrated_energies.items()
    )
    expected_initial = 0.5 * q.rotor_inertia_kg_m2 * speed[0] ** 2
    expected_final = 0.5 * q.rotor_inertia_kg_m2 * speed[-1] ** 2
    rotor_energy_consistent = all(
        abs(reported - expected)
        <= q.absolute_energy_balance_tolerance_J
        + q.relative_energy_balance_tolerance * max(abs(reported), abs(expected), 1.0)
        for reported, expected in (
            (ledger["initial_rotor"], expected_initial),
            (ledger["final_rotor"], expected_final),
        )
    )
    residual = (
        ledger["initial_rotor"]
        + ledger["drive"]
        - ledger["hydraulic"]
        - ledger["dissipated"]
        - ledger["final_rotor"]
        + ledger["numerical_adjustment"]
    )
    scale = max(
        abs(ledger["initial_rotor"]) + abs(ledger["drive"]),
        abs(ledger["hydraulic"]) + abs(ledger["dissipated"]) + abs(ledger["final_rotor"]),
        1.0,
    )
    energy_balanced = abs(residual) <= (
        q.absolute_energy_balance_tolerance_J + q.relative_energy_balance_tolerance * scale
    )
    adjustment_ok = abs(ledger["numerical_adjustment"]) <= q.maximum_energy_numerical_adjustment_J
    converged = native.get("converged") is True
    accepted = (
        power_consistent
        and work_integrals_consistent
        and rotor_energy_consistent
        and energy_balanced
        and adjustment_ok
        and converged
    )
    body = {
        "schema": "pipeline_integrity.pump_coastdown_boundary.v1",
        "status": "computed" if accepted else "withheld",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "provider": identity,
        "native": native,
        "maximum_hydraulic_power_error_W": max(power_errors),
        "hydraulic_power_consistent": power_consistent,
        "integrated_energy_J": integrated_energies,
        "power_integrals_match_energy_ledger": work_integrals_consistent,
        "rotor_energy_consistent": rotor_energy_consistent,
        "energy_balance_residual_J": residual,
        "energy_balance_within_tolerance": energy_balanced,
        "energy_numerical_adjustment_within_limit": adjustment_ok,
        "provider_converged": converged,
        "moc_boundary": {
            "time_s": times,
            "volume_flow_m3_s": flow,
            "head_gain_m": head,
            "clock_id": q.clock_id,
            "source_record": "owner_pump_response",
        },
        "direct_moc_coupling_implemented": False,
        "pump_dynamics_reimplemented": False,
        "field_validation_claim": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return {**body, "record_digest": digest(body)}


def verify_pump_coastdown_record(record, *, request, context, provider):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("pump coastdown boundary seal changed")
    if evaluate_pump_coastdown(request, context, provider=provider) != record:
        raise ValueError("pump provider response or boundary differs on replay")
    return True
