"""Compare coupled owner/frame calculations with independent straight-cylinder and restraint references."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.adapters import hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, Pressure, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.frame_loading import (
    prepare_pressure_frame,
    prepare_uniform_thermal_frame,
    solve_loaded_frame,
)
from pipeline_integrity.material_exchange import evaluate_pressure_section, thermal_rod_frames
from pipeline_integrity.structural import FrameCombination, FrameSupport, frame_signal_frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/thermal_rod"))
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in ("materials", "monitoring", "hra")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((path / "src").resolve()) for path in roots.values()]
    example = runpy.run_path("examples/frame_loading.py")
    base, context = example["reference_frame"]()
    thermal = example["temperature"]()
    pressure = example["pressure_section"](base)
    private = Path("artifacts/frame_loading")
    private.mkdir(parents=True, exist_ok=True)
    cases = {}
    fixed = base.model_copy(
        update={"supports": base.supports + (base.supports[0].model_copy(update={"node_id": "tip"}),)}
    )
    spring = base.model_copy(
        update={
            "supports": base.supports
            + (FrameSupport(node_id="tip", translation_springs_n_m={"DX": 1e8}, evidence_id="reference"),)
        }
    )

    def run(name, frame=base, temperature=None, cylinder=None, restraint="free", spring_k=0.0):
        prepared = []
        if temperature is not None:
            prepared.append(prepare_uniform_thermal_frame(frame, temperature, context))
        if cylinder is not None:
            section = evaluate_pressure_section(cylinder, context)
            prepared.append(
                prepare_pressure_frame(frame, section, example["pressure_mapping"](section), context)
            )
        result = solve_loaded_frame(frame, tuple(prepared), context)
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
        (private / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        row = dict(status=result["status"], issues=result["issues"], result_digest=result["record_digest"])
        if result["status"] == "computed":
            combo = frame.combinations[0]
            out = result["combinations"][combo.id]
            e, nu, area = (
                frame.materials[0].young_modulus_pa,
                frame.materials[0].poisson_ratio,
                frame.sections[0].area_m2,
            )
            delta = np.array(frame.nodes[1].position_m) - np.array(frame.nodes[0].position_m)
            length, axis = float(np.linalg.norm(delta)), delta / np.linalg.norm(delta)
            stress_constant = cap = poisson = thermal_strain = 0.0
            if cylinder is not None:
                pi, pe = cylinder.internal_pressure.absolute_pa(), cylinder.external_pressure.absolute_pa()
                a, b = cylinder.inner_radius_m, cylinder.outer_radius_m
                stress_constant = (pi * a**2 - pe * b**2) / (b**2 - a**2) * combo.factors.get("pressure", 0.0)
                cap = stress_constant * area if cylinder.pressure_loaded_closed_ends else 0.0
                poisson = -2 * nu * stress_constant / e
            if temperature is not None:
                thermal_strain = (
                    temperature.expansion_coefficient_per_k
                    * (temperature.temperature_k - temperature.stress_free_temperature_k)
                    * combo.factors.get("thermal", 0.0)
                )
            initial = poisson + thermal_strain
            displacement = (
                0.0
                if restraint == "fixed"
                else length * (initial + cap / (e * area)) / (1 + spring_k * length / (e * area))
            )
            force = e * area * (displacement / length - initial)
            actual_displacement = float(np.dot(np.array(out["nodes"]["tip"]["translation_m"]), axis))
            actual_stress = out["members"]["pipe"]["samples"][0]["mean_elastic_axial_stress_pa"]
            assert math.isclose(actual_displacement, displacement, rel_tol=1e-10, abs_tol=1e-12), name
            assert math.isclose(actual_stress, force / area, rel_tol=1e-10, abs_tol=1e-5), name
            assert out["static_balance"]["passed"] and out["nodal_balance"]["passed"]
            row.update(
                axial_displacement_m=actual_displacement,
                mean_axial_stress_pa=actual_stress,
                independent_displacement_m=displacement,
                independent_mean_stress_pa=force / area,
                displacement_error_m=abs(actual_displacement - displacement),
                stress_error_pa=abs(actual_stress - force / area),
                native_uncorrected_axial_n=out["members"]["pipe"]["samples"][0]["axial_n"],
                recovered_tension_positive_axial_n=out["members"]["pipe"]["samples"][0][
                    "elastic_axial_force_tension_positive_n"
                ],
                anchor_reaction_n=out["nodes"]["anchor"]["reaction_force_n"],
                static_and_nodal_balance_passed=True,
            )
        cases[name] = row
        return result

    run("thermal_free", temperature=thermal)
    run("thermal_fixed", frame=fixed, temperature=thermal, restraint="fixed")
    run("thermal_spring", frame=spring, temperature=thermal, restraint="spring", spring_k=1e8)
    run(
        "thermal_cooling",
        temperature=thermal.model_copy(
            update={"temperature_k": 273.15, "coefficient_temperature_support_k": (250.0, 400.0)}
        ),
    )
    run(
        "thermal_zero_change",
        temperature=thermal.model_copy(update={"temperature_k": thermal.stress_free_temperature_k}),
    )
    run(
        "thermal_zero_coefficient",
        temperature=thermal.model_copy(update={"expansion_coefficient_per_k": 0.0}),
    )
    run("pressure_closed_free", cylinder=pressure)
    run("pressure_closed_fixed", frame=fixed, cylinder=pressure, restraint="fixed")
    opened = pressure.model_copy(update={"pressure_loaded_closed_ends": False})
    run("pressure_open_free", cylinder=opened)
    run("pressure_open_fixed", frame=fixed, cylinder=opened, restraint="fixed")
    run(
        "pressure_external_dominant",
        cylinder=pressure.model_copy(
            update={
                "internal_pressure": Pressure(value=1e5, unit="Pa", reference="absolute"),
                "external_pressure": Pressure(value=2e6, unit="Pa", reference="absolute"),
            }
        ),
    )
    run(
        "pressure_hydrostatic",
        cylinder=pressure.model_copy(update={"internal_pressure": pressure.external_pressure}),
    )
    run("combined_free", temperature=thermal, cylinder=pressure)
    run("combined_fixed", frame=fixed, temperature=thermal, cylinder=pressure, restraint="fixed")
    run(
        "combined_spring",
        frame=spring,
        temperature=thermal,
        cylinder=pressure,
        restraint="spring",
        spring_k=1e8,
    )
    combination = FrameCombination(
        id="factored", factors={"thermal": 0.5, "pressure": 2.0}, evidence_id="reference"
    )
    run(
        "combined_factored",
        frame=base.model_copy(update={"combinations": (combination,)}),
        temperature=thermal,
        cylinder=pressure,
    )
    rotated_nodes = (base.nodes[0], base.nodes[1].model_copy(update={"position_m": (0.0, 2.4, 3.2)}))
    run(
        "combined_rotated",
        frame=base.model_copy(update={"nodes": rotated_nodes}),
        temperature=thermal,
        cylinder=pressure,
    )
    assert (
        run("missing_temperature", temperature=thermal.model_copy(update={"temperature_k": None}))["status"]
        == "needs_data"
    )
    assert (
        run("outside_coefficient_support", temperature=thermal.model_copy(update={"temperature_k": 500.0}))[
            "status"
        ]
        == "out_of_scope"
    )
    assert (
        run("missing_surface_pressure", cylinder=pressure.model_copy(update={"internal_pressure": None}))[
            "status"
        ]
        == "needs_data"
    )

    archive = args.raw_root / "sbcf36976g-1.zip"
    source, metadata = thermal_rod_frames(archive, "50cu3")
    selected = source[:16]
    source_evidence = EvidenceRef(
        id="thermal-rod",
        citation="Tomanek and Stutts (2021), Mendeley V1, DOI 10.17632/sbcf36976g.1",
        locator="https://data.mendeley.com/datasets/sbcf36976g/1",
        sha256=file_digest(archive),
        origin="laboratory",
        license_id="CC-BY-4.0",
        limitations=(
            "Source temperature excess is mapped only under a declared ambient/stress-free and uniform-frame hypothesis; original rod structural response is not qualified.",
        ),
    )
    source_context = context.model_copy(
        update={
            "clock_id": source[0].clock_id,
            "origin": "laboratory",
            "evidence": context.evidence + (source_evidence,),
            "metadata": {
                "source_temperature_basis": metadata["temperature_basis"],
                "hypothetical_ambient_and_stress_free_temperature_k": 293.15,
                "hypothetical_uniform_target_member": "pipe",
            },
        }
    )
    frames, errors = [], []
    for index, observation in enumerate(selected):
        assert (
            observation.valid["tc1_excess_temperature_K"]
            and observation.units["tc1_excess_temperature_K"] == "K"
        )
        snapshot = fixed.model_copy(
            update={"clock_id": source_context.clock_id, "time_s": observation.time_s}
        )
        change = thermal.model_copy(
            update={
                "temperature_k": 293.15 + observation.values["tc1_excess_temperature_K"],
                "evidence_ids": ("reference", "thermal-rod"),
            }
        )
        prepared = prepare_uniform_thermal_frame(snapshot, change, source_context)
        result = solve_loaded_frame(snapshot, (prepared,), source_context)
        assert result["status"] == "computed"
        result.pop("record_digest")
        result["source_temperature_observation"] = observation.model_dump(mode="json")
        result["record_digest"] = digest(result)
        output = frame_signal_frame(result, "combined", source_context)
        expected = (
            snapshot.materials[0].young_modulus_pa
            * snapshot.sections[0].area_m2
            * change.expansion_coefficient_per_k
            * observation.values["tc1_excess_temperature_K"]
        )
        errors.append(abs(output.values["anchor.fx"] - expected))
        assert math.isclose(output.values["anchor.fx"], expected, rel_tol=1e-10, abs_tol=1e-6)
        assert output.time_s == observation.time_s and output.origin == "simulation"
        frames.append(output)
        (private / f"temperature_snapshot_{index:03d}.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    batch = monitoring_batch(frames)
    series = hra_signal_series(frames, "anchor.fx")
    assert np.array_equal(batch.time_s, [f.time_s for f in selected])
    assert tuple(series.time_s) == tuple(f.time_s for f in selected)
    assert batch.origin == series.origin == "simulation"
    assert batch.metadata["clock_id"] == series.clock_id == source_context.clock_id
    assert before == {key: revision(path) for key, path in roots.items()}
    summary = dict(
        schema="pipeline_integrity.frame_loading_verification.v1",
        authored_cases=cases,
        primary_temperature_exchange=dict(
            source="https://data.mendeley.com/datasets/sbcf36976g/1",
            creators=["Lauren B. Tomanek", "Daniel S. Stutts"],
            license_id="CC-BY-4.0",
            archive_sha256=file_digest(archive),
            source_rows=len(source),
            consecutive_source_rows_used=len(selected),
            clock_id=source_context.clock_id,
            source_temperature_basis=metadata["temperature_basis"],
            declared_ambient_and_stress_free_temperature_k=293.15,
            original_time_and_temperature_excess_retained=True,
            maximum_hypothetical_fixed_reaction_error_n=max(errors),
            monitoring_rows=len(frames),
            monitoring_channels=len(batch.channels),
            hra_samples=len(series.time_s),
            source_origin="laboratory",
            predicted_origin="simulation",
            actual_rod_geometry_temperature_uniformity_and_structural_response_qualified=False,
        ),
        source_versions=before,
        structural_module_sha256=file_digest(Path("src/pipeline_integrity/structural.py")),
        loading_module_sha256=file_digest(Path("src/pipeline_integrity/frame_loading.py")),
        example_sha256=file_digest(Path("examples/frame_loading.py")),
        script_sha256=file_digest(Path(__file__)),
        code_or_field_acceptance=False,
        buckling_or_failure_probability_calculated=False,
    )
    Path("research/frame_loading_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                authored_cases=len(cases),
                computed=sum(row["status"] == "computed" for row in cases.values()),
                source_temperature_snapshots=len(selected),
                maximum_source_reference_reaction_error_n=max(errors),
            )
        )
    )


if __name__ == "__main__":
    main()
