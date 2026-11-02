"""Optional source-checkout pins for reused material-engine calculations."""

import hashlib
import importlib
import inspect
import re
from contextlib import contextmanager
from pathlib import Path


def verify_material_source_pin(report, expected_source_sha256, *, executed=True):
    """Validate a stored source-report claim without importing or executing the owner.

    This checks report consistency, not a signature or independent proof of source
    execution. A withheld request cannot claim that the source guard was executed.
    """
    if report is None:
        if executed and expected_source_sha256 is not None:
            raise ValueError("executed material request lacks its declared source pin")
        return
    if not executed or expected_source_sha256 is None:
        raise ValueError("an unexecuted or unpinned request cannot claim a material source pin")
    if (
        not isinstance(expected_source_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_source_sha256) is None
    ):
        raise ValueError("stored material source pin requires a lowercase SHA-256")
    if (
        not isinstance(report, dict)
        or set(report) != {"before", "after", "source_unchanged"}
        or report["source_unchanged"] is not True
    ):
        raise ValueError("stored material source report has no unchanged-source confirmation")
    before, after = report["before"], report["after"]
    if (
        not isinstance(before, dict)
        or not isinstance(after, dict)
        or set(before) != {"source_tree_sha256", "source_tree_files"}
        or before != after
        or before.get("source_tree_sha256") != expected_source_sha256
        or type(before.get("source_tree_files")) is not int
        or before["source_tree_files"] <= 0
    ):
        raise ValueError("stored material source fingerprints differ from the reviewed pin")


def material_source_fingerprint(source_root):
    """Match check_integrations.revision's sorted relative-name and Python-byte tree hash."""
    root = Path(source_root)
    sources = sorted((root / "src").rglob("*.py"))
    if not sources:
        raise ValueError("source pin requires a material checkout with Python source files")
    fingerprint = hashlib.sha256()
    for path in sources:
        fingerprint.update(path.relative_to(root).as_posix().encode("utf-8"))
        fingerprint.update(path.read_bytes())
    return {"source_tree_sha256": fingerprint.hexdigest(), "source_tree_files": len(sources)}


@contextmanager
def material_source_guard(source_root=None, expected_source_sha256=None, client=None):
    """Check selected bytes, loaded package/evaluator provenance and source stability.

    The yielded mutable dictionary is completed on context exit. Serialize it only
    after leaving the context. With neither pin argument there is no native import.
    """
    if (source_root is None) != (expected_source_sha256 is None):
        raise ValueError("source pin requires both source_root and expected_source_sha256")
    if source_root is None:
        yield None
        return
    if (
        not isinstance(expected_source_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_source_sha256) is None
    ):
        raise ValueError("source pin requires a lowercase SHA-256 digest")
    root = Path(source_root)
    before = material_source_fingerprint(root)
    if before["source_tree_sha256"] != expected_source_sha256:
        raise ValueError("the material source tree differs from its reviewed source pin")
    package = importlib.import_module("matintegrity")
    package_file = getattr(package, "__file__", None)
    source = (root / "src").resolve()
    if package_file is None or not Path(package_file).resolve().is_relative_to(source):
        raise ValueError("the loaded material package does not belong to the pinned source checkout")
    evaluator = package.evaluate if client is None else getattr(client, "evaluator", None)
    try:
        evaluator_file = inspect.getfile(evaluator)
    except (TypeError, OSError) as exc:
        raise ValueError("the material evaluator has no inspectable pinned source") from exc
    if not Path(evaluator_file).resolve().is_relative_to(source):
        raise ValueError("the material evaluator does not belong to the pinned source checkout")
    registry_factory = getattr(client, "registry_factory", None) if client is not None else None
    if registry_factory is not None:
        try:
            registry_file = inspect.getfile(registry_factory)
        except (TypeError, OSError) as exc:
            raise ValueError("the material registry has no inspectable pinned source") from exc
        if not Path(registry_file).resolve().is_relative_to(source):
            raise ValueError("the material registry does not belong to the pinned source checkout")
    record = {"before": before, "after": None, "source_unchanged": False}
    try:
        yield record
    finally:
        after = material_source_fingerprint(root)
        record.update(after=after, source_unchanged=before == after)
        if before != after:
            raise ValueError("the material source tree changed during evaluation")
