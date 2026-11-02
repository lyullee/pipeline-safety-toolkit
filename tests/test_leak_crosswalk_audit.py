"""Synthetic integrity checks for data-catalog crosswalk provenance."""

from pathlib import Path
from copy import deepcopy
from scripts.audit_leak_dataset_crosswalk import (
    AuditAsset,
    AuditDataset,
    audit_crosswalk,
    audit_datasets_from_catalog,
    resolve_output_path,
)


def _fixture():
    sha = "a" * 64
    spec = AuditDataset(
        id="sample",
        source="https://data.example.org/datasets/sample/1",
        license_id="CC-BY-4.0",
        assets=(AuditAsset(
            filename="sample.zip",
            bytes=12,
            checksum="sha256:" + sha,
            checksum_origin="publisher_metadata",
            sha256=None,
        ),),
    )
    crosswalk = {
        "schema": "pipeline_integrity.dataset_crosswalk.v1",
        "expected_dataset_ids": ["sample"],
        "records": [{
            "dataset_id": "sample",
            "publisher_page": spec.source,
            "license_id": spec.license_id,
            "catalog_archive_asset": "sample.zip",
            "catalog_asset_bytes": 12,
            "catalog_asset_sha256": sha,
            "checksum_origin": "publisher_metadata",
            "supplier_checksum_verified": True,
        }],
    }
    inventory = {"datasets": [{
        "id": "sample",
        "files": [{
            "filename": "sample.zip",
            "bytes": 12,
            "sha256": sha,
            "checksum_origin": "publisher_metadata",
            "supplier_checksum_verified": True,
        }],
    }]}
    return crosswalk, inventory, (spec,)


def test_dataset_crosswalk_audit_reconciles_catalog_and_acquisition_metadata():
    crosswalk, inventory, specs = _fixture()
    result = audit_crosswalk(crosswalk, inventory, specs, expected_dataset_ids={"sample"})
    assert result["consistent_dataset_count"] == 1
    assert result["issues"] == []
    assert result["signal_observations_read"] is False


def test_dataset_crosswalk_audit_reports_a_source_pin_mismatch():
    crosswalk, inventory, specs = _fixture()
    changed = deepcopy(inventory)
    changed["datasets"][0]["files"][0]["sha256"] = "b" * 64
    result = audit_crosswalk(crosswalk, changed, specs, expected_dataset_ids={"sample"})
    assert result["consistent_dataset_count"] == 0
    assert "asset_sha256_mismatch" in result["datasets"][0]["issues"]


def test_dataset_crosswalk_audit_rejects_missing_expected_dataset():
    crosswalk, inventory, specs = _fixture()
    result = audit_crosswalk(
        crosswalk, inventory, specs, expected_dataset_ids={"sample", "required_second_source"}
    )
    assert result["consistent_dataset_count"] == 1
    assert {item["issue"] for item in result["issues"]} == {
        "declared_expected_dataset_set_mismatch",
        "expected_dataset_missing_from_crosswalk",
    }


def test_dataset_crosswalk_audit_output_is_relative_to_selected_root(tmp_path):
    assert resolve_output_path(tmp_path, None) == tmp_path / "research/leak_crosswalk_integrity_status.json"
    assert resolve_output_path(tmp_path, Path("review/result.json")) == tmp_path / "review/result.json"



def test_catalog_projection_ignores_unrelated_fields_and_applies_optional_provenance_default():
    specs = audit_datasets_from_catalog({
        "datasets": [{
            "id": "sample",
            "source": "https://data.example.org/datasets/sample/1",
            "license_id": "CC-BY-4.0",
            "future_catalog_field": {"owner": "another module"},
            "assets": [{
                "filename": "sample.zip",
                "bytes": 12,
                "checksum": "sha256:" + "a" * 64,
                "future_asset_field": "ignored by this audit",
            }],
        }]
    })
    assert specs[0].assets[0].checksum_origin == "publisher_metadata"
    assert specs[0].assets[0].sha256 is None
