"""Independently authored noise-shift reference; optionally save a ready-to-run JSON job."""

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline_integrity.acoustics import (
    DelayRequest,
    PipeSpan,
    RelativeTiming,
    SampledTrace,
    estimate_delay,
    locate_on_span,
)
from pipeline_integrity.contracts import RunContext, digest


def authored_job():
    rng = np.random.default_rng(17)
    left = rng.normal(size=4096)
    right = np.r_[np.zeros(25), left[:-25]]
    context = RunContext(
        asset_id="authored_pipe_span", scenario_id="known_source", clock_id="elapsed", origin="synthetic"
    )
    traces = []
    for name, values in (("left", left), ("right", right)):
        traces.append(
            SampledTrace(
                id=name,
                asset_id=context.asset_id,
                sensor_id=name,
                clock_id=context.clock_id,
                start_time_s=0.0,
                sample_period_s=0.0005,
                quantity_id="wall_acceleration",
                unit="m/s^2",
                origin="synthetic",
                source_digest=digest({"generator": "numpy_default_rng_17", "authored_shift_samples": 25}),
                values=tuple(float(v) for v in values),
                valid=(True,) * len(values),
            )
        )
    request = DelayRequest(
        maximum_delay_s=0.08,
        timing=RelativeTiming(
            right_minus_left_latency_s=0.0, absolute_error_bound_s=0.0001, source_id="authored_common_clock"
        ),
    )
    span = PipeSpan(
        left_position_m=0.0,
        right_position_m=100.0,
        wave_speed_m_s=1000.0,
        wave_speed_error_m_s=5.0,
        propagation_source_id="authored_nondispersive_carrier",
        geometry_source_id="authored_100_m_span",
        source_bracketed=True,
        single_path_nondispersive=True,
    )
    return {
        "left": traces[0].model_dump(mode="json"),
        "right": traces[1].model_dump(mode="json"),
        "specification": request.model_dump(mode="json"),
        "span": span.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-job", type=Path)
    args = parser.parse_args()
    job = authored_job()
    if args.write_job is not None:
        args.write_job.parent.mkdir(parents=True, exist_ok=True)
        args.write_job.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"authored_job": str(args.write_job)}))
    else:
        context = RunContext.model_validate_json(json.dumps(job["context"]))
        delay = estimate_delay(
            SampledTrace.model_validate_json(json.dumps(job["left"])),
            SampledTrace.model_validate_json(json.dumps(job["right"])),
            DelayRequest.model_validate_json(json.dumps(job["specification"])),
            context,
        )
        location = locate_on_span(delay, PipeSpan.model_validate_json(json.dumps(job["span"])), context)
        assert location["position_m"] == 43.75
        print(json.dumps({"delay": delay, "location": location}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
