"""Review two pinned small experimental sources without inferring missing material inputs."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_process_data import (
    AHSS_FILENAME,
    AHSS_ID,
    BAM_ID,
    read_ahss_permeation,
    read_bam_charpy,
    verify_material_process_data,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def review_sources(raw_root):
    bam = read_bam_charpy(raw_root / BAM_ID)
    ahss = read_ahss_permeation(raw_root / AHSS_ID / AHSS_FILENAME)
    for record in (bam, ahss):
        verify_material_process_data(record)
    assert bam["summary"]["physical_specimens"] == 6
    assert bam["summary"]["unbroken_specimens"] == 6
    assert ahss["observations"]["summary"]["rows"] == 1859
    assert ahss["observations"]["summary"]["histories"] == 2
    assert all(
        h["summary"]["missing_or_invalid_numeric_cells"] == 0 for h in ahss["observations"]["histories"]
    )
    archive = bam["provenance"]
    archive_asset = dict(
        dataset_id=BAM_ID,
        filename=archive["archive_filename"],
        bytes=archive["archive_bytes"],
        sha256=archive["archive_sha256"],
        supplier_checksum=archive["supplier_checksum"],
        supplier_checksum_verified=archive["supplier_checksum_verified"],
        checksum_origin=archive["checksum_origin"],
        citation=archive["citation"],
        source_version=archive["source_version"],
        license_id=archive["license_id"],
    )
    assets = [archive_asset, *bam["supporting_assets"], ahss["provenance"]]
    residuals = [c["primary_minus_secondary_energy_J"] for c in bam["secondary"]["comparisons"]]
    dimension_residuals = [
        value for c in bam["secondary"]["comparisons"] for value in c["dimension_residuals_m"]
    ]
    assert all(value == 0.0 for value in dimension_residuals)
    assert max(abs(value) for value in residuals) <= 0.05 + 1e-12
    summary = dict(
        schema_version="pipeline_integrity.material_process_data_review.v1",
        status="source_observations_preserved_model_conditions_need_data",
        assets=assets,
        acquired_groups=2,
        acquired_files=len(assets),
        acquired_bytes=sum(row["bytes"] for row in assets),
        source_observation_digest=digest([bam["record_digest"], ahss["record_digest"]]),
        bam_charpy=dict(
            summary=bam["summary"],
            maximum_secondary_energy_rounding_residual_J=max(abs(value) for value in residuals),
            maximum_dimension_reconciliation_residual_m=max(abs(value) for value in dimension_residuals),
            measured_tested_lot_composition=None,
            manufacturer_declaration_tested_lot_match=None,
            manufacturer_declaration_verified_for_tested_lot=False,
            manufacturer_limits_imported=False,
            transition_parameters_fitted=False,
            applicability=bam["applicability"],
        ),
        ahss_permeation=dict(
            summary=ahss["observations"]["summary"],
            history_diagnostics=[
                dict(history_id=h["history_id"], **h["summary"]) for h in ahss["observations"]["histories"]
            ],
            active_area_resolved=False,
            total_current_converted_to_flux=False,
            electrochemical_boundary_converted_to_lattice_concentration=False,
            transport_parameters_fitted=False,
            applicability=ahss["applicability"],
        ),
        model_boundary_review=dict(status="not_executed", native_calls=0, records=0),
        independent_physical_validation=False,
        source_observations_transformed_rows_and_receipts_in_public_summary=False,
        private_artifacts="artifacts/material_process_data",
    )
    assert summary["acquired_files"] == 5 and summary["acquired_bytes"] == 1_620_960
    return bam, ahss, summary


def apply_source_boundaries(bam, ahss, *, owner_source_tree_sha256=None, owner_source_root=None):
    from pipeline_integrity.hydrogen_transport import (
        EQUILIBRIUM,
        KINETIC,
        SEMI,
        HydrogenTransportRequest,
        evaluate_hydrogen_transport,
        verify_hydrogen_transport_record,
    )
    from pipeline_integrity.hydrogen_transport import INPUT_UNITS as HYDROGEN_UNITS
    from pipeline_integrity.material_quality import (
        CHEMISTRY,
        IMPACT,
        INPUT_UNITS,
        PITTING,
        MaterialQualityRequest,
        evaluate_material_quality,
        verify_material_quality_record,
    )

    bam_evidence = EvidenceRef(
        id="bam-source",
        citation=bam["provenance"]["citation"],
        origin="laboratory",
        sha256=bam["provenance"]["archive_sha256"],
        license_id=bam["provenance"]["license_id"],
        limitations=tuple(bam["applicability"]["reasons"]),
    )
    bam_context = RunContext(
        asset_id="bam-one-s355-sheet",
        scenario_id="original-source-conditions",
        clock_id="2021-11-12-charpy-test-event",
        origin="laboratory",
        evidence=(bam_evidence,),
        metadata=dict(
            source_record_digest=bam["record_digest"],
            measured_composition_mass_fraction=None,
            manufacturer_tested_lot_match=None,
            physical_specimens=6,
            test_temperatures=1,
            unbroken_specimens=6,
        ),
    )
    records = []
    for method in (CHEMISTRY, PITTING, IMPACT):
        request = MaterialQualityRequest(
            method=method,
            asset_id=bam_context.asset_id,
            location_id="one-sheet-six-charpy-specimens",
            clock_id=bam_context.clock_id,
            material_condition_id="source-s355j2-plus-n-label",
            population_id="one-source-steel-sheet",
            physical_scope_id="one-source-steel-sheet",
            series_or_assay_id="bam-source-series-without-matched-assay",
            composition_basis={CHEMISTRY: "mass_fraction", PITTING: "mass_percent", IMPACT: "not_applicable"}[
                method
            ],
            parameters=None,
            input_units=INPUT_UNITS[method],
            evidence_ids=(bam_evidence.id,),
            impact_observations_complete=False if method == IMPACT else None,
            owner_source_tree_sha256=owner_source_tree_sha256,
        )
        record = evaluate_material_quality(request, bam_context, owner_source_root=owner_source_root)
        verify_material_quality_record(record)
        records.append(record)
    evidence = EvidenceRef(
        id="ahss-source",
        citation=ahss["provenance"]["citation"],
        origin="laboratory",
        sha256=ahss["provenance"]["sha256"],
        license_id=ahss["provenance"]["license_id"],
        limitations=tuple(ahss["applicability"]["reasons"]),
    )
    for history in ahss["observations"]["histories"]:
        context = RunContext(
            asset_id="ahss-dataset-experiment-unresolved-specimen-identity",
            scenario_id=history["history_id"],
            clock_id="source-sheet-time-" + history["history_id"],
            origin="laboratory",
            evidence=(evidence,),
            metadata=dict(
                source_record_digest=ahss["record_digest"],
                source_history=history["history_id"],
                source_rows=history["summary"]["rows"],
                physical_specimen_id=None,
                effective_permeation_area_m2=None,
                nominal_thickness_m=0.0012,
                source_temperature_K=history["temperature_K"],
                source_total_current_is_flux=False,
                histories_are_independent_specimens=False,
            ),
        )
        for method in (SEMI, EQUILIBRIUM, KINETIC):
            request = HydrogenTransportRequest(
                method=method,
                asset_id=context.asset_id,
                location_id=history["history_id"],
                clock_id=context.clock_id,
                parameters=None,
                input_units=HYDROGEN_UNITS[method],
                evidence_ids=(evidence.id,),
                declared_temperature_K=history["temperature_K"],
                owner_source_tree_sha256=owner_source_tree_sha256,
            )
            record = evaluate_hydrogen_transport(request, context, owner_source_root=owner_source_root)
            verify_hydrogen_transport_record(record)
            records.append(record)
    assert len(records) == 9 and all(row["status"] == "needs_data" for row in records)
    assert all(row["native_receipt"] is None for row in records)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("research/material_process_data_results.json"))
    parser.add_argument("--materials", type=Path)
    args = parser.parse_args()
    bam, ahss, summary = review_sources(args.raw_root)
    folder = Path("artifacts/material_process_data")
    save(folder / "bam_observations.json", bam)
    save(folder / "ahss_observations.json", ahss)
    source_before = None
    if args.materials is not None:
        from pipeline_integrity.material_source import material_source_fingerprint

        source_before = material_source_fingerprint(args.materials)
    records = apply_source_boundaries(
        bam,
        ahss,
        owner_source_tree_sha256=None if source_before is None else source_before["source_tree_sha256"],
        owner_source_root=args.materials,
    )
    for i, record in enumerate(records, start=1):
        save(folder / f"boundary_{i:02d}.json", record)
    source_after = None if source_before is None else material_source_fingerprint(args.materials)
    assert source_before == source_after
    summary["model_boundary_review"] = dict(
        status="passed_original_conditions_withheld",
        records=len(records),
        status_counts={"needs_data": len(records)},
        native_calls=0,
        material_quality_requests=3,
        hydrogen_transport_requests=6,
        source_tree_before=source_before,
        source_tree_after=source_after,
        source_unchanged=source_before == source_after,
    )
    summary["implementation_sha256"] = file_digest(Path("src/pipeline_integrity/material_process_data.py"))
    summary["script_sha256"] = file_digest(Path(__file__))
    save(args.output, summary)
    print(
        json.dumps(
            dict(
                status=summary["status"],
                files=summary["acquired_files"],
                bytes=summary["acquired_bytes"],
                specimens=6,
                permeation_rows=1859,
            )
        )
    )


if __name__ == "__main__":
    main()
