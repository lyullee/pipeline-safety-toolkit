"""Plot authored material-engine references only, excluding source or conditioned rod observations."""

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    private = Path("artifacts/material_sections")
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), layout="constrained")
    paths = []
    pressure = private / "closed.json"
    paths.append(pressure)
    p = json.loads(pressure.read_text())
    radii = [r * 1000 for r in p["specification"]["evaluation_radii_m"]]
    for name, label in [
        ("hoop_stress_Pa", "Hoop"),
        ("axial_stress_Pa", "Axial"),
        ("radial_stress_Pa", "Radial"),
    ]:
        axes[0].plot(
            radii, [r["native"]["payload"]["values"][name] * 1e-6 for r in p["material_records"]], label=label
        )
    axes[0].set(
        xlabel="Radius (mm)", ylabel="Total stress (MPa)", title="Two pressure tractions; closed ends"
    )
    axes[0].legend(fontsize=8)
    for count, marker in [(10, "o"), (20, ".")]:
        path = private / f"solid_radial_{count}.json"
        paths.append(path)
        v = json.loads(path.read_text())["native"]["payload"]["values"]
        axes[1].plot(
            [r * 1000 for r in v["cell_centers_m"]],
            v["temperature_history_K"][-1],
            marker=marker,
            linewidth=1,
            label=f"Native radial, {count} cells",
        )
    samples = [0.05 + 0.02 * i / 100 for i in range(101)]
    axes[1].plot(
        [r * 1000 for r in samples],
        [300 + 40 * math.log(0.07 / r) / math.log(1.4) for r in samples],
        "--",
        color="black",
        linewidth=1,
        label="Steady logarithmic reference",
    )
    axes[1].set(
        xlabel="Radius (mm)", ylabel="Solid temperature (K)", title="Constant properties; steady reference"
    )
    axes[1].legend(fontsize=8)
    for name, label in [
        ("plane_free_uniform", "Free resultants"),
        ("plane_clamped_uniform", "Fixed strain and curvature"),
    ]:
        path = private / (name + ".json")
        paths.append(path)
        p = json.loads(path.read_text())["native"]["payload"]
        axes[2].plot(
            [x * 1000 for x in p["inputs"]["position_m"]],
            [s * 1e-6 for s in p["values"]["stress_Pa"]],
            marker="o",
            label=label,
        )
    axes[2].set(
        xlabel="Planar profile position (mm)",
        ylabel="Uniaxial stress (MPa)",
        title="Uniform +20 K; planar section",
    )
    axes[2].legend(fontsize=8)
    for axis in axes:
        axis.grid(alpha=0.2)
    fig.suptitle(
        "Owned material-engine interface references\nAuthored geometry, properties and loads; no experimental observations",
        fontsize=13,
    )
    destination = Path("docs/images/material_sections_reference.png")
    fig.savefig(destination, dpi=160)
    plt.close(fig)
    metadata = dict(
        schema="pipeline_integrity.material_sections_figure.v1",
        source_sha256=file_digest(Path("research/material_sections_results.json")),
        generator_sha256=file_digest(Path(__file__)),
        figure_sha256=file_digest(destination),
        authored_input_files_sha256={p.name: file_digest(p) for p in paths},
        experimental_observations_plotted=False,
        zero_heat_native_status_is_not_overridden=True,
    )
    Path("research/material_sections_figure.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
