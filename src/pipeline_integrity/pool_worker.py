"""JSON-only subprocess worker: invoke owner constructors and retain every ledger field."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path


def canonical_digest(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def run(payload):
    root = Path(payload["source_root"])
    sys.path.insert(0, str(root / "src"))
    import dynamiclh2poolx as sdk

    loaded = Path(sdk.__file__).resolve()
    if (
        loaded != (root / "src/dynamiclh2poolx/__init__.py").resolve()
        or sdk.__version__ != payload["sdk_version"]
    ):
        raise ValueError("SDK version/import location mismatch")
    c = payload["config"]
    inflow = sdk.DeclaredInflow(tuple(c["inflow"]["times_s"]), tuple(c["inflow"]["rates_kg_s"]))
    common = {
        "ambient_pressure_Pa": c["ambient_pressure_pa_abs"],
        "initial_liquid_mass_kg": c["initial_liquid_mass_kg"],
    }
    if c["route"] == "spreading":
        surface = (
            sdk.SolidSemiInfiniteSurface(**c["surface"]["kwargs"])
            if c["surface"]["kind"] == "solid"
            else sdk.ConstantHeatFluxWaterSurface(**c["surface"]["kwargs"])
        )
        config = sdk.DynamicPoolConfig(
            surface=surface,
            numerics=sdk.ShallowLayerNumerics(**c["numerics"]),
            evaporation_momentum_closure=c["evaporation_momentum_closure"],
            **common,
        )
        result = sdk.run_dynamic_pool(config, inflow, tuple(c["output_times_s"]))
        calls = [
            "DeclaredInflow",
            type(surface).__name__,
            "ShallowLayerNumerics",
            "DynamicPoolConfig",
            "run_dynamic_pool",
        ]
    else:
        config = sdk.FixedAreaPoolConfig(
            substrate=sdk.Substrate(**c["surface"]["kwargs"]), **c["fixed_area"], **common
        )
        result = sdk.run_fixed_area_pool(config, inflow, tuple(c["output_times_s"]))
        calls = ["DeclaredInflow", "Substrate", "FixedAreaPoolConfig", "run_fixed_area_pool"]
    return {
        "status": "native_complete",
        "native_called": True,
        "native": asdict(result),
        "sdk_calls": calls,
        "loaded_sdk": str(loaded),
        "sdk_version": sdk.__version__,
        "runtime": {n: importlib.metadata.version(n) for n in ("CoolProp", "numpy")},
        "python_executable": sys.executable,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read())
    binding = {"schema": payload["schema"], "request_pin": canonical_digest(payload)}
    try:
        answer = {**run(payload), **binding}
        nonfinite = []

        def clean(value, path=""):
            if isinstance(value, float) and not math.isfinite(value):
                nonfinite.append({"path": path, "original": str(value)})
                return None
            if isinstance(value, dict):
                return {k: clean(v, f"{path}/{k}") for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(v, f"{path}/{i}") for i, v in enumerate(value)]
            return value

        answer = clean(answer)
        if nonfinite:
            answer.update(status="partial", nonfinite_observations=nonfinite)
        text = json.dumps(answer, allow_nan=False)
        if len(text.encode()) > int(sys.argv[2]):
            raise ValueError("serialized output budget exceeded; no ledger returned")
    except Exception as exc:
        text = json.dumps(
            {
                **binding,
                "status": "error",
                "native_called": True,
                "native": None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )
    Path(sys.argv[1]).write_text(text, encoding="utf-8")
