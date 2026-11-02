"""Plot aggregate holdout diagnostics only; no individual source or derived cycle rows."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/uncertainty_fit_results.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    c = data["transient_cohort"]["candidates"]
    entries = [c["lognormal"]["heldout"]["empirical_pairs"]]
    labels = ["Paired\nempirical"]
    for family in ("normal", "lognormal"):
        for mode, label in (
            ("normal_score_copula", "copula"),
            ("independent_counterfactual", "independent*"),
        ):
            entries.append(c[family]["heldout"][mode])
            labels.append(f"{family.title()}\n{label}")
    x = np.arange(len(entries))
    figure, axes = plt.subplots(1, 2, figsize=(12.6, 5.2))
    figure.subplots_adjust(bottom=0.25, top=0.86, wspace=0.28)
    axes[0].bar(
        x,
        [e["scores"]["full_holdout_mean_energy_score"] for e in entries],
        color=["#777777", "#287496", "#78a9b6", "#b85737", "#d99b76"],
    )
    axes[0].set_ylabel("Scaled joint energy score (lower is better)")
    axes[0].set_title("Distribution score alone does not qualify transfer")
    axes[0].set_ylim(0, 0.012)
    for offset, key, color, label in (
        (-0.17, "energy_j", "#287496", "Energy"),
        (0.17, "volume_m3", "#b85737", "Volume"),
    ):
        values = [e["marginal_intervals"][key]["conditional_on_valid_coverage"] * 100 for e in entries]
        axes[1].bar(x + offset, values, width=0.32, color=color, label=label)
    axes[1].axhline(90, color="#777777", linestyle=":", label="Nominal 90% interval")
    axes[1].axhline(80, color="black", linestyle="--", linewidth=1, label="Authored 80% gate")
    axes[1].set_ylabel("Holdout marginal coverage (%)")
    axes[1].set_ylim(0, 110)
    axes[1].set_title("Empirical intervals miss the later energy values")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=2, fontsize=8)
    for axis in axes:
        axis.set_xticks(x, labels, fontsize=8)
        axis.grid(axis="y", alpha=0.2)
        axis.set_axisbelow(True)
    figure.suptitle("UCI hydraulic rig: source-order distribution diagnostics", fontsize=14)
    figure.text(
        0.01,
        0.025,
        "128 training / 18 omitted / 64 holdout rows in one source block (static state not confirmed).\n* Independence is a declared counterfactual. No IID confidence interval or field qualification.",
        fontsize=9,
    )
    output = Path("docs/images/uncertainty_fit_reference.png")
    figure.savefig(output, dpi=160, facecolor="white")
    plt.close(figure)
    metadata = dict(
        schema="pipeline_integrity.uncertainty_fit_figure.v1",
        source=source.as_posix(),
        source_sha256=file_digest(source),
        figure=output.as_posix(),
        figure_sha256=file_digest(output),
        script_sha256=file_digest(Path(__file__)),
        displayed_values="aggregate candidate scores and marginal coverage only",
        original_or_derived_cycle_rows_plotted=False,
        caption="Dependent source-order diagnostic evaluation; independence counterfactual and authored criteria, not field qualification.",
    )
    Path("research/uncertainty_fit_figure.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
