"""Private native ledgers and public aggregate verification of the pool exchange."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pipeline_integrity.contracts import EvidenceRef, RunContext  # noqa: E402
from pipeline_integrity.pool_exchange import (  # noqa: E402
    COMMIT,
    PoolRequest,
    digest,
    evaluate_pool_request,
    reference_config,
    source_manifest,
    to_signal_frames,
    validate_stored_result,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/pool_exchange")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    manifest = source_manifest(args.source)
    configs = {
        "solid_spreading": reference_config(),
        "water_spreading": reference_config(),
        "fixed_analytic": reference_config("fixed_area"),
        "fixed_zero": reference_config("fixed_area"),
        "micro": reference_config(),
        "wet_unreported": reference_config(),
        "dryout": reference_config("fixed_area"),
        "domain_escape": reference_config(),
        "refined": reference_config(),
        "zoh_final_hold": reference_config("fixed_area"),
    }
    water = {
        "kind": "water",
        "kwargs": {"heat_flux_W_m2": 1000.0, "calibration_label": "synthetic constant-flux comparison"},
    }
    configs["water_spreading"]["surface"] = copy.deepcopy(water)
    configs["wet_unreported"]["surface"] = copy.deepcopy(water)
    configs["wet_unreported"]["numerics"]["reported_front_depth_m"] = 1.0
    configs["micro"]["inflow"]["rates_kg_s"] = [1e-7, 0.0]
    configs["fixed_analytic"]["initial_liquid_mass_kg"] = 1.0
    configs["fixed_analytic"]["inflow"]["rates_kg_s"] = [0.0, 0.0]
    configs["fixed_zero"]["inflow"]["rates_kg_s"] = [0.0, 0.0]
    configs["dryout"]["initial_liquid_mass_kg"] = 0.001
    configs["dryout"]["inflow"]["rates_kg_s"] = [0.0, 0.0]
    configs["domain_escape"]["surface"] = copy.deepcopy(water)
    configs["domain_escape"]["surface"]["kwargs"]["heat_flux_W_m2"] = 1.0
    configs["domain_escape"]["numerics"].update(
        domain_radius_m=0.2, radial_step_m=0.02, maximum_time_step_s=0.005
    )
    configs["domain_escape"]["inflow"]["rates_kg_s"] = [1.0, 1.0]
    configs["refined"]["numerics"].update(radial_step_m=0.025, maximum_time_step_s=0.005)
    configs["zoh_final_hold"]["initial_liquid_mass_kg"] = 1.0
    configs["zoh_final_hold"]["fixed_area"]["initial_thermal_age_s"] = 2.0
    configs["zoh_final_hold"]["inflow"] = {"times_s": [0.0, 0.2, 0.5], "rates_kg_s": [0.0, 0.01, 0.02]}
    answers, contexts, checks = {}, {}, {}
    for name, config in configs.items():
        config["boundary"]["clock_offset_s"] = 20.0
        config["boundary"]["enu_origin_m"] = [1.0, -2.0, 0.0]
        context = RunContext(
            asset_id="synthetic-ground",
            scenario_id=name,
            clock_id="synthetic-clock",
            origin="synthetic",
            evidence=(
                EvidenceRef(
                    id="synthetic",
                    citation="author-defined verification",
                    origin="synthetic",
                    limitations=("not field validation",),
                ),
            ),
            metadata={"pool_boundary": config["boundary"]},
        )
        request = PoolRequest(
            str(args.source),
            config,
            context,
            source_pin=digest(manifest),
            boundary_reviewed=True,
            source_reviewed=True,
            model_reviewed=True,
        )
        answer = evaluate_pool_request(request)
        private = asdict(request)
        private["context"] = context.model_dump(mode="json")
        (args.artifacts / f"{name}_request.json").write_text(json.dumps(private, indent=2), encoding="utf-8")
        (args.artifacts / f"{name}_response.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
        answers[name], contexts[name] = answer, context
        checks[f"{name}_native"] = (
            answer["status"] in {"computed", "out_of_scope"} and answer.get("native_called") is True
        )
        if answer.get("native") is None:
            continue
        checks[f"{name}_mass"] = all(
            abs(r["mass_residual_kg"]) < 1e-8 and abs(r["inflow_difference_kg"]) < 1e-8
            for r in answer["audits"]
        )
        checks[f"{name}_receipt"] = validate_stored_result(answer, args.source, context)["valid"]
        if name == "fixed_analytic":
            tampered = copy.deepcopy(answer)
            tampered["config"]["boundary"]["enu_origin_m"][0] += 1
            checks["receipt_rejects_enu_tamper"] = not validate_stored_result(tampered, args.source, context)[
                "valid"
            ]
            tampered = copy.deepcopy(answer)
            tampered["native"]["ledger"][-1]["liquid_mass_kg"] += 1
            checks["receipt_rejects_native_tamper"] = not validate_stored_result(
                tampered, args.source, context
            )["valid"]
            for fault in (
                "negative_inventory",
                "negative_evaporation",
                "decreasing_evaporation",
                "scope_escalation",
                "mass_imbalance",
                "energy_claim",
            ):
                tampered = copy.deepcopy(answer)
                last = tampered["native"]["ledger"][-1]
                if fault == "negative_inventory":
                    last["liquid_mass_kg"] = -1000.0
                elif fault == "negative_evaporation":
                    last["cumulative_evaporation_kg"] = -1.0
                elif fault == "decreasing_evaporation":
                    last["cumulative_evaporation_kg"] = (
                        tampered["native"]["ledger"][-2]["cumulative_evaporation_kg"] / 2
                    )
                elif fault == "scope_escalation":
                    last["source_status"] = "out_of_scope"
                elif fault == "mass_imbalance":
                    last["liquid_mass_kg"] += 1.0
                else:
                    tampered["energy_balance_status"] = "verified"
                tampered["native_pin"] = digest(tampered["native"])
                tampered["receipt_pin"] = digest({k: v for k, v in tampered.items() if k != "receipt_pin"})
                checks[f"rehashed_receipt_rejects_{fault}"] = not validate_stored_result(
                    tampered, args.source, context
                )["valid"]
                try:
                    to_signal_frames(tampered, context)
                except (ValueError, KeyError, TypeError):
                    checks[f"signal_export_rejects_{fault}"] = True
                else:
                    checks[f"signal_export_rejects_{fault}"] = False
        if answer["status"] == "computed":
            frames = to_signal_frames(answer, context)
            total = sum(
                (f.values["evaporation_interval_mean_kg_s"] or 0) * (f.time_s - frames[i - 1].time_s)
                for i, f in enumerate(frames)
                if i
            )
            rows = answer["native"]["ledger"]
            checks[f"{name}_signals"] = (
                frames[0].time_s == 20
                and not frames[0].valid["evaporation_interval_mean_kg_s"]
                and frames[0].values["evaporation_interval_mean_kg_s"] is None
                and abs(total - rows[-1]["cumulative_evaporation_kg"]) < 1e-10
            )
    import CoolProp.CoolProp as CP

    pressure = 101325.0
    tsat = CP.PropsSI("T", "P", pressure, "Q", 0, "HEOS::Hydrogen")
    rho = CP.PropsSI("D", "P", pressure, "Q", 0, "HEOS::Hydrogen")
    latent = CP.PropsSI("H", "P", pressure, "Q", 1, "HEOS::Hydrogen") - CP.PropsSI(
        "H", "P", pressure, "Q", 0, "HEOS::Hydrogen"
    )
    if all(a.get("native") for a in answers.values()):
        rows = answers["fixed_analytic"]["native"]["ledger"]
        # Independently integrate the declared uncapped solid heat flux over [0,t].
        expected = 2 * 0.1 * 1.0 * (100 - tsat) / (latent * math.sqrt(math.pi * 1e-6))
        checks["fixed_analytic_integral"] = abs(rows[-1]["cumulative_evaporation_kg"] - expected) < 1e-12
        checks["property_temperature"] = abs(rows[-1]["pool_temperature_K"] - tsat) < 1e-12
        checks["property_density"] = abs(rows[-1]["liquid_density_kg_m3"] - rho) < 1e-12
        checks["zero_input_empty"] = all(
            r["liquid_mass_kg"] == 0 for r in answers["fixed_zero"]["native"]["ledger"]
        )
        checks["micro_inflow_preserved"] = (
            abs(answers["micro"]["native"]["ledger"][-1]["cumulative_inflow_kg"] - 5e-8) < 1e-20
        )
        checks["wet_report_separation"] = any(
            r["liquid_mass_kg"] > 0 and r["wet_area_m2"] > 0 and r["reported_area_m2"] == 0
            for r in answers["wet_unreported"]["native"]["ledger"]
        )
        checks["dryout"] = (
            bool(answers["dryout"]["native"]["disappearance_times_s"])
            and answers["dryout"]["native"]["ledger"][-1]["liquid_mass_kg"] == 0
        )
        checks["domain_escape_retained"] = (
            answers["domain_escape"]["status"] == "out_of_scope"
            and answers["domain_escape"]["native"]["ledger"][-1]["escaped_domain_mass_kg"] > 0
        )
        checks["final_zoh_hold"] = (
            abs(answers["zoh_final_hold"]["native"]["ledger"][-1]["cumulative_inflow_kg"] - 0.013) < 1e-12
        )
        checks["continuous_thermal_age"] = (
            abs(
                answers["zoh_final_hold"]["native"]["ledger"][-1]["cumulative_evaporation_kg"]
                - expected * (math.sqrt(3) - math.sqrt(2))
            )
            < 1e-12
        )
        checks["refinement_diagnostic"] = (
            abs(
                answers["solid_spreading"]["native"]["ledger"][-1]["liquid_mass_kg"]
                - answers["refined"]["native"]["ledger"][-1]["liquid_mass_kg"]
            )
            < 0.005
        )
    checks["source_unchanged"] = source_manifest(args.source) == manifest
    faults = {}
    base_config = reference_config("fixed_area")
    base_context = contexts["fixed_analytic"].model_copy(
        update={"metadata": {"pool_boundary": base_config["boundary"]}}
    )
    for name in ("native_saturation_error", "response_budget", "timeout"):
        config = copy.deepcopy(base_config)
        if name == "native_saturation_error":
            config["ambient_pressure_pa_abs"] = 100e6
        request = PoolRequest(
            str(args.source),
            config,
            base_context,
            source_pin=digest(manifest),
            boundary_reviewed=True,
            source_reviewed=True,
            model_reviewed=True,
            timeout_s=0.05 if name == "timeout" else 30.0,
            output_budget_bytes=2048 if name == "response_budget" else 2_000_000,
        )
        answer = evaluate_pool_request(request)
        expected_status = "timeout" if name == "timeout" else "error"
        checks[f"fault_{name}_retained"] = answer["status"] == expected_status and answer["native"] is None
        faults[name] = {
            "status": answer["status"],
            "native_called": answer["native_called"],
            "reason_class": (answer.get("reason") or "").split(":")[0],
        }
        private = asdict(request)
        private["context"] = request.context.model_dump(mode="json")
        (args.artifacts / f"{name}_request.json").write_text(json.dumps(private, indent=2), encoding="utf-8")
        (args.artifacts / f"{name}_response.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
    paths = [
        "src/pipeline_integrity/pool_exchange.py",
        "src/pipeline_integrity/pool_worker.py",
        "tests/test_pool_exchange.py",
        "examples/pool_exchange.py",
        "scripts/check_pool_exchange.py",
        "docs/POOL_EXCHANGE_KO.md",
    ]
    summary = {}
    for name, answer in answers.items():
        native = answer.get("native") or {}
        rows = native.get("ledger", [])
        summary[name] = {
            "status": answer["status"],
            "native_called": answer["native_called"],
            "row_count": len(rows),
            "native_mass_residual_kg": native.get("mass_balance_residual_kg"),
            "max_independent_mass_residual_kg": max(
                (abs(r["mass_residual_kg"]) for r in answer.get("audits", [])), default=None
            ),
            "source_statuses": sorted({r["source_status"] for r in rows}),
            "warning_count": sum(bool(r["warning"]) for r in rows),
            "sdk_calls": answer.get("sdk_calls"),
            "runtime": answer.get("runtime"),
        }
    report = {
        "schema": "pool-exchange-verification/1",
        "sdk_version": "0.2.0rc2",
        "commit": COMMIT,
        "source": "https://github.com/lyullee/DynamicLH2PoolX",
        "license": "MIT",
        "source_pin": digest(manifest),
        "source_files": {Path(k).name: v for k, v in manifest.items()},
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "cases": summary,
        "faults": faults,
        "adapter_files": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
        "units": {"pressure": "Pa_abs", "time": "s", "mass": "kg", "rate": "kg/s", "origin": "ENU m"},
        "limitations": [
            "synthetic verification only",
            "no energy balance ledger",
            "no jet impact, deposition or dispersion",
            "water empirical closure approximately 50-60s",
            "receipt is unsigned consistency information",
        ],
    }
    (ROOT / "research/pool_exchange_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "cases": {k: a["status"] for k, a in answers.items()},
            },
            indent=2,
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
