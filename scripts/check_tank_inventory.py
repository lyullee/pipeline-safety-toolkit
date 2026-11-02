"""Verify static geometry/inventory and apply clearly separated hypotheses to source tank experiments."""

import argparse
import csv
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.adapters import monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.tank_data import (
    CoupledTankReadRequest,
    coupled_tank_frames,
    review_coupled_tank_source,
)
from pipeline_integrity.tanks import (
    AxisymmetricTank,
    CalibratedTankCurve,
    PropertyTankDensity,
    TankHead,
    TankInventoryRequest,
    TankObservation,
    evaluate_tank_inventory,
    lh2dt_tank_geometry,
)
from pipeline_integrity.thermo import StateRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/coupled_tank"))
    args = parser.parse_args()
    before = {"lh2dt": revision(args.lh2dt), "monitoring": revision(args.monitoring)}
    sys.path[:0] = [str((p / "src").resolve()) for p in (args.lh2dt, args.monitoring)]
    example = runpy.run_path("examples/tank_inventory.py")
    original, context = example["make_case"]()
    root = Path("artifacts/tank_inventory")
    root.mkdir(parents=True, exist_ok=True)
    cases = {}

    def run(key, request=original):
        result = evaluate_tank_inventory(request, context)
        assert result["result_digest"] == digest({k: v for k, v in result.items() if k != "result_digest"})
        path = root / f"{key}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        cases[key] = {
            "status": result["status"],
            "mass_status": result["mass_status"],
            "values": result["values"],
            "issues": result["issues"],
            "record_sha256": file_digest(path),
        }
        return result

    assert run("water_air_example")["mass_status"] == "computed"
    profiles = {}
    for horizontal in (False, True):
        for kind in ("flat", "conical", "ellipsoidal", "spherical", "torispherical", "guppy"):
            if kind == "guppy" and not horizontal:
                continue
            parameters = (
                {}
                if kind == "flat"
                else (
                    {"dish_radius_ratio": 1.0, "knuckle_radius_ratio": 0.08}
                    if kind == "torispherical"
                    else {"depth_m": 0.4}
                )
            )
            head = TankHead(kind=kind, **parameters)
            geometry = AxisymmetricTank(
                inner_diameter_m=2.0,
                straight_length_m=4.0,
                horizontal=horizontal,
                side_a=head,
                side_b=head,
                evidence_id="reference",
            )
            request = original.model_copy(
                update={"geometry": geometry, "liquid_density": None, "headspace_density": None}
            )
            first = run(f"{kind}_{'horizontal' if horizontal else 'vertical'}", request)
            assert first["status"] == "computed" and first["mass_status"] == "needs_data"
            height = first["values"]["maximum_internal_height_m"]
            rows = []
            for i in range(17):
                observation = original.observation.model_copy(update={"value": height * i / 16})
                record = evaluate_tank_inventory(
                    request.model_copy(update={"observation": observation}), context
                )
                assert record["status"] == "computed"
                rows.append(
                    {"height_fraction": i / 16, "volume_fraction": record["values"]["fill_volume_fraction"]}
                )
            profiles[f"{kind}_{'horizontal' if horizontal else 'vertical'}"] = rows
    curve = CalibratedTankCurve(
        heights_m=(0.0, 1.0, 2.0), volumes_m3=(0.0, 1.0, 4.0), evidence_id="reference"
    )
    assert run("calibration_curve", original.model_copy(update={"geometry": curve}))["status"] == "computed"
    for key, value in [("negative_level", -0.01), ("above_tank", 2.1), ("missing_level", None)]:
        result = run(
            key,
            original.model_copy(
                update={"observation": original.observation.model_copy(update={"value": value})}
            ),
        )
        assert result["status"] == ("needs_data" if value is None else "out_of_scope")
    for role, q in [("liquid", 0.0), ("headspace", 1.0)]:
        state = PropertyTankDensity(
            state=StateRequest(fluid="Hydrogen", inputs={"P": 101325.0, "Q": q}, single_phase_required=False),
            evidence_id="reference",
        )
        if role == "liquid":
            liquid = state
        else:
            gas = state
    assert (
        run(
            "saturated_hydrogen",
            original.model_copy(update={"liquid_density": liquid, "headspace_density": gas}),
        )["mass_status"]
        == "computed"
    )
    run("liquid_mass_without_gas_density", original.model_copy(update={"headspace_density": None}))
    exchanges = {}
    for kind in ("flat", "ellipsoidal"):
        head = TankHead() if kind == "flat" else TankHead(kind=kind, depth_m=0.4)
        _, record = lh2dt_tank_geometry(original.geometry.model_copy(update={"side_a": head, "side_b": head}))
        exchanges[kind] = {
            "maximum_volume_difference_m3": record["maximum_volume_difference_m3"],
            "probe_count": len(record["volume_profile"]),
            "qualified_scope": record["qualified_scope"],
        }
        (root / f"geometry_exchange_{kind}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )

    source_spec = next(row for row in catalog() if row.id == "coupled_tank")
    for asset in source_spec.assets:
        algorithm, expected = asset.checksum.split(":", 1)
        assert file_digest(args.raw_root / asset.filename, algorithm) == expected
    source_review = review_coupled_tank_source(args.raw_root)
    assert (
        source_review["operating_metadata_parse_error"]
        and source_review["invalid_json_contains_mm_unit_tokens"]
    )
    assert source_review["level_rows"] == 81 and source_review["control_rows"] == 80
    assert (
        source_review["complete_collection_rows"] == 4502
        and source_review["level_rows_without_same_time_command"] == 1
    )
    (root / "source_review.json").write_text(json.dumps(source_review, indent=2) + "\n", encoding="utf-8")
    with (root / "conditional_inventory.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "run",
                "tank",
                "time_s",
                "assumed_diameter_m",
                "mapped_level_m",
                "liquid_volume_m3",
                "conditional_liquid_mass_kg",
            ]
        )
        totals, missing_commands, first_volume = 0, 0, None
        mappings = {}
        geometries = {
            d: AxisymmetricTank(
                inner_diameter_m=d,
                straight_length_m=0.2 / (math.pi * (d / 2) ** 2),
                horizontal=False,
                evidence_id="assumption",
            )
            for d in (0.4, 0.6)
        }
        for run_number in range(1, 11):
            mapping = CoupledTankReadRequest(
                run_number=run_number,
                level_unit="cm",
                level_zero_offset_m=0.0,
                unit_mapping_evidence_id="assumption",
                datum_evidence_id="assumption",
                source_origin="laboratory",
            )
            frames, metadata = coupled_tank_frames(args.raw_root, mapping)
            mappings[str(run_number)] = metadata
            missing_commands += sum(not row.valid["pump_command_percent"] for row in frames)
            if run_number == 1:
                batch = monitoring_batch(frames)
                monitoring = {
                    "rows": len(frames),
                    "channels": list(batch.channels),
                    "missing_control_samples": missing_commands,
                    "clock_id_preserved": batch.metadata["clock_id"] == frames[0].clock_id,
                    "field_detector_accuracy_established": False,
                }
            for tank in (1, 2):
                data_context = RunContext(
                    asset_id=f"tank_{tank}",
                    scenario_id=f"conditional_geometry_experiment_{run_number}",
                    clock_id=frames[0].clock_id,
                    origin="laboratory",
                    metadata={
                        "native_unit_mapping_digest": metadata["mapping_digest"],
                        "native_column": f"Experimento{run_number}",
                    },
                    evidence=(
                        EvidenceRef(
                            id="source",
                            citation=source_spec.citation,
                            origin="laboratory",
                            license_id="CC-BY-4.0",
                            sha256=metadata["source_sha256"][f"h{tank}.csv"],
                            locator=source_spec.source + f"#file=h{tank}.csv&column=Experimento{run_number}",
                        ),
                        EvidenceRef(
                            id="assumption",
                            citation="Authored cm/datum, diameter and uniform water state hypotheses",
                            origin="synthetic",
                            license_id="MIT",
                        ),
                    ),
                )
                for frame in frames:
                    for diameter, shape in geometries.items():
                        req = TankInventoryRequest(
                            geometry=shape,
                            observation=TankObservation(
                                tank_id=data_context.asset_id,
                                clock_id=frame.clock_id,
                                time_s=frame.time_s,
                                quantity="level_from_internal_bottom",
                                value=frame.values[f"tank{tank}_level_m"],
                                unit="m",
                                evidence_id="source",
                            ),
                            liquid_density=PropertyTankDensity(
                                state=StateRequest(fluid="Water", inputs={"P": 101325.0, "T": 293.15}),
                                evidence_id="assumption",
                            ),
                        )
                        result = evaluate_tank_inventory(req, data_context)
                        assert result["status"] == "computed" and result["mass_status"] == "needs_data"
                        values = result["values"]
                        assert math.isclose(values["total_internal_volume_m3"], 0.2, abs_tol=1e-12)
                        writer.writerow(
                            [
                                run_number,
                                tank,
                                frame.time_s,
                                diameter,
                                values["liquid_level_m"],
                                values["liquid_volume_m3"],
                                values["liquid_mass_kg"],
                            ]
                        )
                        if run_number == tank == 1 and frame.time_s == 0 and diameter == 0.4:
                            first_volume = values["liquid_volume_m3"]
                        totals += 1
    assert totals == 3240 and missing_commands == 10
    (root / "unit_mappings.json").write_text(json.dumps(mappings, indent=2) + "\n", encoding="utf-8")
    alternate, _ = coupled_tank_frames(
        args.raw_root, mapping.model_copy(update={"run_number": 1, "level_unit": "mm"})
    )
    alternate_volume = math.pi * (0.4 / 2) ** 2 * alternate[0].values["tank1_level_m"]
    assert math.isclose(first_volume / alternate_volume, 10.0, rel_tol=1e-12)
    assert before == {"lh2dt": revision(args.lh2dt), "monitoring": revision(args.monitoring)}
    summary = {
        "schema": "pipeline_integrity.tank_inventory_verification.v1",
        "cases": cases,
        "authored_geometry_profiles": profiles,
        "source_versions": before,
        "geometry_exchanges": exchanges,
        "monitoring": monitoring,
        "data": {
            "source": source_spec.source,
            "license_id": source_spec.license_id,
            "creators": list(source_spec.creators),
            "source_status": "needs_data",
            "source_review_sha256": file_digest(root / "source_review.json"),
            "experiment_columns": 10,
            "level_observations": 1620,
            "conditional_snapshots": totals,
            "missing_command_samples": missing_commands,
            "conditional_output_sha256": file_digest(root / "conditional_inventory.csv"),
            "unit_mapping_receipts_sha256": file_digest(root / "unit_mappings.json"),
            "alternative_unit_volume_ratio": 10.0,
            "nominal_capacity_m3": 0.2,
            "assumed_diameters_m": [0.4, 0.6],
            "unit_datum_geometry_temperature_and_pressure_are_hypotheses": True,
            "physical_mass_balance_validated": False,
        },
        "module_sha256": file_digest(Path("src/pipeline_integrity/tanks.py")),
        "loader_sha256": file_digest(Path("src/pipeline_integrity/tank_data.py")),
        "script_sha256": file_digest(Path(__file__)),
        "field_calibrated": False,
        "tank_dynamics_solved": False,
    }
    Path("research/tank_inventory_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "authored_cases": len(cases),
                "conditional_snapshots": totals,
                "source_status": "needs_data",
                "geometry_exchanges": 2,
                "field_calibrated": False,
            }
        )
    )


if __name__ == "__main__":
    main()
