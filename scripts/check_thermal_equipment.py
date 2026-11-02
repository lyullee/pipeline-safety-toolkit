"""Reproduce two native thermal signatures without copying their owner solvers."""

import argparse
import json
import math
import runpy
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--only", choices=("train", "phase", "both"), default="both")
    args = parser.parse_args()
    if args.owner:
        sys.path.insert(0, str((args.owner / "src").resolve()))
    sys.path.insert(0, str(ROOT))
    from pipeline_integrity.contracts import digest
    from pipeline_integrity.datasets import file_digest

    def save(name, records, checks, files):
        directory = ROOT / "artifacts" / name
        directory.mkdir(parents=True, exist_ok=True)
        for key, record in records.items():
            (directory / (key + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        providers = [
            r.get("backend") or r.get("provider")
            for r in records.values()
            if r.get("backend") or r.get("provider")
        ]
        checks["actual_native_provider_pins_stable"] = bool(providers) and all(
            v == providers[0] for v in providers
        )
        result = {
            "schema": f"pipeline_integrity.{name}_verification.v1",
            "checks": checks,
            "passed": sum(checks.values()),
            "total": len(checks),
            "records": len(records),
            "statuses": dict(Counter(r["status"] for r in records.values())),
            "file_sha256": {p: file_digest(ROOT / p) for p in files},
            "criteria_mix_contract_numeric_source_and_replay": True,
            "independent_physical_validation": False,
            "native_provider": providers[0] if providers else None,
            "owner_source_url": "https://github.com/lyullee/lh2dt_models",
        }
        filename = (
            "compressor_train_results.json" if name == "compressor_train" else "phase_change_results.json"
        )
        (ROOT / "research" / filename).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "module": name,
                    "passed": result["passed"],
                    "total": result["total"],
                    "records": result["records"],
                    "statuses": result["statuses"],
                }
            )
        )
        if not all(checks.values()):
            raise SystemExit("native thermal verification failed")

    if args.only in ("train", "both"):
        from pipeline_integrity.compressor_train import (
            CompressorTrainRequest,
            boundary_metadata,
            evaluate_compressor_train,
            to_signal_frame,
            verify_compressor_train_record,
        )

        make = runpy.run_path(str(ROOT / "examples/compressor_train.py"))["make_case"]
        records, checks = {}, {}
        for key, options in (
            ("unreviewed", {}),
            ("hydrogen_two_stage", {"reviewed": True}),
            (
                "parahydrogen_actual",
                {"reviewed": True, "fluid": "ParaHydrogen", "stage_count": 1, "convention": "actual"},
            ),
            ("hydrogen_three_stage", {"reviewed": True, "stage_count": 3}),
        ):
            q, c = make(**options)
            r = evaluate_compressor_train(q, c)
            records[key] = r
            checks[key + ":replay"] = verify_compressor_train_record(r, request=q, context=c)["valid"]
            checks[key + ":status"] = r["status"] == (
                "needs_data" if key == "unreviewed" else "computed_diagnostic"
            )
            if key == "unreviewed":
                checks["default_no_native"] = not r["native_called"] and r["backend"] is None
            else:
                checks[key + ":energy"] = r["audits"]["within_energy_budget"]
                checks[key + ":qualification"] = (
                    not r["surge_dynamics_executed"] and not r["physical_validation_claim"]
                )
                for index, row in enumerate(r["values"]["stages"]):
                    p = row["map_point"]
                    speed, flow = p["speed_coordinate_rad_s"], p["flow_coordinate_kg_s"]
                    checks[f"{key}:affine_map:{index}"] = math.isclose(
                        p["pressure_ratio"], 1.2 + 0.0005 * speed - 2 * flow, rel_tol=1e-13
                    )
                frame = to_signal_frame(r, c)
                checks[key + ":signal_clock_origin"] = (
                    frame.clock_id == q.clock_id
                    and frame.origin == "synthetic"
                    and frame.time_s == q.time_s + q.clock_offset_s
                )
        q, c = make(reviewed=True)
        baseline = evaluate_compressor_train(q, c)
        for key, changes in (
            ("unknown_flow", {"mass_flow_kg_s": None}),
            ("outside_map", {"mass_flow_kg_s": 0.2}),
            ("bad_route", {"target_port_id": "different"}),
        ):
            altered = CompressorTrainRequest.model_validate(
                q.model_copy(update=changes).model_dump(mode="python")
            )
            ctx = c.model_copy(update={"metadata": {"compressor_train_boundary": boundary_metadata(altered)}})
            r = evaluate_compressor_train(altered, ctx)
            records[key] = r
            checks[key + ":status"] = r["status"] == (
                "needs_data" if key == "unknown_flow" else "out_of_scope"
            )
            checks[key + ":no_owner"] = not r["owner_called"]
            checks[key + ":replay"] = verify_compressor_train_record(r, request=altered, context=ctx)["valid"]
        for what in ("power", "source", "status", "qualification", "context"):
            r = deepcopy(baseline)
            if what == "power":
                r["values"]["gas_power_W"] *= 2
            elif what == "source":
                r["backend"]["owner_python_tree_sha256"] = "0" * 64
            elif what == "status":
                r["status"] = "computed"
            elif what == "qualification":
                r["surge_dynamics_executed"] = True
            else:
                r["context"]["scenario_id"] = "changed"
            r["result_digest"] = digest({k: v for k, v in r.items() if k != "result_digest"})
            checks["rehashed_refusal:" + what] = not verify_compressor_train_record(r, request=q, context=c)[
                "valid"
            ]
        save(
            "compressor_train",
            records,
            checks,
            (
                "src/pipeline_integrity/compressor_train.py",
                "src/pipeline_integrity/thermo.py",
                "tests/test_compressor_train.py",
                "tests/test_property_reference.py",
                "examples/compressor_train.py",
                "scripts/check_thermal_equipment.py",
                "docs/COMPRESSOR_TRAIN_KO.md",
            ),
        )

    if args.only in ("phase", "both"):
        from pipeline_integrity.phase_change_exchange import (
            evaluate_phase_change,
            to_flow_boundaries,
            to_signal_frames,
            verify_phase_change_record,
        )

        example = runpy.run_path(str(ROOT / "examples/phase_change_exchange.py"))
        make = example["make_case"]
        records, checks = {}, {}
        for kind in example["KINDS"]:
            q, c = make(kind, reviewed=True)
            r = evaluate_phase_change(q, c)
            records[kind] = r
            checks[kind + ":replay"] = verify_phase_change_record(r, request=q, context=c)["valid"]
            unsupported = kind in ("bulk_freezing", "ambient_frost")
            checks[kind + ":status"] = r["status"] == (
                "out_of_scope" if unsupported else "computed_diagnostic"
            )
            checks[kind + ":qualification"] = (
                not r["physical_validation_claim"] and not r["broad_phase_change_complete"]
            )
            if unsupported:
                checks[kind + ":no_native"] = not r["native_called"] and r["values"] is None
            else:
                checks[kind + ":signal_origin"] = to_signal_frames(r, c)[0].origin == "synthetic"
                if kind.startswith(("steady", "dynamic")):
                    inlet, outlet = to_flow_boundaries(r, c)
                    checks[kind + ":port_basis"] = (
                        inlet.property_basis == outlet.property_basis
                        and inlet.direction == "into_component"
                        and outlet.direction == "out_of_component"
                    )
                if kind.startswith("dynamic"):
                    checks[kind + ":closed_energy"] = (
                        abs(r["audits"]["closed_adiabatic_total_energy_residual_J"]) < 1e-7
                    )
                    checks[kind + ":no_endpoint_integral"] = r["values"]["cumulative_external_heat_J"] is None
        q, c = make()
        r = evaluate_phase_change(q, c)
        records["unreviewed"] = r
        checks["default_no_native"] = (
            r["status"] == "needs_data" and not r["native_called"] and verify_phase_change_record(r)["valid"]
        )
        q, c = make("steady_vaporizer", reviewed=True)
        baseline = records["steady_vaporizer"]
        for what in ("heat", "source", "status", "qualification", "context"):
            r = deepcopy(baseline)
            if what == "heat":
                r["values"]["heat_into_fluid_W"] *= 2
            elif what == "source":
                r["implementation_sha256"] = "0" * 64
            elif what == "status":
                r["status"] = "computed"
            elif what == "qualification":
                r["physical_validation_claim"] = True
            else:
                r["context"]["scenario_id"] = "changed"
            r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
            checks["rehashed_refusal:" + what] = not verify_phase_change_record(r, request=q, context=c)[
                "valid"
            ]
        save(
            "phase_change",
            records,
            checks,
            (
                "src/pipeline_integrity/phase_change_exchange.py",
                "src/pipeline_integrity/thermo.py",
                "tests/test_phase_change_exchange.py",
                "tests/test_property_reference.py",
                "examples/phase_change_exchange.py",
                "scripts/check_thermal_equipment.py",
                "docs/PHASE_CHANGE_KO.md",
            ),
        )


if __name__ == "__main__":
    main()
