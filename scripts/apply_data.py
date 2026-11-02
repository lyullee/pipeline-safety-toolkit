"""Run bounded, reproducible source-schema applications; publish only aggregate findings."""

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.loaders import audit_ili_workbooks, hydraulic_cycle, iter_battledim_frames
from pipeline_integrity.water import WaterRun, simulate_water_network


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("research/data_application.json"))
    args = parser.parse_args()
    root = args.raw_root
    frames = tuple(iter_battledim_frames(root / "battledim", 2018, maximum_rows=864))
    first = frames[0]
    matrix = np.array([[f.values[c] for c in first.values] for f in frames], dtype=float)
    network = simulate_water_network(
        root / "battledim/L-TOWN.inp",
        WaterRun(duration_s=86400.0, hydraulic_timestep_s=300.0, report_timestep_s=300.0),
    )
    cycle = hydraulic_cycle(root / "hydraulic/source.zip", 0, ("PS1", "FS1", "TS1", "VS1"))
    summary = {
        "schema": "pipeline_integrity.data_application.v1",
        "battledim": {
            "source": "https://doi.org/10.5281/zenodo.4017659",
            "origin": "simulation",
            "rows_examined": len(frames),
            "channels": len(first.values),
            "elapsed_start_s": first.time_s,
            "elapsed_end_s": frames[-1].time_s,
            "all_selected_samples_finite": bool(np.isfinite(matrix).all()),
            "pressure_is_head_m_not_absolute_pressure": True,
            "source_sha256": {
                name: file_digest(root / "battledim" / name)
                for name in (
                    "2018_SCADA_Pressures.csv",
                    "2018_SCADA_Flows.csv",
                    "2018_SCADA_Levels.csv",
                    "L-TOWN.inp",
                )
            },
            "network": {
                k: v for k, v in network.items() if k not in {"pressure_head_m", "flow_m3_s", "time_s"}
            },
            "network_report_rows": len(network["time_s"]),
            "observation_agreement_claim": False,
            "limitation": "The released nominal network differs from the hidden generating network; continuity is a solver check.",
        },
        "hydraulic": {
            "source": "https://doi.org/10.24432/C5CW21",
            "origin": "laboratory",
            "source_sha256": file_digest(root / "hydraulic/source.zip"),
            "cycle_index": cycle["cycle_index"],
            "label_excluded_from_signal_inputs": True,
            "sensors": {
                name: {
                    "samples": len(row["values"]),
                    "sample_rate_hz": row["sample_rate_hz"],
                    "unit": row["unit"],
                    "finite": bool(row["valid"].all()),
                }
                for name, row in cycle["sensors"].items()
            },
            "pressure_reference_resolved": False,
            "predictive_accuracy_established": False,
        },
        "ili": {
            "source": "https://doi.org/10.17632/c2h2jf5c54.1",
            "source_sha256": file_digest(root / "ili/ili-v1.zip"),
            **audit_ili_workbooks(root / "ili/ili-v1.zip"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "network_status": network["status"],
                "continuity_residual_m3_s": network["maximum_junction_continuity_residual_m3_s"],
                "ili_workbooks": len(summary["ili"]["workbooks"]),
            }
        )
    )


if __name__ == "__main__":
    main()
