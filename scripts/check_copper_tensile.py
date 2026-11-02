"""Apply explicit specimen transforms and contiguous-prefix offset diagnostics to copper data."""

import argparse
import json
import math
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.characterization import (
    PARAMETER_UNITS,
    REVIEWS,
    CharacterizationRequest,
    evaluate_characterization,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.copper_tensile import load_copper_tensile
from pipeline_integrity.datasets import file_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--archive", type=Path, default=Path("data/raw/copper_mechanical/Files.zip"))
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    records = load_copper_tensile(args.archive)
    private = Path("artifacts/copper_tensile")
    private.mkdir(parents=True, exist_ok=True)
    counts = {
        "physical_records": len(records),
        "source_rows": 0,
        "nonincreasing_time_intervals": 0,
        "nonincreasing_strain_intervals": 0,
        "negative_force_samples": 0,
        "negative_reported_strain_samples": 0,
        "missing_cells": 0,
        "native_point_calls": 0,
        "native_point_computed": 0,
        "selected_point_needs_data": 0,
        "native_offset_calls": 0,
        "native_offset_computed": 0,
        "native_offset_needs_data": 0,
    }
    maximum_stress_error = maximum_strain_error = maximum_offset_equation_error = (
        maximum_export_label_difference
    ) = 0.0
    versions, member_hashes, private_hashes = {}, {}, {}
    for record in records:
        record.check_integrity()
        si = record.si_columns()
        count = len(record.data_rows)
        counts["source_rows"] += count
        counts["missing_cells"] += sum(v is None for row in record.data_rows for v in row)
        counts["nonincreasing_time_intervals"] += sum(
            a is not None and b is not None and b <= a
            for a, b in zip(si["time_s"], si["time_s"][1:], strict=False)
        )
        counts["nonincreasing_strain_intervals"] += sum(
            a is not None and b is not None and b <= a
            for a, b in zip(
                si["reported_engineering_strain"], si["reported_engineering_strain"][1:], strict=False
            )
        )
        counts["negative_force_samples"] += sum(v is not None and v < 0 for v in si["force_N"])
        counts["negative_reported_strain_samples"] += sum(
            v is not None and v < 0 for v in si["reported_engineering_strain"]
        )
        area = record.quantity("Probenquerschnitt", "mm²", 1e-6)
        gauge = record.quantity("Anfangsmesslänge", "mm", 1e-3)
        modulus = record.quantity("Steigung des elastischen Teils (mE)", "GPa", 1e9)
        context = RunContext(
            asset_id=f"copper_specimen:{record.specimen_id}",
            scenario_id="reported_geometry_and_strain_exchange",
            clock_id=f"source_lis_order:{record.specimen_id}:{record.member_sha256[:12]}",
            origin="laboratory",
            evidence=(
                EvidenceRef(
                    id="source",
                    citation=record.source["citation"],
                    origin="laboratory",
                    sha256=record.member_sha256,
                    license_id=record.source["license_id"],
                    limitations=("Original elapsed clock can repeat or reverse; no absolute clock inferred",),
                ),
                EvidenceRef(
                    id="protocol",
                    citation="Original diagnostic: reported strain times source gauge length, original area, no uniform-deformation declaration; contiguous strain prefix and reported slope only",
                    origin="user_declared",
                    license_id="MIT",
                ),
            ),
            metadata={
                "physical_specimen_id": record.specimen_id,
                "nominal_material_label": record.material_label,
                "source_member": record.member,
                "source_order_preserved": True,
            },
        )

        def request(method, parameters, *, context=context, record=record):
            return CharacterizationRequest(
                method=method,
                asset_id=context.asset_id,
                specimen_id=record.specimen_id,
                material_condition_id=f"source_nominal_label:{record.material_label};specimen:{record.specimen_id}",
                location_id="reported_tensile_gauge",
                clock_id=context.clock_id,
                parameters=parameters,
                input_units=PARAMETER_UNITS[method],
                applicability_reviews=dict.fromkeys(REVIEWS[method], True),
                review_evidence_ids=dict.fromkeys(REVIEWS[method], "protocol"),
                evidence_ids=("source", "protocol"),
            )

        points = []
        # First and maximum-force rows are explicitly selected. They do not
        # replace the full trace or become an independent validation partition.
        if all(v is not None for v in si["force_N"]):
            chosen = tuple(dict.fromkeys((0, max(range(count), key=lambda i: si["force_N"][i]))))
            for index in chosen:
                force, strain = si["force_N"][index], si["reported_engineering_strain"][index]
                if force < 0 or strain is None or strain < 0 or area is None or gauge is None:
                    counts["selected_point_needs_data"] += 1
                    points.append(
                        {
                            "source_row_index": index,
                            "source_line": record.source_line_numbers[index],
                            "status": "needs_data",
                            "reason": "selected row lacks supported tensile geometry/sign/strain",
                        }
                    )
                    continue
                # Gauge extension is an explicit reconstruction from the
                # reported strain. Machine travel is deliberately separate.
                parameters = {
                    "force_N": force,
                    "initial_area_m2": area,
                    "extension_m": strain * gauge,
                    "gauge_length_m": gauge,
                    "uniform_deformation_declared": False,
                }
                result = evaluate_characterization(request("materials.tensile_point", parameters), context)
                counts["native_point_calls"] += 1
                assert result["status"] == "computed"
                counts["native_point_computed"] += 1
                values = result["values"]
                assert "true_stress_Pa" not in values and "true_strain" not in values
                reported_stress = si["reported_engineering_stress_Pa"][index]
                if reported_stress is not None:
                    maximum_stress_error = max(
                        maximum_stress_error, abs(values["engineering_stress_Pa"] - reported_stress)
                    )
                    assert math.isclose(
                        values["engineering_stress_Pa"], reported_stress, rel_tol=1e-10, abs_tol=1e-6
                    )
                maximum_strain_error = max(maximum_strain_error, abs(values["engineering_strain"] - strain))
                assert math.isclose(values["engineering_strain"], strain, rel_tol=1e-12, abs_tol=1e-15)
                versions[result["request"]["method"]] = result["native_receipt"]["job"]["model_version"]
                points.append(
                    {
                        "source_row_index": index,
                        "source_line": record.source_line_numbers[index],
                        "reconstructed_gauge_extension_not_machine_travel": True,
                        "result": result,
                    }
                )
        prefix_end = 0
        strain, stress = si["reported_engineering_strain"], si["reported_engineering_stress_Pa"]
        for index in range(count):
            if (
                strain[index] is None
                or stress[index] is None
                or strain[index] < 0
                or stress[index] < 0
                or (index > 0 and strain[index] <= strain[index - 1])
            ):
                break
            prefix_end = index + 1
        offset = None
        if prefix_end >= 3 and modulus is not None:
            parameters = {
                "engineering_strain": list(strain[:prefix_end]),
                "engineering_stress_Pa": list(stress[:prefix_end]),
                "elastic_modulus_Pa": modulus,
                "offset_strain": 0.002,
            }
            offset = evaluate_characterization(request("materials.offset_yield", parameters), context)
            counts["native_offset_calls"] += 1
            assert offset["status"] in ("computed", "needs_data")
            counts[
                "native_offset_computed" if offset["status"] == "computed" else "native_offset_needs_data"
            ] += 1
            versions[offset["request"]["method"]] = offset["native_receipt"]["job"]["model_version"]
            if offset["status"] == "computed":
                v = offset["values"]
                left, right = v["bracket_indices"]
                assert right == left + 1 and 0 <= left < right < prefix_end
                assert strain[left] <= v["intersection_strain"] <= strain[right]
                assert (
                    strain[left] - stress[left] / modulus - 0.002
                    < 0
                    <= strain[right] - stress[right] / modulus - 0.002
                )
                line_stress = modulus * (v["intersection_strain"] - 0.002)
                maximum_offset_equation_error = max(
                    maximum_offset_equation_error, abs(v["offset_yield_stress_Pa"] - line_stress)
                )
                assert math.isclose(v["offset_yield_stress_Pa"], line_stress, rel_tol=1e-11, abs_tol=1e-6)
                label = record.quantity("Dehngrenze (Rp0,2)", "MPa", 1e6)
                if label is not None:
                    maximum_export_label_difference = max(
                        maximum_export_label_difference, abs(v["offset_yield_stress_Pa"] - label)
                    )
        else:
            counts["native_offset_needs_data"] += 1
        record_body = {
            "source_record": record.model_dump(mode="json"),
            "point_selection": "first_and_original_maximum_force_rows",
            "point_results": points,
            "branch_selection": "first_contiguous_nonnegative_strictly_increasing_reported_strain_prefix_without_repair",
            "prefix_end_exclusive": prefix_end,
            "offset_result": offset,
            "source_export_label_is_independent_truth": False,
            "strain_does_not_replace_source_machine_travel": True,
            "cyclic_parameters_fitted": False,
        }
        path = private / f"{record.specimen_id}.json"
        path.write_text(json.dumps(record_body, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        member_hashes[record.member] = record.member_sha256
        private_hashes[record.specimen_id] = file_digest(path)
    after = revision(args.materials)
    assert before == after, "native material source changed during copper exchange"
    summary = {
        "schema": "pipeline_integrity.copper_tensile_verification.v1",
        "status": "passed",
        "primary_data": records[0].source,
        "counts": counts,
        "native_model_versions": versions,
        "source_member_sha256": member_hashes,
        "maximum_selected_stress_difference_from_same_source_export_Pa": maximum_stress_error,
        "maximum_reconstructed_strain_roundtrip_error": maximum_strain_error,
        "maximum_offset_equation_residual_Pa": maximum_offset_equation_error,
        "maximum_offset_difference_from_same_source_rounded_export_label_Pa": maximum_export_label_difference,
        "source_export_labels_are_independent_truth": False,
        "negative_or_repeated_axes_repaired": False,
        "same_trace_rows_used_as_independent_trials": False,
        "gauge_extension_reconstructed_from_reported_strain_not_machine_travel": True,
        "true_stress_conversion_requested": False,
        "reported_slope_used_as_declared_offset_input": True,
        "cyclic_parameters_or_matched_ductility_baselines_inferred": False,
        "clock_monotonicity_required_for_physical_time_history_export": True,
        "time_histories_exported": False,
        "field_material_or_test_procedure_validation": False,
        "raw_or_transformed_observation_rows_in_release": False,
        "external_material_source_before": before,
        "external_material_source_after": after,
        "external_source_unchanged": True,
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/characterization.py")),
        "reader_sha256": file_digest(Path("src/pipeline_integrity/copper_tensile.py")),
        "shared_archive_sha256": file_digest(Path("src/pipeline_integrity/source_archive.py")),
        "script_sha256": file_digest(Path(__file__)),
        "private_record_sha256": private_hashes,
    }
    Path("research/copper_tensile_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "counts": counts,
                "maximum_selected_stress_difference_Pa": maximum_stress_error,
                "maximum_offset_equation_residual_Pa": maximum_offset_equation_error,
                "external_source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
