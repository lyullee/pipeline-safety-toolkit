"""Declared methane network with a pressure reservoir and a controlled mass withdrawal."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_network import GasBoundary, GasJunction, GasNetwork, GasPipe, solve_gas_network


def make_case(fluid="Methane", segments=2):
    context = RunContext(
        asset_id="declared_gas_network",
        scenario_id="steady_withdrawal",
        clock_id="steady_snapshot",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="declared_gas_case",
                citation="Original declared gas network geometry, boundary states and mass load",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    specification = GasNetwork(
        fluid=fluid,
        property_basis="CoolProp HEOS reference shared within this solve; adapter does not reset it",
        parameter_source_id="declared_gas_case",
        boundaries=(GasBoundary(id="source", pressure_pa_abs=6e6, temperature_k=300.0),),
        junctions=(
            GasJunction(
                id="load", initial_pressure_pa_abs=5999000.0, initial_temperature_k=300.0, withdrawal_kg_s=1.0
            ),
        ),
        pipes=(
            GasPipe(
                id="main",
                start_node="source",
                end_node="load",
                length_m=1000.0,
                diameter_m=0.2,
                roughness_m=1e-5,
                segments=segments,
            ),
        ),
    )
    return specification, context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path)
    parser.add_argument("--write-job", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    specification, context = make_case()
    if args.write_job:
        args.write_job.parent.mkdir(parents=True, exist_ok=True)
        args.write_job.write_text(
            json.dumps(
                {
                    "specification": specification.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return
    if args.lh2dt:
        sys.path.insert(0, str((args.lh2dt / "src").resolve()))
    result = solve_gas_network(specification, context)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    else:
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
