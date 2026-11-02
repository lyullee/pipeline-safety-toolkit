"""Reproduce feature timing, actual MOC coupling and rehashed-cause refusals."""

import argparse
import json
import runpy
from collections import Counter
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.pressure_wave import (
    boundary_metadata,
    evaluate_pressure_wave,
    verify_pressure_wave_record,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    argparse.ArgumentParser().parse_args()
    factory = runpy.run_path(str(ROOT / "examples/pressure_wave.py"))
    directory = ROOT / "artifacts/pressure_wave"
    directory.mkdir(exist_ok=True, parents=True)
    records, checks = {}, {}

    def run(name, q, c):
        r = evaluate_pressure_wave(q, c)
        records[name] = r
        checks[name + ":replay"] = verify_pressure_wave_record(r, request=q, context=c)
        checks[name + ":no_cause_claim"] = (
            r["leak_detected"] is None
            and r["leak_probability"] is None
            and r["position_m"] is None
            and not r["field_qualified"]
        )
        (directory / (name + ".json")).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
        return r

    q, c = factory["make_case"]()
    r = run("default", q, c)
    checks["default_no_native"] = r["status"] == "needs_data" and not r["native_called"]
    q, c = factory["make_case"](reviewed=True)
    base = run("forward", q, c)
    checks["known_forward_delay_support"] = (
        base["arrival_delay_bounds_s"][0] <= 0.015 <= base["arrival_delay_bounds_s"][1]
        and base["conditional_travel_direction"] == "left_to_right"
    )
    q, c = factory["make_case"](reviewed=True, left_index=55, right_index=40)
    r = run("reverse", q, c)
    checks["known_reverse_support"] = (
        r["arrival_delay_bounds_s"][0] <= -0.015 <= r["arrival_delay_bounds_s"][1]
        and r["conditional_travel_direction"] == "right_to_left"
    )
    q, c = factory["make_case"](reviewed=True, left_index=40, right_index=40)
    r = run("simultaneous", q, c)
    checks["overlapping_support_no_direction"] = (
        r["first_arrival_receiver"] == "indeterminate" and r["conditional_travel_direction"] is None
    )
    q, c = factory["make_case"](reviewed=True)
    q = q.model_copy(update={"timing": None})
    c = c.model_copy(update={"metadata": {"pressure_wave_boundary": boundary_metadata(q)}})
    r = run("unknown_timing", q, c)
    checks["features_not_physical_arrivals"] = (
        r["feature_delay_bounds_s"] is not None
        and r["arrival_delay_bounds_s"] is None
        and r["status"] == "needs_data"
    )
    source_applications = []
    for step in (0.001, 0.002):
        q, c, native = factory["make_moc_case"](timestep_s=step)
        name = "moc_" + str(step)
        r = run(name, q, c)
        (directory / (name + "_native.json")).write_text(json.dumps(native) + "\n", encoding="utf-8")
        lo, hi = r["arrival_delay_bounds_s"]
        reference = -400.0 / 1000.0
        checks[name + ":separate_distance_speed_reference"] = lo <= reference <= hi
        checks[name + ":source_budgets"] = (
            native["status"] == "computed" and native["all_volume_balances_within_tolerance"]
        )
        checks[name + ":right_origin_direction"] = r["conditional_travel_direction"] == "right_to_left"
        source_applications.append(
            {
                "source": "existing_single_phase_MOC",
                "timestep_s": native["timestep_s"],
                "sensors_m": [300.0, 700.0],
                "reference_arrival_difference_s": reference,
                "arrival_support_s": [lo, hi],
                "native_result_digest": digest(native),
                "source_model_sha256": native["model_source_digest"],
                "actual_cause": "declared_withdrawal_increase; not a leak onset",
                "independent_field_validation": False,
            }
        )
    q, c = factory["make_case"](reviewed=True)
    for what in ("delay", "direction", "cause", "native", "source"):
        r = deepcopy(base)
        if what == "delay":
            r["arrival_delay_bounds_s"][0] -= 1
        elif what == "direction":
            r["conditional_travel_direction"] = "right_to_left"
        elif what == "cause":
            r["leak_detected"] = True
        elif what == "native":
            r["features"]["left"]["candidates"][0]["left_sample_index"] = 0
        else:
            r["implementation_sha256"] = "0" * 64
        r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
        try:
            verify_pressure_wave_record(r, request=q, context=c)
        except ValueError:
            checks["rehashed_refusal:" + what] = True
        else:
            checks["rehashed_refusal:" + what] = False
    result = {
        "schema": "pipeline_integrity.pressure_wave_verification.v1",
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "records": len(records),
        "statuses": dict(Counter(r["status"] for r in records.values())),
        "source_applications": source_applications,
        "rehashed_mutations_refused": 5,
        "file_sha256": {
            p: file_digest(ROOT / p)
            for p in (
                "src/pipeline_integrity/pressure_wave.py",
                "src/pipeline_integrity/acoustics.py",
                "src/pipeline_integrity/transient.py",
                "tests/test_pressure_wave.py",
                "examples/pressure_wave.py",
                "scripts/check_pressure_wave.py",
                "docs/PRESSURE_WAVE_KO.md",
            )
        },
        "independent_physical_validation": False,
        "real_pressure_wave_records_applied": 0,
        "cause_classifier_implemented": False,
    }
    (ROOT / "research/pressure_wave_results.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: result[k] for k in ("passed", "total", "records", "statuses")}))
    if not all(checks.values()):
        raise SystemExit("pressure-wave criteria failed")


if __name__ == "__main__":
    main()
