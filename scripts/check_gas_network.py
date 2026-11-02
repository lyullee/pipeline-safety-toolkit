"""Run real gas-network engines, a low-Mach laminar reference and explicit GasLib mapping limits."""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.gas_network import GasBoundary, GasNetwork, GasPipe, solve_gas_network
from pipeline_integrity.gaslib import read_gaslib
from pipeline_integrity.thermo import CoolPropProvider, StateRequest


def summary(result):
    checks = result.checks
    return dict(
        status=result.status,
        result_digest=result.result_digest,
        global_mass_residual_kg_s=checks.get("global_mass_residual_kg_s"),
        global_static_energy_residual_w=checks.get("global_static_energy_residual_w"),
        maximum_mach=max((s["mach"] for s in checks.get("segments", [])), default=None),
        maximum_segment_density_change=max(
            (s["relative_density_change"] for s in checks.get("segments", [])), default=None
        ),
        node_pressures_pa_abs={}
        if result.native is None
        else {
            n: s["pressure_pa_abs"]
            for n, s in result.native["inspected_states"].items()
            if not n.startswith("__gas_internal:")
        },
        physical_field_validation=False,
        nodes_without_resolved_inflow=checks.get("nodes_without_resolved_inflow", []),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.lh2dt / "src").resolve()), str(Path("examples").resolve())]
    from gas_network import make_case

    source = revision(args.lh2dt)
    root = Path("artifacts/gas_network")
    root.mkdir(parents=True, exist_ok=True)
    spec, context = make_case()
    cases = {}

    def evaluate(name, specification):
        result = solve_gas_network(specification, context)
        result.check_integrity()
        cases[name] = result
        (root / f"{name}.json").write_text(
            json.dumps(
                {
                    "specification": specification.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"case": name, "status": result.status}), flush=True)
        return result

    methane = evaluate("methane_load", spec)
    assert methane.status == "computed", methane.notes
    evaluate("nitrogen_load", spec.model_copy(update={"fluid": "Nitrogen"}))
    assert cases["nitrogen_load"].status == "computed"
    heated = evaluate(
        "heated_load",
        spec.model_copy(update={"pipes": (spec.pipes[0].model_copy(update={"heat_into_fluid_w": 500.0}),)}),
    )
    assert heated.status == "computed", heated.notes
    assert (
        heated.native["inspected_states"]["load"]["temperature_k"]
        > methane.native["inspected_states"]["load"]["temperature_k"]
    )
    mixed = spec.model_copy(
        update={
            "boundaries": (
                spec.boundaries[0],
                GasBoundary(id="warm", pressure_pa_abs=6e6, temperature_k=320.0),
            ),
            "junctions": (
                spec.junctions[0].model_copy(update={"initial_temperature_k": 310.0, "withdrawal_kg_s": 2.0}),
            ),
            "pipes": (
                spec.pipes[0],
                spec.pipes[0].model_copy(update={"id": "warm_pipe", "start_node": "warm"}),
            ),
        }
    )
    mixing = evaluate("two_source_mixing", mixed)
    assert (
        mixing.status == "computed" and 300 < mixing.native["inspected_states"]["load"]["temperature_k"] < 320
    )
    fixed = spec.model_copy(
        update={
            "junctions": (),
            "boundaries": spec.boundaries
            + (GasBoundary(id="load", pressure_pa_abs=5999000.0, temperature_k=300.0),),
        }
    )
    forward = evaluate("fixed_forward", fixed)
    reverse = evaluate(
        "fixed_reverse",
        fixed.model_copy(
            update={"pipes": (spec.pipes[0].model_copy(update={"start_node": "load", "end_node": "source"}),)}
        ),
    )

    def mass(result, pipe_id="main"):
        name = result.native["pipe_links"][pipe_id][0]
        return result.native["solution"]["link_flows"][name]["mass_flow_kg_s"]

    assert forward.status == reverse.status == "computed"
    assert math.isclose(mass(forward), -mass(reverse), rel_tol=1e-8, abs_tol=1e-9)
    parallel = evaluate(
        "parallel",
        fixed.model_copy(
            update={"pipes": fixed.pipes + (fixed.pipes[0].model_copy(update={"id": "parallel_pipe"}),)}
        ),
    )
    assert parallel.status == "computed" and math.isclose(
        mass(parallel), mass(forward), rel_tol=1e-8, abs_tol=1e-9
    )
    assert math.isclose(mass(parallel, "parallel_pipe"), mass(forward), rel_tol=1e-8, abs_tol=1e-9)
    high = fixed.model_copy(
        update={
            "boundaries": (
                fixed.boundaries[0],
                fixed.boundaries[1].model_copy(update={"pressure_pa_abs": 3e6}),
            ),
            "pipes": (fixed.pipes[0].model_copy(update={"length_m": 1.0, "segments": 1}),),
        }
    )
    assert evaluate("outside_low_mach_domain", high).status == "out_of_scope"
    assert evaluate("liquid_rejected", spec.model_copy(update={"fluid": "Water"})).status == "out_of_scope"
    assert (
        evaluate("conversion_budget", spec.model_copy(update={"maximum_state_conversions": 1})).status
        == "withheld"
    )
    stagnant_spec = spec.model_copy(
        update={
            "junctions": (
                spec.junctions[0].model_copy(
                    update={
                        "withdrawal_kg_s": 0.0,
                        "initial_pressure_pa_abs": 6e6,
                        "initial_temperature_k": 320.0,
                    }
                ),
            )
        }
    )
    stagnant = evaluate("stagnant_unknown_enthalpy", stagnant_spec)
    assert stagnant.status == "withheld" and stagnant.checks["native_solver_and_closure"]
    assert stagnant.checks["nodes_without_resolved_inflow"]
    mixture = evaluate(
        "methane_hydrogen_mixture", spec.model_copy(update={"fluid": "Methane[0.9]&Hydrogen[0.1]"})
    )
    # The actual installed property backend decides support; no pure-fluid fallback is permitted.
    assert mixture.status == "computed", mixture.notes
    if mixture.native is not None:
        assert mixture.fluid == "Methane[0.9]&Hydrogen[0.1]"

    laminar = {}
    for segments in (1, 4, 8):
        trial = GasNetwork(
            fluid="Nitrogen",
            property_basis=spec.property_basis,
            parameter_source_id=spec.parameter_source_id,
            boundaries=(
                GasBoundary(id="source", pressure_pa_abs=101000.0, temperature_k=300.0),
                GasBoundary(id="load", pressure_pa_abs=100000.0, temperature_k=300.0),
            ),
            pipes=(
                GasPipe(
                    id="main",
                    start_node="source",
                    end_node="load",
                    length_m=4000.0,
                    diameter_m=0.04,
                    roughness_m=0.0,
                    segments=segments,
                ),
            ),
            mass_tolerance_kg_s=1e-10,
            relative_closure_tolerance=1e-9,
        )
        result = evaluate(f"laminar_{segments}", trial)
        assert result.status == "computed", result.notes
        laminar[segments] = mass(result)
    midpoint = CoolPropProvider().resolve(StateRequest(fluid="Nitrogen", inputs={"P": 100500.0, "T": 300.0}))
    reference = (
        math.pi
        * 0.04**4
        * (101000.0**2 - 100000.0**2)
        / (256 * midpoint.viscosity_pa_s * (100500.0 / midpoint.density_kg_m3) * 4000.0)
    )
    errors = {n: abs(q - reference) / reference for n, q in laminar.items()}
    assert errors[8] < errors[1] / 2 and errors[8] < 0.002, errors
    reynolds = {n: 4 * q / (math.pi * 0.04 * midpoint.viscosity_pa_s) for n, q in laminar.items()}
    assert max(reynolds.values()) < 2300

    dataset = next(d for d in catalog() if d.id == "gaslib11")
    raw = Path("data/raw/gaslib11/GasLib-11-v1-20211130.zip")
    assert dataset.assets[0].checksum == "sha256:" + file_digest(raw)
    source_data = read_gaslib(raw)
    assert source_data["node_types"] == {"source": 3, "sink": 3, "innode": 5}
    assert source_data["connection_types"] == {"pipe": 8, "valve": 1, "compressorStation": 2}
    assert (
        source_data["complete_network_run_status"] == "needs_data"
        and source_data["inline_cc_by_3_0_markers_present"]
    )
    selected_id = "pipe01_entry01_entry03"
    geometry = source_data["pipes"][selected_id]
    derived = fixed.model_copy(
        update={
            "boundaries": (
                fixed.boundaries[0],
                fixed.boundaries[1].model_copy(update={"pressure_pa_abs": 5.5e6}),
            ),
            "pipes": (
                GasPipe(
                    id="main",
                    start_node="source",
                    end_node="load",
                    length_m=geometry["length_m"],
                    diameter_m=geometry["diameter_m"],
                    roughness_m=geometry["roughness_m"],
                    segments=8,
                ),
            ),
        }
    )
    isolated = evaluate("gaslib_isolated_geometry_declared_methane", derived)
    assert isolated.status == "computed", isolated.notes
    job = {"specification": spec.model_dump(mode="json"), "context": context.model_dump(mode="json")}
    Path("examples/gas_network.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str((args.lh2dt / "src").resolve()), str(Path("src").resolve())]
    )
    call = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline_integrity.cli",
            "gas-network",
            "examples/gas_network.json",
            "--output",
            str(root / "cli_result.json"),
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert call.returncode == 0, call.stderr
    assert (
        json.loads((root / "cli_result.json").read_text(encoding="utf-8"))["result_digest"]
        == methane.result_digest
    )
    assert source["source_tree_sha256"] == revision(args.lh2dt)["source_tree_sha256"]
    result = dict(
        schema="pipeline_integrity.gas_network_checks.v1",
        native_source=source,
        module_sha256={
            name: file_digest(Path("src/pipeline_integrity") / (name + ".py"))
            for name in ("gas_network", "gaslib")
        },
        verification_script_sha256=file_digest(Path(__file__)),
        cases={n: summary(r) for n, r in cases.items()},
        native_provenance=methane.native["provenance"],
        property_runtime={
            k: midpoint.model_dump(mode="json")[k] for k in ("provider_version", "provider_revision")
        },
        laminar_reference=dict(
            relation="constant mid-state p/rho and viscosity in an isothermal laminar p-squared reference",
            reference_mass_flow_kg_s=reference,
            actual_mass_flow_kg_s=laminar,
            relative_errors=errors,
            reynolds=reynolds,
            experimental_validation=False,
        ),
        gaslib=dict(
            archive_sha256=source_data["archive_sha256"],
            network_member_sha256=source_data["network_member_sha256"],
            scenario_member_sha256=source_data["scenario_member_sha256"],
            citation=dataset.citation,
            creators=dataset.creators,
            license_id=dataset.license_id,
            node_types=source_data["node_types"],
            connection_types=source_data["connection_types"],
            complete_network_run_status="needs_data",
            missing_mapping=source_data["missing_mapping"],
            selected_geometry_id=selected_id,
            explicit_changes=[
                "isolated pipe; other network devices and nominations not simulated",
                "declared pure Methane and 300 K source; original composition not inferred",
                "declared absolute 6/5.5 MPa reservoirs",
                "adiabatic declared case replaces original heat-transfer coefficient; no ambient boundary inferred",
            ],
            initial_python_download="TLS certificate-chain validation failed; no verification disabled",
            actual_acquisition="PowerShell HTTPS with default OS certificate validation, then pinned SHA-256 and ZIP checks",
        ),
        actual_cli_result_matches=True,
        raw_and_transformed_observation_rows_excluded=True,
        full_total_energy_or_field_validation=False,
    )
    Path("research/gas_network_results.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"cases": len(cases), "laminar_relative_errors": errors, "complete_gaslib_run": "needs_data"}
        )
    )


if __name__ == "__main__":
    main()
