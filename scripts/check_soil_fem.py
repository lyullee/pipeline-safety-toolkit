"""Actual native P1 references, prior counterexamples and refinement records."""

import json
import math
import re
import runpy
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import file_digest  # noqa: E402
from pipeline_integrity.soil_fem import (  # noqa: E402
    SoilFEMBoundary,
    _geometry,
    _setup,
    evaluate_soil_fem,
    to_signal_frames,
    verify_soil_fem_record,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    make = runpy.run_path(str(ROOT / "examples/soil_fem.py"))["make_case"]
    folder = ROOT / "artifacts/soil_fem"
    records, checks, kinds = {}, {}, {}

    def check(name, condition, kind="contract_or_algebra"):
        assert condition, name
        checks[name], kinds[name] = True, kind

    def evaluate(name, q, c):
        result = evaluate_soil_fem(q, c)
        assert verify_soil_fem_record(result, q, c)["valid"], name
        records[name] = result
        save(folder / (name + ".json"), result)
        print(json.dumps({"case": name, "status": result["status"]}), flush=True)
        return result

    for shear in (0.0, 0.3, 0.75):
        q, c = make(shear=shear, reviewed=True)
        result = evaluate(f"linear_Delaunay_{shear}", q, c)
        row = result["native"]["rows"][0]
        check(f"linear_{shear}_computed", result["status"] == "computed")
        check(
            f"linear_{shear}_physical_ground24W",
            abs(row["boundary_physical_gradient_power_into_soil_W"]["ground"] - 24.0) < 1e-9,
            "independent_analytic",
        )
        check(
            f"linear_{shear}_nodal_field",
            max(
                abs(v - 300 - 2 * p[1])
                for v, p in zip(row["node_temperature_K"], q.mesh.vertices_m, strict=True)
            )
            < 1e-10,
            "independent_analytic",
        )
        ground = next(b for b in q.boundaries if b.id == "ground")
        check(
            f"linear_{shear}_native_ground_node_sum",
            abs(sum(row["dirichlet_node_reaction_W"][str(v)] for v in ground.node_ids) - 24) < 1e-9,
            "analytic_case_corner_cancellation_only",
        )
        check(
            f"linear_{shear}_unknown_ground_partition",
            row["boundary_weak_power_into_soil_W"]["ground"] is None,
        )
        check(
            f"linear_{shear}_area22",
            abs(sum(result["native"]["geometry"]["triangle_areas_m2"]) - 22) < 1e-10,
            "independent_geometry",
        )
        check(
            f"linear_{shear}_node_identical",
            result["native"]["geometry"]["vertices_m"] == [list(p) for p in q.mesh.vertices_m],
        )

    legacy = []
    for shear in (0.0, 0.3, 0.75):
        q, c = make(shear=shear, reviewed=True, triangulation="convex_fan")
        result = evaluate(f"legacy_linear_fan_{shear}", q, c)
        legacy.append(result)
        for h in (0.0, 1.0):
            q, c = make(
                mode="robin",
                shear=shear,
                reviewed=True,
                triangulation="convex_fan",
                mass_scheme="consistent",
                robin_quadrature="consistent",
            )
            q = q.model_copy(
                update={
                    "boundaries": tuple(
                        b.model_copy(
                            update={
                                "convection_W_m2_K": tuple(
                                    tuple(h for _ in row) for row in b.convection_W_m2_K
                                )
                            }
                        )
                        for b in q.boundaries
                    )
                }
            )
            result = evaluate(f"legacy_consistent_h{h}_{shear}", q, c)
            legacy.append(result)
            check(
                f"legacy_consistent_h{h}_{shear}_withheld_export",
                result["status"] == "diagnostic_rejected",
                "retained_counterexample",
            )
            if h:
                check(
                    f"legacy_consistent_{shear}_overshoot_preserved",
                    max(max(row["node_temperature_K"]) for row in result["native"]["rows"]) > 310.1,
                    "retained_counterexample",
                )
        q, c = make(
            mode="robin",
            shear=shear,
            reviewed=True,
            triangulation="convex_fan",
            robin_quadrature="consistent",
        )
        legacy.append(evaluate(f"legacy_lumped_fan_{shear}", q, c))
    check("twelve_original_small_probe_cases_replayed", len(legacy) == 12)

    corrected = []
    for refinement in (1, 2, 4):
        for shear in (0.0, 0.3, 0.75):
            q, c = make(mode="robin", shear=shear, refinement=refinement, reviewed=True)
            result = evaluate(f"qualified_robin_r{refinement}_s{shear}", q, c)
            corrected.append(result)
            rows = result["native"]["rows"]
            check(
                f"Robin_r{refinement}_s{shear}_computed",
                result["status"] == "computed",
                "operator_and_physical_bound",
            )
            check(
                f"Robin_r{refinement}_s{shear}_bounds",
                all(
                    min(row["node_temperature_K"]) >= 300 - 1e-9
                    and max(row["node_temperature_K"]) <= 310 + 1e-9
                    for row in rows
                ),
                "operator_and_physical_bound",
            )
            check(
                f"Robin_r{refinement}_s{shear}_energy",
                max(abs(row["step_energy_residual_J"] or 0.0) for row in rows) < 1e-8,
                "native_weak_energy",
            )
    for refinement in (2, 4):
        for shear in (0.3, 0.75):
            q, c = make(
                mode="robin",
                shear=shear,
                refinement=refinement,
                reviewed=True,
                triangulation="convex_fan",
                robin_quadrature="consistent",
            )
            result = evaluate(f"retained_obtuse_fan_r{refinement}_s{shear}", q, c)
            check(
                f"fan_r{refinement}_s{shear}_unqualified",
                result["status"] == "diagnostic_rejected",
                "retained_counterexample",
            )
            check(
                f"fan_r{refinement}_s{shear}_overshoot",
                max(max(row["node_temperature_K"]) for row in result["native"]["rows"]) > 310.001,
                "retained_counterexample",
            )

    for sign in (-1.0, 1.0):
        q, c = make(mode="flux", shear=0.75, reviewed=True)
        q = q.model_copy(
            update={
                "boundaries": tuple(
                    b.model_copy(
                        update={
                            "heat_flux_into_soil_W_m2": tuple(
                                tuple(sign * v for v in row) for row in b.heat_flux_into_soil_W_m2
                            )
                        }
                    )
                    if b.kind == "heat_flux"
                    else b
                    for b in q.boundaries
                )
            }
        )
        result = evaluate(f"source_aware_flux_{sign}", q, c)
        expected = sign * 10 * (2 + 2 * math.sqrt(1 + 0.75**2)) * 2 * 0.2
        check(f"flux_{sign}_computed", result["status"] == "computed", "source_aware_bound")
        check(
            f"flux_{sign}_storage",
            abs(result["native"]["rows"][-1]["stored_energy_change_J"] - expected) < 1e-8,
            "independent_analytic",
        )

    for mixed in (False, True):
        q, c = make(mode="robin" if mixed else "uniform", shear=0.75, reviewed=True)
        if mixed:
            boundaries = []
            for b in q.boundaries:
                if b.id == "ground":
                    nodes = tuple(
                        sorted(
                            {v for f in q.mesh.boundary_face_groups[b.id] for v in q.mesh.face_vertex_ids[f]}
                        )
                    )
                    b = SoilFEMBoundary(
                        id=b.id,
                        role=b.role,
                        kind="temperature",
                        node_ids=nodes,
                        nodal_temperature_K=tuple(tuple(300.0 for _ in nodes) for _ in q.time_s),
                        evidence_id=b.evidence_id,
                    )
                else:
                    b = b.model_copy(
                        update={
                            "ambient_temperature_K": tuple(
                                tuple(330.0 for _ in row) for row in b.ambient_temperature_K
                            )
                        }
                    )
                boundaries.append(b)
            q = q.model_copy(
                update={
                    "initial_node_temperature_K": tuple(300.0 for _ in q.initial_node_temperature_K),
                    "boundaries": tuple(boundaries),
                }
            )
        else:
            q = q.model_copy(
                update={
                    "boundaries": tuple(
                        b.model_copy(
                            update={
                                "nodal_temperature_K": tuple(
                                    tuple(t for _ in b.node_ids) for t in (300.0, 302.0, 301.0)
                                )
                            }
                        )
                        for b in q.boundaries
                    )
                }
            )
        label = "mixed_Dirichlet_Robin" if mixed else "all_Dirichlet_time_history"
        result = evaluate(label, q, c)
        rows = result["native"]["rows"]
        check(label + "_computed", result["status"] == "computed", "source_aware_bound")
        check(
            label + "_union_reaction_known",
            all(row["total_dirichlet_weak_power_W"] is not None for row in rows[1:]),
            "native_weak_energy",
        )
        check(
            label + "_quadrature_storage",
            all(
                abs(row["stored_energy_change_J"] - row["independent_P1_quadrature_energy_change_J"]) < 1e-8
                for row in rows
            ),
            "independent_quadrature_accounting",
        )
        check(
            label + "_boundary_union",
            all(
                abs(
                    row["total_boundary_weak_power_W"]
                    - row["total_dirichlet_weak_power_W"]
                    - sum(
                        row["boundary_weak_power_into_soil_W"][b.id]
                        for b in q.boundaries
                        if b.kind != "temperature"
                    )
                )
                < 1e-8
                for row in rows[1:]
            ),
            "native_weak_energy",
        )

    # This continuum field vanishes on every integer-coordinate cavity edge.
    # Its scalar decay is independent of the native finite-element operator.
    import numpy as np
    import skfem
    from scipy.linalg import eigh

    errors = []
    for refinement in (2, 4, 8):
        q, c = make(mode="uniform", refinement=refinement, reviewed=True, maximum_step_s=0.01)
        boundary_nodes = {node for b in q.boundaries for node in b.node_ids}
        initial = tuple(
            300.0 if i in boundary_nodes else 300 + 2 * math.sin(math.pi * x) * math.sin(math.pi * y)
            for i, (x, y) in enumerate(q.mesh.vertices_m)
        )
        q = q.model_copy(update={"initial_node_temperature_K": initial})
        result = evaluate(f"continuum_eigen_space_r{refinement}", q, c)
        native = result["native"]
        mesh = skfem.MeshTri(
            np.ascontiguousarray(np.asarray(native["geometry"]["vertices_m"]).T),
            np.ascontiguousarray(np.asarray(native["geometry"]["triangles"]).T),
        )
        basis = skfem.Basis(mesh, skfem.ElementTriP1(), intorder=4)
        error_form = skfem.Functional(
            lambda w: (
                (
                    w["uh"]
                    - (
                        300
                        + 2
                        * math.exp(-2 * math.pi**2 / 10 * 0.2)
                        * np.sin(math.pi * w.x[0])
                        * np.sin(math.pi * w.x[1])
                    )
                )
                ** 2
            )
        )
        error = math.sqrt(
            error_form.assemble(
                basis, uh=basis.interpolate(np.asarray(native["rows"][-1]["node_temperature_K"]))
            )
        )
        errors.append(error)
        check(
            f"continuum_space_r{refinement}_computed", result["status"] == "computed", "continuum_refinement"
        )
    check("continuum_space_L2_error_decreases", errors[2] < errors[1] < errors[0], "continuum_refinement")

    q, c = make(mode="uniform", refinement=2, reviewed=True)
    np, _, _, basis, K, M, _, _, _, _ = _setup(q, _geometry(q))
    boundary_nodes = {node for b in q.boundaries for node in b.node_ids}
    free = np.asarray([i for i in range(len(q.mesh.vertices_m)) if i not in boundary_nodes])
    eigenvalues, eigenvectors = eigh(K[free][:, free].toarray(), M[free][:, free].toarray())
    lam = float(eigenvalues[3])
    phi = np.zeros(basis.N)
    phi[free] = eigenvectors[:, 3] / np.max(np.abs(eigenvectors[:, 3]))
    initial = tuple((300 + phi).tolist())
    time_errors = []
    for maximum_step in (0.05, 0.025, 0.0125):
        request = q.model_copy(update={"initial_node_temperature_K": initial, "maximum_step_s": maximum_step})
        result = evaluate(f"discrete_eigen_time_{maximum_step}", request, c)
        rows = result["native"]["rows"]
        amplitude = 1.0
        for first, second in zip(rows, rows[1:], strict=False):
            amplitude /= 1 + lam * (second["time_s"] - first["time_s"])
        actual = np.asarray(rows[-1]["node_temperature_K"]) - 300
        check(
            f"BE_discrete_eigen_{maximum_step}",
            float(np.max(np.abs(actual - phi * amplitude))) < 1e-9,
            "native_discrete_eigen_scalar_reference",
        )
        time_errors.append(float(np.max(np.abs(actual - phi * math.exp(-lam * 0.2)))))
    check(
        "discrete_eigen_time_error_decreases",
        time_errors[2] < time_errors[1] < time_errors[0],
        "time_refinement",
    )

    base = records["qualified_robin_r1_s0.75"]
    rejected = []
    for mode in (
        "node",
        "mean",
        "weak",
        "gradient",
        "energy",
        "matrix",
        "backend",
        "allocation",
        "geometry",
        "source",
        "clock",
        "qualification",
        "promotion",
        "quadrature_storage",
    ):
        changed = deepcopy(base)
        native, row = changed["native"], changed["native"]["rows"][-1]
        if mode == "node":
            row["node_temperature_K"][8] += 1
        elif mode == "mean":
            row["cell_mean_from_P1_K"][0] += 1
        elif mode == "weak":
            row["boundary_weak_power_into_soil_W"]["ground"] += 1
        elif mode == "gradient":
            row["boundary_physical_gradient_power_into_soil_W"]["ground"] += 1
        elif mode == "energy":
            row["cumulative_total_boundary_energy_J"] += 1
        elif mode == "matrix":
            native["capacity_digest"] = "0" * 64
        elif mode == "backend":
            native["backend"]["source_tree_sha256"] = "0" * 64
        elif mode == "allocation":
            native["dirichlet_group_reaction_allocatable"]["ground"] = True
        elif mode == "geometry":
            native["geometry"]["cavity_count"] = 3
        elif mode == "source":
            changed["producer_sha256"] = "0" * 64
        elif mode == "clock":
            row["time_s"] += 1
        elif mode == "qualification":
            changed["qualification"] = "field-qualified"
        elif mode == "quadrature_storage":
            row["independent_P1_quadrature_energy_change_J"] += 1
        else:
            changed = deepcopy(records["legacy_consistent_h1.0_0.75"])
            changed["status"] = "computed"
        changed["native_digest"] = digest(changed["native"])
        changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
        if verify_soil_fem_record(changed)["valid"]:
            raise AssertionError("FEM rehashed corruption accepted: " + mode)
        rejected.append(mode)
    check("rehashed_corruptions_refused", len(rejected) == 14)
    q, c = make()
    result = evaluate("unreviewed_no_native", q, c)
    check("default_is_withheld", result["native"] is None)
    q, c = make(mode="robin", reviewed=True)
    frames = to_signal_frames(records["qualified_robin_r1_s0.0"], c)
    check("first_interval_rate_unknown", frames[0].values["boundary.ground.backward_euler_weak_W"] is None)
    check(
        "clock_offset_and_SI",
        frames[-1].time_s == 20.2 and frames[-1].units["node.0.P1_temperature_K"] == "K",
    )

    doc = ROOT / "docs/SOIL_FEM_KO.md"
    blocks = (
        re.findall(r"```python\n(.*?)```", doc.read_text(encoding="utf-8"), flags=re.S)
        if doc.exists()
        else []
    )
    for block in blocks:
        exec(compile(block, "SOIL_FEM_KO.md", "exec"), {})
    own = (
        "src/pipeline_integrity/soil_fem.py",
        "tests/test_soil_fem.py",
        "examples/soil_fem.py",
        "scripts/check_soil_fem.py",
        "docs/SOIL_FEM_KO.md",
    )
    counts = {kind: sum(k == kind for k in kinds.values()) for kind in sorted(set(kinds.values()))}
    summaries = {}
    for name, result in records.items():
        native = result["native"]
        summaries[name] = {"status": result["status"], "native_called": result["native_called"]}
        if native:
            rows = native["rows"]
            summaries[name].update(
                rows=len(rows),
                nodes=len(rows[0]["node_temperature_K"]) if rows else 0,
                triangles=len(native["geometry"]["triangles"]),
                maximum_temperature_K=max(max(r["node_temperature_K"]) for r in rows),
                maximum_positive_operator_offdiagonal_W_K=max(
                    r["maximum_positive_operator_offdiagonal_W_K"] for r in rows
                ),
                maximum_absolute_step_energy_residual_J=max(
                    abs(r["step_energy_residual_J"] or 0) for r in rows
                ),
                all_operator_checks_passed=all(r["operator_comparison_passed"] for r in rows),
                all_temperature_bounds_passed=all(r["temperature_bound_passed"] for r in rows),
            )
    summary = {
        "schema": "pipeline_integrity.soil_fem_verification.v1",
        "qualification": "bounded_constant_soil_P1_software_references; no curved/wall/fluid/moisture/field qualification",
        "native_cases": sum(r["native"] is not None for r in records.values()),
        "withheld_cases": sum(r["native"] is None for r in records.values()),
        "reference_counts_by_kind": counts,
        "reference_total": len(checks),
        "reference_passed": sum(checks.values()),
        "rehashed_corruptions_rejected": len(rejected),
        "documentation_python_blocks_executed": len(blocks),
        "continuum_space_L2_error_K_m": errors,
        "discrete_time_maximum_error_K": time_errors,
        "native_discrete_eigenvalue_per_s": lam,
        "backend": base["native"]["backend"],
        "cases": summaries,
        "code_sha256": {p: file_digest(ROOT / p) for p in own if (ROOT / p).exists()},
        "notes": [
            "Twelve prior cases and refined obtuse failures are retained; failed operators are not exported.",
            "Raw nodal fields, native matrices and detailed receipts stay in excluded artifacts.",
            "No measured soil or field performance was used to qualify this adapter.",
        ],
    }
    save(
        folder / "reference_checks.json",
        {name: {"passed": value, "kind": kinds[name]} for name, value in checks.items()},
    )
    save(folder / "corruptions_rejected.json", rejected)
    save(ROOT / "research/soil_fem_results.json", summary)
    print(
        json.dumps(
            {
                "native_cases": summary["native_cases"],
                "checks": len(checks),
                "kinds": counts,
                "spatial_errors": errors,
                "time_errors": time_errors,
                "corruptions": len(rejected),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
