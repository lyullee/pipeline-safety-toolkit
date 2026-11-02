"""Review the licensed fastener angle traces; preserve observed force while withholding unreviewed K/At."""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.fastener_data import read_fastener_trace
from pipeline_integrity.fabrication_exchange import FabricationRequest, evaluate_fabrication

PAPER = "https://pmc.ncbi.nlm.nih.gov/articles/PMC10792756/"
PAPER_DOI = "10.1016/j.dib.2023.110022"
PAPER_SHA256 = "0c049dc885088f6844b70f53dce3b7f93b9e2496d6278d36f9d5680d108cdb89"


def _seal(body):
    return body | {"record_digest": digest(body)}


def _verified_assets(root, source):
    result = {}
    for asset in source.assets:
        path = root / asset.filename
        if asset.checksum is None or not asset.checksum.startswith("sha256:"):
            raise ValueError("a publisher SHA-256 must be pinned before fastener source review")
        if asset.checksum_origin != "publisher_metadata":
            raise ValueError("fastener source checksum must originate in publisher metadata")
        actual = file_digest(path)
        if path.stat().st_size != asset.bytes or actual != asset.checksum.split(":", 1)[1]:
            raise ValueError("fastener source size/SHA-256 mismatch: " + asset.filename)
        result[asset.filename] = dict(
            bytes=asset.bytes,
            sha256=actual,
            supplier_checksum=asset.checksum,
            publisher_checksum_verified=True,
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/fastener_clamping"))
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.materials / "src").resolve()))
    source = next(s for s in catalog() if s.id == "fastener_clamping")
    assert source.license_id == "CC-BY-4.0" and source.rights_review == "reviewed_license"
    files = _verified_assets(args.root, source)
    assert len(files) == 102 and sum(v["bytes"] for v in files.values()) == 1281783
    output = Path("artifacts/fastener_source")
    output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (output / (name + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8"
        )

    tables = {}
    for number in range(1, 7):
        name = f"table_{number}.csv"
        with (args.root / name).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        tables[name] = dict(
            filename=name,
            sha256=files[name]["sha256"],
            source_rows=[dict(source_row=i, source_cells=row) for i, row in enumerate(rows, start=1)],
        )
    doe = tables["table_2.csv"]["source_rows"]
    header, units = doe[0]["source_cells"], doe[1]["source_cells"]
    if len(header) != 9 or header[:2] != ["Run #", "Replicate"] or units[3] != "[rad/s]":
        raise ValueError("unreviewed released DOE run, replicate or angular-speed units")
    doe_observations = [r for r in doe[2:] if any(cell.strip() for cell in r["source_cells"])]
    conditions = {int(r["source_cells"][0]): r for r in doe_observations}
    assert set(conditions) == set(range(1, 49)) and len(doe_observations) == 48
    save("released_doe_tables", tables)
    trials, requests = [], []
    for trial in range(1, 49):
        torque = read_fastener_trace(args.root / f"raw_data_torque_test_{trial:02d}t.csv", kind="torque")
        force = read_fastener_trace(args.root / f"raw_data_force_test_{trial:02d}f.csv", kind="force")
        angles_t = [s["angle_deg"] for s in torque["samples"]]
        angles_f = [s["angle_deg"] for s in force["samples"]]
        exact_grid = angles_t == angles_f and all(a is not None for a in angles_t)
        known = [(i, s) for i, s in enumerate(torque["samples"]) if s["valid"]]
        selected = max(known, key=lambda item: item[1]["value_si"]) if known else None
        index, sample = selected if selected else (None, None)
        matching_force = force["samples"][index] if exact_grid and index is not None else None
        metadata = conditions[trial]
        cells = metadata["source_cells"]
        record = _seal(
            dict(
                trial_id=f"fastener_test_{trial:02d}",
                trial_number=trial,
                doe_source_row=metadata["source_row"],
                doe_source_cells=cells,
                doe_headers=header,
                doe_units=units,
                doe_association="equal released Run # and filename test number; chronological order not inferred",
                released_replicate=cells[1],
                factor_group_tokens=cells[2:6],
                nominal_friction_coefficient_is_nut_factor=False,
                angular_speed_source_unit=units[3],
                angular_speed_paper_unit="rpm",
                angular_speed_unit_conflict=True,
                source_nominal_diameter_m=0.01,
                source_thread_pitch_m=0.0015,
                geometry_evidence=dict(source=PAPER, doi=PAPER_DOI, cached_html_sha256=PAPER_SHA256),
                torque=torque,
                force=force,
                force_and_torque_angle_vectors_exactly_equal=exact_grid,
                simultaneous_time_synchronization_established=False,
                pointwise_pairing_rule="exact complete source angle vector equality only; no interpolation",
                selected_torque_rule="largest finite observed signed torque; first occurrence on a tie",
                selected_torque_phase_independently_reviewed=False,
                selected_torque_sample=sample,
                measured_force_at_selected_source_angle=matching_force,
                measured_force_is_model_input=False,
                torque_force_or_angles_resampled=False,
            )
        )
        save(f"trial_{trial:02d}", record)
        context = RunContext(
            asset_id=record["trial_id"],
            scenario_id="recorded_torque_with_unresolved_nut_factor_and_area",
            clock_id=record["trial_id"] + ":angle_only_no_elapsed_clock",
            origin="laboratory",
            evidence=(
                EvidenceRef(
                    id="measurement",
                    citation=source.citation,
                    locator=source.source,
                    origin="laboratory",
                    license_id=source.license_id,
                    sha256=torque["sha256"],
                ),
                EvidenceRef(
                    id="force_reference",
                    citation=source.citation + "; independently retained force trace",
                    locator=source.source,
                    origin="laboratory",
                    license_id=source.license_id,
                    sha256=force["sha256"],
                ),
                EvidenceRef(
                    id="paper_geometry",
                    citation="Ferreira et al., Data in Brief 52, 110022; " + PAPER_DOI,
                    locator=PAPER,
                    origin="literature",
                    license_id="CC-BY-4.0",
                    sha256=PAPER_SHA256,
                ),
            ),
            metadata=dict(
                source_trial_digest=record["record_digest"],
                source_coordinate="angle_deg; no measured time",
                nut_factor_and_effective_tensile_area_not_supplied=True,
            ),
        )
        request = FabricationRequest(
            method="fabrication.bolt_preload",
            asset_id=context.asset_id,
            location_id=record["trial_id"],
            clock_id=context.clock_id,
            material_condition_id="original_DOE_condition_unqualified",
            parameters=dict(
                torque_N_m=None if sample is None else sample["value_si"],
                nominal_diameter_m=0.01,
                nut_factor=None,
                tensile_area_m2=None,
            ),
            input_units=dict(torque_N_m="N*m", nominal_diameter_m="m", nut_factor="1", tensile_area_m2="m2"),
            applicability_reviews={},
            review_evidence_ids={},
            parameter_evidence_ids=dict(torque_N_m="measurement", nominal_diameter_m="paper_geometry"),
            evidence_ids=("measurement", "force_reference", "paper_geometry"),
        )
        result = evaluate_fabrication(request, context)
        result.check_integrity()
        assert result.status == "needs_data" and result.native_receipt is None and not result.values
        save(f"trial_{trial:02d}_withheld_preload", result.model_dump(mode="json"))
        trials.append(record)
        requests.append(result)
    after = revision(args.materials)
    assert before == after and files == _verified_assets(args.root, source)
    factor_groups = Counter(tuple(t["factor_group_tokens"]) for t in trials)
    summary = dict(
        schema="pipeline_integrity.fastener_source_review.v1",
        status="source_reviewed_model_inputs_withheld",
        source=dict(
            dataset_id=source.id,
            source=source.source,
            citation=source.citation,
            creators=list(source.creators),
            version=source.version,
            license_id=source.license_id,
            rights_evidence_url=source.rights_evidence_url,
            dataset_doi="10.17632/jffwrb4pcd.4",
            data_article_doi=PAPER_DOI,
            source_metadata_directory="artifacts/source_metadata/fastener_clamping",
            selected_assets=files,
            acquired_asset_count=len(files),
            acquired_bytes=sum(v["bytes"] for v in files.values()),
            plot_assets_selected=False,
            external_mat_dataset_files_copied=False,
        ),
        physical_trials=len(trials),
        torque_traces=len(trials),
        force_traces=len(trials),
        torque_rows=sum(t["torque"]["summary"]["rows"] for t in trials),
        force_rows=sum(t["force"]["summary"]["rows"] for t in trials),
        invalid_or_missing_torque_rows=sum(t["torque"]["summary"]["invalid_or_missing_rows"] for t in trials),
        invalid_or_missing_force_rows=sum(t["force"]["summary"]["invalid_or_missing_rows"] for t in trials),
        negative_torque_observations_preserved=sum(t["torque"]["summary"]["negative_values"] for t in trials),
        negative_force_observations_preserved=sum(t["force"]["summary"]["negative_values"] for t in trials),
        exact_complete_source_angle_grid_pairs=sum(
            t["force_and_torque_angle_vectors_exactly_equal"] for t in trials
        ),
        unmatched_source_angle_grid_pairs=sum(
            not t["force_and_torque_angle_vectors_exactly_equal"] for t in trials
        ),
        angle_traces_not_strictly_increasing=sum(
            not t[k]["summary"]["angle_strictly_increasing"] for t in trials for k in ("torque", "force")
        ),
        factor_groups=len(factor_groups),
        factor_group_replication_counts=sorted(factor_groups.values()),
        released_replicates=dict(Counter(t["released_replicate"] for t in trials)),
        doe_header_rows_preserved=True,
        blank_doe_metadata_source_rows_preserved=len(doe[2:]) - len(doe_observations),
        doe_tables_are_experimental_metadata_not_standard_limits=True,
        source_coordinate_unit="deg",
        source_time_axis_available=False,
        source_force_unit="kN",
        force_to_N_scale=1000,
        source_torque_unit="Nm",
        torque_model_unit="N*m",
        source_nominal_diameter_m=0.01,
        source_thread_pitch_m=0.0015,
        geometry_source=PAPER,
        geometry_cached_html_sha256=PAPER_SHA256,
        angular_speed_csv_unit="rad/s",
        angular_speed_article_prose_unit="rpm",
        angular_speed_units_resolved=False,
        elapsed_time_synthesized=False,
        force_torque_interpolation_or_smoothing_performed=False,
        time_synchronization_independently_established=False,
        native_model="fabrication.bolt_preload",
        native_model_computed_cases=0,
        unresolved_K_or_At_cases=len(requests),
        unresolved_status_counts=dict(Counter(r.status for r in requests)),
        withheld_reasons=sorted({issue for r in requests for issue in r.issues}),
        measured_force_used_to_infer_nut_factor_or_area=False,
        friction_coefficient_substituted_for_nut_factor=False,
        native_preload_model_calibrated_or_independently_validated=False,
        tightened_joint_capacity_or_field_qualification=False,
        original_source_rows_or_transformed_rows_in_public_summary=False,
        external_material_source_before=before,
        external_material_source_after=after,
        external_material_source_unchanged=True,
        script_sha256=file_digest(Path(__file__)),
        fabrication_exchange_sha256=file_digest(Path("src/pipeline_integrity/fabrication_exchange.py")),
    )
    Path("research/fastener_source_results.json").write_text(
        json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status=summary["status"],
                assets=len(files),
                bytes=summary["source"]["acquired_bytes"],
                trials=len(trials),
                torque_rows=summary["torque_rows"],
                force_rows=summary["force_rows"],
                exact_angle_pairs=summary["exact_complete_source_angle_grid_pairs"],
                unmatched_angle_pairs=summary["unmatched_source_angle_grid_pairs"],
                withheld_preload_cases=len(requests),
                angular_speed_unit_conflict=True,
                external_material_source_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    main()
