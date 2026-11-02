"""Synthetic XLSX/ZIP fixtures exercise provenance, source preservation and conservative branch selection."""

import hashlib
import io
import json
import re
import zipfile

import pytest

import pipeline_integrity.ebw_data as ebw
from pipeline_integrity.contracts import digest

openpyxl = pytest.importorskip("openpyxl")


def workbook_bytes(temperatures=None, *, times=None, second=None, header=None, extra_sheet=False):
    temperatures = temperatures if temperatures is not None else [20, 1000, 900, 800, 650, 500, 400]
    times = times if times is not None else list(range(len(temperatures)))
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "S1300QL_Normal EBW"
    headers = [ebw.TIME_HEADER, "Temperature (c); thermocouple distance from weld centre = 1.55 mm"]
    if second is not None:
        headers.append("Temperature (c) ; thermocouple distance from weld centre = 2 mm")
    sheet.append(header if header is not None else headers)
    for i, (time, value) in enumerate(zip(times, temperatures, strict=True)):
        sheet.append([time, value] if second is None else [time, value, second[i]])
    if extra_sheet:
        book.create_sheet("ambiguous")
    out = io.BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


def archive(tmp_path, monkeypatch, *, filename="S1300Q_Normal EBW_999_2s.xlsx", members=None,
            compression=zipfile.ZIP_STORED, **kwargs):
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w", compression=compression) as source:
        for name, data in members if members is not None else [(filename, workbook_bytes(**kwargs))]:
            source.writestr(name, data)
    monkeypatch.setattr(ebw, "PUBLISHER_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(ebw, "PUBLISHER_BYTES", path.stat().st_size)
    return path


def test_source_rows_units_sensor_identity_and_disagreeing_material_labels_are_preserved(
    tmp_path, monkeypatch
):
    source = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch, second=[21, 920, 850, 790, 640, 490, 390]))
    assert source["physical_experiment_count"] == 1 and source["sensor_history_count"] == 2
    assert source["physical_time_rows"] == 7 and source["sensor_samples"] == 14
    a, b = source["records"]
    assert a["clock_id"] == b["clock_id"] and a["id"] != b["id"]
    assert a["sensor_distance_from_weld_centre_m"] == 0.00155
    assert b["sensor_distance_from_weld_centre_m"] == 0.002
    assert [s["source_row"] for s in a["samples"]] == list(range(2, 9))
    assert a["samples"][0]["temperature_c"] == 20
    assert a["samples"][0]["temperature_k"] == 293.15
    assert a["nominal_material_from_filename"] == "S1300Q"
    assert a["nominal_material_from_sheet"] == "S1300QL"
    assert source["experiments"][0]["nominal_material_labels_disagree"]
    assert not source["experiments"][0]["filename_labels_are_observations"]
    assert a["summary"]["observed_peak_temperature_k"] == 1273.15
    assert source["experiments"][0]["filename_reported_labels"] == [dict(peak_temperature_c=999, t85_s=2)]
    assert source["record_digest"] == digest({k: v for k, v in source.items() if k != "record_digest"})
    json.dumps(source, allow_nan=False)


def test_source_reader_record_replays_and_rejects_resealed_observation_tampering(tmp_path, monkeypatch):
    path = archive(tmp_path, monkeypatch, second=[21, 920, 850, 790, 640, 490, 390])
    source = ebw.load_ebw_thermal(path)
    assert ebw.verify_ebw_thermal_source(source, path)["valid"]

    changed = dict(source)
    changed["records"] = [dict(row) for row in source["records"]]
    changed["records"][0] = dict(changed["records"][0])
    changed["records"][0]["samples"] = [dict(sample) for sample in changed["records"][0]["samples"]]
    changed["records"][0]["samples"][0]["temperature_c"] += 1
    changed["record_digest"] = digest({key: value for key, value in changed.items() if key != "record_digest"})
    assert not ebw.verify_ebw_thermal_source(changed, path)["valid"]


def test_publisher_checksum_is_checked_before_any_workbook_open(tmp_path, monkeypatch):
    path = tmp_path / "unknown.zip"
    path.write_bytes(b"not a publisher archive")
    monkeypatch.setattr(
        openpyxl, "load_workbook", lambda *a, **k: pytest.fail("workbook opened before checksum")
    )
    with pytest.raises(ValueError, match="publisher size/SHA-256"):
        ebw.load_ebw_thermal(path)
    with pytest.raises(ValueError, match="regular non-symlink"):
        ebw.load_ebw_thermal(tmp_path)


def test_separate_experiments_keep_distinct_clocks_even_with_identical_released_times(tmp_path, monkeypatch):
    workbook = workbook_bytes()
    path = archive(
        tmp_path,
        monkeypatch,
        members=[
            ("S1300Q_Normal EBW_999_2s.xlsx", workbook),
            ("S1300Q_Beam oscillation_999_2s.xlsx", workbook),
        ],
    )
    source = ebw.load_ebw_thermal(path)
    assert source["physical_experiment_count"] == 2
    a, b = source["records"]
    assert a["clock_id"] != b["clock_id"]
    assert not source["common_absolute_clock_across_experiments_established"]
    assert [s["time_s"] for s in a["samples"]] == [s["time_s"] for s in b["samples"]]


def test_same_size_tampering_is_rejected_and_pinned_download_wrapper_is_supported(tmp_path, monkeypatch):
    path = archive(tmp_path, monkeypatch)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError, match="publisher size/SHA-256|expected regular publisher ZIP"):
        ebw.load_ebw_thermal(path)
    with zipfile.ZipFile(path, "w") as z:
        member_name = "Dataset on thermal cycles and cooling times during/" + ebw.PUBLISHER_FILENAME
        z.writestr(member_name, original)
    source = ebw.load_ebw_thermal(path)
    assert source["source_filename"] == ebw.PUBLISHER_FILENAME
    assert source["source_sha256"] == ebw.PUBLISHER_SHA256
    assert source["download_container"]["kind"] == "single_member_download_wrapper"
    assert source["download_container"]["member_path"] == member_name
    assert source["download_container"]["publisher_sha256_verified"]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member_name, original)
        z.writestr("unexpected.txt", b"extra")
    with pytest.raises(ValueError, match="member count"):
        ebw.load_ebw_thermal(path)


@pytest.mark.parametrize("member", ["../escape.xlsx", "/absolute.xlsx", "C:/drive.xlsx", "dir\\evil.xlsx"])
def test_unsafe_zip_names_are_rejected_without_extraction(tmp_path, monkeypatch, member):
    path = archive(tmp_path, monkeypatch, members=[(member, b"unused")])
    with pytest.raises(ValueError, match="unsafe|top-level"):
        ebw.load_ebw_thermal(path)


def test_duplicate_and_nested_members_are_rejected(tmp_path, monkeypatch):
    with pytest.warns(UserWarning, match="Duplicate name"):
        path = archive(tmp_path, monkeypatch, members=[("same.xlsx", b"a"), ("same.xlsx", b"b")])
    with pytest.raises(ValueError, match="duplicated"):
        ebw.load_ebw_thermal(path)
    path = archive(tmp_path, monkeypatch, members=[("directory/book.xlsx", b"unused")])
    with pytest.raises(ValueError, match="top-level"):
        ebw.load_ebw_thermal(path)


def test_uncompressed_zip_size_budget_is_enforced(tmp_path, monkeypatch):
    # Keep the outer input below its byte cap so this reaches the decompressed
    # member-size guard rather than failing the earlier container-size guard.
    path = archive(tmp_path, monkeypatch, members=[("large.xlsx", bytes(2_000_001))],
                   compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError, match="oversized"):
        ebw.load_ebw_thermal(path)


@pytest.mark.parametrize(
    "changes", [{"header": ["Time ms", "temperature"]}, {"extra_sheet": True}, {"header": [1, 2]}]
)
def test_unrecognized_header_or_multiple_physical_sheets_are_not_guessed(tmp_path, monkeypatch, changes):
    with pytest.raises(ValueError, match="header|physical experiment"):
        ebw.load_ebw_thermal(archive(tmp_path, monkeypatch, **changes))


def test_missing_and_error_cells_remain_null_without_filling_or_time_generation(tmp_path, monkeypatch):
    path = archive(
        tmp_path, monkeypatch, temperatures=[20, None, "#N/A", "NaN", 500], times=[0, 1, None, 3, 4]
    )
    record = ebw.load_ebw_thermal(path)["records"][0]
    assert record["samples"][1]["temperature_k"] is None
    assert record["samples"][2]["time_s"] is None
    assert [s["temperature_state"] for s in record["samples"]] == [
        "observed",
        "missing",
        "invalid",
        "invalid",
        "observed",
    ]
    assert record["summary"]["missing_or_invalid_temperature_count"] == 3
    assert ebw.select_ebw_cooling_branch(record)["status"] == "needs_data"
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize(
    "value,state", [(float("nan"), "nonfinite"), (float("inf"), "nonfinite"), (True, "invalid")]
)
def test_nonfinite_or_boolean_numbers_are_not_zero_or_observations(value, state):
    assert ebw._number(value) == (None, state)


def test_nonphysical_numeric_source_values_are_preserved_and_withheld(tmp_path, monkeypatch):
    record = ebw.load_ebw_thermal(
        archive(tmp_path, monkeypatch, temperatures=[20, 1000, -300], times=[0, 1, -1])
    )["records"][0]
    assert record["samples"][-1]["time_s"] == -1
    assert record["samples"][-1]["temperature_c"] == -300
    assert record["samples"][-1]["temperature_state"] == "nonphysical"
    assert ebw.select_ebw_cooling_branch(record)["status"] == "needs_data"


def test_unique_contiguous_crossing_bracket_retains_original_indexes_and_peak_history(tmp_path, monkeypatch):
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch))["records"][0]
    selection = ebw.select_ebw_cooling_branch(record)
    assert selection["status"] == "selected"
    assert selection["selected_source_index_range"] == [2, 5]
    assert selection["selected_source_row_range"] == [4, 7]
    assert selection["parameters"]["time_s"] == [2, 3, 4, 5]
    assert selection["samples"] == record["samples"][2:6]
    assert selection["samples"][0] is not record["samples"][2]
    assert record["samples"][0]["temperature_c"] == 20 and len(record["samples"]) == 7
    assert not selection["source_rows_reordered_filled_or_smoothed"]


@pytest.mark.parametrize(
    "values,reason",
    [
        ([20, 1000, 900, 780, 790, 600, 400], "reheating|noise"),
        ([20, 1000, 900, 700, 850, 600, 400], "multiple"),
        ([20, 1000, 900, 650, 450, 550, 400], "multiple"),
        ([20, 1000, 900, 1000, 650, 400], "disjoint"),
        ([20, 700, 650, 400], "both post-peak"),
        ([20, 1000, 900, 650], "both post-peak"),
    ],
)
def test_no_smoothing_second_crossing_or_missing_threshold_is_hidden(tmp_path, monkeypatch, values, reason):
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch, temperatures=values))["records"][0]
    selected = ebw.select_ebw_cooling_branch(record)
    assert selected["status"] == "needs_data" and selected["parameters"] is None
    assert selected["samples"] == []
    assert re.search(reason, selected["issues"][0])


def test_contiguous_global_peak_plateau_and_exact_threshold_plateaus_are_explicit(tmp_path, monkeypatch):
    values = [20, 1000, 1000, 900, 800, 800, 650, 500, 500, 400]
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch, temperatures=values))["records"][0]
    selection = ebw.select_ebw_cooling_branch(record)
    assert selection["status"] == "selected" and selection["observed_peak_source_index"] == 2
    assert selection["upper_crossing_group_count"] == selection["lower_crossing_group_count"] == 1
    assert selection["parameters"]["time_s"] == [3, 4, 5, 6, 7]


@pytest.mark.parametrize("times", [[0, 1, 2, 2, 4, 5, 6], [0, 1, 3, 2, 4, 5, 6]])
def test_native_clock_order_is_not_repaired(tmp_path, monkeypatch, times):
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch, times=times))["records"][0]
    assert ebw.select_ebw_cooling_branch(record)["status"] == "needs_data"
    assert [s["time_s"] for s in record["samples"]] == times


def test_sealed_source_tampering_is_rejected(tmp_path, monkeypatch):
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch))["records"][0]
    record["samples"][2]["temperature_k"] = 1000
    with pytest.raises(ValueError, match="digest mismatch"):
        ebw.select_ebw_cooling_branch(record)


@pytest.mark.parametrize("high,low", [(773.15, 1073.15), (float("nan"), 773.15), (True, 1.0), (1000, 0)])
def test_invalid_target_temperatures_are_rejected(tmp_path, monkeypatch, high, low):
    record = ebw.load_ebw_thermal(archive(tmp_path, monkeypatch))["records"][0]
    with pytest.raises(ValueError, match="ordered"):
        ebw.select_ebw_cooling_branch(record, upper_temperature_k=high, lower_temperature_k=low)
