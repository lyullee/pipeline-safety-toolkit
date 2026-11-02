"""Authored diffusion, equilibrium, kinetic balance and finite-slab numerical references."""

import argparse
import json
import math
import runpy
import sys
from copy import deepcopy
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.hydrogen_transport import (
    EQUILIBRIUM,
    KINETIC,
    SEMI,
    evaluate_hydrogen_transport,
    verify_hydrogen_transport_record,
)
from pipeline_integrity.material_exchange import prepare_material_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", required=True, type=Path)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    from pipeline_integrity.adapters import MaterialClient

    client = MaterialClient()
    files = [
        args.materials / "LICENSE",
        args.materials / "THIRD_PARTY_NOTICES.md",
        *sorted((args.materials / "src").rglob("*.json")),
    ]
    support_before = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in files}
    requests, context = runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()
    requests = {
        key: r.model_copy(update={"owner_source_tree_sha256": before["source_tree_sha256"]})
        for key, r in requests.items()
    }
    records, references, refusals = {}, {}, []

    def evaluate(name, request, **kwargs):
        record = evaluate_hydrogen_transport(
            request, context, client=client, owner_source_root=args.materials, **kwargs
        )
        verify_hydrogen_transport_record(record)
        records[name] = record
        return record

    def reference(name, actual, expected, abs_tol=0):
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=abs_tol), (name, actual, expected)
        references[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    def changed(request, **parameters):
        return request.model_copy(update={"parameters": request.parameters | parameters})

    def refused(name, request):
        try:
            evaluate(name, request)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid hydrogen input accepted: " + name)

    for method, request in requests.items():
        assert evaluate(method, request)["status"] == "computed"
    semi, equilibrium, kinetic = [requests[k] for k in (SEMI, EQUILIBRIUM, KINETIC)]
    values = records[SEMI]["values"]
    for i, z in enumerate((0.0, 0.0002, 0.0006, 0.0002)):
        reference(
            f"half_space_depth_{i}", values["concentration_mol_m3"][i], 10 + 100 * math.erfc(z / 0.0004)
        )
    reference("half_space_surface_flux", values["surface_flux_mol_m2_s"], 0.01 / (2 * math.sqrt(math.pi)))
    reference("half_space_integrated_excess", values["integrated_excess_mol_m2"], 0.04 / math.sqrt(math.pi))
    desorption = evaluate(
        "signed_desorption",
        changed(semi, initial_concentration_mol_m3=100.0, surface_concentration_mol_m3=0.0),
    )
    reference(
        "desorption_flux", desorption["values"]["surface_flux_mol_m2_s"], -values["surface_flux_mol_m2_s"]
    )
    assert desorption["values"]["integrated_excess_mol_m2"] < 0
    values = records[EQUILIBRIUM]["values"]
    reference("zero_binding_lattice_concentration", values["lattice_hydrogen_atoms_mol_m3"], 100.0)
    reference("zero_binding_trap_concentration", values["traps"][0]["hydrogen_atoms_mol_m3"], 10.0)
    reference("zero_binding_lattice_occupancy", values["lattice_occupancy"], 0.1)
    reference("zero_binding_retardation", values["differential_retardation_factor"], 1.1)
    reference("zero_binding_local_diffusivity", values["local_differential_diffusivity_m2_s"], 1e-8 / 1.1)
    zero = evaluate("zero_total_hydrogen", changed(equilibrium, total_hydrogen_atoms_mol_m3=0.0))
    reference("zero_hydrogen_lattice", zero["values"]["lattice_hydrogen_atoms_mol_m3"], 0.0)
    assert zero["values"]["lattice_log_odds"] is None
    no_traps = evaluate(
        "no_trap_partition", changed(equilibrium, traps=[], total_hydrogen_atoms_mol_m3=100.0)
    )
    reference("no_trap_diffusivity", no_traps["values"]["local_differential_diffusivity_m2_s"], 1e-8)
    values = records[KINETIC]["values"]
    reference("closed_uniform_lattice_final", values["lattice_mean_hydrogen_atoms_mol_m3"][-1], 100.0)
    reference("closed_uniform_trap_final", values["traps"][0]["mean_hydrogen_atoms_mol_m3"][-1], 10.0)
    reference("closed_uniform_inventory", values["hydrogen_inventory_mol_m2"][-1], 0.11)
    assert values["elapsed_time_s"] == [0.0, 1.0, 10.0] and values["relative_mass_balance_error"] <= 1e-6
    assert max(map(abs, values["left_inward_flux_mol_m2_s"] + values["right_inward_flux_mol_m2_s"])) == 0
    independent = deepcopy(kinetic.parameters)
    trap = independent["traps"][0]
    trap.update(
        rate_model="independent_frequencies",
        release_frequency_per_s=[0.04] * 3,
        initial_occupancy=[1 / 19] * 8,
        independent_rate_histories_reviewed=True,
    )
    trap["population"]["binding_energy_J_mol"] = 45000.0
    independent["detailed_balance_rate_ratio_reviewed"] = False
    result = evaluate("independent_frequencies", kinetic.model_copy(update={"parameters": independent}))
    reference("independent_rate_lattice", result["values"]["lattice_mean_hydrogen_atoms_mol_m3"][-1], 100.0)
    reference("independent_rate_trap_occupancy", result["values"]["traps"][0]["final_occupancy"][0], 1 / 19)
    for kind in ("linear_surface_transfer", "dissociative_surface_reaction"):
        p = deepcopy(kinetic.parameters)
        for side in ("left_boundary", "right_boundary"):
            boundary = p[side]
            boundary["kind"] = kind
            if kind == "linear_surface_transfer":
                boundary.update(
                    equilibrium_lattice_hydrogen_atoms_mol_m3=[100.0] * 3, transfer_coefficient_m_s=[1e-4] * 3
                )
            else:
                boundary.update(
                    absorption_capacity_flux_mol_m2_s=[1e-6] * 3, desorption_capacity_flux_mol_m2_s=[9e-6] * 3
                )
        record = evaluate(kind, kinetic.model_copy(update={"parameters": p}))
        assert record["status"] == "computed"
        reference(kind + "_balanced_mean", record["values"]["lattice_mean_hydrogen_atoms_mol_m3"][-1], 100.0)
    # Independent continuum mean reference: classical two-sided constant-boundary slab series.
    # This is a verification-only authored series, never a replacement production solver.
    continuum_mean = 100 * (
        1
        - 8 / math.pi**2 * math.fsum(math.exp(-n * n * math.pi**2 * 0.1) / (n * n) for n in range(1, 102, 2))
    )
    grid = []
    for cells in (8, 16):
        p = deepcopy(kinetic.parameters)
        p.update(
            cell_count=cells,
            initial_lattice_hydrogen_atoms_mol_m3=[0.0] * cells,
            traps=[],
            detailed_balance_rate_ratio_reviewed=False,
        )
        for side in ("left_boundary", "right_boundary"):
            p[side].update(kind="prescribed_lattice_concentration", lattice_hydrogen_atoms_mol_m3=[100.0] * 3)
        request = kinetic.model_copy(
            update={"parameters": p, "cell_ids": tuple(f"refined-cell-{i}" for i in range(cells))}
        )
        record = evaluate(f"two_sided_diffusion_{cells}", request)
        assert record["status"] == "computed"
        actual = record["values"]["lattice_mean_hydrogen_atoms_mol_m3"][-1]
        grid.append(
            {
                "cells": cells,
                "native_mean_mol_m3": actual,
                "continuum_mean_mol_m3": continuum_mean,
                "absolute_error": abs(actual - continuum_mean),
                "relative_mass_balance_error": record["values"]["relative_mass_balance_error"],
            }
        )
    assert (
        grid[1]["absolute_error"] < grid[0]["absolute_error"]
        and grid[1]["absolute_error"] / continuum_mean < 0.01
    )
    exhausted = evaluate(
        "solver_attempts_exhausted",
        request.model_copy(update={"parameters": p | {"maximum_step_attempts": 1}}),
    )
    assert (
        exhausted["status"] == "out_of_scope"
        and "completed_elapsed_time_s" in exhausted["values"]
        and "final_lattice_hydrogen_atoms_mol_m3" not in exhausted["values"]
    )
    assert (
        evaluate(
            "unsupported_local_occupancy", changed(equilibrium, maximum_supported_lattice_occupancy=0.05)
        )["status"]
        == "out_of_scope"
    )
    assert (
        evaluate("capacity_exceeded", changed(equilibrium, total_hydrogen_atoms_mol_m3=1101.0))["status"]
        == "out_of_scope"
    )
    assert (
        evaluate("unreviewed_stress_scope", changed(kinetic, negligible_stress_gradient_reviewed=False))[
            "status"
        ]
        == "needs_data"
    )
    for method, request in requests.items():
        assert (
            evaluate("unreviewed_" + method, request.model_copy(update={"applicability_reviews": {}}))[
                "status"
            ]
            == "needs_data"
        )
        assert (
            evaluate("unknown_" + method, request.model_copy(update={"parameters": None}))["status"]
            == "needs_data"
        )
    refused("zero_exposure", changed(semi, exposure_s=0.0))
    refused("negative_concentration", changed(semi, initial_concentration_mol_m3=-1.0))
    refused("reversed_time_knots", changed(kinetic, elapsed_time_s=[0.0, 10.0, 1.0]))
    refused("wrong_source_pin", semi.model_copy(update={"owner_source_tree_sha256": "0" * 64}))
    job = prepare_material_job(
        SEMI,
        semi.parameters,
        context,
        location_id=semi.location_id,
        evidence_ids=semi.evidence_ids,
        client=client,
    )
    assert evaluate("pinned_job_replay", semi, pinned_job=job)["values"] == records[SEMI]["values"]
    after = revision(args.materials)
    support_after = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in files}
    assert before == after and support_before == support_after
    folder = Path("artifacts/hydrogen_transport")
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("native_records", records),
        ("independent_references", references),
        ("spatial_reference", grid),
    ):
        (folder / f"{name}.json").write_text(
            json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    summary = {
        "schema": "pipeline_integrity.hydrogen_transport_verification.v1",
        "status": "passed",
        "native_models": {
            method: {
                "model_version": records[method]["native_receipt"]["job"]["model_version"],
                "input_schema_digest": records[method]["native_receipt"]["job"]["input_schema_digest"],
            }
            for method in requests
        },
        "native_case_count": sum(r["native_receipt"] is not None for r in records.values()),
        "case_status_counts": {
            s: sum(r["status"] == s for r in records.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        "independent_reference_count": len(references),
        "maximum_reference_absolute_error": max(r["absolute_error"] for r in references.values()),
        "spatial_refinement": grid,
        "spatial_comparison_is_global_convergence_or_field_validation": False,
        "refusal_count": len(refusals),
        "refusals": refusals,
        "pinned_job_replay_verified": True,
        "signed_desorption_and_zero_hydrogen_preserved": True,
        "independent_frequency_histories_not_overwritten_by_binding_energy": True,
        "incomplete_trajectory_withheld": True,
        "current_was_inverted_into_boundary_concentration": False,
        "public_data_calibration_performed": False,
        "experimental_field_validation": False,
        "limitations": [
            "Concentrations count mol of H atoms; kinetic profiles are cell averages and both surface fluxes are positive inward.",
            "A local equilibrium differential diffusivity is concentration dependent, not one fitted transient permeation coefficient.",
            "Two meshes and declared mass/time tolerances are numerical evidence only; they do not qualify global accuracy, physical surfaces, trapping, embrittlement or failure probability.",
        ],
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "external_support_files_before": support_before,
        "external_support_files_after": support_after,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/hydrogen_transport.py")),
        "source_guard_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/hydrogen_transport.py")),
    }
    Path("research/hydrogen_transport_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": 3,
                "native_cases": summary["native_case_count"],
                "references": len(references),
                "refusals": len(refusals),
                "mesh_comparison": grid,
            }
        )
    )


if __name__ == "__main__":
    main()
