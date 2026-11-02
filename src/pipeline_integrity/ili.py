"""Read selected joints from open repeated ILI workbooks without assigning defect identity or elapsed time."""

import hashlib
import io
import math
import os
import stat
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field, ValidationError

from .contracts import Contract, Name, Nonnegative, digest
from .inspection import InspectionFeature, InspectionRun


class ILILoadRequest(Contract):
    run_id: Name
    asset_id: Name
    coordinate_frame_id: Name
    source_id: Name
    reported_time_label: Name
    joint_ids: tuple[str, ...] = Field(min_length=1)
    inspection_time_s: Nonnegative | None = None
    clock_id: Name | None = None
    time_basis_evidence_id: Name | None = None
    remaining_ligament_error_bound_m: Nonnegative | None = None
    maximum_source_rows: int | None = Field(default=None, ge=1)
    maximum_selected_features: int = Field(default=100000, ge=1)
    maximum_archive_bytes: int = Field(default=5000000000, ge=1)
    maximum_workbook_bytes: int = Field(default=1000000000, ge=1)


@contextmanager
def _pinned_archive(path: Path, maximum_bytes: int):
    if path.is_symlink():
        raise ValueError("the source archive must not be a symbolic link")
    with path.open("rb") as stream:
        initial = os.fstat(stream.fileno())
        identity = (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > maximum_bytes:
            raise ValueError("the source archive is not a bounded regular file")
        fingerprint = hashlib.sha256()
        for block in iter(lambda: stream.read(2**20), b""):
            fingerprint.update(block)
        archive_sha256 = fingerprint.hexdigest()
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            yield archive, archive_sha256, initial.st_size
        final = os.fstat(stream.fileno())
        try:
            current_path = path.stat(follow_symlinks=False)
        except OSError as error:
            raise ValueError("the source archive path changed during reading") from error
        final_identity = (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
        path_identity = (
            current_path.st_dev,
            current_path.st_ino,
            current_path.st_size,
            current_path.st_mtime_ns,
        )
        if final_identity != identity or path_identity != identity:
            raise ValueError("the source archive changed during hashing or workbook parsing")


def _number(value, column):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid_or_missing:{column}")
    return float(value)


def _identifier(value):
    value = _number(value, "GWNUM")
    if value != int(value):
        raise ValueError("GWNUM_must_be_integral")
    return str(int(value))


def ili_feature_from_row(row, request: ILILoadRequest, *, source_row: int):
    year = _number(row["Year"], "Year")
    if year != int(year) or str(int(year)) != request.reported_time_label:
        raise ValueError("inspection_time_label_mismatch")
    angle_deg = _number(row["SPO.deg"], "SPO.deg")
    if not 0 <= angle_deg <= 360:
        raise ValueError("circumferential_angle_outside_source_degree_range")
    return InspectionFeature(
        feature_id=f"{request.run_id}:row:{source_row}",
        joint_id=_identifier(row["GWNUM"]),
        joint_length_m=_number(row["JL.m"], "JL.m"),
        axial_position_m=_number(row["RD.m"], "RD.m"),
        circumferential_angle_rad=math.radians(angle_deg % 360),
        nominal_wall_m=_number(row["WT.mm"], "WT.mm") / 1000,
        maximum_depth_m=_number(row["MD.mm"], "MD.mm") / 1000,
        length_m=None if row["L.mm"] is None else _number(row["L.mm"], "L.mm") / 1000,
        width_m=None if row["W.mm"] is None else _number(row["W.mm"], "W.mm") / 1000,
        remaining_ligament_error_bound_m=request.remaining_ligament_error_bound_m,
        source_row=source_row,
    )


def load_ili_run(archive_path, member: str, request: ILILoadRequest, *, on_progress=None):
    """Scan a workbook completely by default, retaining selected features and explicit exclusions."""
    import openpyxl

    if not isinstance(request, ILILoadRequest):
        raise TypeError("ILILoadRequest is required")
    if len(set(request.joint_ids)) != len(request.joint_ids):
        raise ValueError("selected joint identifiers must be unique")
    path = Path(archive_path)
    features, invalid, unknown_joints, scanned, complete, nonpositive_ligaments = [], [], 0, 0, True, 0
    selected_rows = 0
    selected_source_row_numbers = []
    years, joints = Counter(), Counter()
    required = {"Year", "GWNUM", "JL.m", "RD.m", "SPO.deg", "WT.mm", "MD.mm", "L.mm", "W.mm"}
    with _pinned_archive(path, request.maximum_archive_bytes) as (archive, archive_sha256, archive_bytes):
        info = archive.getinfo(member)
        if info.file_size > request.maximum_workbook_bytes or not member.lower().endswith(".xlsx"):
            raise ValueError("the requested workbook is outside its declared format or byte budget")
        workbook_bytes = archive.read(member)
        workbook_sha256 = hashlib.sha256(workbook_bytes).hexdigest()
        workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=True)
        try:
            if len(workbook.worksheets) != 1:
                raise ValueError("this dataset reader requires one reviewed source sheet")
            sheet = workbook.active
            reported_extent = {"rows": sheet.max_row, "columns": sheet.max_column}
            sheet.reset_dimensions()
            header_rows = sheet.iter_rows(max_row=1, values_only=True)
            headers = next(header_rows, ())
            header_rows.close()
            if len(set(headers)) != len(headers) or not required <= set(headers):
                raise ValueError("source columns differ from the reviewed ILI dataset")
            rows = sheet.iter_rows(min_row=2, max_col=len(headers), values_only=True)
            for row_index, values in enumerate(rows, 2):
                if request.maximum_source_rows is not None and scanned >= request.maximum_source_rows:
                    complete = False
                    break
                scanned += 1
                row = dict(zip(headers, values, strict=True))
                years[str(row.get("Year"))] += 1
                try:
                    joint = _identifier(row.get("GWNUM"))
                except ValueError:
                    unknown_joints += 1
                    continue
                joints[joint] += 1
                if joint in request.joint_ids:
                    selected_rows += 1
                    selected_source_row_numbers.append(row_index)
                    if selected_rows > request.maximum_selected_features:
                        raise ValueError("selected source row count exceeds the declared budget")
                    try:
                        feature = ili_feature_from_row(row, request, source_row=row_index)
                    except ValueError as error:
                        reason = (
                            ";".join(f"{e['loc']}:{e['type']}" for e in error.errors(include_input=False))
                            if isinstance(error, ValidationError)
                            else str(error)
                        )
                        invalid.append({"source_row": row_index, "reason": reason})
                        continue
                    if len(features) >= request.maximum_selected_features:
                        raise ValueError("selected feature count exceeds the declared budget")
                    features.append(feature)
                    nonpositive_ligaments += feature.nominal_ligament_m <= 0
                if on_progress is not None and scanned % 50000 == 0:
                    on_progress(
                        {
                            "run_id": request.run_id,
                            "source_rows_scanned": scanned,
                            "selected_features": len(features),
                        }
                    )
        finally:
            workbook.close()
    run = InspectionRun(
        run_id=request.run_id,
        asset_id=request.asset_id,
        coordinate_frame_id=request.coordinate_frame_id,
        source_id=request.source_id,
        source_sha256=archive_sha256,
        reported_time_label=request.reported_time_label,
        inspection_time_s=request.inspection_time_s,
        clock_id=request.clock_id,
        time_basis_evidence_id=request.time_basis_evidence_id,
        features=tuple(features),
        origin="measured",
        source_scan_complete=complete,
        excluded_selected_source_rows=len(invalid),
    )
    report = {
        "schema": "pipeline_integrity.ili_load.v1",
        "run_id": run.run_id,
        "member": member,
        "archive_sha256": archive_sha256,
        "archive_bytes": archive_bytes,
        "maximum_archive_bytes": request.maximum_archive_bytes,
        "workbook_sha256": workbook_sha256,
        "workbook_bytes": info.file_size,
        "maximum_workbook_bytes": request.maximum_workbook_bytes,
        "actual_source_rows_scanned": scanned,
        "reported_worksheet_extent": reported_extent,
        "reader_uses_xml_end_instead_of_reported_row_extent": True,
        "source_scan_complete": complete,
        "scan_stop_reason": (
            "maximum_source_rows_reached"
            if not complete and request.maximum_source_rows is not None and scanned >= request.maximum_source_rows
            else None
        ),
        "maximum_source_rows": request.maximum_source_rows,
        "source_year_labels": dict(years),
        "distinct_joint_count": len(joints),
        "selected_joint_row_counts": {joint: joints[joint] for joint in request.joint_ids},
        "selected_source_rows": selected_rows,
        "selected_source_row_numbers": selected_source_row_numbers,
        "selected_feature_source_rows": [feature.source_row for feature in run.features],
        "selected_source_row_digest": digest(selected_source_row_numbers),
        "selected_features": len(features),
        "invalid_selected_rows": invalid,
        "source_rows_without_joint_id": unknown_joints,
        "selected_nonpositive_nominal_ligaments": nonpositive_ligaments,
        "source_time_labels_are_not_calendar_timestamps": True,
        "absolute_distance_and_unclear_significant_point_coordinate_not_used": True,
        "elapsed_time_or_measurement_error_inferred": False,
    }
    report["report_digest"] = digest(report)
    return run, report


def verify_ili_load_record(run, report, request: ILILoadRequest, *, archive_path=None):
    """Check row-level consistency and optionally replay the exact source archive/member."""
    try:
        if not isinstance(request, ILILoadRequest) or not isinstance(run, InspectionRun):
            raise ValueError("ILILoadRequest and InspectionRun are required")
        if not isinstance(report, dict) or report.get("schema") != "pipeline_integrity.ili_load.v1":
            raise ValueError("unsupported ILI load report")
        if report.get("report_digest") != digest({k: v for k, v in report.items() if k != "report_digest"}):
            raise ValueError("ILI report digest mismatch")
        if (
            run.run_id != request.run_id
            or run.asset_id != request.asset_id
            or run.source_id != request.source_id
            or run.reported_time_label != request.reported_time_label
            or run.source_sha256 != report.get("archive_sha256")
            or run.source_scan_complete is not report.get("source_scan_complete")
        ):
            raise ValueError("ILI run identity differs from its request or report")
        row_numbers = report.get("selected_source_row_numbers")
        feature_rows = report.get("selected_feature_source_rows")
        invalid_rows = [item.get("source_row") for item in report.get("invalid_selected_rows", [])]
        if (
            not isinstance(row_numbers, list)
            or any(type(row) is not int or row < 2 for row in row_numbers)
            or row_numbers != sorted(set(row_numbers))
            or report.get("selected_source_row_digest") != digest(row_numbers)
            or report.get("selected_source_rows") != len(row_numbers)
            or not isinstance(feature_rows, list)
            or feature_rows != [feature.source_row for feature in run.features]
            or set(feature_rows) & set(invalid_rows)
            or set(feature_rows) | set(invalid_rows) != set(row_numbers)
            or report.get("selected_features") != len(run.features)
            or run.excluded_selected_source_rows != len(invalid_rows)
        ):
            raise ValueError("ILI selected-row manifest differs from the returned InspectionRun")
        if report.get("source_scan_complete"):
            if report.get("scan_stop_reason") is not None:
                raise ValueError("complete ILI source scan cannot have an early-stop reason")
        elif (
            report.get("scan_stop_reason") != "maximum_source_rows_reached"
            or request.maximum_source_rows is None
            or report.get("actual_source_rows_scanned") != request.maximum_source_rows
        ):
            raise ValueError("incomplete ILI source scan lacks a matching row-budget stop")
        if report.get("archive_bytes", 0) > request.maximum_archive_bytes:
            raise ValueError("ILI archive exceeded the request byte budget")
        if report.get("workbook_bytes", 0) > request.maximum_workbook_bytes:
            raise ValueError("ILI workbook exceeded the request byte budget")
        if archive_path is not None:
            replay_run, replay_report = load_ili_run(archive_path, report.get("member"), request)
            if replay_run != run or replay_report != report:
                raise ValueError("source archive replay differs from the saved ILI run/report")
        return {"valid": True, "source_replayed": archive_path is not None}
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile, ImportError) as error:
        return {"valid": False, "reason": str(error), "source_replayed": False}
