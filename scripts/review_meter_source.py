"""Apply pinned meter sources; publish aggregate checks and preserve private rows."""

import argparse
import copy
import json
import re
from collections import Counter
from importlib.metadata import distribution, version
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import DatasetSpec, catalog, file_digest
from pipeline_integrity.gas_meter import evaluate_gas_meter, verify_gas_meter_record
from pipeline_integrity.meter_data import (
    VORTEX_ID,
    VSL_ID,
    read_vortex_air_meter,
    read_vsl_calibration,
    verify_meter_source_record,
    vortex_gas_meter_cases,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def review(raw_root, specs, artifacts):
    before = {
        f"{identity}/{asset.filename}": file_digest(raw_root / identity / asset.filename)
        for identity, spec in specs.items()
        for asset in spec.assets
    }
    liquid = read_vsl_calibration(raw_root / VSL_ID, source_spec=specs[VSL_ID])
    vortex = read_vortex_air_meter(raw_root / VORTEX_ID / "Research data.pdf", source_spec=specs[VORTEX_ID])
    save(artifacts / "vsl_record.json", liquid)
    save(artifacts / "vortex_record.json", vortex)
    checks = {
        "vsl_original_source_receipt": verify_meter_source_record(
            liquid, source_root=raw_root, source_spec=specs[VSL_ID]
        ),
        "vortex_original_source_receipt": verify_meter_source_record(
            vortex, source_root=raw_root, source_spec=specs[VORTEX_ID]
        ),
        "eleven_reviewed_worksheets": len(liquid["worksheets"]) == 11,
        "274_individual_repeat_averages": len(liquid["runs"]) == 274,
        "123_summary_rows_84_lng_39_water": Counter(x["fluid"] for x in liquid["summaries"])
        == {"LNG": 84, "water": 39},
        "all_detailed_runs_are_liquid_lng": all(
            x["fluid"] == "LNG" and x["phase"] == "liquid" for x in liquid["runs"]
        ),
        "source_lh2_label_never_becomes_hydrogen": all(
            x["fluid"] == "LNG" for x in liquid["runs"] if "LH2" in x["source_sheet"]
        ),
        "no_calendar_timezone_assigned": all(
            x["clock"]["timezone"] is None and not x["clock"]["utc_assigned"] for x in liquid["runs"]
        ),
        "26_missing_atmospheric_units_not_assumed_pa": sum(
            x["observations_si"]["upstream_pressure_pa_abs"] is None for x in liquid["runs"]
        )
        == 26,
        "all_summary_k2_preserved": all(
            x["uncertainty"]["coverage_factor"] == 2 for x in liquid["summaries"]
        ),
        "all_summary_covariance_df_unknown": all(
            x["uncertainty"]["component_covariance"] is None
            and x["uncertainty"]["degrees_of_freedom"] is None
            and not x["uncertainty"]["independence_assumed"]
            for x in liquid["summaries"]
        ),
        "scalar_U_over_k_only": all(
            x["uncertainty"]["scalar_standard_percent_from_U_over_k"]
            == x["uncertainty"]["expanded_percent"] / 2
            for x in liquid["summaries"]
        ),
        "turbine_type_A_mean_is_relative_pulse_factor": all(
            x["uncertainty"]["type_A_quantity"] == "relative_mean_pulse_factor"
            for x in liquid["summaries"]
            if "Trigas" in x["source_sheet"]
        ),
        "composition_not_normalized": len(liquid["compositions"]) == 12
        and all(not x["normalized"] and x["covariance"] is None for x in liquid["compositions"]),
        "vortex_case_ids_not_clock": vortex["clock_kind"] == "operating_case_ids_not_time"
        and len(vortex["observations"]) == 5,
        "vortex_no_drygas_downstream_or_flow_truth_inferred": all(
            x["gas_components"] is None
            and x["gas_volume_basis"] is None
            and x["downstream_pressure_pa_abs"] is None
            and x["independent_flow_reference"] is None
            for r in vortex["observations"]
            for x in r["branches"]
        ),
        "water_summaries_have_no_fabricated_times": all(
            x["clock"] is None for x in liquid["summaries"] if x["fluid"] == "water"
        ),
    }
    error = [
        x["error_rounding_residual_percent"]
        for x in liquid["runs"]
        if x["error_rounding_residual_percent"] is not None
    ]
    pulse_mass = [
        x["pulse_flow_residual_si"]
        for x in liquid["runs"]
        if x["pulse_flow_residual_si"] is not None and x["pulse_flow_residual_unit"] == "kg/s"
    ]
    pulse_volume = [
        x["pulse_flow_residual_si"]
        for x in liquid["runs"]
        if x["pulse_flow_residual_si"] is not None and x["pulse_flow_residual_unit"] == "m3/s"
    ]
    frequency = [
        x["frequency_factor_residual_per_litre"]
        for x in liquid["runs"]
        if x["frequency_factor_residual_per_litre"] is not None
    ]
    clocks = [
        x["wall_minus_counter_duration_s"]
        for x in liquid["runs"]
        if x["wall_minus_counter_duration_s"] is not None
    ]
    checks.update(
        error_224_repeats_matches_reported_rounded_error=len(error) == 224
        and max(map(abs, error)) <= 0.000501,
        pulse_160_mass_repeats_matches_source_rounded_indication=len(pulse_mass) == 160
        and max(map(abs, pulse_mass)) <= 5.01e-6,
        pulse_64_local_volume_repeats_matches_source_formula_cache=len(pulse_volume) == 64
        and max(map(abs, pulse_volume)) <= 1e-15,
        frequency_50_turbine_repeats_matches_source_factor=len(frequency) == 50
        and max(map(abs, frequency)) <= 2e-13,
        wall_clock_does_not_replace_counter_duration=len(clocks) == 224
        and any(v < 0 for v in clocks)
        and any(v > 0 for v in clocks),
        all_source_run_calendar_strings_parse=all(x["clock"]["valid"] for x in liquid["runs"]),
        all_original_numeric_domains_supported=all(not x["invalid_domain_fields"] for x in liquid["runs"]),
    )
    native_records = []
    pairs = vortex_gas_meter_cases(vortex, source_root=raw_root, source_spec=specs[VORTEX_ID])
    for request, context in pairs:
        result = evaluate_gas_meter(request, context)
        label = f"{request.event_id}:{request.meter_id}"
        checks[label + ":actual_needs_data_without_backend"] = (
            result["status"] == "needs_data"
            and not result["native_called"]
            and not result["meter_called"]
            and result["backend"] is None
        )
        checks[label + ":stored_request_replay"] = verify_gas_meter_record(
            result, request=request, context=context
        )["valid"]
        native_records.append(result)
    save(artifacts / "withheld_gas_meter_records.json", native_records)
    assets = [liquid["provenance"], *liquid["method_assets"], vortex["provenance"]]
    checks["3_assets_547329_bytes_supplier_checksums"] = (
        len(assets) == 3
        and sum(a["bytes"] for a in assets) == 547329
        and all(a["supplier_checksum_verified"] for a in assets)
    )
    corruptions = [
        (
            VSL_ID,
            "expanded_becomes_standard",
            lambda r: r["summaries"][0]["uncertainty"].update(coverage_factor=1),
        ),
        (
            VSL_ID,
            "invented_component_covariance",
            lambda r: r["summaries"][0]["uncertainty"].update(component_covariance=[[0]]),
        ),
        (VSL_ID, "lng_becomes_hydrogen_from_sheet_label", lambda r: r["runs"][-1].update(fluid="Hydrogen")),
        (
            VSL_ID,
            "local_volume_becomes_normal_gas",
            lambda r: r["runs"][0].update(reference_volume_basis="normal_dry_gas"),
        ),
        (
            VSL_ID,
            "invented_utc_clock",
            lambda r: r["runs"][0]["clock"].update(timezone="UTC", utc_assigned=True),
        ),
        (VSL_ID, "laboratory_becomes_field", lambda r: r.update(origin="measured")),
        (VSL_ID, "simulation_provenance", lambda r: r["provenance"].update(dataset_origin="simulation")),
        (
            VORTEX_ID,
            "invented_observation_seconds",
            lambda r: r["observations"][0]["branches"][0].update(time_s=0.0),
        ),
        (
            VORTEX_ID,
            "nominal_air_becomes_dry",
            lambda r: r["observations"][0]["branches"][0].update(gas_volume_basis="same_composition_dry"),
        ),
        (
            VORTEX_ID,
            "invented_independent_flow_truth",
            lambda r: r["observations"][0]["branches"][0].update(independent_flow_reference=0.1),
        ),
        (
            VORTEX_ID,
            "invented_downstream_tap",
            lambda r: r["observations"][0]["branches"][0].update(downstream_pressure_pa_abs=100000),
        ),
    ]
    refused = {}
    for identity, label, mutation in corruptions:
        record = copy.deepcopy(liquid if identity == VSL_ID else vortex)
        mutation(record)
        record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
        try:
            verify_meter_source_record(record, source_root=raw_root, source_spec=specs[identity])
        except ValueError:
            refused[label] = True
        else:
            raise AssertionError("rehashed source meaning mutation accepted: " + label)
    checks["all_three_original_assets_unchanged"] = all(
        file_digest(raw_root / name) == value for name, value in before.items()
    )
    if not all(checks.values()):
        raise AssertionError([name for name, passed in checks.items() if not passed])
    pypdf = distribution("pypdf")
    license_files = {
        str(p): file_digest(pypdf.locate_file(p)) for p in pypdf.files if str(p).endswith("/LICENSE")
    }
    return dict(
        schema="pipeline_integrity.meter_source_review.v1",
        status="source_readers_verified_gas_mapping_needs_data",
        acquired_groups=2,
        acquired_files=len(assets),
        acquired_bytes=sum(a["bytes"] for a in assets),
        assets=assets,
        liquid=dict(
            record_digest=liquid["record_digest"],
            individual_repeat_averages=len(liquid["runs"]),
            repeats_by_meter=dict(Counter(x["source_sheet"] for x in liquid["runs"])),
            summaries=len(liquid["summaries"]),
            summary_fluid_counts=dict(Counter(x["fluid"] for x in liquid["summaries"])),
            composition_and_uncertainty_rows=len(liquid["compositions"]),
            source_calendar_timezone=None,
            utc_assigned=False,
            source_reference_density_recomputed=False,
            unknown_absolute_pressure_rows=26,
            missing_ambient_unit_preserved=True,
            exact_row_error_comparisons=len(error),
            maximum_absolute_rounding_residual_percentage_point=max(map(abs, error)),
            pulse_mass_identity_comparisons=len(pulse_mass),
            maximum_absolute_pulse_residual_kg_s=max(map(abs, pulse_mass)),
            pulse_volume_identity_comparisons=len(pulse_volume),
            maximum_absolute_pulse_residual_m3_s=max(map(abs, pulse_volume)),
            turbine_frequency_identity_comparisons=len(frequency),
            maximum_absolute_pulse_factor_residual_per_litre=max(map(abs, frequency)),
            wall_and_counter_durations_compared=len(clocks),
            negative_wall_minus_counter_duration=sum(v < 0 for v in clocks),
            positive_wall_minus_counter_duration=sum(v > 0 for v in clocks),
            expanded_uncertainty_coverage_factor=2,
            degrees_of_freedom=None,
            component_covariance=None,
            summaries_and_repeats_independent=False,
            field_accuracy_qualified=False,
        ),
        vortex=dict(
            record_digest=vortex["record_digest"],
            operating_cases=5,
            branches=15,
            actual_gas_meter_requests=len(pairs),
            actual_request_status_counts=dict(Counter(r["status"] for r in native_records)),
            native_meter_calls=sum(r["meter_called"] for r in native_records),
            property_backend_calls=sum(r["native_called"] for r in native_records),
            observation_time_axis=None,
            gas_components=None,
            independent_flow_reference=None,
            physical_plant_port_mapping=None,
            diagrams_use_symbolic_analysis_nodes=True,
        ),
        checks=checks,
        source_contract_criteria=sum(checks.values()),
        actual_rehashed_source_corruption_refusals=refused,
        independent_meter_model_validation_cases=0,
        rows_and_detailed_receipts_in_release=False,
        runtime={name: version(name) for name in ("openpyxl", "pypdf")},
        pypdf_license="BSD-3-Clause",
        installed_pypdf_license_sha256=license_files,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/meter_data"))
    parser.add_argument("--output", type=Path, default=Path("research/meter_source_results.json"))
    args = parser.parse_args()
    specs = (
        catalog()
        if args.catalog_file is None
        else tuple(
            DatasetSpec.model_validate_json(json.dumps(row))
            for row in json.loads(args.catalog_file.read_text(encoding="utf-8"))["datasets"]
        )
    )
    selected = {s.id: s for s in specs if s.id in {VSL_ID, VORTEX_ID}}
    if set(selected) != {VSL_ID, VORTEX_ID}:
        raise ValueError("the two reviewed meter source specifications must be present")
    result = review(args.raw_root, selected, args.artifacts)
    root = Path(__file__).resolve().parents[1]
    guide = root / "docs/METER_DATA_KO.md"
    blocks = re.findall(r"```python\s*\n(.*?)\n```", guide.read_text(encoding="utf-8"), flags=re.S)
    for block in blocks:
        exec(
            compile(block, "METER_DATA_KO.md", "exec"),
            {"METER_RAW_ROOT": args.raw_root, "METER_SOURCE_SPECS": selected},
        )
    result["doc_python_blocks_executed"] = len(blocks)
    result["project_file_sha256"] = {
        name: file_digest(root / name)
        for name in (
            "src/pipeline_integrity/meter_data.py",
            "tests/test_meter_data.py",
            "scripts/review_meter_source.py",
            "docs/METER_DATA_KO.md",
        )
    }
    save(args.output, result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "acquired_groups",
                    "acquired_files",
                    "acquired_bytes",
                    "source_contract_criteria",
                    "doc_python_blocks_executed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
