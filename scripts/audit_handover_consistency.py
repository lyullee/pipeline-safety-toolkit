"""Cross-check current handover facts against their authoritative JSON inventories."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_handover(root: Path) -> dict:
    root = Path(root).resolve(strict=True)
    data = _read_json(root / "research/data_inventory.json")
    dataset_catalog = _read_json(root / "src/pipeline_integrity/catalog_data/datasets.json")
    dataset_crosswalk = _read_json(root / "research/leak_dataset_crosswalk.json")
    scope = _read_json(root / "research/scope_inventory.json")
    queue = _read_json(root / "research/validation_queue.json")
    requests = _read_json(root / "research/owner_extension_requests.json")
    capabilities = _read_json(root / "src/pipeline_integrity/catalog_data/capabilities.json")
    capability_rows = capabilities if isinstance(capabilities, list) else capabilities["capabilities"]
    request_rows = requests if isinstance(requests, list) else requests["requests"]

    acquired = [row for row in data["datasets"] if row["files"]]
    computed_data = {
        "candidate_families": len(data["datasets"]),
        "acquired_families": len(acquired),
        "acquired_files": sum(len(row["files"]) for row in acquired),
        "acquired_bytes": sum(item["bytes"] for row in acquired for item in row["files"]),
    }
    declared_data = {key: data[key] for key in computed_data}
    computed_states = dict(Counter(row["state"] for row in capability_rows))
    confirmed = queue["confirmed_scope"]
    computed_confirmed = computed_states.get("implemented", 0) + computed_states.get("interface_verified", 0)
    expected_percent = round(100 * computed_confirmed / len(capability_rows), 1)
    request_ids = [row["id"] for row in request_rows]
    expected_request_ids = [f"PIPE-EXT-{index:03d}" for index in range(1, len(request_rows) + 1)]
    request_documents = sorted((root / "docs/owner_requests").glob("PIPE-EXT-*.md"))

    data_by_id = {row["id"]: row for row in data["datasets"]}
    catalog_by_id = {row["id"]: row for row in dataset_catalog["datasets"]}
    crosswalk_rows = dataset_crosswalk["records"]
    crosswalk_ids = [row["dataset_id"] for row in crosswalk_rows]
    crosswalk_records_match = len(crosswalk_ids) == len(set(crosswalk_ids))
    for row in crosswalk_rows:
        dataset_id = row["dataset_id"]
        if dataset_id not in data_by_id or dataset_id not in catalog_by_id:
            crosswalk_records_match = False
            continue
        inventory_row, catalog_row = data_by_id[dataset_id], catalog_by_id[dataset_id]
        assets = catalog_row.get("assets", [])
        if not assets:
            crosswalk_records_match = False
            continue
        asset = assets[0]
        asset_checksum = asset.get("checksum")
        expected_checksum = (
            None if asset_checksum is None else asset_checksum.removeprefix("sha256:")
        )
        crosswalk_records_match = crosswalk_records_match and (
            row["publisher_page"] == catalog_row["source"] == inventory_row["source"]
            and row["license_id"] == catalog_row["license_id"] == inventory_row["license_id"]
            and row["catalog_archive_asset"] == asset["filename"]
            and row["catalog_asset_sha256"] == expected_checksum
            and row["catalog_asset_bytes"] == asset.get("bytes")
        )
        acquired_files = inventory_row["files"]
        if row["catalog_asset_sha256"] is None:
            crosswalk_records_match = crosswalk_records_match and not acquired_files
        else:
            crosswalk_records_match = crosswalk_records_match and any(
                item["sha256"] == row["catalog_asset_sha256"]
                and item["bytes"] == row["catalog_asset_bytes"]
                and item["filename"] == row["catalog_archive_asset"]
                for item in acquired_files
            )
    crosswalk_conservative = (
        dataset_crosswalk["metadata_overlap"]["equivalence_established"] is False
        and dataset_crosswalk["metadata_overlap"]["data_independence_established"] is False
        and dataset_crosswalk["handling"]["do_not_redownload_as_a_separate_family_until_archive_identity_is_resolved"]
        and dataset_crosswalk["handling"]["do_not_count_the_two_records_as_independent_physical_experiments"]
    )

    requirements = (root / "docs/REQUIREMENTS_AUDIT_KO.md").read_text(encoding="utf-8")
    status = (root / "docs/STATUS_KO.md").read_text(encoding="utf-8")
    documentation_tokens = {
        "requirements_data": (
            f"{computed_data['candidate_families']}후보·{computed_data['acquired_families']}자료군·"
            f"{computed_data['acquired_files']}파일·{computed_data['acquired_bytes']:,}바이트"
        ),
        "requirements_requests": f"PIPE-EXT-001~{len(request_rows):03d}",
        "status_progress": f"{computed_confirmed}/{len(capability_rows)}({expected_percent:.1f}%)",
    }
    checks = {
        "data_summary_recomputes": declared_data == computed_data,
        "scope_count_matches_catalog": scope.get("capabilities") == len(capability_rows),
        "scope_states_match_catalog": scope.get("states") == computed_states,
        "confirmed_count_matches_states": confirmed.get("completed") == computed_confirmed,
        "confirmed_total_matches_catalog": confirmed.get("total") == len(capability_rows),
        "confirmed_percent_matches_catalog": confirmed.get("percent") == expected_percent,
        "extension_ids_are_contiguous": request_ids == expected_request_ids,
        "extension_document_count_matches": len(request_documents) == len(request_rows),
        "requirements_data_fact_is_current": documentation_tokens["requirements_data"] in requirements,
        "requirements_request_fact_is_current": documentation_tokens["requirements_requests"] in requirements,
        "status_progress_fact_is_current": documentation_tokens["status_progress"] in status,
        "dataset_crosswalk_matches_catalog_and_acquisition": crosswalk_records_match,
        "dataset_crosswalk_preserves_unresolved_equivalence": crosswalk_conservative,
    }
    return {
        "schema": "pipeline_integrity.handover_consistency_audit.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "authoritative_facts": {
            "data": computed_data,
            "capabilities": len(capability_rows),
            "states": computed_states,
            "confirmed_scope": {
                "completed": computed_confirmed,
                "total": len(capability_rows),
                "percent": expected_percent,
            },
            "extension_requests": len(request_rows),
            "dataset_crosswalk_records": len(crosswalk_rows),
        },
        "tests_executed": 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_handover(args.root)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "failed": sum(not v for v in result["checks"].values())}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
