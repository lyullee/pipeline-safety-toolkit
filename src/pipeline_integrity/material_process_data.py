"""Pinned Charpy and electrochemical permeation records with explicit applicability gaps."""

from __future__ import annotations

import hashlib
import io
import math
import re
from datetime import date, datetime
from pathlib import Path

from .contracts import digest
from .datasets import catalog
from .source_archive import pinned_source_zip

BAM_ID = "bam_s355_charpy"
AHSS_ID = "ahss_hydrogen_permeation"
BAM_PROTOCOLS = (
    "01_primary_data/Charpy_protocol_KX_test_pieces.xlsx",
    "01_primary_data/Charpy_protocol_KY_test_pieces.xlsx",
)
BAM_SECONDARY = "02_secondary_data/Charpy_test_results.xlsx"
BAM_MISSION = "_steel_sheet_test_data_mission_abstract_content.pdf"
BAM_MISSION_REVIEW_SHA256 = "d1bc049e7ee2b35a12d9ea6806919b69fe2009ce8af1f8c2aa33961e19c7db42"
AHSS_FILENAME = "Permeation_MS AHSS 1300.xlsx"
AHSS_SHEETS = {"0.5 to 1 mA_cm2": (5.0, 10.0), "5 to 10 mA_cm2": (50.0, 100.0)}


def _seal(value):
    return dict(value, record_digest=digest(value))


def verify_material_process_data(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("material process source record or conditions changed")


def _numeric(value):
    if value is None or value == "":
        return dict(source_value=value, value=None, status="missing")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return dict(source_value=value, value=None, status="invalid")
    if not math.isfinite(value):
        return dict(source_value=str(value), value=None, status="nonfinite")
    return dict(source_value=value, value=float(value), status="finite")


def _cell(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _workbook(payload):
    # Reading source workbooks is optional; no workbook is authored or recalculated.
    from openpyxl import load_workbook

    if not isinstance(payload, bytes):
        raise TypeError("original workbook bytes are required")
    with io.BytesIO(payload) as stream:
        book = load_workbook(stream, data_only=False, read_only=False)
        try:
            return {
                sheet.title: dict(
                    source_cells=[[_cell(c.value) for c in row] for row in sheet.iter_rows()],
                    merged_cells=sorted(str(item) for item in sheet.merged_cells),
                )
                for sheet in book
            }
        finally:
            book.close()


def _expect(rows, row, column, expected):
    try:
        actual = rows[row - 1][column - 1]
    except IndexError as exc:
        raise ValueError("source workbook layout differs from the reviewed schema") from exc
    if actual != expected:
        raise ValueError(f"source workbook schema differs at row {row}, column {column}")


def parse_bam_protocol(payload, *, member):
    """Read original specimen measurement tables, retaining all layout cells."""
    if member not in BAM_PROTOCOLS:
        raise ValueError("one reviewed BAM primary protocol member is required")
    book = _workbook(payload)
    if list(book) != ["Tabelle1"]:
        raise ValueError("one reviewed BAM protocol worksheet is required")
    sheet = book["Tabelle1"]
    rows = sheet["source_cells"]
    if len(rows) != 53 or any(len(row) != 10 for row in rows):
        raise ValueError("BAM protocol row or column extent differs")
    direction = "rolling" if "KX_" in member else "transverse"
    group = "KX" if direction == "rolling" else "KY"
    for r, c, value in (
        (2, 1, "Probenbezeichnung:"),
        (2, 5, "Werkstoff:"),
        (2, 6, "S355"),
        (6, 3, "V-Kerbe"),
        (6, 6, "in Walzrichtung" if group == "KX" else "gegen Walzrichtung"),
        (8, 5, "Prüftemp.:"),
        (8, 6, "22,5 °C"),
        (48, 1, "Die Proben sind nicht gebrochen."),
    ):
        _expect(rows, r, c, value)
    headers = ("Probe", "Länge L", "Breite W", "Dicke B", "Steghöhe", "KV2", " LE ")
    units = (None, "in mm", "in mm", "in mm", "in mm", "in J", "in mm")
    if tuple(rows[38][:7]) != headers or tuple(rows[39][:7]) != units:
        raise ValueError("BAM measurement headers or units differ")
    specimens = []
    for offset, row in enumerate(rows[40:43], start=1):
        if row[0] != f"{group}{offset}" or rows[offset][2] != row[0]:
            raise ValueError("BAM primary specimen identities do not reconcile")
        cells = {
            key: _numeric(value)
            for key, value in zip(
                ("length_mm", "width_mm", "thickness_mm", "ligament_mm", "absorbed_energy_J"),
                row[1:6],
                strict=True,
            )
        }
        if row[6] != '="-"':
            raise ValueError("BAM missing lateral-expansion marker differs")
        si = {
            key.removesuffix("_mm") + "_m": None if cell["value"] is None else cell["value"] * 1e-3
            for key, cell in cells.items()
            if key.endswith("_mm")
        }
        specimens.append(
            dict(
                specimen_id=row[0],
                source_row=40 + offset,
                source_cells=row,
                direction=direction,
                numeric_cells=cells,
                **si,
                absorbed_energy_J=cells["absorbed_energy_J"]["value"],
                temperature_K=295.65,
                temperature_source="22,5 °C",
                broke=False,
                lateral_expansion_m=None,
                measured_composition_mass_fraction=None,
            )
        )
    return _seal(
        dict(
            member=member,
            member_sha256=hashlib.sha256(payload).hexdigest(),
            worksheet=sheet,
            specimens=specimens,
            observation_date=rows[9][5],
            temperature_conversion="22.5 + 273.15 K; source protocol F8",
        )
    )


def parse_bam_secondary(payload, *, primary_specimens):
    """Reconcile the rounded report without treating it as a second experiment."""
    book = _workbook(payload)
    if list(book) != ["Charpy_test_results"]:
        raise ValueError("reviewed BAM secondary worksheet is required")
    sheet = book["Charpy_test_results"]
    rows = sheet["source_cells"]
    expected = (
        "test piece",
        "crosspiece value / mm",
        "test piece length / mm",
        "test piece width / mm",
        "test piece thickness / mm",
        "impact energy KV2 750/5 / J",
    )
    if len(rows) != 32 or any(len(row) != 6 for row in rows) or tuple(rows[7]) != expected:
        raise ValueError("BAM secondary measurement headers, units or extent differ")
    _expect(rows, 4, 2, "steel S355J2 + N")
    by_id = {row["specimen_id"]: row for row in primary_specimens}
    if len(by_id) != 6 or [r[0] for r in rows[8:14]] != list(by_id):
        raise ValueError("BAM secondary identities or order differ from the primary protocols")
    comparisons = []
    for index, row in enumerate(rows[8:14], start=9):
        primary = by_id[row[0]]
        dims = (primary["ligament_m"], primary["length_m"], primary["width_m"], primary["thickness_m"])
        residuals = [
            None if a is None or _numeric(b)["value"] is None else a - b * 1e-3
            for a, b in zip(dims, row[1:5], strict=True)
        ]
        secondary_energy = _numeric(row[5])["value"]
        primary_energy = primary["absorbed_energy_J"]
        comparisons.append(
            dict(
                specimen_id=row[0],
                source_row=index,
                source_cells=row,
                dimension_residuals_m=residuals,
                secondary_absorbed_energy_J=secondary_energy,
                primary_minus_secondary_energy_J=(
                    None if None in (primary_energy, secondary_energy) else primary_energy - secondary_energy
                ),
                independent_observation=False,
            )
        )
    uncertainty_text = rows[20][0]
    uncertainty_match = re.search(r"uncertainty is ([0-9.]+) J\.", uncertainty_text or "")
    coverage_match = re.search(r"k = ([0-9.]+)", uncertainty_text or "")
    return _seal(
        dict(
            member=BAM_SECONDARY,
            member_sha256=hashlib.sha256(payload).hexdigest(),
            worksheet=sheet,
            comparisons=comparisons,
            independent_validation=False,
            reported_expanded_energy_uncertainty_J=(
                None if uncertainty_match is None else float(uncertainty_match[1])
            ),
            reported_coverage_factor=None if coverage_match is None else float(coverage_match[1]),
            uncertainty_meaning="reported expanded machine-evaluation uncertainty; not independent repeatability",
        )
    )


def _pinned_file(path, dataset_id):
    path = Path(path)
    found = [spec for spec in catalog() if spec.id == dataset_id]
    if len(found) != 1:
        raise ValueError("one reviewed source dataset is required")
    spec = found[0]
    assets = [asset for asset in spec.assets if asset.filename == path.name]
    if (
        spec.rights_review != "reviewed_license"
        or spec.acquisition != "allowed"
        or len(assets) != 1
        or assets[0].checksum is None
        or assets[0].bytes is None
        or assets[0].checksum_origin != "publisher_metadata"
    ):
        raise ValueError("source requires reviewed rights and publisher size/checksum")
    asset = assets[0]
    if asset.bytes > 100_000_000 or path.stat().st_size != asset.bytes:
        raise ValueError("source byte size or budget differs")
    raw = path.read_bytes()
    algorithm, expected = asset.checksum.split(":", 1)
    if hashlib.new(algorithm, raw).hexdigest() != expected:
        raise ValueError("source checksum differs from the reviewed publisher asset")
    return raw, dict(
        dataset_id=spec.id,
        filename=asset.filename,
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        supplier_checksum=asset.checksum,
        supplier_checksum_verified=True,
        checksum_origin=asset.checksum_origin,
        citation=spec.citation,
        creators=list(spec.creators),
        source_version=spec.version,
        license_id=spec.license_id,
        source_url=spec.source,
        rights_evidence_url=spec.rights_evidence_url,
    )


def read_bam_charpy(folder):
    folder = Path(folder)
    # The pinned mission document, section 2, prevents attaching a comparable batch's
    # manufacturer limits to these six measured specimens as their composition.
    support = []
    for filename in (
        BAM_MISSION,
        "Declaration_of_performance_S355_1.0577.pdf",
        "test_piece_extraction_sketch.pdf",
    ):
        _, provenance = _pinned_file(folder / filename, BAM_ID)
        if filename == BAM_MISSION and provenance["sha256"] != BAM_MISSION_REVIEW_SHA256:
            raise ValueError("BAM material-lot interpretation requires review of the pinned mission document")
        support.append(provenance)
    with pinned_source_zip(folder / "charpy_test.zip", BAM_ID) as (archive, provenance):
        protocols = [parse_bam_protocol(archive.read(member), member=member) for member in BAM_PROTOCOLS]
        specimens = [row for protocol in protocols for row in protocol["specimens"]]
        secondary = parse_bam_secondary(archive.read(BAM_SECONDARY), primary_specimens=specimens)
    return _seal(
        dict(
            schema_version="pipeline_integrity.material_process_source.v1",
            dataset_id=BAM_ID,
            provenance=provenance,
            supporting_assets=support,
            protocols=protocols,
            secondary=secondary,
            specimens=specimens,
            material_condition=dict(
                material_label="S355J2 + N",
                physical_sheets=1,
                measured_composition_mass_fraction=None,
                manufacturer_composition_tested_lot_match=None,
                manufacturer_composition_verified_for_tested_lot=False,
                composition_review_locator=f"{BAM_MISSION}, section 2, physical PDF page 4",
                manufacturer_limits_imported=False,
            ),
            applicability=dict(
                composition_reconcile="needs_data",
                pitting_composition_index="needs_data",
                impact_transition="needs_data",
                fitted_transition_parameters=None,
                reasons=[
                    "Tested-lot composition is not provided; the declaration concerns a comparable batch.",
                    "One test temperature and no broken specimens cannot identify a transition curve.",
                    "No declared transition lower/upper shelves, midpoint and width are provided.",
                ],
            ),
            summary=dict(
                physical_specimens=6,
                material_populations=1,
                temperatures=1,
                unbroken_specimens=6,
                secondary_rows=6,
                secondary_rows_are_independent=False,
            ),
        )
    )


def parse_ahss_permeation(payload):
    """Keep both complete current histories; specimen area and lattice boundary stay unknown."""
    book = _workbook(payload)
    if list(book) != list(AHSS_SHEETS):
        raise ValueError("AHSS workbook sheet names or order differ from the reviewed schema")
    histories = []
    for name, sheet in book.items():
        rows = sheet["source_cells"]
        if len(rows) < 4 or any(len(row) != 6 for row in rows):
            raise ValueError("AHSS worksheet extent differs from the reviewed six-column layout")
        for r, c, value in (
            (1, 1, "Time, s"),
            (1, 2, "Current, μA"),
            (1, 4, "Temperature"),
            (1, 6, "293 K"),
            (3, 4, "Material"),
            (3, 5, "MS AHSS grade 1300 "),
        ):
            _expect(rows, r, c, value)
        records = []
        for index, row in enumerate(rows[1:], start=2):
            t, current = _numeric(row[0]), _numeric(row[1])
            records.append(
                dict(
                    source_row=index,
                    source_cells=row,
                    time_cell=t,
                    current_cell=current,
                    time_s=t["value"],
                    current_A=None if current["value"] is None else current["value"] * 1e-6,
                    measured_flux_mol_m2_s=None,
                )
            )
        times = [r["time_s"] for r in records]
        intervals = [None if None in (a, b) else b - a for a, b in zip(times, times[1:], strict=False)]
        histories.append(
            dict(
                history_id=name,
                worksheet=sheet,
                rows=records,
                temperature_K=293.0,
                source_cathodic_current_density_endpoints_A_m2=list(AHSS_SHEETS[name]),
                continuous_entry_current_schedule=None,
                physical_specimen_id=None,
                time_origin_for_concentration_boundary=None,
                effective_permeation_area_m2=None,
                summary=dict(
                    rows=len(records),
                    repeated_adjacent_times=sum(dt == 0 for dt in intervals),
                    decreasing_adjacent_times=sum(dt is not None and dt < 0 for dt in intervals),
                    missing_or_invalid_numeric_cells=sum(
                        c["status"] != "finite" for r in records for c in (r["time_cell"], r["current_cell"])
                    ),
                ),
            )
        )
    return _seal(
        dict(
            histories=histories,
            source_rows_sorted_filtered_interpolated_baselined_or_rectified=False,
            summary=dict(
                histories=len(histories),
                rows=sum(h["summary"]["rows"] for h in histories),
                physical_specimens_resolved=None,
                histories_are_independent_specimens=False,
            ),
        )
    )


def read_ahss_permeation(path):
    raw, provenance = _pinned_file(path, AHSS_ID)
    observed = parse_ahss_permeation(raw)
    return _seal(
        dict(
            schema_version="pipeline_integrity.material_process_source.v1",
            dataset_id=AHSS_ID,
            provenance=provenance,
            observations=observed,
            conditions=dict(
                nominal_specimen_dimensions_m=[0.05, 0.05, 0.0012],
                thickness_m=0.0012,
                dimension_evidence="dataset reproduction method; nominal specimen, not exposed cell area",
                material_label="MS AHSS grade 1300",
                anodic_face="thin Pd layer",
                electrolyte="0.1 M NaOH",
                boundary_meaning="electrochemical cathodic current ramp",
                effective_permeation_area_m2=None,
                entry_lattice_concentration_mol_m3=None,
                lattice_diffusivity_m2_s=None,
                trap_parameters=None,
                surface_kinetic_coefficients=None,
                initial_lattice_concentration_mol_m3=None,
            ),
            applicability=dict(
                trap_equilibrium="needs_data",
                semiinfinite_diffusion="needs_data",
                slab_kinetic="needs_data",
                fitted_transport_parameters=None,
                reasons=[
                    "Cathodic current density does not specify an entry lattice concentration.",
                    "Total measured current lacks a reviewed active area and flux interpretation.",
                    "Lattice diffusivity, initial state, trap properties and kinetic coefficients are absent.",
                    "A finite 1.2 mm sample is not automatically a semi-infinite domain.",
                ],
            ),
        )
    )
