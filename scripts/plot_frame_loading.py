"""Original thermal restraint references and native authored-case overlays, without observed rows."""

import json
import runpy
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/frame_loading_results.json")
    cases = json.loads(source.read_text(encoding="utf-8"))["authored_cases"]
    example = runpy.run_path("examples/frame_loading.py")
    frame, _ = example["reference_frame"]()
    alpha, e, area, length, k = 1e-5, frame.materials[0].young_modulus_pa, frame.sections[0].area_m2, 4.0, 1e8
    delta_t = np.linspace(-20, 50, 141)
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.2), constrained_layout=True)
    modes = (
        (
            "Free",
            "#206999",
            alpha * delta_t * length,
            np.zeros_like(delta_t),
            [("thermal_cooling", -20), ("thermal_zero_change", 0), ("thermal_free", 50)],
        ),
        ("Fixed", "#bc5636", np.zeros_like(delta_t), -e * alpha * delta_t, [("thermal_fixed", 50)]),
        (
            "Axial spring",
            "#409267",
            alpha * delta_t * length / (1 + k * length / (e * area)),
            -k / area * alpha * delta_t * length / (1 + k * length / (e * area)),
            [("thermal_spring", 50)],
        ),
    )
    for label, color, displacement, stress, samples in modes:
        axes[0].plot(delta_t, displacement * 1000, color=color, label=label, linewidth=2)
        axes[1].plot(delta_t, stress / 1e6, color=color, label=label, linewidth=2)
        for key, t in samples:
            row = cases[key]
            axes[0].scatter(
                t, row["axial_displacement_m"] * 1000, facecolors="white", edgecolors=color, s=45, zorder=3
            )
            axes[1].scatter(
                t, row["mean_axial_stress_pa"] / 1e6, facecolors="white", edgecolors=color, s=45, zorder=3
            )
    axes[0].set(
        xlabel="Temperature change from declared stress-free state [K]",
        ylabel="Axial end displacement [mm]",
        title="Actual support stiffness determines movement",
    )
    axes[1].set(
        xlabel="Temperature change from declared stress-free state [K]",
        ylabel="Recovered mean axial stress [MPa]",
        title="Tension positive; free expansion has zero stress",
    )
    for axis in axes:
        axis.grid(alpha=0.2)
        axis.legend(frameon=False)
    figure.suptitle("Independent linear references; circles: native authored cases", fontsize=11)
    target = Path("docs/images/frame_loading_reference.png")
    figure.savefig(target, dpi=160)
    plt.close(figure)
    metadata = dict(
        schema="pipeline_integrity.frame_loading_figure.v1",
        source_sha256=file_digest(source),
        generator_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/frame_loading.py")),
        figure_sha256=file_digest(target),
        experimental_observations_plotted=False,
        vendor_or_standard_tables_plotted=False,
    )
    Path("research/frame_loading_figure.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
