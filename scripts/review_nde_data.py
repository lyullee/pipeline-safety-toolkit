"""Audit the acquired NDE source archives and run explicitly hypothetical echo interpretations in the native engine."""

import argparse
import collections
import json
import math
import sys
import zipfile
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.nde import (
    EchoThicknessRequest,
    evaluate_echo_thickness,
    load_tubers_waveform,
    tubers_visual_pairs,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/tubers_nde"))
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    import matintegrity

    spec = next(x for x in catalog() if x.id == "tubers_nde")
    sources = {}
    for asset in spec.assets:
        path = args.raw_root / asset.filename
        algorithm, expected = asset.checksum.split(":", 1)
        assert path.stat().st_size == asset.bytes and file_digest(path, algorithm) == expected
        sources[asset.filename] = {
            "sha256": file_digest(path),
            "bytes": path.stat().st_size,
            "publisher_checksum": asset.checksum,
            "publisher_checksum_verified": True,
        }
    archive = args.raw_root / "Ultrasonic Data.zip"
    archive_sha = sources[archive.name]["sha256"]
    with zipfile.ZipFile(archive) as z:
        members = sorted(n for n in z.namelist() if n.endswith(".csv"))
    reports = []
    groups = collections.defaultdict(
        lambda: {
            "traces": 0,
            "samples": 0,
            "missing_times": 0,
            "missing_voltages": 0,
            "negative_native_times": 0,
            "nonincreasing_adjacent_times": 0,
        }
    )
    for member in members:
        trace = load_tubers_waveform(archive, member, expected_archive_sha256=archive_sha)
        audit = trace.audit()
        reports.append(
            {
                "member": member,
                "waveform_digest": trace.waveform_digest,
                "member_sha256": trace.member_sha256,
                **audit,
            }
        )
        group = groups[trace.source_group_label]
        group["traces"] += 1
        for key in list(group)[1:]:
            group[key] += audit[key]
    root = Path("artifacts/nde_data")
    root.mkdir(parents=True, exist_ok=True)
    (root / "trace_reports.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    visual = tubers_visual_pairs(
        args.raw_root / "Visual Data.zip", expected_archive_sha256=sources["Visual Data.zip"]["sha256"]
    )
    (root / "visual_correspondence.json").write_text(json.dumps(visual, indent=2) + "\n", encoding="utf-8")
    assert len(reports) == 231 and len(visual["pairs"]) == 318 and len(visual["masks_without_images"]) == 7
    assert visual["status"] == "needs_data"

    trace = load_tubers_waveform(
        archive, "Ultrasonic Data/Plate_1/10MHz/scope_29.csv", expected_archive_sha256=archive_sha
    )
    context = RunContext(
        asset_id="source_echo_interpretation_demo",
        scenario_id="authored_calibration_assumptions",
        clock_id=trace.native_clock_id,
        origin="user_declared",
        evidence=(
            EvidenceRef(
                id="source",
                citation=spec.citation,
                locator=spec.source,
                origin="user_declared",
                sha256=archive_sha,
                license_id=spec.license_id,
            ),
            EvidenceRef(
                id="assumption",
                citation="Authored echo indices, wave speed, angle and time-zero hypothesis",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    initial = EchoThicknessRequest(
        waveform_digest=trace.waveform_digest,
        reference_index=0,
        return_index=200,
        sound_speed_m_s=5900.0,
        time_zero_offset_s=0.0,
        incidence_angle_rad=0.0,
        source_evidence_id="source",
        echo_review_evidence_id="assumption",
        calibration_evidence_id="assumption",
    )
    cases = {}
    for key, changes in [
        ("reference", {}),
        ("time_zero", {"time_zero_offset_s": 2e-7}),
        ("oblique", {"incidence_angle_rad": math.pi / 3}),
        ("offset_consumes_flight", {"time_zero_offset_s": 2e-6}),
        ("missing_tail", {"return_index": len(trace.time_s) - 1}),
    ]:
        request = initial.model_copy(update=changes)
        result = evaluate_echo_thickness(trace, request, context)
        assert result["result_digest"] == digest({k: v for k, v in result.items() if k != "result_digest"})
        if key in {"reference", "time_zero", "oblique"}:
            expected = (1e-6 - request.time_zero_offset_s) * 5900 * math.cos(request.incidence_angle_rad) / 2
            assert result["status"] == "exchange_checked" and math.isclose(
                result["thickness_m"], expected, abs_tol=1e-15
            )
        elif key == "missing_tail":
            assert result["status"] == "needs_data" and result["native_result"] is None
        else:
            assert result["status"] == "withheld" and result["native_result"]["status"] == "out_of_scope"
        path = root / f"echo_{key}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        cases[key] = {
            "status": result["status"],
            "thickness_m": result["thickness_m"],
            "native_status": None if result["native_result"] is None else result["native_result"]["status"],
            "issues": result["issues"],
            "record_sha256": file_digest(path),
        }
    assert before == revision(args.materials), "the owned material source was modified"
    summary = {
        "schema": "pipeline_integrity.nde_data_review.v1",
        "source": spec.source,
        "license_id": spec.license_id,
        "creators": list(spec.creators),
        "sources": sources,
        "waveform_groups": dict(groups),
        "waveforms": len(reports),
        "native_header": {"time": "second", "signal": "Volt"},
        "native_zero_reference": "not_documented",
        "source_rows_or_signed_times_repaired": False,
        "visual": {
            "status": visual["status"],
            "matched_pairs": len(visual["pairs"]),
            "orphan_masks": len(visual["masks_without_images"]),
            "orphan_images": len(visual["images_without_masks"]),
            "pair_dimensions_px": sorted({(p["width_px"], p["height_px"]) for p in visual["pairs"]}),
            "physical_pixel_scale_known": False,
            "mask_class_values_reviewed": False,
        },
        "detection_trial_fit": {
            "status": "needs_data",
            "independent_truth_miss_or_blank_trials_found": False,
            "known_physical_size_and_procedure_calibration_found": False,
            "file_count_is_not_trial_count": True,
        },
        "echo_cases": cases,
        "echo_picks_speed_and_calibration_are_assumptions": True,
        "field_thickness_validated": False,
        "materials": before | {"version": matintegrity.__version__},
        "module_sha256": file_digest(Path("src/pipeline_integrity/nde.py")),
        "script_sha256": file_digest(Path(__file__)),
        "trace_report_sha256": file_digest(root / "trace_reports.json"),
        "visual_report_sha256": file_digest(root / "visual_correspondence.json"),
    }
    Path("research/nde_data_review.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "waveforms": len(reports),
                "matched_visual_pairs": len(visual["pairs"]),
                "orphan_masks": len(visual["masks_without_images"]),
                "echo_cases": len(cases),
                "detection_trial_fit": "needs_data",
            }
        )
    )


if __name__ == "__main__":
    main()
