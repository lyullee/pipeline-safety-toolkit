"""Source pins bind actual loaded code, preserve laziness and detect concurrent edits."""

import hashlib
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from pipeline_integrity.material_source import (
    material_source_fingerprint,
    material_source_guard,
    verify_material_source_pin,
)


def test_stored_source_report_requires_matching_positive_source_inventory_without_native_import():
    before = {"source_tree_sha256": "a" * 64, "source_tree_files": 76}
    verify_material_source_pin({"before": before, "after": before, "source_unchanged": True}, "a" * 64)
    verify_material_source_pin(None, None)
    verify_material_source_pin(None, "a" * 64, executed=False)


@pytest.mark.parametrize(
    "mutation",
    ["unchanged", "after", "zero_files", "boolean_files", "wrong_hash", "unexecuted", "unpinned", "missing"],
)
def test_stored_source_report_cannot_relabel_failed_missing_or_different_pin(mutation):
    before = {"source_tree_sha256": "a" * 64, "source_tree_files": 76}
    report = {"before": before, "after": dict(before), "source_unchanged": True}
    expected, executed = "a" * 64, True
    if mutation == "unchanged":
        report["source_unchanged"] = False
    elif mutation == "after":
        report["after"]["source_tree_sha256"] = "b" * 64
    elif mutation in ("zero_files", "boolean_files"):
        report["before"]["source_tree_files"] = 0 if mutation == "zero_files" else True
        report["after"] = dict(report["before"])
    elif mutation == "wrong_hash":
        expected = "b" * 64
    elif mutation == "unexecuted":
        executed = False
    elif mutation == "unpinned":
        expected = None
    else:
        report = None
    with pytest.raises(ValueError, match="source|pin"):
        verify_material_source_pin(report, expected, executed=executed)


@pytest.mark.parametrize("kind", ["profile", "hydrogen"])
def test_native_facade_verifiers_reject_resealed_false_source_reports(kind):
    from pipeline_integrity.contracts import digest
    from pipeline_integrity.hydrogen_transport import (
        SEMI,
        evaluate_hydrogen_transport,
        verify_hydrogen_transport_record,
    )
    from pipeline_integrity.profile_capacity import evaluate_profile_capacity, verify_profile_capacity_record

    if kind == "profile":
        request, context = runpy.run_path("examples/profile_capacity.py")["make_case"]()
        spy = runpy.run_path("tests/test_profile_capacity.py")["OwnerSpy"]()
        evaluator, verifier = evaluate_profile_capacity, verify_profile_capacity_record
    else:
        requests, context = runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()
        request = requests[SEMI]
        spy = runpy.run_path("tests/test_characterization.py")["OwnerSpy"]()
        evaluator, verifier = evaluate_hydrogen_transport, verify_hydrogen_transport_record
    record = evaluator(request, context, client=spy)
    record["request"]["owner_source_tree_sha256"] = "a" * 64
    tree = {"source_tree_sha256": "a" * 64, "source_tree_files": 76}
    record["source_pin"] = {"before": tree, "after": tree, "source_unchanged": False}
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="source"):
        verifier(record)


@pytest.mark.parametrize("kind", ["profile", "hydrogen"])
def test_request_constructor_checks_work_budget_before_json_serialization(monkeypatch, kind):
    if kind == "profile":
        import pipeline_integrity.profile_capacity as module

        request, _ = runpy.run_path("examples/profile_capacity.py")["make_case"]()
        values = request.model_dump(mode="python") | {"maximum_points": 2}
        cls = module.ProfileCapacityRequest
    else:
        import pipeline_integrity.hydrogen_transport as module

        requests, _ = runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()
        values = requests[module.KINETIC].model_dump(mode="python") | {"maximum_cells": 7}
        cls = module.HydrogenTransportRequest

    def unexpected(*args):
        raise AssertionError("oversized request was serialized before its work budget")

    monkeypatch.setattr(module, "json_value", unexpected)
    with pytest.raises(ValueError, match="budget"):
        cls.model_validate(values)


def authored_owner(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    package_path = root / "src" / "matintegrity" / "__init__.py"
    package_path.parent.mkdir(parents=True)
    source = "def evaluate(*args, **kwargs):\n    return None\n"
    package_path.write_text(source, encoding="utf-8")
    package = ModuleType("matintegrity")
    package.__file__ = str(package_path)
    exec(compile(source, str(package_path), "exec"), package.__dict__)
    monkeypatch.setitem(sys.modules, "matintegrity", package)
    return root, package, package_path


def test_no_pin_leaves_the_native_owner_unimported(monkeypatch):
    def unexpected(name):
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr("pipeline_integrity.material_source.importlib.import_module", unexpected)
    with material_source_guard() as record:
        assert record is None


def test_source_fingerprint_matches_relative_src_names_and_bytes(tmp_path):
    root = tmp_path / "owner"
    (root / "src" / "package").mkdir(parents=True)
    (root / "src" / "package" / "a.py").write_bytes(b"a=1\n")
    (root / "src" / "package" / "z.py").write_bytes(b"z=2\n")
    expected = hashlib.sha256(b"src/package/a.py" + b"a=1\n" + b"src/package/z.py" + b"z=2\n").hexdigest()
    assert material_source_fingerprint(root) == {"source_tree_sha256": expected, "source_tree_files": 2}


def test_loaded_source_and_evaluator_match_and_record_has_no_paths(tmp_path, monkeypatch):
    root, package, _ = authored_owner(tmp_path, monkeypatch)
    before = material_source_fingerprint(root)
    for client in (None, SimpleNamespace(evaluator=package.evaluate)):
        with material_source_guard(root, before["source_tree_sha256"], client) as record:
            assert record["after"] is None
        assert record == {"before": before, "after": before, "source_unchanged": True}
        assert str(tmp_path) not in str(record)


@pytest.mark.parametrize(
    "which", ["package", "evaluator", "uninspectable", "registry", "registry_uninspectable"]
)
def test_loaded_code_cannot_be_from_another_checkout(tmp_path, monkeypatch, which):
    root, package, _ = authored_owner(tmp_path, monkeypatch)
    expected = material_source_fingerprint(root)["source_tree_sha256"]
    client = None
    if which == "package":
        package.__file__ = str(tmp_path / "different" / "__init__.py")
    elif which == "evaluator":
        client = SimpleNamespace(evaluator=lambda *args: None)
    elif which == "uninspectable":
        client = SimpleNamespace(evaluator=object())
    elif which == "registry":
        client = SimpleNamespace(evaluator=package.evaluate, registry_factory=lambda: None)
    else:
        client = SimpleNamespace(evaluator=package.evaluate, registry_factory=object())
    with pytest.raises(ValueError, match="pinned source"):
        with material_source_guard(root, expected, client):
            pytest.fail("a mismatched implementation was yielded")


def test_source_change_during_body_or_exception_is_detected(tmp_path, monkeypatch):
    root, _, source = authored_owner(tmp_path, monkeypatch)
    expected = material_source_fingerprint(root)["source_tree_sha256"]
    with pytest.raises(ValueError, match="changed during"):
        with material_source_guard(root, expected) as record:
            source.write_text("# concurrent change\n", encoding="utf-8")
            raise RuntimeError("underlying calculation failed")
    assert record["before"] != record["after"] and not record["source_unchanged"]


def test_unchanged_sources_preserve_underlying_calculation_exception(tmp_path, monkeypatch):
    root, _, _ = authored_owner(tmp_path, monkeypatch)
    expected = material_source_fingerprint(root)["source_tree_sha256"]
    with pytest.raises(RuntimeError, match="original failure"):
        with material_source_guard(root, expected) as record:
            raise RuntimeError("original failure")
    assert record["source_unchanged"]


@pytest.mark.parametrize("expected", ["0" * 64, "A" * 64, "invalid", True])
def test_pin_mismatch_or_invalid_digest_precedes_owner_import(tmp_path, monkeypatch, expected):
    root, _, _ = authored_owner(tmp_path, monkeypatch)

    def unexpected(name):
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr("pipeline_integrity.material_source.importlib.import_module", unexpected)
    with pytest.raises(ValueError, match="source pin|SHA-256"):
        with material_source_guard(root, expected):
            pytest.fail("an invalid source pin was yielded")


def test_pin_arguments_are_a_pair_and_source_root_must_exist(tmp_path):
    for args in ((tmp_path, None), (None, "0" * 64)):
        with pytest.raises(ValueError, match="both source_root"):
            with material_source_guard(*args):
                pytest.fail("an incomplete source pin was yielded")
    with pytest.raises(ValueError, match="Python source"):
        with material_source_guard(tmp_path, "0" * 64):
            pytest.fail("an empty source tree was yielded")
