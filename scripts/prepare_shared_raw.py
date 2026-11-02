"""Atomically stage one pinned large raw file for a read-only HPC campaign."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path


def _data_tool():
    path = Path(__file__).resolve().with_name("hpc_data.py")
    spec = importlib.util.spec_from_file_location("pipeline_shared_raw_contract", path)
    if spec is None or spec.loader is None:
        raise ValueError("raw-data transfer contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity(stat_result):
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_nlink,
    )


def stage(source, raw_root, plan, *, maximum_bytes):
    tool = _data_tool()
    transfer = tool.validate_plan(plan)
    if len(transfer["files"]) != 1:
        raise ValueError("single-source staging requires exactly one transfer-plan file")
    item = transfer["files"][0]
    if type(maximum_bytes) is not int or not item["bytes"] <= maximum_bytes <= 8 * 1024**3:
        raise ValueError("explicit shared staging byte budget is invalid")
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("shared staging source must be one ordinary file")
    before = source.stat()
    if before.st_nlink != 1 or before.st_size != item["bytes"]:
        raise ValueError("shared staging source size/link identity differs")
    root = Path(raw_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("shared raw root must be an existing non-symlink directory")
    root = root.resolve()
    relative = tool.safe_relative(item["path"])
    target = root.joinpath(*relative.parts)
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError("shared staging target escapes its raw root")
    if target.exists() or target.is_symlink():
        raise FileExistsError("shared staging never overwrites an existing target")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink() or not target.parent.resolve().is_relative_to(root):
        raise ValueError("shared staging target parent is linked or escaped")
    temporary = target.with_name("." + target.name + f".partial-{os.getpid()}")
    if temporary.exists():
        raise FileExistsError("shared staging temporary target already exists")
    checksum = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            opened = os.fstat(incoming.fileno())
            while copied <= maximum_bytes:
                block = incoming.read(min(1024 * 1024, maximum_bytes - copied + 1))
                if not block:
                    break
                outgoing.write(block)
                checksum.update(block)
                copied += len(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
            after_open = os.fstat(incoming.fileno())
        after = source.stat()
        if (
            copied != item["bytes"]
            or checksum.hexdigest() != item["sha256"]
            or not (_identity(before) == _identity(opened) == _identity(after_open) == _identity(after))
            or source.is_symlink()
        ):
            raise ValueError("shared staging source changed or failed its supplier pin")
        os.replace(temporary, target)
        if os.name == "posix":
            target.chmod(0o444)
            target.parent.chmod(0o555)
            root.chmod(0o555)
        checked = tool.check(transfer, root, maximum_bytes=maximum_bytes)
        if checked["verified"] is not True:
            raise ValueError("atomically staged shared target failed transfer verification")
    finally:
        if temporary.exists():
            temporary.unlink()
    body = {
        "schema": "pipeline_integrity.shared_raw_stage.v1",
        "plan_digest": transfer["plan_digest"],
        "dataset_id": item["dataset_id"],
        "canonical_relative_path": item["path"],
        "bytes": copied,
        "sha256": checksum.hexdigest(),
        "source_unchanged": True,
        "target_verified": True,
        "posix_write_bits_cleared": os.name == "posix",
        "absolute_paths_recorded": False,
        "raw_in_code_release": False,
    }
    return {**body, "record_digest": tool.digest(body)}


def verify_and_seal_existing(raw_root, plan, *, maximum_bytes):
    """Verify files already at canonical plan paths, then remove POSIX write bits."""
    tool = _data_tool()
    transfer = tool.validate_plan(plan)
    if type(maximum_bytes) is not int or not transfer["total_bytes"] <= maximum_bytes <= 8 * 1024**3:
        raise ValueError("explicit shared verification byte budget is invalid")
    root = Path(raw_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("shared raw root must be an existing non-symlink directory")
    root = root.resolve()
    before = tool.check(transfer, root, maximum_bytes=maximum_bytes)
    if before["verified"] is not True:
        raise ValueError("existing canonical shared data differs from the transfer plan")
    paths = []
    for item in transfer["files"]:
        target = root.joinpath(*tool.safe_relative(item["path"]).parts)
        if target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1:
            raise ValueError("existing shared target must be an ordinary single-link file")
        paths.append(target)
    if os.name == "posix":
        directories = {root}
        for target in paths:
            target.chmod(0o444)
            parent = target.parent
            while True:
                directories.add(parent)
                if parent == root:
                    break
                parent = parent.parent
        for directory in sorted(directories, key=lambda value: len(value.parts), reverse=True):
            directory.chmod(0o555)
    after = tool.check(transfer, root, maximum_bytes=maximum_bytes)
    if after != before or after["verified"] is not True:
        raise ValueError("shared target changed while applying its read-only seal")
    body = {
        "schema": "pipeline_integrity.shared_raw_existing.v1",
        "plan_digest": transfer["plan_digest"],
        "datasets": [item["id"] for item in transfer["datasets"]],
        "files": len(transfer["files"]),
        "bytes": transfer["total_bytes"],
        "source_verified_before_and_after_seal": True,
        "posix_write_bits_cleared": os.name == "posix",
        "absolute_paths_recorded": False,
        "raw_in_code_release": False,
    }
    return {**body, "record_digest": tool.digest(body)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--maximum-bytes", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    tool = _data_tool()
    plan = tool.read_json(args.plan)
    result = (
        verify_and_seal_existing(args.raw_root, plan, maximum_bytes=args.maximum_bytes)
        if args.verify_existing
        else stage(args.source, args.raw_root, plan, maximum_bytes=args.maximum_bytes)
    )
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": "existing_verified_and_sealed" if args.verify_existing else "staged_and_sealed",
                "record_digest": result["record_digest"],
            }
        )
    )


if __name__ == "__main__":
    main()
