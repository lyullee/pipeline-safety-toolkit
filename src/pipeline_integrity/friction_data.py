"""Preserve the small pilot-pipe observations without fitting a hydraulic model."""

import csv
import hashlib
import io
import math
from pathlib import Path

from .contracts import digest
from .datasets import catalog, file_digest

DATASET_ID = "pipe_friction_roughness"


def read_friction_observations(folder):
    """Read a fixed reviewed source; pressure head is not absolute pressure."""
    root = Path(folder).resolve()
    spec = next(s for s in catalog() if s.id == DATASET_ID)
    if (spec.rights_review, spec.acquisition, spec.license_id) != ("reviewed_license", "allowed", "MIT"):
        raise ValueError("source rights or acquisition policy changed")
    sources, payloads = [], {}
    for asset in spec.assets:
        path = root / asset.filename
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("source file leaves the declared root")
        if asset.bytes is None or asset.bytes > 100_000 or path.stat().st_size != asset.bytes:
            raise ValueError("source byte pin/budget changed")
        if asset.checksum is None or asset.checksum_origin != "publisher_metadata":
            raise ValueError("publisher checksum pin required")
        raw = path.read_bytes()
        algorithm, expected = asset.checksum.split(":", 1)
        if hashlib.new(algorithm, raw).hexdigest() != expected:
            raise ValueError("publisher source checksum differs")
        payloads[asset.filename] = raw
        sources.append(
            {
                "filename": asset.filename,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "expected_checksum": asset.checksum,
                "supplier_checksum_verified": True,
            }
        )
    reader = csv.DictReader(io.StringIO(payloads["dataset.csv"].decode("utf-8-sig")))
    if reader.fieldnames != ["Hin", "Hout", "Q", "nu"]:
        raise ValueError("reviewed source columns changed")
    rows = []
    for index, row in enumerate(reader, 2):
        if len(rows) >= 6 or None in row or any(v is None for v in row.values()):
            raise ValueError("source row width/count changed")
        values = [float(row[k]) for k in reader.fieldnames]
        if not all(math.isfinite(v) for v in values) or values[2] <= 0 or values[3] <= 0:
            raise ValueError("invalid finite positive flow/viscosity source observation")
        rows.append(
            {
                "source_row": index,
                "inlet_pressure_head_m": values[0],
                "outlet_pressure_head_m": values[1],
                "volume_flow_m3_s": values[2],
                "kinematic_viscosity_m2_s": values[3],
            }
        )
    if len(rows) != 6:
        raise ValueError("source operating-point count changed")
    body = {
        "schema": "pipeline_integrity.friction_observations.v1",
        "dataset_id": DATASET_ID,
        "catalog_entry_sha256": digest(spec),
        "sources": sources,
        "citation": spec.citation,
        "license_id": spec.license_id,
        "rows": rows,
        "clock": None,
        "origin": spec.origin,
        "pressure_reference": None,
        "pressure_head_converted_to_Pa": False,
        "viscosity_origin": "temperature_derived_not_independent_measurement",
        "fitted_roughness": None,
        "fitted_minor_loss": None,
        "independent_validation": False,
        "module_sha256": file_digest(Path(__file__)),
        "dependency_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py")) for name in ("contracts", "datasets")
        },
    }
    return dict(body, record_digest=digest(body))


def verify_friction_observation_record(record, *, source_root):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("source record seal changed")
    if read_friction_observations(source_root) != record:
        raise ValueError("source observations or current reader differ on replay")
    return True
