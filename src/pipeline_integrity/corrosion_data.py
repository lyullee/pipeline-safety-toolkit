"""Pinned copper sensor and nanoscratch observations, without inferred exposure or hardness."""

import csv
import hashlib
import io
import math
from datetime import datetime
from pathlib import Path

from .contracts import EvidenceRef, digest
from .datasets import catalog

COPPER_HEADER = (
    "Datetime,NO2_holesovice,PM10_holesovice,O3_holesovice,PM2_5_holesovice,corrosion,"
    "temp_in,hum_in,dew_in,SO2,NO2,PM10,O3,PM2_5,temp_out,dew_out,qnh,winddir,"
    "windspeed,hum_out,corrosion_diff,temp_in_2,hum_in_2,dew_in_2,temp_in_3,hum_in_3,dew_in_3"
).split(",")
AFM_SECTIONS = (
    "FIGURE 4A",
    "Figure 4B",
    "Figure 6A",
    "Figure 7",
    "Figure 8",
    "Figure 10",
    "Figure 12",
    "Figure 13A",
    "Figure 13B",
)


def _seal(body):
    return body | {"record_digest": digest(body)}


def _number(token):
    if not token.strip():
        return {"value": None, "status": "missing"}
    try:
        value = float(token)
    except ValueError:
        return {"value": None, "status": "invalid"}
    if not math.isfinite(value):
        return {"value": None, "status": "nonfinite"}
    return {"value": value, "status": "finite"}


def _scaled(token, scale):
    value = _number(token)["value"]
    return None if value is None or scale is None else value * scale


def _snapshot(path, dataset_id):
    """Validate a small publisher-pinned CSV before parsing one memory snapshot."""
    path = Path(path)
    source = next((d for d in catalog() if d.id == dataset_id), None)
    assets = [] if source is None else [a for a in source.assets if a.filename == path.name]
    if (
        source is None
        or source.acquisition != "allowed"
        or source.rights_review != "reviewed_license"
        or len(assets) != 1
        or assets[0].bytes is None
        or assets[0].checksum is None
        or assets[0].checksum_origin != "publisher_metadata"
    ):
        raise ValueError("one reviewed publisher-pinned CSV asset is required")
    asset = assets[0]
    if asset.bytes > 4 * 1024 * 1024 or path.stat().st_size != asset.bytes:
        raise ValueError("source CSV size or 4 MiB reader budget differs")
    with path.open("rb") as stream:
        raw = stream.read(asset.bytes + 1)
    algorithm, expected = asset.checksum.split(":", 1)
    if len(raw) != asset.bytes or hashlib.new(algorithm, raw).hexdigest() != expected:
        raise ValueError("source CSV checksum differs from publisher metadata")
    receipt = dict(
        dataset_id=source.id,
        source=source.source,
        citation=source.citation,
        creators=list(source.creators),
        version=source.version,
        license_id=source.license_id,
        rights_evidence_url=source.rights_evidence_url,
        rights_review=source.rights_review,
        checked_on=source.checked_on,
        filename=asset.filename,
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        supplier_checksum=asset.checksum,
        publisher_checksum_verified=True,
    )
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))), receipt


def parse_copper_rows(rows):
    """Retain provider units as unknown; elapsed observation time is not exposure age."""
    if not rows or rows[0] != COPPER_HEADER or any(len(r) != len(COPPER_HEADER) for r in rows):
        raise ValueError("copper CSV header or row width differs")
    parsed, clock = [], []
    for index, cells in enumerate(rows[1:], start=2):
        try:
            stamp = datetime.strptime(cells[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            stamp = None
        clock.append(stamp)
        values = {k: _number(v) for k, v in zip(COPPER_HEADER[1:], cells[1:], strict=True)}
        parsed.append(
            dict(
                source_row=index,
                source_cells=cells,
                datetime_naive=None if stamp is None else stamp.isoformat(),
                numeric_cells=values,
                corrosion_source_value=values["corrosion"]["value"],
                corrosion_difference_source_value=values["corrosion_diff"]["value"],
                corrosion_m=None,
                corrosion_difference_m=None,
                physical_exposure_age_s=None,
            )
        )
    origin = clock[0] if clock else None
    for row, stamp in zip(parsed, clock, strict=True):
        row["elapsed_from_first_observation_s"] = (
            None if origin is None or stamp is None else (stamp - origin).total_seconds()
        )
    intervals = [
        None if a is None or b is None else (b - a).total_seconds()
        for a, b in zip(clock, clock[1:], strict=False)
    ]
    differences = []
    for previous, current in zip(parsed, parsed[1:], strict=False):
        a, b, supplied = (
            previous["corrosion_source_value"],
            current["corrosion_source_value"],
            current["corrosion_difference_source_value"],
        )
        differences.append(None if None in (a, b, supplied) else abs(b - a - supplied))
    return _seal(
        dict(
            header=rows[0],
            rows=parsed,
            observation_clock="provider naive datetime; timezone and exposure zero unknown",
            source_depth_unit=None,
            unit_review="CSV has no unit label; article nm plotting does not explicitly map CSV scale",
            material_condition="copper sensing element; alloy, calibration and initial age unresolved",
            physical_specimens_resolved=None,
            source_rows_sorted_filtered_interpolated_or_rectified=False,
            summary=dict(
                rows=len(parsed),
                invalid_clocks=sum(t is None for t in clock),
                repeated_adjacent_clocks=sum(t == 0 for t in intervals),
                decreasing_adjacent_clocks=sum(t is not None and t < 0 for t in intervals),
                adjacent_intervals_other_than_3600_s=sum(t != 3600 for t in intervals),
                missing_or_invalid_numeric_cells=sum(
                    v["status"] != "finite" for row in parsed for v in row["numeric_cells"].values()
                ),
                negative_corrosion_values=sum(
                    r["corrosion_source_value"] is not None and r["corrosion_source_value"] < 0
                    for r in parsed
                ),
                negative_provider_differences=sum(
                    r["corrosion_difference_source_value"] is not None
                    and r["corrosion_difference_source_value"] < 0
                    for r in parsed
                ),
                checked_adjacent_differences=sum(d is not None for d in differences),
                maximum_provider_difference_residual_source_unit=max(
                    (d for d in differences if d is not None), default=None
                ),
                source_unit_minimum=min(
                    (r["corrosion_source_value"] for r in parsed if r["corrosion_source_value"] is not None),
                    default=None,
                ),
                source_unit_maximum=max(
                    (r["corrosion_source_value"] for r in parsed if r["corrosion_source_value"] is not None),
                    default=None,
                ),
            ),
        )
    )


def read_copper_wire(path):
    rows, provenance = _snapshot(path, "copper_wire_corrosion")
    return _seal(dict(provenance=provenance, observations=parse_copper_rows(rows)))


def parse_afm_rows(rows, *, scratch_method_evidence=None):
    """Separate figure panels and keep repeated loads, signed indentation and every padded row."""
    if not rows or any(len(r) != 10 for r in rows):
        raise ValueError("AFM CSV must preserve its ten-column figure layout")
    starts = [i for i, row in enumerate(rows) if row[0] in AFM_SECTIONS]
    if tuple(rows[i][0] for i in starts) != AFM_SECTIONS:
        raise ValueError("AFM figure sections differ from the reviewed source")
    if scratch_method_evidence is not None:
        scratch_method_evidence = EvidenceRef.model_validate(scratch_method_evidence)
        if scratch_method_evidence.origin != "literature" or scratch_method_evidence.sha256 is None:
            raise ValueError("scratch distance review requires hashed primary-method evidence")
    sections = {}
    for position, start in enumerate(starts):
        stop = starts[position + 1] if position + 1 < len(starts) else len(rows)
        sections[rows[start][0]] = [
            {"source_row": i + 1, "source_cells": rows[i]} for i in range(start, stop)
        ]

    def numeric(section, offset, width):
        records = []
        for row in sections[section][offset:]:
            cells = row["source_cells"]
            if not any(cells):
                continue  # The full section above retains these layout-only rows.
            if any(cells[width:]):
                raise ValueError("AFM numeric panel has unexpected trailing values")
            records.append(row | {"numeric_cells": [_number(v) for v in cells[:width]]})
        return records

    hardness = numeric("Figure 4B", 4, 3)
    if sections["Figure 4B"][3]["source_cells"][:3] != ["mN", "Gpa", "%"]:
        raise ValueError("AFM hardness units differ")
    for row in hardness:
        c = row["source_cells"]
        row.update(load_N=_scaled(c[0], 1e-3), hardness_Pa=_scaled(c[1], 1e9))

    passes = numeric("Figure 6A", 4, 3)
    if sections["Figure 6A"][3]["source_cells"][:3] != ["-", "micron^3", "micron^3"]:
        raise ValueError("AFM pass-volume units differ")
    for row in passes:
        c = row["source_cells"]
        row.update(
            passes=_scaled(c[0], 1),
            reported_average_volume_removed_m3=_scaled(c[1], 1e-18),
            reported_error_m3=_scaled(c[2], 1e-18),
            sliding_distance_m=_scaled(c[0], 5e-6) if scratch_method_evidence else None,
            physical_scratch_id=None,
        )

    loads = numeric("Figure 10", 3, 3)
    if sections["Figure 10"][2]["source_cells"][:3] != [
        "Load, [microN]",
        "Pile Up Volume, [micron^3]",
        "Removed Volume, [micron^3]",
    ]:
        raise ValueError("AFM load-volume units differ")
    for row in loads:
        c = row["source_cells"]
        force, volume = _scaled(c[0], 1e-6), _scaled(c[2], 1e-18)
        distance = 0.01 if scratch_method_evidence else None
        specific = None if None in (force, volume, distance) or force <= 0 else volume / (force * distance)
        row.update(
            normal_force_N=force,
            reported_pile_up_volume_m3=_scaled(c[1], 1e-18),
            reported_removed_volume_m3=volume,
            sliding_distance_m=distance,
            specific_reported_removed_volume_m3_per_N_m=specific,
            matched_scratch_hardness_Pa=None,
            distributed_wear_area_m2=None,
            physical_scratch_id=None,
        )
    return _seal(
        dict(
            source_rows=len(rows),
            sections=sections,
            nanoindentation_hardness=hardness,
            interrupted_pass_average_volume=passes,
            continuous_load_volume=loads,
            scratch_method_evidence=(
                None if scratch_method_evidence is None else scratch_method_evidence.model_dump(mode="json")
            ),
            source_rows_sorted_filtered_interpolated_or_rectified=False,
            summary=dict(
                figure_sections=len(sections),
                nanoindentation_hardness_rows=len(hardness),
                average_pass_volume_rows=len(passes),
                continuous_load_volume_rows=len(loads),
                repeated_load_occurrences=len(loads) - len({r["source_cells"][0] for r in loads}),
                nanoindentation_to_scratch_hardness_mapping_reviewed=False,
                independent_physical_specimens_resolved=None,
            ),
            interpretation=(
                "Figure labels are preserved; reported removed volume is not silently replaced by groove-minus-pile-up.",
                "Hardness mN and scratch microN conditions are distinct; passes and average figure rows are not specimens.",
                "Specific reported volume per force-distance has units m3/(N*m), not a dimensionless Archard coefficient.",
            ),
        )
    )


def read_afm_steel_wear(path, *, scratch_method_evidence=None):
    rows, provenance = _snapshot(path, "afm_steel_wear")
    observations = parse_afm_rows(rows, scratch_method_evidence=scratch_method_evidence)
    return _seal(dict(provenance=provenance, observations=observations))
