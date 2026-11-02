"""Run a declared portable test subset without private observations or owner engines."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_public_release.py",
    "test_contract_serialization.py",
    "test_units.py",
    "test_nominal_pipe_pressure_independent_local.py",
    "test_growing_pit_numeric_extremes.py",
    "test_richards_fv_independent.py",
    "test_gas_acoustic_pipe_independent.py",
    "test_acoustic_cavity_modal_boundaries_independent.py",
    "test_liquid_moc_physical_qualification.py",
    "test_moc_simultaneous_wave_superposition_hangang.py",
    "test_elastic_fracture_energy_independent.py",
    "test_circular_plate_individual.py",
    "test_electrical_circuit_individual.py",
    "test_laminar_rheology_individual_methods.py",
    "test_laminar_radial_profile_individual.py",
    "test_porous_release_independent.py",
    "test_hydraulics_liquid_head_loss_physical_qualification.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--installed-site", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve()
    folder = (
        args.output_dir
        or ROOT / "artifacts/public-checks" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if any(not (project / "tests" / name).is_file() for name in TESTS):
        raise FileNotFoundError("A portable test file is missing")
    env = os.environ.copy()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUTF8="1")
    if args.installed_site:
        env["PYTHONPATH"] = str(args.installed_site.resolve())
    else:
        env["PYTHONPATH"] = str(project / "src")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    before = {name: sha(project / "tests" / name) for name in TESTS}
    package_root = (
        args.installed_site / "pipeline_integrity"
        if args.installed_site
        else project / "src/pipeline_integrity"
    ).resolve()

    def source_snapshot():
        return {
            p.relative_to(package_root).as_posix(): sha(p)
            for p in package_root.rglob("*")
            if p.is_file() and (p.suffix in (".py", ".json") or p.name == "py.typed")
        }

    source_before = source_snapshot()
    probe = subprocess.run(
        [sys.executable, "-c", "import pipeline_integrity; print(pipeline_integrity.__file__)"],
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    imported_package = Path(probe.stdout.strip()).resolve()
    if not imported_package.is_relative_to(package_root):
        raise RuntimeError("Public checks imported a different package tree")
    started = time.monotonic()

    def execute(name):
        xml, log = folder / (name + ".xml"), folder / (name + ".log")
        command = [
            sys.executable,
            "-m",
            "pytest",
            str(project / "tests" / name),
            "-q",
            "--tb=short",
            "-o",
            "junit_family=xunit1",
            "--junitxml=" + str(xml),
        ]
        if args.installed_site:
            command += ["-c", str(project / "pytest.ini"), "--rootdir=" + str(project)]
        with log.open("w", encoding="utf-8") as out:
            result = subprocess.run(command, cwd=project, env=env, stdout=out, stderr=subprocess.STDOUT)
        rows = []
        if xml.is_file():
            for case in ET.parse(xml).iter("testcase"):
                status = next(
                    (s for s in ("failure", "error", "skipped") if case.find(s) is not None), "passed"
                )
                row = {"id": case.get("classname", "") + "::" + case.get("name", ""), "status": status}
                if status != "passed":
                    row["diagnostic"] = case.find(status).text
                rows.append(row)
        print(json.dumps({"file": name, "exit_code": result.returncode, "cases": len(rows)}), flush=True)
        return {
            "file": name,
            "exit_code": result.returncode,
            "cases": rows,
            "junit_sha256": sha(xml) if xml.is_file() else None,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = [future.result() for future in as_completed([pool.submit(execute, name) for name in TESTS])]
    after = {name: sha(project / "tests" / name) for name in TESTS}
    source_after = source_snapshot()
    source_changed = sorted(
        p for p in source_before.keys() | source_after.keys() if source_before.get(p) != source_after.get(p)
    )
    cases = [c for run in runs for c in run["cases"]]
    report = {
        "schema": "pipeline_integrity.public_checks.v1",
        "test_files": len(TESTS),
        "cases": len(cases),
        **{s: sum(c["status"] == s for c in cases) for s in ("passed", "failure", "error", "skipped")},
        "passed_all": all(run["exit_code"] == 0 and run["cases"] for run in runs)
        and before == after
        and not source_changed
        and all(c["status"] == "passed" for c in cases),
        "installed_wheel_mode": args.installed_site is not None,
        "test_sha256": after,
        "package_source_sha256": source_after,
        "package_import_origin": str(imported_package),
        "source_files_changed_during_run": source_changed,
        "elapsed_wall_s": time.monotonic() - started,
        "runs": runs,
        "scope": "Declared portable public API/numerical subset; not the full owner/data-dependent research inventory or field validation.",
    }
    (folder / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("cases", "passed", "failure", "error", "skipped", "passed_all", "elapsed_wall_s")
            }
        ),
        flush=True,
    )
    return int(not report["passed_all"])


if __name__ == "__main__":
    raise SystemExit(main())
