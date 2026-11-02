"""Raw transfer integrity regressions; execution deferred to Nurion."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def data_tool():
    path = Path(__file__).resolve().parents[1] / "scripts/hpc_data.py"
    spec = importlib.util.spec_from_file_location("hpc_data_under_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(payload=b"raw"):
    return {
        "datasets": [
            {
                "id": "sample",
                "license_id": "CC-BY-4.0",
                "rights_review": "reviewed_license",
                "citation": "source attribution",
                "raw_in_code_release": False,
                "acquisition_policy": "allowed",
                "selected_assets_complete": True,
                "files": [
                    {
                        "filename": "sample.csv",
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ]
    }


def test_transfer_checks_original_bytes_and_keeps_rights_separate(data_tool, tmp_path):
    plan = data_tool.prepare(inventory(), ["sample"])
    folder = tmp_path / "sample"
    folder.mkdir()
    (folder / "sample.csv").write_bytes(b"raw")
    result = data_tool.check(plan, tmp_path, maximum_bytes=3)
    assert result["verified"] is True
    assert result["numerical_tests_executed"] == 0
    assert result["redistribution_clearance"] is False
    assert str(tmp_path) not in str(result)
    (folder / "sample.csv").write_bytes(b"bad")
    assert data_tool.check(plan, tmp_path, maximum_bytes=3)["verified"] is False


def test_missing_and_wrong_size_are_not_passes(data_tool, tmp_path):
    plan = data_tool.prepare(inventory(), ["sample"])
    assert data_tool.check(plan, tmp_path, maximum_bytes=3)["files"][0]["status"] == "missing"
    (tmp_path / "sample").mkdir()
    (tmp_path / "sample/sample.csv").write_bytes(b"wrong size")
    assert data_tool.check(plan, tmp_path, maximum_bytes=3)["files"][0]["status"] == "size_mismatch"
    with pytest.raises(ValueError, match="budget"):
        data_tool.check(plan, tmp_path, maximum_bytes=2)


@pytest.mark.parametrize("filename", ["../escape", "/absolute", "C:/drive", "a\\b", "a//b", "./sample.csv"])
def test_inventory_path_escape_is_rejected(data_tool, filename):
    source = inventory()
    source["datasets"][0]["files"][0]["filename"] = filename
    with pytest.raises(ValueError):
        data_tool.prepare(source, ["sample"])


def test_resealed_duplicate_and_case_collision_are_rejected(data_tool):
    plan = data_tool.prepare(inventory(), ["sample"])
    plan["files"].append({**plan["files"][0], "path": "sample/SAMPLE.csv"})
    plan["total_bytes"] = 6
    plan["plan_digest"] = data_tool.digest(
        {key: value for key, value in plan.items() if key != "plan_digest"}
    )
    with pytest.raises(ValueError, match="case-colliding"):
        data_tool.validate_plan(plan)


def test_policy_missing_provenance_and_incomplete_acquisition_are_rejected(data_tool):
    source = inventory()
    source["datasets"][0]["selected_assets_complete"] = False
    with pytest.raises(ValueError, match="complete/allowed"):
        data_tool.prepare(source, ["sample"])
    source = inventory()
    source["datasets"][0]["license_id"] = ""
    with pytest.raises(ValueError, match="provenance"):
        data_tool.prepare(source, ["sample"])


def test_single_link_rule_and_exclusive_output(data_tool, tmp_path):
    plan = data_tool.prepare(inventory(), ["sample"])
    (tmp_path / "sample").mkdir()
    original = tmp_path / "original"
    original.write_bytes(b"raw")
    (tmp_path / "sample/sample.csv").hardlink_to(original)
    with pytest.raises(ValueError, match="single-link"):
        data_tool.check(plan, tmp_path, maximum_bytes=3)
    output = tmp_path / "plan.json"
    data_tool.save_new(output, plan)
    with pytest.raises(FileExistsError):
        data_tool.save_new(output, plan)


def test_duplicate_json_and_nonfinite_metadata_rejected(data_tool, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"x": 1, "x": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        data_tool.read_json(path)
    path.write_text('{"x": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="nonfinite"):
        data_tool.read_json(path)

def test_transfer_withholds_result_when_source_directory_changes_during_hash(data_tool, tmp_path, monkeypatch):
    plan = data_tool.prepare(inventory(), ["sample"])
    folder = tmp_path / "sample"
    folder.mkdir()
    target = folder / "sample.csv"
    target.write_bytes(b"raw")
    original_open = Path.open
    changed = False

    def mutate_sibling(path, *args, **kwargs):
        nonlocal changed
        stream = original_open(path, *args, **kwargs)
        if path == target and not changed:
            changed = True
            marker = folder / "transient-entry"
            marker.write_bytes(b"x")
            marker.unlink()
            # Some HPC filesystems coalesce directory timestamp updates within
            # one clock tick. Force an observable metadata change so this tests
            # snapshot comparison, not an unsupported filesystem event monitor.
            metadata = folder.stat()
            os.utime(folder, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000))
        return stream

    monkeypatch.setattr(Path, "open", mutate_sibling)
    result = data_tool.check(plan, tmp_path, maximum_bytes=3)
    assert result["files"][0]["status"] == "verified"
    assert result["directory_tree_stable"] is False
    assert result["verified"] is False
