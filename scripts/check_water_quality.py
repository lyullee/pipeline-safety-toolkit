"""Validate owner-computed transport references and apply declared quality scenarios to an open network."""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.datasets import catalog
from pipeline_integrity.water import WaterRun
from pipeline_integrity.water_quality import (
    WaterQualityRun,
    WaterQualitySource,
    quality_threshold_arrival,
    simulate_water_quality,
    water_quality_batch,
)


def specification(mode, *, network_reference=False, decay=0.0, quality_step=1.0):
    return WaterQualityRun(
        hydraulics=WaterRun(
            duration_s=86400.0 if network_reference else 1000.0,
            hydraulic_timestep_s=300.0 if network_reference else 60.0,
            report_timestep_s=300.0 if network_reference else 5.0,
        ),
        mode=mode,
        parameter_source_id="synthetic_quality_on_open_topology"
        if network_reference
        else "authored_transport_reference",
        quality_timestep_s=60.0 if network_reference else quality_step,
        quality_tolerance=0.01 if mode == "age" else 1e-8,
        initial_default_value=0.0,
        species_id="declared_passive_marker" if mode == "concentration" else None,
        trace_node_id=("R1" if network_reference else "R") if mode == "source_fraction" else None,
        sources=(
            WaterQualitySource(
                source_id="declared_inlet",
                node_id="R1" if network_reference else "R",
                source_type="CONCEN",
                strength=0.001,
                unit="kg/m3",
            ),
        )
        if mode == "concentration"
        else (),
        bulk_decay_per_s=decay,
    )


def summary(result):
    values = np.array([v for rows in result["node_quality"].values() for v in rows], dtype=float)
    return {
        "status": result["status"],
        "source_sha256": result["source_sha256"],
        "result_digest": result["result_digest"],
        "implementation_sha256": result["implementation_sha256"],
        "implementation_files_sha256": result["implementation_files_sha256"],
        "context": result["context"],
        "specification": result["specification"],
        "mode": result["mode"],
        "quantity_unit": result["quantity_unit"],
        "quantity_meaning": result["quantity_meaning"],
        "report_count": len(result["time_s"]),
        "context_time_range_s": [result["time_s"][0], result["time_s"][-1]],
        "node_quality_range": [float(np.min(values)), float(np.max(values))],
        "checks": result["checks"],
        "native_diagnostics": result["native_diagnostics"],
        "balance_quantity_meaning": result["balance_quantity_meaning"],
        "backend": result["backend"],
        "pattern_clock": result["pattern_clock"],
        "topology": result["hydraulics"]["topology"],
        "hydraulic_status": result["hydraulics"]["status"],
        "maximum_junction_continuity_residual_m3_s": result["hydraulics"][
            "maximum_junction_continuity_residual_m3_s"
        ],
        "field_validation_claim": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", type=Path, default=Path("data/raw/battledim/L-TOWN.inp"))
    parser.add_argument("--monitoring", type=Path)
    args = parser.parse_args()
    folder = Path("artifacts/water_quality")
    folder.mkdir(parents=True, exist_ok=True)
    reference_context = RunContext(
        asset_id="authored_water_line",
        scenario_id="reference",
        clock_id="reference_elapsed",
        origin="synthetic",
    )
    reference, full_reference = {}, {}
    for key, mode, decay, step in (
        ("passive", "concentration", 0.0, 1.0),
        ("decay", "concentration", 0.001, 1.0),
        ("decay_coarser", "concentration", 0.001, 2.0),
        ("age", "age", 0.0, 1.0),
        ("source_fraction", "source_fraction", 0.0, 1.0),
    ):
        result = simulate_water_quality(
            "examples/water_transport.inp",
            specification(mode, decay=decay, quality_step=step),
            reference_context,
        )
        assert result["status"] == "computed", result["checks"]
        full_reference[key] = result
        reference[key] = summary(result)
        reference[key]["endpoint_final_value"] = result["node_quality"]["J"][-1]
        (folder / f"reference_{key}.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    travel = math.pi * 0.2**2 / 4 * 100 / 0.01
    native_travel = travel * 28.317 / (1000 * 0.3048**3)
    arrival = quality_threshold_arrival(full_reference["passive"], "J", 0.0005)
    assert arrival["sample_bracket_s"][0] <= travel <= arrival["sample_bracket_s"][1]
    age = full_reference["age"]
    age_profile_error = float(
        np.max(np.abs(np.array(age["node_quality"]["J"]) - np.minimum(age["elapsed_time_s"], native_travel)))
    )
    assert age_profile_error <= 2e-6 * native_travel
    decay_expected = 0.001 * math.exp(-0.001 * travel)
    for key, step in (("decay", 1.0), ("decay_coarser", 2.0)):
        assert (
            decay_expected * math.exp(-0.001 * step)
            <= reference[key]["endpoint_final_value"]
            <= decay_expected * math.exp(0.001 * step)
        )
    reference_checks = {
        "ideal_volume_over_flow_s": travel,
        "epanet_rounded_unit_reference_s": native_travel,
        "epanet_unit_reference_basis": "EPANET_2_2_LPSperCFS_28_317_and_MperFT_0_3048",
        "passive_half_inlet_crossing": arrival,
        "maximum_age_profile_reference_error_s": age_profile_error,
        "age_reference_relative_tolerance": 2e-6,
        "decay_ideal_SI_concentration_kg_m3": decay_expected,
        "decay_timestep_final_difference_kg_m3": abs(
            reference["decay"]["endpoint_final_value"] - reference["decay_coarser"]["endpoint_final_value"]
        ),
    }
    dataset = next(row for row in catalog() if row.id == "battledim")
    context = RunContext(
        asset_id="nominal_L_TOWN",
        scenario_id="declared_quality_boundary",
        clock_id="model_elapsed",
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="open_nominal_topology",
                citation=dataset.citation,
                origin="simulation",
                license_id=dataset.license_id,
            ),
            EvidenceRef(
                id="declared_quality",
                citation="Independently declared initial and boundary quality scenario",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    data, monitoring = {}, None
    if args.monitoring is not None:
        sys.path.insert(0, str((args.monitoring / "src").resolve()))
        native_result = full_reference["source_fraction"]
        batch = water_quality_batch(native_result, node_ids=("R", "J"))
        assert batch.values.shape == (len(native_result["time_s"]), 2)
        assert batch.metadata["source_result_digest"] == native_result["result_digest"]
        assert batch.origin == "simulation"
        monitoring = {
            "case": "independently_authored_source_fraction_reference",
            "owner_source": revision(args.monitoring),
            "native_type": f"{type(batch).__module__}.{type(batch).__qualname__}",
            "shape": list(batch.values.shape),
            "units": list(batch.units),
            "metadata": batch.metadata,
            "origin": batch.origin,
            "all_valid": bool(batch.valid.all()),
            "detector_accuracy_or_HEP_computed": False,
        }
    for mode in ("concentration", "age", "source_fraction"):
        result = simulate_water_quality(args.network, specification(mode, network_reference=True), context)
        data[mode] = summary(result)
        (folder / f"nominal_{mode}.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        assert all(result["checks"].values()), result["checks"]
        if result["status"] == "withheld":
            assert result["native_diagnostics"]["warning_count"] > 0
            try:
                water_quality_batch(result, factory=lambda **kwargs: kwargs)
            except ValueError:
                data[mode]["withheld_monitoring_handoff_blocked"] = True
            else:
                raise AssertionError("a withheld network run must not become an approved monitoring input")
        print(
            json.dumps(
                {
                    "mode": mode,
                    "status": result["status"],
                    "balance_ratio": result["native_diagnostics"]["mass_balance_ratio"],
                }
            ),
            flush=True,
        )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True, layout="constrained")
    for key, label in (("passive", "Conservative marker"), ("decay", "First-order decay")):
        r = full_reference[key]
        axes[0].plot(r["elapsed_time_s"], np.array(r["node_quality"]["J"]) / 0.001, label=label)
    axes[0].axvline(travel, color="0.4", linestyle="--", label="Ideal volume / flow")
    axes[0].set_ylabel("Outlet / inlet concentration")
    axes[0].legend(loc="lower right")
    axes[1].plot(age["elapsed_time_s"], age["node_quality"]["J"], label="EPANET water age")
    axes[1].plot(
        age["elapsed_time_s"],
        np.minimum(age["elapsed_time_s"], native_travel),
        "--",
        label="Plug-flow reference",
    )
    axes[1].set(xlabel="Elapsed time (s)", ylabel="Water age (s)")
    axes[1].legend(loc="lower right")
    for ax in axes:
        ax.grid(alpha=0.25)
    fig.suptitle("Authored single-pipe transport reference")
    figure = Path("docs/images/water_transport_reference.png")
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure, dpi=180)
    plt.close(fig)
    record = {
        "schema": "pipeline_integrity.water_quality_checks.v1",
        "reference_cases": reference,
        "reference_checks": reference_checks,
        "open_network_scenarios": data,
        "source": {
            "citation": dataset.citation,
            "license_id": dataset.license_id,
            "hydraulic_topology_origin": "simulation",
            "quality_initial_and_boundary_origin": "synthetic",
        },
        "monitoring_handoff": monitoring,
        "open_network_warning_not_waived": True,
        "independent_observation_validation": False,
        "raw_or_transformed_external_rows_in_public_summary": False,
        "source_engines_modified": False,
        "figure_source": "independently_authored_reference_cases",
    }
    Path("research/water_quality_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "reference_passed": True,
                "public_summary": "research/water_quality_results.json",
                "monitoring_checked": monitoring is not None,
            }
        )
    )


if __name__ == "__main__":
    main()
