"""Verify pinned thermal rows, conservative cooling branches and native common-clock band overlap."""

import argparse
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.ebw_data import load_ebw_thermal, select_ebw_cooling_branch
from pipeline_integrity.welding_exchange import WeldThermalSpec, evaluate_weld_thermal

MAXIMUM_INTERVAL_S = 0.020001


def _crossing_reference(samples, target_c):
    """Decimal arithmetic on source Celsius/time, independent of the owner's Kelvin interpolation."""
    target = Decimal(str(target_c))
    for a, b in zip(samples, samples[1:], strict=False):
        start, end = Decimal(str(a["temperature_c"])), Decimal(str(b["temperature_c"]))
        if start >= target >= end:
            ta, tb = Decimal(str(a["time_s"])), Decimal(str(b["time_s"]))
            return float(ta if start == end else ta + (target - start) * (tb - ta) / (end - start))
    raise AssertionError("selected source branch does not span the declared reference threshold")


def _overlap_reference(records, lower_c, upper_c):
    """Partition each time cell at all band crossings and check every partition midpoint in Celsius."""
    times = [s["time_s"] for s in records[0]["samples"]]
    total = 0.0
    for index, (start, end) in enumerate(zip(times, times[1:], strict=False)):
        endpoints = [
            (r["samples"][index]["temperature_c"], r["samples"][index + 1]["temperature_c"]) for r in records
        ]
        boundaries = {0.0, 1.0}
        for a, b in endpoints:
            if b != a:
                boundaries.update(f for t in (lower_c, upper_c) if 0 < (f := (t - a) / (b - a)) < 1)
        cuts = sorted(boundaries)
        for a, b in zip(cuts, cuts[1:], strict=False):
            midpoint = (a + b) / 2
            if all(lower_c <= low + midpoint * (high - low) <= upper_c for low, high in endpoints):
                total += (b - a) * (end - start)
    return total


def _frames(records, context, *, samples=None):
    vectors = [r["samples"] for r in records] if samples is None else [samples]
    times = [s["time_s"] for s in vectors[0]]
    if any([s["time_s"] for s in values] != times for values in vectors[1:]):
        raise ValueError("source sensors do not share an exact released clock; alignment is not inferred")
    if any(t is None or t < 0 for t in times):
        raise ValueError("unknown source clock cannot become SignalFrame time")
    channels = [f"sensor_column_{r['source_column']}" for r in records]
    result = []
    for index, time in enumerate(times):
        values = {key: vector[index]["temperature_k"] for key, vector in zip(channels, vectors, strict=True)}
        result.append(
            SignalFrame(
                stream_id=context.asset_id,
                clock_id=context.clock_id,
                time_s=time,
                values=values,
                units=dict.fromkeys(channels, "K"),
                valid={
                    key: vector[index]["temperature_state"] == "observed"
                    for key, vector in zip(channels, vectors, strict=True)
                },
                origin="laboratory",
            )
        )
    return tuple(result), tuple(channels)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/raw/ebw_thermal/EBW_ThermalCycle_CoolingTime_Data_HSSS.zip"),
    )
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    output = Path("artifacts/ebw_thermal")
    output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (output / (name + ".json")).write_text(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )

    source = load_ebw_thermal(args.archive)
    save("source_profiles", source)
    assert (source["physical_experiment_count"], source["sensor_history_count"]) == (5, 7)
    assert (source["physical_time_rows"], source["sensor_samples"]) == (19781, 25416)
    assert all(r["summary"]["strictly_increasing_time"] for r in source["records"])
    assert all(r["summary"]["maximum_sample_interval_s"] < MAXIMUM_INTERVAL_S for r in source["records"])
    assert all(
        r["summary"]["missing_or_invalid_time_count"]
        == r["summary"]["missing_or_invalid_temperature_count"]
        == 0
        for r in source["records"]
    )
    evidence = (
        EvidenceRef(
            id="ebw_source",
            citation=source["citation"],
            origin="laboratory",
            locator=source["source"],
            sha256=source["source_sha256"],
            license_id=source["license_id"],
        ),
        EvidenceRef(
            id="declared_thermal_review",
            origin="user_declared",
            license_id="MIT",
            citation=(
                "Research arithmetic review only: released Celsius and sensor-distance headers, one workbook clock, "
                "unique contiguous unmodified post-peak cooling bracket at 800 to 500 C; a maximum recorded gap "
                "of 0.020001 s is caller declared. The separate illustrative 500 to 800 C occupancy band uses "
                "all recorded sensors simultaneously and is not an EBW heat-treatment acceptance requirement."
            ),
        ),
    )
    cooling, holds = [], []
    references = {}

    def check(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    for experiment in source["experiments"]:
        records = [r for r in source["records"] if r["experiment_id"] == experiment["id"]]
        context = RunContext(
            asset_id=experiment["id"],
            scenario_id="released_ebw_with_declared_thermal_review",
            clock_id=records[0]["clock_id"],
            origin="laboratory",
            evidence=evidence,
        )
        for sensor_index, record in enumerate(records):
            selection = select_ebw_cooling_branch(record)
            save(f"{experiment['id']}_sensor_{sensor_index + 1}_selection", selection)
            summary = dict(
                sensor_id=record["id"],
                source_filename=record["source_filename"],
                source_sheet=record["source_sheet"],
                source_column=record["source_column"],
                nominal_material_from_filename=record["nominal_material_from_filename"],
                nominal_material_from_sheet=record["nominal_material_from_sheet"],
                sensor_distance_from_weld_centre_m=record["sensor_distance_from_weld_centre_m"],
                source_summary=record["summary"],
                selection_status=selection["status"],
                selection_rule=selection["rule"],
                selection_issues=selection["issues"],
                selected_sample_count=len(selection["samples"]),
                selected_source_row_range=selection["selected_source_row_range"],
                post_peak_temperature_increase_count=selection.get("post_peak_temperature_increase_count"),
                native_status="needs_data",
                native_values=None,
                filename_label_association="declared comparison by source sensor column order; not a measured reference",
                filename_reported_label=experiment["filename_reported_labels"][sensor_index],
                filename_label_difference_is_not_a_validation_error=True,
            )
            if selection["status"] != "selected":
                cooling.append(summary)
                continue
            frames, channels = _frames([record], context, samples=selection["samples"])
            spec = WeldThermalSpec(
                asset_id=context.asset_id,
                location_id=record["id"],
                clock_id=context.clock_id,
                material_condition_id=record["nominal_material_from_sheet"]
                + ":unqualified_recorded_condition",
                kind="cooling",
                temperature_channels=channels,
                lower_temperature_k=773.15,
                upper_temperature_k=1073.15,
                maximum_sample_interval_s=MAXIMUM_INTERVAL_S,
                sensor_locations_temperature_and_rule_reviewed=True,
                evidence_ids=("ebw_source", "declared_thermal_review"),
            )
            result = evaluate_weld_thermal(frames, spec, context)
            save(f"{experiment['id']}_sensor_{sensor_index + 1}_native_cooling", result)
            summary.update(native_status=result["status"])
            if result["status"] == "computed":
                values = result["native_result"]["values"]
                summary["native_values"] = values
                upper = _crossing_reference(selection["samples"], 800)
                lower = _crossing_reference(selection["samples"], 500)
                for key, expected in (
                    ("upper_crossing_s", upper),
                    ("lower_crossing_s", lower),
                    ("cooling_interval_s", lower - upper),
                ):
                    check(record["id"] + ":" + key, values[key], expected)
                label = summary["filename_reported_label"]
                summary["calculated_minus_filename_t85_s"] = values["cooling_interval_s"] - label["t85_s"]
                summary["observed_minus_filename_peak_c"] = (
                    record["summary"]["observed_peak_temperature_k"] - 273.15 - label["peak_temperature_c"]
                )
            cooling.append(summary)
        frames, channels = _frames(records, context)
        specification = WeldThermalSpec(
            asset_id=context.asset_id,
            location_id=experiment["id"],
            clock_id=context.clock_id,
            material_condition_id=records[0]["nominal_material_from_sheet"]
            + ":unqualified_recorded_condition",
            kind="hold",
            temperature_channels=channels,
            lower_temperature_k=773.15,
            upper_temperature_k=1073.15,
            maximum_sample_interval_s=MAXIMUM_INTERVAL_S,
            evidence_ids=("ebw_source", "declared_thermal_review"),
        )
        held = evaluate_weld_thermal(frames, specification, context)
        assert held["status"] == "needs_data" and held["native_result"] is None
        reviewed = specification.model_copy(update={"sensor_locations_temperature_and_rule_reviewed": True})
        result = evaluate_weld_thermal(frames, reviewed, context)
        save(experiment["id"] + "_native_illustrative_band", result)
        assert result["status"] == "computed"
        values = result["native_result"]["values"]
        check(
            experiment["id"] + ":simultaneous_band_occupancy_s",
            values["total_simultaneous_hold_s"],
            _overlap_reference(records, 500, 800),
        )
        holds.append(
            dict(
                experiment_id=experiment["id"],
                source_filename=experiment["source_filename"],
                sensor_count=len(records),
                original_time_rows=len(frames),
                unreviewed_rule_status=held["status"],
                native_status=result["status"],
                native_values=values,
                criterion="caller-declared illustrative simultaneous 500 to 800 C occupancy",
                process_was_a_heat_treatment_procedure=False,
                heat_treatment_acceptance_established=False,
            )
        )
    assert len(cooling) == 7 and all(r["native_status"] == "computed" for r in cooling)
    after = revision(args.materials)
    assert before == after
    save(
        "computation_summary",
        dict(
            cooling_results=cooling,
            illustrative_simultaneous_band_results=holds,
            independent_arithmetic_references=references,
        ),
    )
    intervals = [
        r["native_values"]["cooling_interval_s"] for r in cooling if r["native_status"] == "computed"
    ]
    label_errors = [
        abs(r["calculated_minus_filename_t85_s"]) for r in cooling if r["native_status"] == "computed"
    ]
    summary = dict(
        schema="pipeline_integrity.ebw_thermal_verification.v1",
        status="passed",
        primary_data={
            key: source[key]
            for key in (
                "dataset_id",
                "doi",
                "source",
                "citation",
                "license_id",
                "source_filename",
                "source_sha256",
                "source_bytes",
                "supplier_checksum",
                "publisher_checksum_verified_before_xlsx_open",
                "physical_experiment_count",
                "sensor_history_count",
                "physical_time_rows",
                "sensor_samples",
                "common_absolute_clock_across_experiments_established",
                "missing_values_imputed",
            )
        },
        cooling=dict(
            native_model="welding.cooling_interval",
            lower_temperature_k=773.15,
            upper_temperature_k=1073.15,
            selected_sensor_histories=sum(r["selection_status"] == "selected" for r in cooling),
            minimum_calculated_interval_s=min(intervals),
            maximum_calculated_interval_s=max(intervals),
            maximum_absolute_difference_from_filename_t85_label_s=max(label_errors),
            filename_labels_are_independent_observations=False,
            filename_label_sensor_column_order_is_explicit_comparison_assumption=True,
            selection_rule="unique_contiguous_post_peak_crossing_bracket_exact_nonincreasing_v1",
            source_histories_have_post_peak_noise_outside_selected_brackets=any(
                r["post_peak_temperature_increase_count"] > 0 for r in cooling
            ),
        ),
        illustrative_simultaneous_band=dict(
            native_model="welding.heat_treatment_history",
            common_clock_experiments=len(holds),
            single_sensor_experiments=sum(r["sensor_count"] == 1 for r in holds),
            simultaneous_two_sensor_experiments=sum(r["sensor_count"] == 2 for r in holds),
            criterion="caller-declared illustrative simultaneous 500 to 800 C occupancy",
            process_was_a_heat_treatment_procedure=False,
            heat_treatment_acceptance_established=False,
        ),
        independent_reference_count=len(references),
        maximum_independent_reference_absolute_error=max(v["absolute_error"] for v in references.values()),
        cooling_histories_computed=sum(r["native_status"] == "computed" for r in cooling),
        cooling_histories_withheld=sum(r["native_status"] != "computed" for r in cooling),
        unreviewed_band_rules_withheld=len(holds),
        full_original_histories_preserved_in_private_artifacts=True,
        full_heating_and_cooling_histories_sent_to_cooling_method=False,
        samples_inside_selected_brackets_removed_smoothed_or_interpolated_before_native_call=False,
        rows_outside_explicit_selected_brackets_excluded_from_cooling_calls=True,
        piecewise_linear_crossing_interpolation_is_declared=True,
        maximum_declared_sample_interval_s=MAXIMUM_INTERVAL_S,
        nominal_material_label_disagreements=sum(
            x["nominal_material_labels_disagree"] for x in source["experiments"]
        ),
        external_material_source_before=before,
        external_material_source_after=after,
        external_source_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/ebw_data.py")),
        welding_exchange_sha256=file_digest(Path("src/pipeline_integrity/welding_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        raw_or_transformed_observation_rows_in_release=False,
        thermal_cycle_model_metallurgical_or_field_validation=False,
        procedure_or_standard_acceptance=False,
    )
    Path("research/ebw_thermal_results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                experiments=5,
                sensors=7,
                physical_time_rows=19781,
                sensor_samples=25416,
                native_cooling=summary["cooling_histories_computed"],
                native_illustrative_band=len(holds),
                unreviewed_band_withheld=len(holds),
                independent_references=len(references),
                maximum_reference_error=summary["maximum_independent_reference_absolute_error"],
                external_source_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    main()
