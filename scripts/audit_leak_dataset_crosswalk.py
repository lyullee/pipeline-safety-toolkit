"""Check the reviewed leak-deposit crosswalk against source catalog and inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pipeline_integrity.contracts import digest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATASET_IDS = frozenset({"potash_cp", "leak_testbed", "leak_multisensor_transients"})


@dataclass(frozen=True)
class AuditAsset:
    filename: str
    bytes: int | None
    checksum: str | None
    checksum_origin: str
    sha256: str | None


@dataclass(frozen=True)
class AuditDataset:
    id: str
    source: str
    license_id: str
    assets: tuple[AuditAsset, ...]


def audit_datasets_from_catalog(source_catalog: dict) -> tuple[AuditDataset, ...]:
    """Project only the publisher fields used by the crosswalk audit."""
    rows = source_catalog.get("datasets") if isinstance(source_catalog, dict) else None
    if not isinstance(rows, list):
        raise ValueError("source catalog must contain a dataset list")
    try:
        return tuple(
            AuditDataset(
                id=row["id"],
                source=row["source"],
                license_id=row["license_id"],
                assets=tuple(
                    AuditAsset(
                        filename=asset["filename"],
                        bytes=asset.get("bytes"),
                        checksum=asset.get("checksum"),
                        checksum_origin=asset.get("checksum_origin", "publisher_metadata"),
                        sha256=asset.get("sha256"),
                    )
                    for asset in row["assets"]
                ),
            )
            for row in rows
        )
    except (KeyError, TypeError) as error:
        raise ValueError("source catalog dataset or asset metadata is malformed") from error


def audit_crosswalk(
    crosswalk: dict,
    inventory: dict,
    specs: tuple[AuditDataset, ...],
    *,
    expected_dataset_ids: set[str] | frozenset[str] | None = None,
) -> dict:
    """Reconcile archive identity metadata without reading any signal observations."""
    if not isinstance(crosswalk, dict):
        raise ValueError("reviewed dataset crosswalk must be an object")
    records = crosswalk.get("records")
    expected_ids = crosswalk.get("expected_dataset_ids")
    required_ids = EXPECTED_DATASET_IDS if expected_dataset_ids is None else frozenset(expected_dataset_ids)
    inventory_rows = inventory.get("datasets") if isinstance(inventory, dict) else None
    if (
        crosswalk.get("schema") != "pipeline_integrity.dataset_crosswalk.v1"
        or not isinstance(records, list)
        or not isinstance(expected_ids, list)
        or any(not isinstance(value, str) or not value for value in expected_ids)
        or len(expected_ids) != len(set(expected_ids))
        or not expected_ids
    ):
        raise ValueError("reviewed dataset crosswalk schema or record list is invalid")
    if not isinstance(inventory_rows, list):
        raise ValueError("research data inventory has no dataset list")
    spec_by_id = {item.id: item for item in specs}
    inventory_by_id = {item.get("id"): item for item in inventory_rows if isinstance(item, dict)}
    if len(spec_by_id) != len(specs) or len(inventory_by_id) != len(inventory_rows):
        raise ValueError("source catalog or research inventory contains duplicate/malformed IDs")

    checked = []
    seen = set()
    issues = []
    if set(expected_ids) != required_ids:
        issues.append({"dataset_id": None, "issue": "declared_expected_dataset_set_mismatch"})
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("dataset_id"), str):
            raise ValueError("crosswalk contains a malformed dataset record")
        dataset_id = record["dataset_id"]
        if dataset_id in seen:
            raise ValueError("crosswalk contains a duplicate dataset ID")
        seen.add(dataset_id)
        spec, inventory_row = spec_by_id.get(dataset_id), inventory_by_id.get(dataset_id)
        row_issues = []
        supplier_verified = None
        if spec is None or inventory_row is None:
            row_issues.append("dataset_id_missing_from_catalog_or_inventory")
        else:
            assets = [asset for asset in spec.assets if asset.filename == record.get("catalog_archive_asset")]
            files = [asset for asset in inventory_row.get("files", []) if asset.get("filename") == record.get("catalog_archive_asset")]
            if len(assets) != 1 or len(files) != 1:
                row_issues.append("catalog_asset_not_unique_in_source_records")
            else:
                asset, file_record = assets[0], files[0]
                supplier_verified = file_record.get("supplier_checksum_verified")
                checks = {
                    "publisher_page": spec.source == record.get("publisher_page"),
                    "license_id": spec.license_id == record.get("license_id"),
                    "asset_bytes": asset.bytes == record.get("catalog_asset_bytes") == file_record.get("bytes"),
                    "asset_sha256": (
                        record.get("catalog_asset_sha256") == file_record.get("sha256")
                        and (
                            asset.sha256 == file_record.get("sha256")
                            or asset.checksum == f"sha256:{file_record.get('sha256')}"
                        )
                    ),
                    "checksum_origin": asset.checksum_origin == file_record.get("checksum_origin")
                    == record.get("checksum_origin"),
                    "supplier_checksum_flag": file_record.get("supplier_checksum_verified") is (
                        asset.checksum_origin == "publisher_metadata"
                    ) and record.get("supplier_checksum_verified") is file_record.get("supplier_checksum_verified"),
                }
                row_issues.extend(f"{name}_mismatch" for name, matched in checks.items() if not matched)
        checked.append(
            {
                "dataset_id": dataset_id,
                "catalog_asset": record.get("catalog_archive_asset"),
                "status": "consistent" if not row_issues else "mismatch",
                "issues": row_issues,
                "supplier_checksum_verified": supplier_verified,
            }
        )
        issues.extend({"dataset_id": dataset_id, "issue": item} for item in row_issues)

    for dataset_id in sorted(required_ids - seen):
        issues.append({"dataset_id": dataset_id, "issue": "expected_dataset_missing_from_crosswalk"})
    for dataset_id in sorted(seen - required_ids):
        issues.append({"dataset_id": dataset_id, "issue": "unexpected_dataset_in_crosswalk"})

    body = {
        "schema": "pipeline_integrity.dataset_crosswalk_integrity.v1",
        "crosswalk_digest": digest(crosswalk),
        "inventory_digest": digest(inventory),
        "expected_dataset_ids": sorted(required_ids),
        "declared_dataset_ids": sorted(expected_ids),
        "dataset_count": len(checked),
        "consistent_dataset_count": sum(item["status"] == "consistent" for item in checked),
        "datasets": checked,
        "issues": issues,
        "signal_observations_read": False,
        "archive_member_content_compared": False,
        "independent_experiment_or_trial_established": False,
    }
    return {**body, "record_digest": digest(body)}


def resolve_output_path(root: Path, output: Path | None) -> Path:
    """Resolve relative receipts against the audited checkout."""
    if output is None:
        return root / "research/leak_crosswalk_integrity_status.json"
    return output if output.is_absolute() else root / output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    crosswalk = json.loads((root / "research/leak_dataset_crosswalk.json").read_text(encoding="utf-8"))
    inventory = json.loads((root / "research/data_inventory.json").read_text(encoding="utf-8"))
    source_catalog = json.loads(
        (root / "src/pipeline_integrity/catalog_data/datasets.json").read_text(encoding="utf-8")
    )
    specs = audit_datasets_from_catalog(source_catalog)
    result = audit_crosswalk(crosswalk, inventory, specs)
    result["implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["record_digest"] = digest({key: value for key, value in result.items() if key != "record_digest"})
    output = resolve_output_path(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "consistent" if not result["issues"] else "mismatch", "datasets": result["dataset_count"], "issues": len(result["issues"])}))
    if result["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
