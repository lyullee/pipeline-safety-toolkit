"""Verify native motion, coupled liquid pressure waves and source-preserving HRA signal exchange."""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.adapters import hra_signal_series
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.transient import simulate_liquid_transient
from pipeline_integrity.valve_motion import motion_frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [
        str((args.lh2dt / "src").resolve()),
        str((args.hra / "src").resolve()),
        str(Path(__file__).resolve().parents[1] / "examples"),
    ]
    from valve_transient import make_case

    parameters = {
        "fast": {},
        "refined": {"timestep": 0.0125},
        "delayed": {"command_time": 0.5},
        "no_command": {"command": False},
        "slow": {"stroke_time": 3.0},
        "closed_leakage": {"closed_leakage_fraction": 0.05},
        "reverse": {"direction": -1.0},
    }
    records = {}
    root = Path("artifacts/valve_transient")
    root.mkdir(parents=True, exist_ok=True)
    for key, options in parameters.items():
        spec, context, motion = make_case(**options)
        result = simulate_liquid_transient(spec, context)
        assert result["status"] == "computed", (key, result["notes"])
        assert result["all_volume_balances_within_tolerance"]
        assert result["maximum_junction_residual_m3_s"] <= spec.valve_flow_tolerance_m3_s
        assert all(audit["root_qualified"] for audit in result["valve_solver"])
        records[key] = {
            "specification": spec.model_dump(mode="json"),
            "motion": motion.model_dump(mode="json"),
            "result": result,
        }
        (root / f"{key}.json").write_text(
            json.dumps(records[key], indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "case": key,
                    "status": result["status"],
                    "max_pipe_volume_residual_m3": result["maximum_pipe_volume_residual_m3"],
                }
            ),
            flush=True,
        )
    fast = records["fast"]["result"]
    fine = records["refined"]["result"]
    velocity = 0.01 / (math.pi * 0.2**2 / 4)
    analytic_jump = 1000.0 * velocity / 9.80665
    jump = max(fast["pipes"]["upstream"]["end_head_m"]) - 100.0
    assert math.isclose(jump, analytic_jump, rel_tol=0, abs_tol=1e-7)
    refinement_error = float(
        np.max(
            np.abs(
                np.asarray(fast["pipes"]["upstream"]["end_head_m"])
                - np.asarray(fine["pipes"]["upstream"]["end_head_m"])[::2]
            )
        )
    )
    assert refinement_error < 1e-6
    offset = round(0.3 / fast["timestep_s"])
    delayed = records["delayed"]["result"]
    delay_error = float(
        np.max(
            np.abs(
                np.asarray(delayed["pipes"]["upstream"]["end_head_m"])[offset:]
                - np.asarray(fast["pipes"]["upstream"]["end_head_m"])[:-offset]
            )
        )
    )
    assert delay_error < 1e-6
    no_command = records["no_command"]["result"]
    assert np.max(np.abs(np.asarray(no_command["valves"]["V-1"]["volume_flow_m3_s"]) - 0.01)) < 1e-10
    completion = records["fast"]["motion"]["command_records"][0]["completion_bracket_s"]
    completed = next(i for i, t in enumerate(fast["time_s"]) if t >= completion[1])
    valve_q = fast["valves"]["V-1"]["volume_flow_m3_s"][completed]
    downstream_q = fast["pipes"]["downstream"]["end_flow_m3_s"][completed]
    assert abs(valve_q) < 1e-9 and downstream_q > 0.005
    times = np.asarray(fast["time_s"])
    selected = (times >= completion[1]) & (times <= completion[1] + 0.5)
    post_target_volume = float(
        np.trapezoid(np.asarray(fast["pipes"]["downstream"]["end_flow_m3_s"])[selected], x=times[selected])
    )
    assert post_target_volume > 0.004
    leaky = records["closed_leakage"]
    leak_completion = leaky["motion"]["command_records"][0]["completion_bracket_s"]
    leak_index = next(i for i, t in enumerate(leaky["result"]["time_s"]) if t >= leak_completion[1])
    leaking_flow = leaky["result"]["valves"]["V-1"]["volume_flow_m3_s"][leak_index]
    assert leaking_flow > 0.0001
    reverse = records["reverse"]["result"]
    assert min(reverse["valves"]["V-1"]["volume_flow_m3_s"]) < -0.005
    assert min(reverse["valves"]["V-1"]["dissipated_power_w"]) > -1e-8
    # Recreate the receipt once for the existing adapter, without changing its
    # simulated output origin to the input command's observation origin.
    from pipeline_integrity.valve_motion import ValveMotionResult

    motion = ValveMotionResult.model_validate_json(json.dumps(records["fast"]["motion"]))
    frames = tuple(motion_frames(motion))
    native_signal = hra_signal_series(frames, "opening")
    assert native_signal.origin == "simulation" and native_signal.unit == "1"
    assert native_signal.clock_id == "elapsed" and len(native_signal.values) == len(motion.time_s)
    (root / "hra_signal.json").write_text(native_signal.model_dump_json(indent=2) + "\n", encoding="utf-8")
    job = {"specification": records["fast"]["specification"], "context": fast["context"]}
    Path("examples/valve_transient.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    cli_file = root / "cli_result.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline_integrity.cli",
            "liquid-transient",
            "examples/valve_transient.json",
            "--output",
            str(cli_file),
        ],
        check=True,
    )
    cli_result = json.loads(cli_file.read_text(encoding="utf-8"))
    assert (
        cli_result["status"] == "computed"
        and cli_result["specification_digest"] == fast["specification_digest"]
    )
    assert cli_result["pipes"]["upstream"]["end_head_m"] == fast["pipes"]["upstream"]["end_head_m"]
    summary = {
        "schema": "pipeline_integrity.valve_transient_checks.v1",
        "verification_script_sha256": file_digest(Path(__file__)),
        "json_cli_status": cli_result["status"],
        "sources": {"lh2dt_models": revision(args.lh2dt), "HRA": revision(args.hra)},
        "motion_provider": motion.provider,
        "origin": "synthetic",
        "cases": {
            key: {
                "status": record["result"]["status"],
                "maximum_pipe_volume_residual_m3": record["result"]["maximum_pipe_volume_residual_m3"],
                "maximum_junction_residual_m3_s": record["result"]["maximum_junction_residual_m3_s"],
                "maximum_upstream_head_m": max(record["result"]["pipes"]["upstream"]["end_head_m"]),
                "maximum_valve_head_residual_m": max(
                    a["maximum_head_residual_m"] for a in record["result"]["valve_solver"]
                ),
                "maximum_valve_solver_evaluations": max(
                    a["function_evaluations"] for a in record["result"]["valve_solver"]
                ),
            }
            for key, record in records.items()
        },
        "analytic_head_jump_m": analytic_jump,
        "computed_head_jump_m": jump,
        "refinement_maximum_head_difference_m": refinement_error,
        "refinement_tolerance_m": 1e-6,
        "command_delay_translation_s": 0.3,
        "delay_translation_maximum_error_m": delay_error,
        "target_completion_bracket_s": completion,
        "valve_flow_at_target_m3_s": valve_q,
        "downstream_flow_at_target_m3_s": downstream_q,
        "post_target_window_s": 0.5,
        "post_target_downstream_net_volume_m3": post_target_volume,
        "valve_closed_leakage_flow_at_target_m3_s": leaking_flow,
        "hra_signal_exchange": {
            "samples": len(native_signal.values),
            "unit": native_signal.unit,
            "origin": native_signal.origin,
            "clock_id": native_signal.clock_id,
            "motion_result_digest": motion.result_digest,
            "is_human_error_probability": False,
        },
        "implementation_sha256": {
            name: file_digest(Path("src/pipeline_integrity") / name)
            for name in ("transient.py", "valve_boundary.py", "valve_motion.py")
        },
        "local_record_sha256": {key: file_digest(root / f"{key}.json") for key in records},
        "finite_stroke_state_is_not_isolation_or_hra_validation": True,
        "independent_field_validation": False,
        "gas_or_multiphase_isolation_validated": False,
        "external_sources_modified": False,
    }
    Path("research/valve_transient_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"sources", "cases", "implementation_sha256", "local_record_sha256"}
            }
        )
    )


if __name__ == "__main__":
    main()
