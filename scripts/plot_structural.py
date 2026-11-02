"""Plot authored native beam samples against independently evaluated elastic reference curves."""

import json
import runpy
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("research/structural_results.json")
    record = json.loads(source.read_text(encoding="utf-8"))
    example = runpy.run_path("examples/pipe_frame.py")
    request = example["make_case"]()
    ei = request.materials[0].young_modulus_pa * request.sections[0].iz_m4
    length = 4.0
    x = np.linspace(0, length, 161)
    panels = (
        (
            "cantilever_point",
            "Cantilever: tip force -1 kN",
            lambda s: -1000 * s**2 * (3 * length - s) / (6 * ei),
        ),
        (
            "simply_supported",
            "Simply supported: uniform load -100 N/m",
            lambda s: -100 * s * (length**3 - 2 * length * s**2 + s**3) / (24 * ei),
        ),
    )
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.1), constrained_layout=True)
    for axis, (case, title, reference) in zip(axes, panels, strict=True):
        samples = record["authored_cases"][case]["combinations"]["service"]["members"]["pipe"]["samples"]
        sx = np.array([row["x_m"] for row in samples])
        sy = np.array([row["local_translation_m"][1] for row in samples])
        assert np.allclose(sy, reference(sx), rtol=1e-10, atol=1e-12)
        axis.plot(x, reference(x) * 1000, label="Independent elastic reference", linewidth=2)
        axis.scatter(
            sx,
            sy * 1000,
            facecolors="white",
            edgecolors="#c25725",
            linewidth=1.6,
            s=45,
            label="PyNite 3.2.0 samples",
            zorder=3,
        )
        axis.set(
            xlabel="Distance from member start [m]", ylabel="Local transverse displacement [mm]", title=title
        )
        axis.grid(alpha=0.2)
        axis.legend(frameon=False, fontsize=8)
    figure.suptitle("Authored geometry and loads; elastic reference checks", fontsize=12)
    target = Path("docs/images/pipe_frame_reference.png")
    figure.savefig(target, dpi=160)
    plt.close(figure)
    summary = dict(
        schema="pipeline_integrity.structural_figure.v1",
        source_sha256=file_digest(source),
        generator_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/pipe_frame.py")),
        figure_sha256=file_digest(target),
        experimental_observations_plotted=False,
        vendor_or_standard_tables_plotted=False,
    )
    Path("research/structural_figure.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
