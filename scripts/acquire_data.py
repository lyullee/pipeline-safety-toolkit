"""Acquire explicitly selected source families and retain the complete outcome ledger."""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pipeline_integrity.datasets import (
    acquisition_provenance,
    acquire,
    acquire_asset,
    catalog,
    file_digest,
    inspect_rar,
    require_acquisition_allowed,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", required=True)
    parser.add_argument("--destination", type=Path, default=Path("data/raw"))
    parser.add_argument("--cache-root", action="append", default=[])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--maximum-seconds", type=int, default=180)
    parser.add_argument("--existing-asset", action="append", default=[], metavar="DATASET_ID=PATH")
    args = parser.parse_args()
    folder = Path("artifacts/acquisition")
    folder.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]

    def progress(state):
        (folder / f"{run_id}_in_progress.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(
            json.dumps({"completed_files": len(state["files"]), "failed_files": len(state["failures"])}),
            flush=True,
        )

    existing = {}
    for item in args.existing_asset:
        identity, separator, path = item.partition("=")
        if not separator or not identity or not path or identity in existing:
            parser.error("--existing-asset must give unique DATASET_ID=PATH mappings")
        existing[identity] = Path(path)
    if set(existing) - set(args.only):
        parser.error("--existing-asset IDs must be selected by --only")
    if existing:
        selected = {spec.id: spec for spec in catalog() if spec.id in args.only}
        if len(selected) != len(args.only):
            parser.error("--only contains an unknown dataset")
        if len(existing) != len(args.only):
            parser.error("for this acquisition call, provide --existing-asset for every selected dataset")
        records, failures = [], []
        for identity in args.only:
            spec = selected[identity]
            require_acquisition_allowed(spec)
            assets = [asset for asset in spec.assets if asset.default]
            if len(assets) != 1:
                parser.error("existing-asset mode currently requires exactly one selected asset per dataset")
            source = existing[identity]
            target = args.destination / identity / assets[0].filename
            asset = assets[0]
            if source.is_symlink() or not source.is_file():
                raise SystemExit("existing source must be a regular non-symlink file")
            if asset.bytes is None or source.stat().st_size != asset.bytes:
                raise SystemExit("existing source size differs from publisher metadata")
            algorithm, expected = asset.checksum.split(":", 1)
            if file_digest(source, algorithm) != expected:
                raise SystemExit("existing source checksum differs from publisher metadata")
            source_sha256 = file_digest(source, "sha256")
            if source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.stat().st_size != asset.bytes or file_digest(target, algorithm) != expected:
                        raise SystemExit(f"refusing to overwrite mismatched acquired target: {target}")
                    method = "existing_verified_user_copy"
                else:
                    shutil.copy2(source, target)
                    if target.stat().st_size != asset.bytes or file_digest(target, algorithm) != expected:
                        raise SystemExit("copied source failed post-copy size/checksum verification")
                    method = "verified_user_file_copy"
            else:
                method = "existing"
            members = None
            member_inventory = "not_applicable"
            if asset.filename.lower().endswith(".rar"):
                try:
                    members = inspect_rar(target)
                    member_inventory = "listed_no_extraction"
                except FileNotFoundError:
                    member_inventory = "pending_listing_tool"
            result = {
                "dataset_id": identity,
                "filename": asset.filename,
                "bytes": asset.bytes,
                "sha256": source_sha256,
                "expected_checksum": asset.checksum,
                "checksum_origin": asset.checksum_origin,
                "expected_checksum_verified": True,
                "supplier_checksum_verified": asset.checksum_origin == "publisher_metadata",
                "method": method,
                "source_url": asset.url,
                "members": members,
                "member_inventory_status": member_inventory,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "raw_in_code_release": False,
                **acquisition_provenance(spec, asset),
            }
            records.append(result)
        report = {"schema": "pipeline_integrity.acquisition.v1", "files": records, "failures": failures, "complete": True}
    else:
        report = acquire(
            args.only,
            args.destination,
            cache_roots=args.cache_root,
            workers=args.workers,
            maximum_seconds=args.maximum_seconds,
            progress=progress,
        )
    (folder / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (folder / f"{run_id}_complete.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for spec in catalog():
        if spec.id not in args.only:
            continue
        files = [row for row in report["files"] if row["dataset_id"] == spec.id]
        failures = [row for row in report["failures"] if row["dataset_id"] == spec.id]
        group = {
            "dataset_id": spec.id,
            "license_id": spec.license_id,
            "files": files,
            "failures": failures,
            "complete": not failures and bool(files),
        }
        if existing.get(spec.id):
            group["member_inventory_status"] = next(
                (
                    "listed_no_extraction" if row.get("members") is not None else "pending_listing_tool"
                    for row in files
                ),
                "not_created",
            )
        (folder / f"{spec.id}.json").write_text(json.dumps(group, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "id": spec.id,
                    "files": len(files),
                    "bytes": sum(r["bytes"] for r in files),
                    "failures": failures,
                }
            ),
            flush=True,
        )
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
