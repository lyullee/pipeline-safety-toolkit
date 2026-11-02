"""Plot only original synthetic fracture references; primary machine records remain excluded."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/fracture_exchange_results.json")
    r = json.loads(source.read_text())
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    names = ["zero_opening", "base", "above_resistance"]
    axes[0].bar(
        ["Zero load", "Reference", "Above resistance"],
        [r["mode_i"][n]["values"]["stress_intensity_Pa_sqrt_m"] * 1e-6 for n in names],
        color="#126782",
    )
    axes[0].axhline(5.0, linestyle="--", color="#9e302a", label="Declared resistance")
    axes[0].set(title="Scalar Mode-I calculation", ylabel="K (MPa sqrt(m))")
    axes[0].legend(fontsize=8)
    labels = ["zero_range", "threshold", "base", "calibration_limit", "tougher_load", "terminal"]
    for i, name in enumerate(labels):
        v = r["growth"][name]
        depth = v["values"]["final_depth_m"] * 1000
        color = "#8b542b" if v["status"] == "out_of_scope" else "#126782"
        axes[1].plot([1.0, depth], [i, i], linewidth=4, color=color)
        axes[1].plot(depth, i, "o", color=color)
        axes[1].annotate(
            f"{v['values']['cycles_evaluated']:.1f} cycles",
            (depth, i),
            xytext=(4, 5),
            textcoords="offset points",
            fontsize=8,
        )
    axes[1].set(
        yticks=range(len(labels)),
        yticklabels=[
            "Zero range",
            "Below threshold",
            "100-cycle block",
            "Calibration stop*",
            "Resistance stop",
            "Depth stop",
        ],
        xlabel="Final crack depth (mm)",
        title="Native ordered-cycle stops",
    )
    axes[1].set_xlim(0.8, 4.8)
    axes[1].text(
        0.02, -0.16, "* out_of_scope; partial trajectory is retained", transform=axes[1].transAxes, fontsize=8
    )
    axes[2].plot([0.0, 1.0, 2.0], [1.0, 0.8, 0.0], color="#126782", label="Authored boundary")
    for name, marker, color, label in [
        ("inside", "o", "#126782", "Inside supplied boundary"),
        ("above", "x", "#9e302a", "Above supplied boundary"),
        ("outside_support", "s", "#777777", "Outside curve support"),
    ]:
        p = r["declared_boundaries"][name]["values"]["points"][0]
        axes[2].scatter(
            [p["load_ratio_Lr"]], [p["toughness_ratio_Kr"]], marker=marker, color=color, label=label, zorder=5
        )
    axes[2].set(
        xlabel="Declared load ratio Lr",
        ylabel="Declared toughness ratio Kr",
        title="User boundary; no standard claim",
        xlim=(-0.1, 3.3),
        ylim=(-0.05, 1.15),
    )
    axes[2].legend(fontsize=8, loc="upper right")
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle(
        "Owned fracture-engine interface references\nAuthored geometry, coefficients and resistance; no primary observations",
        fontsize=13,
    )
    destination = Path("docs/images/fracture_exchange_reference.png")
    fig.savefig(destination, dpi=160)
    plt.close(fig)
    meta = dict(
        schema="pipeline_integrity.fracture_exchange_figure.v1",
        source_sha256=file_digest(source),
        generator_sha256=file_digest(Path(__file__)),
        figure_sha256=file_digest(destination),
        experimental_observations_plotted=False,
        curve_is_authored_and_not_a_standard=True,
        calibration_out_of_scope_preserved=True,
    )
    Path("research/fracture_exchange_figure.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
