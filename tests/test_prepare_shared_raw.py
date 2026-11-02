"""Atomic shared raw staging cases; execution deferred to Nurion."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def staging_tool():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_shared_raw.py"
    spec = importlib.util.spec_from_file_location("shared_raw_staging_under_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(tool, payload):
    data = tool._data_tool()
    body = {
        "schema": data.SCHEMA,
        "inventory_digest": "a" * 64,
        "datasets": [
            {
                "id": "sample",
                "license_id": "CC-BY-4.0",
                "rights_review": "reviewed_license",
                "citation": "Authored staging fixture",
                "raw_in_code_release": False,
            }
        ],
        "files": [
            {
                "dataset_id": "sample",
                "path": "sample/source.bin",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        "total_bytes": len(payload),
    }
    return {**body, "plan_digest": data.digest(body)}


def test_stage_verifies_canonical_target_and_emits_path_free_receipt(staging_tool, tmp_path):
    payload = b"reviewed raw bytes"
    source = tmp_path / "download-name.bin"
    source.write_bytes(payload)
    root = tmp_path / "raw"
    root.mkdir()
    receipt = staging_tool.stage(source, root, plan(staging_tool, payload), maximum_bytes=len(payload))
    target = root / "sample/source.bin"
    try:
        assert target.read_bytes() == payload
        assert receipt["canonical_relative_path"] == "sample/source.bin"
        assert str(tmp_path) not in str(receipt)
        assert receipt["target_verified"] and receipt["source_unchanged"]
    finally:
        if staging_tool.os.name == "posix":
            root.chmod(0o755)
            target.parent.chmod(0o755)
            target.chmod(0o644)


def test_stage_never_overwrites_or_accepts_wrong_supplier_bytes(staging_tool, tmp_path):
    payload = b"reviewed raw bytes"
    source = tmp_path / "source.bin"
    source.write_bytes(b"wrong raw content")
    root = tmp_path / "raw"
    root.mkdir()
    with pytest.raises(ValueError):
        staging_tool.stage(source, root, plan(staging_tool, payload), maximum_bytes=len(payload))
    source.write_bytes(payload)
    (root / "sample").mkdir(exist_ok=True)
    (root / "sample/source.bin").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        staging_tool.stage(source, root, plan(staging_tool, payload), maximum_bytes=len(payload))


def test_existing_canonical_file_is_verified_and_sealed_without_copy(staging_tool, tmp_path):
    payload = b"already transferred canonical bytes"
    root = tmp_path / "raw"
    target = root / "sample/source.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    receipt = staging_tool.verify_and_seal_existing(
        root, plan(staging_tool, payload), maximum_bytes=len(payload)
    )
    try:
        assert target.read_bytes() == payload
        assert receipt["source_verified_before_and_after_seal"]
        assert receipt["files"] == 1 and receipt["bytes"] == len(payload)
        assert str(tmp_path) not in str(receipt)
    finally:
        if staging_tool.os.name == "posix":
            root.chmod(0o755)
            target.parent.chmod(0o755)
            target.chmod(0o644)


def test_existing_wrong_target_is_never_sealed_as_verified(staging_tool, tmp_path):
    payload = b"reviewed"
    root = tmp_path / "raw"
    target = root / "sample/source.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"different")
    with pytest.raises(ValueError, match="differs"):
        staging_tool.verify_and_seal_existing(root, plan(staging_tool, payload), maximum_bytes=len(payload))
