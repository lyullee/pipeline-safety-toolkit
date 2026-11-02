"""Run the public transient example and export aggregate analytic-comparison evidence."""

import json
import math
from pathlib import Path

import numpy as np

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import LiquidTransient, simulate_liquid_transient


def main():
    request = json.loads(Path("examples/liquid_transient.json").read_text(encoding="utf-8"))
    specification = LiquidTransient.model_validate_json(json.dumps(request["specification"]))
    context = RunContext.model_validate_json(json.dumps(request["context"]))
    coarse = simulate_liquid_transient(specification, context)
    fine = simulate_liquid_transient(
        specification.model_copy(update={"requested_timestep_s": 0.0125}), context
    )
    pipe = specification.pipes[0]
    expected = (
        pipe.wave_speed_m_s
        * pipe.initial_volume_flow_m3_s
        / (math.pi * pipe.diameter_m**2 / 4)
        / specification.gravity_m_s2
    )
    heads = np.array(coarse["pipes"][pipe.id]["end_head_m"])
    refined = np.array(fine["pipes"][pipe.id]["end_head_m"])[::2]
    record = {
        "schema": "pipeline_integrity.transient_reference.v1",
        "origin": "synthetic",
        "status": coarse["status"],
        "joukowsky_head_rise_m": expected,
        "computed_head_rise_m": float(heads.max() - heads[0]),
        "computed_reflected_head_fall_m": float(heads[0] - heads.min()),
        "refinement_maximum_difference_m": float(np.max(np.abs(heads - refined))),
        "maximum_volume_residual_m3": coarse["volume_balance"]["maximum_absolute_residual_m3"],
        "maximum_junction_residual_m3_s": coarse["maximum_junction_residual_m3_s"],
        "grid": coarse["grid"],
        "field_validation_claim": False,
        "tsnet_probe": {
            "distribution_version": "0.3.1",
            "numpy_version": "2.5.4",
            "wntr_version": "1.5.0",
            "outcome": "failed_during_discretization",
            "reason": "NumPy non-scalar-array to int conversion raised TypeError; not adopted or patched.",
        },
    }
    assert coarse["status"] == "computed"
    assert abs(record["computed_head_rise_m"] - expected) < 1e-9
    target = Path("research/transient_verification.json")
    target.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
