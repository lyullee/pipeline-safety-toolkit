"""Export only Git-visible reviewed source, preserving bytes and excluding local evidence."""

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    forbidden_extensions = {
        ".pdf",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".gz",
        ".xlsx",
        ".rdata",
        ".db",
        ".sqlite",
        ".partial",
    }
    permitted_roots = {"src", "tests", "docs", "examples", "research", "scripts", "jobs", ".github"}
    permitted_root_files = {
        ".gitignore",
        ".gitattributes",
        "AGENTS.md",
        "CHANGELOG.md",
        "CITATION.cff",
        ".zenodo.json",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "requirements-hra.txt",
        "requirements-tested.txt",
    }
    credential = re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    )
    local_path = re.compile(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]")
    files, violations = [], []
    for name in sorted(set(names) - {""}):
        path = ROOT / name
        if not path.is_file():
            continue
        relative = Path(name)
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            violations.append({"file": name, "reason": "linked source outside the reviewed tree"})
            continue
        allowed = (
            relative.parts[0] in permitted_roots
            or name in permitted_root_files
            or name in {"data/README.md", "data/.gitignore"}
        )
        if not allowed or relative.suffix.lower() in forbidden_extensions:
            violations.append({"file": name, "reason": "unreviewed root or raw/archive asset"})
        payload = path.read_bytes()
        text = payload.decode("utf-8", errors="ignore")
        if credential.search(text) or local_path.search(text):
            violations.append({"file": name, "reason": "credential-like content or private user directory"})
        if re.search(r"\br\d{4}[a-z]\d{2}\b", text):
            violations.append({"file": name, "reason": "local HPC account"})
        files.append((relative.as_posix(), payload))
    if violations:
        print(json.dumps({"passed": False, "violations": violations}, ensure_ascii=False))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            archive.writestr("pipeline-safety-toolkit/" + name, payload)
    report = {
        "schema": "pipeline_integrity.public_source_export.v1",
        "files": len(files),
        "source_bytes": sum(len(data) for _, data in files),
        "archive_bytes": args.output.stat().st_size,
        "archive_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "member_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files},
        "passed": True,
        "git_history_included": False,
        "scope": "Reviewed Git-visible source snapshot; not automatic legal clearance or full physical qualification.",
    }
    args.output.with_suffix(".receipt.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("files", "archive_bytes", "archive_sha256", "passed")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
