"""Reviewed, bounded exchange with the owner's dynamic LH2 pool SDK."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import RunContext, SignalFrame, digest

SCHEMA = "pool-exchange/1"
PROPERTY_BASIS = "CP-HEOS:Hydrogen:native"
SDK_VERSION = "0.2.0rc2"
COMMIT = "6a700e8e183039bbb81803bacc416f6c9117ed71"
ENERGY_STATUS = "unverified: SDK provides no energy ledger"
RATE_SEMANTICS = "native spreading interval_mean/last_substep and fixed instantaneous retained; signals use cumulative differences"


def source_manifest(root: str | Path) -> dict[str, str]:
    root = Path(root).resolve()
    files = sorted((root / "src/dynamiclh2poolx").glob("*.py"))
    if len(files) != 8:
        raise ValueError("eight owner SDK modules required")
    files += [root / "LICENSE", root / "pyproject.toml", root / "README.md"]
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


@dataclass(frozen=True)
class PoolRequest:
    source_root: str
    config: dict[str, Any]
    context: RunContext
    source_pin: str | None = None
    boundary_reviewed: bool = False
    source_reviewed: bool = False
    model_reviewed: bool = False
    python_executable: str = sys.executable
    timeout_s: float = 30.0
    input_budget_bytes: int = 2_000_000
    output_budget_bytes: int = 2_000_000


def reference_config(route: str = "spreading") -> dict[str, Any]:
    """Synthetic coefficients and directly declared ground-reaching liquid only."""
    common = {
        "schema": SCHEMA,
        "route": route,
        "fluid": "Hydrogen",
        "property_basis": PROPERTY_BASIS,
        "ambient_pressure_pa_abs": 101325.0,
        "initial_liquid_mass_kg": 0.0,
        "output_times_s": [0.0, 0.1, 0.2, 0.5, 1.0],
        "inflow": {"times_s": [0.0, 0.5], "rates_kg_s": [0.01, 0.0]},
        "boundary": {
            "clock_offset_s": 0.0,
            "enu_origin_m": [0.0, 0.0, 0.0],
            "source_asset_id": "synthetic-ground",
            "event_id": "declared-spill",
            "port_id": "liquid-to-ground",
            "meaning": "liquid_already_reached_surface",
            "time_hold": "ZOH_final_hold",
        },
    }
    solid = {"name": "synthetic-solid", "conductivity_W_mK": 1.0, "diffusivity_m2_s": 1e-6}
    if route == "spreading":
        common.update(
            surface={
                "kind": "solid",
                "kwargs": {
                    **solid,
                    "initial_temperature_K": 100.0,
                    "heat_flux_cap_W_m2": None,
                    "heat_flux_multiplier": 1.0,
                },
            },
            numerics={
                "domain_radius_m": 1.0,
                "radial_step_m": 0.05,
                "maximum_time_step_s": 0.01,
                "cfl": 0.2,
                "chezy_coefficient": 0.001,
                "source_inner_radius_m": 0.0,
                "source_radius_m": 0.1,
                "surface_retention_depth_m": 0.0,
                "dry_depth_m": 1e-8,
                "reported_front_depth_m": 0.00075,
            },
            evaporation_momentum_closure="zero_radial_momentum_vapor",
        )
    elif route == "fixed_area":
        common.update(
            surface={"kind": "solid", "kwargs": solid},
            fixed_area={
                "area_m2": 0.1,
                "ambient_temperature_K": 100.0,
                "critical_heat_flux_W_m2": None,
                "initial_thermal_age_s": 0.0,
            },
        )
    else:
        raise ValueError("unknown route")
    return common


def _number(value, label, low=0.0, high=1e12, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: finite bounded SI number required")
    if positive and value == 0:
        raise ValueError(f"{label}: positive number required")


def _clock(values, label, maximum=300.0):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 1001 or values[0] != 0.0:
        raise ValueError(f"{label}: bounded clock starting at zero required")
    last = -1.0
    for value in values:
        _number(value, label, high=maximum)
        if value <= last:
            raise ValueError(f"{label}: clock must strictly increase")
        last = value


def validate_config(c: dict[str, Any]) -> dict[str, int | float]:
    if not isinstance(c, dict) or c.get("route") not in {"spreading", "fixed_area"}:
        raise ValueError("explicit dynamic route required")
    route = c["route"]
    if set(c) != set(reference_config(route)) or c["schema"] != SCHEMA:
        raise ValueError("configuration schema/fields mismatch")
    if c["fluid"] != "Hydrogen" or c["property_basis"] != PROPERTY_BASIS:
        raise NotImplementedError("only native HEOS Hydrogen saturation Q=0/Q=1 is supported")
    _number(c["ambient_pressure_pa_abs"], "ambient_pressure_pa_abs", positive=True)
    _number(c["initial_liquid_mass_kg"], "initial mass")
    if route == "spreading" and c["initial_liquid_mass_kg"] != 0:
        raise NotImplementedError("spreading initial inventory must be zero")
    _clock(c["output_times_s"], "output clock")
    if len(c["output_times_s"]) < 2:
        raise ValueError("at least two output timestamps required")
    inflow = c["inflow"]
    if set(inflow) != {"times_s", "rates_kg_s"}:
        raise ValueError("inflow fields mismatch")
    _clock(inflow["times_s"], "inflow clock")
    if len(inflow["times_s"]) != len(inflow["rates_kg_s"]):
        raise ValueError("inflow rates and timestamps must align")
    for rate in inflow["rates_kg_s"]:
        _number(rate, "liquid-to-ground kg/s")
    b = c["boundary"]
    if set(b) != set(reference_config(route)["boundary"]):
        raise ValueError("boundary context fields mismatch")
    if b["meaning"] != "liquid_already_reached_surface" or b["time_hold"] != "ZOH_final_hold":
        raise NotImplementedError("jet/deposition/alternative inflow interpolation is outside scope")
    _number(b["clock_offset_s"], "clock offset")
    if len(b["enu_origin_m"]) != 3:
        raise ValueError("ENU origin requires three coordinates")
    for value in b["enu_origin_m"]:
        _number(value, "ENU origin", low=-1e9)
    for key in ("source_asset_id", "event_id", "port_id"):
        if not isinstance(b[key], str) or not b[key].strip():
            raise ValueError(f"{key} required")
    surface = c["surface"]
    if set(surface) != {"kind", "kwargs"}:
        raise ValueError("surface kind/kwargs required")
    kwargs = surface["kwargs"]
    if surface["kind"] == "water":
        if route != "spreading" or set(kwargs) != {"heat_flux_W_m2", "calibration_label"}:
            raise NotImplementedError("water closure only supported by spreading route")
        _number(kwargs["heat_flux_W_m2"], "water heat flux", positive=True)
        if not isinstance(kwargs["calibration_label"], str) or not kwargs["calibration_label"].strip():
            raise ValueError("water closure evidence label required")
        if c["output_times_s"][-1] > 60:
            raise NotImplementedError("water empirical closure limited to approximately 50-60 seconds")
    elif surface["kind"] == "solid":
        allowed = set(reference_config(route)["surface"]["kwargs"])
        if set(kwargs) != allowed or not isinstance(kwargs["name"], str) or not kwargs["name"].strip():
            raise ValueError("explicit solid properties required")
        for key, value in kwargs.items():
            if key != "name" and not (key == "heat_flux_cap_W_m2" and value is None):
                _number(value, key, positive=True)
    else:
        raise NotImplementedError("unrecognized surface closure")
    cells, nominal = 0, 0
    if route == "spreading":
        n = c["numerics"]
        if set(n) != set(reference_config()["numerics"]):
            raise ValueError("explicit numerics required")
        for key, value in n.items():
            _number(
                value,
                key,
                positive=key
                not in {"chezy_coefficient", "source_inner_radius_m", "surface_retention_depth_m"},
            )
        if (
            not 0 < n["cfl"] <= 0.5
            or not n["source_inner_radius_m"] < n["source_radius_m"] < n["domain_radius_m"]
        ):
            raise ValueError("numerical domain/CFL/source geometry invalid")
        if c["evaporation_momentum_closure"] not in {
            "zero_radial_momentum_vapor",
            "liquid_velocity_carryoff",
        }:
            raise ValueError("unknown evaporation momentum closure")
        cells = math.ceil(n["domain_radius_m"] / n["radial_step_m"])
        nominal = cells * math.ceil(c["output_times_s"][-1] / n["maximum_time_step_s"])
        if cells > 2000 or nominal > 2_000_000:
            raise ValueError(
                "grid/nominal work budget exceeded; adaptive substeps additionally bounded by timeout"
            )
    else:
        fixed = c["fixed_area"]
        if set(fixed) != set(reference_config(route)["fixed_area"]):
            raise ValueError("fixed-area fields mismatch")
        for key, value in fixed.items():
            if key == "critical_heat_flux_W_m2" and value is None:
                continue
            _number(value, key, positive=key != "initial_thermal_age_s")
    digest(c)  # Reject non-JSON values before serialization and constructors.
    return {"radial_cells": cells, "nominal_cell_steps": nominal}


def zoh_cumulative(times, rates, target):
    """Independent boundary integral; final declared rate continues after its timestamp."""
    return sum(
        rate * max(0.0, min(target, times[i + 1] if i + 1 < len(times) else target) - t)
        for i, (t, rate) in enumerate(zip(times, rates, strict=True))
    )


def _audit(native, config):
    rows = native["ledger"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty native ledger required")
    if [r["time_s"] for r in rows] != list(config["output_times_s"]):
        raise ValueError("native output clock does not match request")
    _number(native["mass_balance_residual_kg"], "native mass residual", low=-1e12)
    inflow = config["inflow"]
    audits = []
    previous = None
    for row in rows:
        # These quantities are extensive nonnegative stores, never signed rates.
        for key in ("liquid_mass_kg", "cumulative_inflow_kg", "cumulative_evaporation_kg"):
            _number(row[key], key)
        for key in ("escaped_domain_mass_kg",):
            _number(row.get(key, 0.0), key)
        for key in ("cumulative_numerical_mass_adjustment_kg", "unledgered_mass_residual_kg"):
            _number(row.get(key, 0.0), key, low=-1e12)
        if previous is not None and any(
            row.get(key, 0.0) < previous.get(key, 0.0)
            for key in ("cumulative_inflow_kg", "cumulative_evaporation_kg", "escaped_domain_mass_kg")
        ):
            raise ValueError("cumulative inflow/evaporation/escape must not decrease")
        if previous is None and (
            row["liquid_mass_kg"] != config["initial_liquid_mass_kg"]
            or any(
                row.get(key, 0.0) != 0
                for key in (
                    "cumulative_inflow_kg",
                    "cumulative_evaporation_kg",
                    "escaped_domain_mass_kg",
                    "cumulative_numerical_mass_adjustment_kg",
                )
            )
        ):
            raise ValueError("initial ledger stores differ from the declared initial boundary")
        expected = zoh_cumulative(inflow["times_s"], inflow["rates_kg_s"], row["time_s"])
        adjustment = row.get("cumulative_numerical_mass_adjustment_kg", 0.0)
        escaped = row.get("escaped_domain_mass_kg", 0.0)
        residual = (
            config["initial_liquid_mass_kg"]
            + expected
            + adjustment
            - row["cumulative_evaporation_kg"]
            - escaped
            - row["liquid_mass_kg"]
        )
        audits.append(
            {
                "time_s": row["time_s"],
                "independent_inflow_kg": expected,
                "inflow_difference_kg": row["cumulative_inflow_kg"] - expected,
                "mass_residual_kg": residual,
            }
        )
        previous = row
    rejected = any(abs(r["mass_residual_kg"]) > 1e-8 or abs(r["inflow_difference_kg"]) > 1e-8 for r in audits)
    rejected |= abs(native["mass_balance_residual_kg"]) > 1e-8
    rejected |= any(
        abs(r.get("unledgered_mass_residual_kg", 0.0)) > 1e-8
        or r["source_status"] not in {"within_declared_scope", "input_limited", "out_of_scope"}
        for r in rows
    )
    scope = any(r["source_status"] == "out_of_scope" or r.get("escaped_domain_mass_kg", 0) > 0 for r in rows)
    adjusted = any(r.get("cumulative_numerical_mass_adjustment_kg", 0) != 0 for r in rows)
    return audits, "out_of_scope" if scope else "diagnostic_rejected" if rejected or adjusted else "computed"


def evaluate(request: PoolRequest) -> dict[str, Any]:
    answer = {"schema": SCHEMA, "status": "needs_data", "native_called": False, "native": None}
    if (
        any(
            v is not True
            for v in (request.boundary_reviewed, request.source_reviewed, request.model_reviewed)
        )
        or not isinstance(request.context, RunContext)
        or not request.context.evidence
        or not request.source_pin
    ):
        answer["reason"] = (
            "reviewed source/model/direct liquid boundary, evidence, RunContext and source pin required"
        )
        return answer
    try:
        budget = validate_config(request.config)
        if request.context.metadata.get("pool_boundary") != request.config["boundary"]:
            raise ValueError(
                "RunContext metadata.pool_boundary must exactly bind source/event/port/ENU/clock"
            )
        _number(request.timeout_s, "timeout", 0.05, 120)
        if (
            type(request.output_budget_bytes) is not int
            or not 2048 <= request.output_budget_bytes <= 8_000_000
        ):
            raise ValueError("output budget out of range")
        if type(request.input_budget_bytes) is not int or not 2048 <= request.input_budget_bytes <= 8_000_000:
            raise ValueError("input budget out of range")
        before = source_manifest(request.source_root)
        if digest(before) != request.source_pin:
            raise ValueError("source pin mismatch")
        worker = Path(__file__).with_name("pool_worker.py")
        payload = {
            "schema": SCHEMA,
            "source_root": str(Path(request.source_root).resolve()),
            "config": request.config,
            "context": request.context.model_dump(mode="json"),
            "source_pin": request.source_pin,
            "sdk_version": SDK_VERSION,
        }
        encoded_payload = json.dumps(payload, allow_nan=False)
        if len(encoded_payload.encode()) > request.input_budget_bytes:
            raise ValueError("serialized input budget exceeded")
        answer.update(
            config=request.config,
            context=payload["context"],
            source_pin=request.source_pin,
            request_pin=digest(payload),
            config_pin=digest(request.config),
            context_pin=digest(payload["context"]),
            evidence_pin=digest(payload["context"]["evidence"]),
            worker_pin=hashlib.sha256(worker.read_bytes()).hexdigest(),
            nominal_work=budget,
            reviews={"boundary": True, "source": True, "model": True},
        )
        answer["adapter_pin"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="pool-exchange-") as temp:
            path = Path(temp) / "response.json"
            completed = subprocess.run(
                [request.python_executable, "-I", str(worker), str(path), str(request.output_budget_bytes)],
                input=encoded_payload,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=request.timeout_s,
                check=False,
            )
            if not path.is_file() or path.stat().st_size > request.output_budget_bytes:
                raise ValueError(f"missing/oversize worker response (exit {completed.returncode})")
            if completed.returncode != 0:
                raise ValueError(f"worker process failed (exit {completed.returncode})")
            response = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
            )
        if response.get("request_pin") != digest(payload) or response.get("schema") != SCHEMA:
            raise ValueError("worker response binding mismatch")
        answer.update(response)
        answer["source_unchanged"] = before == source_manifest(request.source_root)
        if not answer["source_unchanged"]:
            answer.update(status="error", reason="SDK source changed during execution")
        elif response["status"] == "native_complete":
            audits, status = _audit(response["native"], request.config)
            answer.update(
                status=status,
                audits=audits,
                energy_balance_status=ENERGY_STATUS,
                rate_semantics=RATE_SEMANTICS,
            )
        if answer.get("native") is not None:
            answer["native_pin"] = digest(answer["native"])
    except NotImplementedError as exc:
        answer.update(status="out_of_scope", reason=str(exc))
    except subprocess.TimeoutExpired:
        answer.update(status="timeout", reason="SDK worker timed out; native API returned no partial ledger")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        answer.update(status="invalid", reason=str(exc))
    answer["receipt_pin"] = digest(answer)
    return answer


evaluate_pool_request = evaluate


def _stored_checks(result: dict[str, Any], context: RunContext) -> dict[str, bool]:
    """Recheck quantities and their projections; hashes alone do not establish semantics."""
    config = result["config"]
    validate_config(config)
    audits, status = _audit(result["native"], config)
    context_json = context.model_dump(mode="json")
    return {
        "receipt": result["receipt_pin"] == digest({k: v for k, v in result.items() if k != "receipt_pin"}),
        "schema": result.get("schema") == SCHEMA,
        "reviews": result.get("reviews") == {"boundary": True, "source": True, "model": True}
        and all(type(v) is bool for v in result.get("reviews", {}).values()),
        "config": result["config_pin"] == digest(config),
        "context": result["context_pin"] == digest(context_json) and result["context"] == context_json,
        "boundary": context.metadata.get("pool_boundary") == config["boundary"],
        "evidence": bool(context.evidence) and result["evidence_pin"] == digest(context_json["evidence"]),
        "native": result["native_pin"] == digest(result["native"]) and result.get("native_called") is True,
        "worker": result["worker_pin"]
        == hashlib.sha256(Path(__file__).with_name("pool_worker.py").read_bytes()).hexdigest(),
        "adapter": result["adapter_pin"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_unchanged": result["source_unchanged"] is True,
        "sdk_version": result.get("sdk_version") == SDK_VERSION,
        "audits": result.get("audits") == audits,
        "status": result.get("status") == status and not result.get("nonfinite_observations"),
        "energy_meaning": result.get("energy_balance_status") == ENERGY_STATUS,
        "rate_meaning": result.get("rate_semantics") == RATE_SEMANTICS,
    }


def validate_stored_result(
    result: dict[str, Any], source_root: str | Path, context: RunContext
) -> dict[str, Any]:
    """Recheck an unsigned stored receipt against its inputs and current local SDK bytes."""
    try:
        config = result["config"]
        payload = {
            "schema": SCHEMA,
            "source_root": str(Path(source_root).resolve()),
            "config": config,
            "context": context.model_dump(mode="json"),
            "source_pin": result["source_pin"],
            "sdk_version": SDK_VERSION,
        }
        checks = {
            **_stored_checks(result, context),
            "request": result["request_pin"] == digest(payload),
            "source": result["source_pin"] == digest(source_manifest(source_root)),
        }
        return {
            "valid": all(checks.values()),
            "checks": checks,
            "meaning": "local byte/contract consistency check; unsigned and not proof of execution or engineering approval",
        }
    except (KeyError, ValueError, TypeError, OSError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc), "meaning": "unsigned consistency receipt"}


def to_signal_frames(result: dict[str, Any], context: RunContext) -> tuple[SignalFrame, ...]:
    """Mass-preserving interval averages; no point-rate equivalence is assumed."""
    if result.get("status") != "computed" or not all(_stored_checks(result, context).values()):
        raise ValueError("semantically consistent completed receipt with matching RunContext required")
    config = result["config"]
    if context.metadata.get("pool_boundary") != config["boundary"]:
        raise ValueError("parent context boundary mismatch")
    rows = result["native"]["ledger"]
    frames = []
    previous = None
    for row in rows:
        mean = (
            None
            if previous is None
            else (
                (row["cumulative_evaporation_kg"] - previous["cumulative_evaporation_kg"])
                / (row["time_s"] - previous["time_s"])
            )
        )
        values = {
            "evaporation_interval_mean_kg_s": mean,
            "liquid_mass_kg": row["liquid_mass_kg"],
            "cumulative_evaporation_kg": row["cumulative_evaporation_kg"],
        }
        frames.append(
            SignalFrame(
                stream_id=f"{config['boundary']['event_id']}:{config['boundary']['port_id']}",
                clock_id=context.clock_id,
                time_s=row["time_s"] + config["boundary"]["clock_offset_s"],
                values=values,
                units={
                    "evaporation_interval_mean_kg_s": "kg/s",
                    "liquid_mass_kg": "kg",
                    "cumulative_evaporation_kg": "kg",
                },
                valid={k: v is not None for k, v in values.items()},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode="reviewed-ground-liquid-pool",
            )
        )
        previous = row
    return tuple(frames)
