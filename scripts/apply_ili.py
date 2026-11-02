"""Scan open ILI workbooks, propose registered correspondences, and verify a synthetic material handoff."""

import argparse
import hashlib
import inspect
import json
import math
import sys
from importlib.metadata import version
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.ili import ILILoadRequest, load_ili_run
from pipeline_integrity.inspection import (
    CorrespondenceReview,
    FeatureMatchPolicy,
    InspectionFeature,
    InspectionRegistration,
    InspectionRun,
    InspectionTrack,
    evaluate_inspection_track,
    match_inspections,
)


def synthetic_reference(materials):
    sys.path.insert(0, str((materials / "src").resolve()))
    runs = []
    for i in range(3):
        runs.append(
            InspectionRun(
                run_id=f"synthetic-{i}",
                asset_id="synthetic_line",
                coordinate_frame_id=f"synthetic_frame_{i}",
                source_id="declared_synthetic_reference",
                source_sha256=digest({"synthetic_index": i}),
                reported_time_label=str(i),
                inspection_time_s=i * 31557600.0,
                clock_id="synthetic_year_seconds",
                time_basis_evidence_id="declared_365_25_day_year",
                origin="synthetic",
                source_scan_complete=True,
                features=(
                    InspectionFeature(
                        feature_id=f"known_pit_{i}",
                        joint_id="synthetic_joint",
                        joint_length_m=10.0,
                        axial_position_m=2.0 + i * 0.03,
                        circumferential_angle_rad=(6.27 + i * 0.03) % (2 * math.pi),
                        nominal_wall_m=0.008,
                        maximum_depth_m=0.001 + i * 0.0002,
                        remaining_ligament_error_bound_m=0.00005,
                        source_row=2,
                    ),
                ),
            )
        )
    matches, reviews = [], []
    policy = FeatureMatchPolicy(axial_gate_m=0.1, angular_gate_rad=0.1, unmatched_cost=2.5, ambiguity_gap=0.1)
    for a, b in zip(runs, runs[1:], strict=False):
        registration = InspectionRegistration(
            registration_id="synthetic_known_registration",
            baseline_run_id=a.run_id,
            followup_run_id=b.run_id,
            baseline_frame_id=a.coordinate_frame_id,
            followup_frame_id=b.coordinate_frame_id,
            joint_map={"synthetic_joint": "synthetic_joint"},
            axial_mode="joint_distance",
            axial_offset_m=0.03,
            circumferential_offset_rad=0.03,
            provenance="declared_synthetic_coordinate_change",
            review_evidence_id="synthetic_ground_truth",
        )
        matched = match_inspections(a, b, registration, policy)
        matches.append(matched)
        reviews.append(
            CorrespondenceReview(
                match_result_digest=matched.result_digest,
                baseline_feature_id=a.features[0].feature_id,
                followup_feature_id=b.features[0].feature_id,
                reviewer_id="synthetic_truth_fixture",
                evidence_id="declared_synthetic_identity",
                approved=True,
            )
        )
    track = InspectionTrack(
        track_id="synthetic_known_pit",
        feature_id_by_run={r.run_id: r.features[0].feature_id for r in runs},
        reviews=tuple(reviews),
    )
    client = MaterialClient()
    names = client.models()
    schema = client.describe("corrosion.thickness_trend")
    assert "corrosion.thickness_trend" in names and schema["model_id"] == "corrosion.thickness_trend"
    result = evaluate_inspection_track(
        track,
        tuple(runs),
        tuple(matches),
        client,
        RunContext(
            asset_id="synthetic_line",
            scenario_id="analytic_linear_loss",
            clock_id="synthetic_year_seconds",
            origin="synthetic",
        ),
    )
    assert result["status"] == "computed"
    expected = 0.0002 / 31557600.0
    rate = result["external_result"]["payload"]["values"]["rate_m_s"]
    assert math.isclose(rate, expected, rel_tol=1e-10, abs_tol=1e-18)
    return {
        "owner_source": revision(materials),
        "expected_rate_m_s": expected,
        "actual_result": result,
        "owner_model_discovery_count": len(names),
        "owner_model_ids": list(names),
        "trend_input_schema_checked": True,
        "model_discovery_is_not_all_model_validation": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=Path("data/raw/ili/ili-v1.zip"))
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--joints", nargs="+", default=["14"])
    parser.add_argument("--maximum-source-rows", type=int)
    parser.add_argument("--reuse-scans", action="store_true")
    args = parser.parse_args()
    local = Path("artifacts/ili_application")
    local.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256()
    with args.archive.open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            fingerprint.update(block)
    archive_digest = fingerprint.hexdigest()
    runs, scans = [], []
    for label, word in (("1", "One"), ("3", "Three"), ("5", "Five"), ("7", "Seven")):
        member = f"Dataset (.xlsx)/External Features Year {word}.xlsx"
        request = ILILoadRequest(
            run_id=f"ili_label_{label}",
            asset_id="anonymized_ili_line",
            coordinate_frame_id=f"joint_start_instrument_clock_label_{label}",
            source_id="doi:10.17632/c2h2jf5c54.1",
            reported_time_label=label,
            joint_ids=tuple(args.joints),
            maximum_source_rows=args.maximum_source_rows,
        )
        cache_key = digest(
            {
                "archive": archive_digest,
                "request": request,
                "member": member,
                "loader_code": hashlib.sha256(Path(inspect.getfile(load_ili_run)).read_bytes()).hexdigest(),
                "run_schema": InspectionRun.model_json_schema(),
                "openpyxl": version("openpyxl"),
            }
        )
        cache = local / f"scan_{label}.json"
        cached = (
            json.loads(cache.read_text(encoding="utf-8")) if args.reuse_scans and cache.is_file() else None
        )
        if (
            cached is not None
            and cached.get("cache_key") == cache_key
            and cached.get("payload_digest")
            == digest({key: value for key, value in cached.items() if key != "payload_digest"})
        ):
            run = InspectionRun.model_validate_json(json.dumps(cached["run"]))
            report = cached["report"]
            print(json.dumps({"run_id": run.run_id, "verified_scan_cache": True}), flush=True)
        else:

            def progress(record):
                (local / "progress.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
                print(json.dumps(record), flush=True)

            run, report = load_ili_run(args.archive, member, request, on_progress=progress)
            body = {"cache_key": cache_key, "run": run.model_dump(mode="json"), "report": report}
            cache.write_text(
                json.dumps({**body, "payload_digest": digest(body)}, indent=2) + "\n", encoding="utf-8"
            )
        runs.append(run)
        scans.append(report)
        print(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "complete": run.source_scan_complete,
                    "rows": report["actual_source_rows_scanned"],
                }
            ),
            flush=True,
        )
    matches, summaries, candidate_growth = [], [], None
    policy = FeatureMatchPolicy(
        axial_gate_m=0.25, angular_gate_rad=math.radians(20), unmatched_cost=2.5, ambiguity_gap=0.1
    )
    if all(run.source_scan_complete for run in runs):
        for a, b in zip(runs, runs[1:], strict=False):
            registration = InspectionRegistration(
                registration_id=f"candidate_{a.run_id}_{b.run_id}",
                baseline_run_id=a.run_id,
                followup_run_id=b.run_id,
                baseline_frame_id=a.coordinate_frame_id,
                followup_frame_id=b.coordinate_frame_id,
                joint_map={joint: joint for joint in args.joints},
                axial_mode="fraction_of_joint_length",
                axial_offset_m=0.0,
                circumferential_offset_rad=0.0,
                provenance="unreviewed_joint_fraction_and_clock_angle_assumption",
            )
            matched = match_inspections(a, b, registration, policy)
            matches.append(matched)
            summaries.append(
                {
                    "baseline_run_id": a.run_id,
                    "followup_run_id": b.run_id,
                    "registration": registration.model_dump(mode="json"),
                    "policy": policy.model_dump(mode="json"),
                    "candidate_edges": matched.candidate_edges,
                    "selected_proposals": len(matched.proposals),
                    "ambiguous_proposals": sum(p.ambiguous for p in matched.proposals),
                    "unmatched_baseline_count": len(matched.unmatched_baseline_ids),
                    "unmatched_followup_count": len(matched.unmatched_followup_ids),
                    "result_digest": matched.result_digest,
                }
            )
        maps = [{p.baseline_feature_id: p.followup_feature_id for p in m.proposals} for m in matches]
        for feature in runs[0].features:
            chain = [feature.feature_id]
            for mapping in maps:
                if chain[-1] not in mapping:
                    break
                chain.append(mapping[chain[-1]])
            if len(chain) == len(runs):
                track = InspectionTrack(
                    track_id="unreviewed_candidate_track",
                    feature_id_by_run=dict(zip([r.run_id for r in runs], chain, strict=True)),
                    reviews=(),
                )
                held = evaluate_inspection_track(
                    track,
                    tuple(runs),
                    tuple(matches),
                    None,
                    RunContext(
                        asset_id=runs[0].asset_id,
                        scenario_id="unreviewed_correspondence",
                        clock_id="unassigned_inspection_time",
                        origin="measured",
                    ),
                )
                assert held["status"] == "needs_data"
                candidate_growth = {
                    "status": held["status"],
                    "issues": held["issues"],
                    "material_growth_called": False,
                }
                (local / "candidate_track.json").write_text(
                    json.dumps(held, indent=2) + "\n", encoding="utf-8"
                )
                break
        (local / "matches.json").write_text(
            json.dumps([m.model_dump(mode="json") for m in matches], indent=2) + "\n", encoding="utf-8"
        )
    result = {
        "schema": "pipeline_integrity.ili_application.v1",
        "source": "https://doi.org/10.17632/c2h2jf5c54.1",
        "license_id": "CC-BY-4.0",
        "creators": ["Rioshar Yarveisy", "Faisal Khan", "Rouzbeh Abbassi"],
        "scan_reports": scans,
        "selected_joints": args.joints,
        "matches": summaries,
        "matching_implementation": (
            {
                "algorithm_version": matches[0].algorithm_version,
                "implementation_sha256": matches[0].implementation_sha256,
                "runtime_versions": matches[0].runtime_versions,
            }
            if matches
            else None
        ),
        "application_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidate_growth": candidate_growth,
        "synthetic_handoff": synthetic_reference(args.materials),
        "runtime_versions": {name: version(name) for name in ("numpy", "scipy", "pydantic", "openpyxl")},
        "full_source_scans_complete": all(run.source_scan_complete for run in runs),
        "independently_validated_real_defect_correspondences": False,
        "field_corrosion_rate_qualified": False,
        "raw_or_transformed_feature_rows_in_public_summary": False,
    }
    target = (
        Path("research/ili_application.json")
        if result["full_source_scans_complete"]
        else local / "partial_application.json"
    )
    target.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "full_source_scans_complete": result["full_source_scans_complete"],
                "selected_counts": [len(r.features) for r in runs],
                "proposal_counts": [len(m.proposals) for m in matches],
                "actual_field_growth_computed": False,
                "synthetic_handoff": result["synthetic_handoff"]["actual_result"]["status"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
