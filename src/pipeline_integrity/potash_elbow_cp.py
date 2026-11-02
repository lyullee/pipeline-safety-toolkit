"""Read the reviewed Mendeley potash-elbow DOCX with standard-library OOXML tools."""

from __future__ import annotations

import hashlib
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

DATASET_ID = "potash_elbow_cp_slurry"
DATASET_DOI = "10.17632/gkwrx7tz2k.3"
DATASET_VERSION = "3"
DATASET_LICENSE = "CC-BY-4.0"
SOURCE_FILENAME = (
    "Current Density Required to Protect AISI 1018 CS in Saturated Potash Brine "
    "in Various Slurry Concentrations.docx"
)
SOURCE_SHA256 = "9b6ba2bd756d402596783353c9dfb7ecad951fb6f3780e67e239acc7f9aa5103"
SOURCE_BYTES = 34410
_EXPECTED_DATA_ROWS = {1: 25, 2: 11, 3: 36, 4: 36, 5: 36}
_EXPECTED_REPLICATES = {1: 9, 2: 9, 3: 4, 4: 3, 5: 3}
SOURCE_UNITS = "mA/cm2"
SI_UNITS = "A/m2"
SI_CONVERSION_FACTOR = 10.0
MAXIMUM_SOURCE_BYTES = 10_000_000
MAXIMUM_XML_BYTES = 10_000_000

_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_W = "{" + _NS["w"] + "}"
_CAPTION_TABLE = re.compile(r"\bTable\s+A\.(\d+)\b", re.IGNORECASE)
_CAPTION_VELOCITY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*m\s*/\s*s\b", re.IGNORECASE)


class PotashElbowCPError(ValueError):
    """The source archive or its documented table schema is not usable."""


def _text(element):
    return "".join(node.text or "" for node in element.findall(".//w:t", _NS)).strip()


def _cells(row):
    return row.findall("./w:tc", _NS)


def _number(cell, *, label, nonnegative=True):
    raw = _text(cell)
    if not raw:
        raise PotashElbowCPError(f"missing required {label}")
    try:
        value = float(raw.replace(",", ""))
    except ValueError as exc:
        raise PotashElbowCPError(f"invalid numeric {label}") from exc
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise PotashElbowCPError(f"nonfinite or negative {label}")
    return raw, value


def _check_headers(table, table_index):
    rows = table.findall("./w:tr", _NS)
    if len(rows) < 4:
        raise PotashElbowCPError(f"table {table_index} lacks its three header rows or data")
    first = [_text(cell).casefold().replace("²", "2") for cell in _cells(rows[0])]
    if len(first) != 4:
        raise PotashElbowCPError(f"table {table_index} has an unexpected grouped header")
    required = (
        ("slurry", "concentration", "wt.%"),
        ("cathodic", "protection", "current", "density", "ma/cm2"),
        ("average", "current", "density", "ma/cm2"),
        ("95%", "confidence", "interval", "ma/cm2"),
    )
    for label, tokens in zip(first, required, strict=True):
        normalized = re.sub(r"\s+", " ", label)
        if not all(token in normalized for token in tokens):
            raise PotashElbowCPError(f"table {table_index} header does not match the reviewed source schema")
    second = " ".join(_text(cell).casefold() for cell in _cells(rows[1]))
    if "experimental trials" not in second:
        raise PotashElbowCPError(f"table {table_index} is missing the replicate-trial header")
    trial_cells = _cells(rows[2])
    if len(trial_cells) < 5:
        raise PotashElbowCPError(f"table {table_index} needs at least two replicate columns")
    trial_ids = [_text(cell) for cell in trial_cells[1:-2]]
    if trial_ids != [str(index) for index in range(1, len(trial_ids) + 1)]:
        raise PotashElbowCPError(f"table {table_index} replicate columns are not sequential")
    if _text(trial_cells[0]) or any(_text(cell) for cell in trial_cells[-2:]):
        raise PotashElbowCPError(f"table {table_index} has unexpected labels in aggregate columns")
    return rows, len(trial_ids)


def _caption_metadata(caption, table_index):
    table_match = _CAPTION_TABLE.search(caption)
    velocity_match = _CAPTION_VELOCITY.search(caption)
    if table_match is None or velocity_match is None:
        raise PotashElbowCPError(f"table {table_index} has no immediately preceding table/velocity caption")
    table_id = f"A.{int(table_match.group(1))}"
    velocity = float(velocity_match.group(1))
    if velocity <= 0:
        raise PotashElbowCPError(f"table {table_index} has a nonpositive caption velocity")
    return table_id, velocity


def read_potash_elbow_cp(path, *, expected_sha256=SOURCE_SHA256):
    """Parse all source rows while keeping replicates and publisher summaries distinct.

    The default checksum pins Mendeley Data V3. An alternate digest may be supplied only
    when a caller intentionally pins another compatible file; the parser still enforces
    this reviewed OOXML schema. Source observations are read from the caller's DOCX and
    are never bundled in this module.
    """
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise PotashElbowCPError("source must be a regular DOCX file")
    if source.stat().st_size > MAXIMUM_SOURCE_BYTES:
        raise PotashElbowCPError("source exceeds the declared DOCX size budget")
    if expected_sha256 == SOURCE_SHA256 and source.stat().st_size != SOURCE_BYTES:
        raise PotashElbowCPError("the pinned Mendeley V3 file size differs from its publisher metadata")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise PotashElbowCPError("an explicit lowercase SHA-256 source pin is required")
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    source_sha256 = hasher.hexdigest()
    if source_sha256 != expected_sha256:
        raise PotashElbowCPError("source SHA-256 differs from the declared pin")
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or "word/document.xml" not in names:
                raise PotashElbowCPError("DOCX member list is incomplete or contains duplicates")
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise PotashElbowCPError("DOCX contains an unsafe member path")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAXIMUM_XML_BYTES:
                raise PotashElbowCPError("Word document XML exceeds the declared size budget")
            xml = archive.read(info)
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise PotashElbowCPError("DTD and entity declarations are not accepted")
            document = ElementTree.fromstring(xml)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        if isinstance(exc, PotashElbowCPError):
            raise
        raise PotashElbowCPError("DOCX container or document XML is invalid") from exc

    body = document.find("w:body", _NS)
    if body is None:
        raise PotashElbowCPError("DOCX body is missing")
    caption = ""
    tables = []
    for child in body:
        if child.tag == _W + "p":
            candidate = _text(child)
            if candidate:
                caption = candidate
        elif child.tag == _W + "tbl":
            tables.append((caption, child))
    if len(tables) != 5:
        raise PotashElbowCPError("the pinned experiment must contain exactly five source tables")

    rows_out = []
    seen_by_velocity_table = defaultdict(set)
    fragment_numbers = defaultdict(int)
    table_manifest = []
    for table_index, (table_caption, table) in enumerate(tables, start=1):
        table_id, velocity_m_s = _caption_metadata(table_caption, table_index)
        fragment_numbers[table_id] += 1
        fragment_index = fragment_numbers[table_id]
        xml_rows, replicate_count = _check_headers(table, table_index)
        if len(xml_rows) - 3 != _EXPECTED_DATA_ROWS[table_index]:
            raise PotashElbowCPError(f"table {table_index} row count differs from the pinned source schema")
        if replicate_count != _EXPECTED_REPLICATES[table_index]:
            raise PotashElbowCPError(
                f"table {table_index} replicate count differs from the pinned source schema"
            )
        table_manifest.append(
            {
                "source_table_index": table_index,
                "velocity_table_id": table_id,
                "fragment_index": fragment_index,
                "velocity_m_s": velocity_m_s,
                "data_rows": len(xml_rows) - 3,
                "replicate_count": replicate_count,
            }
        )
        for row_index, row in enumerate(xml_rows[3:], start=4):
            cells = _cells(row)
            if len(cells) != replicate_count + 3:
                raise PotashElbowCPError(f"table {table_index} row {row_index} column count changed")
            source_text = [_text(cell) for cell in cells]
            if any(value == "" for value in source_text):
                raise PotashElbowCPError(f"table {table_index} row {row_index} has an empty required cell")
            concentration_raw, concentration = _number(cells[0], label="sand concentration")
            if concentration > 100:
                raise PotashElbowCPError(
                    f"table {table_index} row {row_index} sand fraction exceeds 100 wt.%"
                )
            if concentration in seen_by_velocity_table[table_id]:
                raise PotashElbowCPError(f"duplicate sand concentration in velocity table {table_id}")
            seen_by_velocity_table[table_id].add(concentration)
            replicate_pairs = [_number(cell, label="replicate current density") for cell in cells[1:-2]]
            mean_raw, mean = _number(cells[-2], label="reported mean current density")
            ci_raw, ci = _number(cells[-1], label="reported 95% confidence interval")
            replicates = [value for _, value in replicate_pairs]
            rows_out.append(
                {
                    "row_id": f"source_table_{table_index}:row_{row_index}",
                    "velocity_table_id": table_id,
                    "source_table_index": table_index,
                    "fragment_index": fragment_index,
                    "source_row_index": row_index,
                    "velocity_m_s": velocity_m_s,
                    "sand_concentration_wt_pct": concentration,
                    "sand_concentration_source_text": concentration_raw,
                    "replicate_trial_ids": list(range(1, replicate_count + 1)),
                    "replicate_current_density_mA_cm2": replicates,
                    "replicate_current_density_A_m2": [value * SI_CONVERSION_FACTOR for value in replicates],
                    "replicate_source_text": [raw for raw, _ in replicate_pairs],
                    "reported_mean_current_density_mA_cm2": mean,
                    "reported_mean_current_density_A_m2": mean * SI_CONVERSION_FACTOR,
                    "reported_mean_source_text": mean_raw,
                    "reported_95_ci_mA_cm2": ci,
                    "reported_95_ci_A_m2": ci * SI_CONVERSION_FACTOR,
                    "reported_95_ci_source_text": ci_raw,
                    "current_density_source_units": SOURCE_UNITS,
                    "current_density_si_units": SI_UNITS,
                    "current_density_si_conversion_factor": SI_CONVERSION_FACTOR,
                    "replicate_readings_are_field_trials": False,
                    "row_is_independent_field_trial": False,
                    "source_cells_text": source_text,
                }
            )

    expected_fragments = [("A.1", 1), ("A.1", 2), ("A.2", 1), ("A.3", 1), ("A.4", 1)]
    actual_fragments = [(item["velocity_table_id"], item["fragment_index"]) for item in table_manifest]
    if actual_fragments != expected_fragments:
        raise PotashElbowCPError("caption/table fragment sequence differs from the pinned source schema")
    expected_velocities = {"A.1": 2.5, "A.2": 3.0, "A.3": 3.5, "A.4": 4.0}
    for item in table_manifest:
        if item["velocity_m_s"] != expected_velocities[item["velocity_table_id"]]:
            raise PotashElbowCPError("caption velocity differs from the pinned source schema")
    if any(len(seen_by_velocity_table[key]) != 36 for key in expected_velocities):
        raise PotashElbowCPError("merged velocity tables must cover 36 unique sand-loading conditions")

    return {
        "schema": "pipeline_integrity.potash_elbow_cp.v1",
        "dataset_id": DATASET_ID,
        "doi": DATASET_DOI,
        "version": DATASET_VERSION,
        "license_id": DATASET_LICENSE,
        "attribution": {
            "creator": "Michael Trask",
            "title": (
                "Protection Current Density Required to Protect an AISI 1018 Carbon Steel Elbow in "
                "Saturated Potash Brines at Various Flow Velocities and Slurry Concentrations of "
                "1.05 mm Diameter Sand using a Vertical Flow Loop Apparatus"
            ),
            "doi": DATASET_DOI,
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "modification_notice": (
                "Source values retained with added explicit A/m2 conversion at factor 10; "
                "source observations and reported summaries are not altered."
            ),
        },
        "source_filename": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": source_sha256,
        "source_checksum_verified": True,
        "source_units": SOURCE_UNITS,
        "si_units": SI_UNITS,
        "si_conversion_factor": SI_CONVERSION_FACTOR,
        "table_fragments": table_manifest,
        "rows": rows_out,
        "row_count": len(rows_out),
        "replicates_are_independent_field_trials": False,
        "velocity_conditions_are_independent_field_trials": False,
        "reported_mean_and_ci_are_recomputed": False,
        "field_transfer_or_cathodic_protection_qualification": False,
    }
