"""Isolated owner API caller. No thermodynamic or ODE implementation lives here."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import sys
from pathlib import Path

_NATIVE_CALLED = False


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def full_episode(payload):
    """Native full loop with no faults; any requested consequence fails closed."""
    from dataclasses import asdict

    from h2station import ReferenceScenario, build_reference_scenario

    class UnavailableConsequence:
        def evaluate_release(self, *args, **kwargs):
            raise RuntimeError("consequence scope excluded; supply reviewed owner backend in an extension")

    c = payload["config"]
    config = ReferenceScenario(
        duration_s=c["duration_s"],
        control_period_s=c["control_period_s"],
        **{"vehicle_tank_calibration": "reference", **c["scenario"]},
    )
    built = build_reference_scenario(config, UnavailableConsequence())
    samples = []
    try:
        trajectory = built.simulator.simulate(
            built.initial_state,
            config.duration_s,
            config.control_period_s,
            sample_callback=lambda v: samples.append(asdict(v)),
            pace_idle=False,
        )
        risk = [[asdict(v) for v in group] for group in trajectory.risk_snapshots]
        return {
            "status": "computed",
            "scope": "native reference full station; no faults or consequence backend",
            "trajectory": {
                "time_s": trajectory.time_s.tolist(),
                "samples": samples,
                "vehicle_pressure_pa_abs": trajectory.vehicle_pressure_pa.tolist(),
                "nozzle_mass_flow_kg_s": trajectory.nozzle_mass_flow_kg_s.tolist(),
                "header_mass_kg": trajectory.header_mass_kg.tolist(),
                "risk_snapshots": risk,
            },
            "balance": None,
            "balance_status": "needs_data: full-loop mass/energy boundary ledger not exposed here",
            "esd_time_s": trajectory.esd_time_s,
            "native_called": True,
            "limitations": [
                "no leak/fault injection, HyRAM, HRA, external commands or LLM",
                "no facility qualification or full-loop balance claim",
            ],
        }
    except Exception as exc:
        return {
            "status": "partial",
            "native_called": True,
            "trajectory": {"samples": samples},
            "reason": f"{type(exc).__name__}: {exc}",
        }


def run(payload):
    global _NATIVE_CALLED
    root = Path(payload["source_root"])
    sys.path.insert(0, str(root / "src"))
    scipy_version = tuple(int(v) for v in importlib.metadata.version("scipy").split(".")[:2])
    if not (1, 11) <= scipy_version < (1, 16):
        raise RuntimeError("owner requires scipy>=1.11,<1.16; select the isolated compatible interpreter")
    import h2station
    import numpy as np
    from h2station import (
        HydrogenEOS,
        LumpedTank,
        NetworkCommands,
        RealGasRestriction,
        RestrictionConnection,
        RestrictionParameters,
        StationNetwork,
        TankParameters,
    )

    loaded = Path(h2station.__file__).resolve()
    if loaded != (root / "src/h2station/__init__.py").resolve():
        raise ValueError("unexpected owner import location")
    c = payload["config"]
    _NATIVE_CALLED = True
    if c["schema"] == "station-reference-episode/1":
        answer = full_episode(payload)
        answer.update(
            loaded_owner=str(loaded),
            owner_version=h2station.__version__,
            runtime={n: importlib.metadata.version(n) for n in ("numpy", "scipy", "fluids", "ht")},
            python_executable=sys.executable,
            property_basis="Hydrogen TABLE; pinned src/h2station/data/hydrogen_properties_v1.npz",
        )
        return answer
    eos = HydrogenEOS()
    tanks = {name: LumpedTank(TankParameters(**v["parameters"]), eos) for name, v in c["tanks"].items()}
    initial = {
        name: tanks[name].initial_inventory(v["pressure_pa_abs"], v["temperature_k"])
        for name, v in c["tanks"].items()
    }
    edges = tuple(
        RestrictionConnection(
            e["name"],
            e["source"],
            e["target"],
            RealGasRestriction(RestrictionParameters(diameter=e["diameter_m"]), eos),
            allow_reverse=e["allow_reverse"],
        )
        for e in c["connections"]
    )
    network = StationNetwork(tanks, restrictions=edges)
    calls = 0

    def command(t, states):
        nonlocal calls
        calls += 1
        if calls > c["maximum_rhs_calls"]:
            raise RuntimeError("RHS call budget exhausted")
        item = next(x for x in reversed(c["commands"]) if x["time_s"] <= t)
        return NetworkCommands(restriction_openings=item["openings"])

    # Segment command discontinuities and retain completed segments on owner errors.
    times = []
    records = {
        name: {
            "mass_kg": [],
            "gas_energy_j": [],
            "wall_temperature_k": [],
            "pressure_pa_abs": [],
            "gas_temperature_k": [],
        }
        for name in tanks
    }
    flow_records = []
    heat_rates = []
    state = initial
    grid = np.linspace(0, c["duration_s"], c["sample_count"])
    boundaries = sorted(set([0, c["duration_s"], *[v["time_s"] for v in c["commands"]]]))
    success = True
    message = ""
    for a, b in zip(boundaries[:-1], boundaries[1:], strict=False):
        selected = np.unique(np.concatenate(([a, b], grid[(grid >= a) & (grid <= b)])))
        try:
            episode = network.simulate(
                state,
                (a, b),
                command,
                c["ambient_temperature_k"],
                evaluation_times=selected,
                maximum_step=c["maximum_step_s"],
                relative_tolerance=1e-8,
                absolute_tolerance=1e-9,
            )
            message = episode.solver_message
            success = bool(episode.solver_success)
            for i, t in enumerate(episode.time):
                if times and float(t) <= times[-1]:
                    continue
                state_i = {
                    name: type(initial[name])(*(float(x[i]) for x in episode.inventories[name]))
                    for name in tanks
                }
                states = network.thermodynamic_states(state_i)
                item = next(x for x in reversed(c["commands"]) if x["time_s"] <= t)
                _, flows = network.evaluate(
                    state_i,
                    NetworkCommands(restriction_openings=item["openings"]),
                    c["ambient_temperature_k"],
                )
                times.append(float(t))
                heat_rates.append(
                    sum(
                        tanks[n].parameters.wall_ambient_htc
                        * tanks[n].parameters.wall_ambient_area
                        * (c["ambient_temperature_k"] - state_i[n].wall_temperature)
                        for n in tanks
                    )
                )
                flow_records.append(
                    [
                        {
                            "name": f.name,
                            "source": f.source,
                            "target": f.target,
                            "mass_flow_kg_s": f.mass_flow,
                            "stream_enthalpy_j_kg": f.stream_enthalpy,
                        }
                        for f in flows
                    ]
                )
                for name in tanks:
                    rec = records[name]
                    inv = state_i[name]
                    for key, value in zip(
                        rec,
                        (
                            inv.mass,
                            inv.internal_energy,
                            inv.wall_temperature,
                            states[name].pressure,
                            states[name].temperature,
                        ),
                        strict=False,
                    ):
                        rec[key].append(float(value))
            if len(episode.time):
                state = {
                    name: type(initial[name])(*(float(x[-1]) for x in episode.inventories[name]))
                    for name in tanks
                }
            if not success:
                break
        except Exception as exc:
            success = False
            message = f"{type(exc).__name__}: {exc}"
            break
    budget = None
    if times:
        mass = np.sum([r["mass_kg"] for r in records.values()], axis=0)
        energy = np.sum(
            [
                np.asarray(r["gas_energy_j"])
                + tanks[n].parameters.wall_heat_capacity * np.asarray(r["wall_temperature_k"])
                for n, r in records.items()
            ],
            axis=0,
        )
        heat = float(np.trapezoid(heat_rates, times))
        budget = {
            "mass_residual_kg": float(mass[-1] - mass[0]),
            "energy_residual_j": float(energy[-1] - energy[0] - heat),
            "ambient_heat_j": heat,
            "initial_total_energy_j": float(energy[0]),
        }
    return {
        "status": "computed" if success else "partial",
        "native_called": True,
        "solver_success": success,
        "solver_message": message,
        "rhs_calls": calls,
        "loaded_owner": str(loaded),
        "owner_version": h2station.__version__,
        "runtime": {n: importlib.metadata.version(n) for n in ("numpy", "scipy", "fluids", "ht")},
        "property_basis": "Hydrogen TABLE; pinned src/h2station/data/hydrogen_properties_v1.npz",
        "python_version": sys.version,
        "python_executable": sys.executable,
        "trajectory": {"time_s": times, "tanks": records, "flows": flow_records},
        "balance": budget,
        "limitations": [
            "synthetic closed network; no full station, vehicle, PLC, risk or experimental validation",
            "energy quadrature diagnostic; interrupted native solve has no recoverable internal samples",
        ],
    }


if __name__ == "__main__":
    payload = None
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        answer = run(payload)
        nonfinite = []

        def preserve_unknown(value, path=""):
            if isinstance(value, float) and not math.isfinite(value):
                nonfinite.append({"path": path, "original": str(value)})
                return None
            if isinstance(value, dict):
                return {k: preserve_unknown(v, f"{path}/{k}") for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [preserve_unknown(v, f"{path}/{i}") for i, v in enumerate(value)]
            return value

        answer = preserve_unknown(answer)
        if nonfinite:
            answer.update(status="partial", nonfinite_observations=nonfinite)
        envelope = {"schema": "station-exchange/2", "request_pin": digest(payload), "native": answer}
        encoded = json.dumps(envelope, allow_nan=False)
        if len(encoded.encode("utf-8")) > int(sys.argv[2]):
            raise ValueError("serialized output exceeds budget")
    except Exception as exc:
        encoded = json.dumps(
            {
                "schema": "station-exchange/2",
                "request_pin": digest(payload),
                "native": {
                    "status": "error",
                    "native_called": _NATIVE_CALLED,
                    "trajectory": None,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            }
        )
    Path(sys.argv[1]).write_text(encoded, encoding="utf-8")
