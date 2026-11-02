"""Preserve publisher indentation rows; keep assumed geometry separate from source conditions."""

import hashlib
import io
import json
import math
import stat
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

from pydantic import Field, model_validator

from .contracts import Contract, EvidenceRef, Name, RunContext, digest, json_value
from .hardness_exchange import REVIEWS, HardnessConditions, HardnessRequest, evaluate_hardness
from .source_archive import pinned_source_zip

METADATA = (
    "Versuchsbezeichnung",
    "Norm",
    "Versuchs-Datum",
    "Probenbezeichnung",
    "Werkstoff",
    "Probe Vorbereitung",
    "Probe Dimension",
    "Prüfmaschine",
    "Prüftemperatur",
    "Prüfkraft F",
    "Einwirkdauer der Prüfkraft",
    "Eindringkörper",
    "Eindringkörper Material",
    "Kugeldurchmesser",
    "Eindringwinkel",
    "Abstand zwischen den Eindrücken",
    "Abstand zwischen den Eindrücken und Rand",
)


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _verify(record, schema):
    json_value(record)
    if record.get("schema") != schema or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("copper source record changed after sealing")


def _cell(cell):
    value = cell.value
    if cell.data_type == "f":
        return dict(value=value, state="unevaluated_formula")
    if value is None or value == "":
        return dict(value=value, state="missing")
    if isinstance(value, float) and not math.isfinite(value):
        return dict(value=repr(value), state="invalid_nonfinite")
    if isinstance(value, str) and value.strip().upper() == "NN":
        return dict(value=value, state="unresolved_placeholder")
    if not isinstance(value, (str, int, float, bool)):
        return dict(value=str(value), state="unsupported_type")
    return dict(value=value, state="observed")


def _xlsx_snapshot(raw):
    """Validate the inner OOXML package separately from the pinned outer archive."""
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("hardness workbook byte budget exceeded")
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        infos, names, total = package.infolist(), set(), 0
        if len(infos) > 256:
            raise ValueError("hardness workbook part budget exceeded")
        for part in infos:
            name = part.filename
            path = PurePosixPath(name)
            if (
                not name
                or part.orig_filename != name
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or name in names
                or part.flag_bits & 1
                or stat.S_ISLNK(part.external_attr >> 16)
                or part.file_size > 4 * 1024 * 1024
            ):
                raise ValueError("unsafe or oversized hardness workbook part")
            names.add(name)
            total += part.file_size
            if total > 16 * 1024 * 1024:
                raise ValueError("hardness workbook expansion budget exceeded")
            if "vbaproject" in name.lower() or name.startswith("xl/externalLinks/"):
                raise ValueError("active or externally linked hardness workbook")
            if name.endswith((".xml", ".rels")):
                xml = package.read(part)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValueError("XML entities are not supported")
                if name.endswith(".rels") and any(
                    node.attrib.get("TargetMode", "").lower() == "external"
                    for node in ElementTree.fromstring(xml).iter()
                ):
                    raise ValueError("external workbook relationship is not supported")


def _parse_workbook(raw, member, method, provenance):
    _xlsx_snapshot(raw)
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("select one unambiguous source hardness sheet")
        sheet = workbook.worksheets[0]
        if sheet.max_row > 1000 or sheet.max_column > 64:
            raise ValueError("hardness sheet dimension budget exceeded")
        rows = list(sheet.iter_rows())
        markers = [i for i, row in enumerate(rows) if row[0].value == "[Daten]"]
        if len(markers) != 1:
            raise ValueError("one source indentation table is required")
        marker = markers[0]
        if marker + 3 >= len(rows):
            raise ValueError("source indentation headers, units and rows are required")
        header, units = rows[marker + 1], rows[marker + 2]
        expected = [
            "Probennummer",
            "d1",
            "d2",
            "d",
            "HBW" if method == "testing.brinell" else "HV",
            "Bemerkung",
        ]
        if [str(c.value).strip() for c in header[:6]] != expected:
            raise ValueError("unexpected source indentation columns")
        metadata = {}
        for row in rows[:marker]:
            key = row[0].value
            if key in METADATA:
                if key in metadata:
                    raise ValueError("duplicate hardness metadata field")
                metadata[key] = dict(
                    source_row=row[0].row, source_header=key, **_cell(row[1]), unit=row[2].value
                )
        observations = []
        for source_row, row in enumerate(rows[marker + 3 :], start=marker + 4):
            if all(c.value is None or c.value == "" for c in row):
                continue
            if any(c.value is not None and c.value != "" for c in row[6:]):
                raise ValueError("unexpected additional source indentation cells")
            observations.append(
                _seal(
                    dict(
                        schema="pipeline_integrity.copper_indentation.v1",
                        row_id=f"{member}#row={source_row}",
                        source_row=source_row,
                        cells={
                            key: _cell(cell)
                            for key, cell in zip(
                                (
                                    "provider_indent_id",
                                    "d1",
                                    "d2",
                                    "source_mean",
                                    "reported_hardness",
                                    "notes",
                                ),
                                row[:6],
                                strict=True,
                            )
                        },
                    )
                )
            )
        if not observations:
            raise ValueError("source hardness table has no observations")
        return _seal(
            dict(
                schema="pipeline_integrity.copper_hardness_test.v1",
                provenance=provenance,
                member=member,
                workbook_bytes=len(raw),
                workbook_sha256=hashlib.sha256(raw).hexdigest(),
                sheet=sheet.title,
                method=method,
                metadata=metadata,
                source_header_row=header[0].row,
                source_units_row=marker + 3,
                source_headers=[c.value for c in header[:6]],
                source_units=[c.value for c in units[:6]],
                summary_method_label_conflict=method == "testing.vickers"
                and any("Brinell" in str(row[0].value) for row in rows[:marker]),
                grouping_basis="publisher specimen labels; physical independence and cross-method pairing unproven",
                rows=observations,
            )
        )
    finally:
        workbook.close()


def load_copper_hardness(archive):
    """Read primary reports in archive/row order without repairing or executing cells."""
    tests = []
    with pinned_source_zip(archive, "copper_mechanical") as (source, provenance):
        for item in source.infolist():
            method = next(
                (
                    method
                    for prefix, method in (
                        ("Brinell hardness test/Primary data/", "testing.brinell"),
                        ("Vickers hardness test/Primary data/", "testing.vickers"),
                    )
                    if item.filename.startswith(prefix) and item.filename.endswith(".xlsx")
                ),
                None,
            )
            if method:
                tests.append(_parse_workbook(source.read(item), item.filename, method, provenance))
    if not tests:
        raise ValueError("no primary copper hardness reports found")
    return _seal(
        dict(schema="pipeline_integrity.copper_hardness_source.v1", provenance=provenance, tests=tests)
    )


class CopperGeometryReview(Contract):
    expected_test_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_row_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    units_and_column_mapping_reviewed: bool = False
    force_dimensions_dwell_and_method_reviewed: bool = False
    geometry_model_reviewed: bool = False
    diagnostic_limitations_reviewed: bool = False
    declared_material_condition_id: Name | None = None
    declared_surface_condition_id: Name | None = None
    assumption_evidence: EvidenceRef

    @model_validator(mode="after")
    def assumption_origin(self):
        if self.assumption_evidence.origin not in {"synthetic", "user_declared"}:
            raise ValueError("geometry assumptions require synthetic or user_declared evidence")
        return self


def _number(cell, unit, expected_unit, scale=1.0):
    if cell is not None and cell["state"] in {"invalid_nonfinite", "unsupported_type"}:
        raise ValueError("invalid source indentation value is not an unknown observation")
    if cell is None or cell["state"] != "observed" or unit != expected_unit:
        return None
    value = cell["value"]
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("observed hardness dimensions, force and dwell must be finite and positive")
    return float(value) * scale


def evaluate_copper_indentation(test, row_id, review=None, *, client=None):
    """Always retain source-condition withholding; assumptions create a distinct diagnostic."""
    _verify(test, "pipeline_integrity.copper_hardness_test.v1")
    for row in test["rows"]:
        _verify(row, "pipeline_integrity.copper_indentation.v1")
    matches = [row for row in test["rows"] if row["row_id"] == row_id]
    if len(matches) != 1:
        raise ValueError("select one original indentation row ID")
    row, metadata, method = matches[0], test["metadata"], test["method"]
    if review is not None:
        payload = json_value(review.model_dump(mode="python"))
        review = CopperGeometryReview.model_validate_json(json.dumps(payload, allow_nan=False))
        if (review.expected_test_digest, review.expected_row_digest) != (
            test["record_digest"],
            row["record_digest"],
        ):
            raise ValueError("geometry review pins a different source test or row")

    def metanumber(key, unit, scale=1.0):
        cell = metadata.get(key)
        return _number(cell, None if cell is None else cell["unit"], unit, scale)

    unit = "mm" if method == "testing.brinell" else "µm"
    scale = 1e-3 if method == "testing.brinell" else 1e-6
    p = dict(test_force_N=metanumber("Prüfkraft F", "N"))
    if method == "testing.brinell":
        p.update(
            ball_diameter_m=metanumber("Kugeldurchmesser", "mm", 1e-3),
            mean_indentation_diameter_m=_number(
                row["cells"]["source_mean"], test["source_units"][3], unit, scale
            ),
        )
        geometry = (
            None
            if any(v is None for v in p.values())
            else p["mean_indentation_diameter_m"] < p["ball_diameter_m"]
        )
    else:
        p.update(
            first_diagonal_m=_number(row["cells"]["d1"], test["source_units"][1], unit, scale),
            second_diagonal_m=_number(row["cells"]["d2"], test["source_units"][2], unit, scale),
        )
        angle = metadata.get("Eindringwinkel")
        geometry = None if angle is None or angle["state"] != "observed" else angle["value"] == 136
    source_id = "copper_source:" + row["record_digest"]
    evidence = EvidenceRef(
        id=source_id,
        citation=test["provenance"]["citation"],
        origin="laboratory",
        locator=row_id,
        sha256=test["workbook_sha256"],
        license_id=test["provenance"]["license_id"],
        limitations=("source surface preparation NN and actual material condition unresolved",),
    )
    asset = "publisher_specimen_label:" + str(metadata.get("Probenbezeichnung", {}).get("value"))
    context = RunContext(
        asset_id=asset,
        scenario_id="source_conditions",
        clock_id="source_test_date_only:" + test["member"],
        origin="laboratory",
        evidence=(evidence,),
        metadata=dict(
            test_digest=test["record_digest"],
            row_digest=row["record_digest"],
            source_date=metadata.get("Versuchs-Datum"),
            nominal_material=metadata.get("Werkstoff"),
            actual_material_condition_unknown=True,
            surface_preparation=metadata.get("Probe Vorbereitung"),
            grouping_basis=test["grouping_basis"],
            elapsed_time_and_UTC_unknown=True,
        ),
    )
    reviewed = review is not None and all(
        (
            review.units_and_column_mapping_reviewed,
            review.force_dimensions_dwell_and_method_reviewed,
            review.geometry_model_reviewed,
            review.diagnostic_limitations_reviewed,
        )
    )
    procedure = metadata.get("Norm")
    request = HardnessRequest(
        method=method,
        asset_id=asset,
        location_id=row_id,
        clock_id=context.clock_id,
        material_condition_id=None,
        parameters=p,
        conditions=HardnessConditions(
            test_id=test["member"],
            specimen_id=asset,
            dwell_s=metanumber("Einwirkdauer der Prüfkraft", "s"),
            surface_condition_id=None,
            procedure_id=str(procedure["value"]) if procedure and procedure["state"] == "observed" else None,
            force_evidence_id=source_id,
            length_evidence_id=source_id,
            conditions_evidence_id=source_id,
            geometry_applicable=geometry,
        ),
        applicability_reviews=dict.fromkeys(REVIEWS[method], bool(reviewed)),
        evidence_ids=(source_id,),
    )
    original = evaluate_hardness(request, context, client=client)
    diagnostic = None
    if review is not None:
        if review.assumption_evidence.id == source_id:
            raise ValueError("assumption and source evidence must be distinct")
        assumed = request.model_copy(
            update=dict(
                material_condition_id=review.declared_material_condition_id,
                conditions=request.conditions.model_copy(
                    update=dict(
                        surface_condition_id=review.declared_surface_condition_id,
                        conditions_evidence_id=review.assumption_evidence.id,
                    )
                ),
                evidence_ids=(source_id, review.assumption_evidence.id),
            )
        )
        scenario = context.model_copy(
            update=dict(
                scenario_id="declared_assumptions_geometry_only",
                origin="simulation",
                evidence=(evidence, review.assumption_evidence),
                metadata=context.metadata | dict(assumptions_are_observations=False),
            )
        )
        diagnostic = evaluate_hardness(assumed, scenario, client=client)
    return _seal(
        dict(
            schema="pipeline_integrity.copper_hardness_application.v1",
            source_test_digest=test["record_digest"],
            source_row_digest=row["record_digest"],
            row_id=row_id,
            review=None if review is None else review.model_dump(mode="json"),
            source_condition_result=original,
            declared_assumptions_geometry_diagnostic=diagnostic,
            original_observation_modified=False,
            field_or_standard_qualification=False,
            reported_hardness_comparison_is_independent_validation=False,
        )
    )
