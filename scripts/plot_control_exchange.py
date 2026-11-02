"""Plot authored native-engine references only; laboratory observations stay outside the release."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline_integrity.datasets import file_digest


def main():
    root = Path("artifacts/control_exchange")
    inputs = {}
    fig, axes = plt.subplots(3, 1, figsize=(10, 10), constrained_layout=True)
    for axis, quantity, factor, unit in zip(
        axes[:2],
        ("pressure", "temperature"),
        (1e-3, 1.0),
        ("Pressure (kPa absolute)", "Temperature (K)"),
        strict=True,
    ):
        for label, style, color in (
            ("base", "-", "#126782"),
            ("refined", "--", "#d17a22"),
            ("data_gap", ":", "#753b86"),
        ):
            name = quantity + "_" + label + ".json"
            path = root / name
            rows = json.loads(path.read_text())
            inputs[name] = file_digest(path)
            axis.plot(
                [r["time_s"] for r in rows],
                [factor * r["process_value"] for r in rows],
                style,
                color=color,
                label={"base": "0.50 s scan", "refined": "0.25 s scan", "data_gap": "2 s data gap"}[label],
                linewidth=1.8,
            )
        target = rows[0]["decision"]["setpoint"] * factor
        axis.axhline(target, color="#39424e", linestyle="--", linewidth=1, label="Declared target")
        axis.set_ylabel(unit)
        axis.set_xlabel("Scenario time (s)")
        axis.grid(alpha=0.2)
        axis.legend(loc="lower right", ncols=2, fontsize=8)
    path = root / "valve_flow.json"
    rows = json.loads(path.read_text())
    inputs[path.name] = file_digest(path)
    axes[2].plot(
        [r["time_s"] for r in rows],
        [1e6 * r["measured_mass_flow_kg_s"] for r in rows],
        color="#126782",
        linewidth=1.8,
        label="Actual valve-flow calculation",
    )
    axes[2].axhline(30.0, color="#39424e", linestyle="--", linewidth=1, label="Declared target")
    axes[2].set_ylabel("Hydrogen flow (mg/s)")
    axes[2].set_xlabel("Scenario time (s)")
    axes[2].grid(alpha=0.2)
    axes[2].legend(loc="lower right", fontsize=8)
    axes[0].set_title(
        "Pressure and temperature: native tank mass/energy integration", loc="left", fontsize=11
    )
    axes[1].set_title(
        "Temperature retains about +0.92 K error at 120 s; tracking is unqualified", loc="left", fontsize=10
    )
    axes[2].set_title("Mass-flow loop: native valve with 3 s full-stroke actuation", loc="left", fontsize=11)
    fig.suptitle(
        "Feedback reference cases\nAuthored boundaries and fixed gains; no experimental observations",
        fontsize=13,
    )
    destination = Path("docs/images/control_exchange_reference.png")
    fig.savefig(destination, dpi=160)
    plt.close(fig)
    metadata = dict(
        schema="pipeline_integrity.control_exchange_figure.v1",
        source_sha256=file_digest(Path("research/control_exchange_results.json")),
        generator_sha256=file_digest(Path(__file__)),
        figure_sha256=file_digest(destination),
        authored_reference_files_sha256=inputs,
        experimental_observations_plotted=False,
        performance_qualification_claim=False,
    )
    Path("research/control_exchange_figure.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
