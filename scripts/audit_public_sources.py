"""Bounded source-only publication candidates; never a copyright or execution clearance."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import re
import shlex
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(
    r"(?:\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
USER_PATH = re.compile(
    r"(?:\b[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\r\n\"'<>]+|/(?:Users|home)/[^/\s\"'<>]+/[^\r\n\"'<>]+)",
    re.I,
)
URL = re.compile(r"https?://[^\s<>\"']+")
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\n)]+)\)")
RAW_SUFFIXES = {
    ".pdf",
    ".zip",
    ".7z",
    ".rar",
    ".csv",
    ".xlsx",
    ".rdata",
    ".sqlite",
    ".db",
    ".wav",
    ".mp4",
    ".bin",
    ".whl",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".npy",
    ".npz",
    ".parquet",
    ".h5",
    ".hdf5",
}
MAGIC = (b"%PDF-", b"PK\x03\x04", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"SQLite format 3", b"MZ", b"\x1f\x8b")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def _payload_files(root):
    # Load this auditor's trusted sibling, never a Python file from the target root.
    path = Path(__file__).with_name("hpc_tests.py")
    spec = importlib.util.spec_from_file_location("public_audit_trusted_payload_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.payload_files(root)


def _manifest_files(root):
    """Project the current simple MANIFEST directives; no setuptools or target code is run."""
    manifest = root / "MANIFEST.in"
    if not manifest.is_file():
        return set(), []
    selected, unsupported = set(), []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        args = shlex.split(line, comments=True)
        if not args:
            continue
        mode, *values = args
        if mode in {"include", "recursive-include", "exclude", "prune"} and any(
            PurePosixPath(v).is_absolute() or ".." in PurePosixPath(v).parts or "\\" in v or ":" in v
            for v in values
        ):
            unsupported.append("unsafe_manifest_path")
            continue
        if mode == "include":
            for pattern in values:
                selected.update(p for p in root.glob(pattern) if p.is_file())
        elif mode == "recursive-include" and len(values) >= 2:
            directory, *patterns = values
            selected.update(
                p
                for p in (root / directory).rglob("*")
                if p.is_file() and any(fnmatch.fnmatch(p.name, pat) for pat in patterns)
            )
        elif mode == "exclude":
            selected = {
                p
                for p in selected
                if not any(fnmatch.fnmatch(p.relative_to(root).as_posix(), pat) for pat in values)
            }
        elif mode == "global-exclude":
            selected = {p for p in selected if not any(fnmatch.fnmatch(p.name, pat) for pat in values)}
        elif mode == "prune" and len(values) == 1:
            selected = {p for p in selected if not p.is_relative_to(root / values[0])}
        else:
            unsupported.append(mode)
    return selected, sorted(set(unsupported))


def _markdown_prose(text):
    """Mask fenced/inline code while retaining original offsets and line numbers."""
    rows, fence = [], None
    for row in text.splitlines(keepends=True):
        marker = re.match(r"(`{3,}|~{3,})", row.lstrip())
        if marker and fence is None:
            fence = marker.group()
            rows.append("".join("\n" if c == "\n" else " " for c in row))
        elif fence is not None:
            rows.append("".join("\n" if c == "\n" else " " for c in row))
            if marker and marker.group()[0] == fence[0] and len(marker.group()) >= len(fence):
                fence = None
        else:
            rows.append(row)
    result = "".join(rows)
    return re.sub(
        r"(`+)(.*?)\1", lambda m: "".join("\n" if c == "\n" else " " for c in m.group()), result, flags=re.S
    )


def audit_sources(root, *, maximum_file_bytes=16_000_000, maximum_total_bytes=512_000_000):
    root = Path(root).resolve(strict=True)
    if (
        type(maximum_file_bytes) is not int
        or type(maximum_total_bytes) is not int
        or not 0 < maximum_file_bytes <= maximum_total_bytes
    ):
        raise ValueError("explicit positive per-file/total source byte budgets are required")
    findings, pins, texts, pdf_domains = [], {}, {}, {}
    complete, total = True, 0

    def report(path, rule, severity, *, text=None, offset=None, matched=None):
        name = path.relative_to(root).as_posix() if isinstance(path, Path) else path
        if TOKEN.search(name):
            name = "<redacted-path-" + sha(name.encode())[:12] + ">"
        value = {"path": name, "rule": rule, "severity": severity}
        if offset is not None and text is not None:
            value["line"] = text.count("\n", 0, offset) + 1
        if matched is not None:
            value["candidate_sha256"] = sha(matched.encode("utf-8"))
        findings.append(value)

    try:
        payload = set(_payload_files(root))
    except (ValueError, OSError) as exc:
        return {
            "schema": "pipeline_integrity.public_source_audit.v1",
            "passed": False,
            "scan_complete": False,
            "findings": [
                {
                    "path": ".",
                    "rule": "unsafe_or_unreadable_payload",
                    "severity": "blocking",
                    "exception_type": type(exc).__name__,
                }
            ],
            "copyright_clearance_confirmed": False,
            "tests_executed": 0,
            "blocking_candidates": 1,
            "review_candidates": 0,
        }
    projected, unsupported = _manifest_files(root)
    for mode in unsupported:
        report("MANIFEST.in", "unsupported_manifest_directive", "review", matched=mode)
    # The mutable validation queue is excluded from transfer payloads to avoid
    # self-referential snapshots, but its handover declarations still need the
    # same bounded, path-checked static scan as public configuration.
    queue_metadata = root / "research/validation_queue.json"
    audit_metadata = {queue_metadata} if queue_metadata.is_file() else set()
    files = sorted(payload | projected | audit_metadata)
    if len(files) > 50000:
        raise ValueError("source file-count budget exceeded")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            report(path, "linked_or_outside_source_member", "blocking")
            continue
        parts = path.relative_to(root).parts
        if path in projected and path not in payload:
            report(path, "manifest_member_outside_hpc_public_allowlist", "review")
        if (
            any(
                p in {"private", "raw", "processed", "artifacts", "tmp", "repos", "external_repos", ".git"}
                or p.startswith(".venv")
                for p in parts
            )
            or path.name.startswith(".env")
            or (parts[0] == "jobs" and path.suffix == ".json" and not path.name.endswith(".example.json"))
        ):
            report(path, "private_raw_repository_or_local_configuration_member", "blocking")
        if path.suffix.lower() in RAW_SUFFIXES:
            report(path, "raw_or_binary_research_asset_selected", "blocking")
            continue  # In particular, never open an original PDF during this source audit.
        size = path.stat().st_size
        total += size
        if size > maximum_file_bytes or total > maximum_total_bytes:
            complete = False
            report(path, "source_scan_byte_budget_exceeded_not_truncated", "blocking")
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.read(maximum_file_bytes + 1)
        except OSError:
            complete = False
            report(path, "source_unreadable_during_scan", "blocking")
            continue
        if len(raw) != size or len(raw) > maximum_file_bytes:
            complete = False
            report(path, "source_changed_or_exceeded_budget_during_read", "blocking")
            continue
        name = path.relative_to(root).as_posix()
        pins["<redacted-path-" + sha(name.encode())[:12] + ">" if TOKEN.search(name) else name] = sha(raw)
        if any(raw.startswith(signature) for signature in MAGIC):
            report(path, "raw_archive_document_or_executable_disguised_as_source", "blocking")
            continue
        if path.suffix.lower() == ".png":
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            report(path, "non_utf8_source_requires_review", "review")
            continue
        texts[path] = text
        for match in TOKEN.finditer(text):
            report(
                path,
                "credential_like_content",
                "blocking",
                text=text,
                offset=match.start(),
                matched=match.group(),
            )
        masked = text
        for match in reversed(list(URL.finditer(text))):
            address = match.group().rstrip(").,;")
            try:
                parsed = urlsplit(address)
            except ValueError:
                report(
                    path, "malformed_external_url", "review", text=text, offset=match.start(), matched=address
                )
                masked = masked[: match.start()] + " " * len(match.group()) + masked[match.end() :]
                continue
            if parsed.username or parsed.password:
                report(
                    path, "url_contains_userinfo", "review", text=text, offset=match.start(), matched=address
                )
            if ".pdf" in parsed.path.lower():
                pdf_domains[parsed.hostname or "unknown"] = (
                    pdf_domains.get(parsed.hostname or "unknown", 0) + 1
                )
            masked = masked[: match.start()] + " " * len(match.group()) + masked[match.end() :]
        for match in USER_PATH.finditer(masked):
            report(
                path,
                "local_user_path_in_public_source",
                "blocking",
                text=text,
                offset=match.start(),
                matched=match.group(),
            )
        for match in re.finditer(r"file://(?:/|[A-Za-z0-9])[^\s\"'<>]+", masked, re.I):
            report(
                path,
                "local_file_uri_in_public_source",
                "review",
                text=text,
                offset=match.start(),
                matched=match.group(),
            )
        for match in re.finditer(r"[^\r\n\"'<>]{1,200}\.pdf\b", masked, re.I):
            if re.search(r"\b(?:book|textbook|chapter|scan)\b|서적|교재|스캔", match.group(), re.I):
                report(
                    path,
                    "local_book_document_reference_candidate",
                    "review",
                    text=text,
                    offset=match.start(),
                    matched=match.group(),
                )
        if path.suffix == ".py" and set(parts) & {"vendor", "vendored"}:
            report(path, "possible_vendored_external_implementation", "review")
        if path.suffix == ".md":
            prose = _markdown_prose(text)
            for match in MARKDOWN_LINK.finditer(prose):
                target = match.group(1).strip().removeprefix("<").removesuffix(">")
                try:
                    external = bool(urlsplit(target).scheme)
                except ValueError:
                    report(
                        path,
                        "malformed_document_link",
                        "review",
                        text=text,
                        offset=match.start(),
                        matched=target,
                    )
                    continue
                if external or target.startswith("#") or target.startswith("/"):
                    continue
                target = unquote(target.split("#", 1)[0].split("?", 1)[0])
                resolved = path.parent / target
                if target and (not resolved.resolve().is_relative_to(root) or not resolved.exists()):
                    report(
                        path,
                        "unresolved_or_outside_local_document_link",
                        "review",
                        text=text,
                        offset=match.start(),
                        matched=target,
                    )
            for match in re.finditer(r"(?:^>[^\n]*\n?){2,}", prose, re.M):
                if len(match.group()) > 1000 and not URL.search(match.group()):
                    report(
                        path,
                        "long_unattributed_blockquote_candidate",
                        "review",
                        text=text,
                        offset=match.start(),
                        matched=match.group(),
                    )
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not (root / name).is_file() or not (root / name).stat().st_size:
            report(name, "missing_project_license_or_dependency_notice", "blocking")
    provenance = {
        "dataset_entries": 0,
        "public_pdf_url_occurrences": sum(pdf_domains.values()),
        "pdf_url_domains": pdf_domains,
    }
    catalog = root / "src/pipeline_integrity/catalog_data/datasets.json"
    if catalog in texts:
        try:
            rows = json.loads(texts[catalog])["datasets"]
            provenance["dataset_entries"] = len(rows)
            for row in rows:
                missing = [
                    k
                    for k in (
                        "source",
                        "citation",
                        "version",
                        "license_id",
                        "rights_evidence_url",
                        "rights_review",
                        "origin",
                    )
                    if not row.get(k)
                ]
                if missing or row.get("raw_in_code_release") is not False:
                    report(
                        catalog,
                        "dataset_provenance_or_release_boundary_incomplete",
                        "review",
                        matched=str(row.get("id", "unknown")),
                    )
                if row.get("acquisition") == "allowed" and row.get("rights_review") != "reviewed_license":
                    report(
                        catalog,
                        "analysis_only_or_unresolved_acquisition_rights_scope",
                        "review",
                        matched=str(row.get("id", "unknown")),
                    )
        except (ValueError, KeyError, TypeError):
            report(catalog, "dataset_catalog_unreadable_for_static_audit", "blocking")
    queue = root / "research/validation_queue.json"
    handover = {
        "validation_queue_present": queue in texts,
        "prior_passes_not_current_source": None,
        "pending_policy_declared": False,
    }
    if queue in texts:
        try:
            value = json.loads(texts[queue])
            previous = value.get("last_completed_full_suite", {}).get("applies_to", "")
            handover["prior_passes_not_current_source"] = "previous" in previous.lower()
            policy = value.get("local_policy", "").lower()
            handover["pending_policy_declared"] = "static" in policy and (
                "defer" in policy or "nurion" in policy
            )
        except (ValueError, TypeError):
            report(queue, "validation_queue_unreadable", "review")
    if not all(
        (
            handover["validation_queue_present"],
            handover["prior_passes_not_current_source"],
            handover["pending_policy_declared"],
        )
    ):
        report(
            "research/validation_queue.json", "current_vs_prior_validation_boundary_requires_review", "review"
        )
    blocking = sum(f["severity"] == "blocking" for f in findings)
    return {
        "schema": "pipeline_integrity.public_source_audit.v1",
        "passed": not blocking and complete,
        "scan_complete": complete,
        "selected_source_files": len(files),
        "scanned_bytes": total,
        "file_sha256": pins,
        "findings": findings,
        "blocking_candidates": blocking,
        "review_candidates": sum(f["severity"] == "review" for f in findings),
        "provenance": provenance,
        "handover": handover,
        "scope": "current source/HPC allowlist plus simple MANIFEST projection; no archive build, URL fetch, original-document comparison or scientific execution",
        "copyright_clearance_confirmed": False,
        "verbatim_original_comparison_performed": False,
        "original_pdf_read": False,
        "tests_executed": 0,
        "native_executions": 0,
        "auditor_sha256": sha(Path(__file__).read_bytes()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_sources(args.root)
    if args.output:
        selected = args.output.resolve()
        root = args.root.resolve()
        if selected.is_relative_to(root) and not selected.is_relative_to(root / "artifacts"):
            raise ValueError("put mutable audit output under artifacts or outside the source payload")
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "passed",
                    "scan_complete",
                    "blocking_candidates",
                    "review_candidates",
                    "tests_executed",
                )
            }
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
