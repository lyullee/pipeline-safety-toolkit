"""Export source and dependency facts, excluding observations and local checkout locations."""

import argparse
import json
import platform
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import distribution
from pathlib import Path

from pipeline_integrity import __version__, create_registry
from pipeline_integrity.capabilities import capabilities
from pipeline_integrity.datasets import catalog, verify_acquisition_provenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("artifacts/acquisition"))
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="refresh the public dataset inventory without environment or scope files",
    )
    args = parser.parse_args()
    folder = Path("research")
    folder.mkdir(exist_ok=True)
    previous_path = folder / "data_inventory.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    previous_rows = previous.get("datasets", [])
    previous_by_id = {row["id"]: row for row in previous_rows}
    datasets = []
    catalog_rows = catalog()
    catalog_ids = {spec.id for spec in catalog_rows}
    for spec in catalog_rows:
        path = args.ledger / f"{spec.id}.json"
        if path.exists():
            ledger = json.loads(path.read_text(encoding="utf-8"))
        else:
            # Keep already verified acquisition receipts when their legacy ledger is absent.
            saved = previous_by_id.get(spec.id, {})
            ledger = {"files": saved.get("files", []), "failures": saved.get("failures", [])}
        selected = {a.filename for a in spec.assets if a.default}
        files = []
        for item in ledger.get("files", []):
            verify_acquisition_provenance(item)
            fields = (
                "filename",
                "bytes",
                "sha256",
                "expected_checksum",
                "checksum_origin",
                "expected_checksum_verified",
                "supplier_checksum_verified",
                "source_url",
                "retrieved_at_utc",
                "catalog_snapshot_sha256",
                "asset_snapshot_sha256",
                "asset_catalogued",
                "catalog_snapshot",
                "asset_snapshot",
            )
            files.append({key: item[key] for key in fields if item.get(key) is not None})
        complete = (
            bool(selected) and selected <= {row["filename"] for row in files} and not ledger.get("failures")
        )
        datasets.append(
            {
                "id": spec.id,
                "title": spec.title,
                "citation": spec.citation,
                "creators": spec.creators,
                "version": spec.version,
                "source": spec.source,
                "license_id": spec.license_id,
                "rights_evidence_url": spec.rights_evidence_url,
                "rights_review": spec.rights_review,
                "origin": spec.origin,
                "acquisition_policy": spec.acquisition,
                "selected_assets_complete": complete,
                "files": files,
                "failures": ledger.get("failures", []),
                "limitations": spec.limitations,
                "raw_in_code_release": False,
            }
        )
    datasets.extend(row for row in previous_rows if row.get("id") not in catalog_ids)
    acquired = [row for row in datasets if row["files"]]
    summary = {
        "schema": "pipeline_integrity.data_inventory.v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_families": len(datasets),
        "acquired_families": len(acquired),
        "acquired_files": sum(len(row["files"]) for row in acquired),
        "acquired_bytes": sum(f["bytes"] for row in acquired for f in row["files"]),
        "datasets": datasets,
    }
    (folder / "data_inventory.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.data_only:
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in ("candidate_families", "acquired_families", "acquired_files", "acquired_bytes")
                }
            )
        )
        return
    packages = {}
    for name in (
        "numpy",
        "pydantic",
        "pydantic_core",
        "pint",
        "scipy",
        "fluids",
        "ht",
        "FiPy",
        "scikit-fem",
        "odrpack",
        "CoolProp",
        "pandas",
        "openpyxl",
        "h5py",
        "pypdf",
        "rdata",
        "wntr",
        "matplotlib",
        "ortools",
        "PyNiteFEA",
        "pytest",
        "build",
        "ruff",
    ):
        dist = distribution(name)
        metadata = dist.metadata
        packages[name] = {
            "version": dist.version,
            "license_expression": metadata.get("License-Expression"),
            "license_classifiers": [c for c in metadata.get_all("Classifier", []) if c.startswith("License")],
            "project_urls": metadata.get_all("Project-URL", []),
        }
    environment = {
        "schema": "pipeline_integrity.tested_environment.v1",
        "python": platform.python_version(),
        "platform": platform.system(),
        "toolkit_version": __version__,
        "direct_packages": packages,
        "is_complete_transitive_lock": False,
    }
    (folder / "tested_environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    Path("requirements-tested.txt").write_text(
        "# Tested direct dependencies; this file is a constraints file, not a transitive lock.\n"
        + "\n".join(f"{name}=={item['version']}" for name, item in packages.items())
        + "\n",
        encoding="utf-8",
    )
    count = Counter(row.state for row in capabilities())
    inventory = {
        "schema": "pipeline_integrity.scope_inventory.v1",
        "capabilities": len(capabilities()),
        "states": dict(count),
        "registered_models": len(create_registry().models()),
        "catalog_entries_are_not_independent_validation": True,
    }
    (folder / "scope_inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in ("candidate_families", "acquired_families", "acquired_files", "acquired_bytes")
            }
        )
    )


if __name__ == "__main__":
    main()
