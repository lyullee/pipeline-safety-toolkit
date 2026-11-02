"""Freeze and check selected raw-data transfers; never run models or redistribute data."""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

SCHEMA = "pipeline_integrity.hpc_data_transfer.v1"
MAXIMUM_METADATA_BYTES = 16 * 1024 * 1024


def read_json(path):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAXIMUM_METADATA_BYTES + 1)
    if len(raw) > MAXIMUM_METADATA_BYTES:
        raise ValueError("metadata byte budget exceeded")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("unsafe relative data path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("unsafe relative data path")
    return path


def validate_plan(plan):
    if set(plan) != {"schema", "inventory_digest", "datasets", "files", "total_bytes", "plan_digest"}:
        raise ValueError("unexpected transfer plan fields")
    body = {key: value for key, value in plan.items() if key != "plan_digest"}
    if plan["schema"] != SCHEMA or digest(body) != plan["plan_digest"]:
        raise ValueError("transfer plan digest/schema mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", plan["inventory_digest"]):
        raise ValueError("invalid inventory digest")
    if not isinstance(plan["datasets"], list) or not plan["datasets"] or not plan["files"]:
        raise ValueError("select at least one acquired dataset")
    ids = []
    for dataset in plan["datasets"]:
        if set(dataset) != {"id", "license_id", "rights_review", "citation", "raw_in_code_release"}:
            raise ValueError("invalid dataset provenance")
        if dataset["raw_in_code_release"] is not False:
            raise ValueError("raw data must remain outside code releases")
        if not all(
            isinstance(dataset[key], str) and dataset[key]
            for key in ("id", "license_id", "rights_review", "citation")
        ):
            raise ValueError("missing dataset provenance")
        safe_relative(dataset["id"])
        if "/" in dataset["id"]:
            raise ValueError("dataset id must be one path component")
        ids.append(dataset["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dataset ids")
    seen = set()
    used = set()
    total = 0
    for item in plan["files"]:
        if set(item) != {"dataset_id", "path", "bytes", "sha256"}:
            raise ValueError("invalid file contract")
        path = safe_relative(item["path"])
        if item["dataset_id"] not in ids or len(path.parts) < 2 or path.parts[0] != item["dataset_id"]:
            raise ValueError("file/dataset binding mismatch")
        if item["path"].casefold() in seen:
            raise ValueError("duplicate or case-colliding data paths")
        seen.add(item["path"].casefold())
        used.add(item["dataset_id"])
        if (
            type(item["bytes"]) is not int
            or item["bytes"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise ValueError("invalid expected file size/hash")
        total += item["bytes"]
    if used != set(ids) or type(plan["total_bytes"]) is not int or plan["total_bytes"] != total:
        raise ValueError("incomplete dataset or byte total")
    return plan


def prepare(inventory, selected):
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("select unique dataset ids")
    available = {item["id"]: item for item in inventory["datasets"]}
    if len(available) != len(inventory["datasets"]):
        raise ValueError("duplicate inventory dataset ids")
    datasets, files = [], []
    for name in selected:
        if name not in available:
            raise ValueError(f"unknown dataset: {name}")
        item = available[name]
        if item.get("acquisition_policy") != "allowed" or not item.get("selected_assets_complete"):
            raise ValueError("dataset acquisition is not complete/allowed")
        datasets.append(
            {
                key: item[key]
                for key in ("id", "license_id", "rights_review", "citation", "raw_in_code_release")
            }
        )
        for source in item["files"]:
            files.append(
                {
                    "dataset_id": name,
                    "path": f"{name}/{source['filename']}",
                    "bytes": source["bytes"],
                    "sha256": source["sha256"],
                }
            )
    body = {
        "schema": SCHEMA,
        "inventory_digest": digest(inventory),
        "datasets": datasets,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }
    return validate_plan({**body, "plan_digest": digest(body)})


def _directory_identity(path):
    """Return a no-follow identity for one directory, or None if it changed type."""
    try:
        info = Path(path).lstat()
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def check(plan, raw_root, *, maximum_bytes):
    plan = validate_plan(plan)
    if type(maximum_bytes) is not int or maximum_bytes < plan["total_bytes"]:
        raise ValueError("explicit data verification byte budget exceeded")
    root = Path(raw_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("raw root must be an existing non-symlink directory")
    root = root.resolve()
    results = []
    directory_identities = {}
    directory_tree_stable = True
    root_identity = _directory_identity(root)
    if root_identity is None:
        raise ValueError("raw root identity could not be pinned")
    directory_identities[root] = root_identity
    for item in plan["files"]:
        relative = safe_relative(item["path"])
        path = root.joinpath(*relative.parts)
        # Check every component; resolving only the final path hides interior links.
        parents = [path, *path.parents]
        if any(value.is_symlink() for value in parents[: parents.index(root)]):
            raise ValueError("linked data path is not accepted")
        if not path.resolve().is_relative_to(root):
            raise ValueError("data path escapes raw root")
        for depth in range(1, len(relative.parts)):
            directory = root.joinpath(*relative.parts[:depth])
            if directory.exists() or directory.is_symlink():
                observed = _directory_identity(directory)
                if observed is None:
                    directory_tree_stable = False
                    continue
                previous = directory_identities.setdefault(directory, observed)
                if observed != previous:
                    directory_tree_stable = False
        status = "missing"
        actual_sha = None
        if path.exists():
            before = path.stat()
            if not path.is_file() or before.st_nlink != 1:
                raise ValueError("data must be a regular single-link file")
            status = "size_mismatch"
            if before.st_size == item["bytes"]:
                value = hashlib.sha256()
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    remaining = item["bytes"]
                    while remaining:
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        value.update(block)
                        remaining -= len(block)
                    after_open = os.fstat(stream.fileno())
                after = path.stat()

                def identity(stat):
                    return (
                        stat.st_dev,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_nlink,
                    )

                # Windows path stat and descriptor fstat may expose different ctime
                # meanings. Compare ctime within each API, never across the two.
                unchanged = (
                    identity(before) == identity(opened) == identity(after_open) == identity(after)
                    and before.st_ctime_ns == after.st_ctime_ns
                    and opened.st_ctime_ns == after_open.st_ctime_ns
                    and not path.is_symlink()
                )
                actual_sha = value.hexdigest()
                status = (
                    "verified" if unchanged and actual_sha == item["sha256"] else "changed_or_hash_mismatch"
                )
        results.append({"path": item["path"], "status": status, "actual_sha256": actual_sha})
    for directory, expected in directory_identities.items():
        if _directory_identity(directory) != expected:
            directory_tree_stable = False
    return {
        "schema": "pipeline_integrity.hpc_data_check.v1",
        "plan_digest": plan["plan_digest"],
        "directory_tree_stable": directory_tree_stable,
        "verified": directory_tree_stable and all(item["status"] == "verified" for item in results),
        "files": results,
        "numerical_tests_executed": 0,
        "redistribution_clearance": False,
    }


def save_new(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("prepare")
    plan.add_argument("--inventory", type=Path, required=True)
    plan.add_argument("--only", nargs="+", required=True)
    plan.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("check")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--raw-root", type=Path, required=True)
    verify.add_argument("--maximum-bytes", type=int, required=True)
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(read_json(args.inventory), args.only)
    else:
        result = check(read_json(args.plan), args.raw_root, maximum_bytes=args.maximum_bytes)
    save_new(args.output, result)
    if args.command == "check" and not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
