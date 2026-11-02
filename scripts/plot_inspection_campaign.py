"""Visualize the original hypothetical campaign without exporting source observations."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    summary_file = Path("research/inspection_planning_results.json")
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    reference_file = Path("artifacts/inspection_planning/reference.json")
    reference = json.loads(reference_file.read_text(encoding="utf-8"))
    cases = summary["cases"]
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    axes[0].plot(
        [0, 1, 2, 3],
        [
            cases[k]["unrounded_model_score"]
            for k in ["zero_budget", "one_budget", "reference", "three_budget"]
        ],
        marker="o",
        linewidth=2,
        color="#2269ab",
    )
    axes[0].set(
        xlabel="Declared budget units",
        ylabel="Expected unrevealed weight",
        title="Finite-menu budget comparison",
        xticks=[0, 1, 2, 3],
        ylim=(0, 3.3),
    )
    axes[0].grid(alpha=0.2)
    for i, row in enumerate(reference["selected"]):
        if row["mode_id"] == "not_selected":
            axes[1].text(0.15, i, "Not selected", va="center", color="#666666")
        else:
            start, finish = row["start_s"], row["finish_s"]
            axes[1].barh(i, finish - start, left=start, color="#008471", height=0.5)
            axes[1].text(
                (start + finish) / 2, i, f"{start:g}–{finish:g}", ha="center", va="center", color="white"
            )
    axes[1].set(
        yticks=range(len(reference["selected"])),
        yticklabels=[r["operation_id"] for r in reference["selected"]],
        xlabel="Hypothetical elapsed seconds",
        xlim=(0, 8),
        ylim=(-0.5, len(reference["selected"]) - 0.5),
        title="Reference: budget 2, one crew",
    )
    axes[1].invert_yaxis()
    figure.suptitle("Authored assumptions; no field calibration, repair or failure-risk claim", fontsize=11)
    target = Path("docs/images/inspection_campaign_reference.png")
    figure.savefig(target, dpi=150)
    plt.close(figure)
    record = {
        "schema": "pipeline_integrity.inspection_planning_figure.v1",
        "summary_sha256": file_digest(summary_file),
        "reference_sha256": file_digest(reference_file),
        "generator_sha256": file_digest(Path(__file__)),
        "figure_sha256": file_digest(target),
        "original_synthetic_case": True,
        "field_calibrated": False,
    }
    Path("research/inspection_planning_figure.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
