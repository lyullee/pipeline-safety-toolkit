"""Authored two-pipe liquid reference with native finite-stroke actuator motion."""

import argparse
import json
import math
import sys
from pathlib import Path

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import (
    History,
    LiquidNode,
    LiquidPipe,
    LiquidTransient,
    simulate_liquid_transient,
)
from pipeline_integrity.valve_motion import (
    ConductanceCurve,
    ValveCommand,
    ValveMotionRequest,
    liquid_valve_from_motion,
    simulate_valve_motion,
)


def make_case(
    *,
    timestep=0.025,
    stroke_time=0.1,
    command_time=0.2,
    command=True,
    closed_leakage_fraction=0.0,
    direction=1.0,
    duration=4.0,
):
    context = RunContext(
        asset_id="two_pipe_valve_network",
        scenario_id="finite_stroke_reference",
        clock_id="elapsed",
        origin="synthetic",
    )
    motion_context = context.model_copy(update={"asset_id": "V-1"})
    count = math.ceil(duration / timestep)
    dt = duration / count
    full_area = 0.01 / math.sqrt(2 * 9.80665 * 5.0)
    curve = ConductanceCurve(
        opening=(0.0, 0.5, 1.0),
        effective_area_m2=(
            full_area * closed_leakage_fraction,
            full_area * (closed_leakage_fraction + 0.25 * (1 - closed_leakage_fraction)),
            full_area,
        ),
        parameter_source_id="authored_nonlinear_effective_area_reference",
    )
    motion = simulate_valve_motion(
        ValveMotionRequest(
            component_id="V-1",
            initial_opening=1.0,
            full_stroke_time_s=stroke_time,
            sample_time_s=tuple(i * dt for i in range(count + 1)),
            conductance=curve,
            commands=(
                ValveCommand(
                    id="close_command",
                    time_s=command_time,
                    target_opening=0.0,
                    evidence_id="authored:issued_command",
                ),
            )
            if command
            else (),
        ),
        motion_context,
    )
    left_head, right_head = (100.0, 95.0) if direction > 0 else (95.0, 100.0)
    nodes = tuple(
        LiquidNode(
            id=name,
            elevation_m=0.0,
            initial_head_m=head,
            boundary=boundary,
            history=History(time_s=(0.0,), values=(head if boundary == "head" else 0.0,)),
        )
        for name, head, boundary in (
            ("source", left_head, "head"),
            ("left", left_head, "withdrawal"),
            ("right", right_head, "withdrawal"),
            ("sink", right_head, "head"),
        )
    )
    pipes = tuple(
        LiquidPipe(
            id=key,
            start_node=start,
            end_node=end,
            length_m=1000.0,
            diameter_m=0.2,
            wave_speed_m_s=1000.0,
            darcy_friction_factor=0.0,
            initial_volume_flow_m3_s=direction * 0.01,
        )
        for key, start, end in (("upstream", "source", "left"), ("downstream", "right", "sink"))
    )
    valve = liquid_valve_from_motion(
        motion,
        start_node="left",
        end_node="right",
        initial_volume_flow_m3_s=direction * 0.01,
        network_context=context,
    )
    specification = LiquidTransient(
        nodes=nodes,
        pipes=pipes,
        valves=(valve,),
        duration_s=duration,
        requested_timestep_s=timestep,
        density_kg_m3=998.0,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
    )
    return specification, context, motion


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path)
    parser.add_argument("--write-job", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.lh2dt:
        sys.path.insert(0, str((args.lh2dt / "src").resolve()))
    specification, context, motion = make_case()
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
    result = simulate_liquid_transient(specification, context)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "command_records": motion.command_records,
                "maximum_upstream_head_m": max(result["pipes"]["upstream"]["end_head_m"]),
                "maximum_pipe_volume_residual_m3": result["maximum_pipe_volume_residual_m3"],
                "maximum_junction_residual_m3_s": result["maximum_junction_residual_m3_s"],
                "notes": result["notes"],
            }
        )
    )


if __name__ == "__main__":
    main()
