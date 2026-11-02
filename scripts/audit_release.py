"""Inspect built source/wheel member names and text for release-boundary violations."""

import argparse
import hashlib
import json
import re
import stat
import tarfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


def audit(path):
    if path.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            directories = {info.filename for info in infos if info.is_dir()}
            unsupported = [info.filename for info in infos if stat.S_ISLNK(info.external_attr >> 16)]
            entries = [
                (info.filename, archive.read(info))
                for info in infos
                if not info.is_dir() and info.filename not in unsupported
            ]
        wheel = path.suffix == ".whl"
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
            directories = {member.name for member in archive.getmembers() if member.isdir()}
            unsupported = [
                member.name for member in archive.getmembers() if not (member.isfile() or member.isdir())
            ]
            entries = [
                (member.name, archive.extractfile(member).read())
                for member in archive.getmembers()
                if member.isfile()
            ]
        wheel = False
    violations = [
        {"member": name, "reason": "duplicate archive member"}
        for name, count in Counter(names).items()
        if count > 1
    ]
    violations.extend({"member": name, "reason": "unsupported archive member type"} for name in unsupported)
    allowed_metadata = {
        "tests/data/mechanics_input_baselines_223.json",
        "data/README.md",
        "data/.gitignore",
    }
    for name in names:
        logical = PurePosixPath(name)
        parts = logical.parts if wheel else logical.parts[1:]
        if (
            logical.is_absolute()
            or ":" in name
            or "\\" in name
            or ".." in logical.parts
            or any(
                part in {"data", "artifacts", "tmp", ".venv", ".venv-core", "__pycache__", ".git"}
                for part in parts
            ) and "/".join(parts) not in allowed_metadata
            and not (name in directories and "/".join(parts) == "tests/data")
        ):
            violations.append({"member": name, "reason": "excluded source or runtime directory"})
        if logical.suffix.lower() in {
            ".pdf",
            ".zip",
            ".7z",
            ".rar",
            ".xlsx",
            ".rdata",
            ".sqlite",
            ".db",
            ".partial",
        }:
            violations.append({"member": name, "reason": "excluded raw or binary research asset"})
        if logical.name.startswith(".env") and logical.name != ".env.example":
            violations.append({"member": name, "reason": "environment configuration"})
    for name, payload in entries:
        text = payload.decode("utf-8", errors="ignore")
        if re.search(
            r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
            text,
        ):
            violations.append({"member": name, "reason": "credential-like content"})
        if re.search(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]", text):
            violations.append({"member": name, "reason": "local user directory in public artifact"})
    return {
        "archive": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "members": len(entries),
        "passed": not violations,
        "violations": violations,
    }


def check_consistency(root, wheel, sdist):
    """Compare release bytes to the workspace and the core installation receipt."""
    root, wheel, sdist = Path(root), Path(wheel), Path(sdist)
    issues, package_names, source_names = [], set(), set()
    wheel_count = source_count = 0
    generated = []
    package_root = root / "src/pipeline_integrity"
    expected = {
        "pipeline_integrity/" + p.relative_to(package_root).as_posix()
        for p in package_root.rglob("*")
        if p.is_file() and (p.suffix in {".py", ".json"} or p.name == "py.typed")
    }
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        issues.extend(
            {"member": name, "reason": "duplicate wheel member"}
            for name, count in Counter(info.filename for info in infos).items()
            if count > 1
        )
        for info in infos:
            name = info.filename
            if (
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or ":" in name
            ):
                issues.append({"member": name, "reason": "unsafe package path"})
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                issues.append({"member": name, "reason": "unsupported wheel member type"})
                continue
            if not name.startswith("pipeline_integrity/") or name.endswith("/"):
                continue
            package_names.add(name)
            source = root / "src" / name
            if not source.is_file() or source.read_bytes() != archive.read(info):
                issues.append({"member": name, "reason": "wheel differs from workspace source"})
            else:
                wheel_count += 1
    for name in sorted(expected - package_names):
        issues.append({"member": name, "reason": "workspace package file missing from wheel"})
    receipt = None
    with tarfile.open(sdist) as archive:
        roots = {
            PurePosixPath(member.name).parts[0]
            for member in archive.getmembers()
            if PurePosixPath(member.name).parts
        }
        if len(roots) != 1:
            issues.append({"member": ".", "reason": "source distribution requires one common root"})
        issues.extend(
            {"member": name, "reason": "duplicate source distribution member"}
            for name, count in Counter(archive.getnames()).items()
            if count > 1
        )
        for member in archive.getmembers():
            logical = PurePosixPath(member.name)
            if logical.is_absolute() or ".." in logical.parts or "\\" in member.name or ":" in member.name:
                issues.append({"member": member.name, "reason": "unsafe source distribution path"})
                continue
            if not (member.isfile() or member.isdir()):
                issues.append(
                    {"member": member.name, "reason": "unsupported source distribution member type"}
                )
                continue
            if not member.isfile():
                continue
            relative = PurePosixPath(*logical.parts[1:])
            name = relative.as_posix()
            source_names.add(name)
            payload = archive.extractfile(member).read()
            source = root / name
            if source.is_file():
                if source.read_bytes() == payload:
                    source_count += 1
                else:
                    issues.append({"member": name, "reason": "source distribution differs from workspace"})
            elif name in {"PKG-INFO", "setup.cfg"}:
                generated.append(name)
            else:
                issues.append({"member": name, "reason": "unexplained source distribution member"})
            if name == "research/core_installation.json":
                receipt = json.loads(payload)
    for name in sorted({"src/" + name for name in expected} - source_names):
        issues.append({"member": name, "reason": "workspace package file missing from source distribution"})
    core_matches = bool(
        receipt
        and receipt.get("passed") is True
        and receipt.get("installed_wheel_used") is True
        and receipt.get("wheel_sha256") == hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    if not core_matches:
        issues.append(
            {
                "member": "research/core_installation.json",
                "reason": "core receipt does not confirm this wheel",
            }
        )
    return {
        "passed": not issues,
        "wheel_package_files_match_workspace": wheel_count,
        "source_distribution_files_match_workspace": source_count,
        "generated_source_distribution_members": sorted(generated),
        "source_distribution_core_receipt_matches_wheel_sha256": core_matches,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="*", type=Path)
    parser.add_argument("--check-consistency", action="store_true")
    args = parser.parse_args()
    archives = args.archives or sorted(Path("dist").glob("*.whl")) + sorted(Path("dist").glob("*.tar.gz"))
    if not archives:
        raise SystemExit("build archives before auditing")
    result = {
        "schema": "pipeline_integrity.release_audit.v1",
        "archives": [audit(path) for path in archives],
        "automated_scan_is_not_a_legal_opinion": True,
    }
    if args.check_consistency:
        wheels = [p for p in archives if p.suffix == ".whl"]
        sources = [p for p in archives if p.name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sources) != 1:
            raise SystemExit("consistency checks require exactly one wheel and one source distribution")
        result["consistency"] = check_consistency(Path.cwd(), wheels[0], sources[0])
    Path("research").mkdir(exist_ok=True)
    Path("research/release_audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    passed = all(row["passed"] for row in result["archives"]) and result.get("consistency", {}).get(
        "passed", True
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
