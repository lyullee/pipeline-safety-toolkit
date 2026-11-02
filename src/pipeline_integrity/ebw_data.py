"""Read the pinned EBW thermal release without replacing missing observations or source labels."""

import hashlib
import io
import math
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .contracts import digest
from .datasets import file_digest

PUBLISHER_SHA256 = "329063f7b187e7df6a245f7fb868972d6a24b7f9ad6fdffc336a270dc2e6be5b"
PUBLISHER_BYTES = 368314
PUBLISHER_FILENAME = "EBW_ThermalCycle_CoolingTime_Data_HSSS.zip"
MAX_DOWNLOAD_CONTAINER_BYTES = 2_000_000
DOI = "10.17632/c7d9mt9h9x.1"
TIME_HEADER = "Time  1 - default sample rate [s]"
TEMPERATURE_HEADER = re.compile(
    r"Temperature \(c\)\s*; thermocouple distance from weld centre = ([0-9]+(?:\.[0-9]+)?) mm"
)


def _seal(body):
    return {**body, "record_digest": digest(body)}


def _check_seal(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("EBW source record digest mismatch")


def _safe_zip(archive, *, total_limit, member_limit, maximum_members):
    members = archive.infolist()
    if len(members) > maximum_members or sum(m.file_size for m in members) > total_limit:
        raise ValueError("EBW ZIP member count or uncompressed size budget exceeded")
    seen = set()
    for member in members:
        path = PurePosixPath(member.filename)
        mode = member.external_attr >> 16
        if (
            not member.filename
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in member.filename
            or ":" in member.filename
            or member.filename in seen
            or stat.S_ISLNK(mode)
            or member.flag_bits & 1
            or member.file_size > member_limit
        ):
            raise ValueError("unsafe, duplicated, encrypted or oversized EBW ZIP member")
        seen.add(member.filename)
    return members


def _identity(info):
    return (
        info.st_dev,
        stat.S_IFMT(info.st_mode),
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    )


def _pinned_release(path):
    """Read either the pinned publisher ZIP or one bounded, single-member download wrapper."""
    path = Path(path)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError("EBW source must be a regular non-symlink file")
    if not 0 < before.st_size <= MAX_DOWNLOAD_CONTAINER_BYTES:
        raise ValueError("EBW source container exceeds its bounded input size")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(before):
            raise ValueError("EBW source changed between path inspection and open")
        payload = stream.read(MAX_DOWNLOAD_CONTAINER_BYTES + 1)
        closed = os.fstat(stream.fileno())
    after = path.stat(follow_symlinks=False)
    if len(payload) > MAX_DOWNLOAD_CONTAINER_BYTES or any(
        _identity(item) != _identity(before) for item in (opened, closed, after)
    ):
        raise ValueError("EBW source identity or bounded input size changed while reading")
    wrapper_digest = hashlib.sha256(payload).hexdigest()
    if len(payload) == PUBLISHER_BYTES and wrapper_digest == PUBLISHER_SHA256:
        return payload, None
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as wrapper:
            members = _safe_zip(
                wrapper,
                total_limit=PUBLISHER_BYTES,
                member_limit=PUBLISHER_BYTES,
                maximum_members=1,
            )
            if len(members) != 1:
                raise ValueError("EBW download wrapper must contain exactly one pinned publisher ZIP")
            member = members[0]
            mode = member.external_attr >> 16
            member_type = stat.S_IFMT(mode)
            if (
                member.is_dir()
                or PurePosixPath(member.filename).name != PUBLISHER_FILENAME
                or member.file_size != PUBLISHER_BYTES
                or member_type not in (0, stat.S_IFREG)
            ):
                raise ValueError("EBW download wrapper member is not the expected regular publisher ZIP")
            publisher = wrapper.read(member)
    except zipfile.BadZipFile as error:
        raise ValueError("EBW publisher size/SHA-256 mismatch or invalid download wrapper") from error
    if len(publisher) != PUBLISHER_BYTES or hashlib.sha256(publisher).hexdigest() != PUBLISHER_SHA256:
        raise ValueError("EBW inner publisher ZIP size/SHA-256 mismatch")
    container = {
        "kind": "single_member_download_wrapper",
        "wrapper_filename": path.name,
        "wrapper_bytes": len(payload),
        "wrapper_sha256": wrapper_digest,
        "member_path": member.filename,
        "member_compressed_bytes": member.compress_size,
        "member_publisher_bytes": member.file_size,
        "member_crc32": f"{member.CRC:08x}",
        "member_crc32_verified_by_zip_reader": True,
        "publisher_asset_sha256": hashlib.sha256(publisher).hexdigest(),
        "publisher_sha256_verified": True,
    }
    return publisher, container


def _number(value):
    if value is None or value == "":
        return None, "missing"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "invalid"
    return (float(value), "observed") if math.isfinite(value) else (None, "nonfinite")


def _summary(samples):
    known = [s for s in samples if s["temperature_k"] is not None]
    peak = max((s["temperature_k"] for s in known), default=None)
    peak_samples = [s for s in known if s["temperature_k"] == peak]
    times = [s["time_s"] for s in samples]
    complete_clock = all(t is not None for t in times)
    intervals = [b - a for a, b in zip(times, times[1:], strict=False)] if complete_clock else []
    return dict(
        sample_count=len(samples),
        missing_or_invalid_time_count=sum(s["time_state"] != "observed" for s in samples),
        missing_or_invalid_temperature_count=sum(s["temperature_state"] != "observed" for s in samples),
        first_time_s=times[0],
        last_time_s=times[-1],
        strictly_increasing_time=all(x > 0 for x in intervals) if complete_clock else None,
        minimum_sample_interval_s=min(intervals, default=None),
        maximum_sample_interval_s=max(intervals, default=None),
        minimum_temperature_k=min((s["temperature_k"] for s in known), default=None),
        observed_peak_temperature_k=peak,
        observed_peak_source_rows=[s["source_row"] for s in peak_samples],
        observed_peak_time_s=[s["time_s"] for s in peak_samples],
    )


def load_ebw_thermal(archive):
    """Verify publisher SHA-256 before opening XLSX and retain all original sensor rows.

    Accepts the pinned publisher ZIP directly or a small single-member download wrapper
    containing exactly that named publisher asset. No member is extracted to disk.
    """
    archive_bytes, download_container = _pinned_release(archive)
    import openpyxl

    records, experiments = [], []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as release:
        members = _safe_zip(release, total_limit=8_000_000, member_limit=2_000_000, maximum_members=10)
        if not members or any(
            m.is_dir() or "/" in m.filename or not m.filename.endswith(".xlsx") for m in members
        ):
            raise ValueError("the EBW release must contain only top-level XLSX workbooks")
        for index, member in enumerate(members, start=1):
            workbook_bytes = release.read(member)
            with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as package:
                _safe_zip(package, total_limit=20_000_000, member_limit=10_000_000, maximum_members=100)
            workbook = openpyxl.load_workbook(
                io.BytesIO(workbook_bytes), read_only=True, data_only=True, keep_links=False
            )
            try:
                if len(workbook.worksheets) != 1:
                    raise ValueError("each EBW workbook must identify one physical experiment sheet")
                sheet = workbook.worksheets[0]
                if sheet.max_column not in (2, 3) or not 3 <= sheet.max_row <= 100_001:
                    raise ValueError("unexpected EBW worksheet dimensions")
                rows = list(sheet.iter_rows(values_only=True))
                header = rows[0]
                matches = [
                    TEMPERATURE_HEADER.fullmatch(h) if isinstance(h, str) else None for h in header[1:]
                ]
                if header[0] != TIME_HEADER or not all(matches):
                    raise ValueError("unrecognized EBW time, Celsius or sensor-position source header")
                experiment_id = f"ebw_experiment_{index}"
                filename_material, filename_process, *_ = member.filename.split("_")
                sheet_material = sheet.title.split("_", 1)[0]
                labels = [
                    dict(peak_temperature_c=float(peak), t85_s=float(interval))
                    for peak, interval in re.findall(
                        r"_([0-9]+(?:\.[0-9]+)?)_([0-9]+(?:\.[0-9]+)?)s(?=_|\.xlsx$)",
                        member.filename,
                    )
                ]
                experiment = dict(
                    id=experiment_id,
                    source_filename=member.filename,
                    source_workbook_sha256=hashlib.sha256(workbook_bytes).hexdigest(),
                    source_sheet=sheet.title,
                    nominal_material_from_filename=filename_material,
                    nominal_material_from_sheet=sheet_material,
                    nominal_material_labels_disagree=filename_material != sheet_material,
                    process_label_from_filename=filename_process,
                    source_headers=list(header),
                    source_time_row_count=len(rows) - 1,
                    sensor_ids=[],
                    filename_reported_labels=labels,
                    filename_labels_are_observations=False,
                    filename_label_sensor_association_established=False,
                )
                for column, match in enumerate(matches, start=1):
                    sensor_id = f"{experiment_id}:sensor_{column}"
                    experiment["sensor_ids"].append(sensor_id)
                    samples = []
                    for source_row, row in enumerate(rows[1:], start=2):
                        time, time_state = _number(row[0])
                        temperature, state = _number(row[column])
                        kelvin = None if temperature is None else temperature + 273.15
                        if time is not None and time < 0:
                            time_state = "negative"
                        if kelvin is not None and kelvin <= 0:
                            state = "nonphysical"
                        samples.append(
                            dict(
                                source_row=source_row,
                                time_s=time,
                                temperature_c=temperature,
                                temperature_k=kelvin,
                                time_state=time_state,
                                temperature_state=state,
                            )
                        )
                    body = dict(
                        schema="pipeline_integrity.ebw_thermal_sensor.v1",
                        id=sensor_id,
                        experiment_id=experiment_id,
                        clock_id=f"{experiment_id}:released_elapsed_s:{PUBLISHER_SHA256}",
                        source_archive_sha256=PUBLISHER_SHA256,
                        source_filename=member.filename,
                        source_workbook_sha256=experiment["source_workbook_sha256"],
                        source_sheet=sheet.title,
                        source_column=column + 1,
                        source_temperature_header=header[column],
                        sensor_distance_from_weld_centre_m=float(match.group(1)) / 1000,
                        sensor_distance_evidence="source_temperature_header",
                        nominal_material_from_filename=filename_material,
                        nominal_material_from_sheet=sheet_material,
                        origin="laboratory",
                        temperature_conversion="temperature_K = source_temperature_C + 273.15",
                        source_rows_reordered_filled_or_smoothed=False,
                        summary=_summary(samples),
                        samples=samples,
                    )
                    records.append(_seal(body))
                experiments.append(experiment)
            finally:
                workbook.close()
    body = dict(
        schema="pipeline_integrity.ebw_thermal_source.v1",
        dataset_id="ebw_thermal",
        doi=DOI,
        source="https://doi.org/" + DOI,
        citation=(
            "Sisodia, Raghawendra Pratap Singh; Śliwiński, Piotr; Weglowski, Marek St. (2025), "
            '"Dataset on thermal cycles and cooling times during electron beam welding of high-strength '
            'structural Steels", Mendeley Data, V1, doi: 10.17632/c7d9mt9h9x.1'
        ),
        license_id="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        source_filename=PUBLISHER_FILENAME,
        download_container=download_container,
        source_sha256=PUBLISHER_SHA256,
        source_bytes=PUBLISHER_BYTES,
        supplier_checksum="sha256:" + PUBLISHER_SHA256,
        publisher_checksum_verified_before_xlsx_open=True,
        checksum_metadata_url="https://data.mendeley.com/public-api/datasets/c7d9mt9h9x/versions/1/files",
        identification_metadata_url="https://api.datacite.org/dois/10.17632/c7d9mt9h9x.1",
        reported_instrument_metadata=dict(
            sensor_type="K-type thermocouple",
            acquisition_system="MGC Plus AB22A",
            acquisition_software="Catman Easy V3.4.2",
            evidence="DataCite abstract for the source DOI",
            channel_calibration_or_uncertainty_established=False,
        ),
        physical_experiment_count=len(experiments),
        sensor_history_count=len(records),
        physical_time_rows=sum(x["source_time_row_count"] for x in experiments),
        sensor_samples=sum(len(r["samples"]) for r in records),
        common_absolute_clock_across_experiments_established=False,
        missing_values_imputed=False,
        procedure_or_field_qualification=False,
        experiments=experiments,
        records=records,
        implementation_sha256=file_digest(Path(__file__)),
    )
    return _seal(body)


def verify_ebw_thermal_source(record, archive_path):
    """Replay the pinned source reader and compare the complete experiment/sensor receipt."""
    try:
        _check_seal(record)
        if record.get("schema") != "pipeline_integrity.ebw_thermal_source.v1":
            raise ValueError("an EBW thermal source record is required")
        replayed = load_ebw_thermal(archive_path)
        if replayed != record:
            raise ValueError("EBW archive replay differs from the sealed source record")
        return {"valid": True, "source_replayed": True, "record_digest": record["record_digest"]}
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile, ImportError) as error:
        return {"valid": False, "source_replayed": False, "reason": str(error)}


def select_ebw_cooling_branch(record, *, upper_temperature_k=1073.15, lower_temperature_k=773.15):
    """Select the unique contiguous post-peak crossing bracket, never deleting a reheating sample.

    Nonincreasing temperature is exact at released precision. Repeated crossing groups,
    a disjoint maximum, missing values, a bad clock or any increase inside the bracket
    produces needs_data. The complete sensor record remains the authoritative history.
    """
    _check_seal(record)
    if record.get("schema") != "pipeline_integrity.ebw_thermal_sensor.v1":
        raise ValueError("a sealed EBW sensor history record is required")
    if (
        any(
            isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0
            for t in (upper_temperature_k, lower_temperature_k)
        )
        or lower_temperature_k >= upper_temperature_k
    ):
        raise ValueError("finite positive ordered cooling target temperatures are required")
    body = dict(
        schema="pipeline_integrity.ebw_cooling_selection.v1",
        source_record_id=record["id"],
        source_record_digest=record["record_digest"],
        status="needs_data",
        issues=[],
        rule="unique_contiguous_post_peak_crossing_bracket_exact_nonincreasing_v1",
        upper_temperature_k=upper_temperature_k,
        lower_temperature_k=lower_temperature_k,
        selected_source_index_range=None,
        selected_source_row_range=None,
        source_index_basis="zero based in complete sensor samples; source_row is one based XLSX",
        source_sample_count=len(record["samples"]),
        samples=[],
        parameters=None,
        source_rows_reordered_filled_or_smoothed=False,
    )

    def hold(message):
        body["issues"] = [message]
        return _seal(body)

    samples = record["samples"]
    if len(samples) < 2 or any(
        s["time_state"] != "observed" or s["temperature_state"] != "observed" for s in samples
    ):
        return hold("complete finite physical time and temperature observations are required")
    time, temperature = ([s[key] for s in samples] for key in ("time_s", "temperature_k"))
    if any(b <= a for a, b in zip(time, time[1:], strict=False)):
        return hold("source time is not strictly increasing; no sorting or deduplication is permitted")
    maximum = max(temperature)
    peak_indices = [i for i, t in enumerate(temperature) if t == maximum]
    if peak_indices != list(range(peak_indices[0], peak_indices[-1] + 1)):
        return hold("disjoint observed global maxima make the post-peak branch ambiguous")
    peak = peak_indices[-1]
    body["observed_peak_source_index"] = peak
    body["observed_peak_source_row"] = samples[peak]["source_row"]
    body["post_peak_temperature_increase_count"] = sum(
        b > a for a, b in zip(temperature[peak:], temperature[peak + 1 :], strict=False)
    )

    def crossing_groups(target):
        indices = [i for i in range(peak, len(samples) - 1) if temperature[i] >= target >= temperature[i + 1]]
        return [i for j, i in enumerate(indices) if j == 0 or i > indices[j - 1] + 1]

    upper, lower = crossing_groups(upper_temperature_k), crossing_groups(lower_temperature_k)
    body["upper_crossing_group_count"], body["lower_crossing_group_count"] = len(upper), len(lower)
    if not upper or not lower:
        return hold("both post-peak temperature crossings must be observed")
    if len(upper) != 1 or len(lower) != 1:
        return hold("multiple post-peak temperature crossing groups require an explicit source review")
    start, end = upper[0], lower[0] + 1
    if start >= end or any(
        b > a for a, b in zip(temperature[start : end + 1], temperature[start + 1 : end + 1], strict=False)
    ):
        return hold(
            "reheating or measurement noise inside the crossing bracket cannot be removed or smoothed"
        )
    selected = [dict(s) for s in samples[start : end + 1]]
    body.update(
        status="selected",
        selected_source_index_range=[start, end],
        selected_source_row_range=[selected[0]["source_row"], selected[-1]["source_row"]],
        samples=selected,
        parameters=dict(
            time_s=[s["time_s"] for s in selected],
            temperature_K=[s["temperature_k"] for s in selected],
            upper_temperature_K=upper_temperature_k,
            lower_temperature_K=lower_temperature_k,
        ),
    )
    return _seal(body)
