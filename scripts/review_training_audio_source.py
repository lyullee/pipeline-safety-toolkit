"""Explicit selected-member HPC study driver; not executed during implementation."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.audio_data import read_training_audio
from pipeline_integrity.audio_study import (
    REVIEWS,
    AudioBand,
    AudioStudyRequest,
    boundary_metadata,
    evaluate_audio_clip,
    runtime_metadata,
    signal_origin_for_audio_source,
    verify_audio_study_record,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext


def main():
    parser = argparse.ArgumentParser(
        description="One selected archive member; no automatic labels, splits or detector training"
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--tar", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--frame-count", type=int, required=True)
    parser.add_argument("--nperseg", type=int, required=True)
    parser.add_argument("--noverlap", type=int, required=True)
    parser.add_argument("--maximum-source-frames", type=int, default=100000)
    parser.add_argument("--window", choices=("hann", "boxcar"), required=True)
    parser.add_argument("--detrend", choices=("constant", "none"), required=True)
    parser.add_argument("--band", action="append", default=[], help="ID:LOW_HZ:HIGH_HZ")
    parser.add_argument("--execute-numerics", action="store_true")
    args = parser.parse_args()
    bands = []
    for item in args.band:
        name, low, high = item.split(":")
        bands.append(AudioBand(id=name, lower_Hz=float(low), upper_Hz=float(high)))
    q = AudioStudyRequest(
        id="selected_training_audio",
        asset_id="caller_named_audio_collection",
        clock_id="file_relative_sample_s",
        archive_filename=args.archive,
        member=args.member,
        start_frame=args.start_frame,
        frame_count=args.frame_count,
        maximum_source_frames=args.maximum_source_frames,
        window=args.window,
        nperseg=args.nperseg,
        noverlap=args.noverlap,
        detrend=args.detrend,
        bands=tuple(bands),
        evidence_id="publisher_source",
    )
    if not args.execute_numerics:
        result = {
            "status": "prepared_not_executed",
            "request": q.model_dump(mode="json"),
            "note": "no archive read, signal analysis, training or independent split executed",
        }
    else:
        root = Path(args.source_root).resolve(strict=True)
        archive = root / q.archive_filename
        if archive.is_symlink() or not archive.resolve(strict=True).is_relative_to(root):
            raise ValueError("selected archive leaves source root")
        source = read_training_audio(
            archive, q.member, tar_executable=args.tar, maximum_frames=q.maximum_source_frames
        )
        q = q.model_copy(
            update={
                "expected_source_record_digest": source["record_digest"],
                "runtime_pins": runtime_metadata(),
                "reviews": dict.fromkeys(REVIEWS, True),
                "review_evidence_ids": dict.fromkeys(REVIEWS, "publisher_source"),
            }
        )
        c = RunContext(
            asset_id=q.asset_id,
            scenario_id="single_clip_diagnostic",
            clock_id=q.clock_id,
            origin=signal_origin_for_audio_source(source),
            evidence=(
                EvidenceRef(
                    id="publisher_source",
                    citation=source["citation"],
                    origin=signal_origin_for_audio_source(source),
                    sha256=source["archive_sha256"],
                    license_id=source["license_id"],
                    limitations=(
                        "Publisher folder labels are not independent experiment groups or amplitude calibration",
                    ),
                ),
            ),
            metadata={"audio_study_boundary": boundary_metadata(q)},
        )
        result = evaluate_audio_clip(q, c, source_root=root, tar_executable=args.tar)
        verify_audio_study_record(result, source_root=root, tar_executable=args.tar, request=q, context=c)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
