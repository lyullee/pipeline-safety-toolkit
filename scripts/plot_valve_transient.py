"""Render the reproducible synthetic valve/pressure reference; no native source prose is reused."""

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path("artifacts/valve_transient")
    cases = {
        key: json.loads((root / f"{key}.json").read_text(encoding="utf-8"))
        for key in ("fast", "delayed", "no_command", "slow", "closed_leakage")
    }
    colors = {
        "fast": "#2563eb",
        "delayed": "#d97706",
        "no_command": "#64748b",
        "slow": "#15803d",
        "closed_leakage": "#9333ea",
    }
    labels = {
        "fast": "Fast closure",
        "delayed": "Command delayed 0.3 s",
        "no_command": "No command",
        "slow": "3 s full stroke",
        "closed_leakage": "5% closed CdA",
    }
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 2, figsize=(12.0, 8.0), layout="constrained")
    for key in ("fast", "delayed", "slow", "no_command"):
        record = cases[key]
        axes[0, 0].plot(
            record["motion"]["time_s"], record["motion"]["opening"], label=labels[key], color=colors[key]
        )
    fast = cases["fast"]
    axes[0, 0].step(
        fast["motion"]["time_s"],
        fast["motion"]["target_opening"],
        where="post",
        color="#0f172a",
        ls=":",
        lw=1.3,
        label="Fast target",
    )
    axes[0, 0].set(title="Command and physical opening", ylabel="Opening (1)", ylim=(-0.04, 1.12))
    for key in ("fast", "slow", "closed_leakage", "no_command"):
        result = cases[key]["result"]
        axes[0, 1].plot(
            result["time_s"],
            1000 * np.asarray(result["valves"]["V-1"]["volume_flow_m3_s"]),
            label=labels[key],
            color=colors[key],
        )
    axes[0, 1].set(title="Valve flow responds to head and CdA", ylabel="Signed valve flow (L/s)")
    for key in ("fast", "delayed", "slow", "no_command"):
        result = cases[key]["result"]
        axes[1, 0].plot(
            result["time_s"], result["pipes"]["upstream"]["end_head_m"], label=labels[key], color=colors[key]
        )
    axes[1, 0].set(title="Pressure waves at the upstream valve port", ylabel="Piezometric head (m)")
    result = fast["result"]
    axes[1, 1].plot(
        result["time_s"],
        1000 * np.asarray(result["valves"]["V-1"]["volume_flow_m3_s"]),
        label="Through valve",
        color=colors["fast"],
    )
    axes[1, 1].plot(
        result["time_s"],
        1000 * np.asarray(result["pipes"]["downstream"]["end_flow_m3_s"]),
        label="At downstream pipe outlet",
        color="#dc2626",
    )
    bracket = fast["motion"]["command_records"][0]["completion_bracket_s"]
    axes[1, 1].axvspan(*bracket, color="#eab308", alpha=0.3, label="Target observation bracket")
    axes[1, 1].annotate(
        "10 L/s after the position target",
        xy=(bracket[1], 10),
        xytext=(1.2, 4.0),
        arrowprops={"arrowstyle": "->", "color": "#475569"},
        fontsize=9,
    )
    axes[1, 1].set(title="Position target and downstream discharge differ", ylabel="Signed flow (L/s)")
    for ax in axes.flat:
        ax.set_xlabel("Time (s)")
        ax.set_xlim(0, 4)
        ax.grid(alpha=0.18)
        ax.legend(loc="best", fontsize=8, frameon=False)
    fig.suptitle(
        "Finite valve motion coupled to liquid pressure waves\nSynthetic two-pipe reference: 1000 m each, D = 0.2 m, wave speed = 1000 m/s",
        fontsize=13,
    )
    destination = Path("docs/images/valve_transient_reference.png")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=160)
    plt.close(fig)
    metadata = {
        "schema": "pipeline_integrity.figure.v1",
        "figure": destination.as_posix(),
        "figure_sha256": sha(destination),
        "matplotlib_version": matplotlib.__version__,
        "script_sha256": sha(Path(__file__)),
        "source_sha256": {key: sha(root / f"{key}.json") for key in cases},
        "field_validation_claim": False,
    }
    Path("research/valve_figure.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"figure": destination.as_posix(), "sha256": metadata["figure_sha256"]}))


if __name__ == "__main__":
    main()
