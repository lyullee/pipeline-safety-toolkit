"""Read source tank experiments only under explicit unit and level-datum interpretations."""

import csv
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Contract, Name, SignalFrame, digest
from .datasets import catalog, file_digest
from .units import convert_quantity


class CoupledTankReadRequest(Contract):
    run_number: int = Field(ge=1, le=10)
    level_unit: Literal["cm", "mm"]
    level_zero_offset_m: float
    unit_mapping_evidence_id: Name
    datum_evidence_id: Name
    source_origin: Literal["measured", "laboratory", "simulation", "synthetic"]


def _table(path, expected):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    if not rows or rows[0] != expected or len(rows) < 2:
        raise ValueError("unexpected native coupled-tank header or empty table")
    if any(len(r) != len(expected) for r in rows[1:]):
        raise ValueError("native tank rows must preserve their column count")
    return rows[1:]


def _number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def _source_tables(root):
    root = Path(root)
    header = ["t_ref", *(f"Experimento{i}" for i in range(1, 11))]
    h1, h2, control = (
        _table(root / "h1.csv", header),
        _table(root / "h2.csv", header),
        _table(root / "U.csv", ["t_ref", "U"]),
    )
    t1, t2, tu = ([_number(r[0]) for r in rows] for rows in (h1, h2, control))
    for time in (t1, t2, tu):
        if any(t is None or t < 0 for t in time) or any(b <= a for a, b in zip(time, time[1:], strict=False)):
            raise ValueError("native experiment clocks must be finite nonnegative and strictly increasing")
    if t1 != t2:
        raise ValueError("the two level tables do not share the released t_ref vector")
    return h1, h2, control, t1, tu


def _published_files(root, names):
    specification = next(row for row in catalog() if row.id == "coupled_tank")
    assets = {a.filename: a for a in specification.assets}
    return {
        name: name in assets
        and file_digest(Path(root) / name, assets[name].checksum.split(":", 1)[0])
        == assets[name].checksum.split(":", 1)[1]
        for name in names
    }


def review_coupled_tank_source(root):
    """Report source inconsistencies; never repair truncated JSON or choose a physical unit silently."""
    root = Path(root)
    h1, h2, control, times, input_times = _source_tables(root)
    metadata = (root / "operational_parameters.json").read_text(encoding="utf-8")
    try:
        parsed = json.loads(metadata)
        parse_error = None
    except json.JSONDecodeError as error:
        parsed, parse_error = None, str(error)
    complete = _table(root / "complete_data.csv", ["t", "h1", "h2", "u"])
    hashes = {p.name: file_digest(p) for p in root.iterdir() if p.is_file()}
    published = _published_files(root, hashes)
    body = {
        "schema": "pipeline_integrity.coupled_tank_source_review.v1",
        "status": "needs_data",
        "source_sha256": hashes,
        "level_rows": len(times),
        "released_experiment_columns": 10,
        "control_rows": len(control),
        "complete_collection_rows": len(complete),
        "level_native_headers_preserved": True,
        "native_t_ref_s": times,
        "minimum_sample_step_s": min(b - a for a, b in zip(times, times[1:], strict=False)),
        "maximum_sample_step_s": max(b - a for a, b in zip(times, times[1:], strict=False)),
        "level_rows_without_same_time_command": sum(t not in set(input_times) for t in times),
        "operating_metadata_parse_error": parse_error,
        "operating_metadata": parsed,
        "readme_level_unit": "cm" if published.get("README.md") else None,
        "publisher_checksums_match": published,
        "invalid_json_contains_mm_unit_tokens": '"mm"' in metadata,
        "tank_diameter_and_internal_height_resolved": False,
        "temperature_pressure_and_density_resolved": False,
        "physical_inflow_outflow_calibrated": False,
        "command_is_flow": False,
        "unit_and_datum_review_required": True,
        "original_metadata_repaired": False,
    }
    return body | {"result_digest": digest(body)}


def coupled_tank_frames(root, request: CoupledTankReadRequest):
    """Preserve released t_ref, run identity and absent commands; apply only the caller-declared level mapping."""
    request = CoupledTankReadRequest.model_validate(request.model_dump(mode="python"))
    root = Path(root)
    h1, h2, control, times, input_times = _source_tables(root)
    controls = {t: _number(r[1]) for t, r in zip(input_times, control, strict=True)}
    scale = convert_quantity(1.0, request.level_unit, "m")
    source = {name: file_digest(root / name) for name in ("h1.csv", "h2.csv", "U.csv")}
    published = _published_files(root, source)
    if all(published.values()) and request.source_origin != "laboratory":
        raise ValueError("the pinned source documents laboratory experiments; the origin cannot be relabeled")
    run_id = f"coupled_tank.experiment_{request.run_number}"
    clock = run_id + ":released_relative_t_ref:" + digest(source)
    frames = []
    for index, time in enumerate(times):
        levels = [_number(rows[index][request.run_number]) for rows in (h1, h2)]
        values = {
            "tank1_level_m": None if levels[0] is None else levels[0] * scale + request.level_zero_offset_m,
            "tank2_level_m": None if levels[1] is None else levels[1] * scale + request.level_zero_offset_m,
            "pump_command_percent": controls.get(time),
        }
        frames.append(
            SignalFrame(
                stream_id=run_id,
                clock_id=clock,
                time_s=time,
                values=values,
                units={"tank1_level_m": "m", "tank2_level_m": "m", "pump_command_percent": "%"},
                valid={k: v is not None for k, v in values.items()},
                origin=request.source_origin,
            )
        )
    metadata = {
        "schema": "pipeline_integrity.coupled_tank_mapping.v1",
        "source_sha256": source,
        "publisher_checksums_match": published,
        "request": request.model_dump(mode="json"),
        "source_rows": list(range(2, len(times) + 2)),
        "clock_id": clock,
        "same_released_time_vector_used_for_each_experiment": True,
        "common_absolute_clock_across_experiments_established": False,
        "command_missing_at_unmatched_times_is_retained": True,
        "unit_datum_and_geometry_field_validated": False,
    }
    return tuple(frames), metadata | {"mapping_digest": digest(metadata)}
