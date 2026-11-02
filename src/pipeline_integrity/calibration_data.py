"""Pinned calibration inputs with full publisher covariance and source origin.

The supplied intermediate values summarize a publisher Monte Carlo propagation
of laboratory measurements. They are neither independent campaigns nor raw
thermodynamic observations. Supplier programs are never executed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import warnings
import zipfile
from pathlib import Path

from .contracts import digest
from .soil_data import _bounded_xlsx, _numeric, _pinned, _scalar

EMUE_ID = "emue_sonic_nozzle_calibration"
ARCHIVE_NAME = "EMUE-D4-3-SonicNozzles.zip"
SCHEMA = "pipeline_integrity.calibration_source.v1"
METHOD_FILES = ("Example_A223.pdf", "Example_A223.Rmd", "Example_A223.tex")
CSV_FIELDS = ("mean.Q", "SU.Q", "mean.Psi", "SU.Psi")


def _seal(body):
    return dict(body, record_digest=digest(body))


def _number(token):
    try:
        number = float(token)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _csv_observations(payload):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 100000:
        raise ValueError("bounded original calibration CSV required")
    text = payload.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows or tuple(rows[0]) != CSV_FIELDS or len(rows) != 31:
        raise ValueError("reviewed primitive CSV header/order or thirty-point extent differs")
    observations = []
    for index, row in enumerate(rows[1:]):
        if len(row) != 4:
            raise ValueError("primitive CSV row width differs")
        values = [_number(v) for v in row]
        valid = all(v is not None for v in values) and (
            values[0] > 0 and values[2] > 0 and values[1] >= 0 and values[3] >= 0
        )
        observations.append(
            dict(
                point_id=f"publisher-point-{index + 1}",
                source_row=index + 2,
                source_tokens=row,
                mass_flow_kg_s=values[0],
                mass_flow_type_A_standard_uncertainty_kg_s=values[1],
                critical_mass_flux_kg_m2_s=values[2],
                critical_mass_flux_type_A_standard_uncertainty_kg_m2_s=values[3],
                numeric_domain_valid=valid,
                origin="publisher_laboratory_repeat_estimates",
                type_A_is_total_uncertainty=False,
                coverage_factor=None,
                degrees_of_freedom=None,
            )
        )
    return dict(
        source_text=text,
        source_fields=list(CSV_FIELDS),
        observations=observations,
        source_units={"mean.Q": "kg/s", "SU.Q": "kg/s", "mean.Psi": "kg/(m2*s)", "SU.Psi": "kg/(m2*s)"},
        uncertainty_kind="Type_A_standard_components_not_total",
        observation_time_s=None,
        upstream_pressure_pa_abs=None,
        upstream_temperature_K=None,
        gas_mole_fractions=None,
    )


def _source_workbook(payload):
    _bounded_xlsx(payload)
    from openpyxl import load_workbook

    # This source writes A1:A1 for every sheet. Read its actual XML extent;
    # never change the workbook bytes or infer absent observations from sizes.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
    try:
        if tuple(book.sheetnames) != ("Data", "Var_x", "Var_y", "Var_xy"):
            raise ValueError("reviewed calibration worksheet identities/order differ")
        sheets = {}
        for sheet in book:
            declared = sheet.calculate_dimension()
            sheet.reset_dimensions()
            actual = sheet.calculate_dimension(force=True)
            if sheet.max_row != 31 or sheet.max_column != (2 if sheet.title == "Data" else 30):
                raise ValueError("calibration workbook actual XML extent differs")
            rows = []
            for row in sheet:
                if any(cell.data_type == "f" for cell in row):
                    raise ValueError("reviewed calibration matrix source has no formulas")
                rows.append([_scalar(cell.value) for cell in row])
            sheets[sheet.title] = dict(
                declared_dimension=declared,
                observed_dimension=actual,
                declared_dimension_matches_observed=declared == actual,
                source_cells=rows,
                source_bytes_changed=False,
            )
        return sheets, [str(item.message) for item in caught]
    finally:
        book.close()


def _intermediate(sheets):
    data = sheets["Data"]["source_cells"]
    if data[0] != ["x", "y"]:
        raise ValueError("intermediate axis order differs")
    matrices = {}
    for name in ("Var_x", "Var_y", "Var_xy"):
        rows = sheets[name]["source_cells"]
        if rows[0] != [f"V{i}" for i in range(1, 31)]:
            raise ValueError("source covariance point order differs")
        matrices[name] = [[_numeric(v) for v in row] for row in rows[1:]]
    x, y = ([_numeric(row[column]) for row in data[1:]] for column in (0, 1))
    vx, vy, vxy = (matrices[name] for name in ("Var_x", "Var_y", "Var_xy"))
    covariance = [vx[i] + vxy[i] for i in range(30)] + [
        [vxy[j][i] for j in range(30)] + vy[i] for i in range(30)
    ]
    complete = all(v is not None for row in covariance for v in row)
    symmetric = complete and all(covariance[i][j] == covariance[j][i] for i in range(60) for j in range(60))
    positive_diagonal = complete and all(covariance[i][i] > 0 for i in range(60))
    values_valid = all(v is not None and v > 0 for v in x + y)
    return dict(
        x=x,
        y=y,
        point_ids=[f"publisher-point-{i}" for i in range(1, 31)],
        source_blocks=matrices,
        standard_joint_covariance=covariance,
        covariance_order=[f"x:{i}" for i in range(1, 31)] + [f"y:{i}" for i in range(1, 31)],
        covariance_unit="1",
        x_unit="1",
        y_unit="1",
        x_quantity="inverse_square_root_reynolds_number",
        y_quantity="discharge_coefficient",
        source_cross_block_orientation="Cov(X_i,Y_j)",
        source_origin="publisher_Monte_Carlo_propagation_of_laboratory_estimates",
        publisher_MC_trials=100000000,
        publisher_MC_samples_acquired=False,
        MC_rerun=False,
        covariance_complete=complete,
        covariance_exactly_symmetric=symmetric,
        covariance_positive_diagonal=positive_diagonal,
        values_numeric_domain_valid=values_valid,
        offdiagonal_nonzero_entries=None
        if not complete
        else sum(covariance[i][j] != 0 for i in range(60) for j in range(60) if i != j),
        covariance_repaired=False,
        covariance_diagonalized=False,
        independent_validation=None,
        observation_time_s=None,
        split_unit="whole_single_thirty_point_campaign_with_shared_input_covariance",
    )


def parse_emue_archive(payload):
    """Parse only the bounded data members; preserve method/source member hashes."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 2000000:
        raise ValueError("nonempty original calibration ZIP within 2 MB required")
    _bounded_xlsx(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) > 40 or sum(m.file_size for m in members) > 8000000:
            raise ValueError("calibration archive member/uncompressed budget")
        required = ("data_A223.csv", "MC_data_A223.xlsx", *METHOD_FILES)
        if not set(required) <= set(archive.namelist()):
            raise ValueError("calibration data and reviewed supplier method files required")
        selected = {name: archive.read(name) for name in required}
        member_provenance = {
            name: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            for name, raw in selected.items()
        }
        inventory = {m.filename: dict(bytes=m.file_size, compressed_bytes=m.compress_size) for m in members}
    primitive = _csv_observations(selected["data_A223.csv"])
    sheets, warnings_seen = _source_workbook(selected["MC_data_A223.xlsx"])
    intermediate = _intermediate(sheets)
    resolved = all(x["numeric_domain_valid"] for x in primitive["observations"]) and all(
        intermediate[key]
        for key in (
            "covariance_complete",
            "covariance_exactly_symmetric",
            "covariance_positive_diagonal",
            "values_numeric_domain_valid",
        )
    )
    return _seal(
        dict(
            schema=SCHEMA,
            kind="emue_sonic_nozzle_calibration",
            status="source_values_available" if resolved else "needs_data",
            origin="mixed_laboratory_and_source_Monte_Carlo_summary",
            primitive=primitive,
            intermediate=intermediate,
            worksheets=sheets,
            supplier_reader_warnings=warnings_seen,
            selected_members=member_provenance,
            archive_inventory=inventory,
            claims=dict(
                independent_validation=False,
                field_calibration=False,
                thermodynamic_model_recomputed=False,
                primitive_Type_A_is_total_uncertainty=False,
                source_code_executed=False,
            ),
            limitations=[
                "Source Monte Carlo intermediate estimates are not raw independent sensor/reference readings",
                "Complete source covariance includes shared-input cross-point and cross-axis correlations",
                "Only one selected thirty-point campaign; no independent held-out run",
                "No supplied observation time or complete gas state",
                "Source viscosity-table unit is unresolved and not used for recomputation",
            ],
        )
    )


def read_emue_calibration(path, *, source_spec=None):
    raw, provenance = _pinned(path, EMUE_ID, source_spec=source_spec)
    if provenance["dataset_origin"] != "mixed_compilation":
        raise ValueError("calibration source retains mixed laboratory/MC-summary origin")
    body = {k: v for k, v in parse_emue_archive(raw).items() if k != "record_digest"}
    return _seal(dict(body, provenance=provenance))


def verify_calibration_source_record(record, *, source_root, source_spec=None):
    """Re-read the pinned original ZIP; rehashed source meaning changes are refused."""
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("calibration source record digest differs")
    expected = read_emue_calibration(Path(source_root) / EMUE_ID / ARCHIVE_NAME, source_spec=source_spec)
    if record != expected:
        raise ValueError("calibration source origin, units, covariance, point order or data differ")
    return True


def emue_calibration_inputs(record, *, source_root, source_spec=None):
    """Return the exact full-covariance inputs; native applicability remains explicit."""
    verify_calibration_source_record(record, source_root=source_root, source_spec=source_spec)
    if record["status"] != "source_values_available":
        raise ValueError("resolved source values and full covariance required")
    item = record["intermediate"]
    return dict(
        point_ids=tuple(item["point_ids"]),
        x=tuple(item["x"]),
        y=tuple(item["y"]),
        standard_joint_covariance=tuple(tuple(row) for row in item["standard_joint_covariance"]),
        covariance_order=tuple(item["covariance_order"]),
        x_unit="1",
        y_unit="1",
        x_quantity=item["x_quantity"],
        y_quantity=item["y_quantity"],
        source_record_digest=record["record_digest"],
        source_asset_sha256=record["provenance"]["sha256"],
        source_citation=record["provenance"]["citation"],
        source_url=record["provenance"]["source_url"],
        source_license_id=record["provenance"]["license_id"],
        source_member_sha256=record["selected_members"]["MC_data_A223.xlsx"]["sha256"],
        source_origin=item["source_origin"],
        independent_validation=None,
        observation_time_s=None,
        split_unit=item["split_unit"],
        source_uncertainty_scale="standard_covariance_not_expanded",
    )
