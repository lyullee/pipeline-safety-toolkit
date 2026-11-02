"""Read two pinned small sources and audit what their observations can support."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, digest
from pipeline_integrity.corrosion_data import read_afm_steel_wear, read_copper_wire
from pipeline_integrity.datasets import file_digest

AFM_METHOD_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8336757/"
AFM_METHOD_SHA256 = "9647a9f01a2b6655912204c9b7f0e769558bb114772d24fd96530f223459b811"
COPPER_METHOD_URL = "https://doi.org/10.1186/s40494-024-01212-6"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _boundaries(copper, afm, *, materials, owner_sha256):
    # The owner adapter is imported only after the caller has selected the reviewed owner snapshot.
    from pipeline_integrity.contracts import RunContext
    from pipeline_integrity.corrosion_wear import (
        INPUT_UNITS,
        CorrosionWearRequest,
        evaluate_corrosion_wear,
        verify_corrosion_wear_record,
    )

    requests, contexts = [], []
    copper_evidence = EvidenceRef(
        id="copper-source",
        citation=copper["provenance"]["citation"],
        origin="measured",
        sha256=copper["provenance"]["sha256"],
        license_id=copper["provenance"]["license_id"],
        limitations=("CSV depth unit and physical exposure origin are unresolved.",),
    )
    afm_evidence = EvidenceRef(
        id="afm-source",
        citation=afm["provenance"]["citation"],
        origin="laboratory",
        sha256=afm["provenance"]["sha256"],
        license_id=afm["provenance"]["license_id"],
        limitations=("Scratch hardness and distributed wear area are unresolved.",),
    )
    for method in ("corrosion.power_law", "corrosion.gumbel_area_maximum"):
        context = RunContext(
            asset_id="kbely-copper-sensing-element",
            scenario_id="unresolved-source-conditions",
            clock_id="provider-naive-observation-clock-not-exposure-age",
            origin="measured",
            evidence=(copper_evidence,),
            metadata={"source_record_digest": copper["record_digest"]},
        )
        contexts.append(context)
        requests.append(
            CorrosionWearRequest(
                method=method,
                asset_id=context.asset_id,
                location_id="single-logger-location",
                clock_id=context.clock_id,
                material_condition_id="copper-condition-unresolved",
                environment_or_contact_id="kbely-hangar-observed-environment",
                population_id="one-repeated-copper-sensor-history",
                quantity_meaning="unresolved",
                parameters=None,
                input_units=INPUT_UNITS[method],
                evidence_ids=(copper_evidence.id,),
                owner_source_tree_sha256=owner_sha256,
            )
        )
    for row in afm["observations"]["continuous_load_volume"]:
        context = RunContext(
            asset_id="aisi52100-afm-substrate",
            scenario_id="unresolved-source-conditions",
            clock_id="2000-directional-passes-no-physical-age-clock",
            origin="laboratory",
            evidence=(afm_evidence,),
            metadata={
                "source_record_digest": afm["record_digest"],
                "source_row": row["source_row"],
                "observed_normal_force_N": row["normal_force_N"],
                "reviewed_sliding_distance_m": row["sliding_distance_m"],
                "reported_removed_volume_m3": row["reported_removed_volume_m3"],
                "matched_hardness_Pa": None,
                "distributed_area_m2": None,
            },
        )
        contexts.append(context)
        requests.append(
            CorrosionWearRequest(
                method="degradation.archard_wear",
                asset_id=context.asset_id,
                location_id="source-row-" + str(row["source_row"]),
                clock_id=context.clock_id,
                material_condition_id="aisi52100-nanoscratch-condition",
                environment_or_contact_id="afm-tip-substrate-contact",
                population_id="unresolved-physical-scratch-identities",
                quantity_meaning="wear_volume",
                parameters=None,
                input_units=INPUT_UNITS["degradation.archard_wear"],
                evidence_ids=(afm_evidence.id,),
                owner_source_tree_sha256=owner_sha256,
            )
        )
    records = [
        evaluate_corrosion_wear(request, context, owner_source_root=materials)
        for request, context in zip(requests, contexts, strict=True)
    ]
    for record in records:
        verify_corrosion_wear_record(record)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("research/corrosion_source_results.json"))
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.materials / "src").resolve()))
    before = revision(args.materials)
    folder = Path("artifacts/corrosion_source")
    method = EvidenceRef(
        id="afm-primary-method",
        citation="Walker et al. (2021), DOI 10.1098/rspa.2021.0103",
        origin="literature",
        locator="Sections 2b and 3e: 5 micrometres per directional pass; 2000 passes",
        sha256=AFM_METHOD_SHA256,
        license_id="CC-BY-4.0",
        limitations=("The figure table does not identify each physical scratch or supply matched hardness.",),
    )
    copper = read_copper_wire(args.raw_root / "copper_wire_corrosion/procraft_corrosion_kbely_data_v02.csv")
    afm = read_afm_steel_wear(
        args.raw_root
        / "afm_steel_wear/Asperity_Level_Characterisation_of_Abrasive_Wear_Using_Atomic_Force_Microscopy_.csv",
        scratch_method_evidence=method,
    )
    assert copper["observations"]["summary"]["rows"] == 9387
    assert afm["observations"]["source_rows"] == 1346
    assert len(afm["observations"]["continuous_load_volume"]) == 12
    save(folder / "copper_observations.json", copper)
    save(folder / "afm_observations.json", afm)
    records = _boundaries(copper, afm, materials=args.materials, owner_sha256=before["source_tree_sha256"])
    assert len(records) == 14 and all(r["status"] == "needs_data" for r in records)
    assert all(r["native_receipt"] is None for r in records)
    for i, record in enumerate(records, start=1):
        save(folder / f"boundary_{i:02d}.json", record)
    after = revision(args.materials)
    assert before == after, "owner source changed during source review"
    specific = [
        r["specific_reported_removed_volume_m3_per_N_m"]
        for r in afm["observations"]["continuous_load_volume"]
    ]
    assert all(value is not None for value in specific)
    assets = [copper["provenance"], afm["provenance"]]
    source_digest = digest([copper["record_digest"], afm["record_digest"]])
    summary = dict(
        schema_version="pipeline_integrity.corrosion_source_review.v1",
        status="source_observations_preserved_model_conditions_need_data",
        assets=assets,
        acquired_files=len(assets),
        acquired_bytes=sum(p["bytes"] for p in assets),
        source_observation_digest=source_digest,
        copper=dict(
            summary=copper["observations"]["summary"],
            source_depth_unit=None,
            depth_to_SI_scale=None,
            physical_exposure_origin=None,
            source_numeric_columns_have_unit_labels=False,
            observation_time_is_physical_exposure_age=False,
            article_methods=COPPER_METHOD_URL,
            article_plotted_depth_unit="nm",
            article_to_csv_depth_scale_explicitly_declared=False,
            provider_differences_retained=True,
            independent_physical_specimens_resolved=None,
            physical_power_law_fit_performed=False,
            inferred_unit_or_zero_clock_used=False,
        ),
        afm=dict(
            summary=afm["observations"]["summary"],
            source_rows=afm["observations"]["source_rows"],
            source_normal_force_unit="microN",
            normal_force_to_N=1e-6,
            source_volume_unit="micron^3",
            volume_to_m3=1e-18,
            source_hardness_load_unit="mN",
            hardness_load_to_N=1e-3,
            source_hardness_unit="Gpa",
            hardness_to_Pa=1e9,
            reviewed_directional_pass_distance_m=5e-6,
            reviewed_Figure10_passes=2000,
            reviewed_Figure10_sliding_distance_m=0.01,
            specific_reported_removed_volume_units="m^3/(N*m)",
            specific_reported_removed_volume_range=[min(specific), max(specific)],
            specific_volume_is_dimensionless_archard_coefficient=False,
            reported_removed_and_pile_up_volumes_kept_distinct=True,
            matched_scratch_hardness_Pa=None,
            distributed_wear_area_m2=None,
            physical_scratch_identity_resolved=False,
            nanoscale_to_pipeline_contact_transfer_reviewed=False,
            primary_method=AFM_METHOD_URL,
            primary_method_cached_sha256=AFM_METHOD_SHA256,
        ),
        requested_model_boundaries=len(records),
        boundary_status_counts=dict(Counter(record["status"] for record in records)),
        owner_native_model_calls=0,
        physical_source_calibration_performed=False,
        independent_experimental_validation_performed=False,
        pit_equal_area_block_maxima_available=False,
        known_flaw_hit_miss_and_blank_trials_available=False,
        period_failure_probability_computed=False,
        source_rows_transformed_rows_or_detailed_native_receipts_in_public_summary=False,
        external_material_source_before=before,
        external_material_source_after=after,
        external_material_source_unchanged=True,
        script_sha256=file_digest(Path(__file__)),
        reader_sha256=file_digest(Path("src/pipeline_integrity/corrosion_data.py")),
        limitations=[
            "Copper CSV loss units and exposure zero are unresolved; row chronology is preserved without physical power-law fitting.",
            "AFM dimensional specific volume is descriptive; hardness, wear footprint and specimen identities do not qualify native Archard transfer.",
            "No equal-area pit maxima, independent hit/miss flaw truth or blank trials support Gumbel or POD.",
            "Repeated sensor hours, passes and averaged figure rows are not independent experimental specimens.",
        ],
    )
    save(args.output, summary)
    print(
        json.dumps(
            dict(
                status=summary["status"],
                files=len(assets),
                bytes=summary["acquired_bytes"],
                copper_rows=9387,
                afm_rows=1346,
                afm_specific_volume_rows=12,
                model_status_counts=summary["boundary_status_counts"],
                owner_source_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    main()
