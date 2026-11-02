import json
from pathlib import Path

from scripts.audit_handover_consistency import audit_handover


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(root: Path):
    _write(
        root / "research/data_inventory.json",
        {
            "candidate_families": 2,
            "acquired_families": 1,
            "acquired_files": 1,
            "acquired_bytes": 7,
            "datasets": [
                {"id": "candidate_a", "source": "https://data.example/a", "license_id": "CC-BY-4.0", "files": [{"filename": "a.zip", "bytes": 7, "sha256": "a" * 64}]},
                {"id": "candidate_b", "source": "https://data.example/b", "license_id": "CC-BY-4.0", "files": []},
            ],
        },
    )
    _write(
        root / "src/pipeline_integrity/catalog_data/datasets.json",
        {
            "datasets": [
                {"id": "candidate_a", "source": "https://data.example/a", "license_id": "CC-BY-4.0", "assets": [{"filename": "a.zip", "checksum": "sha256:" + "a" * 64, "bytes": 7}]},
                {"id": "candidate_b", "source": "https://data.example/b", "license_id": "CC-BY-4.0", "assets": [{"filename": "b.zip"}]},
            ]
        },
    )
    _write(
        root / "research/leak_dataset_crosswalk.json",
        {
            "records": [
                {"dataset_id": "candidate_a", "publisher_page": "https://data.example/a", "license_id": "CC-BY-4.0", "catalog_archive_asset": "a.zip", "catalog_asset_sha256": "a" * 64, "catalog_asset_bytes": 7},
                {"dataset_id": "candidate_b", "publisher_page": "https://data.example/b", "license_id": "CC-BY-4.0", "catalog_archive_asset": "b.zip", "catalog_asset_sha256": None, "catalog_asset_bytes": None},
            ],
            "metadata_overlap": {"equivalence_established": False, "data_independence_established": False},
            "handling": {
                "do_not_redownload_as_a_separate_family_until_archive_identity_is_resolved": True,
                "do_not_count_the_two_records_as_independent_physical_experiments": True,
            },
        },
    )
    _write(
        root / "research/scope_inventory.json",
        {"capabilities": 3, "states": {"implemented": 1, "interface_verified": 1, "partial": 1}},
    )
    _write(
        root / "research/validation_queue.json",
        {"confirmed_scope": {"completed": 2, "total": 3, "percent": 66.7}},
    )
    _write(
        root / "research/owner_extension_requests.json",
        {"requests": [{"id": "PIPE-EXT-001"}, {"id": "PIPE-EXT-002"}]},
    )
    _write(
        root / "src/pipeline_integrity/catalog_data/capabilities.json",
        [
            {"state": "implemented"},
            {"state": "interface_verified"},
            {"state": "partial"},
        ],
    )
    for name in ("PIPE-EXT-001.md", "PIPE-EXT-002.md"):
        path = root / "docs/owner_requests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    (root / "docs/REQUIREMENTS_AUDIT_KO.md").write_text(
        "2후보·1자료군·1파일·7바이트 PIPE-EXT-001~002", encoding="utf-8"
    )
    (root / "docs/STATUS_KO.md").write_text("2/3(66.7%)", encoding="utf-8")


def test_handover_audit_recomputes_machine_and_current_document_facts(tmp_path):
    fixture(tmp_path)
    result = audit_handover(tmp_path)
    assert result["passed"]
    assert result["authoritative_facts"]["extension_requests"] == 2


def test_handover_audit_rejects_stale_document_or_resealed_wrong_summary(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "research/data_inventory.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["acquired_bytes"] = 8
    _write(path, value)
    (tmp_path / "docs/STATUS_KO.md").write_text("1/3(33.3%)", encoding="utf-8")
    result = audit_handover(tmp_path)
    assert not result["passed"]
    assert not result["checks"]["data_summary_recomputes"]
    assert not result["checks"]["status_progress_fact_is_current"]


def test_handover_audit_rejects_crosswalk_digest_that_differs_from_acquired_asset(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "research/leak_dataset_crosswalk.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["records"][0]["catalog_asset_sha256"] = "b" * 64
    _write(path, value)
    result = audit_handover(tmp_path)
    assert not result["passed"]
    assert not result["checks"]["dataset_crosswalk_matches_catalog_and_acquisition"]
