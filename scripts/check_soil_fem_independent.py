"""Read-only independent reference and receipt probes on the frozen FEM producer."""

import copy
import json
import math
import runpy
from pathlib import Path

from pipeline_integrity import soil_fem as fem
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = json.loads((ROOT / "research/soil_fem_results.json").read_text(encoding="utf-8"))["code_sha256"][
    "src/pipeline_integrity/soil_fem.py"
]
MAKE = runpy.run_path(str(ROOT / "examples/soil_fem.py"))["make_case"]


def save(name, value):
    private = ROOT / "artifacts/verification/fem_independent_review"
    private.mkdir(parents=True, exist_ok=True)
    value["review_script_sha256"] = file_digest(Path(__file__))
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    (private / name).write_text(encoded, encoding="utf-8")
    (ROOT / "research/soil_fem_independent_review.json").write_text(encoded, encoding="utf-8")


def reseal(record):
    if record.get("native") is not None:
        record["native_digest"] = digest(record["native"])
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    return record


def refusal(record, request, context):
    checked = fem.verify_soil_fem_record(reseal(record), request, context)
    try:
        fem.to_signal_frames(record, context)
    except ValueError:
        exported = False
    else:
        exported = True
    return {
        "validator_refused": not checked["valid"],
        "export_refused": not exported,
        "reason": checked.get("reason"),
    }


def main():
    assert file_digest(ROOT / "src/pipeline_integrity/soil_fem.py") == EXPECTED
    checks, observations = {}, {}
    original_backend = fem._backend

    def forbidden():
        raise AssertionError("unreviewed default touched the optional numerical backend")

    try:
        fem._backend = forbidden
        q, c = MAKE()
        withheld = fem.evaluate_soil_fem(q, c)
        checks["default_gate_is_lazy"] = (
            withheld["status"] == "needs_data"
            and not withheld["native_called"]
            and fem.verify_soil_fem_record(withheld, q, c)["valid"]
        )
    finally:
        fem._backend = original_backend

    q, c = MAKE(shear=0.625, refinement=2, reviewed=True)
    linear = fem.evaluate_soil_fem(q, c)
    row = linear["native"]["rows"][0]
    field_error = max(
        abs(t - (300 + 2 * p[1])) for t, p in zip(row["node_temperature_K"], q.mesh.vertices_m, strict=True)
    )
    ground_W = row["boundary_physical_gradient_power_into_soil_W"]["ground"]
    checks["independent_new_shear_exact_field"] = linear["status"] == "computed" and field_error < 1e-9
    checks["independent_ground_6m_L2m_k1_grad2_exact24W"] = abs(ground_W - 24.0) < 1e-8
    checks["shared_corner_weak_unknown_retained"] = row["boundary_weak_power_into_soil_W"]["ground"] is None
    observations["linear"] = dict(
        shear=0.625,
        refinement=2,
        maximum_temperature_error_K=field_error,
        physical_ground_W=ground_W,
        expected_W=24.0,
    )

    q, c = MAKE(mode="robin", shear=0.625, refinement=3, reviewed=True)
    robin = fem.evaluate_soil_fem(q, c)
    frames = fem.to_signal_frames(robin, c)
    rows = robin["native"]["rows"]
    minimum = min(min(r["node_temperature_K"]) for r in rows)
    maximum = max(max(r["node_temperature_K"]) for r in rows)
    checks["new_refinement3_passive_bounds"] = (
        robin["status"] == "computed"
        and minimum >= 300 - 1e-8
        and maximum <= 310 + 1e-8
        and all(r["operator_comparison_passed"] and r["temperature_bound_passed"] for r in rows)
    )
    checks["actual_localtime_plus_offset"] = [f.time_s for f in frames] == [r["time_s"] + 20.0 for r in rows]
    last = rows[-1]
    mismatch = max(
        abs(
            last["boundary_physical_gradient_power_into_soil_W"][key]
            - last["boundary_weak_power_into_soil_W"][key]
        )
        for key in last["boundary_weak_power_into_soil_W"]
    )
    checks["physical_gradient_not_weak_reaction"] = mismatch > 0.01
    observations["passive"] = dict(
        shear=0.625,
        refinement=3,
        minimum_K=minimum,
        maximum_K=maximum,
        physical_weak_maximum_difference_W=mismatch,
    )
    mutations = {}
    for name in (
        "wrong_node_count",
        "wrong_node_value",
        "wrong_clock",
        "physical_as_weak",
        "cell_mean_as_point",
    ):
        changed = copy.deepcopy(robin)
        r = changed["native"]["rows"][-1]
        if name == "wrong_node_count":
            r["node_temperature_K"].pop()
        elif name == "wrong_node_value":
            r["node_temperature_K"][0] += 0.1
        elif name == "wrong_clock":
            r["time_s"] += 20
        elif name == "physical_as_weak":
            r["boundary_weak_power_into_soil_W"] = copy.deepcopy(
                r["boundary_physical_gradient_power_into_soil_W"]
            )
        else:
            r["cell_mean_from_P1_K"][0] = r["node_temperature_K"][0]
        result = refusal(changed, q, c)
        mutations[name] = result
        checks[name] = result["validator_refused"] and result["export_refused"]
    wrong_context = c.model_copy(update={"clock_id": "different-physical-clock"})
    try:
        fem.to_signal_frames(robin, wrong_context)
    except ValueError:
        checks["wrong_context_clock_export_refused"] = True
    else:
        checks["wrong_context_clock_export_refused"] = False

    # Construct a paired nodal perturbation with zero global capacity increment.
    # Choose interior vertices not coupled to any Dirichlet node, so the global
    # boundary reaction also stays unchanged. The local residual must still fail.
    q, c = MAKE(mode="uniform", shear=0.625, refinement=3, reviewed=True)
    boundary = {v for b in q.boundaries for v in b.node_ids}
    initial = tuple(
        300.0
        if i in boundary
        else 302.0 + math.sin((x - 0.625 * y) * math.pi / 6) * math.sin(y * math.pi / 4)
        for i, (x, y) in enumerate(q.mesh.vertices_m)
    )
    q = q.model_copy(update={"initial_node_temperature_K": initial})
    baseline = fem.evaluate_soil_fem(q, c)
    assert baseline["status"] == "computed"
    geometry = fem._geometry(q)
    np, _, _, _, K, M, *_ = fem._setup(q, geometry)
    boundary_ids = sorted(boundary)
    far_nodes = [
        i
        for i in range(len(q.mesh.vertices_m))
        if i not in boundary and np.all(np.abs(K[boundary_ids, i].toarray()) < 1e-12)
    ]
    states = [r["node_temperature_K"][:] for r in baseline["native"]["rows"]]
    first, second = far_nodes[len(far_nodes) // 3], far_nodes[2 * len(far_nodes) // 3]
    weights = np.asarray(M.sum(axis=0)).ravel()
    change = 0.001
    states[-1][first] += change
    states[-1][second] -= change * weights[first] / weights[second]
    reconstructed = fem._run(q, geometry, replay=states)
    bad = reconstructed["rows"][-1]
    checks["equal_opposite_local_residual_detected"] = (
        not bad["local_equations_passed"] and bad["maximum_free_equation_residual_W"] > 1e-4
    )
    checks["global_energy_zero_cannot_hide_local_failure"] = (
        bad["energy_budget_passed"]
        and bad["quadrature_energy_consistency_passed"]
        and bad["temperature_bound_passed"]
        and not bad["local_equations_passed"]
    )
    promoted = copy.deepcopy(baseline)
    promoted.update(native=reconstructed, status="computed")
    result = refusal(promoted, q, c)
    checks["fully_reconstructed_local_failure_cannot_promote"] = (
        result["validator_refused"] and result["export_refused"]
    )
    observations["balanced_local_attack"] = dict(
        nodes=[first, second],
        positive_change_K=change,
        opposite_change_K=-change * weights[first] / weights[second],
        global_energy_residual_J=bad["step_energy_residual_J"],
        maximum_local_residual_W=bad["maximum_free_equation_residual_W"],
        temperature_bound_passed=bad["temperature_bound_passed"],
        energy_budget_passed=bad["energy_budget_passed"],
        refusal=result,
    )

    # Uniform 310K has no thermal load. A small paired perturbation must not
    # acquire a loose tolerance merely from the large absolute Kelvin RHS.
    q, c = MAKE(mode="uniform", shear=0.625, refinement=3, reviewed=True)
    q = q.model_copy(
        update={
            "initial_node_temperature_K": tuple(310.0 for _ in q.initial_node_temperature_K),
            "boundaries": tuple(
                b.model_copy(
                    update={"nodal_temperature_K": tuple(tuple(310.0 for _ in b.node_ids) for _ in q.time_s)}
                )
                for b in q.boundaries
            ),
            "temperature_comparison_tolerance_K": 1e-5,
        }
    )
    baseline310 = fem.evaluate_soil_fem(q, c)
    geometry310 = fem._geometry(q)
    np, _, _, _, K, M, *_ = fem._setup(q, geometry310)
    weights = np.asarray(M.sum(axis=0)).ravel()
    states = [r["node_temperature_K"][:] for r in baseline310["native"]["rows"]]
    delta = 2e-6
    states[-1][first] += delta
    states[-1][second] -= delta * weights[first] / weights[second]
    reconstructed = fem._run(q, geometry310, replay=states)
    bad310 = reconstructed["rows"][-1]
    checks["310K_reference_does_not_inflate_local_tolerance"] = (
        bad310["temperature_bound_passed"]
        and bad310["energy_budget_passed"]
        and not bad310["local_equations_passed"]
    )
    promoted = copy.deepcopy(baseline310)
    promoted.update(native=reconstructed, status="computed")
    result310 = refusal(promoted, q, c)
    checks["310K_false_computed_export_refused"] = (
        result310["validator_refused"] and result310["export_refused"]
    )
    observations["310K_attack"] = dict(
        perturbation_K=delta,
        global_energy_residual_J=bad310["step_energy_residual_J"],
        maximum_local_residual_W=bad310["maximum_free_equation_residual_W"],
        temperature_bound_passed=bad310["temperature_bound_passed"],
        energy_budget_passed=bad310["energy_budget_passed"],
        refusal=result310,
    )
    checks["producer_unchanged"] = file_digest(ROOT / "src/pipeline_integrity/soil_fem.py") == EXPECTED
    result = dict(
        producer_sha256=EXPECTED,
        checks=checks,
        passed=sum(checks.values()),
        total=len(checks),
        observations=observations,
        direct_mutation_refusals=mutations,
        scope="bounded independent analytic/contract/adversarial review; no field validation",
    )
    save("results.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if not all(checks.values()):
        raise AssertionError([key for key, value in checks.items() if not value])


if __name__ == "__main__":
    main()
