"""Original straight-pipe temperature/pressure coupling with separately installed material and frame engines."""

import argparse
import json
import runpy
import sys
from pathlib import Path

from pipeline_integrity.contracts import Pressure
from pipeline_integrity.frame_loading import (
    PressureFrameMapping,
    UniformFrameTemperature,
    prepare_pressure_frame,
    prepare_uniform_thermal_frame,
    solve_loaded_frame,
)
from pipeline_integrity.material_exchange import PressureSection, evaluate_pressure_section
from pipeline_integrity.structural import FrameCombination


def reference_frame():
    example = runpy.run_path(str(Path(__file__).with_name("pipe_frame.py")))
    frame = example["make_case"]().model_copy(
        update={
            "node_loads": (),
            "load_cases": ("thermal", "pressure"),
            "combinations": (
                FrameCombination(
                    id="combined", factors={"thermal": 1.0, "pressure": 1.0}, evidence_id="reference"
                ),
            ),
        }
    )
    return frame, example["context"]()


def temperature():
    return UniformFrameTemperature(
        id="temperature",
        member_id="pipe",
        material_id="declared-elastic",
        case_id="thermal",
        temperature_k=343.15,
        stress_free_temperature_k=293.15,
        expansion_coefficient_per_k=1e-5,
        coefficient_temperature_support_k=(280.0, 400.0),
        evidence_ids=("reference",),
        uniform_temperature_and_constant_coefficient_reviewed=True,
    )


def pressure_section(frame):
    return PressureSection(
        asset_id=frame.asset_id,
        location_id="pipe",
        clock_id=frame.clock_id,
        time_s=frame.time_s,
        inner_radius_m=0.09,
        outer_radius_m=0.1,
        evaluation_radii_m=(0.09, 0.095, 0.1),
        internal_pressure=Pressure(value=2e6, unit="Pa", reference="absolute"),
        external_pressure=Pressure(value=1e5, unit="Pa", reference="absolute"),
        pressure_loaded_closed_ends=True,
        geometry_evidence_id="reference",
        pressure_evidence_id="reference",
    )


def pressure_mapping(record):
    return PressureFrameMapping(
        id="pressure",
        member_id="pipe",
        case_id="pressure",
        source_location_id="pipe",
        expected_pressure_receipt_digest=record["record_digest"],
        evidence_ids=("reference",),
        straight_uniform_cylinder_applicability_reviewed=True,
        end_condition_and_total_pressure_reviewed=True,
        poisson_coupling_reviewed=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path)
    args = parser.parse_args()
    if args.materials is not None:
        sys.path.insert(0, str((args.materials / "src").resolve()))
    frame, context = reference_frame()
    thermal = prepare_uniform_thermal_frame(frame, temperature(), context)
    section = evaluate_pressure_section(pressure_section(frame), context)
    pressure = prepare_pressure_frame(frame, section, pressure_mapping(section), context)
    print(json.dumps(solve_loaded_frame(frame, (thermal, pressure), context), indent=2))
