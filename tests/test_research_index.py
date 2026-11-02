"""Preserve candidate research and acquired receipts across index regeneration."""
import json
import runpy
import sys
from pathlib import Path


def test_data_only_rebuild_preserves_unregistered_candidates_and_legacy_receipts(tmp_path, monkeypatch):
    research = tmp_path / "research"
    research.mkdir()
    previous = {
        "schema": "pipeline_integrity.data_inventory.v1",
        "candidate_families": 2,
        "acquired_families": 1,
        "acquired_files": 1,
        "acquired_bytes": 12,
        "datasets": [
            {"id": "wds_event_db", "files": [{"filename": "old.zip", "bytes": 12, "sha256": "a" * 64}], "limitations": ["saved"]},
            {"id": "legacy_search_candidate", "files": [], "limitations": ["unregistered research source"]},
        ],
    }
    (research / "data_inventory.json").write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_research_index.py", "--data-only"])
    tool = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "build_research_index.py"))
    tool["main"]()
    rebuilt = json.loads((research / "data_inventory.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in rebuilt["datasets"]}
    assert rows["legacy_search_candidate"]["limitations"] == ["unregistered research source"]
    assert rows["wds_event_db"]["files"][0]["filename"] == "old.zip"
    assert rebuilt["candidate_families"] >= len(previous["datasets"])
