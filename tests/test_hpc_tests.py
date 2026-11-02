"""Nurion controller integrity cases; execution deferred with the numerical suite."""

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def controller():
    path = Path(__file__).resolve().parents[1] / "scripts/hpc_tests.py"
    spec = importlib.util.spec_from_file_location("hpc_controller_under_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_plan(c, tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "tests/test_a.py").write_text("def test_a(): pass\n")
    (root / "scripts/hpc_tests.py").write_text("# frozen controller\n")
    monkeypatch.setattr(c, "ROOT", root)
    plan = tmp_path / "manifest.json"
    c.prepare(SimpleNamespace(manifest=plan, shards=1, history_junit=None, source_map=None))
    return plan, root


def test_validation_queue_manifest_reference_does_not_invalidate_its_configuration(
    controller, tmp_path, monkeypatch
):
    root = tmp_path / "queue-source"
    for folder in ("research", "docs", "scripts", "tests", "src/pipeline_integrity"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    queue = root / "research/validation_queue.json"
    queue.write_text('{"manifest": "manifest-v1.json"}\n')
    (root / "docs/STATUS_KO.md").write_text("stable public configuration\n")
    (root / "scripts/hpc_tests.py").write_text("# controller\n")
    (root / "tests/test_a.py").write_text("def test_a(): pass\n")
    (root / "src/pipeline_integrity/core.py").write_text("VALUE = 1\n")
    monkeypatch.setattr(controller, "ROOT", root)

    before = controller.snapshots()
    queue.write_text('{"manifest": "manifest-v2.json", "digest": "changed"}\n')
    after = controller.snapshots()

    assert "research/validation_queue.json" not in before[2]
    assert before[2] == after[2]
    assert before[0] == after[0]
    assert before[1] == after[1]

    (root / "docs/STATUS_KO.md").write_text("changed public configuration\n")
    assert controller.snapshots()[2] != after[2]


def test_plan_rejects_source_changed_after_preparation(controller, tmp_path, monkeypatch):
    plan, root = frozen_plan(controller, tmp_path, monkeypatch)
    (root / "tests/test_a.py").write_text("def test_a(): assert False\n")
    with pytest.raises(ValueError, match="changed after"):
        controller.load_manifest(plan)


def test_resealed_duplicate_assignment_is_still_invalid(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    value = json.loads(plan.read_text())
    value["shards"][0]["test_files"] *= 2
    value["manifest_digest"] = controller.fingerprint(
        {k: v for k, v in value.items() if k != "manifest_digest"}
    )
    controller.save(plan, value)
    with pytest.raises(ValueError, match="exactly once"):
        controller.load_manifest(plan)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_aggregate_rejects_hidden_outcomes(controller, tmp_path, monkeypatch, outcome):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    value = controller.load_manifest(plan)
    results = tmp_path / "results"
    rank = results / "rank-00000"
    rank.mkdir(parents=True)
    xml = rank / "tests.xml"
    xml.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.test_a" name="test_a">'
        f"<{outcome}/></testcase></testsuite>"
    )
    controller.save(
        rank / "receipt.json",
        {
            "manifest_digest": value["manifest_digest"],
            "rank": 0,
            "size": 1,
            "test_files": ["tests/test_a.py"],
            "environment": {"review_fixture": True},
            "controller_source_sha256": value["configuration_sha256"]["scripts/hpc_tests.py"],
            "external_sources": {},
            "data_fixture": {"status": "not_configured"},
            "status": "finished",
            "pytest_exit_code": 0,
            "junit_sha256": controller.sha(xml),
        },
    )
    with pytest.raises(SystemExit):
        controller.aggregate(
            SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
        )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["status"] == "incomplete_or_failed"
    assert any("outcome elements" in row["reason"] for row in summary["issues"])


def test_missing_rank_is_incomplete(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "absent-results"
    with pytest.raises(SystemExit):
        controller.aggregate(
            SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
        )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["completed_test_modules"] == 0
    assert summary["status"] == "incomplete_or_failed"


def reported_rank(c, manifest, results, *, skipped=False, failed=False):
    rank = results / "rank-00000"
    rank.mkdir(parents=True)
    node = "tests/test_a.py::test_a"
    outcome = "skipped" if skipped else "failure" if failed else None
    child = f"<{outcome}/>" if outcome else ""
    xml = rank / "tests.xml"
    xml.write_text(
        f'<testsuite tests="1" failures="{int(failed)}" errors="0" skipped="{int(skipped)}">'
        '<testcase classname="tests.test_a" name="test_a"><properties>'
        f'<property name="pipeline_hpc_node_sha256" value="{c.fingerprint(node)}"/>'
        f"</properties>{child}</testcase></testsuite>"
    )
    capture = {
        "schema": "pipeline_integrity.hpc_pytest_capture.v1",
        "collected": [node],
        "deselected": [],
        "reports": {
            node: {
                "setup": "passed",
                "call": "skipped" if skipped else "failed" if failed else "passed",
                "teardown": "passed",
            }
        },
        "exitstatus": 1 if failed else 0,
    }
    c.save(rank / "capture.json", capture)
    receipt = {
        "manifest_digest": manifest["manifest_digest"],
        "rank": 0,
        "size": 1,
        "test_files": ["tests/test_a.py"],
        "environment": {"review_fixture": True},
        "controller_source_sha256": manifest["configuration_sha256"]["scripts/hpc_tests.py"],
        "external_sources": {},
        "data_fixture": {"status": "not_configured"},
        "status": "finished",
        "pytest_exit_code": 1 if failed else 0,
        "junit_sha256": c.sha(xml),
        "capture_sha256": c.sha(rank / "capture.json"),
        "provenance_unchanged": True,
    }
    c.save(rank / "receipt.json", receipt)
    return rank, receipt, capture


def test_aggregate_pass_requires_actual_collection_and_stage_coverage(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "results"
    reported_rank(controller, controller.load_manifest(plan), results)
    controller.aggregate(
        SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
    )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["status"] == "passed" and summary["counts"]["tests"] == 1


@pytest.mark.parametrize(
    "defect", ["deselected", "unexecuted", "missing_teardown", "false_pass", "changed_provenance"]
)
def test_aggregate_does_not_accept_plausible_but_incomplete_junit(controller, tmp_path, monkeypatch, defect):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "results"
    rank, receipt, captured = reported_rank(controller, controller.load_manifest(plan), results)
    node = captured["collected"][0]
    if defect == "deselected":
        captured["deselected"] = ["tests/test_a.py::omitted"]
    elif defect == "unexecuted":
        captured["collected"].append("tests/test_a.py::omitted")
    elif defect == "missing_teardown":
        captured["reports"][node].pop("teardown")
    elif defect == "false_pass":
        captured["reports"][node]["call"] = "failed"
    else:
        receipt["provenance_unchanged"] = False
    controller.save(rank / "capture.json", captured)
    receipt["capture_sha256"] = controller.sha(rank / "capture.json")
    controller.save(rank / "receipt.json", receipt)
    with pytest.raises(SystemExit):
        controller.aggregate(
            SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
        )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["counts"]["tests"] == 0  # Invalid rank's partial counts are quarantined.


def test_genuine_failed_cases_are_reported_in_actual_counts(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "results"
    reported_rank(controller, controller.load_manifest(plan), results, failed=True)
    with pytest.raises(SystemExit):
        controller.aggregate(
            SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
        )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["counts"]["tests"] == summary["counts"]["failures"] == 1


def test_native_pytest_call_failure_plus_teardown_error_is_not_controller_failure(
    controller, tmp_path, monkeypatch
):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "results"
    rank, receipt, captured = reported_rank(controller, controller.load_manifest(plan), results, failed=True)
    xml = rank / "tests.xml"
    suite = ET.parse(xml).getroot()
    second = ET.fromstring(ET.tostring(suite.find("testcase")))
    second.remove(second.find("failure"))
    ET.SubElement(second, "error")
    suite.append(second)
    suite.set("tests", "2")
    suite.set("errors", "1")
    ET.ElementTree(suite).write(xml)
    captured["reports"][captured["collected"][0]]["teardown"] = "failed"
    controller.save(rank / "capture.json", captured)
    receipt.update(junit_sha256=controller.sha(xml), capture_sha256=controller.sha(rank / "capture.json"))
    controller.save(rank / "receipt.json", receipt)
    with pytest.raises(SystemExit):
        controller.aggregate(
            SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=False)
        )
    summary = json.loads((results / "summary.json").read_text())
    assert summary["controller_complete"] is True
    assert summary["counts"]["tests"] == 2 and summary["collected_test_nodes"] == 1


def test_allowed_skips_are_distinct_from_no_skip_pass(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    results = tmp_path / "results"
    reported_rank(controller, controller.load_manifest(plan), results, skipped=True)
    controller.aggregate(
        SimpleNamespace(manifest=plan, results=results, public_summary=None, allow_skips=True)
    )
    assert json.loads((results / "summary.json").read_text())["status"] == "passed_with_allowed_skips"


@pytest.mark.parametrize(
    "unsafe", ["tests/../test_a.py", "tests//test_a.py", "tests/C:/test_a.py", "tests\\test_a.py"]
)
def test_resealed_manifest_noncanonical_paths_rejected(controller, tmp_path, monkeypatch, unsafe):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    value = json.loads(plan.read_text())
    value["test_file_sha256"][unsafe] = value["test_file_sha256"].pop("tests/test_a.py")
    value["shards"][0]["test_files"] = [unsafe]
    value["manifest_digest"] = controller.fingerprint(
        {k: v for k, v in value.items() if k != "manifest_digest"}
    )
    controller.save(plan, value)
    with pytest.raises(ValueError, match="unsafe"):
        controller.load_manifest(plan, validate_current=False)


def test_owner_license_and_native_binary_are_pinned_and_isolated(controller, tmp_path):
    project = tmp_path / "owner"
    (project / "src/pkg").mkdir(parents=True)
    (project / "src/pkg/__init__.py").write_text("value = 1\n")
    (project / "src/pkg/libowner.so").write_bytes(b"authored binary identity; never loaded")
    (project / "LICENSE").write_text("Original test fixture license\n")
    mapping = tmp_path / "sources.json"
    controller.save(mapping, {"owner": {"project_root": str(project), "import_root": "src"}})
    pinned, _ = controller.source_map(mapping)
    staged, paths = controller.stage_sources(mapping, tmp_path / "private_rank", pinned)
    assert controller.source_map(staged)[0] == pinned and str(project / "src") not in paths
    (project / "LICENSE").write_text("Changed license identity\n")
    assert controller.source_map(mapping)[0] != pinned
    assert controller.source_map(staged)[0] == pinned


def data_mapping(controller, tmp_path):
    raw = tmp_path / "raw"
    (raw / "sample").mkdir(parents=True)
    payload = b"original reviewed fixture"
    (raw / "sample/dataset.csv").write_bytes(payload)
    tool = controller._data_tool()
    inventory = {
        "datasets": [
            {
                "id": "sample",
                "license_id": "CC-BY-4.0",
                "rights_review": "reviewed_license",
                "citation": "authored controller fixture",
                "raw_in_code_release": False,
                "acquisition_policy": "allowed",
                "selected_assets_complete": True,
                "files": [
                    {
                        "filename": "dataset.csv",
                        "bytes": len(payload),
                        "sha256": controller.hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ]
    }
    plan = tmp_path / "transfer.json"
    controller.save(plan, tool.prepare(inventory, ["sample"]))
    mapping = tmp_path / "data-map.json"
    controller.save(
        mapping,
        {
            "transfer_plan": str(plan),
            "raw_root": str(raw),
            "test_files": ["tests/test_a.py"],
            "maximum_stage_bytes": len(payload),
        },
    )
    return mapping, raw, payload


def test_data_fixture_is_pinned_and_staged_only_from_verified_plan(controller, tmp_path):
    mapping, raw, payload = data_mapping(controller, tmp_path)
    identity = controller.data_fixture(mapping, {"tests/test_a.py"})
    destination = tmp_path / "rank-data"
    receipt = controller.stage_data(mapping, destination, identity)
    assert receipt == {
        "status": "staged_and_verified",
        "plan_digest": identity["plan_digest"],
        "files": 1,
        "bytes": len(payload),
    }
    assert (destination / "sample/dataset.csv").read_bytes() == payload
    assert controller.staged_data_matches(mapping, destination, identity)
    (destination / "sample/dataset.csv").write_bytes(b"changed staged bytes")
    assert not controller.staged_data_matches(mapping, destination, identity)
    (raw / "sample/dataset.csv").write_bytes(b"changed fixture bytes")
    with pytest.raises(ValueError, match="source differs"):
        controller.data_fixture(mapping, {"tests/test_a.py"})


def test_data_fixture_rejects_unknown_consumer_and_oversized_stage(controller, tmp_path):
    mapping, _, _ = data_mapping(controller, tmp_path)
    with pytest.raises(ValueError, match="outside the frozen suite"):
        controller.data_fixture(mapping, {"tests/test_other.py"})
    value = json.loads(mapping.read_text())
    value["maximum_stage_bytes"] = controller.MAXIMUM_DATA_STAGE_BYTES + 1
    controller.save(tmp_path / "oversized.json", value)
    with pytest.raises(ValueError, match="bounded budget"):
        controller.data_fixture(tmp_path / "oversized.json", {"tests/test_a.py"})


def test_large_shared_fixture_is_verified_in_place_and_never_rank_copied(controller, tmp_path):
    mapping, raw, payload = data_mapping(controller, tmp_path)
    value = json.loads(mapping.read_text())
    value.pop("maximum_stage_bytes")
    value.update(
        access_mode="shared_read_only",
        maximum_verify_bytes=controller.MAXIMUM_DATA_STAGE_BYTES + 1,
    )
    shared = tmp_path / "shared.json"
    controller.save(shared, value)
    identity = controller.data_fixture(shared, {"tests/test_a.py"})
    assert identity["access_mode"] == "shared_read_only"
    assert str(raw) not in json.dumps(identity)
    protected = [raw / "sample/dataset.csv", raw / "sample", raw]
    if controller.os.name == "posix":
        with pytest.raises(ValueError, match="POSIX read-only"):
            controller.verify_shared_data(shared, identity)
        protected[0].chmod(0o444)
        for directory in protected[1:]:
            directory.chmod(0o555)
    try:
        source_root, receipt = controller.verify_shared_data(shared, identity)
        assert source_root == raw.resolve()
        assert receipt == {
            "status": "shared_verified",
            "plan_digest": identity["plan_digest"],
            "files": 1,
            "bytes": len(payload),
        }
        with pytest.raises(ValueError, match="cannot be copied"):
            controller.stage_data(shared, tmp_path / "forbidden-copy", identity)
    finally:
        if controller.os.name == "posix":
            for directory in reversed(protected[1:]):
                directory.chmod(0o755)
            protected[0].chmod(0o644)


def test_manifest_binds_data_consumer_without_storing_private_root(controller, tmp_path, monkeypatch):
    mapping, raw, _ = data_mapping(controller, tmp_path)
    plan, root = frozen_plan(controller, tmp_path, monkeypatch)
    # Re-prepare after frozen_plan changed the controller root.
    controller.prepare(
        SimpleNamespace(
            manifest=tmp_path / "with-data.json",
            shards=1,
            history_junit=None,
            source_map=None,
            data_map=mapping,
        )
    )
    manifest = controller.load_manifest(tmp_path / "with-data.json")
    assert manifest["data_fixture"]["test_files"] == ["tests/test_a.py"]
    assert str(raw) not in json.dumps(manifest)
    assert root.is_dir() and plan.is_file()


def test_targeted_campaign_freezes_only_named_modules_without_claiming_full_suite(
    controller, tmp_path, monkeypatch
):
    plan, root = frozen_plan(controller, tmp_path, monkeypatch)
    (root / "tests/test_b.py").write_text("def test_b():\n    assert True\n")
    target = tmp_path / "targeted.json"
    controller.prepare(
        SimpleNamespace(
            manifest=target,
            shards=2,
            history_junit=None,
            source_map=None,
            data_map=None,
            only_tests=["tests/test_b.py"],
        )
    )
    manifest = controller.load_manifest(target)
    assert set(manifest["test_file_sha256"]) == {"tests/test_b.py"}
    assert [name for shard in manifest["shards"] for name in shard["test_files"]] == ["tests/test_b.py"]
    assert "tests/test_a.py" not in manifest["test_file_sha256"]
    assert plan.is_file()


def test_payload_does_not_bundle_private_nested_artifacts(controller, tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "docs/artifacts").mkdir(parents=True)
    (root / "docs/artifacts/private.png").write_bytes(b"not a public figure")
    (root / "docs/public.md").write_text("Original public guide\n")
    (root / "jobs").mkdir()
    (root / "jobs/nurion_sources.local.json").write_text('{"private_path": "not for transfer"}')
    (root / "jobs/nurion_sources.example.json").write_text("{}")
    monkeypatch.setattr(controller, "ROOT", root)
    assert [p.relative_to(root).as_posix() for p in controller.payload_files()] == [
        "docs/public.md",
        "jobs/nurion_sources.example.json",
    ]


def test_run_sanitizes_pytest_environment_without_launching_tests(controller, tmp_path, monkeypatch):
    plan, _ = frozen_plan(controller, tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--deselect=tests/test_a.py::test_a")
    monkeypatch.setenv("PYTEST_PLUGINS", "unreviewed_plugin")
    monkeypatch.setenv("PYTHONPATH", "/unreviewed/source")
    seen = {}

    def child(command, **kwargs):
        seen.update(command=command, env=kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(controller.subprocess, "run", child)
    controller.run(
        SimpleNamespace(manifest=plan, results=tmp_path / "results", rank=0, size=1, source_map=None)
    )
    assert "-p" in seen["command"] and "hpc_tests" in seen["command"]
    assert "PYTEST_ADDOPTS" not in seen["env"] and "PYTEST_PLUGINS" not in seen["env"]
    assert "/unreviewed/source" not in seen["env"]["PYTHONPATH"]
    assert seen["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert seen["env"]["PIPELINE_HPC_CAMPAIGN_KIND"] == "full_suite"
