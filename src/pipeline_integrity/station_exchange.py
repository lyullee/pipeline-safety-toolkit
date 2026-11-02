"""Bounded JSON process boundary to the owner's hydrogen station network API."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import RunContext, SignalFrame

SCHEMA = "station-network-exchange/1"
FULL_SCHEMA = "station-reference-episode/1"
RESULT_SCHEMA = "station-exchange/2"
PROPERTY_BASIS = "Hydrogen TABLE; pinned src/h2station/data/hydrogen_properties_v1.npz"
OWNER_VERSION = "0.1.2"
FULL_FIELDS = {
    "ambient_temperature_k",
    "initial_vehicle_pressure_pa",
    "initial_vehicle_temperature_k",
    "vehicle_internal_volume_m3",
    "target_vehicle_pressure_pa",
    "average_pressure_ramp_rate_pa_s",
    "delivery_temperature_k",
    "maximum_gas_temperature_k",
    "maximum_mass_flow_kg_s",
    "compressor_suction_pressure_pa",
    "compressor_suction_temperature_k",
    "header_internal_volume_m3",
    "bank_header_flow_area_m2",
    "compressor_package_internal_volume_m3",
    "compressor_package_flow_area_m2",
    "vehicle_tank_calibration",
    "bank_internal_volume_m3",
    "initial_bank_fill_percent",
}
INPUT_UNITS = {"pressure": "Pa_abs", "temperature": "K", "time": "s", "mass": "kg", "energy": "J"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_manifest(root: str | Path) -> dict[str, str]:
    """Pin all owner Python modules and property data, plus identity and license."""
    root = Path(root).resolve()
    files = [root / name for name in ("LICENSE", "pyproject.toml")]
    for name in ("scripts/generate_hydrogen_table.py", "research/dispersion_detector_logic_validation.json"):
        if (root / name).is_file():
            files.append(root / name)
    files += [p for p in (root / "src/h2station").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    if not all(p.is_file() for p in files) or len(files) < 3:
        raise ValueError("incomplete owner source")
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


@dataclass(frozen=True)
class StationRequest:
    source_root: str
    python_executable: str
    config: dict[str, Any]
    context: RunContext
    evidence: tuple[str, ...] = ()
    source_pin: str | None = None
    source_reviewed: bool = False
    model_reviewed: bool = False
    conditions_reviewed: bool = False
    timeout_s: float = 30.0
    input_budget_bytes: int = 2_000_000
    output_budget_bytes: int = 2_000_000


def reference_boundary(asset_id: str, clock_id: str) -> dict[str, Any]:
    """Caller-reviewed event/clock labels; no implicit clock conversion is performed."""
    return {
        "asset_id": asset_id,
        "clock_id": clock_id,
        "clock_offset_s": 0.0,
        "event_id": "synthetic-station-transfer",
        "pressure_reference": "absolute",
        "property_basis": PROPERTY_BASIS,
    }


def _context(context: RunContext, evidence: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(context, RunContext):
        raise ValueError("shared RunContext required")
    canonical = RunContext.model_validate_json(context.model_dump_json()).model_dump(mode="json")
    identifiers = [ref.id for ref in context.evidence]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate context evidence identifier")
    if (
        not isinstance(evidence, (tuple, list))
        or not evidence
        or any(not isinstance(x, str) or not x.strip() for x in evidence)
        or len(set(evidence)) != len(evidence)
        or not set(evidence).issubset(identifiers)
    ):
        raise ValueError("selected evidence must identify unique RunContext evidence records")
    boundary = context.metadata.get("station_boundary")
    expected = reference_boundary(context.asset_id, context.clock_id)
    if not isinstance(boundary, dict) or set(boundary) != set(expected):
        raise ValueError("RunContext.metadata.station_boundary with explicit event and clock required")
    for key in ("asset_id", "clock_id", "pressure_reference", "property_basis"):
        if boundary[key] != expected[key]:
            raise ValueError(f"station boundary {key} mismatch")
    _finite(boundary["clock_offset_s"], "clock offset", 0, 1e12)
    if not isinstance(boundary["event_id"], str) or not boundary["event_id"].strip():
        raise ValueError("station event identifier required")
    return canonical


def reference_config() -> dict[str, Any]:
    """Explicit synthetic two-vessel configuration; no site calibration is implied."""
    parameters = dict(
        volume=0.1,
        gas_wall_area=1.0,
        wall_ambient_area=1.0,
        wall_heat_capacity=10000.0,
        gas_wall_htc=5.0,
        wall_ambient_htc=2.0,
    )
    return {
        "schema": SCHEMA,
        "units": INPUT_UNITS.copy(),
        "ambient_temperature_k": 298.15,
        "duration_s": 1.0,
        "sample_count": 21,
        "maximum_step_s": 0.05,
        "maximum_rhs_calls": 20000,
        "tanks": {
            "bank": {"parameters": parameters.copy(), "pressure_pa_abs": 20e6, "temperature_k": 298.15},
            "receiver": {"parameters": parameters.copy(), "pressure_pa_abs": 5e6, "temperature_k": 298.15},
        },
        "connections": [
            {
                "name": "transfer",
                "source": "bank",
                "target": "receiver",
                "diameter_m": 0.001,
                "allow_reverse": False,
            }
        ],
        "commands": [{"time_s": 0.0, "openings": {"transfer": 0.5}}],
    }


def _finite(value: Any, label: str, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label}: outside bounded domain")


def validate_config(c: dict[str, Any]) -> None:
    if not isinstance(c, dict):
        raise ValueError("config object required")
    if c.get("schema") == FULL_SCHEMA:
        if (
            set(c) != {"schema", "units", "duration_s", "control_period_s", "scenario"}
            or c["units"] != INPUT_UNITS
        ):
            raise ValueError("full episode schema/units mismatch")
        _finite(c["duration_s"], "duration", 0.01, 30)
        _finite(c["control_period_s"], "control period", 0.01, 1)
        if c["duration_s"] / c["control_period_s"] > 300:
            raise ValueError("full episode step budget")
        if not isinstance(c["scenario"], dict) or set(c["scenario"]) - FULL_FIELDS:
            raise ValueError("unknown full scenario parameter")
        for key, value in c["scenario"].items():
            if key == "vehicle_tank_calibration":
                if value != "reference":
                    raise ValueError("only uncalibrated reference mode is in this boundary")
            elif key in {"bank_internal_volume_m3", "initial_bank_fill_percent"}:
                if not isinstance(value, (list, tuple)) or len(value) != 3:
                    raise ValueError("three bank values required")
                for entry in value:
                    _finite(entry, key, 1e-6, 100)
            else:
                _finite(value, key, 1e-9, 100e6)
        return
    expected = set(reference_config())
    if set(c) != expected or c["schema"] != SCHEMA or c["units"] != INPUT_UNITS:
        raise ValueError("config schema/units/fields mismatch")
    _finite(c["duration_s"], "duration", 1e-3, 300)
    _finite(c["maximum_step_s"], "step", c["duration_s"] / 10000, c["duration_s"])
    for key, lo, hi in (("sample_count", 2, 2001), ("maximum_rhs_calls", 10, 100000)):
        if type(c[key]) is not int or not lo <= c[key] <= hi:
            raise ValueError(f"{key}: integer budget required")
    _finite(c["ambient_temperature_k"], "ambient", 100, 500)
    tanks = c["tanks"]
    if not isinstance(tanks, dict) or not 1 <= len(tanks) <= 8:
        raise ValueError("one to eight tanks required")
    keys = set(reference_config()["tanks"]["bank"]["parameters"])
    for name, tank in tanks.items():
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("invalid tank name")
        if set(tank) != {"parameters", "pressure_pa_abs", "temperature_k"} or set(tank["parameters"]) != keys:
            raise ValueError("tank fields mismatch")
        for key, value in tank["parameters"].items():
            _finite(value, key, 1e-9, 1e9)
        _finite(tank["pressure_pa_abs"], "pressure_pa_abs", 1e5, 100e6)
        _finite(tank["temperature_k"], "temperature_k", 100, 500)
    if not isinstance(c["connections"], list) or len(c["connections"]) > 16:
        raise ValueError("connection budget")
    names = set()
    for edge in c["connections"]:
        if set(edge) != {"name", "source", "target", "diameter_m", "allow_reverse"}:
            raise ValueError("connection fields mismatch")
        if not isinstance(edge["name"], str) or not edge["name"] or edge["name"] in names:
            raise ValueError("invalid/duplicate port name")
        names.add(edge["name"])
        if edge["source"] not in tanks or edge["target"] not in tanks or edge["source"] == edge["target"]:
            raise ValueError("unknown or identical ports")
        if type(edge["allow_reverse"]) is not bool:
            raise ValueError("reverse flag must be boolean")
        _finite(edge["diameter_m"], "diameter", 1e-6, 0.01)
    if not isinstance(c["commands"], list) or not 1 <= len(c["commands"]) <= 100:
        raise ValueError("command budget")
    last = -1.0
    for command in c["commands"]:
        if set(command) != {"time_s", "openings"} or set(command["openings"]) != names:
            raise ValueError("command ports mismatch")
        _finite(command["time_s"], "command time", 0, c["duration_s"])
        if command["time_s"] <= last:
            raise ValueError("command clock must increase")
        last = command["time_s"]
        for opening in command["openings"].values():
            _finite(opening, "opening", 0, 1)
    if c["commands"][0]["time_s"] != 0:
        raise ValueError("first command must start at zero")


def _producer_pins() -> dict[str, str]:
    return {
        "adapter_pin": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "worker_pin": hashlib.sha256(Path(__file__).with_name("station_worker.py").read_bytes()).hexdigest(),
    }


def _payload(config, context, evidence, source_pin, source_root):
    return {
        "schema": RESULT_SCHEMA,
        "source_root": str(Path(source_root).resolve()),
        "config": config,
        "context": context,
        "evidence": list(evidence),
        "source_pin": source_pin,
    }


def _clock_check(times, expected, complete):
    if not isinstance(times, list) or (complete and not times) or len(times) > len(expected):
        raise ValueError("native clock length mismatch")
    for i, value in enumerate(times):
        _finite(value, "native clock", 0, expected[-1])
        if (i and value <= times[i - 1]) or not math.isclose(value, expected[i], abs_tol=1e-9):
            raise ValueError("native clock is not the requested clock/prefix")
    if complete and len(times) != len(expected):
        raise ValueError("completed native clock is incomplete")


def _native_audit(native, config, source_root):
    """Check observable units/clock/ports and independently aggregate exposed budgets."""
    if not isinstance(native, dict) or native.get("status") not in {"computed", "partial", "error"}:
        raise ValueError("invalid native response status")
    reserved = {
        "schema",
        "config",
        "context",
        "evidence",
        "request_pin",
        "config_pin",
        "context_pin",
        "evidence_pin",
        "selected_evidence_pin",
        "source_pin",
        "source_unchanged",
        "adapter_pin",
        "worker_pin",
        "receipt_pin",
        "native",
        "native_pin",
        "audits",
    }
    if set(native) & reserved:
        raise ValueError("native result cannot overwrite parent receipt fields")
    if native["status"] == "error":
        if native.get("trajectory") is not None:
            raise ValueError("error response cannot claim a completed trajectory")
        return {"observable_budget": "not_returned"}
    if native.get("native_called") is not True:
        raise ValueError("successful native call flag missing")
    if native.get("loaded_owner") != str((Path(source_root) / "src/h2station/__init__.py").resolve()):
        raise ValueError("loaded owner does not match the pinned source")
    if native.get("owner_version") != OWNER_VERSION or native.get("property_basis") != PROPERTY_BASIS:
        raise ValueError("owner version/property basis mismatch")
    runtime = native.get("runtime", {})
    scipy = tuple(int(v) for v in runtime.get("scipy", "0.0").split(".")[:2])
    if not (1, 11) <= scipy < (1, 16):
        raise ValueError("native receipt has unsupported SciPy version")
    trajectory = native["trajectory"]
    complete = native["status"] == "computed"

    def reported_unknown(path):
        records = native.get("nonfinite_observations", [])
        return any(
            item.get("path") == path and item.get("original") in {"nan", "inf", "-inf"}
            for item in records
            if isinstance(item, dict)
        )

    if config["schema"] == FULL_SCHEMA:
        expected = [0.0]
        while expected[-1] < config["duration_s"]:
            expected.append(min(config["duration_s"], expected[-1] + config["control_period_s"]))
        samples = trajectory["samples"]
        times = trajectory.get("time_s", [s["time_s"] for s in samples])
        _clock_check(times, expected, complete)
        if len(samples) != len(times) or any(s["time_s"] != t for s, t in zip(samples, times, strict=True)):
            raise ValueError("full episode samples do not align with the native clock")
        for i, sample in enumerate(samples):
            if sample.get("active_faults") or sample.get("consequence_updated"):
                raise ValueError("full episode active fault/consequence scope is unsupported")
            for key in (
                "vehicle_pressure_pa",
                "vehicle_temperature_k",
                "vehicle_mass_kg",
                "header_pressure_pa",
                "header_temperature_k",
                "header_mass_kg",
                "nozzle_mass_flow_kg_s",
            ):
                value = sample.get(key)
                if value is None and not complete and reported_unknown(f"/trajectory/samples/{i}/{key}"):
                    continue
                _finite(value, key, 0, 1e30)
        if complete:
            for field in (
                "vehicle_pressure_pa_abs",
                "nozzle_mass_flow_kg_s",
                "header_mass_kg",
                "risk_snapshots",
            ):
                if len(trajectory[field]) != len(times):
                    raise ValueError("full episode channel length mismatch")
            for field, sample_field in (
                ("vehicle_pressure_pa_abs", "vehicle_pressure_pa"),
                ("nozzle_mass_flow_kg_s", "nozzle_mass_flow_kg_s"),
                ("header_mass_kg", "header_mass_kg"),
            ):
                if trajectory[field] != [s[sample_field] for s in samples]:
                    raise ValueError("full episode duplicated channel mismatch")
            if any(trajectory["risk_snapshots"]) or native.get("balance") is not None:
                raise ValueError("risk/consequence and full-loop balance are outside this boundary")
            if not native.get("balance_status", "").startswith("needs_data"):
                raise ValueError("full-loop balance unknown must be explicit")
        return {
            "observable_budget": "needs_data: full-loop mass/energy ledger unavailable",
            "samples": len(times),
        }
    times = trajectory["time_s"]
    expected = sorted(
        set([i * config["duration_s"] / (config["sample_count"] - 1) for i in range(config["sample_count"])])
        | {c["time_s"] for c in config["commands"]}
    )
    _clock_check(times, expected, complete)
    fields = {"mass_kg", "gas_energy_j", "wall_temperature_k", "pressure_pa_abs", "gas_temperature_k"}
    tanks = trajectory["tanks"]
    if set(tanks) != set(config["tanks"]):
        raise ValueError("native tank identifiers mismatch")
    unknown = False
    for tank_name, record in tanks.items():
        if set(record) != fields or any(len(values) != len(times) for values in record.values()):
            raise ValueError("native tank channel fields/lengths mismatch")
        for name, values in record.items():
            for i, value in enumerate(values):
                if (
                    value is None
                    and not complete
                    and reported_unknown(f"/trajectory/tanks/{tank_name}/{name}/{i}")
                ):
                    unknown = True
                else:
                    _finite(value, name, -1e30 if name == "gas_energy_j" else 0, 1e30)
    edges = {e["name"]: e for e in config["connections"]}
    if len(trajectory["flows"]) != len(times):
        raise ValueError("native flow rows do not align with clock")
    for i, row in enumerate(trajectory["flows"]):
        names = set()
        for j, flow in enumerate(row):
            name = flow["name"]
            if name not in edges or name in names:
                raise ValueError("unknown/duplicate native connection")
            names.add(name)
            edge = edges[name]
            ports = (flow["source"], flow["target"])
            if ports != (edge["source"], edge["target"]) and not (
                edge["allow_reverse"] and ports == (edge["target"], edge["source"])
            ):
                raise ValueError("native flow direction/ports mismatch")
            for key, low, high in (("mass_flow_kg_s", 0, 1e20), ("stream_enthalpy_j_kg", -1e30, 1e30)):
                if (
                    flow[key] is None
                    and not complete
                    and reported_unknown(f"/trajectory/flows/{i}/{j}/{key}")
                ):
                    unknown = True
                else:
                    _finite(flow[key], key, low, high)
    if not times or unknown:
        return {"observable_budget": "needs_data: empty or unknown prefix", "samples": len(times)}
    masses = [sum(t["mass_kg"][i] for t in tanks.values()) for i in range(len(times))]
    energies = [
        sum(
            t["gas_energy_j"][i]
            + config["tanks"][name]["parameters"]["wall_heat_capacity"] * t["wall_temperature_k"][i]
            for name, t in tanks.items()
        )
        for i in range(len(times))
    ]
    heat_rates = [
        sum(
            config["tanks"][name]["parameters"]["wall_ambient_htc"]
            * config["tanks"][name]["parameters"]["wall_ambient_area"]
            * (config["ambient_temperature_k"] - t["wall_temperature_k"][i])
            for name, t in tanks.items()
        )
        for i in range(len(times))
    ]
    heat = sum(
        (heat_rates[i] + heat_rates[i - 1]) / 2 * (times[i] - times[i - 1]) for i in range(1, len(times))
    )
    recomputed = {
        "mass_residual_kg": masses[-1] - masses[0],
        "energy_residual_j": energies[-1] - energies[0] - heat,
        "ambient_heat_j": heat,
        "initial_total_energy_j": energies[0],
    }
    balance = native.get("balance")
    if not isinstance(balance, dict) or set(balance) != set(recomputed):
        raise ValueError("network observable budget missing")
    if any(not math.isclose(balance[k], v, rel_tol=1e-12, abs_tol=1e-7) for k, v in recomputed.items()):
        raise ValueError("native balance does not match independently aggregated trajectory")
    return {
        "observable_budget": "closed-network gas+wall diagnostic; quadrature not an exact energy integral",
        "samples": len(times),
        "recomputed_balance": recomputed,
        "mass_within_1e_8_kg": abs(recomputed["mass_residual_kg"]) < 1e-8,
        "energy_within_0_5_J": abs(recomputed["energy_residual_j"]) < 0.5,
    }


def evaluate(request: StationRequest) -> dict[str, Any]:
    """Call a reviewed owner in isolation; missing reviews never inspect owner bytes."""
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "needs_data",
        "native_called": False,
        "trajectory": None,
        "scope": "reviewed station API; no facility qualification",
        **_producer_pins(),
    }
    if any(
        value is not True
        for value in (request.source_reviewed, request.model_reviewed, request.conditions_reviewed)
    ) or not all((request.source_pin, request.evidence, request.context)):
        result["reason"] = (
            "source/model/conditions review, source pin, RunContext and selected evidence required"
        )
        result["receipt_pin"] = digest(result)
        return result
    try:
        validate_config(request.config)
        context = _context(request.context, request.evidence)
        _finite(request.timeout_s, "timeout", 0.05, 120)
        for name, value in (("input", request.input_budget_bytes), ("output", request.output_budget_bytes)):
            if type(value) is not int or not 1024 <= value <= 8_000_000:
                raise ValueError(f"{name} budget")
        before = source_manifest(request.source_root)
        if digest(before) != request.source_pin:
            raise ValueError("source pin mismatch")
        payload = _payload(request.config, context, request.evidence, request.source_pin, request.source_root)
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > request.input_budget_bytes:
            raise ValueError("serialized input budget exceeded")
        result.update(
            config=request.config,
            context=context,
            evidence=list(request.evidence),
            request_pin=digest(payload),
            config_pin=digest(request.config),
            context_pin=digest(context),
            evidence_pin=digest(context["evidence"]),
            selected_evidence_pin=digest(list(request.evidence)),
            source_pin=request.source_pin,
        )
        worker = Path(__file__).with_name("station_worker.py")
        with tempfile.TemporaryDirectory(prefix="station-exchange-") as temp:
            target = Path(temp) / "response.json"
            completed = subprocess.run(
                [request.python_executable, "-I", str(worker), str(target), str(request.output_budget_bytes)],
                input=encoded,
                text=True,
                encoding="utf-8",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=request.timeout_s,
                check=False,
            )
            if (
                completed.returncode != 0
                or not target.is_file()
                or target.stat().st_size > request.output_budget_bytes
            ):
                raise ValueError(f"worker failed/missing/over budget (exit {completed.returncode})")
            response = json.loads(
                target.read_text(encoding="utf-8"),
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
            )
        if (
            set(response) != {"schema", "request_pin", "native"}
            or response["schema"] != RESULT_SCHEMA
            or response["request_pin"] != digest(payload)
        ):
            raise ValueError("worker response binding mismatch")
        result["native"] = response["native"]
        result["native_pin"] = digest(response["native"])
        result["source_unchanged"] = before == source_manifest(request.source_root)
        if not result["source_unchanged"]:
            result.update(status="error", reason="owner source changed during execution")
        else:
            audit = _native_audit(response["native"], request.config, request.source_root)
            result.update(response["native"], audits=audit)
    except subprocess.TimeoutExpired:
        result.update(status="timeout", reason="worker wall time exceeded; no completed trajectory returned")
    except (ValueError, TypeError, KeyError, OSError, AttributeError, OverflowError) as exc:
        result.update(status="invalid", reason=str(exc))
    result["receipt_pin"] = digest(result)
    return result


def validate_stored_result(
    result: dict[str, Any], source_root: str | Path | None = None, context: RunContext | None = None
) -> dict[str, Any]:
    """Unsigned local byte/semantic consistency; no proof of execution or approval."""
    try:
        body = {k: v for k, v in result.items() if k != "receipt_pin"}
        checks = {
            "schema": result["schema"] == RESULT_SCHEMA,
            "receipt": result["receipt_pin"] == digest(body),
        }
        checks.update({key: result[key] == value for key, value in _producer_pins().items()})
        if result["status"] == "needs_data" and "native" not in result:
            checks["withheld"] = result.get("native_called") is False and result.get("trajectory") is None
        else:
            if result["status"] not in {"computed", "partial"} or source_root is None or context is None:
                raise ValueError("computed/partial receipt requires source root and matching RunContext")
            canonical = _context(context, result["evidence"])
            config = result["config"]
            validate_config(config)
            payload = _payload(config, canonical, result["evidence"], result["source_pin"], source_root)
            checks.update(
                request=result["request_pin"] == digest(payload),
                config=result["config_pin"] == digest(config),
                context=result["context_pin"] == digest(canonical) and result["context"] == canonical,
                evidence=result["evidence_pin"] == digest(canonical["evidence"]),
                selected_evidence=result["selected_evidence_pin"] == digest(result["evidence"]),
                native=result["native_pin"] == digest(result["native"]),
                source=result["source_pin"] == digest(source_manifest(source_root)),
                source_unchanged=result["source_unchanged"] is True,
                semantic=result["audits"] == _native_audit(result["native"], config, source_root),
                mirrored=all(result.get(k) == v for k, v in result["native"].items()),
            )
        return {
            "valid": all(checks.values()),
            "checks": checks,
            "meaning": "unsigned local consistency; not execution, calibration or engineering approval",
        }
    except (KeyError, ValueError, TypeError, OSError, AttributeError, OverflowError) as exc:
        return {"valid": False, "reason": str(exc), "meaning": "unsigned local consistency receipt"}


def to_signal_frames(
    result: dict[str, Any], context: RunContext, *, source_root: str | Path, allow_partial: bool = False
) -> tuple[SignalFrame, ...]:
    """Expose verified native channels on an explicit clock; incomplete prefixes need opt-in."""
    if result.get("status") != "computed" and not (
        allow_partial is True and result.get("status") == "partial"
    ):
        raise ValueError("computed result required; partial prefix needs explicit allow_partial=True")
    if not validate_stored_result(result, source_root, context)["valid"]:
        raise ValueError("stored station receipt/context/source verification failed")
    trajectory = result["trajectory"]
    config = result["config"]
    if config["schema"] == SCHEMA and any(
        result["audits"].get(key) is False for key in ("mass_within_1e_8_kg", "energy_within_0_5_J")
    ):
        raise ValueError("network mass/energy diagnostic failed; downstream signals withheld")
    boundary = context.metadata["station_boundary"]
    frames = []
    if config["schema"] == FULL_SCHEMA:
        channels = {
            "vehicle_pressure_pa": "Pa_abs",
            "vehicle_temperature_k": "K",
            "vehicle_mass_kg": "kg",
            "header_pressure_pa": "Pa_abs",
            "header_temperature_k": "K",
            "header_mass_kg": "kg",
            "nozzle_mass_flow_kg_s": "kg/s",
        }
        rows = [(s["time_s"], {key: s.get(key) for key in channels}, channels) for s in trajectory["samples"]]
    else:
        rows = []
        for i, time in enumerate(trajectory["time_s"]):
            values, units = {}, {}
            for name, record in trajectory["tanks"].items():
                for field, unit in (
                    ("mass_kg", "kg"),
                    ("gas_energy_j", "J"),
                    ("wall_temperature_k", "K"),
                    ("pressure_pa_abs", "Pa_abs"),
                    ("gas_temperature_k", "K"),
                ):
                    key = f"tank.{name}.{field}"
                    values[key], units[key] = record[field][i], unit
            flows = {f["name"]: f for f in trajectory["flows"][i]}
            for edge in config["connections"]:
                flow = flows.get(edge["name"])
                sign = 1 if flow is None or flow["source"] == edge["source"] else -1
                key = f"flow.{edge['name']}.signed_nominal_mass_flow_kg_s"
                values[key], units[key] = (
                    (
                        0.0
                        if flow is None
                        else None
                        if flow["mass_flow_kg_s"] is None
                        else sign * flow["mass_flow_kg_s"]
                    ),
                    "kg/s",
                )
                key = f"flow.{edge['name']}.stream_enthalpy_j_kg"
                values[key], units[key] = (None if flow is None else flow["stream_enthalpy_j_kg"]), "J/kg"
            rows.append((time, values, units))
    for time, values, units in rows:
        frames.append(
            SignalFrame(
                stream_id=f"{context.asset_id}:{boundary['event_id']}",
                clock_id=context.clock_id,
                time_s=time + boundary["clock_offset_s"],
                values=values,
                units=units,
                valid={key: value is not None for key, value in values.items()},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode="station-native-complete"
                if result["status"] == "computed"
                else "station-native-partial-prefix",
            )
        )
    return tuple(frames)
