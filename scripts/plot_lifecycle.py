"""Plot only original REF-currency/carbons scenarios; no measured power rows or market quotes."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/lifecycle_results.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    sensitivity = data["authored_discount_sensitivity"]
    reference = data["authored_cases"]["declared_reference"]["alternatives"]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.2), constrained_layout=True)
    colors = dict(keep="#287496", replace="#b85737")
    for name, color in colors.items():
        axes[0].plot(
            [row["annual_discount_rate"] * 100 for row in sensitivity],
            [row["present_cost"][name] for row in sensitivity],
            label=name.title(),
            color=color,
            linewidth=2,
        )
        row = reference[name]
        axes[1].scatter(row["present_cost"], row["emissions_kg_co2e"], s=90, color=color)
        axes[1].annotate(
            name.title(),
            (row["present_cost"], row["emissions_kg_co2e"]),
            xytext=(7, 7),
            textcoords="offset points",
        )
    axes[0].set(
        xlabel="Declared real annual discount rate [%]",
        ylabel="Present cost [REF]",
        title="Cost preference changes with declared inputs",
    )
    axes[0].legend(frameon=False)
    axes[1].set(
        xlabel="Present cost at 5% [REF]",
        ylabel="Undiscounted inventory [kg CO2e]",
        title="Cost and emission inventories stay separate",
    )
    axes[1].margins(x=0.3, y=0.3)
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.suptitle("Authored five-year service, costs and emissions; fictional REF currency", fontsize=11)
    target = Path("docs/images/lifecycle_reference.png")
    figure.savefig(target, dpi=160)
    plt.close(figure)
    metadata = dict(
        schema="pipeline_integrity.lifecycle_figure.v1",
        source_sha256=file_digest(source),
        generator_sha256=file_digest(Path(__file__)),
        figure_sha256=file_digest(target),
        observed_power_samples_plotted=False,
        market_prices_or_discount_rates_recommended=False,
    )
    Path("research/lifecycle_figure.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
