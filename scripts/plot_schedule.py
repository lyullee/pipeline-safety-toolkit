"""Plot the authored planning reference and its audited half-open resource occupancy."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pipeline_integrity.datasets import file_digest


def main():
    source = Path("artifacts/scheduling/reference.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    spec, result = document["specification"], document["result"]
    assert result["status"] == "optimal" and result["audit"]["valid"]
    rows = {r["operation_id"]: r for r in result["timeline"]}
    modes = {
        j["id"]: next(m for m in j["modes"] if m["id"] == rows[j["id"]]["mode_id"])
        for j in spec["operations"]
    }
    times = np.arange(0.0, spec["horizon_s"] + 1.0)
    holds = []
    for hold in spec["holds"]:
        start = rows[hold["from_operation"]]["start_s" if hold["from_event"] == "start" else "finish_s"]
        finish = rows[hold["to_operation"]]["start_s" if hold["to_event"] == "start" else "finish_s"]
        holds.append((start, finish, hold["resources"]))
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 8),
        sharex=True,
        gridspec_kw={"height_ratios": (2.5, 1, 1)},
        constrained_layout=True,
    )
    colors = {
        "isolate": "#3264ca",
        "verify": "#008471",
        "inspect": "#008471",
        "restore": "#3264ca",
        "other_use": "#68758c",
    }
    for i, job in enumerate(spec["operations"]):
        row = rows[job["id"]]
        a, b = row["start_s"] / 60, row["finish_s"] / 60
        axes[0].barh(i, b - a, left=a, height=0.6, color=colors[job["id"]])
        axes[0].text((a + b) / 2, i, f"{a:g}–{b:g}", ha="center", va="center", color="white", fontsize=10)
    axes[0].set_yticks(
        range(len(spec["operations"])), [j["id"].replace("_", " ") for j in spec["operations"]]
    )
    axes[0].invert_yaxis()
    for a, b, _ in holds:
        axes[0].axvspan(a / 60, b / 60, color="#e3a32b", alpha=0.12, label="Declared asset hold")
    axes[0].legend(loc="upper right", frameon=False)
    axes[0].set_title(
        "Declared operation plan: objective 8, makespan 8 min\nHypothetical durations and capacities; work authorization is not calculated",
        fontsize=13,
    )
    for ax, name in zip(axes[1:], ("operator", "asset"), strict=True):
        resource = next(r for r in spec["resources"] if r["id"] == name)
        capacity = np.full(len(times), resource["capacity"], dtype=float)
        demand = np.zeros(len(times))
        for window in resource["calendar"]:
            capacity[(times >= window["start_s"]) & (times < window["finish_s"])] = window["capacity"]
        for id, row in rows.items():
            demand[(times >= row["start_s"]) & (times < row["finish_s"])] += modes[id]["resources"].get(
                name, 0
            )
        for a, b, resources in holds:
            demand[(times >= a) & (times < b)] += resources.get(name, 0)
        ax.step(
            times / 60, capacity, where="post", color="#777777", linestyle="--", label="Available capacity"
        )
        ax.step(
            times / 60,
            demand,
            where="post",
            color="#b97800" if name == "asset" else "#3264ca",
            label="Planned demand",
        )
        ax.set_ylim(-0.05, 1.35)
        ax.set_yticks((0, 1))
        ax.set_ylabel(name)
        ax.legend(loc="upper right", frameon=False, ncols=2, fontsize=9)
    for ax in axes:
        ax.grid(axis="x", alpha=0.2)
        ax.set_xlim(0, 10)
        ax.set_xticks(range(11))
    axes[-1].set_xlabel("Elapsed planning time (min); resource intervals use [start, finish)")
    target = Path("docs/images/operations_schedule_reference.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=160)
    plt.close(figure)
    metadata = dict(
        schema="pipeline_integrity.schedule_figure.v1",
        generator_sha256=file_digest(Path(__file__)),
        source_case_sha256=file_digest(source),
        figure_sha256=file_digest(target),
        matplotlib=matplotlib.__version__,
        original_synthetic_case=True,
        field_schedule_validation=False,
    )
    Path("research/scheduling_figure.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
