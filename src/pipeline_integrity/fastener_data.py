"""Source-pinned, reusable reader for released fastener clamping traces.

This module preserves the source coordinate as angle. It does not infer elapsed
time, synchronized channels, bolt preload, or a joint's sealing capacity.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from pathlib import Path

from .contracts import digest
from .datasets import DatasetSpec, catalog

DATASET_ID = "fastener_clamping"
SCHEMA = "pipeline_integrity.fastener_trace.v1"
_TRACE_NAME = re.compile(r"raw_data_(torque|force)_test_(\d{2})([tf])\.csv\Z")
MAXIMUM_TRACE_BYTES = 2_000_000


class FastenerTraceError(ValueError):
    """A trace does not match the reviewed source asset or CSV contract."""


def _source_spec() -> DatasetSpec:
    choices = [item for item in catalog() if item.id == DATASET_ID]
    if len(choices) != 1 or choices[0].id != DATASET_ID:
        raise FastenerTraceError("one reviewed fastener-clamping source specification is required")
    spec = choices[0]
    if (
        spec.license_id != "CC-BY-4.0"
        or spec.rights_review != "reviewed_license"
        or spec.acquisition != "allowed"
    ):
        raise FastenerTraceError("fastener trace use requires the reviewed CC BY 4.0 source")
    return spec


def _finite_number(token: str) -> float | None:
    if not token.strip():
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def read_fastener_trace(
    path: str | Path,
    *,
    kind: str | None = None,
) -> dict:
    """Read and verify one released torque or force curve.

    `kind` may be omitted when the source basename identifies it. Force values
    are retained in kN and explicitly converted to N; torque remains N*m.
    Every row and original cell token is preserved in this caller-owned result.
    The result should be kept outside a public code release because it contains
    licensed pointwise observations.
    """
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise FastenerTraceError("source must be a regular, non-symlink CSV file")
    match = _TRACE_NAME.fullmatch(source.name)
    if match is None:
        raise FastenerTraceError("source filename is not a reviewed torque/force trace")
    filename_kind, trial_text, suffix = match.groups()
    trial_number = int(trial_text)
    if not 1 <= trial_number <= 48 or suffix != ("t" if filename_kind == "torque" else "f"):
        raise FastenerTraceError("source trial number or channel suffix is outside the reviewed schema")
    selected_kind = filename_kind if kind is None else kind
    if selected_kind not in {"torque", "force"} or selected_kind != filename_kind:
        raise FastenerTraceError("requested channel does not match the source filename")

    spec = _source_spec()
    assets = [asset for asset in spec.assets if asset.filename == source.name]
    if len(assets) != 1:
        raise FastenerTraceError("file is not one of the reviewed publisher assets")
    asset = assets[0]
    if asset.checksum is None or not asset.checksum.startswith("sha256:"):
        raise FastenerTraceError("a publisher SHA-256 must be pinned before reading")
    if asset.checksum_origin != "publisher_metadata":
        raise FastenerTraceError("source checksum must originate in publisher metadata")
    if (
        asset.bytes is None
        or asset.bytes > MAXIMUM_TRACE_BYTES
        or source.stat().st_size != asset.bytes
    ):
        raise FastenerTraceError("source size differs from publisher metadata")

    raw = source.read_bytes()
    if len(raw) > MAXIMUM_TRACE_BYTES:
        raise FastenerTraceError("trace exceeds the declared byte budget")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != asset.checksum.split(":", 1)[1]:
        raise FastenerTraceError("source SHA-256 differs from publisher metadata")
    expected_header = [
        "Angle (summary) [deg]",
        "Torque [Nm]" if selected_kind == "torque" else "Force [kN]",
    ]
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="strict"), newline="")))
    if not rows or rows[0] != expected_header or len(rows) < 3 or any(len(row) != 2 for row in rows[1:]):
        raise FastenerTraceError("trace header or two-column row shape differs from reviewed source")

    factor = 1.0 if selected_kind == "torque" else 1000.0
    source_unit, si_unit = ("Nm", "N*m") if selected_kind == "torque" else ("kN", "N")
    samples = []
    for source_row, cells in enumerate(rows[1:], start=2):
        angle, source_value = (_finite_number(token) for token in cells)
        samples.append(
            {
                "source_row": source_row,
                "source_cells": cells,
                "angle_deg": angle,
                "value_source_unit": source_value,
                "value_si": None if source_value is None else source_value * factor,
                "valid": angle is not None and source_value is not None,
            }
        )
    angles = [sample["angle_deg"] for sample in samples]
    complete_angles = all(value is not None for value in angles)
    values = [sample["value_si"] for sample in samples if sample["value_si"] is not None]
    body = {
        "schema": SCHEMA,
        "dataset_id": spec.id,
        "citation": spec.citation,
        "source_url": spec.source,
        "rights_evidence_url": spec.rights_evidence_url,
        "license_id": spec.license_id,
        "dataset_version": spec.version,
        "creators": list(spec.creators),
        "split_unit": spec.split_unit,
        "trial_id": f"fastener_test_{trial_number:02d}",
        "trial_number": trial_number,
        "filename": source.name,
        "bytes": len(raw),
        "sha256": source_sha256,
        "expected_checksum": asset.checksum,
        "checksum_origin": asset.checksum_origin,
        "publisher_checksum_verified": True,
        "kind": selected_kind,
        "source_unit": source_unit,
        "si_unit": si_unit,
        "si_conversion_factor": factor,
        "coordinate": "angle_deg",
        "elapsed_time_observed": False,
        "simultaneous_time_synchronization_established": False,
        "samples": samples,
        "summary": {
            "rows": len(samples),
            "invalid_or_missing_rows": sum(not sample["valid"] for sample in samples),
            "negative_values": sum(value < 0 for value in values),
            "zero_values": sum(value == 0 for value in values),
            "angle_strictly_increasing": (
                all(b > a for a, b in zip(angles, angles[1:], strict=False))
                if complete_angles
                else None
            ),
            "repeated_adjacent_angles": (
                sum(b == a for a, b in zip(angles, angles[1:], strict=False))
                if complete_angles
                else None
            ),
            "decreasing_adjacent_angles": (
                sum(b < a for a, b in zip(angles, angles[1:], strict=False))
                if complete_angles
                else None
            ),
        },
        "source_rows_sorted_smoothed_or_rectified": False,
    }
    return body | {"record_digest": digest(body)}
