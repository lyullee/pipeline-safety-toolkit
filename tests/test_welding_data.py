"""Synthetic source structures exercise clock and integrity semantics, not weld performance."""

import hashlib
import json
import zipfile
from dataclasses import FrozenInstanceError
from io import BytesIO
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from pipeline_integrity import welding_data as wd
from pipeline_integrity.contracts import digest


@pytest.fixture
def make_archive(tmp_path, monkeypatch):
    assets = []

    def make(
        kind="process",
        *,
        name="WID631_L1.zip",
        ticks=None,
        tick_units=None,
        root_time="2025-01-01T00:00:00.123456789+00:00",
        calendar="proleptic_gregorian",
        unit_change=None,
        shape_change=None,
        channel_change=None,
        link=False,
        virtual=False,
        member_change=None,
        extra_zip=None,
        coordinate_labels=("x", "y", "z"),
        start_epoch="2025-01-01T00:00:00.123456789",
    ):
        ticks = np.asarray(
            ticks
            if ticks is not None
            else ([0, 99999, 200000, 400000] if kind == "process" else [0, 1, 2, 4]),
            dtype=np.int64,
        )
        memory = BytesIO()
        with h5py.File(memory, "w") as native:
            native.attrs["start_time"] = root_time
            native.attrs["File"] = "C:/private/supplier/source-path"
            clock = native.create_dataset("time", data=ticks)
            clock.attrs["units"] = (
                tick_units or f"{'nanoseconds' if kind == 'process' else 'milliseconds'} since {start_epoch}"
            )
            clock.attrs["calendar"] = calendar
            for channel, unit in wd._CHANNEL_UNITS[kind].items():
                if channel_change == channel:
                    continue
                shape = (len(ticks), 3) if channel in {"tcp_coords", "tcp_angles"} else (len(ticks),)
                values = np.full(shape, 2.0, dtype=np.float64)
                if len(ticks) >= 4:
                    values[1] = np.nan
                    if channel == "thermo_2":
                        values[2] = -448
                    elif channel == "voltage_leading":
                        values[2] = -3
                    elif channel == "voltage_trailing":
                        values[2] = np.inf
                if shape_change == channel:
                    values = values[:-1]
                node = native.create_dataset(channel, data=values)
                node.attrs["units"] = np.array([unit], dtype=h5py.string_dtype()) if unit == "°C" else unit
                if unit_change and unit_change[0] == channel:
                    node.attrs["units"] = unit_change[1]
            if link:
                del native["current_leading"]
                native["current_leading"] = h5py.ExternalLink("missing.nc", "/private")
            if virtual:
                del native["current_leading"]
                layout = h5py.VirtualLayout(shape=(len(ticks),), dtype=np.float64)
                layout[:] = h5py.VirtualSource("unreadable.h5", "/current", shape=(len(ticks),))
                native.create_virtual_dataset("current_leading", layout)
            if kind == "robot":
                native.create_dataset("c", data=np.array(coordinate_labels, dtype=h5py.string_dtype()))
        path = tmp_path / name
        member = member_change or f"{path.stem}_T1_{kind}.nc"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr(member, memory.getvalue())
            if extra_zip:
                # Construct the malicious name verbatim: Windows ZipInfo's
                # constructor otherwise normalizes backslashes for writers.
                extra = zipfile.ZipInfo("placeholder")
                extra.filename = extra_zip
                zipped.writestr(extra, b"unexecuted")
        asset = SimpleNamespace(
            filename=name,
            checksum="md5:" + hashlib.md5(path.read_bytes()).hexdigest(),
            checksum_origin="publisher_metadata",
            bytes=path.stat().st_size,
        )
        assets[:] = [old for old in assets if old.filename != name] + [asset]
        specification = SimpleNamespace(
            id="tandem_gmaw",
            version="synthetic test",
            citation="synthetic fixture",
            license_id="synthetic fixture",
            assets=tuple(assets),
        )
        monkeypatch.setattr(wd, "catalog", lambda: (specification,))
        return path

    return make


@pytest.mark.parametrize(
    "kind,expected", [("process", [0, 0.000099999, 0.0002, 0.0004]), ("robot", [0, 0.001, 0.002, 0.004])]
)
def test_native_clock_and_complete_source_proof(make_archive, kind, expected):
    recording = wd.read_tandem_recording(make_archive(kind), kind)
    np.testing.assert_allclose(recording.time_s, expected, rtol=0, atol=1e-18)
    summary = recording.summary()
    assert summary["supplier_checksum_verified"]
    assert summary["time_origin_utc"] == "2025-01-01T00:00:00.123456789+00:00"
    assert summary["clock_quality"]["samples_dropped"] == 0
    assert summary["source_samples"] == 4
    assert len(summary["archive_sha256"]) == len(summary["member_sha256"]) == 64
    assert "private" not in json.dumps(summary)
    assert set(summary["quality"]) == set(wd._CHANNEL_UNITS[kind])


def test_nonfinite_values_and_original_polarity_are_preserved(make_archive):
    recording = wd.read_tandem_recording(make_archive())
    channels = ("current_leading", "voltage_leading", "voltage_trailing")
    frames, report = recording.frames(channels=channels)
    assert len(frames) == 4
    assert np.isnan(recording.values["current_leading"][1])
    assert frames[1].values == dict.fromkeys(channels)
    assert not any(frames[1].valid.values())
    assert frames[2].values["voltage_leading"] == -3
    assert frames[2].valid["voltage_leading"]
    assert frames[2].values["voltage_trailing"] is None
    assert not frames[2].valid["voltage_trailing"]
    assert report["source"]["electrical_product_quality"]["leading"]["negative_products"] == 1
    assert report["samples_dropped_within_selection"] == 0
    assert report["full_source_span_selected"]
    assert all(frame.timestamp_utc is None for frame in frames)


def test_absolute_zero_fault_is_invalid_but_not_deleted_or_replaced(make_archive):
    recording = wd.read_tandem_recording(make_archive("robot"), "robot")
    frames, report = recording.frames(channels=("thermo_2",))
    assert recording.units["thermo_2"] == "°C"
    assert recording.values["thermo_2"][2] == frames[2].values["thermo_2"] == -448
    assert not recording.valid["thermo_2"][2] and not frames[2].valid["thermo_2"]
    quality = report["source"]["quality"]["thermo_2"]
    assert quality["invalid_finite"] == 1 and quality["nan"] == 1 and quality["valid"] == 2
    assert quality["minimum_finite"] == -448
    assert recording.values["tcp_coords"].shape == (4, 3)
    with pytest.raises(ValueError, match="scalar 1D"):
        recording.frames(channels=("tcp_coords",))


def test_arrays_mappings_and_nested_metadata_are_sealed(make_archive):
    recording = wd.read_tandem_recording(make_archive())
    with pytest.raises(FrozenInstanceError):
        recording.record_id = "replacement"
    for array in (recording.time_s, *recording.values.values(), *recording.valid.values()):
        with pytest.raises(ValueError):
            array.setflags(write=True)
        with pytest.raises(ValueError):
            array.flat[0] = 1
    with pytest.raises(TypeError):
        recording.values["current_leading"] = np.zeros(4)
    with pytest.raises(TypeError):
        recording.metadata["quality"]["current_leading"]["valid"] = 20
    copied = recording.summary()
    copied["quality"]["current_leading"]["valid"] = 20
    assert recording.summary()["quality"]["current_leading"]["valid"] == 3


@pytest.mark.parametrize("field", ["time_s", "values", "valid", "units", "metadata", "record_id"])
def test_integrity_rechecked_before_frame_conversion(make_archive, field):
    recording = wd.read_tandem_recording(make_archive())
    substitutions = {
        "time_s": np.arange(4, dtype=float),
        "values": dict(recording.values) | {"current_leading": np.ones(4)},
        "valid": dict(recording.valid) | {"current_leading": np.zeros(4, dtype=bool)},
        "units": dict(recording.units) | {"current_leading": "mA"},
        "metadata": recording.summary() | {"time_origin_utc": "2026-01-01T00:00:00+00:00"},
        "record_id": "different",
    }
    object.__setattr__(recording, field, substitutions[field])
    with pytest.raises(ValueError, match="changed after reading"):
        recording.frames(channels=("current_leading",))


def test_exact_inclusive_window_and_sample_budget(make_archive):
    recording = wd.read_tandem_recording(make_archive())
    with pytest.raises(ValueError, match="maximum_samples"):
        recording.frames(channels=("current_leading",), maximum_samples=3)
    frames, report = recording.frames(
        start_s=recording.time_s[1],
        finish_s=recording.time_s[2],
        channels=("current_leading",),
        maximum_samples=2,
    )
    assert len(frames) == 2
    assert report["source_index_start"] == 1 and report["source_index_stop_exclusive"] == 3
    assert report["requested_bounds_s"] == report["selected_source_bounds_s"]
    assert report["source_samples"] == 4 and not report["full_source_span_selected"]
    assert report["source_full_span_s"] == [0, recording.time_s[-1]]
    assert report["selection_digest"] == digest(
        {key: value for key, value in report.items() if key != "selection_digest"}
    )


def test_default_budget_never_silently_downsamples(make_archive):
    recording = wd.read_tandem_recording(make_archive(ticks=np.arange(100001)))
    with pytest.raises(ValueError, match="explicitly raise"):
        recording.frames(channels=("current_leading",))
    frames, report = recording.frames(start_s=recording.time_s[-1], channels=("current_leading",))
    assert len(frames) == 1 and report["source_index_start"] == 100000


@pytest.mark.parametrize(
    "options",
    [
        {"start_s": -1},
        {"start_s": float("nan")},
        {"finish_s": float("inf")},
        {"start_s": True},
        {"start_s": 1, "finish_s": 0},
        {"start_s": 1},
        {"maximum_samples": True},
        {"maximum_samples": 0},
        {"maximum_samples": 10000001},
        {"channels": ()},
        {"channels": ["current_leading"]},
        {"channels": ("unknown",)},
        {"channels": ("current_leading", "current_leading")},
    ],
)
def test_invalid_selection_requests_are_rejected(make_archive, options):
    recording = wd.read_tandem_recording(make_archive())
    with pytest.raises(ValueError):
        recording.frames(**({"channels": ("current_leading",)} | options))


def test_common_origin_aligns_recordings_without_guessing_or_rounding(make_archive):
    process = wd.read_tandem_recording(make_archive())
    robot = wd.read_tandem_recording(
        make_archive(
            "robot",
            name="WID631_L2.zip",
            root_time="2025-01-01T00:00:00.120000000+00:00",
            start_epoch="2025-01-01T00:00:00.120000000",
        ),
        "robot",
    )
    origin = "2025-01-01T00:00:00.120000000Z"
    process_frames, process_selection = process.frames(
        channels=("current_leading",), common_origin_utc=origin
    )
    robot_frames, robot_selection = robot.frames(channels=("thermo_1",), common_origin_utc=origin)
    assert process_frames[0].clock_id == robot_frames[0].clock_id
    assert process_frames[0].time_s == pytest.approx(0.003456789, abs=1e-18)
    assert process_frames[1].time_s - process_frames[0].time_s == pytest.approx(0.000099999, abs=1e-18)
    assert process_selection["source_origin_offset_ns"] == 3456789
    assert robot_selection["source_origin_offset_ns"] == 0
    assert process_selection["selected_source_bounds_s"][0] == 0
    assert robot_frames[0].time_s == 0


@pytest.mark.parametrize(
    "origin", ["2025-01-01T00:00:00", "2025-01-01T00:00:00+09:00", "2025-01-01T00:00:01+00:00"]
)
def test_ambiguous_non_utc_or_negative_common_clocks_are_rejected(make_archive, origin):
    recording = wd.read_tandem_recording(make_archive())
    with pytest.raises(ValueError):
        recording.frames(channels=("current_leading",), common_origin_utc=origin)


@pytest.mark.parametrize("ticks", [[0, 1, 1, 2], [0, 2, 1, 3], [-1, 0, 1, 2]])
def test_native_clock_failures_are_rejected_without_repair(make_archive, ticks):
    with pytest.raises(ValueError, match="not repaired"):
        wd.read_tandem_recording(make_archive(ticks=ticks))


@pytest.mark.parametrize(
    "options",
    [
        {"tick_units": "milliseconds since 2025-01-01T00:00:00.123456789"},
        {"root_time": "2025-01-01T00:00:00.123456788+00:00"},
        {"root_time": "2025-01-01T00:00:00.123456789"},
        {"root_time": "2025-01-01T09:00:00.123456789+09:00"},
        {"calendar": "standard"},
        {"unit_change": ("current_leading", "mA")},
        {"shape_change": "current_leading"},
        {"channel_change": "current_leading"},
        {"link": True},
        {"virtual": True},
    ],
)
def test_source_schema_time_and_units_are_checked_before_conversion(make_archive, options):
    with pytest.raises(ValueError):
        wd.read_tandem_recording(make_archive(**options))


def test_robot_component_labels_are_checked(make_archive):
    with pytest.raises(ValueError, match="component labels"):
        wd.read_tandem_recording(make_archive("robot", coordinate_labels=("z", "y", "x")), "robot")


def test_checksum_is_checked_before_optional_dependency(make_archive, monkeypatch):
    path = make_archive()
    contents = bytearray(path.read_bytes())
    contents[-1] ^= 1
    path.write_bytes(contents)
    monkeypatch.setattr(
        wd.importlib, "import_module", lambda _: pytest.fail("optional import preceded checksum validation")
    )
    with pytest.raises(ValueError, match="supplier checksum"):
        wd.read_tandem_recording(path)


def test_unpinned_supplier_or_size_never_loads_optional_dependency(make_archive, monkeypatch):
    path = make_archive()
    spec = wd.catalog()[0]
    spec.assets[0].checksum_origin = "previous_acquisition"
    monkeypatch.setattr(
        wd.importlib, "import_module", lambda _: pytest.fail("optional import preceded supplier validation")
    )
    with pytest.raises(ValueError, match="supplier checksum"):
        wd.read_tandem_recording(path)
    spec.assets[0].checksum_origin = "publisher_metadata"
    spec.assets[0].bytes += 1
    with pytest.raises(ValueError, match="size differs"):
        wd.read_tandem_recording(path)


@pytest.mark.parametrize(
    "options",
    [
        {"member_change": "WID632_L1_T1_process.nc"},
        {"extra_zip": "../outside"},
        {"extra_zip": "C:/outside"},
        {"extra_zip": "folder\\outside"},
    ],
)
def test_zip_member_identity_and_paths_are_validated(make_archive, options):
    with pytest.raises(ValueError):
        wd.read_tandem_recording(make_archive(**options))


def test_member_byte_budget_checked_before_optional_dependency(make_archive, monkeypatch):
    path = make_archive()
    monkeypatch.setattr(wd, "_MAXIMUM_MEMBER_BYTES", 1)
    monkeypatch.setattr(
        wd.importlib, "import_module", lambda _: pytest.fail("optional import preceded member budget")
    )
    with pytest.raises(ValueError, match="member exceeds"):
        wd.read_tandem_recording(path)


@pytest.mark.parametrize(
    "budget,error",
    [("_MAXIMUM_ARCHIVE_BYTES", "archive exceeds"), ("_MAXIMUM_DECODED_BYTES", "decoded byte budget")],
)
def test_archive_and_decoded_array_byte_budgets(make_archive, monkeypatch, budget, error):
    path = make_archive()
    monkeypatch.setattr(wd, budget, 1)
    with pytest.raises(ValueError, match=error):
        wd.read_tandem_recording(path)
