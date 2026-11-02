"""Plot original normalized geometry references without publishing source experimental observations."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/tank_inventory_results.json")
    rows = json.loads(source.read_text(encoding="utf-8"))["authored_geometry_profiles"]
    figure, axis = plt.subplots(figsize=(7.4, 4.8), constrained_layout=True)
    for key, label in [
        ("flat_vertical", "Vertical cylinder"),
        ("flat_horizontal", "Horizontal cylinder"),
        ("conical_vertical", "Vertical cylinder with conical heads"),
    ]:
        profile = rows[key]
        axis.plot(
            [r["height_fraction"] for r in profile],
            [r["volume_fraction"] for r in profile],
            label=label,
            linewidth=2,
        )
    axis.set(
        xlabel="Level / internal maximum height",
        ylabel="Liquid volume / internal capacity",
        xlim=(0, 1),
        ylim=(0, 1),
        title="Level fraction and volume fraction depend on geometry",
    )
    axis.legend(frameon=False, loc="upper left")
    axis.grid(alpha=0.2)
    figure.suptitle("Authored dimensions; not a calibration of the released tank experiments", fontsize=10)
    target = Path("docs/images/tank_inventory_reference.png")
    figure.savefig(target, dpi=150)
    plt.close(figure)
    record = {
        "schema": "pipeline_integrity.tank_inventory_figure.v1",
        "source_sha256": file_digest(source),
        "generator_sha256": file_digest(Path(__file__)),
        "figure_sha256": file_digest(target),
        "original_geometry_inputs": True,
        "experimental_observations_plotted": False,
    }
    Path("research/tank_inventory_figure.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
