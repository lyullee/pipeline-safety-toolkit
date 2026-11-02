"""Replay actual FiPy polygon cavities, authored references and receiving contracts."""

from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import math
import runpy
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import file_digest  # noqa: E402
from pipeline_integrity.soil_cross_section import (  # noqa: E402
    _audit,
    _backend,
    evaluate_soil_cross_section,
    to_signal_frames,
    verify_soil_cross_section_record,
)

MAKE_CASE = runpy.run_path(str(ROOT / "examples/soil_cross_section.py"))["make_case"]


def analytic_cell_wave(request, time):
    wave = math.pi / 6
    result = []
    for cell in request.mesh.cell_face_ids:
        xs = [request.mesh.vertices_m[v][0] for f in cell for v in request.mesh.face_vertex_ids[f]]
        a, b = min(xs), max(xs)
        result.append(
            300
            + 10
            * math.exp(-wave * wave * time)
            * (math.sin(wave * b) - math.sin(wave * a))
            / (wave * (b - a))
        )
    return result


def probe_unqualified_shear():
    """Independent direct-library counterexample; never accepted production output."""
    import numpy as np
    from fipy import CellVariable, DiffusionTerm, FaceVariable, TransientTerm
    from fipy.meshes.mesh2D import Mesh2D
    from fipy.solvers.scipy import LinearLUSolver
    from fipy.terms.diffusionTermCorrection import DiffusionTermCorrection

    rows = []
    for shear in (0.0, 0.3, 0.75):
        request, _ = MAKE_CASE(reviewed=True, mode="linear")
        coordinates = np.asarray([(x + shear * y, y) for x, y in request.mesh.vertices_m]).T
        for name, term in (("default", DiffusionTerm), ("correction_attempt", DiffusionTermCorrection)):
            mesh = Mesh2D(
                coordinates,
                np.asarray(request.mesh.face_vertex_ids).T,
                np.asarray(request.mesh.cell_face_ids).T,
            )
            temperature = CellVariable(mesh=mesh, value=request.initial_temperature_K, hasOld=True)
            boundary = FaceVariable(mesh=mesh, value=300.0)
            for item in request.boundaries:
                boundary[list(request.mesh.boundary_face_groups[item.id])] = item.temperature_K[0]
            temperature.constrain(boundary, where=mesh.exteriorFaces)
            equation = TransientTerm(coeff=10.0) == term(coeff=1.0)
            temperature.updateOld()
            row = {"shear": shear, "term": name, "qualified": False, "exact_ground_normal_heat_W": 24.0}
            try:
                for _iteration in range(1, 41):
                    residual = float(
                        equation.sweep(var=temperature, dt=0.05, solver=LinearLUSolver(tolerance=1e-12))
                    )
                    if residual < 1e-8:
                        break
                ids = list(request.mesh.boundary_face_groups["ground"])
                lengths = np.asarray(
                    [
                        math.dist(
                            coordinates[:, request.mesh.face_vertex_ids[f][0]],
                            coordinates[:, request.mesh.face_vertex_ids[f][1]],
                        )
                        for f in ids
                    ]
                )
                normal = np.asarray(mesh.faceNormals)[:, ids]
                raw = np.sum(np.asarray(temperature.faceGrad.value)[:, ids] * normal, axis=0)
                gauss = np.sum(
                    np.asarray(temperature.grad.arithmeticFaceValue.value)[:, ids] * normal, axis=0
                )
                row.update(
                    status="diagnostic_only",
                    sweeps=_iteration,
                    library_residual=residual,
                    raw_face_gradient_ground_W=float(np.sum(raw * lengths) * 2.0),
                    cell_gauss_gradient_ground_W=float(np.sum(gauss * lengths) * 2.0),
                    maximum_temperature_drift_K=float(
                        np.max(np.abs(temperature.value - np.asarray(request.initial_temperature_K)))
                    ),
                )
            except Exception as exc:
                row.update(status="failed_attempt", reason=f"{type(exc).__name__}: {exc}")
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/soil_cross_section")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    before = None
    records = {}
    requests = {}
    contexts = {}
    checks = {}
    numeric = {}
    cases = {}

    def run(name, request, context):
        record = evaluate_soil_cross_section(request, context)
        records[name] = record
        requests[name] = request
        contexts[name] = context
        (args.artifacts / (name + ".json")).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        checks[name + "_receipt"] = verify_soil_cross_section_record(record, request, context)["valid"]
        if record["status"] == "computed":
            checks[name + "_global_energy"] = record["audit"]["energy_budget_passed"]
            checks[name + "_cell_equations"] = record["audit"]["cell_equations_passed"]
            frames = to_signal_frames(record, context)
            checks[name + "_signal_clock"] = all(
                f.clock_id == request.clock_id and f.time_s == t + request.clock_offset_s
                for f, t in zip(frames, record["native"]["trajectory"]["time_s"], strict=True)
            )
        cases[name] = {
            "status": record["status"],
            "native_called": record["native_called"],
            "cells": len(request.initial_temperature_K),
            "cavities": len([b for b in request.boundaries if b.role == "pipe"]),
            "record_digest": record["record_digest"],
            "audit": record.get("audit"),
            "native_reason": record["native"].get("reason") if record.get("native") else None,
        }
        return record

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        before = _backend()[1]
        for mode in (
            "uniform",
            "linear",
            "flux_sign",
            "convection",
            "interaction_A",
            "interaction_B",
            "interaction_both",
        ):
            record = run(mode, *MAKE_CASE(reviewed=True, mode=mode))
            checks[mode + "_computed"] = record["status"] == "computed"
        baseline = records["uniform"]
        numeric["uniform_max_error_K"] = max(
            abs(v - 300) for row in baseline["native"]["trajectory"]["temperature_K"] for v in row
        )
        checks["uniform_invariance"] = numeric["uniform_max_error_K"] < 1e-9
        linear = records["linear"]
        numeric["linear_max_error_K"] = max(
            abs(v - requests["linear"].initial_temperature_K[i])
            for row in linear["native"]["trajectory"]["temperature_K"]
            for i, v in enumerate(row)
        )
        checks["embedded_linear_solution"] = numeric["linear_max_error_K"] < 1e-9
        numeric["linear_ground_W"] = linear["native"]["trajectory"]["boundary_heat_rate_into_soil_W"][
            "ground"
        ][-1]
        checks["linear_ground_sign_and_length"] = math.isclose(numeric["linear_ground_W"], 24, abs_tol=1e-8)
        numeric["positive_flux_storage_J"] = records["flux_sign"]["audit"]["final_stored_energy_change_J"]
        checks["positive_flux_analytic_storage"] = math.isclose(
            numeric["positive_flux_storage_J"], 16, abs_tol=1e-7
        )
        request, context = MAKE_CASE(reviewed=True, mode="flux_sign")
        negative = request.model_copy(
            update={
                "boundaries": tuple(
                    b.model_copy(
                        update={
                            "heat_flux_into_soil_W_m2": tuple(
                                tuple(-10.0 for _ in row) for row in b.heat_flux_into_soil_W_m2
                            )
                        }
                    )
                    if b.id == "pipe_A"
                    else b
                    for b in request.boundaries
                )
            }
        )
        cooling = run("negative_flux", negative, context)
        checks["negative_flux_analytic_storage"] = cooling["status"] == "computed" and math.isclose(
            cooling["audit"]["final_stored_energy_change_J"], -16, abs_tol=1e-7
        )
        short = run("extrusion_one_m", *MAKE_CASE(reviewed=True, mode="flux_sign", length_m=1.0))
        checks["extrusion_temperature_invariance"] = (
            short["native"]["trajectory"]["temperature_K"]
            == records["flux_sign"]["native"]["trajectory"]["temperature_K"]
        )
        checks["extrusion_energy_scaling"] = math.isclose(
            2 * short["audit"]["final_stored_energy_change_J"],
            numeric["positive_flux_storage_J"],
            abs_tol=1e-7,
        )
        last = {
            name: {
                key: values[-1]
                for key, values in records[name]["native"]["trajectory"][
                    "boundary_heat_rate_into_soil_W"
                ].items()
            }
            for name in ("interaction_A", "interaction_B", "interaction_both")
        }
        numeric["two_pipe_final_heat_into_soil_W"] = last
        checks["actual_cross_pipe_heat_transfer"] = last["interaction_A"]["pipe_B"] < -0.1
        checks["two_pipe_reciprocity"] = math.isclose(
            last["interaction_A"]["pipe_B"], last["interaction_B"]["pipe_A"], abs_tol=1e-7
        )
        checks["two_pipe_superposition"] = all(
            math.isclose(
                last["interaction_both"][name],
                last["interaction_A"][name] + last["interaction_B"][name],
                abs_tol=1e-6,
            )
            for name in last["interaction_both"]
        )
        errors = []
        for level in (1, 2, 4):
            request, context = MAKE_CASE(
                reviewed=True, mode="heat_wave", refinement=level, maximum_step_s=0.005
            )
            record = run("wave_space_" + str(level), request, context)
            exact = analytic_cell_wave(request, request.time_s[-1])
            error = max(
                abs(a - b)
                for a, b in zip(record["native"]["trajectory"]["temperature_K"][-1], exact, strict=True)
            )
            errors.append(error)
        numeric["heat_wave_spatial_max_error_K"] = errors
        checks["heat_wave_spatial_refinement"] = errors[1] < 0.4 * errors[0] and errors[2] < 0.4 * errors[1]
        temporal = []
        for dt in (0.05, 0.025, 0.0125, 0.00625):
            record = run(
                "wave_time_" + str(dt),
                *MAKE_CASE(reviewed=True, mode="heat_wave", refinement=2, maximum_step_s=dt),
            )
            temporal.append(record["native"]["trajectory"]["temperature_K"][-1])
        distances = [max(abs(a - b) for a, b in zip(row, temporal[-1], strict=True)) for row in temporal[:-1]]
        numeric["same_spatial_finer_time_reference_distances_K"] = distances
        checks["time_refinement_same_spatial_operator"] = distances[2] < distances[1] < distances[0]
        for policy, expected in (("linear", 2.0), ("previous_knot_hold", 8.0)):
            request, context = MAKE_CASE(reviewed=True, mode="flux_sign")
            boundaries = tuple(
                b.model_copy(
                    update={
                        "heat_flux_into_soil_W_m2": (
                            b.heat_flux_into_soil_W_m2[0],
                            tuple(0.0 for _ in b.heat_flux_into_soil_W_m2[0]),
                            tuple(0.0 for _ in b.heat_flux_into_soil_W_m2[0]),
                        )
                    }
                )
                if b.id == "pipe_A"
                else b
                for b in request.boundaries
            )
            record = run(
                "forcing_" + policy,
                request.model_copy(update={"boundaries": boundaries, "history_interpolation": policy}),
                context,
            )
            checks[policy + "_boundary_quadrature"] = math.isclose(
                record["audit"]["final_stored_energy_change_J"], expected, abs_tol=1e-7
            )
        run("unreviewed", *MAKE_CASE())
        request, context = MAKE_CASE(reviewed=True)
        run("unknown_conductivity", request.model_copy(update={"conductivity_W_m_K": None}), context)
        request, context = MAKE_CASE(reviewed=True, mode="flux_sign")
        high = request.model_copy(
            update={
                "boundaries": tuple(
                    b.model_copy(
                        update={
                            "heat_flux_into_soil_W_m2": tuple(
                                tuple(100.0 for _ in row) for row in b.heat_flux_into_soil_W_m2
                            )
                        }
                    )
                    if b.id == "pipe_A"
                    else b
                    for b in request.boundaries
                )
            }
        )
        withheld = run("surface_support_exceeded", high, context)
        checks["surface_support_withheld"] = (
            withheld["status"] == "out_of_scope" and not withheld["audit"]["temperature_support_passed"]
        )
        try:
            to_signal_frames(withheld, context)
            checks["support_export_refused"] = False
        except ValueError:
            checks["support_export_refused"] = True
        for shear in (0.3, 0.75):
            request, context = MAKE_CASE(reviewed=True, mode="linear")
            mesh = request.mesh.model_copy(
                update={
                    "vertices_m": tuple((x + shear * y, y) for x, y in request.mesh.vertices_m),
                    "maximum_reviewed_nonorthogonality_deg": 85.0,
                }
            )
            unsupported = run(
                "unsupported_shear_" + str(shear), request.model_copy(update={"mesh": mesh}), context
            )
            checks["shear_" + str(shear) + "_not_called"] = (
                unsupported["status"] == "out_of_scope" and unsupported["native_called"] is False
            )
        unsupported_research = probe_unqualified_shear()
        checks["direct_skew_counterexample_preserved"] = any(
            row.get("shear") == 0.75 and abs(row.get("raw_face_gradient_ground_W", 24) - 24) > 4
            for row in unsupported_research
        )
        forged = copy.deepcopy(baseline)
        trajectory = forged["native"]["trajectory"]
        for name, power in (("ground", 100.0), ("far", -100.0)):
            trajectory["boundary_heat_rate_into_soil_W"][name] = [power] * len(trajectory["time_s"])
            trajectory["cumulative_boundary_energy_into_soil_J"][name] = [
                power * t for t in trajectory["time_s"]
            ]
        forged["audit"] = _audit(requests["uniform"], forged["native"])
        forged["native_digest"] = digest(forged["native"])
        forged["record_digest"] = digest({k: v for k, v in forged.items() if k != "record_digest"})
        checks["balanced_false_dirichlet_heat_refused"] = not verify_soil_cross_section_record(
            forged, requests["uniform"], contexts["uniform"]
        )["valid"]
        checks["backend_unchanged"] = _backend()[1] == before
    warning_rows = []
    for warning in captured:
        row = {"category": warning.category.__name__, "message": str(warning.message)}
        if row not in warning_rows:
            warning_rows.append(row)
    files = [
        ROOT / "src/pipeline_integrity/soil_cross_section.py",
        ROOT / "examples/soil_cross_section.py",
        ROOT / "tests/test_soil_cross_section.py",
        Path(__file__),
        ROOT / "docs/SOIL_CROSS_SECTION_KO.md",
    ]
    license_files = {}
    for name in ("FiPy", "typer", "shellingham", "rich", "markdown-it-py", "mdurl"):
        distribution = importlib.metadata.distribution(name)
        license_files[name] = {
            "version": distribution.version,
            "files": {
                str(path): file_digest(distribution.locate_file(path))
                for path in distribution.files
                if "license" in str(path).lower()
                and str(path).endswith(("LICENSE", "LICENSE.txt", "LICENSE.rst"))
            },
        }
    report = {
        "schema": "pipeline_integrity.soil_cross_section_verification.v1",
        "backend_before": before,
        "backend_after": _backend()[1],
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "numerical_references": numeric,
        "unqualified_direct_library_research": unsupported_research,
        "cases": cases,
        "warnings": warning_rows,
        "license_files": license_files,
        "files": {path.relative_to(ROOT).as_posix(): file_digest(path) for path in files},
        "limits": [
            "orthogonal authored polygon cavities and artificial constant soil; no field validation; caller review cannot override the 1e-6 degree support bound",
            "space refinement compares analytic cell-volume averages; time refinement additionally uses a finer same-spatial-operator result, not an independent solver",
            "no pipe-wall, fluid advection, moisture, freezing, phase-change or circular-pipe qualification",
            "FiPy gradients and local implicit-equation residuals are reconstructed for stored records; hashes are unsigned and not proof of historical execution or approval",
        ],
    }
    target = ROOT / "research/soil_cross_section_results.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "cases": {key: row["status"] for key, row in cases.items()},
                "references": numeric,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
