"""Run private native episodes and publish aggregate diagnostics only."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pipeline_integrity.contracts import EvidenceRef, RunContext  # noqa: E402
from pipeline_integrity.station_exchange import (  # noqa: E402
    FULL_SCHEMA,
    INPUT_UNITS,
    StationRequest,
    digest,
    evaluate,
    reference_boundary,
    reference_config,
    source_manifest,
    to_signal_frames,
    validate_stored_result,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--upstream-source", type=Path)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/station_exchange")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    manifest = source_manifest(args.source)
    original = source_manifest(args.upstream_source) if args.upstream_source else None
    request = StationRequest(
        str(args.source),
        args.python,
        reference_config(),
        RunContext(
            asset_id="synthetic-station",
            scenario_id="bounded-native-episodes",
            clock_id="station-episode",
            origin="synthetic",
            evidence=(
                EvidenceRef(
                    id="authored-case",
                    citation="Original synthetic engineering verification; no field validation",
                    origin="synthetic",
                    license_id="MIT",
                ),
            ),
            metadata={
                "station_boundary": {
                    **reference_boundary("synthetic-station", "station-episode"),
                    "clock_offset_s": 30.0,
                }
            },
        ),
        evidence=("authored-case",),
        source_pin=digest(manifest),
        source_reviewed=True,
        model_reviewed=True,
        conditions_reviewed=True,
        timeout_s=60,
    )
    configs = {
        "open": reference_config(),
        "closed": reference_config(),
        "equal": reference_config(),
        "reverse_blocked": reference_config(),
        "reverse_allowed": reference_config(),
        "refined": reference_config(),
        "switched": reference_config(),
        "three_nodes": reference_config(),
    }
    configs["closed"]["commands"][0]["openings"]["transfer"] = 0
    configs["equal"]["tanks"]["receiver"]["pressure_pa_abs"] = 20e6
    for key in ("reverse_blocked", "reverse_allowed"):
        configs[key]["tanks"]["receiver"]["pressure_pa_abs"] = 30e6
    configs["reverse_allowed"]["connections"][0]["allow_reverse"] = True
    configs["refined"]["maximum_step_s"] = 0.025
    configs["switched"]["commands"].append({"time_s": 0.5, "openings": {"transfer": 0.0}})
    third = configs["three_nodes"]
    third["tanks"]["third"] = copy.deepcopy(third["tanks"]["receiver"])
    third["tanks"]["third"]["pressure_pa_abs"] = 1e6
    third["connections"].append(
        {
            "name": "second",
            "source": "receiver",
            "target": "third",
            "diameter_m": 0.0005,
            "allow_reverse": False,
        }
    )
    third["commands"][0]["openings"]["second"] = 0.2
    configs["full_reference"] = {
        "schema": FULL_SCHEMA,
        "units": INPUT_UNITS,
        "duration_s": 0.4,
        "control_period_s": 0.2,
        "scenario": {"vehicle_tank_calibration": "reference"},
    }
    answers = {}
    for name, config in configs.items():
        item = replace(request, config=config)
        answer = evaluate(item)
        (args.artifacts / f"{name}_request.json").write_text(
            json.dumps({**asdict(item), "context": item.context.model_dump(mode="json")}, indent=2),
            encoding="utf-8",
        )
        (args.artifacts / f"{name}_response.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
        answers[name] = answer
    limited = copy.deepcopy(configs["switched"])
    limited["maximum_rhs_calls"] = 100
    limited_request = replace(request, config=limited)
    limited_answer = evaluate(limited_request)
    (args.artifacts / "limited_request.json").write_text(
        json.dumps(
            {**asdict(limited_request), "context": limited_request.context.model_dump(mode="json")}, indent=2
        ),
        encoding="utf-8",
    )
    (args.artifacts / "limited_response.json").write_text(
        json.dumps(limited_answer, indent=2), encoding="utf-8"
    )
    checks = {}
    checks["partial_preserves_prefix"] = (
        limited_answer["status"] == "partial"
        and len(limited_answer["trajectory"]["time_s"]) > 0
        and limited_answer["trajectory"]["time_s"][-1] < 1
    )
    if original is not None:
        checks["upstream_unchanged"] = source_manifest(args.upstream_source) == original
        checks["snapshot_preserves_upstream_files"] = all(manifest.get(k) == v for k, v in original.items())
    for name, answer in answers.items():
        checks[f"{name}_native_success"] = (
            answer["status"] == "computed" and answer.get("source_unchanged") is True
        )
        if answer.get("balance"):
            checks[f"{name}_mass"] = abs(answer["balance"]["mass_residual_kg"]) < 1e-8
            checks[f"{name}_energy"] = abs(answer["balance"]["energy_residual_j"]) < 0.5
    if all(a["status"] == "computed" for a in answers.values()):

        def change(name, tank="bank"):
            values = answers[name]["trajectory"]["tanks"][tank]["mass_kg"]
            return values[-1] - values[0]

        checks.update(
            open_sign=change("open") < 0,
            closed=abs(change("closed")) < 1e-12,
            equal=abs(change("equal")) < 1e-12,
            reverse_blocked=abs(change("reverse_blocked")) < 1e-12,
            reverse_allowed=change("reverse_allowed") > 0,
            refinement=abs(change("open") - change("refined")) < 1e-7,
            three_nodes=change("three_nodes", "third") > 0,
            clock=all(
                math.isclose(t, i / 20, abs_tol=1e-14)
                for i, t in enumerate(answers["open"]["trajectory"]["time_s"])
            ),
            no_active_risk=not any(answers["full_reference"]["trajectory"]["risk_snapshots"]),
        )
    for name, answer in answers.items():
        checks[f"{name}_stored_receipt"] = validate_stored_result(answer, args.source, request.context)[
            "valid"
        ]
        if answer["status"] == "computed":
            frames = to_signal_frames(answer, request.context, source_root=args.source)
            checks[f"{name}_signal_clock"] = (
                len(frames) == len(answer["trajectory"]["time_s"])
                and all(f.clock_id == request.context.clock_id for f in frames)
                and all(
                    math.isclose(f.time_s, t + 30.0)
                    for f, t in zip(frames, answer["trajectory"]["time_s"], strict=True)
                )
            )
            checks[f"{name}_signal_origin"] = all(f.origin == "synthetic" for f in frames)
            if name == "reverse_allowed":
                checks["reverse_signal_signed_nominal"] = (
                    frames[0].values["flow.transfer.signed_nominal_mass_flow_kg_s"] < 0
                )
            if name == "closed":
                checks["closed_signal_no_flow"] = all(
                    f.values["flow.transfer.signed_nominal_mass_flow_kg_s"] == 0 for f in frames
                )
                checks["closed_stream_enthalpy_unknown"] = all(
                    f.values["flow.transfer.stream_enthalpy_j_kg"] is None
                    and not f.valid["flow.transfer.stream_enthalpy_j_kg"]
                    for f in frames
                )
    checks["partial_stored_receipt"] = validate_stored_result(limited_answer, args.source, request.context)[
        "valid"
    ]
    try:
        to_signal_frames(limited_answer, request.context, source_root=args.source)
        checks["partial_default_export_refused"] = False
    except ValueError:
        checks["partial_default_export_refused"] = True
    limited_frames = to_signal_frames(
        limited_answer, request.context, source_root=args.source, allow_partial=True
    )
    checks["partial_opt_in_export"] = len(limited_frames) == len(
        limited_answer["trajectory"]["time_s"]
    ) and all(f.operating_mode == "station-native-partial-prefix" for f in limited_frames)
    corruptions = {}
    for mode in (
        "clock",
        "ports",
        "balance",
        "property_basis",
        "owner",
        "selected_evidence",
        "mirror",
        "worker_pin",
    ):
        forged = copy.deepcopy(answers["open"])
        native = forged["native"]
        if mode == "clock":
            native["trajectory"]["time_s"][-1] = 0.99
        elif mode == "ports":
            native["trajectory"]["flows"][0][0]["source"] = "receiver"
        elif mode == "balance":
            native["balance"]["mass_residual_kg"] = 1.0
        elif mode == "property_basis":
            native["property_basis"] = "unreviewed HEOS"
        elif mode == "owner":
            native["loaded_owner"] = "unreviewed-owner"
        elif mode == "selected_evidence":
            forged["evidence"] = ["unlisted-evidence"]
            forged["selected_evidence_pin"] = digest(forged["evidence"])
        elif mode == "mirror":
            forged["status"] = "partial"
        else:
            forged["worker_pin"] = "0" * 64
        forged["native_pin"] = digest(native)
        if mode != "mirror":
            forged.update(native)
        forged["receipt_pin"] = digest({k: v for k, v in forged.items() if k != "receipt_pin"})
        corruptions[mode] = not validate_stored_result(forged, args.source, request.context)["valid"]
        checks[f"corruption_{mode}_refused"] = corruptions[mode]
    summaries = {
        name: {
            key: a.get(key)
            for key in (
                "status",
                "balance",
                "reason",
                "runtime",
                "rhs_calls",
                "owner_version",
                "source_unchanged",
                "scope",
                "limitations",
            )
        }
        for name, a in answers.items()
    }
    files = [
        ROOT / "src/pipeline_integrity/station_exchange.py",
        ROOT / "src/pipeline_integrity/station_worker.py",
        Path(__file__),
        ROOT / "tests/test_station_exchange.py",
        ROOT / "examples/station_exchange.py",
        ROOT / "docs/STATION_EXCHANGE_KO.md",
    ]
    report = {
        "schema": "station-exchange-verification/1",
        "source_pin": request.source_pin,
        "source_version": "0.1.2",
        "license": "MIT",
        "readiness": "research alpha; no engineering qualification",
        "owner_files": manifest,
        "upstream_pin": digest(original) if original else None,
        "derived_table": {
            "generator": "scripts/generate_hydrogen_table.py",
            "CoolProp": "8.0.0",
            "original_table_missing": original is not None
            and "src/h2station/data/hydrogen_properties_v1.npz" not in original,
        },
        "checks": checks,
        "check_meaning": "observable numerical budgets and authored behavioural/contract checks, not independent full-engine or field validation",
        "rehashed_corruptions_refused": corruptions,
        "passed": sum(checks.values()),
        "total": len(checks),
        "cases": summaries,
        "limited_case": {
            k: limited_answer.get(k) for k in ("status", "solver_message", "rhs_calls", "balance")
        },
        "adapter_files": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        },
        "limits": [
            "synthetic verification, not experimental validation",
            "full-loop balance remains needs_data",
            "HyRAM consequence, active faults, external control and LLM excluded",
        ],
    }
    (ROOT / "research/station_exchange_results.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "cases": {n: a["status"] for n, a in answers.items()},
            },
            indent=2,
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
