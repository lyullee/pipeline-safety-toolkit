"""Read the licensed stagnant-potash pipe experiment without conflating related studies."""

from __future__ import annotations

import hashlib
import math
import re
import zipfile
from pathlib import Path, PurePosixPath

from .contracts import EvidenceRef, SignalFrame
from xml.etree import ElementTree

DATASET_ID = "potash_pipe_cp_stagnant"
DATASET_DOI = "10.17632/z6m27978vj.2"
DATASET_VERSION = "2"
DATASET_LICENSE = "CC-BY-4.0"
SOURCE_FILENAME = "Current Density to Protect Carbon Steel in Saturated Brine.docx"
SOURCE_SHA256 = "de262b94567fb6d72c6ed520cdbecb5660d68579e266e33d0b33c023def9ed2f"
SOURCE_BYTES = 15345
SOURCE_UNITS = "mA/cm2"
SI_UNITS = "A/m2"
SI_CONVERSION_FACTOR = 10.0
_EXPECTED_DATA_ROWS = 20
_EXPECTED_REPLICATES = 3
_MAXIMUM_SOURCE_BYTES = 10_000_000
_MAXIMUM_XML_BYTES = 10_000_000
_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_W = "{" + _NS["w"] + "}"


class PotashPipeCPError(ValueError):
    """The file does not match the pinned public stagnant-pipe source schema."""


def _text(element):
    return "".join(node.text or "" for node in element.findall(".//w:t", _NS)).strip()


def _cells(row):
    return row.findall("./w:tc", _NS)


def _numeric(cell, label):
    raw = _text(cell)
    if not raw:
        raise PotashPipeCPError(f"missing required {label}")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise PotashPipeCPError(f"invalid numeric {label}") from exc
    if not math.isfinite(number) or number < 0:
        raise PotashPipeCPError(f"nonfinite or negative {label}")
    return number


def read_potash_pipe_cp(path, *, expected_sha256=SOURCE_SHA256):
    """Read time-series replicates and reported summaries from the pinned CC BY DOCX.

    Point observations are returned to the caller and are not written into the package.
    Reported averages and confidence intervals are preserved, never recomputed.
    """
    source = Path(path)
    try:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise PotashPipeCPError("an explicit lowercase SHA-256 source pin is required")
        if source.is_symlink() or not source.is_file() or source.stat().st_size > _MAXIMUM_SOURCE_BYTES:
            raise PotashPipeCPError("source must be a regular DOCX within the declared size budget")
        hasher = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        source_sha256 = hasher.hexdigest()
        if source_sha256 != expected_sha256:
            raise PotashPipeCPError("source SHA-256 differs from the reviewed dataset asset")
        if expected_sha256 == SOURCE_SHA256 and source.stat().st_size != SOURCE_BYTES:
            raise PotashPipeCPError("source size does not match the pinned Mendeley V2 asset")
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if sum(member.file_size for member in members) > _MAXIMUM_XML_BYTES * 2:
                raise PotashPipeCPError("DOCX expanded content exceeds the declared size budget")
            seen = set()
            for member in members:
                raw = member.filename
                name = raw[:-1] if member.is_dir() and raw.endswith("/") else raw
                logical = PurePosixPath(name)
                if (
                    not name
                    or logical.is_absolute()
                    or ".." in logical.parts
                    or "\\" in raw
                    or ":" in raw
                    or logical.as_posix() != name
                    or name.casefold() in seen
                ):
                    raise PotashPipeCPError("DOCX contains an unsafe or ambiguous member path")
                seen.add(name.casefold())
                if member.file_size > _MAXIMUM_XML_BYTES:
                    raise PotashPipeCPError("DOCX member exceeds the declared XML size budget")
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise PotashPipeCPError("DOCX document XML is missing") from exc
            if info.file_size > _MAXIMUM_XML_BYTES:
                raise PotashPipeCPError("Word document XML exceeds the declared size budget")
            xml = archive.read(info)
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise PotashPipeCPError("DTD and entity declarations are not accepted")
            document = ElementTree.fromstring(xml)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        if isinstance(exc, PotashPipeCPError):
            raise
        raise PotashPipeCPError("DOCX container or document XML is invalid") from exc

    body = document.find("w:body", _NS)
    if body is None:
        raise PotashPipeCPError("Word document body is missing")
    tables = [child for child in body if child.tag == _W + "tbl"]
    if len(tables) != 1:
        raise PotashPipeCPError("the pinned pipe experiment requires exactly one data table")
    rows = tables[0].findall("./w:tr", _NS)
    if len(rows) != _EXPECTED_DATA_ROWS + 2:
        raise PotashPipeCPError("the source row count differs from the pinned schema")
    header = [_text(cell).casefold().replace("²", "2") for cell in _cells(rows[0])]
    required = (
        ("time", "min"),
        ("protection", "current", "density", "ma/cm2"),
        ("average", "ma/cm2"),
        ("95%", "confidence", "interval", "ma/cm2"),
    )
    if len(header) != 4 or any(not all(token in re.sub(r"\s+", " ", label) for token in tokens)
                                for label, tokens in zip(header, required, strict=True)):
        raise PotashPipeCPError("the first table row does not match the reviewed four-field schema")
    trial_labels = [_text(cell).casefold() for cell in _cells(rows[1])]
    if len(trial_labels) != 6 or [value for value in trial_labels if value] != [
        "trial 1", "trial 2", "trial 3"
    ]:
        raise PotashPipeCPError("the source must declare exactly three replicate columns")

    points = []
    for index, row in enumerate(rows[2:], start=1):
        cells = _cells(row)
        if len(cells) != 6:
            raise PotashPipeCPError(f"source observation row {index} has an unexpected cell count")
        values = [_numeric(cell, "observation field") for cell in cells]
        time_min, *remainder = values
        replicates, reported_mean, reported_ci = remainder[:3], remainder[3], remainder[4]
        if len(replicates) != _EXPECTED_REPLICATES:
            raise PotashPipeCPError(f"source observation row {index} has the wrong replicate count")
        points.append(
            {
                "time_min": time_min,
                "replicate_current_density_mA_cm2": replicates,
                "reported_mean_current_density_mA_cm2": reported_mean,
                "reported_95_ci_mA_cm2": reported_ci,
                "replicate_current_density_A_m2": [value * SI_CONVERSION_FACTOR for value in replicates],
                "reported_mean_current_density_A_m2": reported_mean * SI_CONVERSION_FACTOR,
                "reported_95_ci_A_m2": reported_ci * SI_CONVERSION_FACTOR,
                "reported_statistics_recomputed": False,
                "replicates_are_independent_field_trials": False,
            }
        )
    times = [point["time_min"] for point in points]
    if (
        times[0] != 0.0
        or times[-1] != 95.0
        or times != sorted(set(times))
        or any(
            not math.isclose(after - before, 5.0, rel_tol=0.0, abs_tol=1e-12)
            for before, after in zip(times, times[1:])
        )
    ):
        raise PotashPipeCPError("time points must cover 0 to 95 minutes on the unique five-minute grid")
    return {
        "schema": "pipeline_integrity.potash_pipe_cp.v1",
        "dataset_id": DATASET_ID,
        "doi": DATASET_DOI,
        "version": DATASET_VERSION,
        "license_id": DATASET_LICENSE,
        "attribution": {
            "creator": "Michael Trask",
            "doi": DATASET_DOI,
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "modification_notice": "Added explicit A/m2 fields using factor 10; source fields are retained.",
        },
        "source_filename": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": source_sha256,
        "source_checksum_verified": True,
        "time_basis": "minutes_from_exposure_start_as_published",
        "source_units": SOURCE_UNITS,
        "si_units": SI_UNITS,
        "si_conversion_factor": SI_CONVERSION_FACTOR,
        "row_count": len(points),
        "replicate_count_per_time": _EXPECTED_REPLICATES,
        "reported_mean_and_ci_recomputed": False,
        "replicates_are_independent_field_trials": False,
        "field_transfer_or_cathodic_protection_qualification": False,
        "limitations": [
            "The three reported replicates are not independent field opportunities.",
            "The source does not establish a general field design basis or corrosion-loss rate.",
            "Review electrode potential, exposed area, temperature, geometry and protection criterion before engineering use.",
        ],
        "points": points,
    }


def to_signal_frames(
    dataset, *, clock_id, stream_id, statistic="reported_mean", replicate_index=None
):
    """Map one declared laboratory series into existing SignalFrame contracts.

    Returns ``(frames, source_evidence)``. The caller must add that evidence to its
    RunContext and separately decide whether the laboratory series applies to its
    asset. No area, bare fraction, potential criterion, or field design input is inferred.
    """
    if (
        not isinstance(dataset, dict)
        or dataset.get("schema") != "pipeline_integrity.potash_pipe_cp.v1"
        or dataset.get("dataset_id") != DATASET_ID
        or dataset.get("source_sha256") != SOURCE_SHA256
        or dataset.get("source_checksum_verified") is not True
        or dataset.get("license_id") != DATASET_LICENSE
        or dataset.get("time_basis") != "minutes_from_exposure_start_as_published"
        or not isinstance(dataset.get("points"), list)
        or len(dataset["points"]) != _EXPECTED_DATA_ROWS
    ):
        raise PotashPipeCPError("adapter requires the checksum-verified pinned dataset record")
    if statistic == "reported_mean":
        field = "reported_mean_current_density_A_m2"
        label = "publisher-reported mean"
        if replicate_index is not None:
            raise PotashPipeCPError("replicate_index only applies to a replicate series")
    elif statistic == "replicate":
        if type(replicate_index) is not int or replicate_index not in range(_EXPECTED_REPLICATES):
            raise PotashPipeCPError("replicate series requires replicate_index 0, 1, or 2")
        field = None
        label = f"replicate {replicate_index + 1}"
    else:
        raise PotashPipeCPError("statistic must be reported_mean or replicate")

    frames = []
    previous_time = -1.0
    for point in dataset["points"]:
        minute = point.get("time_min")
        if not isinstance(minute, (int, float)) or not math.isfinite(minute) or minute <= previous_time:
            raise PotashPipeCPError("adapted time points must be finite and strictly increasing")
        if statistic == "replicate":
            values = point.get("replicate_current_density_A_m2")
            value = values[replicate_index] if isinstance(values, list) and len(values) == 3 else None
        else:
            value = point.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise PotashPipeCPError("selected protective-demand current density is invalid")
        frames.append(
            SignalFrame(
                stream_id=stream_id,
                clock_id=clock_id,
                time_s=float(minute) * 60.0,
                values={"protective_demand_current_density": float(value)},
                units={"protective_demand_current_density": "A/m2"},
                valid={"protective_demand_current_density": True},
                origin="laboratory",
                operating_mode="stagnant_saturated_potash_brine",
            )
        )
        previous_time = float(minute)
    evidence = EvidenceRef(
        id=f"{DATASET_ID}:{SOURCE_SHA256[:12]}:{statistic}:{replicate_index}",
        citation=f"Michael Trask, Mendeley Data V2, DOI {DATASET_DOI}",
        origin="laboratory",
        locator=f"https://doi.org/{DATASET_DOI}; selected series: {label}",
        sha256=SOURCE_SHA256,
        license_id=DATASET_LICENSE,
        limitations=(
            "95-minute stagnant-brine laboratory series; not independent field trials",
            "does not establish exposed area, protection potential criterion, or field transfer",
            "reported source summaries are retained and not recomputed",
        ),
    )
    return tuple(frames), evidence
