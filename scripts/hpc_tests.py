"""Prepare deterministic test shards and run/aggregate them under an MPI launcher.

No MPI Python binding is needed: each launched process reads its rank from the
launcher environment. Preparing a plan never imports pytest or runs a test.
"""

import argparse
import faulthandler
import hashlib
import importlib.util
import json
import math
import os
import platform
import shutil
import stat
import statistics
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "pipeline_integrity.hpc_test_manifest.v1"
MAXIMUM_OWNER_FILES = 50000
MAXIMUM_OWNER_BYTES = 512 * 1024 * 1024
MAXIMUM_DATA_STAGE_BYTES = 512 * 1024 * 1024
MAXIMUM_SHARED_DATA_VERIFY_BYTES = 8 * 1024 * 1024 * 1024


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _data_tool():
    """Load the sibling transfer-contract implementation without importing models."""
    path = Path(__file__).resolve().with_name("hpc_data.py")
    spec = importlib.util.spec_from_file_location("pipeline_hpc_data_contract", path)
    if spec is None or spec.loader is None:
        raise ValueError("raw-data transfer contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def relative(path, root=None):
    root = ROOT if root is None else Path(root)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("test/source snapshots must stay inside this source checkout")
    return path.relative_to(root).as_posix()


def payload_files(root=None):
    """An explicit public-source allowlist, shared by transfer and rank isolation."""
    root = ROOT if root is None else Path(root)
    chosen = []
    for name in (
        "pyproject.toml",
        "MANIFEST.in",
        "requirements-tested.txt",
        "requirements-hra.txt",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "README.md",
        "AGENTS.md",
        ".gitattributes",
    ):
        path = root / name
        if path.is_file():
            relative(path, root)
            chosen.append(path)
    allowed = {
        "src": {".py", ".json", ".typed"},
        "tests": {".py", ".json", ".inp"},
        "examples": {".py", ".json", ".inp"},
        "scripts": {".py"},
        "docs": {".md", ".png"},
        "research": {".json", ".md"},
        "jobs": {".pbs", ".json"},
        ".github": {".yml"},
    }
    for folder, suffixes in allowed.items():
        for path in sorted((root / folder).rglob("*")):
            if folder == "jobs" and path.suffix == ".json" and not path.name.endswith(".example.json"):
                continue
            if (
                path.is_file()
                and path.suffix in suffixes
                and not (folder == "research" and path.name == "validation_queue.json")
                and not path.name.startswith(".env")
                and not set(path.relative_to(root).parts).intersection(
                    {
                        "__pycache__",
                        "artifacts",
                        "raw",
                        "processed",
                        "private",
                        "tmp",
                        ".git",
                        ".venv",
                        "build",
                        "dist",
                    }
                )
            ):
                relative(path, root)
                chosen.append(path)
    # These pinned native consequence records are regression inputs, not an
    # unrestricted artifacts tree. Keep their exact paths and hashes in the
    # worker source manifest so actual-result forgery cases run on HPC.
    for name in (
        "artifacts/consequences/wind_270.json",
        "artifacts/consequences/unadapted.json",
    ):
        path = root / name
        if path.is_file():
            relative(path, root)
            chosen.append(path)
    return sorted(set(chosen))


def snapshots(root=None):
    root = ROOT if root is None else Path(root)
    selected = payload_files(root)
    tests = {
        relative(p, root): sha(p)
        for p in selected
        if p.relative_to(root).parts[0] == "tests" and p.name.startswith("test_") and p.suffix == ".py"
    }
    package = {
        relative(p, root): sha(p)
        for p in selected
        if p.relative_to(root).as_posix().startswith("src/pipeline_integrity/")
    }
    runtime_names = set(tests) | set(package)
    # This queue stores the manifest's own path and digest, so fingerprinting it
    # would make each queue update invalidate the manifest that the queue names.
    config = {
        relative(p, root): sha(p)
        for p in selected
        if relative(p, root) not in runtime_names and relative(p, root) != "research/validation_queue.json"
    }
    return tests, package, config


def snapshots_for_manifest(manifest, root=None):
    tests, package, config = snapshots(root)
    selected = manifest.get("selected_test_modules")
    if selected is not None:
        tests = {name: tests[name] for name in selected if name in tests}
    return tests, package, config


def _source_entries(path):
    if path is None:
        return {}
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, dict):
        raise ValueError("source map must associate names with reviewed Python source directories")
    result = {}
    for name, value in sorted(entries.items()):
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(c in name for c in "/\\:")
            or name in {".", ".."}
        ):
            raise ValueError("source names and paths must be explicit strings")
        if isinstance(value, str):
            imported = Path(value).expanduser().resolve()
            project = (
                imported.parent
                if imported.name == "src" and (imported.parent / "pyproject.toml").is_file()
                else imported
            )
        elif (
            isinstance(value, dict)
            and set(value) == {"project_root", "import_root"}
            and all(isinstance(v, str) for v in value.values())
        ):
            project = Path(value["project_root"]).expanduser().resolve()
            imported = (project / value["import_root"]).resolve()
        else:
            raise ValueError("source entry requires a path or project_root/import_root strings")
        if not project.is_dir() or not imported.is_dir() or not imported.is_relative_to(project):
            raise ValueError(f"reviewed external source is missing: {name}")
        result[name] = (project, imported)
    return result


def source_map(path):
    identities, paths = {}, []
    for name, (project, imported) in _source_entries(path).items():
        members = {}
        total_bytes = 0
        excluded = {
            ".git",
            "__pycache__",
            "artifacts",
            "data",
            "raw",
            "processed",
            "private",
            "tmp",
            "build",
            "dist",
            ".venv",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".cache",
        }
        for directory, folders, filenames in os.walk(project, followlinks=False):
            for folder in folders:
                if (
                    folder not in excluded
                    and not folder.startswith((".venv", "venv"))
                    and (Path(directory) / folder).is_symlink()
                ):
                    raise ValueError("external snapshots cannot traverse linked directories")
            folders[:] = [f for f in folders if f not in excluded and not f.startswith((".venv", "venv"))]
            for filename in filenames:
                if filename.startswith(".env"):
                    continue
                p = Path(directory) / filename
                if p.is_symlink() or not p.resolve().is_relative_to(project):
                    raise ValueError("external snapshots require a resolved, ordinary source tree")
                if (
                    p.suffix.lower()
                    in {
                        ".py",
                        ".json",
                        ".toml",
                        ".txt",
                        ".md",
                        ".ini",
                        ".cfg",
                        ".yaml",
                        ".yml",
                        ".inp",
                        ".dll",
                        ".so",
                        ".dylib",
                        ".pyd",
                    }
                    or ".so." in p.name
                    or p.name.upper().startswith(("LICENSE", "NOTICE", "COPYING"))
                ):
                    total_bytes += p.stat().st_size
                    if len(members) >= MAXIMUM_OWNER_FILES or total_bytes > MAXIMUM_OWNER_BYTES:
                        raise ValueError("reviewed owner source file/byte budget exceeded")
                    members[p.relative_to(project).as_posix()] = sha(p)
        if not members:
            raise ValueError(f"external source directory has no Python/resources: {name}")
        identities[name] = {
            "files": len(members),
            "tree_sha256": fingerprint(members),
            "file_sha256": members,
            "import_relative_path": imported.relative_to(project).as_posix(),
            "license_approval_implied": False,
        }
        paths.append(str(imported))
    return identities, paths


def stage_sources(path, destination, identities):
    entries = _source_entries(path)
    staged = {}
    for name, (project, _) in entries.items():
        target = destination / name
        for member, pin in identities[name]["file_sha256"].items():
            output = target / member
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / member, output)
            if sha(output) != pin:
                raise ValueError("external owner copy differs from frozen plan")
        staged[name] = {"project_root": str(target), "import_root": identities[name]["import_relative_path"]}
    mapped = destination / "source-map.json"
    save(mapped, staged)
    if source_map(mapped)[0] != identities:
        raise ValueError("isolated owner tree differs from frozen plan")
    return mapped, source_map(mapped)[1]


def _data_entry(path):
    if path is None:
        return None
    tool = _data_tool()
    config_path = Path(path).resolve()
    value = tool.read_json(config_path)
    copy_fields = {"transfer_plan", "raw_root", "test_files", "maximum_stage_bytes"}
    shared_fields = {
        "transfer_plan",
        "raw_root",
        "test_files",
        "access_mode",
        "maximum_verify_bytes",
    }
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(copy_fields),
        frozenset(shared_fields),
    }:
        raise ValueError("data map requires one supported copy or shared-read-only contract")
    if not isinstance(value["transfer_plan"], str) or not isinstance(value["raw_root"], str):
        raise ValueError("data map paths must be explicit strings")

    def mapped_path(raw):
        selected = Path(raw).expanduser()
        return (selected if selected.is_absolute() else config_path.parent / selected).resolve()

    plan_path, raw_root = mapped_path(value["transfer_plan"]), mapped_path(value["raw_root"])
    plan = tool.validate_plan(tool.read_json(plan_path))
    test_files = value["test_files"]
    mode = value.get("access_mode", "copy_to_rank")
    maximum = value.get("maximum_stage_bytes", value.get("maximum_verify_bytes"))
    if (
        not isinstance(test_files, list)
        or not test_files
        or any(not isinstance(name, str) for name in test_files)
        or len(test_files) != len(set(test_files))
    ):
        raise ValueError("data map must name unique consuming test modules")
    for name in test_files:
        p = PurePosixPath(name)
        if (
            p.as_posix() != name
            or p.is_absolute()
            or ".." in p.parts
            or not name.startswith("tests/test_")
            or not name.endswith(".py")
            or any(c in name for c in "\\:")
        ):
            raise ValueError("unsafe data-consuming test module path")
    limit = MAXIMUM_DATA_STAGE_BYTES if mode == "copy_to_rank" else MAXIMUM_SHARED_DATA_VERIFY_BYTES
    if mode not in {"copy_to_rank", "shared_read_only"}:
        raise ValueError("unsupported raw-data access mode")
    if type(maximum) is not int or maximum < plan["total_bytes"] or maximum > limit:
        raise ValueError("raw-data access exceeds its explicit bounded budget")
    return tool, plan, raw_root, sorted(test_files), mode, maximum


def data_fixture(path, available_tests=None):
    entry = _data_entry(path)
    if entry is None:
        return None
    tool, plan, raw_root, test_files, mode, maximum = entry
    if available_tests is not None and not set(test_files) <= set(available_tests):
        raise ValueError("data map names a test module outside the frozen suite")
    checked = tool.check(plan, raw_root, maximum_bytes=maximum)
    if checked["verified"] is not True:
        raise ValueError("raw-data source differs from its frozen transfer plan")
    return {
        "plan_digest": plan["plan_digest"],
        "inventory_digest": plan["inventory_digest"],
        "datasets": plan["datasets"],
        "files": plan["files"],
        "total_bytes": plan["total_bytes"],
        "test_files": test_files,
        "access_mode": mode,
        "maximum_io_bytes": maximum,
        "raw_data_redistribution_clearance": False,
    }


def stage_data(path, destination, identity):
    entry = _data_entry(path)
    if entry is None:
        raise ValueError("a frozen data map is required for rank staging")
    tool, plan, raw_root, _, mode, maximum = entry
    if mode != "copy_to_rank":
        raise ValueError("shared read-only data cannot be copied into a rank workspace")
    if data_fixture(path) != identity:
        raise ValueError("raw-data fixture differs from the frozen plan")
    destination.mkdir(parents=True, exist_ok=False)
    for item in plan["files"]:
        source = raw_root.joinpath(*tool.safe_relative(item["path"]).parts)
        target = destination.joinpath(*tool.safe_relative(item["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            remaining = item["bytes"] + 1
            while remaining:
                block = incoming.read(min(1024 * 1024, remaining))
                if not block:
                    break
                outgoing.write(block)
                remaining -= len(block)
    checked = tool.check(plan, destination, maximum_bytes=maximum)
    if checked["verified"] is not True or data_fixture(path) != identity:
        raise ValueError("staged or original raw data changed during rank isolation")
    return {
        "status": "staged_and_verified",
        "plan_digest": identity["plan_digest"],
        "files": len(identity["files"]),
        "bytes": identity["total_bytes"],
    }


def staged_data_matches(path, destination, identity):
    entry = _data_entry(path)
    if entry is None:
        return False
    tool, plan, _, _, mode, maximum = entry
    if mode != "copy_to_rank":
        return False
    checked = tool.check(plan, destination, maximum_bytes=maximum)
    return checked["verified"] is True and data_fixture(path) == identity


def verify_shared_data(path, identity):
    entry = _data_entry(path)
    if entry is None:
        raise ValueError("a frozen data map is required for shared verification")
    tool, plan, raw_root, _, mode, maximum = entry
    if mode != "shared_read_only" or data_fixture(path) != identity:
        raise ValueError("shared raw-data fixture differs from the frozen plan")
    if os.name == "posix":
        writable = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        checked_paths = {raw_root}
        for item in plan["files"]:
            source = raw_root.joinpath(*tool.safe_relative(item["path"]).parts)
            checked_paths.add(source)
            parent = source.parent
            while parent != raw_root:
                checked_paths.add(parent)
                parent = parent.parent
        if any(candidate.stat().st_mode & writable for candidate in checked_paths):
            raise ValueError("shared raw-data files and directories must be POSIX read-only")
    checked = tool.check(plan, raw_root, maximum_bytes=maximum)
    if checked["verified"] is not True:
        raise ValueError("shared raw data changed or failed verification")
    return raw_root, {
        "status": "shared_verified",
        "plan_digest": identity["plan_digest"],
        "files": len(identity["files"]),
        "bytes": identity["total_bytes"],
    }


def prepare(args):
    if type(args.shards) is not int or not 1 <= args.shards <= 4096:
        raise ValueError("shards must be in [1,4096]")
    tests, package, config = snapshots()
    only_tests = getattr(args, "only_tests", None)
    if only_tests:
        if len(only_tests) != len(set(only_tests)):
            raise ValueError("targeted campaign test modules must be unique")
        selected = {}
        for name in only_tests:
            path = PurePosixPath(name)
            if (
                not isinstance(name, str)
                or path.as_posix() != name
                or path.is_absolute()
                or ".." in path.parts
                or not name.startswith("tests/test_")
                or not name.endswith(".py")
                or name not in tests
            ):
                raise ValueError("targeted campaign names an unsafe or unavailable test module")
            selected[name] = tests[name]
        tests = selected
    target = Path(args.manifest).resolve()
    if target.is_relative_to(ROOT) and not target.is_relative_to(ROOT / "artifacts"):
        raise ValueError("put the mutable plan under artifacts or outside the source payload")
    if not tests:
        raise ValueError("no test modules found")
    weights = {}
    history_digest = None
    if args.history_junit:
        history = Path(args.history_junit)
        tree = ET.parse(history).getroot()
        history_digest = sha(history)
        for case in tree.iter("testcase"):
            module = case.get("classname", "")
            parts = module.split(".")
            # Test classes may follow the module name in a JUnit classname.
            for index in range(len(parts), 0, -1):
                name = "/".join(parts[:index]) + ".py"
                if name in tests:
                    duration = float(case.get("time", "0"))
                    if not math.isfinite(duration) or duration < 0:
                        raise ValueError("historical duration must be finite and nonnegative")
                    weights[name] = weights.get(name, 0.0) + duration
                    break
    default = (
        statistics.median([v for v in weights.values() if v > 0])
        if any(v > 0 for v in weights.values())
        else 1.0
    )
    shards = [{"rank": i, "test_files": [], "estimated_seconds": 0.0} for i in range(args.shards)]
    for name in sorted(tests, key=lambda n: (-weights.get(n, default), n)):
        target = min(shards, key=lambda s: (s["estimated_seconds"], s["rank"]))
        target["test_files"].append(name)
        target["estimated_seconds"] += weights.get(name, default)
    for shard in shards:
        shard["test_files"].sort()
    fixture = data_fixture(getattr(args, "data_map", None), tests)
    manifest = {
        "schema": SCHEMA,
        "test_file_sha256": tests,
        "package_file_sha256": package,
        "configuration_sha256": config,
        "shard_count": args.shards,
        "shards": shards,
        "history_junit_sha256": history_digest,
        "weights_are_scheduling_hints_not_validation": True,
        "tests_executed_by_prepare": 0,
        "external_sources": source_map(args.source_map)[0],
        "data_fixture": fixture,
        "campaign_kind": "targeted" if only_tests else "full_suite",
        "selected_test_modules": sorted(tests) if only_tests else None,
    }
    manifest["manifest_digest"] = fingerprint(manifest)
    save(args.manifest, manifest)
    print(
        json.dumps(
            {
                "status": "prepared_not_executed",
                "test_modules": len(tests),
                "shards": args.shards,
                "manifest_digest": manifest["manifest_digest"],
            }
        )
    )


def load_manifest(path, *, validate_current=True):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    if manifest.get("schema") != SCHEMA or manifest.get("manifest_digest") != fingerprint(body):
        raise ValueError("test manifest seal/schema changed")
    selected = manifest.get("selected_test_modules")
    if (
        manifest.get("campaign_kind") not in {"full_suite", "targeted"}
        or (manifest["campaign_kind"] == "full_suite" and selected is not None)
        or (
            manifest["campaign_kind"] == "targeted"
            and (
                not isinstance(selected, list)
                or not selected
                or len(selected) != len(set(selected))
                or selected != sorted(selected)
            )
        )
    ):
        raise ValueError("manifest campaign scope is invalid")
    all_names = []
    for key in ("test_file_sha256", "package_file_sha256", "configuration_sha256"):
        entries = manifest[key]
        if not isinstance(entries, dict):
            raise ValueError("manifest file snapshots must be named hash dictionaries")
        for name, pin in entries.items():
            if not isinstance(name, str):
                raise ValueError("manifest source names must be strings")
            p = PurePosixPath(name)
            if (
                not isinstance(name, str)
                or p.is_absolute()
                or p.as_posix() != name
                or ".." in p.parts
                or any(c in name for c in "\\:")
                or any(ord(c) < 32 for c in name)
            ):
                raise ValueError("unsafe manifest source path")
            if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
                raise ValueError("manifest requires lowercase SHA256 file pins")
            all_names.append(name)
    if len(all_names) != len(set(all_names)):
        raise ValueError("manifest snapshot categories overlap")
    n = manifest["shard_count"]
    if type(n) is not int or n <= 0 or len(manifest["shards"]) != n:
        raise ValueError("manifest shard count is invalid")
    scheduled = []
    for index, shard in enumerate(manifest["shards"]):
        if type(shard["rank"]) is not int or shard["rank"] != index:
            raise ValueError("rank assignments are not unique and ordered")
        for name in shard["test_files"]:
            p = Path(name)
            if (
                p.is_absolute()
                or ".." in p.parts
                or not name.startswith("tests/")
                or not name.endswith(".py")
                or PurePosixPath(name).as_posix() != name
                or any(c in name for c in "\\:")
            ):
                raise ValueError("unsafe test module path")
            scheduled.append(name)
    if len(scheduled) != len(set(scheduled)) or set(scheduled) != set(manifest["test_file_sha256"]):
        raise ValueError("test modules must be covered exactly once")
    if selected is not None and selected != sorted(manifest["test_file_sha256"]):
        raise ValueError("targeted campaign scope differs from its test snapshots")
    fixture = manifest.get("data_fixture")
    if fixture is not None:
        if not isinstance(fixture, dict):
            raise ValueError("manifest raw-data fixture identity is invalid")
        plan = {
            "schema": "pipeline_integrity.hpc_data_transfer.v1",
            "inventory_digest": fixture.get("inventory_digest"),
            "datasets": fixture.get("datasets"),
            "files": fixture.get("files"),
            "total_bytes": fixture.get("total_bytes"),
            "plan_digest": fixture.get("plan_digest"),
        }
        if (
            set(fixture)
            != {
                "plan_digest",
                "inventory_digest",
                "datasets",
                "files",
                "total_bytes",
                "test_files",
                "access_mode",
                "maximum_io_bytes",
                "raw_data_redistribution_clearance",
            }
            or not isinstance(fixture.get("test_files"), list)
            or not fixture.get("test_files")
            or len(fixture["test_files"]) != len(set(fixture["test_files"]))
            or set(fixture["test_files"]) - set(manifest["test_file_sha256"])
            or fixture.get("raw_data_redistribution_clearance") is not False
            or type(fixture.get("total_bytes")) is not int
            or fixture.get("total_bytes", -1) < 0
            or fixture.get("access_mode") not in {"copy_to_rank", "shared_read_only"}
            or type(fixture.get("maximum_io_bytes")) is not int
            or fixture.get("total_bytes") > fixture.get("maximum_io_bytes")
            or fixture.get("maximum_io_bytes")
            > (
                MAXIMUM_DATA_STAGE_BYTES
                if fixture.get("access_mode") == "copy_to_rank"
                else MAXIMUM_SHARED_DATA_VERIFY_BYTES
            )
        ):
            raise ValueError("manifest raw-data fixture identity is invalid")
        _data_tool().validate_plan(plan)
    if validate_current:
        actual = snapshots_for_manifest(manifest)
        expected = tuple(
            manifest[k] for k in ("test_file_sha256", "package_file_sha256", "configuration_sha256")
        )
        if actual != expected:
            raise ValueError("source, configuration or test files changed after the plan was frozen")
    return manifest


def rank_info(args):
    if args.rank is not None or args.size is not None:
        if args.rank is None or args.size is None:
            raise ValueError("explicit rank and size must be supplied together")
        return args.rank, args.size
    for rank_key, size_key in (
        ("OMPI_COMM_WORLD_RANK", "OMPI_COMM_WORLD_SIZE"),
        ("PMI_RANK", "PMI_SIZE"),
        ("PMIX_RANK", "PMIX_JOB_SIZE"),
        ("SLURM_PROCID", "SLURM_NTASKS"),
    ):
        if rank_key in os.environ and size_key in os.environ:
            return int(os.environ[rank_key]), int(os.environ[size_key])
    raise ValueError("no complete MPI rank/size environment; no implicit local test execution")


def environment():
    packages = {}
    # Do not make worker startup walk shared-filesystem distribution metadata
    # for every optional dependency.  The validation bundle records immutable
    # container/wheel hashes separately; a package metadata scan is useful for
    # diagnosis, but must be an explicit opt-in on HPC systems where site
    # packages may reside on a high-latency shared filesystem.
    collect_package_versions = os.environ.get("PIPELINE_HPC_RECORD_PACKAGE_VERSIONS") == "1"
    if collect_package_versions:
        for name in (
            "pytest",
            "numpy",
            "scipy",
            "pydantic",
            "pydantic-core",
            "pint",
            "CoolProp",
            "thermopack",
            "fluids",
            "ht",
            "FiPy",
            "scikit-fem",
            "odrpack",
            "ortools",
            "PyNiteFEA",
            "openpyxl",
            "pypdf",
            "pandas",
            "h5py",
            "rdata",
            "wntr",
            "matplotlib",
            "build",
            "ruff",
            "scikit-learn",
            "joblib",
            "threadpoolctl",
        ):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "packages": packages,
        "package_versions_collected": collect_package_versions,
        "thread_limits": {
            name: os.environ.get(name)
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
        },
    }


_PYTEST_CAPTURE = {"collected": [], "deselected": [], "reports": {}, "duplicate_reports": []}


def _write_worker_progress(stage, **fields):
    """Publish a small atomic heartbeat outside node-local scratch when configured."""
    raw_path = os.environ.get("PIPELINE_HPC_PROGRESS_PATH")
    if not raw_path:
        return
    path = Path(raw_path)
    temporary = path.with_name(path.name + ".tmp")
    payload = {"schema": "pipeline_integrity.hpc_worker_progress.v1", "stage": stage, "time_unix_s": time.time(), **fields}
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    event_path = os.environ.get("PIPELINE_HPC_EVENT_LOG_PATH")
    if event_path:
        with Path(event_path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")


_TRACE_HANDLE = None


def pytest_configure(config):
    """Keep a traceback trail when a native or numerical test stops advancing."""
    global _TRACE_HANDLE
    trace_path = os.environ.get("PIPELINE_HPC_STALL_TRACE_PATH")
    if trace_path:
        _TRACE_HANDLE = Path(trace_path).open("a", encoding="utf-8")
        faulthandler.enable(file=_TRACE_HANDLE, all_threads=True)
        interval = max(30, int(os.environ.get("PIPELINE_HPC_STALL_TRACE_INTERVAL_S", "120")))
        faulthandler.dump_traceback_later(interval, repeat=True, file=_TRACE_HANDLE)


def pytest_unconfigure(config):
    global _TRACE_HANDLE
    if _TRACE_HANDLE is not None:
        faulthandler.cancel_dump_traceback_later()
        _TRACE_HANDLE.flush()
        _TRACE_HANDLE.close()
        _TRACE_HANDLE = None


def pytest_runtest_logstart(nodeid, location):
    """Expose the current case so a long native solve is distinguishable from a hang."""
    _write_worker_progress("test_started", nodeid=nodeid, location=list(location))


def pytest_collection_finish(session):
    """Capture actual collected node IDs without a separate pytest collection run."""
    _PYTEST_CAPTURE["collected"] = [item.nodeid for item in session.items]
    for item in session.items:
        item.user_properties.append(("pipeline_hpc_node_sha256", fingerprint(item.nodeid)))


def pytest_deselected(items):
    _PYTEST_CAPTURE["deselected"].extend(item.nodeid for item in items)


def pytest_runtest_logreport(report):
    stages = _PYTEST_CAPTURE["reports"].setdefault(report.nodeid, {})
    if report.when in stages:
        _PYTEST_CAPTURE["duplicate_reports"].append([report.nodeid, report.when])
    stages[report.when] = report.outcome
    if report.when == "call":
        _write_worker_progress("test_finished", nodeid=report.nodeid, outcome=report.outcome)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("PIPELINE_HPC_CAPTURE_PATH")
    if path:
        save(
            path,
            {
                **_PYTEST_CAPTURE,
                "exitstatus": int(exitstatus),
                "schema": "pipeline_integrity.hpc_pytest_capture.v1",
            },
        )
    _write_worker_progress("pytest_finished", exitstatus=int(exitstatus))


def run(args):
    _write_worker_progress("manifest_validation_started")
    manifest = load_manifest(args.manifest)
    _write_worker_progress("manifest_validated", manifest_digest=manifest["manifest_digest"])
    rank, size = rank_info(args)
    if size != manifest["shard_count"] or not 0 <= rank < size:
        raise ValueError("launcher rank/size differs from the frozen plan")
    owners, paths = source_map(args.source_map)
    if owners != manifest["external_sources"]:
        raise ValueError("external source trees differ from the reviewed plan")
    fixture = data_fixture(getattr(args, "data_map", None), manifest["test_file_sha256"])
    if fixture != manifest.get("data_fixture"):
        raise ValueError("raw-data fixture differs from the reviewed plan")
    results = Path(args.results).resolve()
    if results.is_relative_to(ROOT) and not results.is_relative_to(ROOT / "artifacts"):
        raise ValueError("put mutable results under artifacts or outside the source payload")
    directory = results / f"rank-{rank:05d}"
    directory.mkdir(parents=True, exist_ok=False)
    files = manifest["shards"][rank]["test_files"]
    _write_worker_progress("environment_snapshot_started", rank=rank, test_files=files)
    environment_snapshot = environment()
    receipt = {
        "schema": "pipeline_integrity.hpc_rank.v1",
        "manifest_digest": manifest["manifest_digest"],
        "rank": rank,
        "size": size,
        "test_files": files,
        "environment": environment_snapshot,
        "controller_source_sha256": sha(__file__),
        "status": "unassigned" if not files else "started",
        "pytest_exit_code": None,
        "junit_sha256": None,
        "duration_s": 0.0,
        "external_sources": owners,
        "data_fixture": {"status": "not_configured" if fixture is None else "not_required_for_rank"},
        "capture_sha256": None,
        "provenance_unchanged": False,
    }
    save(directory / "receipt.json", receipt)
    _write_worker_progress("rank_receipt_saved", rank=rank, test_files=files)
    if files:
        workspace = directory / "work"
        workspace.mkdir()
        _write_worker_progress("payload_inventory_started", rank=rank)
        staged_files = payload_files()
        _write_worker_progress("payload_copy_started", rank=rank, file_count=len(staged_files))
        for index, path in enumerate(staged_files, start=1):
            target = workspace / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if index % 50 == 0 or index == len(staged_files):
                _write_worker_progress(
                    "payload_copy_progress", rank=rank, files_copied=index, file_count=len(staged_files)
                )
        expected = {
            **manifest["test_file_sha256"],
            **manifest["package_file_sha256"],
            **manifest["configuration_sha256"],
        }
        _write_worker_progress("workspace_hash_check_started", rank=rank, file_count=len(expected))
        if any(sha(workspace / name) != pin for name, pin in expected.items()):
            raise ValueError("rank workspace copy differs from its frozen manifest")
        _write_worker_progress("workspace_hash_check_finished", rank=rank, file_count=len(expected))
        staged_map, paths = stage_sources(args.source_map, directory / "owners", owners)
        _write_worker_progress("external_sources_staged", rank=rank, source_count=len(owners))
        data_required = fixture is not None and bool(set(files) & set(fixture["test_files"]))
        staged_data_root = None
        shared_data_root = None
        if data_required:
            if fixture["access_mode"] == "copy_to_rank":
                staged_data_root = workspace / "data" / "raw"
                receipt["data_fixture"] = stage_data(args.data_map, staged_data_root, fixture)
            else:
                shared_data_root, receipt["data_fixture"] = verify_shared_data(args.data_map, fixture)
        verbosity = "-vv" if os.environ.get("PIPELINE_HPC_VERBOSE") == "1" else "-q"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "hpc_tests",
            verbosity,
            *files,
            f"--junitxml={directory / 'tests.xml'}",
        ]
        start = time.monotonic()
        try:
            _write_worker_progress("pytest_launching", rank=rank, test_files=files)
            with (directory / "pytest.log").open("wb") as log:
                env = dict(os.environ)
                # Only inherit explicitly reviewed node-local runtime paths.  An
                # arbitrary parent PYTHONPATH is user-controlled and can shadow
                # project modules or inject code into every test subprocess.
                runtime_pythonpath = [
                    entry
                    for entry in os.environ.get(
                        "PIPELINE_HPC_TRUSTED_PYTHONPATH", ""
                    ).split(os.pathsep)
                    if entry
                ]
                env["PYTHONPATH"] = os.pathsep.join(
                    [
                        str(workspace / "scripts"),
                        str(workspace / "src"),
                        str(workspace),
                        *paths,
                        *runtime_pythonpath,
                    ]
                )
                env.pop("PYTEST_ADDOPTS", None)
                env.pop("PYTEST_PLUGINS", None)
                env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                env["PIPELINE_HPC_CAPTURE_PATH"] = str(directory / "capture.json")
                env["PIPELINE_HPC_EVENT_LOG_PATH"] = str(directory / "progress-events.jsonl")
                env["PIPELINE_HPC_STALL_TRACE_PATH"] = str(directory / "tracebacks.log")
                env["PYTHONFAULTHANDLER"] = "1"
                env["PIPELINE_HPC_CAMPAIGN_KIND"] = manifest["campaign_kind"]
                if shared_data_root is not None:
                    env["PIPELINE_HPC_RAW_ROOT"] = str(shared_data_root)
                result = subprocess.run(
                    command, cwd=workspace, stdout=log, stderr=subprocess.STDOUT, check=False, env=env
                )
            receipt["pytest_exit_code"] = result.returncode
            receipt["status"] = "finished"
            _write_worker_progress(
                "pytest_process_finished", rank=rank, pytest_exit_code=result.returncode
            )
        except OSError as exc:
            receipt.update(status="launch_failed", error=str(exc))
        receipt["duration_s"] = time.monotonic() - start
        if (directory / "tests.xml").is_file():
            receipt["junit_sha256"] = sha(directory / "tests.xml")
        if (directory / "capture.json").is_file():
            receipt["capture_sha256"] = sha(directory / "capture.json")
        receipt["provenance_unchanged"] = (
            snapshots_for_manifest(manifest, workspace)
            == (
                manifest["test_file_sha256"],
                manifest["package_file_sha256"],
                manifest["configuration_sha256"],
            )
            and snapshots_for_manifest(manifest)
            == (
                manifest["test_file_sha256"],
                manifest["package_file_sha256"],
                manifest["configuration_sha256"],
            )
            and source_map(staged_map)[0] == owners
            and source_map(args.source_map)[0] == owners
            and data_fixture(getattr(args, "data_map", None), manifest["test_file_sha256"]) == fixture
            and (
                not data_required
                or receipt["data_fixture"]["status"] == "staged_and_verified"
                and staged_data_matches(args.data_map, staged_data_root, fixture)
                or receipt["data_fixture"]["status"] == "shared_verified"
                and verify_shared_data(args.data_map, fixture)[1] == receipt["data_fixture"]
            )
            and environment() == receipt["environment"]
        )
        if not receipt["provenance_unchanged"]:
            receipt["status"] = "source_or_environment_changed"
        elif receipt["junit_sha256"] is None or receipt["capture_sha256"] is None:
            receipt["status"] = "controller_incomplete_output"
        save(directory / "receipt.json", receipt)
    print(
        json.dumps(
            {
                "rank": rank,
                "controller_status": receipt["status"],
                "pytest_exit_code": receipt["pytest_exit_code"],
            }
        )
    )
    # The aggregator is the only overall pass gate. A finished failing shard
    # must not cause the MPI launcher to cancel every other validation shard.


def aggregate(args):
    manifest = load_manifest(args.manifest)
    merged = ET.Element("testsuites", name="pipeline_integrity_hpc")
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    issues, ids, environments = [], set(), []
    completed_files = []
    for shard in manifest["shards"]:
        rank = shard["rank"]
        directory = Path(args.results) / f"rank-{rank:05d}"
        try:
            receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
            if (receipt["manifest_digest"], receipt["rank"], receipt["size"], receipt["test_files"]) != (
                manifest["manifest_digest"],
                rank,
                manifest["shard_count"],
                shard["test_files"],
            ):
                raise ValueError("rank receipt assignment or manifest differs")
            environments.append(receipt["environment"])
            if (
                receipt.get("controller_source_sha256")
                != manifest["configuration_sha256"]["scripts/hpc_tests.py"]
                or receipt.get("external_sources") != manifest["external_sources"]
            ):
                raise ValueError("rank controller or external source provenance differs")
            needs_data = manifest.get("data_fixture") is not None and bool(
                set(shard["test_files"]) & set(manifest["data_fixture"]["test_files"])
            )
            expected_data_status = (
                (
                    "staged_and_verified"
                    if manifest["data_fixture"]["access_mode"] == "copy_to_rank"
                    else "shared_verified"
                )
                if needs_data
                else "not_configured"
                if manifest.get("data_fixture") is None
                else "not_required_for_rank"
            )
            if receipt.get("data_fixture", {}).get("status") != expected_data_status:
                raise ValueError("rank raw-data fixture staging differs from its assignment")
            if needs_data and (
                receipt["data_fixture"].get("plan_digest") != manifest["data_fixture"]["plan_digest"]
                or receipt["data_fixture"].get("files") != len(manifest["data_fixture"]["files"])
                or receipt["data_fixture"].get("bytes") != manifest["data_fixture"]["total_bytes"]
            ):
                raise ValueError("rank raw-data fixture receipt differs from the frozen identity")
            if not shard["test_files"]:
                if receipt["status"] != "unassigned":
                    raise ValueError("empty rank did not retain its unassigned state")
                continue
            if (
                receipt["status"] != "finished"
                or type(receipt["pytest_exit_code"]) is not int
                or receipt["pytest_exit_code"] not in {0, 1}
            ):
                raise ValueError("pytest shard did not finish with a complete pass/fail outcome")
            if receipt["junit_sha256"] != sha(directory / "tests.xml"):
                raise ValueError("rank JUnit is absent or changed")
            root = ET.parse(directory / "tests.xml").getroot()
            if root.tag not in {"testsuite", "testsuites"}:
                raise ValueError("unexpected JUnit root structure")
            suites = [root] if root.tag == "testsuite" else list(root)
            observed = 0
            observed_modules = set()
            local_counts = dict.fromkeys(counts, 0)
            local_ids = set()
            local_node_outcomes = {}
            for suite in suites:
                if suite.tag != "testsuite":
                    raise ValueError("unexpected JUnit structure")
                actual_cases = list(suite.iter("testcase"))
                actual_counts = {
                    "tests": len(actual_cases),
                    "failures": sum(len(case.findall("failure")) for case in actual_cases),
                    "errors": sum(len(case.findall("error")) for case in actual_cases),
                    "skipped": sum(len(case.findall("skipped")) for case in actual_cases),
                }
                for key in counts:
                    value = int(suite.get(key, "0"))
                    if value != actual_counts[key]:
                        raise ValueError("JUnit declared totals differ from actual outcome elements")
                    if value < 0:
                        raise ValueError("negative JUnit count")
                    local_counts[key] += value
                for case in suite.iter("testcase"):
                    parts = case.get("classname", "").split(".")
                    module = next(
                        (
                            "/".join(parts[:i]) + ".py"
                            for i in range(len(parts), 0, -1)
                            if "/".join(parts[:i]) + ".py" in shard["test_files"]
                        ),
                        None,
                    )
                    if module is None:
                        raise ValueError("JUnit case does not belong to an assigned test module")
                    observed_modules.add(module)
                    if not case.get("name", "").strip() or any(
                        child.tag
                        not in {"properties", "failure", "error", "skipped", "system-out", "system-err"}
                        for child in case
                    ):
                        raise ValueError("JUnit case identity or outcome structure is invalid")
                    identities = [
                        p.get("value")
                        for p in case.findall("properties/property")
                        if p.get("name") == "pipeline_hpc_node_sha256"
                    ]
                    if len(identities) != 1:
                        raise ValueError("JUnit case lacks a unique collected-node binding")
                    identity = identities[0]
                    outcomes = [tag for tag in ("failure", "error", "skipped") if case.find(tag) is not None]
                    if identity in ids:
                        raise ValueError("duplicate test case across ranks")
                    if identity in local_ids and sorted(local_node_outcomes[identity] + outcomes) != [
                        "error",
                        "failure",
                    ]:
                        raise ValueError(
                            "duplicate test case without the native call-failure/teardown-error split"
                        )
                    local_node_outcomes.setdefault(identity, []).extend(outcomes)
                    local_ids.add(identity)
                    observed += 1
            if root.tag == "testsuites" and any(
                root.get(key) is not None and int(root.get(key)) != local_counts[key] for key in counts
            ):
                raise ValueError("JUnit root totals differ from actual suite outcome elements")
            if observed == 0:
                raise ValueError("assigned shard produced no test cases")
            if observed_modules != set(shard["test_files"]):
                raise ValueError("not every assigned test module appears in the actual JUnit cases")
            capture_path = directory / "capture.json"
            if receipt.get("capture_sha256") != sha(capture_path):
                raise ValueError("pytest collection/execution capture is absent or changed")
            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            collected, reports = captured["collected"], captured["reports"]
            if captured.get("duplicate_reports"):
                raise ValueError("pytest emitted duplicate execution phase reports")
            if (
                captured.get("schema") != "pipeline_integrity.hpc_pytest_capture.v1"
                or captured["exitstatus"] != receipt["pytest_exit_code"]
                or captured["deselected"]
            ):
                raise ValueError("pytest capture contains deselection or a different exit status")
            if not collected or len(collected) != len(set(collected)) or set(reports) != set(collected):
                raise ValueError("actual collected tests were not executed exactly once")
            if (
                any(node.split("::", 1)[0] not in shard["test_files"] for node in collected)
                or {fingerprint(node) for node in collected} != local_ids
            ):
                raise ValueError("JUnit cases do not cover the actual collected node IDs exactly")
            expected_counts = dict.fromkeys(counts, 0)
            expected_counts["tests"] = len(collected)
            for stages in reports.values():
                if (
                    not isinstance(stages, dict)
                    or set(stages) - {"setup", "call", "teardown"}
                    or not {"setup", "teardown"} <= set(stages)
                    or any(v not in {"passed", "failed", "skipped"} for v in stages.values())
                    or stages["setup"] == "passed"
                    and "call" not in stages
                ):
                    raise ValueError("test setup/call/teardown did not complete")
                expected_counts["failures"] += stages.get("call") == "failed"
                expected_counts["errors"] += int(stages["setup"] == "failed") + int(
                    stages["teardown"] == "failed"
                )
                expected_counts["skipped"] += "skipped" in stages.values()
                expected_counts["tests"] += stages.get("call") == "failed" and stages["teardown"] == "failed"
            if expected_counts != local_counts or (
                receipt["pytest_exit_code"] == 0 and (local_counts["failures"] or local_counts["errors"])
            ):
                raise ValueError("JUnit outcomes differ from captured setup/call/teardown")
            if receipt.get("provenance_unchanged") is not True:
                raise ValueError("source or environment provenance changed during execution")
            ids.update(local_ids)
            for key in counts:
                counts[key] += local_counts[key]
            merged.extend(suites)
            completed_files.extend(shard["test_files"])
            if receipt["pytest_exit_code"] != 0:
                issues.append(
                    {"rank": rank, "kind": "test_failure", "reason": "pytest reported failing tests"}
                )
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
            issues.append({"rank": rank, "kind": "controller_incomplete", "reason": str(exc)})
    if sorted(completed_files) != sorted(manifest["test_file_sha256"]):
        issues.append({"kind": "controller_incomplete", "reason": "not every planned test module completed"})
    if counts["tests"] < len(ids):
        issues.append(
            {"kind": "controller_incomplete", "reason": "JUnit totals differ from unique actual test cases"}
        )
    if counts["failures"] or counts["errors"] or (counts["skipped"] and not args.allow_skips):
        issues.append(
            {
                "kind": "test_or_skip_gate",
                "reason": "failed/error/skipped tests do not satisfy the selected gate",
            }
        )
    if not environments or any(value != environments[0] for value in environments):
        issues.append(
            {"kind": "controller_incomplete", "reason": "rank environments are missing or inconsistent"}
        )
    result = {
        "schema": "pipeline_integrity.hpc_verification.v1",
        "status": "incomplete_or_failed"
        if issues
        else "passed_with_allowed_skips"
        if counts["skipped"]
        else "passed",
        "manifest_digest": manifest["manifest_digest"],
        "controller_complete": not any(row.get("kind") == "controller_incomplete" for row in issues),
        "counts_meaning": "actual JUnit case elements/outcomes; dual call-failure/teardown-error entries count twice; incomplete ranks excluded",
        "collected_test_nodes": len(ids),
        "counts": counts,
        "planned_test_modules": len(manifest["test_file_sha256"]),
        "completed_test_modules": len(completed_files),
        "shards": manifest["shard_count"],
        "skip_policy": "explicitly_allowed" if args.allow_skips else "no_skips",
        "environment": environments[0] if environments else None,
        "issues": issues,
        "engineering_or_field_qualification": False,
    }
    Path(args.results).mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(Path(args.results) / "merged.xml", encoding="utf-8", xml_declaration=True)
    save(Path(args.results) / "summary.json", result)
    if args.public_summary:
        save(args.public_summary, result)
    print(json.dumps({"status": result["status"], **counts, "issues": len(issues)}))
    if issues:
        raise SystemExit(1)


def bundle(args):
    destination = Path(args.output).resolve()
    if destination.exists():
        raise ValueError("source bundle already exists; use a new output name")
    if destination.is_relative_to(ROOT) and not destination.is_relative_to(ROOT / "artifacts"):
        raise ValueError("put mutable bundle output under artifacts or outside the source payload")
    files = payload_files()
    pins = {relative(p): sha(p) for p in files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "x:gz") as archive:
        for path in files:
            archive.add(path, arcname="pipeline-integrity-toolkit/" + relative(path), recursive=False)
    actual = {}
    with tarfile.open(destination, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith("pipeline-integrity-toolkit/"):
                raise ValueError("source bundle contains an unsupported member")
            name = member.name.removeprefix("pipeline-integrity-toolkit/")
            if name in actual:
                raise ValueError("source bundle contains duplicate members")
            value = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            actual[name] = value.hexdigest()
    if actual != pins or {relative(p): sha(p) for p in payload_files()} != pins:
        raise ValueError("source payload changed while bundling or differs from actual archived bytes")
    receipt = {
        "schema": "pipeline_integrity.hpc_source_bundle.v1",
        "kind": "source_payload_not_validation_release",
        "files": len(files),
        "bytes": destination.stat().st_size,
        "sha256": sha(destination),
        "file_sha256": actual,
        "raw_data_or_external_owners_bundled": False,
        "tests_executed": 0,
        "hpc_execution_verified": False,
    }
    save(destination.with_suffix(destination.suffix + ".json"), receipt)
    print(json.dumps({k: receipt[k] for k in ("kind", "files", "bytes", "sha256", "tests_executed")}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    transfer = sub.add_parser("bundle")
    transfer.add_argument("--output", type=Path, required=True)
    plan = sub.add_parser("prepare")
    plan.add_argument("--shards", type=int, required=True)
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--history-junit", type=Path)
    plan.add_argument("--source-map", type=Path)
    plan.add_argument("--data-map", type=Path)
    plan.add_argument("--only-tests", nargs="+")
    worker = sub.add_parser("run")
    worker.add_argument("--manifest", type=Path, required=True)
    worker.add_argument("--results", type=Path, required=True)
    worker.add_argument("--rank", type=int)
    worker.add_argument("--size", type=int)
    worker.add_argument("--source-map", type=Path)
    worker.add_argument("--data-map", type=Path)
    collect = sub.add_parser("aggregate")
    collect.add_argument("--manifest", type=Path, required=True)
    collect.add_argument("--results", type=Path, required=True)
    collect.add_argument("--public-summary", type=Path)
    collect.add_argument("--allow-skips", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        if not 1 <= args.shards <= 4096:
            parser.error("shards must be in [1,4096]")
        prepare(args)
    elif args.mode == "bundle":
        bundle(args)
    elif args.mode == "run":
        run(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
