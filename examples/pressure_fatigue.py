"""A declared pressure history; no real material fatigue calibration is implied."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fatigue_exchange import (
    FatigueCurve,
    FatigueGeometry,
    PressureFatigueRequest,
    PressureHistory,
    assess_pressure_fatigue,
)


def make_case():
    source = "authored_pressure_fatigue_reference"
    context = RunContext(
        asset_id="declared_thin_pipe",
        scenario_id="two_pressure_excursions",
        clock_id="reference_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=source,
                citation="Original demonstration: declared pressure, geometry and artificial fatigue curve",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    times = (0.0, 1.0, 2.0, 3.0, 4.0)
    pressures = (2e6, 4e6, 2e6, 4e6, 2e6)
    history = PressureHistory(
        asset_id=context.asset_id,
        location_id="uniform_wall",
        scenario_id=context.scenario_id,
        clock_id=context.clock_id,
        time_s=times,
        pressure_pa=pressures,
        pressure_reference="gauge",
        ambient_pressure_pa_abs=101325.0,
        temperature_k=(293.15,) * 5,
        valid=(True,) * 5,
        pressure_origin="synthetic",
        temperature_origin="user_declared",
        source_id=source,
        source_sha256=digest({"time_s": times, "gauge_pressure_pa": pressures}),
        temperature_evidence_id=source,
    )
    geometry = FatigueGeometry(
        mean_radius_m=0.1,
        wall_thickness_m=0.005,
        maximum_thickness_to_radius_ratio=0.1,
        evidence_id=source,
    )
    curve = FatigueCurve(
        reference_stress_range_pa=40e6,
        reference_cycles=1000.0,
        basquin_exponent=3.0,
        minimum_stress_range_pa=1.0,
        maximum_stress_range_pa=100e6,
        minimum_temperature_k=290.0,
        maximum_temperature_k=300.0,
        minimum_stress_ratio=0.0,
        maximum_stress_ratio=0.999999999999,
        basis="synthetic_reference",
        evidence_id=source,
    )
    request = PressureFatigueRequest(
        history=history,
        geometry=geometry,
        curve=curve,
        external_pressure_pa_abs=101325.0,
        external_pressure_evidence_id=source,
        initial_damage=0.1,
        initial_damage_evidence_id=source,
        declared_damage_limit=1.0,
        damage_limit_evidence_id=source,
    )
    return request, context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path)
    parser.add_argument("--write-job", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    request, context = make_case()
    if args.write_job:
        args.write_job.parent.mkdir(parents=True, exist_ok=True)
        args.write_job.write_text(
            json.dumps(
                {
                    "specification": request.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return
    if args.materials:
        sys.path.insert(0, str((args.materials / "src").resolve()))
    result = assess_pressure_fatigue(request, context)
    rendered = result.model_dump_json(indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
