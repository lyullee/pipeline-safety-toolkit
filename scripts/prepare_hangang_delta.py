"""Build a compact, checksum-sealed delta for the Hangang runtime-v2 rerun."""

from __future__ import annotations

import hashlib
import gzip
import shutil
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HPC = ROOT / "artifacts" / "hpc"
OUT = HPC / "runtime-delta-v2"
METADATA = (
    "owners/lh2dt_models/pyproject.toml",
    "owners/lh2dt_models/README.md",
    "owners/human_reliability/DATA_LICENSES.md",
    "owners/human_reliability/pyproject.toml",
    "owners/human_reliability/README.md",
    "owners/human_reliability/README_KO.md",
    "owners/human_reliability/THIRD_PARTY_NOTICES.md",
    "owners/process_monitoring/pyproject.toml",
    "owners/process_monitoring/README.md",
    "owners/process_monitoring/THIRD_PARTY_NOTICES.md",
    "owners/prism/pyproject.toml",
    "owners/prism/README.md",
    "owners/prism/LICENSE",
)
PAYLOADS = (
    "hangang-source-patch-v2.tar.gz",
    "hangang-external-sources-runtime-v2.tar.gz",
    "hangang-source-map-runtime-v2.json",
    "extra-wheels/autograd-1.9.1-py3-none-any.whl",
    "extra-wheels/moorpy-1.3.0-py3-none-any.whl",
    "hangang_full_validation.sbatch",
)
PERSISTED = (
    "hangang-raw-data-v1.tar.gz",
    "hangang-data-plan-v1.json",
    "hangang-data-map-v1.json",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for item in PAYLOADS:
        source = HPC / item
        target = OUT / item
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    full_archive = HPC / "hangang-external-sources-runtime-v2.tar.gz"
    delta_archive = OUT / "hangang-external-sources-runtime-v2.tar.gz"
    metadata_root = OUT / "metadata-tree"
    metadata_root.mkdir(exist_ok=True)
    with tarfile.open(full_archive, "r:gz") as source:
        for name in METADATA:
            member = source.getmember(name)
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError(f"metadata member is not a regular file: {name}")
            target = metadata_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read())
    with delta_archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as target:
                for name in METADATA:
                    target.add(metadata_root / name, arcname=name, recursive=False)

    lines = []
    for item in (*PAYLOADS, *PERSISTED):
        path = (OUT / item) if item in PAYLOADS else (HPC / item)
        lines.append(f"{digest(path)}  {item}")
    (OUT / "hangang-payloads.SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode("ascii"))

    stage = HPC / "hangang-delta-stage-20261030.tar"
    with tarfile.open(stage, "w") as archive:
        for item in (*PAYLOADS, "hangang-payloads.SHA256SUMS"):
            archive.add(OUT / item, arcname=item, recursive=True)
    print(f"metadata_files={len(METADATA)}")
    print(f"external_delta_bytes={delta_archive.stat().st_size}")
    print(f"stage_bytes={stage.stat().st_size}")
    print(f"stage_sha256={digest(stage)}")


if __name__ == "__main__":
    main()
